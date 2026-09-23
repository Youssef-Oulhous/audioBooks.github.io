"""Round 6: the "Chapters" step of the new-book flow, grouped by part.

Run through e2e.py:  ../server/.venv/bin/python e2e.py --base http://127.0.0.1:8799 --only setup
Uses two big mock drafts: Crime and Punishment (43 chapters in 7 parts, numbering restarting
in each part) and War and Peace (374 chapters in 17 books).
"""

from __future__ import annotations

import json
import time

from playwright.sync_api import expect

from e2e import check, new_context, shot, watch
from e2e_parts import api

CRIME = "bk_crime010"
WAR = "bk_warpeace11"


def heads(page):
    return page.eval_on_selector_all(".sch-part-head", "els => els.map(e => e.innerText.replace(/\\n/g, ' | '))")


def part_switch(page, n: int):
    return page.locator(".sch-part-top .switch").nth(n)  # the part's own switch, not a chapter's


def open_part(page, n: int = 0):
    head = page.locator(".sch-part-head").nth(n)
    if head.get_attribute("aria-expanded") != "true":
        head.click()
        page.wait_for_timeout(200)


def setup_flow(browser, base: str) -> None:
    api(base, "/__mock/reset")
    api(base, "/__mock/rate?v=0")
    book = api(base, f"/api/books/{CRIME}")
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "setup")
    page.goto(f"{base}/#/new/{CRIME}/chapters")
    page.wait_for_selector(".sch-part")
    page.wait_for_timeout(300)

    # ---------- grouping ----------
    check("setup: one header per part", page.locator(".sch-part").count() == len(book["parts"]) == 7, str(page.locator(".sch-part").count()))
    first = heads(page)[0]
    check("setup: header shows pages, chapters and time", first.startswith("Part I") and "pages 3–107" in first and "6 of 6 chapters" in first and "1 h 45 m" in first, first)
    outside = len([c for c in book["chapters"] if not c["part"]])
    check("setup: chapters outside a part stay in place", page.locator(".sch-list > .sch").count() == outside == 2, str(page.locator(".sch-list > .sch").count()))
    check("setup: a long book starts collapsed", page.locator(".sch-part.closed").count() == 7 and page.locator(".sch-part-body .sch").count() == 0)
    shot(page, "52-setup-collapsed")

    open_part(page, 0)
    rows = page.locator(".sch-part").nth(0).locator(".sch")
    check("setup: opening a part builds its rows", rows.count() == 6, str(rows.count()))
    check("setup: rows keep the book's numbering and pages", "Chapter I" in rows.first.inner_text() and "pages 3–16" in rows.first.inner_text(), rows.first.inner_text().replace("\n", " | "))
    check("setup: rename and preview are still there", rows.first.locator(".sch-title").count() == 1 and rows.first.locator(".sch-preview").count() == 1)
    shot(page, "53-setup-grouped")

    # ---------- the part toggle ----------
    totals = lambda: page.locator(".totals-text").inner_text().replace("\n", " · ")  # noqa: E731
    before = totals()
    part_switch(page, 0).click()
    page.wait_for_timeout(250)
    check("setup: the part toggle unticks its chapters", page.locator(".sch-part").nth(0).locator(".sch:not(.off)").count() == 0 and "0 of 6 chapters" in heads(page)[0], heads(page)[0])
    check("setup: totals follow the part toggle", totals().startswith("37 of 44 chapters") and totals() != before, f"{before} -> {totals()}")
    rows.first.locator(".switch").click()  # one chapter back on -> the part is mixed
    page.wait_for_timeout(250)
    mixed = page.evaluate("document.querySelector('.sch-part .switch').indeterminate")
    check("setup: one chapter back on leaves the part mixed", mixed and "1 of 6 chapters" in heads(page)[0], heads(page)[0])
    shot(page, "54-setup-part-mixed")

    # Select all / None still win over the part switches
    page.get_by_role("button", name="None").click()
    page.wait_for_timeout(200)
    check("setup: None unticks every part", page.locator(".sch-part.off").count() == 7 and totals().startswith("0 of 44"), totals())
    page.get_by_role("button", name="Select all").click()
    page.wait_for_timeout(200)
    check("setup: Select all ticks them back", page.locator(".sch-part.off").count() == 0 and totals().startswith("44 of 44"), totals())

    # ---------- collapse / expand, remembered ----------
    saved = page.evaluate(f"JSON.parse(localStorage.getItem('auk.parts:{CRIME}'))")
    check("setup: open parts are remembered", saved.get("Part I") is True, str(saved))
    page.reload()
    page.wait_for_selector(".sch-part")
    page.wait_for_timeout(400)
    check("setup: still open after a reload", page.locator(".sch-part").nth(0).locator(".sch").count() == 6 and page.locator(".sch-part.closed").count() == 6)

    # ---------- what the server is told ----------
    open_part(page, 1)
    page.wait_for_timeout(200)
    part_switch(page, 1).click()  # untick all of Part II
    page.wait_for_timeout(200)
    second = [c["index"] for c in book["chapters"] if c["part"] == book["parts"][1]]
    patches = []
    page.on("request", lambda r: patches.append(r.post_data) if r.method == "PATCH" else None)
    page.get_by_role("button", name="Choose a voice").click()
    page.wait_for_url("**/voice", timeout=15000)
    body = json.loads(patches[-1]) if patches else {}
    sent = sorted(c["index"] for c in body.get("chapters", []) if c["include"] is False)
    check("setup: PATCH sends exactly the chapters that were unticked", sent == sorted(second), f"{sent} vs {sorted(second)}")
    fresh = api(base, f"/api/books/{CRIME}")
    off = sorted(c["index"] for c in fresh["chapters"] if not c["include"])
    want_off = sorted(second + [c["index"] for c in book["chapters"] if not c["include"]])  # plus the Contents, off already
    check("setup: the server has those chapters off", off == want_off, f"{off} vs {want_off}")

    # ---------- 374 chapters in 17 books ----------
    t0 = time.time()
    page.goto(f"{base}/#/new/{WAR}/chapters")
    page.wait_for_selector(".sch-part")
    page.wait_for_selector(".totals-text")
    took = time.time() - t0
    war = api(base, f"/api/books/{WAR}")
    check("setup: a 374-chapter book opens as its 17 books", page.locator(".sch-part").count() == 17 and page.locator(".sch").count() == 2, f"{page.locator('.sch-part').count()} parts, {page.locator('.sch').count()} rows")
    check("setup: ... and renders in well under a second", took < 1.0, f"{took * 1000:.0f} ms for {len(war['chapters'])} chapters")
    open_part(page, 5)
    page.wait_for_timeout(250)
    check("setup: opening one of them shows only its chapters", page.locator(".sch-part").nth(5).locator(".sch").count() in (20, 22, 24), str(page.locator(".sch-part").nth(5).locator(".sch").count()))
    t0 = time.time()
    page.mouse.wheel(0, 4000)
    page.wait_for_timeout(200)
    check("setup: scrolling stays smooth", time.time() - t0 < 1.5, f"{(time.time() - t0) * 1000:.0f} ms")
    ctx.close()


def setup_shots(browser, base: str) -> None:
    for scheme in ("dark", "light"):
        api(base, "/__mock/reset")
        ctx = new_context(browser, scheme=scheme)
        page = ctx.new_page()
        watch(page, f"setup-shots-{scheme}")
        page.goto(f"{base}/#/new/{CRIME}/chapters")
        page.wait_for_selector(".sch-part")
        page.wait_for_timeout(400)
        page.locator(".sch-part").first.scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        shot(page, f"55-setup-collapsed-{scheme}")
        open_part(page, 0)
        page.locator(".sch-part").first.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        shot(page, f"56-setup-grouped-{scheme}")
        page.locator(".sch-part-top .switch").first.click()
        page.wait_for_timeout(200)
        page.locator(".sch-part").nth(0).locator(".sch .switch").first.click()
        page.wait_for_timeout(250)
        page.locator(".sch-part").first.scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        shot(page, f"57-setup-part-mixed-{scheme}")
        ctx.close()


def run_all(browser, base: str) -> None:
    try:
        setup_flow(browser, base)
        setup_shots(browser, base)
    finally:
        api(base, "/__mock/reset")
