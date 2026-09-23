"""Throwaway mock of the AuK audiobook API (docs/API.md) for front-end testing.

Serves ../app at / and fakes every /api endpoint with in-memory fixtures:
voices (some with samples), books in mixed states, simulated rendering progress,
PDF upload with chapter detection (PyMuPDF), covers and chapter audio.

NOTE: chapter "m4a", stream and "mp3" files are really 4 kHz 8-bit WAV — tiny and
generated on the fly. The stream is the chapters back to back, so Chapter.offset values
line up exactly with the audio.

Run:  ../server/.venv/bin/python mock_server.py --port 8799 [--token secret] [--demo]
Test hooks:  GET /__mock/engine?state=loading&demo=1&rf=12.5   GET /__mock/reset
             GET /__mock/finish?id=<book>  (finish a rendering book instantly)
             GET /__mock/rate?v=0  (render speed, parts per second; 0 stops the engine)
             GET /__mock/part?id=<book>&ch=<index>&k=<part>|all  (voice specific parts now)
             GET /__mock/inspect?id=<book>  (internal part plan, for assertions)
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import struct
import threading
import time
import zipfile
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pymupdf

APP_DIR = Path(__file__).resolve().parent.parent / "app"
LOCK = threading.RLock()
ARGS = None

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}


class ApiErr(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ------------------------------------------------------------------ audio

AUDIO_CACHE: dict[tuple, bytes] = {}


def wav_bytes(seconds: float, seed: int = 0, rate: int = 4000, base: float = 190.0) -> bytes:
    n = max(1, int(seconds * rate))
    t = np.arange(n, dtype=np.float32) / rate
    rng = np.random.default_rng(seed)
    syl = np.clip(np.sin(2 * np.pi * 3.6 * t + rng.uniform(0, 6)), 0, None) ** 1.5
    phrase = (np.sin(2 * np.pi * 0.21 * t + seed) > -0.55).astype(np.float32)
    f = base * (1 + 0.07 * np.sin(2 * np.pi * 0.8 * t))
    phase = 2 * np.pi * np.cumsum(f) / rate
    sig = 0.3 * syl * phrase * (np.sin(phase) + 0.3 * np.sin(2 * phase))
    pcm = np.clip(128 + sig * 110, 0, 255).astype(np.uint8).tobytes()
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def tone_pcm(n: int, seed: int, rate: int = 4000, base: float = 190.0) -> bytes:
    t = np.arange(n, dtype=np.float32) / rate
    rng = np.random.default_rng(seed)
    syl = np.clip(np.sin(2 * np.pi * 3.6 * t + rng.uniform(0, 6)), 0, None) ** 1.5
    phrase = (np.sin(2 * np.pi * 0.21 * t + seed) > -0.55).astype(np.float32)
    f = base * (1 + 0.07 * np.sin(2 * np.pi * 0.8 * t))
    phase = 2 * np.pi * np.cumsum(f) / rate
    sig = 0.3 * syl * phrase * (np.sin(phase) + 0.3 * np.sin(2 * phase))
    return np.clip(128 + sig * 110, 0, 255).astype(np.uint8).tobytes()


def wav_wrap(pcm: bytes, rate: int = 4000) -> bytes:
    return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8) + b"data" + struct.pack("<I", len(pcm)) + pcm


def part_pcm(book_id: str, ch: dict, k: int) -> bytes:
    pt = ch["parts"][k]
    key = ("part", book_id, ch["index"], k, pt["v"], pt["n"])
    with LOCK:
        if key not in AUDIO_CACHE:
            AUDIO_CACHE[key] = tone_pcm(pt["n"], seed=(ch["index"] * 131 + k * 7 + 3) % 100000)
        return AUDIO_CACHE[key]


def chapter_audio(book_id: str, ch: dict) -> bytes:
    """The chapter file is exactly its parts joined (like the real server)."""
    key = ("chapter", book_id, ch["index"], ch["ver"])
    with LOCK:
        if key in AUDIO_CACHE:
            return AUDIO_CACHE[key]
    data = wav_wrap(b"".join(part_pcm(book_id, ch, k) for k in range(len(ch["parts"]))))
    with LOCK:
        AUDIO_CACHE[key] = data
    return data


def voice_audio(v: dict) -> bytes:
    key = ("voice", v["id"], v["take"])
    with LOCK:
        if key not in AUDIO_CACHE:
            AUDIO_CACHE[key] = wav_bytes(6.0, seed=v["take"] * 13 + len(v["id"]), rate=8000, base=130 if v["gender"] == "male" else 230)
        return AUDIO_CACHE[key]


RATE_HZ = 4000


def pcm_seconds(duration: float) -> float:
    """Exact length of a generated chapter file (whole samples)."""
    return max(1, int(duration * RATE_HZ)) / RATE_HZ


def book_version(b: dict) -> int:
    return sum(c["ver"] for c in b["chapters"]) or 1


def stream_audio(b: dict) -> bytes:
    """Whole book as one WAV: included chapters back to back (offsets = cumulative lengths)."""
    key = ("stream", b["id"], book_version(b))
    with LOCK:
        if key in AUDIO_CACHE:
            return AUDIO_CACHE[key]
    parts = []
    for c in sorted(b["chapters"], key=lambda c: c["index"]):
        if c["include"] and c["status"] == "ready":
            parts.append(chapter_audio(b["id"], c)[44:])
    pcm = b"".join(parts)
    data = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE_HZ, RATE_HZ, 1, 8) + b"data" + struct.pack("<I", len(pcm)) + pcm
    with LOCK:
        AUDIO_CACHE[key] = data
    return data


def zip_of_chapters(b: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        n = 0
        for c in sorted(b["chapters"], key=lambda c: c["index"]):
            if c["include"] and c["status"] == "ready":
                n += 1
                z.writestr(f"{n:02d} - {c['title']}.mp3", chapter_audio(b["id"], c))
    return buf.getvalue()


# ------------------------------------------------------------------ covers

def make_cover(title: str, author: str, rgb: tuple[float, float, float]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    page.draw_rect(page.rect, color=None, fill=rgb)
    page.draw_rect(pymupdf.Rect(26, 26, 374, 574), color=(1, 0.92, 0.75), width=1.2)
    page.draw_line((60, 330), (340, 330), color=(1, 0.92, 0.75), width=0.8)
    font = "china-s" if re.search(r"[一-鿿]", title) else "tiro"
    page.insert_textbox(pymupdf.Rect(50, 150, 350, 320), title, fontsize=40, fontname=font, color=(1, 0.95, 0.85), align=1)
    page.insert_textbox(pymupdf.Rect(50, 350, 350, 420), author, fontsize=20, fontname="tiro", color=(1, 0.9, 0.7), align=1)
    return page.get_pixmap().tobytes("jpg", jpg_quality=85)


def cover_from_pdf(data: bytes) -> bytes | None:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        page = doc[0]
        zoom = 400 / page.rect.width
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("jpg", jpg_quality=85)
    except Exception:
        return None


# ------------------------------------------------------------------ state

S: dict = {}


def voice_public(v: dict) -> dict:
    out = {k: v[k] for k in ("id", "name", "language", "gender", "tagline", "description", "custom", "take")}
    out["preview_url"] = f"/api/voices/{v['id']}/preview.wav?v={v['take']}" if v["has"] else None
    return out


def mk_chapter(index, title, est, *, include=True, status="pending", done=0, total=None, preview=None, words=None, pages=None, part=None, sections=None):
    return {
        "index": index,
        "title": title,
        "include": include,
        "part": part,  # the part of the book this chapter sits in ("Part II"), or None
        "sections": sections or [],  # [{"title": ..., "page": <pdf page>}] navigation points inside it
        "words": words if words is not None else int(est * 2.5),
        "est_seconds": int(est),
        "preview": preview or LOREM[index % len(LOREM)],
        "status": "pending",
        "done_chunks": 0,
        "total_chunks": 0,
        "duration": None,
        "ver": 0,
        "pages": pages,
        "parts": [],
        "page_marks": None,
        "_want": status,  # fixture state applied once the parts are planned
        "_done": done,
        "_nparts": total,
    }


def page_numbering(b: dict) -> str:
    """"printed" when the book prints its own page numbers (what its index refers to), else "pdf"."""
    labels = b.get("page_labels") or []
    return "printed" if sum(1 for x in labels if (x or "").strip()) >= 0.3 * max(1, len(labels)) else "pdf"


def label_of(b: dict, pdf_page: int):
    labels = b.get("page_labels") or []
    return (labels[pdf_page - 1] or "").strip() or None if 0 < pdf_page <= len(labels) else None


def user_page(b: dict, pdf_page: int):
    """The number the reader sees on that page (None when it isn't a plain number, e.g. "xii")."""
    if page_numbering(b) == "pdf":
        return pdf_page
    label = label_of(b, pdf_page)
    return int(label) if label and label.isdigit() else None


def to_pdf_page(b: dict, page) -> int:
    """A page number (or label, e.g. "xii") as the reader types it -> PDF page."""
    wanted = str(page).strip()
    labels = [(x or "").strip() for x in b.get("page_labels") or []]
    if page_numbering(b) == "printed":
        for i, label in enumerate(labels):
            if label.lower() == wanted.lower():
                return i + 1
    try:
        number = int(wanted)
    except (TypeError, ValueError):
        return 1
    if page_numbering(b) == "pdf":
        return max(1, number)
    numeric = [(int(x), i + 1) for i, x in enumerate(labels) if x.isdigit()]
    earlier = [pdf for value, pdf in numeric if value <= number]
    return earlier[-1] if earlier else (numeric[0][1] if numeric else max(1, number))


def reader_marks(b: dict, marks: list | None) -> list:
    """Page marks for the app: [page as the reader sees it, seconds]. Roman pages stay strings."""
    return [[user_page(b, page) or label_of(b, page) or page, t] for page, t in marks or []]


def reader_range(b: dict) -> dict:
    """The lowest and highest page numbers the reader can type (the book's own numbering)."""
    if page_numbering(b) == "pdf":
        return {"first_page": 1, "last_page": b["pages"]}
    numbers = [int(x) for x in ((y or "").strip() for y in b.get("page_labels") or []) if x.isdigit()]
    return {"first_page": min(numbers) if numbers else 1, "last_page": max(numbers) if numbers else b["pages"]}


PART_SECONDS = 30.0


def plan_chapter(b: dict, c: dict) -> None:
    """~30 s parts with exact sample lengths, page ranges and [page, seconds] marks."""
    est = c["est_seconds"] / pace_of(b)
    n = c.get("_nparts") or max(2, round(est / PART_SECONDS))
    w = [1 + 0.18 * math.sin(k * 1.7 + c["index"]) for k in range(n)]
    samples = [max(RATE_HZ, round(est * x / sum(w) * RATE_HZ)) for x in w]
    total = sum(samples) / RATE_HZ
    fp, lp = c["pages"]
    pages = list(range(fp, lp + 1))
    starts = [0.6 + i * max(0.0, total - 1.2) / len(pages) for i in range(len(pages))]
    parts, s = [], 0.0
    for k, ns in enumerate(samples):
        d = ns / RATE_HZ
        e = s + d
        before = [p for p, st in zip(pages, starts) if st <= s + 1e-6]
        p0 = before[-1] if before else fp
        marks = [[p0, 0.6 if k == 0 else 0.3]]
        for p, st in zip(pages, starts):
            if s + 1e-6 < st < e - 1e-6 and p != marks[-1][0]:
                marks.append([p, round(st - s, 2)])
        parts.append({"n": ns, "dur": d, "ready": False, "v": 0, "pages": [p0, marks[-1][0]], "marks": marks})
        s = e
    c["parts"] = parts
    c["total_chunks"] = len(parts)


def refresh_chapter(c: dict) -> None:
    n_ready = sum(1 for p in c["parts"] if p["ready"])
    c["done_chunks"] = n_ready
    c["total_chunks"] = len(c["parts"])
    if c["parts"] and n_ready == len(c["parts"]):
        if c["status"] != "ready":
            c["status"] = "ready"
            c["ver"] += 1
        c["duration"] = sum(p["n"] for p in c["parts"]) / RATE_HZ
        marks, off = [], 0.0
        for p in c["parts"]:
            for page, t in p["marks"]:
                if not marks or marks[-1][0] != page:
                    marks.append([page, round(off + t, 2)])
            off += p["dur"]
        c["page_marks"] = marks
    else:
        c["status"] = "rendering" if n_ready else "pending"
        c["duration"] = None
        c["page_marks"] = None


def start_planned(b: dict) -> None:
    """The render reached this book: plan its parts, then honour a start page asked for earlier."""
    if b.get("planned"):
        return
    plan_book(b, reset=True)
    for c in b["chapters"]:
        if c["include"]:
            refresh_chapter(c)
    b["planned"] = True
    if b.get("pending_page"):
        prioritize(b, b.pop("pending_page"))


def plan_book(b: dict, reset: bool = False) -> None:
    page = 1
    for c in sorted(b["chapters"], key=lambda c: c["index"]):
        if not c.get("pages"):
            span = max(1, round(c["est_seconds"] / 60)) if not c["include"] else max(2, round(c["est_seconds"] / 120))
            c["pages"] = [page, page + span - 1]
        page = c["pages"][1] + 1
        if c["include"] and (reset or not c["parts"]):
            plan_chapter(b, c)
    b["pages"] = max(b.get("pages") or 0, page - 1)


def mark_part(c: dict, k: int) -> None:
    c["parts"][k]["ready"] = True
    c["parts"][k]["v"] = int(time.time() * 1000) % 1000000000


def offset_of(b: dict, ch: dict) -> float:
    off = 0.0
    for c in sorted(b["chapters"], key=lambda c: c["index"]):
        if c["index"] == ch["index"]:
            return off
        if c["include"] and c["status"] == "ready":
            off += c["duration"]
    return off


def time_at(marks, page):
    best = 0.0
    for mp, t in marks or []:
        if mp == page:
            return t
        if mp > page:
            break
        best = t
    return best


def locate(b: dict, page) -> dict | None:
    """Mirror of the server's locate(). `page` is the number the reader sees in the book (or its
    label, e.g. "xii"); pages in skipped chapters go to the next narrated page."""
    if not b.get("planned"):
        return None  # like the server: parts (and page ranges) are planned when the render starts
    chs = [c for c in sorted(b["chapters"], key=lambda c: c["index"]) if c["include"] and c.get("pages")]
    if not chs:
        return None
    asked, page = page, to_pdf_page(b, page)
    skipped = next((c for c in b["chapters"] if not c["include"] and c.get("pages") and c["pages"][0] <= page <= c["pages"][1]), None)
    starts = [c for c in chs if c["pages"][0] <= page]
    if not starts:
        ch = chs[0]
    else:
        ch = starts[-1]
        later = chs[chs.index(ch) + 1:]
        if page > ch["pages"][1] + 1 and later:
            ch = later[0]
    target = min(max(page, ch["pages"][0]), ch["pages"][1])
    out = {"page": asked, "resolved_page": user_page(b, target) or target, "page_label": label_of(b, target), "pdf_page": target,
           "chapter": ch["index"], "chapter_title": ch["title"], "part_title": ch.get("part"),
           "skipped_chapter_title": skipped["title"] if skipped else None,
           "chapter_ready": ch["status"] == "ready",
           "chapter_time": None, "book_time": None, "part": None, "part_ready": False, "part_url": None, "part_time": None}
    if ch["status"] == "ready":
        out["chapter_time"] = time_at(ch.get("page_marks"), target)
        if b["status"] == "ready":
            out["book_time"] = round(offset_of(b, ch) + out["chapter_time"], 2)
    parts = ch["parts"]
    if parts:
        # mirrors the server: the first part where text of this page starts, else the last part
        # whose text starts before it (a paragraph running across the page break)
        k = next((i for i, pt in enumerate(parts) if pt["pages"][0] <= target <= pt["pages"][1]), None)
        if k is None:
            k = max([i for i, pt in enumerate(parts) if pt["pages"][0] <= target] or [0])
        pt = parts[k]
        out.update(part=k, part_ready=pt["ready"], part_time=time_at(pt["marks"], target) if pt["ready"] else None)
        if pt["ready"] and ch["status"] != "ready":
            out["part_url"] = f"/api/books/{b['id']}/audio/{ch['index']}/part/{k}.m4a?v={pt['v']}"
    return out


def prioritize(b: dict, page) -> dict:
    where = locate(b, page)
    if where and where["part"] is not None and not where["part_ready"]:
        b["cursor"] = (where["chapter"], where["part"])
    elif where is None:
        b["pending_page"] = page  # applied once the parts are planned
    return where or {"page": page, "part": None, "part_ready": False}


LOREM = [
    "It was on a dreary night of November that I beheld the accomplishment of my toils. With an anxiety that almost amounted to agony, I collected the instruments of life around me.",
    "You will rejoice to hear that no disaster has accompanied the commencement of an enterprise which you have regarded with such evil forebodings.",
    "The Time Traveller (for so it will be convenient to speak of him) was expounding a recondite matter to us. His grey eyes shone and twinkled.",
    "Begin the morning by saying to thyself, I shall meet with the busy-body, the ungrateful, arrogant, deceitful, envious, unsocial.",
    "Call me Ishmael. Some years ago—never mind how long precisely—having little or no money in my purse, I thought I would sail about a little.",
    "It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife.",
]


def fixtures() -> None:
    now = time.time()
    S.clear()
    S["engine"] = {"name": "demo" if ARGS.demo else "auk", "label": "Demo engine (beeps)" if ARGS.demo else "AuK-Flash on cuda", "state": "ready", "detail": None, "is_demo": bool(ARGS.demo), "realtime_factor": 12.5}
    S["voices"] = [
        dict(id="james", name="James", language="en", gender="male", tagline="Deep, calm classic narrator", description="A calm male narrator in his fifties with a deep, warm, unhurried voice.", custom=False, take=3, has=True),
        dict(id="emma", name="Emma", language="en", gender="female", tagline="Warm, clear storyteller", description="A warm female storyteller in her thirties, clear and friendly.", custom=False, take=1, has=True),
        dict(id="oliver", name="Oliver", language="en", gender="male", tagline="Bright, youthful and lively", description="A bright young man with lively energy.", custom=False, take=0, has=False),
        dict(id="sophia", name="Sophia", language="en", gender="female", tagline="Soft and intimate — lovely for fiction", description="A soft, intimate female voice, close to the microphone.", custom=False, take=0, has=False),
        dict(id="arthur", name="Arthur", language="en", gender="male", tagline="Gravelly old-world raconteur", description="An old gentleman with a gravelly voice who loves a good story.", custom=False, take=2, has=True),
        dict(id="grace", name="Grace", language="en", gender="female", tagline="Crisp and articulate, great for non-fiction", description="A crisp, articulate woman, precise diction.", custom=False, take=0, has=False),
        dict(id="mei", name="Mei", language="zh", gender="female", tagline="温柔清晰的女声 · gentle and clear", description="温柔清晰的年轻女声。", custom=False, take=1, has=True),
        dict(id="lei", name="Lei", language="zh", gender="male", tagline="沉稳的男声 · steady and grounded", description="沉稳有力的中年男声。", custom=False, take=0, has=False),
    ]
    S["books"] = {}
    S["files"] = {}
    S["stats"] = {}
    S["last_tick"] = now
    S["budget"] = 0.0
    if ARGS.empty:
        return

    def add(bid, title, author, created, status, chapters, *, voice=None, pace=1.0, error=None, detection=None, color=(0.25, 0.2, 0.15), language="en", warning=None, pages=300, labels=None):
        b = {
            "id": bid, "title": title, "author": author, "language": language, "language_warning": warning,
            "page_labels": labels or [],  # what the book prints on each PDF page ("i".."xii", then "1"...)
            "pages": 0, "created_at": int(created), "detection": detection or {"method": "outline", "note": f"Found {len(chapters)} chapters in the PDF's bookmarks."},
            "status": status, "error": error, "voice_id": voice, "voice_name": next((v["name"] for v in S["voices"] if v["id"] == voice), None),
            "pace": pace, "chapters": chapters, "queued_at": created, "made_with": ("demo" if ARGS.demo else "auk") if status == "ready" else None,
        }
        plan_book(b)
        b["planned"] = True
        for c in b["chapters"]:
            if not c["include"]:
                continue
            want, done = c.pop("_want"), c.pop("_done")
            c.pop("_nparts", None)
            if want == "ready":
                for k in range(len(c["parts"])):
                    mark_part(c, k)
            elif want == "rendering":
                for k in range(min(done, len(c["parts"]))):
                    mark_part(c, k)
                b["cursor"] = (c["index"], done)
            refresh_chapter(c)
        for c in b["chapters"]:
            for key in ("_want", "_done", "_nparts"):
                c.pop(key, None)
        S["books"][bid] = b
        S["files"][bid] = {"cover": make_cover(title, author, color)}

    fr_titles = ["Contents", "Letter 1", "Letter 2", "Letter 3", "Letter 4", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4", "Chapter 5", "Chapter 6", "Chapter 7", "Index"]
    fr_est = [60, 420, 380, 240, 610, 520, 700, 560, 820, 640, 700, 760, 90]
    fr = []
    for i, (t, e) in enumerate(zip(fr_titles, fr_est)):
        if i in (0, 12):
            fr.append(mk_chapter(i, t, e, include=False))
        elif i <= 5:
            fr.append(mk_chapter(i, t, e, status="ready"))
        elif i == 6:
            fr.append(mk_chapter(i, t, e, status="rendering", done=5, total=12))
        else:
            fr.append(mk_chapter(i, t, e))
    add("bk_frank001", "Frankenstein", "Mary Shelley", now - 3600, "rendering", fr, voice="james", color=(0.16, 0.22, 0.2))

    tm_titles = ["I. Introduction", "II. The Machine", "III. The Time Traveller Returns", "IV. Time Travelling", "V. In the Golden Age", "VI. The Sunset of Mankind", "VII. A Sudden Shock", "VIII. Explanation", "IX. The Morlocks", "X. When Night Came", "XI. The Palace of Green Porcelain", "XII. In the Darkness"]
    tm_est = [520, 300, 610, 480, 700, 390, 640, 560, 450, 600, 530, 580]
    tm = [mk_chapter(i, t, e, status="ready") for i, (t, e) in enumerate(zip(tm_titles, tm_est))]
    add("bk_time002", "The Time Machine", "H. G. Wells", now - 86400 * 3, "ready", tm, voice="emma", color=(0.12, 0.16, 0.28))

    wd = [mk_chapter(i, t, e, status="ready") for i, (t, e) in enumerate(zip(["Economy", "Where I Lived", "Reading", "Sounds", "Solitude", "Visitors", "The Bean-Field", "The Village"], [900, 620, 410, 700, 380, 520, 560, 300]))]
    add("bk_walden07", "Walden", "Henry David Thoreau", now - 86400 * 9, "ready", wd, voice="arthur", color=(0.14, 0.26, 0.2))

    cw = [mk_chapter(i, t, e, status="ready") for i, (t, e) in enumerate(zip(["Into the Primitive", "The Law of Club and Fang", "The Dominant Primordial Beast", "Who Has Won to Mastership", "The Toil of Trace and Trail", "For the Love of a Man", "The Sounding of the Call"], [640, 580, 700, 420, 820, 610, 900]))]
    add("bk_callw08", "The Call of the Wild", "Jack London", now - 86400 * 1.5, "ready", cw, voice="james", color=(0.28, 0.18, 0.1))

    med = [mk_chapter(i, f"Book {w}", e) for i, (w, e) in enumerate(zip(["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"], [300, 560, 610, 1400, 1100, 1500, 1300, 1200, 1000, 1250, 1150, 800]))]
    med.insert(0, mk_chapter(0, "Introduction by the Translator", 900))
    med.append(mk_chapter(13, "Notes", 400, include=False))
    for i, c in enumerate(med):
        c["index"] = i
    add("bk_medit003", "Meditations", "Marcus Aurelius", now - 600, "draft", med, detection={"method": "headings", "note": "Found 14 chapters from headings like “Book One”."}, color=(0.3, 0.12, 0.1))

    mb = [mk_chapter(i, t, e, status="ready" if i < 3 else "pending") for i, (t, e) in enumerate(zip(["Etymology", "Extracts", "Chapter 1. Loomings", "Chapter 2. The Carpet-Bag", "Chapter 3. The Spouter-Inn", "Chapter 4. The Counterpane", "Chapter 5. Breakfast", "Chapter 6. The Street"], [200, 500, 640, 520, 1500, 480, 300, 350]))]
    add("bk_moby004", "Moby-Dick; or, The Whale", "Herman Melville", now - 86400 * 7, "error", mb, voice="arthur", error="The voice engine ran out of GPU memory on chapter 4. Try again, or restart the server.", color=(0.1, 0.18, 0.3))

    pp = [mk_chapter(i, f"Chapter {i + 1}", e, status="ready" if i < 3 else "pending") for i, e in enumerate([480, 400, 620, 380, 300, 700, 650, 560, 420, 500])]
    add("bk_pride005", "Pride and Prejudice", "Jane Austen", now - 86400 * 14, "paused", pp, voice="emma", pace=1.1, color=(0.32, 0.22, 0.28))

    # A textbook: roman front matter, then arabic pages, 7 parts of 6 chapters, sections inside each.
    nav_parts = [
        ("Part I: Reading the Sky", ["The Horizon", "Stars and Their Names", "The Sun at Noon", "Twilight", "Clouds", "Keeping a Log"]),
        ("Part II: Instruments", ["The Sextant", "Chronometers", "The Compass", "Charts and Dividers", "The Lead Line", "Care and Repair"]),
        ("Part III: Position", ["Latitude by Meridian", "Longitude by Time", "Running Fixes", "Lines of Position", "Plotting", "Errors"]),
        ("Part IV: The Sea", ["Tides", "Currents", "Weather at Sea", "Swell and Waves", "Ice", "Fog"]),
        ("Part V: Passage Making", ["Planning a Passage", "Departure", "Landfall", "Pilotage", "Anchorages", "Harbours"]),
        ("Part VI: When Things Go Wrong", ["Lost Instruments", "Dead Reckoning Alone", "Heavy Weather", "Man Overboard", "Grounding", "Calling for Help"]),
        ("Part VII: The Modern Bridge", ["Satellites", "Radar", "Electronic Charts", "Autopilots", "Redundancy", "The Watch"]),
    ]
    nav = [
        mk_chapter(0, "Contents", 70, include=False, pages=[1, 4]),
        mk_chapter(1, "Preface", 300, status="ready", pages=[5, 8],
                   sections=[{"title": "About this edition", "page": 6}, {"title": "A note on units", "page": 7}]),
        # still being voiced, so its pages (roman) are read from the parts that are ready
        mk_chapter(2, "Introduction", 320, status="rendering", done=3, total=8, pages=[9, 12],
                   sections=[{"title": "What this book assumes", "page": 10}, {"title": "How the chapters run", "page": 11}]),
    ]
    nav_page = 13  # printed page 1
    idx = 3
    for pi, (part_title, titles) in enumerate(nav_parts):
        for ci, t in enumerate(titles):
            span = 7 + (ci % 3)
            secs = [
                {"title": f"{pi + 1}.{ci + 1}.{k + 1} {name}", "page": nav_page + min(span - 1, 1 + k * 2)}
                for k, name in enumerate(["Overview", "In practice", "Worked example", "Exercises", "Further reading"][: 3 + (ci % 3)])
            ]
            # first two parts are voiced, the third is being voiced now, the rest are waiting
            want = "ready" if pi < 2 else ("rendering" if pi == 2 and ci == 0 else "pending")
            nav.append(mk_chapter(idx, f"{pi + 1}.{ci + 1} {t}", 520 + 40 * ci, status=want, done=4 if want == "rendering" else 0,
                                  total=11 if want == "rendering" else None, pages=[nav_page, nav_page + span - 1], part=part_title, sections=secs))
            nav_page += span
            idx += 1
    nav.append(mk_chapter(idx, "Index", 80, include=False, pages=[nav_page, nav_page + 5]))
    nav_labels = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"] + [str(n) for n in range(1, nav_page + 6 - 12)]
    add("bk_navig009", "The Craft of Navigation", "H. R. Wexford", now - 1800, "rendering", nav, voice="emma",
        detection={"method": "outline", "note": f"Found {len(nav) - 2} chapters in 7 parts, with {sum(len(c['sections']) for c in nav)} sections, in the PDF’s bookmarks."},
        color=(0.1, 0.2, 0.3), labels=nav_labels)

    # a classic novel: 42 chapters in 7 parts, chapter numbers restarting in every part
    ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
    cp, page_at, idx = [], 1, 0
    cp.append(mk_chapter(idx, "Contents", 60, include=False, pages=[1, 2]))
    idx, page_at = 1, 3
    for pi in range(7):
        for ci in range(6):
            span = 14 + (ci % 4) * 3
            cp.append(mk_chapter(idx, f"Chapter {ROMAN[ci]}", 900 + 60 * ci, pages=[page_at, page_at + span - 1], part=f"Part {ROMAN[pi]}"))
            page_at += span
            idx += 1
    cp.append(mk_chapter(idx, "Epilogue", 700, pages=[page_at, page_at + 20]))
    add("bk_crime010", "Crime and Punishment", "Fyodor Dostoevsky", now - 240, "draft", cp,
        detection={"method": "outline", "note": "Found 43 chapters in 7 parts in the PDF’s bookmarks."}, color=(0.22, 0.1, 0.12))

    # a very long one: 357 chapters in 17 books, to keep the setup step honest about speed
    wp, page_at, idx = [], 1, 0
    for pi in range(17):
        n = 20 + (pi % 3) * 2
        for ci in range(n):
            span = 6 + (ci % 3)
            wp.append(mk_chapter(idx, f"Chapter {ci + 1}", 420 + 10 * (ci % 7), pages=[page_at, page_at + span - 1], part=f"Book {ROMAN[pi] if pi < len(ROMAN) else pi + 1}"))
            page_at += span
            idx += 1
    wp.append(mk_chapter(idx, "First Epilogue", 900, pages=[page_at, page_at + 25]))
    wp.append(mk_chapter(idx + 1, "Second Epilogue", 800, pages=[page_at + 26, page_at + 50]))
    add("bk_warpeace11", "War and Peace", "Leo Tolstoy", now - 180, "draft", wp,
        detection={"method": "headings", "note": f"Found {len(wp)} chapters in 17 books from headings like “Chapter 1”."}, color=(0.14, 0.14, 0.24))

    war = [mk_chapter(i, t, 420) for i, t in enumerate(["I. Laying Plans", "II. Waging War", "III. Attack by Stratagem", "IV. Tactical Dispositions", "V. Energy", "VI. Weak Points and Strong"])]
    add("bk_war006", "The Art of War", "Sun Tzu", now - 1200, "queued", war, voice="arthur", pace=0.9, color=(0.35, 0.1, 0.08))
    S["books"]["bk_war006"]["queued_at"] = now + 1  # after Frankenstein


def pace_of(b):
    return b.get("pace") or 1.0


def book_public(b: dict) -> dict:
    inc = [c for c in b["chapters"] if c["include"]]
    est = sum(c["est_seconds"] for c in inc)
    audio = sum(c["duration"] or 0 for c in inc if c["status"] == "ready")
    progress = None
    if b["status"] != "draft":
        done = sum(c["done_chunks"] for c in inc)
        total = sum(c["total_chunks"] for c in inc) or 1
        cur = next((c["index"] for c in inc if c["status"] == "rendering"), None)
        remaining_audio = sum(pt["dur"] for c in inc for pt in c["parts"] if not pt["ready"])
        eta = int(remaining_audio / S["engine"]["realtime_factor"]) if b["status"] in ("rendering", "queued") and S["engine"]["realtime_factor"] else None
        progress = {"done_chunks": done, "total_chunks": total, "percent": round(100 * done / total, 1), "eta_seconds": eta, "current_chapter": cur, "stage": "done" if b["status"] == "ready" else "voicing"}
    chapters = []
    ready_book = b["status"] == "ready"
    offset = 0.0
    for c in sorted(b["chapters"], key=lambda c: c["index"]):
        st = "skipped" if not c["include"] else c["status"]
        out = {k: c[k] for k in ("index", "title", "include", "words", "est_seconds", "preview", "done_chunks", "total_chunks")}
        out["status"] = st
        ok = c["status"] == "ready" and c["include"]
        out["duration"] = round(c["duration"], 4) if c["status"] == "ready" else None
        pages = c.get("pages")  # known from detection, as on the real server
        out["first_page"] = user_page(b, pages[0]) if pages else None
        out["last_page"] = user_page(b, pages[1]) if pages else None
        out["first_page_label"] = label_of(b, pages[0]) if pages else None
        out["part"] = c.get("part")
        out["sections"] = [{"title": sec["title"], "page": user_page(b, sec["page"]), "page_label": label_of(b, sec["page"])} for sec in c.get("sections") or []]
        out["page_marks"] = reader_marks(b, c.get("page_marks")) if ok else None
        out["parts"] = [
            {"index": k, "ready": pt["ready"], "duration": round(pt["dur"], 4) if pt["ready"] else None, "first_page": user_page(b, pt["pages"][0]), "last_page": user_page(b, pt["pages"][1]),
             "first_page_label": label_of(b, pt["pages"][0]),
             "marks": reader_marks(b, pt["marks"]) if pt["ready"] else [], "url": f"/api/books/{b['id']}/audio/{c['index']}/part/{k}.m4a?v={pt['v']}" if pt["ready"] else None}
            for k, pt in enumerate(c["parts"])
        ] if c["include"] and c["status"] != "ready" else []
        # exact part lengths of a finished chapter while the book is still being made
        out["part_durations"] = [round(pt["dur"], 4) for pt in c["parts"]] if ok and not ready_book and c["parts"] else None
        out["audio_url"] = f"/api/books/{b['id']}/audio/{c['index']}.m4a?v={c['ver']}" if ok else None
        out["mp3_url"] = f"/api/books/{b['id']}/audio/{c['index']}.mp3?v={c['ver']}" if ok else None
        out["offset"] = round(offset, 3) if ready_book and ok else None
        if ready_book and ok:
            offset += c["duration"]
        chapters.append(out)
    v = book_version(b)
    return {
        "id": b["id"], "title": b["title"], "author": b["author"], "language": b["language"], "language_warning": b["language_warning"],
        "pages": b["pages"], "created_at": b["created_at"],
        "page_numbering": page_numbering(b), **reader_range(b),
        "parts": [p for p in dict.fromkeys(c.get("part") for c in b["chapters"] if c["include"] and c.get("part")) if p], "cover_url": f"/api/books/{b['id']}/cover.jpg" if S["files"].get(b["id"], {}).get("cover") else None,
        "detection": b["detection"], "status": b["status"], "error": b["error"], "voice_id": b["voice_id"], "voice_name": b["voice_name"], "pace": b["pace"],
        "made_with": b.get("made_with"), "voice_available": True,
        "progress": progress, "total_words": sum(c["words"] for c in b["chapters"]), "est_seconds": int(est), "audio_seconds": round(audio, 1),
        "m4b_url": f"/api/books/{b['id']}/book.m4b?v={v}" if ready_book else None,
        "stream_url": f"/api/books/{b['id']}/stream.m4a?v={v}" if ready_book else None,
        "mp3_url": f"/api/books/{b['id']}/book.mp3?v={v}" if ready_book else None,
        "mp3_zip_url": f"/api/books/{b['id']}/chapters-mp3.zip?v={v}" if ready_book else None,
        "chapters": chapters,
    }


def tick() -> None:
    """Advance simulated rendering: ARGS.rate chunks per second for the book at the head of the queue."""
    with LOCK:
        now = time.time()
        dt = now - S["last_tick"]
        S["last_tick"] = now
        queue = sorted((b for b in S["books"].values() if b["status"] in ("queued", "rendering")), key=lambda b: b["queued_at"])
        if not queue:
            S["budget"] = 0
            return
        S["budget"] += dt * ARGS.rate
        while queue:
            b = queue[0]
            b["status"] = "rendering"
            start_planned(b)
            inc = [c for c in sorted(b["chapters"], key=lambda c: c["index"]) if c["include"]]
            todo = [(c, k) for c in inc if c["status"] != "ready" for k, pt in enumerate(c["parts"]) if not pt["ready"]]
            if not todo:
                b["status"] = "ready"
                b["made_with"] = "demo" if ARGS.demo else "auk"
                queue.pop(0)
                continue
            if S["budget"] < 1:
                break
            cur = tuple(b.get("cursor") or (inc[0]["index"], 0))
            fwd = [(c, k) for c, k in todo if (c["index"], k) >= cur]
            c, k = (fwd or todo)[0]
            mark_part(c, k)
            b["cursor"] = (c["index"], k + 1)
            refresh_chapter(c)
            S["budget"] -= 1


# ------------------------------------------------------------------ PDF upload

def parse_pdf(data: bytes, filename: str) -> dict:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ApiErr(400, "That file isn’t a readable PDF.")
    if doc.page_count == 0:
        raise ApiErr(400, "This PDF is empty.")
    pages = [p.get_text() for p in doc]
    full = "\n".join(pages)
    if len(full.split()) < 20:
        raise ApiErr(400, "This PDF has no selectable text — it looks like a scanned book. Run it through OCR (text recognition) first, or try another PDF.")
    toc = [t for t in doc.get_toc() if t[0] == 1]
    parts: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []
    if toc:
        method = "outline"
        for i, (_lvl, title, page) in enumerate(toc):
            start = max(0, page - 1)
            end = (toc[i + 1][2] - 1) if i + 1 < len(toc) else len(pages)
            parts.append((title.strip(), "\n".join(pages[start:max(end, start + 1)])))
            spans.append((start + 1, max(end, start + 1)))
        note = f"Found {len(parts)} chapters in the PDF's bookmarks."
    else:
        lines = full.splitlines()
        idx = [i for i, ln in enumerate(lines) if re.match(r"^\s*(chapter|book|part)\s+[\w.]+", ln, re.I)]
        if len(idx) >= 2:
            method = "headings"
            for j, i in enumerate(idx):
                end = idx[j + 1] if j + 1 < len(idx) else len(lines)
                parts.append((lines[i].strip(), "\n".join(lines[i + 1:end])))
            note = f"Found {len(parts)} chapters from headings like “{parts[0][0]}”."
        else:
            method = "sections"
            step = 10
            for s in range(0, len(pages), step):
                parts.append((f"Part {s // step + 1}", "\n".join(pages[s:s + step])))
            note = "No chapters found, so the book was split into sections of about 10 pages."
    title = (doc.metadata or {}).get("title") or Path(filename or "Untitled").stem.replace("_", " ").title()
    author = (doc.metadata or {}).get("author") or ""
    cjk = len(re.findall(r"[一-鿿]", full))
    fr = len(re.findall(r"\b(le|la|les|et|des|une|est|dans)\b", full, re.I))
    language, warning = "en", None
    if cjk > len(full) * 0.2:
        language = "zh"
    elif fr > len(full.split()) * 0.06:
        language, warning = "fr", "This book looks like it’s in French. AuK only speaks English and Chinese, so the narration will sound wrong."
    chapters = []
    for i, (t, text) in enumerate(parts):
        words = len(text.split())
        est = max(20, round(words / 150 * 60))
        skip = bool(re.match(r"^(contents|table of contents|index|copyright|acknowledg)", t, re.I))
        chapters.append(mk_chapter(i, t or f"Chapter {i + 1}", est, include=not skip, preview=" ".join(text.split()[:45]) or "(empty)", words=words, pages=list(spans[i]) if i < len(spans) else None))
    for c in chapters:
        for key in ("_want", "_done", "_nparts"):
            c.pop(key, None)
    return {"title": title, "author": author, "language": language, "language_warning": warning, "pages": doc.page_count, "detection": {"method": method, "note": note}, "chapters": chapters}


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockAuK/1.0"

    def log_message(self, fmt, *args):  # quiet
        if ARGS.verbose:
            super().log_message(fmt, *args)

    # -- helpers
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Access-Token, Content-Type, Range")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_bytes(self, data: bytes, ctype: str, *, ranged=False, extra=None, cache="no-cache"):
        start, end, status = 0, len(data) - 1, 200
        rng = self.headers.get("Range")
        if ranged and rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1) == "":
                    start = max(0, len(data) - int(m.group(2)))
                else:
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), len(data) - 1)
                if start >= len(data):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.send_header("Content-Length", "0")
                    self._cors()
                    self.end_headers()
                    return
                status = 206
        chunk = data[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Cache-Control", cache)
        if ranged:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json_body(self) -> dict:
        raw = self.read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            raise ApiErr(422, "Body must be JSON.")

    def authorized(self, qs) -> bool:
        if not ARGS.token:
            return True
        return self.headers.get("X-Access-Token") == ARGS.token or qs.get("token", [None])[0] == ARGS.token

    # -- verbs
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")

    def do_PUT(self):
        self.route("PUT")

    def route(self, method):
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)
        try:
            if path.startswith("/__mock/"):
                return self.mock_admin(path, qs)
            if path.startswith("/api/"):
                if path != "/api/status" and not self.authorized(qs):
                    # drain body so keep-alive stays sane
                    if method in ("POST", "PATCH"):
                        self.read_body()
                    raise ApiErr(401, "Missing or wrong access token.")
                tick()
                return self.api(method, path, qs)
            if method != "GET":
                raise ApiErr(405, "Method not allowed")
            return self.static(path)
        except ApiErr as e:
            self.send_json({"detail": e.detail}, e.status)

    def static(self, path):
        rel = path.lstrip("/") or "index.html"
        f = (APP_DIR / rel).resolve()
        if not str(f).startswith(str(APP_DIR)) or not f.is_file():
            if "." not in rel.split("/")[-1]:
                f = APP_DIR / "index.html"
            else:
                raise ApiErr(404, "Not found")
        self.send_bytes(f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))

    def mock_admin(self, path, qs):
        with LOCK:
            if path == "/__mock/reset":
                fixtures()
            elif path == "/__mock/engine":
                if "state" in qs:
                    S["engine"]["state"] = qs["state"][0]
                if "demo" in qs:
                    S["engine"]["is_demo"] = qs["demo"][0] == "1"
                if "rf" in qs:
                    S["engine"]["realtime_factor"] = float(qs["rf"][0]) if qs["rf"][0] != "null" else None
                if "detail" in qs:
                    S["engine"]["detail"] = qs["detail"][0]
            elif path == "/__mock/finish":
                b = S["books"].get(qs.get("id", [""])[0])
                if b:
                    start_planned(b)
                    for c in b["chapters"]:
                        if c["include"] and c["status"] != "ready":
                            for k in range(len(c["parts"])):
                                if not c["parts"][k]["ready"]:
                                    mark_part(c, k)
                            refresh_chapter(c)
                    b["status"] = "ready"
                    b["made_with"] = "demo" if ARGS.demo else "auk"
            elif path == "/__mock/rate":
                ARGS.rate = float(qs.get("v", ["0.5"])[0])
            elif path == "/__mock/part":
                # mark parts ready: ?id=<book>&ch=<index>&k=<part>|all  (k may repeat)
                b = S["books"].get(qs.get("id", [""])[0])
                if b:
                    start_planned(b)
                c = next((c for c in b["chapters"] if c["index"] == int(qs.get("ch", ["-1"])[0])), None) if b else None
                if c and c["include"]:
                    ks = qs.get("k", ["all"])
                    for k in (range(len(c["parts"])) if "all" in ks else [int(x) for x in ks]):
                        if 0 <= k < len(c["parts"]) and not c["parts"][k]["ready"]:
                            mark_part(c, k)
                    refresh_chapter(c)
                    if b["status"] in ("queued", "rendering", "paused") and all(cc["status"] == "ready" for cc in b["chapters"] if cc["include"]):
                        b["status"] = "ready"
                        b["made_with"] = "demo" if ARGS.demo else "auk"
            elif path == "/__mock/inspect":
                b = S["books"].get(qs.get("id", [""])[0])
                if b:
                    return self.send_json({"status": b["status"], "cursor": b.get("cursor"), "pages": b["pages"],
                                           "chapters": [{"index": c["index"], "status": c["status"], "pages": c.get("pages"),
                                                         "parts": [{"ready": pt["ready"], "dur": pt["dur"], "pages": pt["pages"], "marks": pt["marks"]} for pt in c["parts"]]}
                                                        for c in b["chapters"]]})
        self.send_json({"ok": True, "engine": S["engine"], "rate": ARGS.rate})

    def get_book(self, bid):
        b = S["books"].get(bid)
        if not b:
            raise ApiErr(404, "Book not found.")
        return b

    def api(self, method, path, qs):
        m = lambda pat: re.fullmatch(pat, path)  # noqa: E731
        with LOCK:
            if method == "GET" and path == "/api/status":
                queue = [b["id"] for b in sorted(S["books"].values(), key=lambda b: b["queued_at"]) if b["status"] in ("queued", "rendering")]
                return self.send_json({"app": "AuK Audiobooks", "version": "1.0.0-mock", "auth_required": bool(ARGS.token), "authorized": self.authorized(qs), "engine": S["engine"], "queue": queue})

            if path == "/api/stats" and method == "GET":
                return self.send_json({"devices": S["stats"]})
            if mm := m(r"/api/stats/([^/]+)"):
                if method != "PUT":
                    raise ApiErr(405, "Use PUT.")
                if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", mm.group(1)):
                    raise ApiErr(400, "Bad device id.")
                body = self.json_body()
                if not isinstance(body, dict) or not isinstance(body.get("days", {}), dict):
                    raise ApiErr(422, "A stats document is an object with days and books.")
                S["stats"][mm.group(1)] = body
                return self.send_json({"ok": True})

            if path == "/api/voices":
                if method == "GET":
                    return self.send_json({"voices": [voice_public(v) for v in S["voices"]]})
                if method == "POST":
                    body = self.json_body()
                    name = (body.get("name") or "").strip()
                    if not name or len((body.get("description") or "").strip()) < 3:
                        raise ApiErr(400, "A custom voice needs a name and a description.")
                    vid = "custom_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + f"_{int(time.time()) % 10000}"
                    v = dict(id=vid, name=name, language=body.get("language", "en"), gender=body.get("gender", "female"), tagline=body.get("description")[:60], description=body.get("description"), custom=True, take=0, has=False)
                    S["voices"].insert(0, v)
                    return self.send_json(voice_public(v))
            if mm := m(r"/api/voices/([^/]+)/preview"):
                v = next((x for x in S["voices"] if x["id"] == mm.group(1)), None)
                if not v:
                    raise ApiErr(404, "Voice not found.")
                body = self.json_body()
                if method == "POST":
                    need = body.get("retake") or not v["has"]
                if method == "POST" and need:
                    LOCK.release()
                    try:
                        time.sleep(ARGS.preview_delay)
                    finally:
                        LOCK.acquire()
                    v["take"] += 1
                    v["has"] = True
                return self.send_json(voice_public(v))
            if mm := m(r"/api/voices/([^/]+)/preview\.wav"):
                v = next((x for x in S["voices"] if x["id"] == mm.group(1)), None)
                if not v or not v["has"]:
                    raise ApiErr(404, "No sample yet.")
                return self.send_bytes(voice_audio(v), "audio/wav", ranged=True)
            if mm := m(r"/api/voices/([^/]+)"):
                if method == "DELETE":
                    v = next((x for x in S["voices"] if x["id"] == mm.group(1)), None)
                    if not v:
                        raise ApiErr(404, "Voice not found.")
                    if not v["custom"]:
                        raise ApiErr(400, "Only custom voices can be deleted.")
                    S["voices"].remove(v)
                    return self.send_json({"ok": True})

            if path == "/api/books":
                if method == "GET":
                    books = sorted(S["books"].values(), key=lambda b: -b["created_at"])
                    return self.send_json({"books": [book_public(b) for b in books]})
                if method == "POST":
                    ctype = self.headers.get("Content-Type", "")
                    raw = self.read_body()
                    if "multipart/form-data" not in ctype:
                        raise ApiErr(400, "Upload the PDF as multipart form field 'file'.")
                    msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
                    fname, data = None, None
                    for part in msg.iter_parts():
                        if part.get_param("name", header="content-disposition") == "file":
                            fname, data = part.get_filename(), part.get_payload(decode=True)
                    if not data:
                        raise ApiErr(400, "The upload was empty.")
                    if not data.startswith(b"%PDF"):
                        raise ApiErr(400, "That file isn’t a PDF.")
                    LOCK.release()
                    try:
                        time.sleep(ARGS.parse_delay)
                        info = parse_pdf(data, fname)
                        cover = cover_from_pdf(data)
                    finally:
                        LOCK.acquire()
                    bid = f"bk_{int(time.time() * 1000) % 100000000:08d}"
                    b = {"id": bid, "created_at": int(time.time()), "status": "draft", "error": None, "voice_id": None, "voice_name": None, "pace": 1.0, "queued_at": time.time(), **info}
                    S["books"][bid] = b
                    S["files"][bid] = {"cover": cover, "pdf": data}
                    return self.send_json(book_public(b))

            if mm := m(r"/api/books/([^/]+)"):
                b = self.get_book(mm.group(1))
                if method == "GET":
                    return self.send_json(book_public(b))
                if method == "PATCH":
                    if b["status"] not in ("draft", "paused", "error", "ready"):
                        raise ApiErr(409, "Pause the book before changing it.")
                    body = self.json_body()
                    if "title" in body and body["title"].strip():
                        b["title"] = body["title"].strip()
                    if "author" in body:
                        b["author"] = body["author"].strip()
                    changed = False
                    for patch in body.get("chapters") or []:
                        ch = next((c for c in b["chapters"] if c["index"] == patch.get("index")), None)
                        if not ch:
                            continue
                        if "title" in patch and patch["title"].strip():
                            ch["title"] = patch["title"].strip()
                        if "include" in patch and bool(patch["include"]) != ch["include"]:
                            ch["include"] = bool(patch["include"])
                            changed = True
                    if changed and b["status"] == "ready":
                        b["status"] = "paused"
                    return self.send_json(book_public(b))
                if method == "DELETE":
                    del S["books"][b["id"]]
                    S["files"].pop(b["id"], None)
                    return self.send_json({"ok": True})
            if mm := m(r"/api/books/([^/]+)/render"):
                b = self.get_book(mm.group(1))
                body = self.json_body()
                v = next((x for x in S["voices"] if x["id"] == body.get("voice_id")), None)
                if not v:
                    raise ApiErr(400, "Unknown voice.")
                pace = float(body.get("pace") or 1.0)
                if not 0.85 <= pace <= 1.15:
                    raise ApiErr(400, "Pace must be between 0.85 and 1.15.")
                changed = b["voice_id"] and (b["voice_id"] != v["id"] or abs(pace_of(b) - pace) > 1e-6)
                b.update(voice_id=v["id"], voice_name=v["name"], pace=pace, status="queued", error=None, queued_at=time.time())
                if changed or not b.get("planned") or not any(c["parts"] for c in b["chapters"] if c["include"]):
                    for c in b["chapters"]:
                        c.update(status="pending", done_chunks=0, total_chunks=0, duration=None, page_marks=None, parts=[], ver=c["ver"] + (1 if changed else 0))
                    b["cursor"] = None
                    b["planned"] = False  # planned when the render reaches this book (see tick)
                    b.pop("pending_page", None)
                if body.get("start_page"):
                    prioritize(b, int(body["start_page"]))
                return self.send_json(book_public(b))
            if mm := m(r"/api/books/([^/]+)/locate"):
                b = self.get_book(mm.group(1))
                if not b["voice_id"]:
                    raise ApiErr(409, "This book hasn't been started yet.")
                page = (qs.get("page", [""])[0] or "").strip()  # a number, or a label such as "xii"
                if not page:
                    raise ApiErr(422, "page is required")
                where = locate(b, page)
                if where is None:
                    raise ApiErr(409, "Choose a voice and start the audiobook first.")
                return self.send_json(where)
            if mm := m(r"/api/books/([^/]+)/prioritize"):
                b = self.get_book(mm.group(1))
                body = self.json_body()
                if b["status"] not in ("queued", "rendering", "ready"):
                    raise ApiErr(409, "Start the audiobook first.")
                return self.send_json(prioritize(b, body.get("page") or 1))
            if mm := m(r"/api/books/([^/]+)/pause"):
                b = self.get_book(mm.group(1))
                self.read_body()
                if b["status"] in ("queued", "rendering"):
                    b["status"] = "paused"
                return self.send_json(book_public(b))
            if mm := m(r"/api/books/([^/]+)/cover\.jpg"):
                self.get_book(mm.group(1))
                data = S["files"].get(mm.group(1), {}).get("cover")
                if not data:
                    raise ApiErr(404, "No cover.")
                return self.send_bytes(data, "image/jpeg", cache="max-age=60")
            if mm := m(r"/api/books/([^/]+)/audio/(\d+)/part/(\d+)\.m4a"):
                b = self.get_book(mm.group(1))
                c = next((x for x in b["chapters"] if x["index"] == int(mm.group(2))), None)
                k = int(mm.group(3))
                if not c or c["status"] == "ready" or k >= len(c["parts"]) or not c["parts"][k]["ready"]:
                    raise ApiErr(404, "That part isn't ready (or the chapter is finished; use the chapter file).")
                LOCK.release()
                try:
                    return self.send_bytes(wav_wrap(part_pcm(b["id"], c, k)), "audio/wav", ranged=True)
                finally:
                    LOCK.acquire()
            if mm := m(r"/api/books/([^/]+)/audio/(\d+)\.mp3"):
                b = self.get_book(mm.group(1))
                c = next((x for x in b["chapters"] if x["index"] == int(mm.group(2))), None)
                if not c or c["status"] != "ready":
                    raise ApiErr(404, "This chapter isn’t ready yet.")
                n = sorted(x["index"] for x in b["chapters"] if x["include"]).index(c["index"]) + 1
                data = chapter_audio(b["id"], c)
                return self.send_bytes(data, "audio/mpeg", extra={"Content-Disposition": f'attachment; filename="{n:02d} - {c["title"]}.mp3"'.encode("latin-1", "replace").decode("latin-1")})
            if b_ready := m(r"/api/books/([^/]+)/(stream\.m4a|book\.mp3|chapters-mp3\.zip)"):
                b = self.get_book(b_ready.group(1))
                if b["status"] != "ready":
                    raise ApiErr(409, "The book isn’t finished yet.")
                kind = b_ready.group(2)
                LOCK.release()
                try:
                    if kind == "chapters-mp3.zip":
                        return self.send_bytes(zip_of_chapters(b), "application/zip", extra={"Content-Disposition": f'attachment; filename="{b["id"]}-chapters.zip"'})
                    data = stream_audio(b)
                    if kind == "stream.m4a":
                        return self.send_bytes(data, "audio/wav", ranged=True)
                    return self.send_bytes(data, "audio/mpeg", extra={"Content-Disposition": f'attachment; filename="{b["id"]}.mp3"'})
                finally:
                    LOCK.acquire()
            if mm := m(r"/api/books/([^/]+)/audio/(\d+)\.m4a"):
                b = self.get_book(mm.group(1))
                ch = next((c for c in b["chapters"] if c["index"] == int(mm.group(2))), None)
                if not ch or ch["status"] != "ready":
                    raise ApiErr(404, "This chapter isn’t ready yet.")
            else:
                ch = None
            if ch is not None:
                pass
            elif mm := m(r"/api/books/([^/]+)/book\.m4b"):
                b = self.get_book(mm.group(1))
                if b["status"] != "ready":
                    raise ApiErr(409, "The book isn’t finished yet.")
                first = next(c for c in b["chapters"] if c["status"] == "ready")
                data = chapter_audio(b["id"], first)
                return self.send_bytes(data, "audio/mp4", ranged=True, extra={"Content-Disposition": f'attachment; filename="{b["id"]}.m4b"'})
            else:
                raise ApiErr(404, "Not found.")
        # audio outside the lock (generation can take a moment)
        data = chapter_audio(b["id"], ch)
        return self.send_bytes(data, "audio/wav", ranged=True)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default="")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--empty", action="store_true", help="start with no books")
    ap.add_argument("--rate", type=float, default=0.5, help="simulated render speed, chunks per second")
    ap.add_argument("--preview-delay", type=float, default=2.0)
    ap.add_argument("--parse-delay", type=float, default=1.2)
    ap.add_argument("--verbose", action="store_true")
    ARGS = ap.parse_args()
    fixtures()
    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    srv.daemon_threads = True
    print(f"mock AuK server on http://{ARGS.host}:{ARGS.port}  (app: {APP_DIR})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
