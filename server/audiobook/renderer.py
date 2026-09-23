"""Background worker that turns books into audio, one chunk at a time.

Resumable: every finished chunk is a WAV under work/<chapter>/, every finished chapter an .m4a.
A chapter is only re-voiced when its text, voice or pace changes (tracked by a plan key).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from . import audio
from .config import settings
from .engine_manager import engine_manager
from .store import BookNotFound, book_store
from .textprep import Chunk, chunk_chapter, estimate_seconds, speech_text
from .voices import voice_store


log = logging.getLogger("audiobook.renderer")


class Stop(Exception):
    """The book was paused or deleted while rendering."""


class Renderer:
    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.queue: deque[str] = deque()
        self.current: str | None = None
        self.stop_requested: set[str] = set()
        self.thread: threading.Thread | None = None
        # book id -> (chapter index, part index) to voice next ("start from page", "go to page")
        self.priority: dict[str, tuple[int, int]] = {}
        self.pending_pages: dict[str, int] = {}  # start page asked for before the parts were planned

    # ---------------------------------------------------------------- control

    def start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, name="renderer", daemon=True)
            self.thread.start()
        for book in book_store.list():
            if book["status"] in ("queued", "rendering"):
                self.enqueue(book["id"])  # resume work interrupted by a restart
                continue

            def tidy(b: dict) -> None:  # a chapter left mid-voicing by a shutdown isn't being voiced now
                for c in b["chapters"]:
                    if c["status"] == "rendering":
                        c["status"] = "pending"

            if any(c["status"] == "rendering" for c in book["chapters"]):
                book_store.update(book["id"], tidy)

    def enqueue(self, book_id: str) -> None:
        with self.cv:
            self.stop_requested.discard(book_id)
            if book_id != self.current and book_id not in self.queue:
                self.queue.append(book_id)
            self.cv.notify_all()

    def stop(self, book_id: str, wait: float = 0.0) -> None:
        """Remove from the queue / ask the running render to stop at the next chunk."""
        with self.cv:
            if book_id in self.queue:
                self.queue.remove(book_id)
            if self.current == book_id:
                self.stop_requested.add(book_id)
            deadline = time.time() + wait
            while wait and self.current == book_id and time.time() < deadline:
                self.cv.wait(timeout=0.2)

    def prioritize(self, book_id: str, chapter: int, part: int) -> None:
        """Voice this part next (then carry on forward from it)."""
        self.priority[book_id] = (chapter, part)

    def queue_ids(self) -> list[str]:
        with self.cv:
            return ([self.current] if self.current else []) + list(self.queue)

    def _loop(self) -> None:
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                book_id = self.queue.popleft()
                self.current = book_id
            try:
                self._render(book_id)
            except (Stop, BookNotFound):
                pass
            except Exception as exc:
                log.exception("Rendering %s failed", book_id)
                try:
                    book_store.update(book_id, lambda b: b.update(status="error", error=f"{type(exc).__name__}: {exc}"))
                except BookNotFound:
                    pass
            finally:
                with self.cv:
                    self.current = None
                    self.stop_requested.discard(book_id)
                    self.cv.notify_all()

    def _check(self, book_id: str) -> None:
        if book_id in self.stop_requested or not (book_store.dir(book_id) / "book.json").exists():
            raise Stop()

    # ---------------------------------------------------------------- rendering

    def _voice_for(self, book: dict) -> dict:
        """Freeze the narrator sample into the book folder so re-rolling a voice later can't change
        the voice halfway through this book."""
        folder = book_store.dir(book["id"])
        frozen_meta, frozen_wav = folder / "voice.json", folder / "voice.wav"
        if frozen_meta.exists() and frozen_wav.exists():
            meta = json.loads(frozen_meta.read_text(encoding="utf-8"))
            if meta.get("voice_id") == book["voice_id"]:
                return meta
        voice = voice_store.get(book["voice_id"])
        if voice is None:
            raise RuntimeError("The chosen voice no longer exists. Pick another voice.")
        meta = dict(voice_store.ensure_sample(voice), voice_id=voice.id, voice_seed=voice.seed)
        shutil.copyfile(voice_store.sample_path(voice.id), frozen_wav)
        frozen_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    def _render(self, book_id: str) -> None:
        book = book_store.load(book_id)
        if book["status"] not in ("queued", "rendering"):
            return
        book_store.update(book_id, lambda b: b.update(status="rendering", error=None))
        engine = engine_manager.ensure_loaded()
        sr = engine.sample_rate
        folder = book_store.dir(book_id)

        pace = float(book.get("pace") or 1.0)
        lang = book["language"]
        ref_wav = folder / "voice.wav"
        preset = engine.voice_mode == "preset"
        if preset:
            # fixed speaker (Kokoro): natural length, speed = pace, no reference sample needed
            chosen = voice_store.get(book["voice_id"])
            if chosen is None or not chosen.engine_voice:
                raise RuntimeError("This book's voice belongs to another engine. Pick one of the current voices.")
            voice = {"voice_id": chosen.id, "take": 0}
            rate, target_budget, limit, seed = 1.0, 120.0, settings.max_chunk_seconds, 0
        else:
            voice = self._voice_for(book)
            # how much slower/faster this narrator speaks than AuK's baseline estimate
            rate = voice["seconds"] / max(0.5, estimate_seconds(voice["text"], voice["language"]))
            target_budget = settings.max_sequence_seconds - 0.5 - voice["seconds"]
            target_cap = max(4.0, min(settings.max_chunk_seconds, target_budget))
            limit = target_cap * pace / rate
            seed = int(voice.get("seed", 0))

        texts = book_store.text(book_id)
        page_lists = book_store.pages(book_id)
        break_lists = book_store.breaks(book_id)
        plans: dict[int, tuple[list[Chunk], str]] = {}
        parts: dict[int, list[tuple[int, int]]] = {}
        for ch in book["chapters"]:
            if not ch["include"]:
                continue
            idx = ch["index"]
            plan = chunk_chapter(ch["spoken_title"] or ch["title"], texts[idx], lang, limit, page_lists[idx], break_lists[idx])
            key = hashlib.sha1(json.dumps(
                [engine.name, voice["voice_id"], voice["take"], pace, round(limit, 2), [(c.text, c.pause_after) for c in plan]],
                ensure_ascii=False,
            ).encode()).hexdigest()[:16]
            plans[idx] = (plan, key)
            parts[idx] = make_parts(plan, rate, pace)

        def is_done(ch: dict) -> bool:
            idx = ch["index"]
            # same text, voice and pace as the audio already on disk (even if the chapter was untoggled meanwhile)
            return ch["plan_key"] == plans[idx][1] and ch.get("duration") is not None and (folder / "audio" / f"{idx}.m4a").exists()

        def backfill(ch: dict) -> None:
            """Chapters voiced before MP3/FLAC existed: derive them from the .m4a (no re-voicing)."""
            base = folder / "audio" / str(ch["index"])
            if base.with_suffix(".flac").exists() and base.with_suffix(".mp3").exists():
                return
            wav = folder / "audio" / f"{ch['index']}.backfill.wav"
            audio.decode_to_wav(base.with_suffix(".m4a"), wav, sr)
            audio.encode_flac(wav, base.with_suffix(".flac"))
            audio.encode_mp3(wav, base.with_suffix(".mp3"), settings.mp3_bitrate, chapter_tags(book, ch, plans))
            wav.unlink(missing_ok=True)

        def work_dir(idx: int) -> Path:
            return folder / "work" / str(idx)

        def part_meta(idx: int, p: int) -> dict | None:
            meta = work_dir(idx) / f"part{p:03d}.json"
            if meta.exists() and (work_dir(idx) / f"part{p:03d}.wav").exists() and part_file(folder, idx, p).exists():
                return json.loads(meta.read_text())
            return None

        def init(b: dict) -> None:
            done = total = 0
            for ch in b["chapters"]:
                idx = ch["index"]
                if idx not in plans:
                    ch["status"], ch["parts"] = "skipped", []
                    skipped_pages = [n for n in page_lists[idx] if n]
                    ch["pages"] = [min(skipped_pages), max(skipped_pages)] if skipped_pages else None
                    continue
                plan, key = plans[idx]
                page_nums = [c.page for c in plan if c.page]
                ch["pages"] = [min(page_nums), max(page_nums)] if page_nums else None
                ch["total_chunks"] = len(plan)
                total += len(plan)
                ch["offset"] = None  # positions in the whole-book stream are set when the book is packaged
                if is_done(ch):
                    ch["status"], ch["done_chunks"], ch["parts"] = "ready", len(plan), []
                    done += len(plan)
                    continue
                work = work_dir(idx)
                keyfile = work / "plan.key"
                if keyfile.exists() and keyfile.read_text() != key:
                    shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True, exist_ok=True)
                keyfile.write_text(key)
                ch["status"], ch["duration"], ch["page_marks"] = "pending", None, None
                ch["parts"] = []
                chunks_done = 0
                for p, (a, z) in enumerate(parts[idx]):
                    meta = part_meta(idx, p)
                    part_pages = [c.page for c in plan[a:z] if c.page]
                    ch["parts"].append({
                        "pages": [min(part_pages), max(part_pages)] if part_pages else None,
                        "page_list": sorted(set(part_pages)),  # pages whose text starts in this part
                        "ready": meta is not None,
                        "duration": meta["duration"] if meta else None,
                        "marks": meta["marks"] if meta else [],
                        "v": meta["v"] if meta else 0,
                    })
                    chunks_done += (z - a) if meta else 0
                ch["done_chunks"] = chunks_done
                done += chunks_done
            b["progress"] = {"done_chunks": done, "total_chunks": total, "eta_seconds": None, "current_chapter": None, "stage": "voicing"}

        book = book_store.update(book_id, init)
        if book_id in self.pending_pages:
            from .store import locate

            where = locate(book, self.pending_pages.pop(book_id))
            if where and where["part"] is not None:
                self.priority[book_id] = (where["chapter"], where["part"])
        remaining_audio = sum(
            c.est_seconds * rate / pace for ch in book["chapters"] if ch["index"] in plans and not is_done(ch) for c in plans[ch["index"]][0]
        )
        done_total = book["progress"]["done_chunks"]
        last_save = [0.0]
        chapter_done = {ch["index"]: is_done(ch) for ch in book["chapters"] if ch["index"] in plans}

        def render_part(idx: int, p: int) -> None:
            nonlocal done_total, remaining_audio
            plan = plans[idx][0]
            a, z = parts[idx][p]
            work = work_dir(idx)

            def mark_rendering(b: dict) -> None:
                b["chapters"][idx]["status"] = "rendering"
                b["progress"]["current_chapter"] = idx

            book_store.update(book_id, mark_rendering)
            earlier = sum(z2 - a2 for q, (a2, z2) in enumerate(parts[idx]) if part_meta(idx, q))
            for k in range(a, z):
                self._check(book_id)
                chunk = plan[k]
                path = work / f"{k:05d}.wav"
                seconds = min(target_budget, max(0.5, chunk.est_seconds * rate / pace))
                if not path.exists():
                    text = speech_text(chunk.text)
                    started = time.time()
                    if preset:
                        raw = engine_manager.run(lambda: engine.speak_preset(chosen.engine_voice, text, lang, pace))
                    else:
                        raw = engine_manager.run(lambda: engine.speak(ref_wav, text, seconds, seed))
                    engine_manager.record_speed(len(raw) / sr, time.time() - started)
                    clip = audio.level(audio.trim_silence(raw, sr), sr)
                    if len(clip) == 0:
                        log.warning("Chunk %s/%s/%s came out silent: %r", book_id, idx, k, text[:80])
                        clip = audio.silence(0.2, sr)
                    self._check(book_id)
                    audio.write_wav(path, clip, sr)
                    remaining_audio = max(0.0, remaining_audio - seconds)
                done_total += 1
                if time.time() - last_save[0] > 1.5:
                    last_save[0] = time.time()
                    eta = remaining_audio / engine_manager.realtime_factor if engine_manager.realtime_factor else None

                    def progress(b: dict, done_total=done_total, eta=eta, in_chapter=earlier + k - a + 1) -> None:
                        b["chapters"][idx]["done_chunks"] = in_chapter
                        b["progress"].update(done_chunks=done_total, eta_seconds=round(eta) if eta is not None else None)

                    book_store.update(book_id, progress)

            # the part is playable now: stitch its chunks (with pauses) and publish a small .m4a
            self._check(book_id)
            lead = LEAD_IN if p == 0 else 0.0
            tail = TAIL if p == len(parts[idx]) - 1 else 0.0
            pieces, marks, t = [audio.silence(lead, sr)], [], lead
            prev_page = plan[a - 1].page if a > 0 else None
            for k in range(a, z):
                chunk = plan[k]
                clip = audio.read_wav(work / f"{k:05d}.wav")
                seconds = len(clip) / sr
                if chunk.page and (k == a or chunk.page != prev_page):
                    marks.append([chunk.page, round(t, 2)])
                prev_page = chunk.page or prev_page
                # pages that start inside this chunk: estimate the moment from how far into its text they are
                for off, page in chunk.page_breaks:
                    marks.append([page, round(t + seconds * off / max(1, len(chunk.text)), 2)])
                    prev_page = page
                pieces += [clip, audio.silence(chunk.pause_after, sr)]
                t += seconds + chunk.pause_after
            pieces.append(audio.silence(tail, sr))
            pcm = np.concatenate(pieces)
            part_wav = work / f"part{p:03d}.wav"
            audio.write_wav(part_wav, pcm, sr)
            part_file(folder, idx, p).parent.mkdir(exist_ok=True)
            audio.encode_m4a(part_wav, part_file(folder, idx, p), settings.aac_bitrate, {"title": book["title"]})
            meta = {"duration": round(len(pcm) / sr, 3), "marks": marks, "v": int(time.time() * 1000)}
            (work / f"part{p:03d}.json").write_text(json.dumps(meta))

            def part_ready(b: dict) -> None:
                c = b["chapters"][idx]
                c["parts"][p].update(ready=True, **meta)
                c["done_chunks"] = sum(z2 - a2 for (a2, z2), pt in zip(parts[idx], c["parts"]) if pt["ready"])
                b["progress"]["done_chunks"] = done_total

            book_store.update(book_id, part_ready)

        def finish_chapter(idx: int) -> None:
            """All parts done: the chapter becomes one file (.m4a to stream, .mp3 to download, .flac master)."""
            ch = book_store.load(book_id)["chapters"][idx]
            work = work_dir(idx)
            pcm_parts = [audio.read_wav(work / f"part{p:03d}.wav") for p in range(len(parts[idx]))]
            page_marks, offset = [], 0.0
            for pt, pcm_part in zip(ch["parts"], pcm_parts):
                for page, t in pt["marks"]:
                    if not page_marks or page_marks[-1][0] != page:  # one mark per page change
                        page_marks.append([page, round(offset + t, 2)])
                offset += len(pcm_part) / sr
            pcm = np.concatenate(pcm_parts)
            chapter_wav = work / "chapter.wav"
            audio.write_wav(chapter_wav, pcm, sr)
            base = folder / "audio" / str(idx)
            tags = chapter_tags(book, ch, plans)
            audio.encode_chapter(chapter_wav, base, settings.aac_bitrate, settings.mp3_bitrate, tags)
            for f in work.glob("*.wav"):
                f.unlink(missing_ok=True)
            key, duration = plans[idx][1], len(pcm) / sr

            def chapter_ready(b: dict) -> None:
                c = b["chapters"][idx]
                c.update(status="ready", duration=round(duration, 2), plan_key=key, done_chunks=c["total_chunks"], page_marks=page_marks)
                c["audio_version"] = c.get("audio_version", 0) + 1

            book_store.update(book_id, chapter_ready)
            chapter_done[idx] = True

        # chapters whose parts all finished before a restart
        for idx in plans:
            if not chapter_done[idx] and all(part_meta(idx, p) for p in range(len(parts[idx]))):
                finish_chapter(idx)

        # voice parts in listening order, starting wherever the listener asked (start page / "go to page")
        while True:
            self._check(book_id)
            todo = [(idx, p) for idx in sorted(plans) if not chapter_done[idx] for p in range(len(parts[idx])) if part_meta(idx, p) is None]
            if not todo:
                break
            want = self.priority.get(book_id)
            idx, p = next((item for item in todo if want and item >= want), todo[0])
            render_part(idx, p)
            if all(part_meta(idx, q) for q in range(len(parts[idx]))):
                finish_chapter(idx)
        book_store.update(book_id, lambda b: b["progress"].update(done_chunks=done_total))

        # whole book, encoded once from the lossless chapter masters: .m4b (Apple Books + the app's continuous
        # stream, which keeps playing on a locked phone) and .mp3, both with chapter markers
        self._check(book_id)
        book_store.update(book_id, lambda b: b["progress"].update(stage="packaging", current_chapter=None))
        book = book_store.load(book_id)
        included = [c for c in book["chapters"] if c["index"] in plans]
        for c in included:
            backfill(c)
        offsets = audio.build_book(
            # chapter markers name their part too, so the list is readable in Apple Books
            [(folder / "audio" / f"{c['index']}.flac", f"{c['part']} · {c['title']}" if c.get("part") else c["title"],
              float(c["duration"])) for c in included],
            folder / "book.m4b",
            folder / "book.mp3",
            {
                "title": book["title"], "album": book["title"], "artist": book["author"] or voice_store_name(book),
                "album_artist": book["author"] or "", "genre": "Audiobook",
                "comment": f"Narrated by {book.get('voice_name') or 'AuK'} (AuK)",
            },
            folder / "cover.jpg",
            settings.aac_bitrate,
            settings.mp3_bitrate,
        )
        shutil.rmtree(folder / "work", ignore_errors=True)
        for leftover in list(folder.glob("chapters-mp3*.zip")) + list((folder / "audio").glob("*.part*.m4a")):
            leftover.unlink(missing_ok=True)
        self.priority.pop(book_id, None)
        starts = {c["index"]: off for c, off in zip(included, offsets)}

        def finished(b: dict) -> None:
            if b["status"] != "rendering":
                return
            b["status"] = "ready"
            b["made_with"] = engine.name  # lets the app flag books narrated by another engine (e.g. demo tones)
            b["m4b_version"] = b.get("m4b_version", 0) + 1
            for c in b["chapters"]:
                c["offset"] = starts.get(c["index"])
                c["parts"] = []  # the finished book plays as one stream; parts were only for listening early
            b["progress"].update(done_chunks=b["progress"]["total_chunks"], eta_seconds=0, current_chapter=None, stage="done")

        book_store.update(book_id, finished)
        log.info("Finished %s (%s)", book_id, book["title"])


LEAD_IN = 0.6  # silence before a chapter
TAIL = 1.4  # silence after a chapter
PART_SECONDS = 30.0  # a playable piece while the book is still being made


def make_parts(plan: list[Chunk], rate: float, pace: float) -> list[tuple[int, int]]:
    """Group a chapter's chunks into ~30 s parts (chunk index ranges)."""
    out, start, acc = [], 0, 0.0
    for k, chunk in enumerate(plan):
        seconds = chunk.est_seconds * rate / pace + chunk.pause_after
        if k > start and acc + seconds > PART_SECONDS:
            out.append((start, k))
            start, acc = k, 0.0
        acc += seconds
    out.append((start, len(plan)))
    return out


def part_file(folder: Path, idx: int, p: int) -> Path:
    return folder / "audio" / f"{idx}.part{p:03d}.m4a"


def voice_store_name(book: dict) -> str:
    return book.get("voice_name") or "AuK"


def chapter_tags(book: dict, ch: dict, plans: dict) -> dict[str, str]:
    order = sorted(plans)
    return {
        "title": ch["title"], "album": book["title"], "artist": book["author"] or voice_store_name(book),
        "track": f"{order.index(ch['index']) + 1}/{len(order)}", "genre": "Audiobook",
    }


renderer = Renderer()
