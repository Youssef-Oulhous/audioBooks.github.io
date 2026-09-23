"""HTTP API (see docs/API.md) + serves the iPhone web app from ../app."""

from __future__ import annotations

import hmac
import json
import logging
import shutil
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .engine_manager import engine_manager
from .renderer import renderer
from .stats import stats_store
from .store import EDITABLE, BookNotFound, book_store, locate, safe_filename
from .voices import voice_store


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("audiobook")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    book_store.init()
    voice_store.init()
    renderer.start()
    if settings.preload:
        engine_manager.start_background_load()
    log.info("Data in %s · engine=%s · auth=%s", settings.data_dir, settings.engine, "on" if settings.token else "off")
    yield


app = FastAPI(title="AuK Audiobooks", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,  # lets the app be hosted elsewhere (e.g. GitHub Pages) and still talk to this server
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


# ----------------------------------------------------------------------------- auth

def _token_ok(request: Request) -> bool:
    if not settings.token:
        return True
    given = request.headers.get("x-access-token") or request.query_params.get("token") or ""
    return hmac.compare_digest(given.encode(), settings.token.encode())


def require_token(request: Request) -> None:
    if not _token_ok(request):
        raise HTTPException(401, "Access code missing or wrong. Open Settings and enter the code from the server.")


@app.exception_handler(BookNotFound)
async def _missing_book(_: Request, __: BookNotFound):
    return JSONResponse({"detail": "Book not found."}, status_code=404)


# ----------------------------------------------------------------------------- status

@app.get("/api/status")
def status(request: Request):
    authorized = _token_ok(request)
    return {
        "app": "AuK Audiobooks",
        "version": __version__,
        "auth_required": bool(settings.token),
        "authorized": authorized,
        "engine": engine_manager.status(),
        "queue": renderer.queue_ids() if authorized else [],
    }


# ----------------------------------------------------------------------------- voices

class PreviewRequest(BaseModel):
    retake: bool = False


class NewVoice(BaseModel):
    name: str = Field(default="My voice", max_length=60)
    description: str
    language: str = "en"
    gender: str = "female"


@app.get("/api/voices", dependencies=[Depends(require_token)])
def list_voices():
    return {"voices": [voice_store.to_api(v) for v in voice_store.all()]}


@app.post("/api/voices", dependencies=[Depends(require_token)])
def create_voice(body: NewVoice):
    try:
        voice = voice_store.create(body.name, body.description, body.language, body.gender)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return voice_store.to_api(voice)


@app.post("/api/voices/{voice_id}/preview", dependencies=[Depends(require_token)])
def preview_voice(voice_id: str, body: PreviewRequest | None = None):
    voice = voice_store.get(voice_id)
    if voice is None:
        raise HTTPException(404, "Voice not found.")
    try:
        voice_store.ensure_sample(voice, retake=bool(body and body.retake))
    except Exception as exc:
        if engine_manager.state == "error":
            raise HTTPException(503, f"The voice engine isn't working: {engine_manager.detail}")
        raise HTTPException(500, f"Couldn't create the voice sample: {exc}")
    return voice_store.to_api(voice)


@app.get("/api/voices/{voice_id}/preview.wav", dependencies=[Depends(require_token)])
def voice_audio(voice_id: str):
    if voice_store.get(voice_id) is None or voice_store.sample_meta(voice_id) is None:
        raise HTTPException(404, "No sample yet.")
    return FileResponse(voice_store.sample_path(voice_id), media_type="audio/wav", headers={"Cache-Control": "no-cache"})


@app.delete("/api/voices/{voice_id}", dependencies=[Depends(require_token)])
def delete_voice(voice_id: str):
    try:
        voice_store.delete(voice_id)
    except KeyError:
        raise HTTPException(400, "Only your own custom voices can be deleted.")
    return {"ok": True}


# ----------------------------------------------------------------------------- books

class ChapterEdit(BaseModel):
    index: int
    include: bool | None = None
    title: str | None = Field(default=None, max_length=200)


class BookEdit(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=300)
    chapters: list[ChapterEdit] | None = None


class RenderRequest(BaseModel):
    voice_id: str
    pace: float = 1.0
    start_page: int | str | None = None  # voice this page first, so listening can start there right away


class PageRequest(BaseModel):
    page: int | str  # the number the reader sees, or a page label such as "xii"


@app.get("/api/books", dependencies=[Depends(require_token)])
def list_books():
    return {"books": [book_store.to_api(b) for b in book_store.list()]}


@app.post("/api/books", dependencies=[Depends(require_token)])
def upload_book(file: UploadFile = File(...)):
    limit = settings.max_upload_mb * 1024 * 1024
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=settings.data_dir) as tmp:
        size = 0
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise HTTPException(413, f"That file is over {settings.max_upload_mb} MB.")
            tmp.write(chunk)
    path = Path(tmp.name)
    with path.open("rb") as fh:
        if b"%PDF" not in fh.read(1024):
            path.unlink(missing_ok=True)
            raise HTTPException(400, "That file isn't a PDF.")
    try:
        book = book_store.create(path, file.filename or "book.pdf")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        log.exception("Import failed")
        raise HTTPException(400, f"Couldn't read this PDF: {exc}")
    finally:
        path.unlink(missing_ok=True)
    return book_store.to_api(book)


