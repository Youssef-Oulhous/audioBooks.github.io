import re
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from audiobook.audio import ffmpeg_exe
from audiobook.main import app
from tests.conftest import FIXTURES

H = {"X-Access-Token": "test-token"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_for(client, book_id, statuses, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        book = client.get(f"/api/books/{book_id}", headers=H).json()
        if book["status"] in statuses:
            return book
        time.sleep(0.2)
    raise AssertionError(f"book stuck in {book['status']}: {book.get('error')}")


def upload(client, name):
    with open(FIXTURES / name, "rb") as fh:
        r = client.post("/api/books", headers=H, files={"file": (name, fh, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_auth(client):
    assert client.get("/api/books").status_code == 401
    status = client.get("/api/status").json()
    assert status["auth_required"] and not status["authorized"]
    assert client.get("/api/status", headers=H).json()["authorized"]
    assert client.get("/api/books?token=test-token").status_code == 200


def test_rejects_non_pdf(client):
    r = client.post("/api/books", headers=H, files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400


def test_voice_preview_and_custom_voice(client):
    voices = client.get("/api/voices", headers=H).json()["voices"]
    assert {"james", "ava", "mei"} <= {v["id"] for v in voices}
    v = client.post("/api/voices/ava/preview", headers=H, json={}).json()
    assert v["take"] == 1 and v["preview_url"]
    wav = client.get(v["preview_url"], headers=H)
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert client.post("/api/voices/ava/preview", headers=H, json={"retake": True}).json()["take"] == 2
    custom = client.post("/api/voices", headers=H, json={"name": "Grandpa", "description": "An old man with a warm raspy voice, slow.", "gender": "male"}).json()
    assert custom["custom"] and custom["preview_url"] is None
    assert client.delete(f"/api/voices/{custom['id']}", headers=H).json() == {"ok": True}
    assert client.delete("/api/voices/james", headers=H).status_code == 400


def test_full_book_flow(client):
    book = upload(client, "synthetic_parts.pdf")
    assert book["status"] == "draft" and book["detection"]["method"] == "headings"
    included = [c for c in book["chapters"] if c["include"]]
    assert len(included) == 4

    # skip chapter 3 to check reuse later
    third = included[2]["index"]
    book = client.patch(f"/api/books/{book['id']}", headers=H, json={"chapters": [{"index": third, "include": False}]}).json()
    assert client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "james", "pace": 1.0}).json()["status"] == "queued"
    book = wait_for(client, book["id"], {"ready", "error"})
    assert book["status"] == "ready", book["error"]
    ready = [c for c in book["chapters"] if c["status"] == "ready"]
    assert len(ready) == 3 and all(c["duration"] > 10 for c in ready)
    assert book["m4b_url"]

    # chapter audio supports byte ranges (iOS Safari needs this)
    r = client.get(ready[0]["audio_url"], headers={**H, "Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    # the .m4b has one chapter marker per included chapter, with titles
    m4b = client.get(book["m4b_url"], headers=H)
    assert m4b.status_code == 200
    path = FIXTURES.parent / "_out.m4b"
    path.write_bytes(m4b.content)
    info = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    assert len(re.findall(r"Chapter #\d+:\d+", info)) == 3
    assert "Chapter 1: The Storm" in info and "attached pic" in info

    # MP3 downloads: every chapter, the whole book (with ID3 chapter markers) and a zip of chapters
    r = client.get(ready[0]["mp3_url"], headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert 'filename="01 - Chapter 1_ The Storm.mp3"' in r.headers["content-disposition"] or "01%20-%20Chapter" in r.headers["content-disposition"]
    mp3 = client.get(book["mp3_url"], headers=H)
    assert mp3.status_code == 200 and mp3.content[:3] == b"ID3"
    path.write_bytes(mp3.content)
    info = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    assert len(re.findall(r"Chapter #\d+:\d+", info)) == 3 and "Chapter 2: Morning" in info
    zipped = client.get(book["mp3_zip_url"], headers=H)
    assert zipped.status_code == 200
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert len(names) == 3 and names[0].startswith("01 - ") and all(n.endswith(".mp3") for n in names)

    # one continuous stream for the player; each chapter's offset lands exactly on its 0.6 s lead-in silence
    stream = client.get(book["stream_url"], headers={**H, "Range": "bytes=0-99"})
    assert stream.status_code == 206 and "attachment" not in stream.headers.get("content-disposition", "")
    path.write_bytes(client.get(book["stream_url"], headers=H).content)
    offsets = [c["offset"] for c in ready]
    assert offsets[0] == 0.0
    for c, nxt in zip(ready, ready[1:]):
        assert abs(nxt["offset"] - (c["offset"] + c["duration"])) < 0.01
    import numpy as np
    for off in offsets[1:]:
        pcm = subprocess.run([ffmpeg_exe(), "-v", "error", "-ss", f"{off - 0.3:.3f}", "-t", "1.2", "-i", str(path), "-ac", "1", "-ar", "24000",
                              "-f", "s16le", "-"], capture_output=True).stdout
        x = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768
        frames = np.sqrt((x[: len(x) // 480 * 480].reshape(-1, 480) ** 2).mean(axis=1))  # 20 ms frames
        # 0.3 s before the offset = previous chapter's 1.4 s tail silence, then 0.6 s lead-in, then speech
        assert frames[: 40].max() < 0.01, "sound where the chapter boundary should be silent"
        assert frames[48:].max() > 0.02, "no speech right after the lead-in"
    path.unlink()

    # re-include chapter 3: only it gets voiced, the others are reused
    versions = {c["index"]: c["audio_url"] for c in ready}
    book = client.patch(f"/api/books/{book['id']}", headers=H, json={"chapters": [{"index": third, "include": True}]}).json()
    assert book["status"] == "paused"
    client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "james", "pace": 1.0})
    book = wait_for(client, book["id"], {"ready", "error"})
    assert book["status"] == "ready"
    for c in book["chapters"]:
        if c["index"] in versions:
            assert c["audio_url"] == versions[c["index"]]  # untouched
    assert book["chapters"][third]["status"] == "ready"

    # untick + re-tick a finished chapter: nothing is re-voiced
    first = [c for c in book["chapters"] if c["status"] == "ready"][0]
    client.patch(f"/api/books/{book['id']}", headers=H, json={"chapters": [{"index": first["index"], "include": False}]})
    client.patch(f"/api/books/{book['id']}", headers=H, json={"chapters": [{"index": first["index"], "include": True}]})
    client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "james", "pace": 1.0})
    book = wait_for(client, book["id"], {"ready", "error"})
    assert book["chapters"][first["index"]]["audio_url"] == first["audio_url"]

    # changing the voice re-voices everything
    client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "ava", "pace": 1.1})
    book = wait_for(client, book["id"], {"ready", "error"})
    assert book["voice_name"] == "Ava" and book["pace"] == 1.1
    assert all(c["audio_url"] != versions.get(c["index"]) for c in book["chapters"] if c["status"] == "ready")

    assert client.delete(f"/api/books/{book['id']}", headers=H).json() == {"ok": True}
    assert client.get(f"/api/books/{book['id']}", headers=H).status_code == 404


def test_pause_and_resume(client, monkeypatch):
    from audiobook.engine_manager import engine_manager

    monkeypatch.setattr(engine_manager.ensure_loaded(), "delay", 0.02)
    book = upload(client, "synthetic_fontsize.pdf")
    client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "leo"})
    wait_for(client, book["id"], {"rendering"})
    time.sleep(0.5)
    paused = client.post(f"/api/books/{book['id']}/pause", headers=H).json()
    assert paused["status"] == "paused"
    time.sleep(0.5)
    done_at_pause = client.get(f"/api/books/{book['id']}", headers=H).json()["progress"]["done_chunks"]
    time.sleep(0.5)
    assert client.get(f"/api/books/{book['id']}", headers=H).json()["progress"]["done_chunks"] == done_at_pause
    client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "leo"})
    book = wait_for(client, book["id"], {"ready", "error"})
    assert book["status"] == "ready" and book["progress"]["percent"] == 100.0


