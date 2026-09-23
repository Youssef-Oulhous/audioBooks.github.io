"""Round 4 against the REAL server (demo engine): parts, sections and the book's own page numbers.

Start a server of your own first (never the one on :8000), e.g.:
  cd ../server && AUDIOBOOK_DATA=/tmp/x AUDIOBOOK_DEMO_DELAY=0.3 nice -n 15 .venv/bin/python -m audiobook --engine demo --host 127.0.0.1 --port 8795
Then:  ../server/.venv/bin/python e2e_nav_real.py --base http://127.0.0.1:8795
"""

from __future__ import annotations

import argparse
import sys
import time

import pymupdf
from playwright.sync_api import sync_playwright

import e2e
from e2e import check, errors, results, shot, wait_playing, watch
import e2e_parts
from e2e_parts import EXPOSE, near, st, toast_text, wait_src

BASE = ""
PARTS = [
    ("Part I: Reading the Sky", ["The Horizon", "Stars and Their Names", "The Sun at Noon"]),
    ("Part II: Instruments", ["The Sextant", "Chronometers", "The Compass"]),
    ("Part III: Position", ["Latitude by Meridian", "Longitude by Time", "Running Fixes"]),
]


def api(path: str, body: dict | None = None):
    return e2e_parts.api(BASE, path, body)


def label_status(bid: str, label: str) -> int:
    """What the server answers for a page label (422 while its HTTP layer still types page as int)."""
    import urllib.error

    try:
        api(f"/api/books/{bid}/locate?page={label}")
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def locate(bid: str, page):
    import urllib.error

    try:
        return api(f"/api/books/{bid}/locate?page={page}")
    except urllib.error.HTTPError:
        return None


def make_pdf():
    """Roman front matter, then 3 parts of 3 chapters (3 pages each) with numbered sections."""
    path = e2e.FIX / "the-navigator.pdf"
    e2e.FIX.mkdir(exist_ok=True)
    doc = pymupdf.open()
    toc = []

    def add_page(heading=None, size=18, body=2):
        page = doc.new_page(width=420, height=640)
        y = 70
        if heading:
            page.insert_text((40, y), heading, fontsize=size, fontname="tiro")
            y += 24
        page.insert_textbox(pymupdf.Rect(40, y, 380, 600), e2e.PROSE * body, fontsize=10.5, fontname="tiro")
        return doc.page_count  # 1-based number of the page just added

    # front matter (labelled i… in the PDF's page labels)
    add_page("Contents", 20, 1)
    toc.append([1, "Contents", 1])
    for i in range(3):
        n = add_page("Preface" if i == 0 else None)
        if i == 0:
            toc.append([1, "Preface", n])
    while doc.page_count < 12:
        add_page(None, body=2)

    for pi, (part, chapters) in enumerate(PARTS):
        toc.append([1, part, doc.page_count + 1])
        for ci, title in enumerate(chapters):
            pages = [add_page(f"{pi + 1}.{ci + 1} {title}" if k == 0 else None) for k in range(3)]
            toc.append([2, f"{pi + 1}.{ci + 1} {title}", pages[0]])
            for k in (1, 2):
                toc.append([3, f"{pi + 1}.{ci + 1}.{k} " + ("In practice" if k == 1 else "Worked example"), pages[k]])
    doc.set_toc(toc)
    # the numbers the book prints: i, ii, iii… for the front matter, then 1, 2, 3…
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}, {"startpage": 12, "prefix": "", "style": "D", "firstpagenum": 1}])
    doc.set_metadata({"title": "The Navigator", "author": "H. R. Wexford"})
    doc.save(path)
    return path


def create_book(page, pdf) -> str:
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
    page.get_by_role("button", name="Create audiobook").click()
    page.wait_for_url("**/#/book/**")
    page.evaluate(EXPOSE)
    return page.url.rsplit("/", 1)[-1]