@app.get("/api/books/{book_id}", dependencies=[Depends(require_token)])
def get_book(book_id: str):
    return book_store.to_api(book_store.load(book_id))


@app.patch("/api/books/{book_id}", dependencies=[Depends(require_token)])
def edit_book(book_id: str, body: BookEdit):
    book = book_store.load(book_id)
    if book["status"] not in EDITABLE:
        raise HTTPException(409, "Pause the book before editing it.")

    def apply(b: dict) -> None:
        if body.title is not None and body.title.strip():
            b["title"] = body.title.strip()
        if body.author is not None:
            b["author"] = body.author.strip()
        changed = False
        for edit in body.chapters or []:
            if not 0 <= edit.index < len(b["chapters"]):
                continue
            ch = b["chapters"][edit.index]
            if edit.include is not None and edit.include != ch["include"]:
                ch["include"] = edit.include
                ch["status"] = "pending" if edit.include else "skipped"
                changed = True
            if edit.title is not None and edit.title.strip() and edit.title.strip() != ch["title"]:
                ch["title"] = edit.title.strip()
                ch["spoken_title"] = None
                changed = True
        if changed and b["status"] == "ready":
            b["status"] = "paused"  # re-create to apply; unchanged chapters are reused

    return book_store.to_api(book_store.update(book_id, apply))


@app.post("/api/books/{book_id}/render", dependencies=[Depends(require_token)])
def render_book(book_id: str, body: RenderRequest):
    voice = voice_store.get(body.voice_id)
    if voice is None:
        raise HTTPException(400, "Pick a voice first.")
    if engine_manager.state == "error":
        raise HTTPException(503, f"The voice engine isn't working: {engine_manager.detail}")
    pace = min(1.2, max(0.8, float(body.pace)))
    book = book_store.load(book_id)
    if not any(c["include"] for c in book["chapters"]):
        raise HTTPException(400, "Select at least one chapter.")
    if book["status"] in ("queued", "rendering") and book.get("voice_id") == voice.id and book.get("pace") == pace:
        if body.start_page:
            _prioritize(book_id, body.start_page)
        return book_store.to_api(book)
    if book["status"] in ("queued", "rendering"):
        renderer.stop(book_id, wait=60)

    def apply(b: dict) -> None:
        if b.get("voice_id") != voice.id:
            for name in ("voice.wav", "voice.json"):
                (book_store.dir(book_id) / name).unlink(missing_ok=True)
        b.update(voice_id=voice.id, voice_name=voice.name, pace=pace, status="queued", error=None)

    book = book_store.update(book_id, apply)
    if body.start_page:
        _prioritize(book_id, body.start_page)
    renderer.enqueue(book_id)
    return book_store.to_api(book)


def _prioritize(book_id: str, page: int) -> dict | None:
    book = book_store.ensure_page_info(book_store.load(book_id))
    where = locate(book, page)
    if where and where["part"] is not None and not where["part_ready"]:
        renderer.prioritize(book_id, where["chapter"], where["part"])
    elif (where is None or where["part"] is None) and book["status"] in ("queued", "rendering", "draft", "paused"):
        # the parts aren't planned yet (the render was only just queued): remember it for when they are
        renderer.pending_pages[book_id] = page
    return where


@app.get("/api/books/{book_id}/locate", dependencies=[Depends(require_token)])
def locate_page(book_id: str, page: str):
    """Where a PDF page is in the audio (to jump there)."""
    where = locate(book_store.ensure_page_info(book_store.load(book_id)), page)
    if where is None:
        raise HTTPException(409, "Choose a voice and start the audiobook first.")
    return where


@app.post("/api/books/{book_id}/prioritize", dependencies=[Depends(require_token)])
def prioritize_page(book_id: str, body: PageRequest):
    """Voice this page next, so it can be played as soon as possible."""
    book = book_store.load(book_id)
    if book["status"] not in ("queued", "rendering", "ready"):
        raise HTTPException(409, "Start the audiobook first.")
    where = _prioritize(book_id, body.page)
    return where or {"page": body.page, "part": None, "part_ready": False}


@app.post("/api/books/{book_id}/pause", dependencies=[Depends(require_token)])
def pause_book(book_id: str):
    book = book_store.load(book_id)
    if book["status"] in ("queued", "rendering"):
        renderer.stop(book_id)
        book = book_store.update(book_id, lambda b: b.update(status="paused"))
    return book_store.to_api(book)


@app.delete("/api/books/{book_id}", dependencies=[Depends(require_token)])
def delete_book(book_id: str):
    book_store.load(book_id)
    renderer.stop(book_id, wait=120)
    book_store.delete(book_id)
    return {"ok": True}


