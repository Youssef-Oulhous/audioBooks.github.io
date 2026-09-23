"""Round 4: navigation accuracy — parts, sections, page ranges and the book's own page numbers.

Run through e2e.py:  ../server/.venv/bin/python e2e.py --base http://127.0.0.1:8799 --only nav
Uses the mock's textbook fixture (bk_navig009): roman front matter, then arabic pages,
43 chapters in 7 parts, sections inside every chapter.
"""

from __future__ import annotations

import time

from playwright.sync_api import expect

from e2e import check, new_context, shot, wait_playing, watch
from e2e_parts import EXPOSE, api, near, open_app, st, toast_text, wait_src

NAV = "bk_navig009"
FR = "bk_frank001"


def heads(page):
    return page.eval_on_selector_all(".part-head", "els => els.map(e => e.innerText.replace(/\\n/g, ' | '))")


def row(page, index: int):
    return page.locator(f".ch[data-index='{index}']")


def open_part(page, n: int = 0):
    head = page.locator(".part-head").nth(n)
    if head.get_attribute("aria-expanded") != "true":
        head.click()
        page.wait_for_timeout(250)
    return head


def nav_flow(browser, base: str) -> None:
    api(base, "/__mock/reset")
    api(base, "/__mock/rate?v=0")
    book = api(base, f"/api/books/{NAV}")
    first_ch = next(c for c in book["chapters"] if c["part"] == book["parts"][0])  # first chapter of Part I
    c1 = first_ch["index"]
    intro = next(c for c in book["chapters"] if c["title"] == "Introduction")  # roman pages, still being voiced
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "nav")
    open_app(page, base, f"#/book/{NAV}")
    page.wait_for_selector(".part-head")
    page.wait_for_timeout(600)

    # ---------- grouping ----------
    check("nav: book has parts", len(book["parts"]) == 7 and book["page_numbering"] == "printed", f"{book['parts'][:2]}… {book['first_page']}-{book['last_page']}")
    check("nav: one header per part", page.locator(".part-head").count() == 7)
    check("nav: every chapter has a row", page.locator(".ch-row").count() == len(book["chapters"]), str(page.locator(".ch-row").count()))
    first = heads(page)[0]
    check("nav: part header shows pages, chapters and time", first.startswith("Part I: Reading the Sky") and "pages 1–48" in first and "6 chapters" in first and "1 h 02 m" in first, first)
    check("nav: unfinished part shows how many chapters are ready", "0/6" in heads(page)[2], heads(page)[2])
    outside = len([c for c in book["chapters"] if not c["part"]])
    check("nav: chapters outside a part stay ungrouped", page.locator(".ch-list > .ch-row").count() == outside, str(page.locator(".ch-list > .ch-row").count()))
    check("nav: a long book opens collapsed", page.locator(".part-group.closed").count() == 7 and not row(page, c1).is_visible())
    shot(page, "41-parts-collapsed")

    # ---------- collapse / expand, remembered ----------
    open_part(page, 0)
    check("nav: tapping a part shows its chapters", row(page, c1).is_visible() and page.locator(".part-group.closed").count() == 6)
    shot(page, "42-parts-open")
    saved = page.evaluate(f"JSON.parse(localStorage.getItem('auk.parts:{NAV}'))")
    check("nav: open parts are remembered per book", saved.get("Part I: Reading the Sky") is True, str(saved))
    page.reload()
    page.wait_for_selector(".part-head")
    page.wait_for_timeout(500)
    page.evaluate(EXPOSE)
    check("nav: still open after a reload", row(page, c1).is_visible() and page.locator(".part-group.closed").count() == 6)
    # the header stays in view while its chapters scroll past
    page.locator(".part-head").first.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, 420)")
    page.wait_for_timeout(400)
    head_box = page.locator(".part-head").first.bounding_box()
    bar = page.evaluate("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')) || 56")
    check("nav: part header sticks while scrolling its rows", head_box and abs(head_box["y"] - bar) < 6, f"y={head_box and round(head_box['y'], 1)} vs {bar}")

    # ---------- page ranges on rows ----------
    check("nav: chapter row shows its page range", f"pages {first_ch['first_page']}–{first_ch['last_page']}" in row(page, c1).inner_text(), row(page, c1).inner_text().replace("\n", " | "))
    check("nav: front matter shows the printed label", "page v" in row(page, 1).inner_text(), row(page, 1).inner_text().replace("\n", " | "))
    check("nav: skipped front matter too", "page i" in row(page, 0).inner_text(), row(page, 0).inner_text().replace("\n", " | "))
    ch3 = next(c for c in book["chapters"] if c["index"] == c1 + 1)
    check("nav: ranges use the book's own numbers", f"pages {ch3['first_page']}–{ch3['last_page']}" in row(page, c1 + 1).inner_text(), row(page, c1 + 1).inner_text().replace("\n", " | "))

    # ---------- sections ----------
    exp = page.locator(f".ch-row:has(.ch[data-index='{c1}']) .ch-exp")
    check("nav: a chapter with sections can be expanded", exp.is_visible())
    exp.click()
    page.wait_for_timeout(300)
    secs = page.eval_on_selector_all(f".ch-row:has(.ch[data-index='{c1}']) .sec-list > *", "els => els.map(e => e.innerText.replace(/\\n/g, ' '))")
    check("nav: sections listed with their page", len(secs) == 3 and secs[0].startswith("1.1.1 Overview") and secs[0].endswith("2"), str(secs))
    shot(page, "43-sections-open")
    # a chapter without sections has no expander
    check("nav: no expander without sections", page.locator(".ch-row:has(.ch[data-index='0']) .ch-exp").is_hidden())
    # front matter: sections carry roman labels and jump there by label
    page.locator(".ch-row:has(.ch[data-index='1']) .ch-exp").click()
    page.wait_for_timeout(250)
    pre = page.locator(".ch-row:has(.ch[data-index='1']) .sec-list > *")
    check("nav: roman front-matter sections show their label", "vi" in pre.first.inner_text(), pre.first.inner_text().replace("\n", " "))
    check("nav: ... and are tappable", "sec-flat" not in (pre.first.get_attribute("class") or ""))
    loc_vi = api(base, f"/api/books/{NAV}/locate?page=vi")
    pre.first.click()
    wait_src(page, "/audio/1.m4a")
    wait_playing(page)
    s = st(page)
    check("nav: a roman section plays from its page", s["index"] == 1 and near(s["abs"], loc_vi["chapter_time"], 1.2), f"{s['abs']:.2f} vs {loc_vi['chapter_time']}")
    check("nav: ... and the indicator shows the roman page", page.locator(".mini-sub").inner_text().startswith("Page vi"), page.locator(".mini-sub").inner_text())
    check("nav: no toast when the page is exactly where we asked", "isn’t narrated" not in toast_text(page) and "is in the" not in toast_text(page), toast_text(page))
    page.locator(".ch-row:has(.ch[data-index='1']) .ch-exp").click()

    # a chapter that is still played as parts keeps the roman numbering
    page.locator(f".ch[data-index='{intro['index']}']").click()
    wait_src(page, f"/audio/{intro['index']}/part/0.m4a")
    wait_playing(page)
    page.wait_for_timeout(400)
    check("nav: roman pages while a chapter plays as parts", page.locator(".mini-sub").inner_text().startswith("Page ix"), page.locator(".mini-sub").inner_text())
    marks = next(p for p in api(base, f"/api/books/{NAV}")["chapters"] if p["index"] == intro["index"])["parts"][1]["marks"]
    later = [m for m in marks if isinstance(m[0], str) and m[0] != "ix"]
    page.evaluate(f"window.__p.load(window.__p.book, {intro['index']}, {{ part: 1, partTime: {later[0][1] + 0.5 if later else 1} }})")
    wait_src(page, f"/audio/{intro['index']}/part/1.m4a")
    wait_playing(page)
    page.wait_for_timeout(400)
    want = later[0][0] if later else "ix"
    check("nav: marks are used as they come, no conversion", page.locator(".mini-sub").inner_text().startswith(f"Page {want}"), f"{page.locator('.mini-sub').inner_text()} (mark {later[0] if later else None})")

    # tapping a section plays from that page (chapter is voiced: its own file)
    loc = api(base, f"/api/books/{NAV}/locate?page=4")
    page.locator(f".ch-row:has(.ch[data-index='{c1}']) .sec", has_text="1.1.2 In practice").click()
    wait_src(page, f"/audio/{c1}.m4a")
    wait_playing(page)
    s = st(page)
    check("nav: a section starts at its page", s["index"] == c1 and near(s["abs"], loc["chapter_time"], 1.2), f"{s['abs']:.2f} vs {loc['chapter_time']}")
    check("nav: page indicator follows the printed numbers", page.locator(".mini-sub").inner_text().startswith("Page 4"), page.locator(".mini-sub").inner_text())

    # ---------- part context while listening ----------
    page.locator(".mini-main").click()
    page.wait_for_selector(".np.open")
    page.wait_for_timeout(500)
    check("nav: Now Playing shows the part", page.locator(".np-part").text_content().strip() == "Part I: Reading the Sky", page.locator(".np-part").text_content())
    check("nav: ... and the mini player shows it short", page.locator(".mini-title").inner_text() == "Part I · 1.1 The Horizon", page.locator(".mini-title").inner_text())
    shot(page, "44-np-part-page")
    page.locator(".np .icon-btn[aria-label='Close Now Playing']").click()
    page.wait_for_timeout(300)

    # ---------- go to page, in the book's own numbers ----------
    page.locator(".goto-btn").first.click()
    page.wait_for_selector(".goto .input-page")
    page.wait_for_timeout(350)
    hint = page.locator(".goto .sheet-msg").inner_text()
    check("nav: the sheet says whose numbers these are", hint == f"Page number printed in the book ({book['first_page']}–{book['last_page']}).", hint)
    shot(page, "45-goto-printed")
    page.locator(".goto .input-page").fill("9999")
    page.locator(".goto button[type=submit]").click()
    check("nav: outside the book's range is refused", f"between {book['first_page']} and {book['last_page']}" in page.locator(".goto .form-error").inner_text(), page.locator(".goto .form-error").inner_text())
    loc = api(base, f"/api/books/{NAV}/locate?page=20")
    page.locator(".goto .input-page").fill("20")
    page.locator(".goto button[type=submit]").click()
    wait_src(page, f"/audio/{loc['chapter']}.m4a")
    wait_playing(page)
    s = st(page)
    check("nav: printed page 20 is not PDF page 20", loc["pdf_page"] == 32 and s["index"] == loc["chapter"] and near(s["abs"], loc["chapter_time"], 1.2), f"pdf {loc['pdf_page']}, chapter {loc['chapter']} at {s['abs']:.2f} vs {loc['chapter_time']}")
    check("nav: page indicator shows the printed page", page.locator(".mini-sub").inner_text().startswith("Page 20"), page.locator(".mini-sub").inner_text())

    # a page inside the skipped Index
    idx = next(c for c in book["chapters"] if c["title"] == "Index")
    page.locator(".goto-btn").first.click()
    page.wait_for_selector(".goto .input-page")
    page.wait_for_timeout(300)
    page.locator(".goto .input-page").fill(str(book["last_page"]))
    page.locator(".goto button[type=submit]").click()
    page.wait_for_timeout(1500)
    check("nav: a page in the index is explained", "is in the index" in toast_text(page).lower() and "Starting at page" in toast_text(page), toast_text(page))
    # that last page isn't voiced yet, so the "Voicing page…" sheet is waiting: leave it
    if page.locator(".sheet-voicing.show").count():
        page.locator(".sheet-voicing.show").get_by_role("button", name="Cancel").click()
        expect(page.locator(".sheet.show")).to_have_count(0)

    # roman front matter: the Preface reads its own printed numbers
    row(page, 1).scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    row(page, 1).click()
    wait_src(page, "/audio/1.m4a")
    wait_playing(page)
    page.wait_for_timeout(600)
    check("nav: front matter shows roman pages", page.locator(".mini-sub").inner_text().startswith("Page v"), page.locator(".mini-sub").inner_text())

    # ---------- a section that isn't voiced yet ----------
    api(base, f"/__mock/part?id={NAV}&ch=2&k=0")  # (no-op, keeps the fixture warm)
    target = next(c for c in book["chapters"] if c["part"] == book["parts"][3] and c["status"] == "pending")
    part_i = book["parts"].index(target["part"])
    page.locator(".part-head").nth(part_i).click()
    page.wait_for_timeout(300)
    page.locator(f".ch-row:has(.ch[data-index='{target['index']}']) .ch-exp").click()
    page.wait_for_timeout(300)
    sec = next(s for s in target["sections"] if s["page"])
    page.locator(f".ch-row:has(.ch[data-index='{target['index']}']) .sec", has_text=sec["title"]).click()
    page.wait_for_selector(".sheet-voicing.show", timeout=8000)
    check("nav: a section that isn't voiced asks the server for it", page.locator(".sheet-voicing .sheet-title").inner_text() == f"Voicing page {sec['page']}…", page.locator(".sheet-voicing .sheet-title").inner_text())
    loc = api(base, f"/api/books/{NAV}/locate?page={sec['page']}")
    cur = api(base, f"/__mock/inspect?id={NAV}")["cursor"]
    check("nav: ... and that page is voiced next", cur == [loc["chapter"], loc["part"]], f"{cur} vs {[loc['chapter'], loc['part']]}")
    t0 = time.time()
    api(base, f"/__mock/part?id={NAV}&ch={loc['chapter']}&k={loc['part']}")
    wait_src(page, f"/audio/{loc['chapter']}/part/{loc['part']}.m4a", timeout=8000)
    wait_playing(page)
    check("nav: ... then playback starts there", time.time() - t0 < 4.5 and page.locator(".sheet-voicing.show").count() == 0, f"{time.time() - t0:.1f} s")
    check("nav: the part of the playing chapter opened", page.locator(".part-group").nth(part_i).get_attribute("class").find("closed") < 0)

    # ---------- a book without parts is unchanged ----------
    page.goto(f"{base}/#/book/{FR}")
    page.wait_for_selector(".ch")
    page.wait_for_timeout(400)
    check("nav: a book without parts has no part headers", page.locator(".part-head").count() == 0 and page.locator(".ch-row").count() > 5)
    check("nav: ... and no page ranges it doesn't know", page.locator(".ch-exp:not([hidden])").count() == 0)
    fr = api(base, f"/api/books/{FR}")
    ch1 = next(c for c in fr["chapters"] if c["index"] == 1)
    check("nav: PDF-numbered books still show their pages", f"pages {ch1['first_page']}–{ch1['last_page']}" in row(page, 1).inner_text(), row(page, 1).inner_text().replace("\n", " | "))
    ctx.close()