def run(base: str) -> None:
    global BASE
    BASE = base
    pdf = make_pdf()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = e2e.new_context(browser)
        page = ctx.new_page()
        watch(page, "real-nav")
        bid = create_book(page, pdf)

        # the server plans pages and parts once the render starts
        deadline = time.time() + 120
        book = api(f"/api/books/{bid}")
        while time.time() < deadline and not (book["parts"] and any(c["status"] == "ready" for c in book["chapters"])):
            page.wait_for_timeout(500)
            book = api(f"/api/books/{bid}")
        check("real-nav: the server found the parts", book["parts"] == [p for p, _ in PARTS], str(book["parts"]))
        check("real-nav: printed page numbers", book["page_numbering"] == "printed" and book["first_page"] == 1 and book["last_page"] == 27, f"{book['page_numbering']} {book['first_page']}-{book['last_page']}")
        secs = [c for c in book["chapters"] if c.get("sections")]
        check("real-nav: sections inside chapters", len(secs) >= 6 and all(len(c["sections"]) >= 2 for c in secs), f"{len(secs)} chapters with sections")
        pre = next((c for c in book["chapters"] if c["title"].lower().startswith("preface")), None)
        check("real-nav: front matter keeps its roman numbering", pre is not None and pre["first_page"] is None and (pre["first_page_label"] or "").startswith(("i", "v")), str(pre and (pre["first_page"], pre["first_page_label"])))

        page.reload()
        page.wait_for_selector(".part-head")
        page.wait_for_timeout(700)
        page.evaluate(EXPOSE)
        check("real-nav: one header per part", page.locator(".part-head").count() == len(PARTS), str(page.locator(".part-head").count()))
        head = page.locator(".part-head").first.inner_text().replace("\n", " | ")
        first_ch = next(c for c in book["chapters"] if c["part"] == book["parts"][0])
        check("real-nav: the header carries the part's pages", f"pages {first_ch['first_page']}–" in head, head)
        # every part is open here (3 parts), so the rows are visible
        r = page.locator(f".ch[data-index='{first_ch['index']}']")
        check("real-nav: rows show their printed page range", f"pages {first_ch['first_page']}–{first_ch['last_page']}" in r.inner_text(), r.inner_text().replace("\n", " | "))
        shot(page, "real-40-parts")

        # a section jumps to its page (wait until a chapter with sections can be played)
        target = None
        deadline = time.time() + 180
        while time.time() < deadline and target is None:
            book = api(f"/api/books/{bid}")
            target = next((c for c in book["chapters"] if c.get("sections") and (c["status"] == "ready" or any(p["ready"] for p in c["parts"]))), None)
            if target is None:
                page.wait_for_timeout(700)
        check("real-nav: a chapter with sections becomes playable", target is not None)
        sec = next(s for s in target["sections"] if s["page"])
        loc = locate(bid, sec["page"])
        page.locator(f".ch-row:has(.ch[data-index='{target['index']}']) .ch-exp").click()
        page.wait_for_timeout(300)
        shot(page, "real-41-sections")
        page.locator(f".ch-row:has(.ch[data-index='{target['index']}']) .sec", has_text=sec["title"]).click()
        page.wait_for_function("(i) => { const s = document.querySelector('audio').src; return s.includes('/audio/' + i + '.m4a') || s.includes('/audio/' + i + '/part/'); }", arg=target["index"], timeout=20000)
        wait_playing(page)
        s = st(page)
        loc = locate(bid, sec["page"])  # again: the chapter may have finished while it loaded
        want = loc["part_time"] if s["mode"] == "part" and loc["part_time"] is not None else loc["chapter_time"]
        at_page = str(s["page"]) == str(sec["page"])
        check("real-nav: a section starts at its page", s["index"] == target["index"] and at_page and (want is None or near(s["abs"], want, 2.0)), f"page {s['page']} at {s['abs']:.2f} vs {want}")
        check("real-nav: the page indicator uses the printed number", page.locator(".mini-sub").inner_text().startswith(f"Page {sec['page']}"), page.locator(".mini-sub").inner_text())
        page.locator(".mini-main").click()
        page.wait_for_selector(".np.open")
        page.wait_for_timeout(600)
        check("real-nav: Now Playing shows the part", page.locator(".np-part").text_content().strip() == target["part"], page.locator(".np-part").text_content())
        shot(page, "real-42-np-part")
        page.locator(".np .icon-btn[aria-label='Close Now Playing']").click()
        page.wait_for_timeout(300)

        # go to page, in the book's own numbers
        page.locator(".goto-btn").first.click()
        page.wait_for_selector(".goto .input-page")
        page.wait_for_timeout(350)
        hint = page.locator(".goto .sheet-msg").inner_text()
        check("real-nav: the sheet says these are the book's numbers", hint == "Page number printed in the book (1–27).", hint)
        loc = locate(bid, 5)
        page.locator(".goto .input-page").fill("5")
        page.locator(".goto button[type=submit]").click()
        page.wait_for_function("(i) => { const s = document.querySelector('audio').src; return s.includes('/audio/' + i + '.m4a') || s.includes('/audio/' + i + '/part/') || s.includes('stream.m4a'); }", arg=loc["chapter"], timeout=60000)
        wait_playing(page)
        s = st(page)
        check("real-nav: printed page 5 is PDF page 17", loc["pdf_page"] == 17 and s["index"] == loc["chapter"], f"pdf {loc['pdf_page']}, chapter {s['index']} vs {loc['chapter']}")
        check("real-nav: the page indicator agrees", page.locator(".mini-sub").inner_text().startswith("Page 5"), page.locator(".mini-sub").inner_text())

        # the contents pages aren't narrated
        page.locator(".goto-btn").first.click()
        page.wait_for_selector(".goto .input-page")
        page.wait_for_timeout(300)
        page.locator(".goto .input-page").fill("1")
        page.locator(".goto button[type=submit]").click()
        page.wait_for_timeout(2000)
        check("real-nav: page 1 of the book is its first narrated page", "Page 1" in page.locator(".mini-sub").inner_text() or "Starting at page" in toast_text(page), f"{page.locator('.mini-sub').inner_text()} / {toast_text(page)}")

        # front matter: roman numbers while it plays
        if pre and pre["status"] != "skipped":
            page.locator(f".ch[data-index='{pre['index']}']").scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            page.locator(f".ch[data-index='{pre['index']}']").click()
            wait_src(page, f"/audio/{pre['index']}", timeout=30000)
            wait_playing(page)
            page.wait_for_timeout(600)
            check("real-nav: front matter shows roman pages", page.locator(".mini-sub").inner_text().lower().startswith(("page i", "page v", "page x")), page.locator(".mini-sub").inner_text())
            # the marks arrive in the reader's numbering, so the app shows them as they come
            fresh = next(c for c in api(f"/api/books/{bid}")["chapters"] if c["index"] == pre["index"])
            marks = fresh["page_marks"] or (fresh["parts"][0]["marks"] if fresh["parts"] else [])
            later = [m for m in marks if m[1] > 1][:1]
            if later:
                page.evaluate(f"window.__p.seekTo({later[0][1] + 0.4})")
                page.wait_for_timeout(700)
                check("real-nav: marks are shown as they come", page.locator(".mini-sub").inner_text().startswith(f"Page {later[0][0]}"), f"{page.locator('.mini-sub').inner_text()} (mark {later[0]})")
            else:
                check("real-nav: marks are shown as they come (no later mark to try)", True)
            # a labelled page ("iii"): the app asks the server and, if it refuses the label, works it
            # out from the marks it already has
            label = str(later[0][0]) if later else str(pre["first_page_label"])
            want_t = later[0][1] if later else 0
            status = label_status(bid, label)
            page.locator(".mini-play").click()  # pause first, so the jump is easy to see
            page.wait_for_timeout(300)
            page.evaluate(
                """([id, label]) => import('./js/gotopage.js').then(async (m) => {
                     const b = await (await fetch('/api/books/' + id)).json();
                     return m.goToPage(b, label);
                   })""",
                [bid, label],
            )
            wait_playing(page, timeout=20000)
            page.wait_for_timeout(400)
            s2 = st(page)
            check("real-nav: a labelled page starts in the right place", near(s2["abs"], want_t, 2.0) and page.locator(".mini-sub").inner_text().startswith(f"Page {label}"), f"{s2['abs']:.2f} vs {want_t} ({page.locator('.mini-sub').inner_text()}); /locate?page={label} -> HTTP {status}")
        else:
            check("real-nav: front matter (skipped by the server)", True)
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