# ----------------------------------------------------------------------------- listening stats

@app.get("/api/stats", dependencies=[Depends(require_token)])
def get_stats():
    return {"devices": stats_store.all()}


@app.put("/api/stats/{device_id}", dependencies=[Depends(require_token)])
async def put_stats(device_id: str, request: Request):
    body = await request.body()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "Stats document too large.")
    try:
        stats_store.put(device_id, json.loads(body or b"{}"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


# ----------------------------------------------------------------------------- media

@app.get("/api/books/{book_id}/cover.jpg", dependencies=[Depends(require_token)])
def cover(book_id: str):
    path = book_store.dir(book_id) / "cover.jpg"
    if not path.exists():
        raise HTTPException(404, "No cover.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/books/{book_id}/audio/{index}.m4a", dependencies=[Depends(require_token)])
def chapter_audio(book_id: str, index: int):
    book = book_store.load(book_id)
    if not 0 <= index < len(book["chapters"]) or book["chapters"][index]["status"] != "ready":
        raise HTTPException(404, "That chapter isn't ready yet.")
    path = book_store.dir(book_id) / "audio" / f"{index}.m4a"
    return FileResponse(path, media_type="audio/mp4", headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/books/{book_id}/audio/{index}.mp3", dependencies=[Depends(require_token)])
def chapter_mp3(book_id: str, index: int):
    book = book_store.load(book_id)
    if not 0 <= index < len(book["chapters"]) or book["chapters"][index]["status"] != "ready":
        raise HTTPException(404, "That chapter isn't ready yet.")
    path = book_store.dir(book_id) / "audio" / f"{index}.mp3"
    if not path.exists():
        raise HTTPException(404, "This chapter's MP3 is made when the book is finished.")
    return FileResponse(path, media_type="audio/mpeg", filename=_chapter_filename(book, index, "mp3"))


def _finished_file(book_id: str, name: str) -> tuple[dict, Path]:
    book = book_store.load(book_id)
    path = book_store.dir(book_id) / name
    if book["status"] != "ready" or not path.exists():
        raise HTTPException(404, "The audiobook isn't finished yet.")
    return book, path


def _chapter_filename(book: dict, index: int, ext: str) -> str:
    included = [c for c in book["chapters"] if c["include"]]
    number = next((n for n, c in enumerate(included, 1) if c["index"] == index), index + 1)
    return f"{number:02d} - {safe_filename(book['chapters'][index]['title'], 'Chapter')}.{ext}"


@app.get("/api/books/{book_id}/audio/{index}/part/{part}.m4a", dependencies=[Depends(require_token)])
def part_audio(book_id: str, index: int, part: int):
    path = book_store.dir(book_id) / "audio" / f"{index}.part{part:03d}.m4a"
    if not path.exists():
        raise HTTPException(404, "That part isn't ready (or the chapter is finished; use the chapter file).")
    return FileResponse(path, media_type="audio/mp4", headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/books/{book_id}/stream.m4a", dependencies=[Depends(require_token)])
def book_stream(book_id: str):
    """The whole book as one continuous file for the player (inline, byte ranges)."""
    _, path = _finished_file(book_id, "book.m4b")
    return FileResponse(path, media_type="audio/mp4", headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/books/{book_id}/book.m4b", dependencies=[Depends(require_token)])
def whole_book(book_id: str):
    book, path = _finished_file(book_id, "book.m4b")
    return FileResponse(path, media_type="audio/mp4", filename=f"{safe_filename(book['title'])}.m4b")


@app.get("/api/books/{book_id}/book.mp3", dependencies=[Depends(require_token)])
def whole_book_mp3(book_id: str):
    book, path = _finished_file(book_id, "book.mp3")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{safe_filename(book['title'])}.mp3")


@app.get("/api/books/{book_id}/chapters-mp3.zip", dependencies=[Depends(require_token)])
def chapters_zip(book_id: str):
    book, _ = _finished_file(book_id, "book.mp3")
    folder = book_store.dir(book_id)
    zip_path = folder / f"chapters-mp3.v{book['m4b_version']}.zip"
    if not zip_path.exists():
        tmp = zip_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:  # MP3 doesn't compress further
            for c in book["chapters"]:
                mp3 = folder / "audio" / f"{c['index']}.mp3"
                if c["include"] and c["status"] == "ready" and mp3.exists():
                    zf.write(mp3, arcname=_chapter_filename(book, c["index"], "mp3"))
        tmp.replace(zip_path)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{safe_filename(book['title'])} (MP3 chapters).zip")


# ----------------------------------------------------------------------------- the app

class _AppFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in ("", ".", "index.html", "sw.js", "manifest.webmanifest"):
            response.headers["Cache-Control"] = "no-cache"
        return response


if settings.app_dir.exists():
    app.mount("/", _AppFiles(directory=settings.app_dir, html=True), name="app")