def nav_shots(browser, base: str) -> None:
    """The round-4 screens, dark and light at 390."""
    for scheme in ("dark", "light"):
        api(base, "/__mock/reset")
        api(base, "/__mock/rate?v=0")
        ctx = new_context(browser, scheme=scheme)
        page = ctx.new_page()
        watch(page, f"nav-shots-{scheme}")
        book = api(base, f"/api/books/{NAV}")
        c1 = next(c["index"] for c in book["chapters"] if c["part"] == book["parts"][0])
        open_app(page, base, f"#/book/{NAV}")
        page.wait_for_selector(".part-head")
        page.wait_for_timeout(700)
        page.locator(".part-head").nth(3).scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        shot(page, f"46-parts-collapsed-{scheme}")
        open_part(page, 0)
        page.locator(".part-head").first.scroll_into_view_if_needed()
        page.wait_for_timeout(350)
        shot(page, f"47-parts-grouped-{scheme}")
        page.evaluate("window.scrollBy(0, 420)")
        page.wait_for_timeout(350)
        check(f"nav: the parked header meets the top bar ({scheme})", page.locator(".part-group.stuck").count() >= 1 and abs(page.locator(".part-head").first.bounding_box()["y"] - page.locator(".topbar").bounding_box()["height"]) < 2)
        shot(page, f"51-part-header-parked-{scheme}")
        page.locator(".part-head").first.scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        page.locator(f".ch-row:has(.ch[data-index='{c1}']) .ch-exp").click()
        page.wait_for_timeout(300)
        row(page, c1).scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        shot(page, f"48-sections-{scheme}")
        page.locator(".goto-btn").first.click()
        page.wait_for_selector(".goto .input-page")
        page.wait_for_timeout(450)
        page.locator(".goto .input-page").fill("59")
        shot(page, f"49-goto-printed-{scheme}")
        page.locator(".goto button[type=submit]").click()
        wait_playing(page, timeout=20000)
        page.locator(".mini-main").click()
        page.wait_for_selector(".np.open")
        page.wait_for_timeout(700)
        shot(page, f"50-np-part-page-{scheme}")
        ctx.close()


def run_all(browser, base: str) -> None:
    try:
        nav_flow(browser, base)
        nav_shots(browser, base)
    finally:
        api(base, "/__mock/rate?v=0.5")
        api(base, "/__mock/reset")