def test_stats_per_device(client):
    assert client.get("/api/stats").status_code == 401
    doc = {
        "days": {"2026-09-20": {"listen": 1800, "app": 2400, "books": {"bk_12345678": 1800}}, "bad-day": {"listen": 5}},
        "books": {"bk_12345678": {"title": "Frankenstein", "furthest": 5230.2, "total": 27600, "finished_at": None}},
        "goal_minutes": 45,
    }
    assert client.put("/api/stats/iphone-abc123", headers=H, json=doc).json() == {"ok": True}
    assert client.put("/api/stats/ipad-xyz7890", headers=H, json={"days": {"2026-09-20": {"listen": 999999}}}).json() == {"ok": True}
    devices = client.get("/api/stats", headers=H).json()["devices"]
    assert set(devices) == {"iphone-abc123", "ipad-xyz7890"}
    phone = devices["iphone-abc123"]
    assert list(phone["days"]) == ["2026-09-20"] and phone["days"]["2026-09-20"]["listen"] == 1800
    assert phone["books"]["bk_12345678"]["title"] == "Frankenstein" and phone["goal_minutes"] == 45
    assert devices["ipad-xyz7890"]["days"]["2026-09-20"]["listen"] == 86400  # clamped to one day
    assert client.put("/api/stats/x", headers=H, json=doc).status_code == 400  # bad device id


