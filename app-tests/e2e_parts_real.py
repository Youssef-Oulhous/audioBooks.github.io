"""Round 3 against the REAL server (demo engine): listen while being made, start page, go to page.

Start a separate server first (own data dir and port, never the one on :8000), e.g.:
  cd ../server && AUDIOBOOK_DATA=/tmp/x AUDIOBOOK_DEMO_DELAY=1.2 nice -n 15 .venv/bin/python -m audiobook --engine demo --host 127.0.0.1 --port 8795
Then:  ../server/.venv/bin/python e2e_parts_real.py --base http://127.0.0.1:8795
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

import pymupdf
from playwright.sync_api import expect, sync_playwright

import e2e
from e2e import check, errors, results, shot, wait_playing, watch
from e2e_parts import EXPOSE, end_of_media, go_to_page, near, st, toast_text, wait_src

BASE = ""


def api(path: str, body: dict | None = None, method: str | None = None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def locate(bid: str, page: int) -> dict | None:
    try:
        return api(f"/api/books/{bid}/locate?page={page}")
    except urllib.error.HTTPError:
        return None  # 409 until the render has planned the parts


def make_pdf():
    """Contents + 5 chapters of 3 pages each (16 pages): pages span 2-3 parts."""
    path = e2e.FIX / "harbour-lights.pdf"
    e2e.FIX.mkdir(exist_ok=True)
    doc = pymupdf.open()
    names = ["The Pier", "Night Watch", "The Chart Room", "Fog", "Home Port"]
    toc = [[1, "Contents", 1]]
    page = doc.new_page(width=420, height=640)
    page.insert_text((40, 70), "Contents", fontsize=18, fontname="tiro")
    page.insert_textbox(pymupdf.Rect(40, 90, 380, 610), "\n".join(f"Chapter {i + 1}: {n}" for i, n in enumerate(names)), fontsize=10.5, fontname="tiro")
    page.insert_text((200, 628), "1", fontsize=9, fontname="tiro")  # the book numbers this page too
    for i, n in enumerate(names):
        toc.append([1, f"Chapter {i + 1}: {n}", doc.page_count + 1])
        for k in range(3):
            page = doc.new_page(width=420, height=640)
            y = 70
            if k == 0:
                page.insert_text((40, 70), f"Chapter {i + 1}: {n}", fontsize=18, fontname="tiro")
                y = 90
            page.insert_textbox(pymupdf.Rect(40, y, 380, 610), e2e.PROSE * 2, fontsize=10.5, fontname="tiro")
            page.insert_text((200, 628), str(doc.page_count), fontsize=9, fontname="tiro")
    doc.set_toc(toc)
    doc.set_metadata({"title": "Harbour Lights", "author": "R. Test"})
    doc.save(path)
    return path


def create_book(page, pdf, start_page: int | None) -> str:
    page.goto(f"{BASE}/#/new")
    page.wait_for_selector(".drop")
    page.set_input_files("input[type=file]", str(pdf))
    page.wait_for_url("**/chapters", timeout=60000)
    page.wait_for_selector(".sch")
    page.get_by_role("button", name="Choose a voice").click()
    page.wait_for_url("**/voice")
    page.wait_for_selector(".vc")
    page.locator(".vc", has_text="James").locator(".vc-main").click()
    page.locator(".flow-footer .btn").click()
    page.wait_for_selector(".confirm-sheet")
    if start_page:
        page.locator(".start-toggle").click()
        page.locator("#start-page").fill(str(start_page))
    page.get_by_role("button", name="Create audiobook").click()
    page.wait_for_url("**/#/book/**")
    page.evaluate(EXPOSE)
    return page.url.rsplit("/", 1)[-1]


def chapter_by_title(book: dict, prefix: str) -> dict:
    return next(c for c in book["chapters"] if c["title"].startswith(prefix))


def playing(page, chapter: int | None = None) -> bool:
    frag = f"/audio/{chapter}" if chapter is not None else "/api/books/"
    return page.evaluate("(f) => { const a = document.querySelector('audio'); return !a.paused && a.currentTime > 0.2 && a.src.includes(f); }", frag)


def watch_start(page, bid: str, pg: int, timeout: float = 120, chapter: int | None = None) -> tuple[float | None, float | None, bool]:
    """Poll the server and the page together: when did the page's audio become ready, when did playback start?"""
    t_ready = t_play = None
    sheet = False
    end = time.time() + timeout
    while time.time() < end and t_play is None:
        if t_ready is None:
            loc = locate(bid, pg)
            if loc and (loc.get("part_ready") or loc.get("chapter_ready")):
                t_ready = time.time()
        sheet = sheet or page.locator(".sheet-voicing.show").count() > 0
        if playing(page, chapter):
            t_play = time.time()
            break
        page.wait_for_timeout(200)
    return t_ready, t_play, sheet


