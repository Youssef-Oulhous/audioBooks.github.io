"""Books on disk: one folder per book with book.json (state), text.json (chapter paragraphs),
source.pdf, cover.jpg, voice.wav (the narrator sample frozen at render time), audio/<n>.m4a|.mp3|.flac
per chapter, and book.m4b / book.mp3 for the whole book."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import chapters as chapter_detection
from .config import settings
from .engines import engine_traits
from .pdf_extract import extract, render_cover
from .textprep import detect_language, narration_language


EDITABLE = {"draft", "paused", "error", "ready"}


class BookNotFound(KeyError):
    pass


class BookStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()

    @property
    def root(self) -> Path:
        return settings.books_dir

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, book_id: str) -> Path:
        if not re.fullmatch(r"bk_[0-9a-f]{8}", book_id):
            raise BookNotFound(book_id)
        return self.root / book_id

    # ---------------------------------------------------------------- persistence

    def _write_json(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def load(self, book_id: str) -> dict:
        path = self.dir(book_id) / "book.json"
        with self.lock:
            if not path.exists():
                raise BookNotFound(book_id)
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, book: dict) -> None:
        with self.lock:
            self._write_json(self.dir(book["id"]) / "book.json", book)

    def update(self, book_id: str, fn: Callable[[dict], None]) -> dict:
        with self.lock:
            book = self.load(book_id)
            fn(book)
            self.save(book)
            return book

    def text(self, book_id: str) -> list[list[str]]:
        return json.loads((self.dir(book_id) / "text.json").read_text(encoding="utf-8"))["chapters"]

    def breaks(self, book_id: str) -> list[list[list]]:
        """Where new pages start inside each paragraph: [(character offset, page)] per paragraph."""
        data = json.loads((self.dir(book_id) / "text.json").read_text(encoding="utf-8"))
        stored = data.get("breaks")
        if stored and len(stored) == len(data["chapters"]):
            return stored
        return [[[] for _ in paras] for paras in data["chapters"]]

    def pages(self, book_id: str) -> list[list[int]]:
        """PDF page (1-based) of every paragraph, per chapter. Books imported before pages were
        tracked get them by re-reading their PDF; if the layout no longer lines up, each paragraph
        falls back to its chapter's first page."""
        path = self.dir(book_id) / "text.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if "pages" in data and len(data["pages"]) == len(data["chapters"]):
            return data["pages"]
        book = self.load(book_id)
        fallback = [[c.get("start_page", 1)] * len(paras) for c, paras in zip(book["chapters"], data["chapters"])]
        try:
            ex = extract(str(self.dir(book_id) / "source.pdf"), book["title"])
            detected = chapter_detection.detect(ex, book["language"]).chapters
            if len(detected) == len(data["chapters"]):
                data["pages"] = [
                    d.pages if d.paragraphs == stored else fb
                    for d, stored, fb in zip(detected, data["chapters"], fallback)
                ]
            else:
                data["pages"] = fallback
        except Exception:
            data["pages"] = fallback
        with self.lock:
            self._write_json(path, data)
        return data["pages"]

    def list(self) -> list[dict]:
        books = []
        with self.lock:
            for folder in self.root.glob("bk_*"):
                try:
                    books.append(self.load(folder.name))
                except (BookNotFound, json.JSONDecodeError):
                    continue
        return sorted(books, key=lambda b: b.get("created_at", 0), reverse=True)

    def delete(self, book_id: str) -> None:
        folder = self.dir(book_id)
        if not folder.exists():
            raise BookNotFound(book_id)
        shutil.rmtree(folder, ignore_errors=True)

    # ---------------------------------------------------------------- import

    def create(self, pdf_path: Path, filename: str) -> dict:
        book_id = "bk_" + uuid.uuid4().hex[:8]
        folder = self.dir(book_id)
        folder.mkdir(parents=True)
        try:
            source = folder / "source.pdf"
            shutil.move(str(pdf_path), source)
            fallback = _title_from_filename(filename)
            ex = extract(str(source), fallback)
            all_text = " ".join(p.text for p in ex.paras)
            if len(all_text.split()) < 20 and len(all_text) < 200:
                raise ValueError(
                    "This PDF has no selectable text — it's probably scanned page images. "
                    "Run it through OCR first (e.g. 'Scan text' in the Files app or ocrmypdf), then upload again."
                )
            detected = detect_language(all_text)
            lang, warning = narration_language(detected, engine_traits(settings.engine)["languages"])
            detection = chapter_detection.detect(ex, lang)
            has_cover = render_cover(str(source), str(folder / "cover.jpg"))
            book = {
                "id": book_id,
                "title": ex.title,
                "author": ex.author,
                "language": lang,
                "detected_language": detected,
                "language_warning": warning,
                "pages": ex.pages,
                "created_at": int(time.time()),
                "source_name": filename,
                "has_cover": has_cover,
                "detection": {"method": detection.method, "note": detection.note},
                "page_labels": ex.labels,  # the page number the book itself prints on each PDF page
                "status": "draft",
                "error": None,
                "voice_id": None,
                "voice_name": None,
                "pace": 1.0,
                "progress": None,
                "m4b_version": 0,
                "chapters": [
                    {
                        "index": i,
                        "title": c.title,
                        "spoken_title": c.spoken_title,
                        "include": c.include,
                        "words": c.words,
                        "est_seconds": c.est_seconds,
                        "preview": c.preview,
                        "start_page": c.start_page,
                        "part": c.part,
                        "sections": [{"title": t, "page": p} for t, p in c.sections],
                        "pages": [min(c.pages), max(c.pages)] if c.pages else None,
                        "status": "pending" if c.include else "skipped",
                        "done_chunks": 0,
                        "total_chunks": 0,
                        "duration": None,
                        "audio_version": 0,
                        "plan_key": None,
                    }
                    for i, c in enumerate(detection.chapters)
                ],
            }
            self._write_json(folder / "text.json", {
                "chapters": [c.paragraphs for c in detection.chapters],
                "pages": [c.pages for c in detection.chapters],
                "breaks": [c.para_breaks for c in detection.chapters],
            })
            self.save(book)
            return book
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def refresh_text(self, book_id: str) -> list[int]:
        """Re-read the PDF with the current extractor (after text-cleanup fixes) while keeping the
        user's chapter choices. Returns the chapters whose text changed; re-rendering the book then
        re-voices only those. Does nothing if the chapter structure came out differently."""
        book = self.load(book_id)
        ex = extract(str(self.dir(book_id) / "source.pdf"), book["title"])
        detected = chapter_detection.detect(ex, book["language"]).chapters
        old = self.text(book_id)
        if len(detected) != len(book["chapters"]):
            return []
        changed = [i for i, (d, o) in enumerate(zip(detected, old)) if d.paragraphs != o]

        def structure(b: dict) -> None:
            b["page_labels"] = ex.labels
            for c, d in zip(b["chapters"], detected):
                c["part"], c["sections"] = d.part, [{"title": t, "page": p} for t, p in d.sections]
                c.setdefault("pages", None)

        def apply(b: dict) -> None:
            for i in changed:
                c, d = b["chapters"][i], detected[i]
                c.update(words=d.words, est_seconds=d.est_seconds, preview=d.preview)

        with self.lock:
            self._write_json(self.dir(book_id) / "text.json", {
                "chapters": [d.paragraphs for d in detected], "pages": [d.pages for d in detected],
                "breaks": [d.para_breaks for d in detected],
            })
            self.update(book_id, structure)
            self.update(book_id, apply)
        return changed

    def ensure_page_info(self, book: dict) -> dict:
        """Books finished before pages were tracked: derive each chapter's page range from its text and
        estimate where each page starts by how far into the chapter's text it begins."""
        missing = [c for c in book["chapters"] if c["include"] and c["status"] == "ready" and not c.get("page_marks")]
        if not missing:
            return book
        texts, pages = self.text(book["id"]), self.pages(book["id"])

        def fill(b: dict) -> None:
            for c in b["chapters"]:
                if not (c["include"] and c["status"] == "ready" and not c.get("page_marks")):
                    continue
                paras, nums = texts[c["index"]], pages[c["index"]]
                if not paras or not nums:
                    continue
                total = sum(len(t) for t in paras) or 1
                usable = max(0.0, float(c["duration"] or 0) - 2.0)  # minus lead-in and tail silence
                marks, seen, pos = [], None, 0
                for text, num in zip(paras, nums):
                    if num != seen:
                        marks.append([num, round(0.6 + usable * pos / total, 2)])
                        seen = num
                    pos += len(text)
                c["pages"] = [min(nums), max(nums)]
                c["page_marks"] = marks
                c["page_marks_estimated"] = True

        return self.update(book["id"], fill)

    # ---------------------------------------------------------------- API view

    def to_api(self, book: dict) -> dict:
        bid = book["id"]
        chapters = []
        for c in book["chapters"]:
            ready = c["status"] == "ready"
            chapters.append({
                "index": c["index"], "title": c["title"], "include": c["include"], "words": c["words"],
                "est_seconds": c["est_seconds"], "preview": c["preview"],
                "status": c["status"] if c["include"] else "skipped",
                "done_chunks": c["done_chunks"], "total_chunks": c["total_chunks"],
                "duration": c["duration"] if ready else None,
                "audio_url": f"/api/books/{bid}/audio/{c['index']}.m4a?v={c['audio_version']}" if ready else None,
                "mp3_url": f"/api/books/{bid}/audio/{c['index']}.mp3?v={c['audio_version']}" if ready else None,
                "offset": c.get("offset") if ready and book["status"] == "ready" else None,
                "first_page": user_page(book, (c.get("pages") or [0, 0])[0]) if c.get("pages") else None,
                "last_page": user_page(book, (c.get("pages") or [0, 0])[1]) if c.get("pages") else None,
                "first_page_label": label_of(book, (c.get("pages") or [0])[0]) if c.get("pages") else None,
                "part": c.get("part"),
                "sections": [
                    {"title": sec["title"], "page": user_page(book, sec["page"]), "page_label": label_of(book, sec["page"])}
                    for sec in c.get("sections") or []
                ],
                "page_marks": reader_marks(book, c.get("page_marks")) if ready else None,
                # ~30 s pieces playable while the rest of the book is still being made
                "parts": [
                    {
                        "index": p, "ready": pt["ready"], "duration": pt["duration"],
                        "first_page": user_page(book, (pt.get("pages") or [0, 0])[0]) if pt.get("pages") else None,
                        "last_page": user_page(book, (pt.get("pages") or [0, 0])[1]) if pt.get("pages") else None,
                        "marks": reader_marks(book, pt.get("marks")),
                        "first_page_label": label_of(book, (pt.get("pages") or [0])[0]) if pt.get("pages") else None,
                        "url": f"/api/books/{bid}/audio/{c['index']}/part/{p}.m4a?v={pt.get('v', 0)}" if pt["ready"] else None,
                    }
                    for p, pt in enumerate(c.get("parts") or [])
                ] if c["include"] and not ready else [],
                # exact part lengths of a finished chapter while the book is still being made, so a player
                # that was playing parts can switch to the chapter file at exactly the same second
                "part_durations": [pt["duration"] for pt in c.get("parts") or []] if ready and book["status"] != "ready" else None,
            })
        progress = book.get("progress")
        if progress:
            total = progress.get("total_chunks") or 0
            progress = {**progress, "percent": round(100.0 * progress.get("done_chunks", 0) / total, 1) if total else 0.0}
        return {
            "id": bid,
            "title": book["title"],
            "author": book["author"],
            "language": book["language"],
            "language_warning": book.get("language_warning"),
            "pages": book["pages"],
            "page_numbering": page_numbering(book),  # "printed" = the book's own numbers, "pdf" = file pages
            **_reader_page_range(book),
            "parts": [p for p in dict.fromkeys(c.get("part") for c in book["chapters"] if c["include"] and c.get("part")) if p],
            "created_at": book["created_at"],
            "cover_url": f"/api/books/{bid}/cover.jpg" if book.get("has_cover") else None,
            "detection": book["detection"],
            "status": book["status"],
            "error": book.get("error"),
            "voice_id": book.get("voice_id"),
            "voice_name": book.get("voice_name"),
            "made_with": book.get("made_with"),  # engine that narrated the finished audio: "auk" | "kokoro" | "demo"
            "voice_available": _voice_available(book.get("voice_id")),
            "pace": book.get("pace", 1.0),
            "progress": progress,
            "total_words": sum(c["words"] for c in book["chapters"] if c["include"]),
            "est_seconds": round(sum(c["est_seconds"] for c in book["chapters"] if c["include"]), 1),
            "audio_seconds": round(sum(c["duration"] or 0 for c in book["chapters"] if c["include"] and c["status"] == "ready"), 1),
            **self._book_files(book),
            "chapters": chapters,
        }


    def _book_files(self, book: dict) -> dict:
        finished = book["status"] == "ready" and book.get("m4b_version") and (self.dir(book["id"]) / "book.mp3").exists()
        v, bid = book.get("m4b_version"), book["id"]
        return {
            "m4b_url": f"/api/books/{bid}/book.m4b?v={v}" if finished else None,
            "stream_url": f"/api/books/{bid}/stream.m4a?v={v}" if finished else None,
            "mp3_url": f"/api/books/{bid}/book.mp3?v={v}" if finished else None,
            "mp3_zip_url": f"/api/books/{bid}/chapters-mp3.zip?v={v}" if finished else None,
        }