def test_listen_early_and_start_from_page(client, monkeypatch):
    from audiobook.engine_manager import engine_manager
    from tests.conftest import real_fixture

    real_fixture("alices-adventures-in-wonderland.pdf")
    monkeypatch.setattr(engine_manager.ensure_loaded(), "delay", 0.03)
    book = upload(client, "alices-adventures-in-wonderland.pdf")
    keep = {1, 2}  # chapters I and II
    client.patch(f"/api/books/{book['id']}", headers=H, json={"chapters": [{"index": c["index"], "include": c["index"] in keep} for c in book["chapters"]]})
    ch2 = next(c for c in book["chapters"] if c["index"] == 2)
    page = 16  # middle of chapter II (pages 12-19)

    client.post(f"/api/books/{book['id']}/render", headers=H, json={"voice_id": "james", "start_page": page})
    first_ready = None
    deadline = time.time() + 60
    while time.time() < deadline and first_ready is None:
        b = client.get(f"/api/books/{book['id']}", headers=H).json()
        ready = [(c["index"], p) for c in b["chapters"] for p in c["parts"] if p["ready"]]
        if ready:
            first_ready = ready[0]
        time.sleep(0.03)
    assert first_ready, "no part became playable"
    idx, part = first_ready
    assert idx == 2 and part["first_page"] <= page <= max(part["last_page"], page), part  # the asked page came first
    assert part["url"] and part["duration"] > 5

    where = client.get(f"/api/books/{book['id']}/locate?page={page}", headers=H).json()
    assert where["chapter"] == 2 and where["part_ready"] and where["part_url"] and where["part_time"] is not None
    r = client.get(where["part_url"], headers={**H, "Range": "bytes=0-9"})
    assert r.status_code == 206

    b = wait_for(client, book["id"], {"ready", "error"}, timeout=400)
    assert b["status"] == "ready"
    assert all(c["parts"] == [] for c in b["chapters"])  # finished books play as one stream
    c2 = next(c for c in b["chapters"] if c["index"] == 2)
    assert c2["first_page"] == 12 and c2["page_marks"]
    at_start = client.get(f"/api/books/{book['id']}/locate?page=12", headers=H).json()
    at_page = client.get(f"/api/books/{book['id']}/locate?page={page}", headers=H).json()
    assert abs(at_start["book_time"] - c2["offset"]) < 1.0
    assert c2["offset"] < at_page["book_time"] < c2["offset"] + c2["duration"]
    # pages in skipped chapters jump to the next narrated one
    skipped = client.get(f"/api/books/{book['id']}/locate?page=2", headers=H).json()
    assert skipped["chapter"] == 1
    client.delete(f"/api/books/{book['id']}", headers=H)


def test_go_to_page_on_books_voiced_before_page_tracking(tmp_path, monkeypatch):
    """Chapters finished by older versions have a page range but no page positions: they are estimated
    from how far into the chapter's text each page starts."""
    import json as _json

    from audiobook.store import book_store, locate

    monkeypatch.setattr(book_store, "dir", lambda book_id: tmp_path)
    (tmp_path / "text.json").write_text(_json.dumps({
        "chapters": [["a" * 100, "b" * 100, "c" * 200]], "pages": [[5, 6, 7]],
    }))
    book = {"id": "bk_12345678", "status": "ready", "chapters": [
        {"index": 0, "title": "One", "include": True, "status": "ready", "duration": 402.0, "offset": 0.0, "pages": [5, 7]},
    ]}
    (tmp_path / "book.json").write_text(_json.dumps(book))
    book = book_store.ensure_page_info(book_store.load("bk_12345678"))
    times = [locate(book, p)["book_time"] for p in (5, 6, 7)]
    assert times[0] < times[1] < times[2]
    assert abs(times[1] - (0.6 + 400 * 100 / 400)) < 0.5 and abs(times[2] - (0.6 + 400 * 200 / 400)) < 0.5


def test_pages_use_the_numbers_printed_in_the_book(client):
    """A textbook with roman front matter: the app asks by the book's own page numbers and labels."""
    from tests.conftest import real_fixture

    real_fixture("thinkpython2.pdf")
    book = upload(client, "thinkpython2.pdf")
    assert book["page_numbering"] == "printed" and book["first_page"] == 1
    assert any(c["sections"] for c in book["chapters"])  # sections come from the bookmarks
    chapter = next(c for c in book["chapters"] if c["title"].startswith("The way of the program"))
    assert chapter["first_page"] == 1 and chapter["first_page_label"] == "1"
    assert [s["title"] for s in chapter["sections"]][:2] == ["What is a program?", "Running Python"]

    printed = client.get(f"/api/books/{book['id']}/locate?page=5", headers=H).json()
    assert printed["pdf_page"] == 27 and printed["page_label"] == "5"  # printed 5 is the PDF's page 27
    label = client.get(f"/api/books/{book['id']}/locate?page=xii", headers=H)
    assert label.status_code == 200 and label.json()["pdf_page"] <= 27  # roman front matter is accepted
    client.delete(f"/api/books/{book['id']}", headers=H)