def run(base: str) -> None:
    global BASE
    BASE = base
    pdf = make_pdf()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = e2e.new_context(browser)
        page = ctx.new_page()
        watch(page, "real-parts")

        # ---------------- book A: created with "Start at page 6" (chapter 2, its middle page) ----------------
        a = create_book(page, pdf, 6)
        t_ready, t_play, sheet = watch_start(page, a, 6)
        check("real: start page plays", t_play is not None)
        lag = (t_play - t_ready) if (t_play and t_ready) else None
        check("real: start page begins within seconds of its part being ready", lag is not None and lag < 4.5, f"{lag:.1f} s, voicing sheet shown: {sheet}" if lag is not None else "never")
        api(f"/api/books/{a}/pause", {})  # freeze the renderer so the next steps are deterministic
        bk = api(f"/api/books/{a}")
        ch2 = chapter_by_title(bk, "Chapter 2")
        s = st(page)
        check("real: start page is in chapter 2, as parts", s["index"] == ch2["index"] and s["mode"] == "part", f"index {s['index']} mode {s['mode']} part {s['part']}")
        check("real: page indicator shows the start page", "Page 6" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
        loc6 = locate(a, 6)
        check("real: started at the page's second in the part", near(s["abs"], (loc6 or {}).get("part_time") or 0, 2.0), f"{s['abs']:.2f} vs {(loc6 or {}).get('part_time')}")
        shot(page, "real-30-start-page-playing")

        # part-to-part continuation, then waiting for a part that isn't made (creation paused)
        page.wait_for_timeout(1500)
        bk = api(f"/api/books/{a}")
        ch2 = chapter_by_title(bk, "Chapter 2")
        parts = ch2["parts"]
        k = st(page)["part"]
        if ch2["status"] != "ready" and k + 1 < len(parts) and parts[k + 1]["ready"]:
            page.evaluate("window.__p.updateBook(%s)" % json.dumps(bk))
            end_of_media(page)
            wait_src(page, f"/part/{k + 1}.m4a")
            wait_playing(page)
            check("real: next part follows on its own", st(page)["part"] == k + 1)
            k += 1
        # jump to the end of the last contiguous ready part, then it must wait
        last = k
        while last + 1 < len(parts) and parts[last + 1]["ready"]:
            last += 1
        if ch2["status"] != "ready" and last + 1 < len(parts):
            if last != k:
                page.evaluate("window.__p.updateBook(%s)" % json.dumps(bk))
                page.evaluate(f"window.__p.load(window.__p.book, {ch2['index']}, {{ part: {last}, partTime: 0 }})")
                wait_src(page, f"/part/{last}.m4a")
                wait_playing(page)
            end_of_media(page)
            page.wait_for_function("window.__p.waiting === 'part'", timeout=8000)
            check("real: waits for the next part (Preparing the next part…)", page.locator(".mini-sub").inner_text() == "Preparing the next part…", page.locator(".mini-sub").inner_text())
            nxt = parts[last + 1]
            api(f"/api/books/{a}/render", {"voice_id": bk["voice_id"], "pace": bk["pace"], "start_page": nxt["first_page"]})
            t0 = time.time()
            t_rdy = None
            while time.time() - t0 < 60:
                if t_rdy is None:
                    c = chapter_by_title(api(f"/api/books/{a}"), "Chapter 2")
                    if c["status"] == "ready" or (c["parts"] and c["parts"][last + 1]["ready"]):
                        t_rdy = time.time()
                if not page.evaluate("window.__p.waiting") and playing(page):
                    break
                page.wait_for_timeout(200)
            s = st(page)
            lag = time.time() - (t_rdy or t0)
            check("real: carries on by itself once the part is made", s["waiting"] is None and (f"/part/{last + 1}.m4a" in s["src"] or f"/audio/{ch2['index']}.m4a" in s["src"]), f"{lag:.1f} s after it was ready; src {s['src'].split('/api/books/')[-1]}")
        else:
            check("real: waiting state (skipped: the chapter finished before the pause)", True)
            api(f"/api/books/{a}/render", {"voice_id": bk["voice_id"], "pace": bk["pace"]})

        # part -> chapter file at the same chapter time, once chapter 2 is finished
        page.locator(".mini-play").click()  # hold the position inside the part while the rest is made
        page.wait_for_timeout(300)
        s = st(page)
        part_before, nparts = s["part"], len(parts)
        durs: dict[int, float] = {}
        t0 = time.time()
        while time.time() - t0 < 150:
            fresh = api(f"/api/books/{a}")
            c = chapter_by_title(fresh, "Chapter 2")
            for i, pt in enumerate(c["parts"]):
                if pt["ready"] and pt["duration"]:
                    durs[i] = pt["duration"]
            if c["status"] == "ready":
                break
            page.wait_for_timeout(300)
        ch2 = c
        check("real: chapter 2 finishes", ch2["status"] == "ready")
        page.evaluate("window.__p.updateBook(%s)" % json.dumps(fresh))
        if s["mode"] == "part" and s["index"] == ch2["index"]:
            page_before = s["page"]
            if part_before + 1 < nparts:
                # natural moment 1: the end of the part (played on directly, not through the play button)
                expect_t = page.evaluate("window.__p._partStart(window.__p.book, window.__p.chapter, window.__p.part + 1)")
                page.evaluate("(() => { const a = document.querySelector('audio'); a.currentTime = a.duration - 0.5; a.play(); })()")
                how = "at the part boundary"
            else:
                # last part: natural moment 2, pressing play
                expect_t = s["time"]
                page.locator(".mini-play").click()
                how = "on play"
            # the book may have been finished too by now: then the stream file is the better source
            page.wait_for_function("(i) => { const s = document.querySelector('audio').src; return s.includes('/audio/' + i + '.m4a') || s.includes('stream.m4a'); }", arg=ch2["index"], timeout=8000)
            wait_playing(page)
            s = st(page)
            base_t = 0.0 if s["mode"] == "chapter" else (next(c for c in api(f"/api/books/{a}")["chapters"] if c["index"] == ch2["index"])["offset"] or 0)
            check(f"real: part -> {s['mode']} file {how}, same chapter time", s["mode"] in ("chapter", "stream") and expect_t - 0.3 <= s["abs"] - base_t <= expect_t + 2.5, f"{s['abs'] - base_t:.2f} vs {expect_t:.2f}")
            upto = part_before + 1 if how == "at the part boundary" else part_before
            if how == "at the part boundary" and all(i in durs for i in range(upto)):
                truth = sum(durs[i] for i in range(upto))
                check("real: ... equals the sum of the real part durations", abs(expect_t - truth) < 0.1, f"player {expect_t:.2f} vs parts {truth:.2f}")
            num = lambda v: int(v) if str(v).isdigit() else None  # noqa: E731 (pages may be printed labels)
            before_n, after_n = num(page_before), num(s["page"])
            check("real: page carries over the switch", before_n is not None and after_n in (before_n, before_n + 1), f"{page_before} -> {s['page']}")
        else:
            check("real: part -> chapter file (skipped: player not in chapter 2 parts)", True, s["mode"])

        # chapter/part -> stream once the book is ready (next play)
        t0 = time.time()
        while time.time() - t0 < 180 and api(f"/api/books/{a}")["status"] != "ready":
            page.wait_for_timeout(1000)
        bk = api(f"/api/books/{a}")
        check("real: book A finishes", bk["status"] == "ready" and bool(bk.get("stream_url")))
        page.wait_for_function("window.__p.book.status === 'ready'", timeout=15000)
        page.locator(".mini-play").click()
        page.wait_for_timeout(300)
        before = st(page)
        page.locator(".mini-play").click()
        wait_src(page, "stream.m4a", timeout=8000)
        wait_playing(page)
        s = st(page)
        off = next(c for c in bk["chapters"] if c["index"] == before["index"])["offset"]
        check("real: -> single stream file at the same place on play", s["mode"] == "stream" and near(s["abs"], off + before["time"], 1.5), f"{s['abs']:.2f} vs {off + before['time']:.2f}")

        # go to page on a finished book: stream seek
        page.goto(f"{BASE}/#/book/{a}")
        page.wait_for_selector(".goto-btn")
        loc = locate(a, 12)
        go_to_page(page, 12)
        page.wait_for_function(f"Math.abs(document.querySelector('audio').currentTime - {loc['book_time']}) < 1.5", timeout=8000)
        wait_playing(page)
        s = st(page)
        check("real: go to page (book ready) -> stream at book_time", s["mode"] == "stream" and near(s["abs"], loc["book_time"], 1.5), f"{s['abs']:.2f} vs {loc['book_time']}")
        check("real: page indicator (stream)", "Page 12" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
        go_to_page(page, 1)
        page.wait_for_timeout(1200)
        check("real: page in the skipped Contents -> page 2, with a note", "Page 1 is in the contents. Starting at page 2." in toast_text(page), toast_text(page))
        page.locator(".mini-play").click()

        # ---------------- book B: go to page while it is being made ----------------
        b = create_book(page, pdf, None)
        t0 = time.time()
        paused = False
        while time.time() - t0 < 90:
            bb = api(f"/api/books/{b}")
            c1 = chapter_by_title(bb, "Chapter 1")
            if c1["status"] != "ready" and any(pt["ready"] for pt in c1["parts"]):
                api(f"/api/books/{b}/pause", {})
                paused = True
                break
            page.wait_for_timeout(150)
        check("real: book B paused with chapter 1 partly voiced", paused)
        page.wait_for_timeout(1500)
        bb = api(f"/api/books/{b}")
        page.goto(f"{BASE}/#/library")
        page.wait_for_selector(".page-library")
        page.goto(f"{BASE}/#/book/{b}")
        page.wait_for_selector(".goto-btn")
        page.evaluate(EXPOSE)
        c1 = chapter_by_title(bb, "Chapter 1")
        if c1["status"] != "ready" and c1["parts"] and c1["parts"][0]["ready"]:
            row = page.locator(f".ch[data-index='{c1['index']}']")
            check("real: partly voiced chapter row", "parts" in row.inner_text() and not row.is_disabled(), row.inner_text().replace("\n", " | "))
            go_to_page(page, 2)
            wait_src(page, f"/audio/{c1['index']}/part/")
            wait_playing(page)
            s = st(page)
            check("real: go to page (part ready) -> that part", s["mode"] == "part" and s["index"] == c1["index"], f"part {s['part']} at {s['abs']:.2f}")
            check("real: page indicator (part)", "Page 2" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
            # resume after reload in part mode
            page.wait_for_timeout(1500)
            page.locator(".mini-play").click()
            page.wait_for_timeout(400)
            saved = page.evaluate(f"JSON.parse(localStorage.getItem('auk.pos:{b}'))")
            page.reload()
            page.wait_for_selector(".mini:not([hidden])")
            page.evaluate(EXPOSE)
            page.locator(".mini-play").click()
            wait_src(page, f"/part/{saved['part']}.m4a")
            wait_playing(page)
            s = st(page)
            check("real: resume after reload -> same part, same second", s["mode"] == "part" and near(s["abs"], saved["pt"], 1.0), f"part {saved['part']}: {s['abs']:.2f} vs {saved['pt']}")
            page.locator(".mini-play").click()
        else:
            check("real: part-ready state (skipped: chapter 1 finished before the pause)", True)

        # not voiced + creation paused -> confirm, resume with that page first, voicing sheet, starts
        go_to_page(page, 14)
        page.wait_for_selector(".sheet.show >> text=Creation is paused", timeout=8000)
        check("real: paused book asks to resume", True)
        page.get_by_role("button", name="Resume", exact=True).click()
        page.wait_for_selector(".sheet-voicing.show", timeout=8000)
        check("real: Voicing page 14…", page.locator(".sheet-voicing .sheet-title").inner_text() == "Voicing page 14…")
        shot(page, "real-31-voicing-page")
        t_ready, t_play, _ = watch_start(page, b, 14, timeout=90, chapter=chapter_by_title(api(f"/api/books/{b}"), "Chapter 5")["index"])
        lag = (t_play - t_ready) if (t_play and t_ready) else None
        check("real: go to page (not voiced) starts within seconds of the part being ready", lag is not None and lag < 4.5, f"{lag:.1f} s" if lag is not None else "never")
        bb = api(f"/api/books/{b}")
        s = st(page)
        check("real: ... in chapter 5, at page 14", s["index"] == chapter_by_title(bb, "Chapter 5")["index"] and "Page 14" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
        page.locator(".mini-main").click()
        page.wait_for_selector(".np.open")
        page.wait_for_timeout(600)
        shot(page, "real-32-np-page")
        page.locator(".np .icon-btn[aria-label='Close Now Playing']").click()

        # chapter ready while the book is not -> chapter file at chapter_time
        t0 = time.time()
        got = False
        while time.time() - t0 < 120:
            bb = api(f"/api/books/{b}")
            if chapter_by_title(bb, "Chapter 1")["status"] == "ready":
                if bb["status"] != "ready":
                    api(f"/api/books/{b}/pause", {})
                    got = True
                break
            page.wait_for_timeout(250)
        if got:
            page.wait_for_timeout(1200)
            loc = locate(b, 3)
            go_to_page(page, 3)
            c1 = chapter_by_title(api(f"/api/books/{b}"), "Chapter 1")
            wait_src(page, f"/audio/{c1['index']}.m4a")
            wait_playing(page)
            s = st(page)
            check("real: go to page (chapter ready) -> chapter file at chapter_time", s["mode"] == "chapter" and near(s["abs"], loc["chapter_time"], 1.5), f"{s['abs']:.2f} vs {loc['chapter_time']}")
            check("real: page indicator (chapter)", "Page 3" in page.locator(".mini-sub").inner_text(), page.locator(".mini-sub").inner_text())
            go_to_page(page, 1)
            page.wait_for_timeout(1500)
            check("real: Contents page -> page 2 (chapter file)", "Page 1 is in the contents. Starting at page 2." in toast_text(page) and "Page 2" in page.locator(".mini-sub").inner_text(), toast_text(page))
            # not voiced while rendering -> prioritized, voicing sheet, starts
            api(f"/api/books/{b}/render", {"voice_id": bb["voice_id"], "pace": bb["pace"]})
            c3 = chapter_by_title(api(f"/api/books/{b}"), "Chapter 3")
            target = c3["last_page"] or 10
            loc = locate(b, target)
            if loc and not loc["part_ready"] and not loc["chapter_ready"]:
                go_to_page(page, target)  # while chapter 1 is still playing
                t_ready, t_play, sheet = watch_start(page, b, target, timeout=90, chapter=loc["chapter"])
                lag = (t_play - t_ready) if (t_play and t_ready) else None
                check("real: go to page while rendering -> prioritized and starts", lag is not None and lag < 4.5 and sheet, f"{lag:.1f} s, sheet {sheet}" if lag is not None else "never")
            else:
                check("real: prioritize-while-rendering (skipped: page already voiced)", True)
        else:
            check("real: chapter-ready state (skipped: book finished first)", True)
        page.locator(".mini-play").click()
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8795")
    args = ap.parse_args()
    sys.modules.setdefault("e2e", e2e)
    t = time.time()
    run(args.base.rstrip("/"))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - t:.0f}s")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)
    print("console errors:" if errors else "no console errors")
    for e in errors:
        print("  ", e)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