def _reader_page_range(book: dict) -> dict:
    """The lowest and highest page numbers the reader can type (the book's own numbering)."""
    if page_numbering(book) == "pdf":
        return {"first_page": 1, "last_page": book["pages"]}
    numbers = [int(x) for x in ((y or "").strip() for y in book.get("page_labels") or []) if x.isdigit()]
    return {"first_page": min(numbers) if numbers else 1, "last_page": max(numbers) if numbers else book["pages"]}


def page_numbering(book: dict) -> str:
    """"printed" when the book prints its own page numbers (what its index refers to), else "pdf"."""
    labels = book.get("page_labels") or []
    return "printed" if sum(1 for x in labels if (x or "").strip()) >= 0.3 * max(1, len(labels)) else "pdf"


def label_of(book: dict, pdf_page: int) -> str | None:
    labels = book.get("page_labels") or []
    return (labels[pdf_page - 1] or "").strip() or None if 0 < pdf_page <= len(labels) else None


def user_page(book: dict, pdf_page: int) -> int | None:
    """The number the reader sees on that page (None when it isn't a plain number, e.g. "xii")."""
    if page_numbering(book) == "pdf":
        return pdf_page
    label = label_of(book, pdf_page)
    return int(label) if label and label.isdigit() else None


def to_pdf_page(book: dict, page) -> int:
    """A page number as the reader types it (the book's own numbering) -> PDF page."""
    wanted = str(page).strip()
    labels = [(x or "").strip() for x in book.get("page_labels") or []]
    if page_numbering(book) == "printed":
        for i, label in enumerate(labels):
            if label.lower() == wanted.lower():  # exact page label, including "xii"
                return i + 1
    try:
        number = int(wanted)
    except (TypeError, ValueError):
        return 1
    if page_numbering(book) == "pdf":
        return max(1, number)
    numeric = [(int(x), i + 1) for i, x in enumerate(labels) if x.isdigit()]
    earlier = [pdf for value, pdf in numeric if value <= number]
    return earlier[-1] if earlier else (numeric[0][1] if numeric else max(1, number))


