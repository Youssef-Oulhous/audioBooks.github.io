"""Round 3: listen while a book is being made (~30 s parts) and "Go to page", against mock_server.py.

Run through e2e.py:  ../server/.venv/bin/python e2e.py --base http://127.0.0.1:8799 --only parts
The mock's engine is stopped (/__mock/rate?v=0) and parts are voiced on demand with
/__mock/part, so every state is deterministic. The mock is reset afterwards.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from playwright.sync_api import expect

import e2e
from e2e import audio_time, check, new_context, shot, wait_playing, watch

FR = "bk_frank001"
TM = "bk_time002"

# window.__p = the app's player (same module instance as the app's)
EXPOSE = "import('./js/player.js').then((m) => { window.__p = m.player; return true; })"
STATE = """(() => { const p = window.__p; return { mode: p.mode, index: p.index, part: p.part, time: p.time, abs: p.audio.currentTime,
  src: p.audio.src, waiting: p.waiting, paused: p.audio.paused, page: p.currentPage(), status: p.book && p.book.status }; })()"""


def api(base: str, path: str, body: dict | None = None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def open_app(page, base: str, route: str) -> None:
    page.goto(f"{base}/{route}")
    page.wait_for_function("document.querySelector('audio') !== null")
    page.evaluate(EXPOSE)


def st(page) -> dict:
    return page.evaluate(STATE)


def wait_src(page, fragment: str, timeout: int = 8000) -> None:
    page.wait_for_function("(f) => document.querySelector('audio').src.includes(f)", arg=fragment, timeout=timeout)


def end_of_media(page, before: float = 0.6) -> None:
    page.wait_for_function("() => { const a = document.querySelector('audio'); return a.readyState >= 1 && isFinite(a.duration) && a.duration > 0 }", timeout=8000)
    page.evaluate(f"(() => {{ const a = document.querySelector('audio'); a.currentTime = Math.max(0, a.duration - {before}); }})()")


def go_to_page(page, n: int, where: str = ".goto-btn") -> None:
    page.locator(where).first.click()
    page.wait_for_selector(".goto .input-page")
    page.wait_for_timeout(400)
    page.locator(".goto .input-page").fill(str(n))
    page.locator(".goto button[type=submit]").click()


def time_at(marks, page) -> float:
    best = 0.0
    for p, t in marks or []:
        if p == page:
            return t
        if p > page:
            break
        best = t
    return best


def toast_text(page) -> str:
    return " | ".join(page.locator(".toast-msg").all_inner_texts())


# ----------------------------------------------------------------------------------------------


def parts_flow(browser, base: str) -> None:
    """Chapter 2 of Frankenstein (index 6) is half voiced: 5 of 12 parts ready."""
    api(base, "/__mock/reset")
    api(base, "/__mock/rate?v=0")
    plan = api(base, f"/__mock/inspect?id={FR}")
    ch6 = next(c for c in plan["chapters"] if c["index"] == 6)
    durs = [p["dur"] for p in ch6["parts"]]
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "parts")
    open_app(page, base, f"#/book/{FR}")
    row = page.locator(".ch[data-index='6']")
    expect(row).to_contain_text("5 of 12 parts", timeout=8000)
    check("parts: row shows parts voiced", "Voicing… 5 of 12 parts" in row.inner_text(), row.inner_text().replace("\n", " | "))
    check("parts: half-voiced chapter is tappable", "partial" in (row.get_attribute("class") or "") and not row.is_disabled() and "You can listen now" in row.inner_text())
    pend = page.locator(".ch[data-index='7']")
    check("parts: chapter with no parts yet is not tappable", pend.is_disabled())
    shot(page, "30-book-parts")

    # --- play parts: part 0, then part 1 follows on its own ---
    row.click()
    wait_playing(page)
    s = st(page)
    check("parts: chapter plays as parts, from part 0", s["mode"] == "part" and s["part"] == 0 and "/audio/6/part/0.m4a" in s["src"], s["src"])
    check("parts: current page from part marks (mini player)", "Page 20" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
    end_of_media(page)
    wait_src(page, "/audio/6/part/1.m4a")
    wait_playing(page)
    s = st(page)
    check("parts: next part plays when one ends", s["part"] == 1 and near(s["time"], durs[0] + s["abs"], 0.05) and s["abs"] < 3, f"part {s['part']} abs {s['abs']:.2f} chapter time {s['time']:.2f}")

    # --- last voiced part ends -> "Preparing the next part…", then carries on by itself ---
    page.evaluate(f"window.__p.seekTo({sum(durs[:4]) + 2})")
    wait_src(page, "/audio/6/part/4.m4a")
    wait_playing(page)
    s = st(page)
    check("parts: seek within chapter jumps to the right part", s["part"] == 4 and near(s["abs"], 2, 1.2), f"part {s['part']} at {s['abs']:.2f}")
    end_of_media(page)
    page.wait_for_function("window.__p.waiting === 'part'", timeout=8000)
    check("parts: waits for the next part", page.locator(".mini-sub").inner_text() == "Preparing the next part…", page.locator(".mini-sub").inner_text())
    page.locator(".mini-main").click()
    page.wait_for_selector(".np.open")
    expect(page.locator(".np-wait")).to_be_visible()
    check("parts: Now Playing shows the wait", page.locator(".np-wait").inner_text().strip() == "Preparing the next part…", page.locator(".np-wait").inner_text())
    shot(page, "31-np-preparing-part")
    t_ready = time.time()
    api(base, f"/__mock/part?id={FR}&ch=6&k=5")
    wait_src(page, "/audio/6/part/5.m4a", timeout=8000)
    wait_playing(page)
    lag = time.time() - t_ready
    s = st(page)
    check("parts: continues on its own when the part is ready", lag < 5 and s["waiting"] is None and page.locator(".np-wait").is_hidden(), f"{lag:.1f} s")
    check("parts: chapter time = earlier parts + time in part", near(s["time"], sum(durs[:5]) + s["abs"], 0.05), f"{s['time']:.2f} vs {sum(durs[:5]) + s['abs']:.2f}")

    # --- chapter finished meanwhile -> at the end of this part, switch to the chapter file ---
    api(base, f"/__mock/part?id={FR}&ch=6&k=all")
    page.wait_for_function("window.__p.book.chapters.find(c => c.index === 6).status === 'ready'", timeout=9000)
    check("parts: no switch while the part is still playing", "/audio/6/part/5.m4a" in st(page)["src"])
    end_of_media(page, 0.5)
    wait_src(page, "/audio/6.m4a")
    wait_playing(page)
    s = st(page)
    target = sum(durs[:6])
    check("parts: part -> chapter file at the same chapter time", s["mode"] == "chapter" and target - 0.2 <= s["abs"] <= target + 2.5, f"{s['abs']:.2f} vs {target:.2f}")
    chapter = next(c for c in api(base, f"/api/books/{FR}")["chapters"] if c["index"] == 6)
    want_page = max((p for p, t in chapter["page_marks"] if t <= s["time"] + 0.05), default=chapter["first_page"])
    expect(page.locator(".np-page")).to_have_text(f"Page {want_page}", timeout=3000)
    check("parts: page indicator follows the chapter file", True, f"Page {want_page}")

    # --- book finished meanwhile -> next play/resume moves to the single stream file ---
    api(base, f"/__mock/finish?id={FR}")
    page.wait_for_function("window.__p.book.status === 'ready'", timeout=9000)
    check("parts: no switch to stream while playing", "/audio/6.m4a" in st(page)["src"])
    page.locator(".np-play").click()
    page.wait_for_timeout(300)
    before = st(page)["time"]
    page.locator(".np-play").click()
    wait_src(page, "stream.m4a")
    wait_playing(page)
    off6 = next(c for c in api(base, f"/api/books/{FR}")["chapters"] if c["index"] == 6)["offset"]
    s = st(page)
    check("parts: chapter file -> stream at the same place on resume", s["mode"] == "stream" and near(s["abs"], off6 + before, 1.5), f"{s['abs']:.2f} vs {off6 + before:.2f}")
    check("parts: still the same chapter", page.locator(".np-chapter").inner_text() == "Chapter 2")
    page.locator(".np .icon-btn[aria-label='Close Now Playing']").click()
    ctx.close()


def resume_flow(browser, base: str) -> None:
    """Pause inside a part, reload the app, press play: same part, same second."""
    api(base, "/__mock/reset")
    api(base, "/__mock/rate?v=0")
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "resume")
    open_app(page, base, f"#/book/{FR}")
    page.wait_for_selector(".ch[data-index='6']")
    page.evaluate(f"(async () => {{ const b = await (await fetch('/api/books/{FR}')).json(); window.__p.load(b, 6, {{ part: 3, partTime: 12 }}); }})()")
    wait_src(page, "/audio/6/part/3.m4a")
    wait_playing(page)
    page.wait_for_timeout(1500)
    page.locator(".mini-play").click()
    page.wait_for_timeout(400)
    saved = page.evaluate(f"JSON.parse(localStorage.getItem('auk.pos:{FR}'))")
    check("resume: exact part position saved", saved.get("part") == 3 and saved.get("pt", 0) > 12.5 and saved.get("i") == 6, str({k: saved.get(k) for k in ("i", "t", "mode", "part", "pt")}))
    page.reload()
    page.wait_for_selector(".mini:not([hidden])")
    page.evaluate(EXPOSE)
    check("resume: mini player restored", "Chapter 2" in page.locator(".mini-title").inner_text())
    page.locator(".mini-play").click()
    wait_src(page, "/audio/6/part/3.m4a")
    wait_playing(page)
    s = st(page)
    check("resume: after reload plays the same part at the same second", s["mode"] == "part" and near(s["abs"], saved["pt"], 0.8), f"{s['abs']:.2f} vs {saved['pt']}")
    ctx.close()


def goto_flow(browser, base: str) -> None:
    """Go to page in every state: not voiced, part voiced, chapter ready, book ready, skipped chapter."""
    api(base, "/__mock/reset")
    api(base, "/__mock/rate?v=0")
    fr = api(base, f"/api/books/{FR}")
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "goto")
    open_app(page, base, f"#/book/{FR}")
    page.wait_for_selector(".goto-btn")
    check("goto: button next to play", page.locator(".primary-slot .goto-btn").count() == 1)

    # validation
    go_to_page(page, fr["pages"] + 1)
    expect(page.locator(".goto .form-error")).to_be_visible()
    check("goto: rejects pages outside the PDF", f"between 1 and {fr['pages']}" in page.locator(".goto .form-error").inner_text())
    check("goto: numeric keypad", page.locator(".goto .input-page").get_attribute("inputmode") == "numeric")
    page.locator(".sheet.show .goto").get_by_role("button", name="Cancel").click()
    expect(page.locator(".sheet.show")).to_have_count(0)

    # 1) not voiced yet -> prioritized, "Voicing page N…", starts by itself
    loc = api(base, f"/api/books/{FR}/locate?page=40")
    go_to_page(page, 40)
    page.wait_for_selector(".sheet-voicing.show", timeout=8000)
    check("goto: not voiced -> Voicing page N", page.locator(".sheet-voicing .sheet-title").inner_text() == "Voicing page 40…")
    cur = api(base, f"/__mock/inspect?id={FR}")["cursor"]
    check("goto: asked the server to voice that page next", cur == [loc["chapter"], loc["part"]], f"{cur} vs {[loc['chapter'], loc['part']]}")
    page.wait_for_timeout(2500)  # at least one poll that finds nothing yet
    check("goto: nothing plays while waiting", page.evaluate("document.querySelector('audio').paused"))
    t_ready = time.time()
    api(base, f"/__mock/part?id={FR}&ch={loc['chapter']}&k={loc['part']}")
    wait_src(page, f"/audio/{loc['chapter']}/part/{loc['part']}.m4a", timeout=8000)
    wait_playing(page)
    lag = time.time() - t_ready
    expect(page.locator(".sheet-voicing.show")).to_have_count(0)
    loc2 = api(base, f"/api/books/{FR}/locate?page=40")
    s = st(page)
    check("goto: starts within seconds of the part being ready", lag < 4.5, f"{lag:.1f} s")
    check("goto: plays that part at the page", s["mode"] == "part" and near(s["abs"], loc2["part_time"], 1.2), f"{s['abs']:.2f} vs {loc2['part_time']}")
    check("goto: 'Starting at page' note", "Starting at page 40." in toast_text(page), toast_text(page))
    check("goto: page indicator", "Page 40" in page.locator(".mini-sub").inner_text() or "Page 41" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())

    # cancel while voicing: nothing starts
    page.locator(".mini-play").click()
    go_to_page(page, 50)
    page.wait_for_selector(".sheet-voicing.show", timeout=8000)
    page.locator(".sheet-voicing.show").get_by_role("button", name="Cancel").click()
    expect(page.locator(".sheet.show")).to_have_count(0)
    loc50 = api(base, f"/api/books/{FR}/locate?page=50")
    api(base, f"/__mock/part?id={FR}&ch={loc50['chapter']}&k={loc50['part']}")
    page.wait_for_timeout(3000)
    check("goto: cancel stops waiting", f"/audio/{loc50['chapter']}/part/" not in st(page)["src"] and page.evaluate("document.querySelector('audio').paused"))

    # 2) part voiced (chapter still being made) -> the part where the page begins
    #    (page 21 spans parts 1-4; the server's /locate points at part 4, the page starts in part 1)
    ch6 = next(c for c in api(base, f"/api/books/{FR}")["chapters"] if c["index"] == 6)
    k = next(i for i, pt in enumerate(ch6["parts"]) if pt["last_page"] >= 21)
    want = time_at(ch6["parts"][k]["marks"], 21)
    go_to_page(page, 21)
    wait_src(page, f"/audio/6/part/{k}.m4a")
    wait_playing(page)
    s = st(page)
    check("goto: part voiced -> plays the part where the page begins", s["mode"] == "part" and s["part"] == k and near(s["abs"], want, 1.2), f"part {k} at {s['abs']:.2f} vs {want}")
    check("goto: page 21 shown", "Page 21" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())

    # 3) chapter ready (book still rendering) -> chapter file at chapter_time
    loc = api(base, f"/api/books/{FR}/locate?page=12")
    go_to_page(page, 12)
    wait_src(page, f"/audio/{loc['chapter']}.m4a")
    wait_playing(page)
    s = st(page)
    check("goto: chapter ready -> chapter file at the page", s["mode"] == "chapter" and near(s["abs"], loc["chapter_time"], 1.2), f"{s['abs']:.2f} vs {loc['chapter_time']}")
    page.locator(".mini-main").click()
    page.wait_for_selector(".np.open")
    expect(page.locator(".np-page")).to_have_text("Page 12", timeout=3000)
    check("goto: Now Playing shows the page", True)
    # the page pill opens the sheet too
    page.locator(".np-page").click()
    page.wait_for_selector(".goto .input-page")
    check("goto: page pill in Now Playing opens Go to page", page.locator(".goto .input-page").get_attribute("placeholder") == "12")
    page.locator(".goto .input-page").fill("1")
    page.locator(".goto button[type=submit]").click()

    # 4) page inside a skipped chapter -> next narrated page, with a note
    expect(page.locator(".np-chapter")).to_have_text("Letter 1", timeout=8000)
    wait_playing(page)
    check("goto: skipped chapter resolves to the next narrated page", "Page 1 is in the contents. Starting at page 2." in toast_text(page), toast_text(page))
    expect(page.locator(".np-page")).to_have_text("Page 2", timeout=3000)
    page.locator(".np .icon-btn[aria-label='Close Now Playing']").click()

    # 5) book ready -> stream seek to book_time
    page.goto(f"{base}/#/book/{TM}")
    page.wait_for_selector(".goto-btn")
    loc = api(base, f"/api/books/{TM}/locate?page=20")
    go_to_page(page, 20)
    wait_src(page, "stream.m4a")
    wait_playing(page)
    s = st(page)
    check("goto: book ready -> stream at book_time", s["mode"] == "stream" and near(s["abs"], loc["book_time"], 1.2), f"{s['abs']:.2f} vs {loc['book_time']}")
    check("goto: stream page indicator", "Page 20" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
    ctx.close()


def startpage_flow(browser, base: str, book_pdf) -> None:
    """Create a book with "Start at page 3": it opens, voices page 3 first and starts there."""
    api(base, "/__mock/reset")
    api(base, "/__mock/rate?v=0")
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "startpage")
    open_app(page, base, "#/new")
    page.wait_for_selector(".drop")
    page.set_input_files("input[type=file]", str(book_pdf))
    page.wait_for_url("**/chapters", timeout=20000)
    page.wait_for_selector(".sch")
    page.get_by_role("button", name="Choose a voice").click()
    page.wait_for_url("**/voice")
    page.wait_for_selector(".vc")
    page.locator(".vc", has_text="James").locator(".vc-main").click()
    page.locator(".flow-footer .btn").click()
    page.wait_for_selector(".confirm-sheet")
    check("start page: hidden until asked", page.locator(".start-box").is_hidden())
    page.locator(".start-toggle").click()
    expect(page.locator(".start-box")).to_be_visible()
    page.locator("#start-page").fill("99")
    page.get_by_role("button", name="Create audiobook").click()
    check("start page: validated", page.locator(".confirm-sheet").is_visible() and "between 1 and" in page.locator(".start-box").inner_text(), page.locator(".start-box").inner_text().replace("\n", " | "))
    page.locator("#start-page").fill("3")
    shot(page, "35-confirm-start-page")
    page.get_by_role("button", name="Create audiobook").click()
    page.wait_for_url("**/#/book/**")
    book_id = page.url.rsplit("/", 1)[-1]
    page.evaluate(EXPOSE)
    page.wait_for_selector(".sheet-voicing.show", timeout=8000)
    check("start page: opens voicing that page", page.locator(".sheet-voicing .sheet-title").inner_text() == "Voicing page 3…")
    # queued behind two books: the server hasn't planned its parts yet and answers 409 to /locate
    try:
        api(base, f"/api/books/{book_id}/locate?page=3")
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
    check("start page: waits while the book is queued (parts not planned, 409)", status == 409 and page.locator(".sheet-voicing.show").count() == 1 and page.locator(".toast-error").count() == 0, str(status))
    for other in (FR, "bk_navig009", "bk_war006"):  # everything ahead of it in the queue
        api(base, f"/api/books/{other}/pause", {})
    loc = api(base, f"/api/books/{book_id}/locate?page=3")  # the render reached it: planned now
    cur = api(base, f"/__mock/inspect?id={book_id}")["cursor"]
    check("start page: voiced first once the render reaches the book", cur == [loc["chapter"], loc["part"]], f"{cur} vs {loc}")
    t_ready = time.time()
    api(base, f"/__mock/part?id={book_id}&ch={loc['chapter']}&k={loc['part']}")
    wait_src(page, f"/audio/{loc['chapter']}/part/{loc['part']}.m4a", timeout=8000)
    wait_playing(page)
    lag = time.time() - t_ready
    check("start page: starts within seconds of its part being ready", lag < 4.5, f"{lag:.1f} s")
    check("start page: in chapter 2", "Chapter 2" in page.locator(".mini-title").inner_text(), page.locator(".mini-title").inner_text())
    check("start page: remembered only once", page.evaluate(f"localStorage.getItem('auk.startpage:{book_id}')") is None)
    # reopening the book does not jump again
    page.goto(f"{base}/#/library")
    page.goto(f"{base}/#/book/{book_id}")
    page.wait_for_selector(".ch")
    page.wait_for_timeout(1500)
    check("start page: not re-applied on the next visit", page.locator(".sheet-voicing").count() == 0)
    ctx.close()


def round3_shots(browser, base: str) -> None:
    """Go to page sheet, "Voicing page N" and Now Playing with the page indicator, dark + light at 390."""
    for scheme in ("dark", "light"):
        api(base, "/__mock/reset")
        api(base, "/__mock/rate?v=0")
        ctx = new_context(browser, scheme=scheme)
        page = ctx.new_page()
        watch(page, f"shots-{scheme}")
        open_app(page, base, f"#/book/{FR}")
        page.wait_for_selector(".goto-btn")
        shot(page, f"36-book-goto-{scheme}")
        page.locator(".goto-btn").click()
        page.wait_for_selector(".goto .input-page")
        page.wait_for_timeout(450)
        page.locator(".goto .input-page").fill("40")
        shot(page, f"37-goto-sheet-{scheme}")
        page.locator(".goto button[type=submit]").click()
        page.wait_for_selector(".sheet-voicing.show", timeout=8000)
        page.wait_for_timeout(500)
        shot(page, f"38-voicing-page-{scheme}")
        page.locator(".sheet-voicing.show").get_by_role("button", name="Cancel").click()
        expect(page.locator(".sheet.show")).to_have_count(0)
        go_to_page(page, 21)
        wait_src(page, "/audio/6/part/")
        wait_playing(page)
        page.locator(".mini-main").click()
        page.wait_for_selector(".np.open")
        page.wait_for_timeout(700)
        shot(page, f"39-np-page-{scheme}")
        page.locator(".np .icon-btn[aria-label='Close Now Playing']").click()
        page.wait_for_timeout(400)
        shot(page, f"40-mini-page-{scheme}")
        ctx.close()


def run_all(browser, base: str) -> None:
    book_pdf, _ = e2e.make_pdfs()
    try:
        parts_flow(browser, base)
        resume_flow(browser, base)
        goto_flow(browser, base)
        startpage_flow(browser, base, book_pdf)
        round3_shots(browser, base)
    finally:
        api(base, "/__mock/rate?v=0.5")
        api(base, "/__mock/reset")