def reader_marks(book: dict, marks: list | None) -> list:
    """Page marks for the app: [page as the reader sees it, seconds]. Roman pages stay strings."""
    out = []
    for page, t in marks or []:
        out.append([user_page(book, page) or label_of(book, page) or page, t])
    return out


def _time_at(marks: list | None, page: int) -> float:
    """Seconds into a part/chapter where `page` begins: its first mark, or else where the paragraph
    running into that page started (the last mark on an earlier page)."""
    best = 0.0
    for mark_page, t in marks or []:
        if mark_page == page:
            return t
        if mark_page > page:
            break
        best = t
    return best


def locate(book: dict, page: int) -> dict | None:
    """Where a page is in the audio: which chapter/part, and at what second. `page` is the number the
    reader sees in the book (its own printed numbering when it has one).

    Pages that fall in skipped chapters (contents, notes…) resolve to the next narrated page."""
    chapters = [c for c in book["chapters"] if c["include"] and c.get("pages")]
    if not chapters:
        return None
    asked, page = page, to_pdf_page(book, page)
    skipped = next((c for c in book["chapters"] if not c["include"] and c.get("pages") and c["pages"][0] <= page <= c["pages"][1]), None)
    starts = [c for c in chapters if c["pages"][0] <= page]
    if not starts:
        ch = chapters[0]
    else:
        ch = starts[-1]
        later = chapters[chapters.index(ch) + 1:]
        if page > ch["pages"][1] + 1 and later:
            ch = later[0]  # the page is in a skipped part of the book: go to the next narrated chapter
    target = min(max(page, ch["pages"][0]), ch["pages"][1])
    bid = book["id"]
    out = {
        "page": asked, "resolved_page": user_page(book, target) or target, "page_label": label_of(book, target),
        "pdf_page": target, "chapter": ch["index"], "chapter_title": ch["title"], "part_title": ch.get("part"),
        "skipped_chapter_title": skipped["title"] if skipped else None,  # the page is in a chapter that isn't narrated
        "chapter_ready": ch["status"] == "ready", "chapter_time": None, "book_time": None,
        "part": None, "part_ready": False, "part_url": None, "part_time": None,
    }
    if ch["status"] == "ready":
        out["chapter_time"] = _time_at(ch.get("page_marks"), target)
        if book["status"] == "ready" and ch.get("offset") is not None:
            out["book_time"] = round(ch["offset"] + out["chapter_time"], 2)
    parts = [pt for pt in ch.get("parts") or []]
    if parts:
        def starts_here(pt: dict) -> list:
            return pt.get("page_list") or (list(range(pt["pages"][0], pt["pages"][1] + 1)) if pt.get("pages") else [])

        # the first part where text of this page starts; if no sentence starts on that page (one long
        # paragraph runs across it), the last part whose text starts before it
        p = next((i for i, pt in enumerate(parts) if target in starts_here(pt)), None)
        if p is None:
            p = max([i for i, pt in enumerate(parts) if starts_here(pt) and starts_here(pt)[0] <= target] or [0])
        pt = parts[p]
        out.update(part=p, part_ready=pt["ready"], part_time=_time_at(pt.get("marks"), target) if pt["ready"] else None)
        if pt["ready"]:
            out["part_url"] = f"/api/books/{bid}/audio/{ch['index']}/part/{p}.m4a?v={pt.get('v', 0)}"
    return out


def _voice_available(voice_id: str | None) -> bool:
    """Is the book's voice offered by the engine the server runs now?"""
    if not voice_id:
        return False
    from .voices import voice_store

    return voice_store.get(voice_id) is not None


def safe_filename(name: str, fallback: str = "audiobook") -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in " -_.,'()").strip(" .")
    return cleaned[:120] or fallback


def _title_from_filename(filename: str) -> str:
    stem = Path(filename or "Untitled").stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip() or "Untitled"
    return stem.title() if stem.islower() else stem


book_store = BookStore()
