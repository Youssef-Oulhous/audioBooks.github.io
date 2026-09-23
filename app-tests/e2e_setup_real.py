"""Round 6 against the REAL server: the grouped "Chapters" step with a genuinely long book.

Start a server of your own first (never :8000), e.g.:
  cd ../server && AUDIOBOOK_DATA=/tmp/nav-check .venv/bin/python -m audiobook --engine demo --host 127.0.0.1 --port 8795
Then:  ../server/.venv/bin/python e2e_setup_real.py --base http://127.0.0.1:8795
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import e2e
from e2e import check, errors, results, shot, watch
import e2e_parts

PDF = Path(__file__).resolve().parent.parent / "server" / "tests" / "fixtures" / "crime-and-punishment.pdf"


def run(base: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = e2e.new_context(browser)
        page = ctx.new_page()
        watch(page, "real-setup")
        page.goto(f"{base}/#/new")
        page.wait_for_selector(".drop")
        page.set_input_files("input[type=file]", str(PDF))
        t0 = time.time()
        page.wait_for_url("**/chapters", timeout=300000)
        page.wait_for_selector(".totals-text", timeout=60000)
        page.wait_for_timeout(400)
        bid = page.url.split("/new/")[1].split("/")[0]
        book = e2e_parts.api(base, f"/api/books/{bid}")
        parts = book["parts"]
        check("real-setup: the server found the parts", len(parts) >= 6, f"{len(parts)} parts, {len(book['chapters'])} chapters, read in {time.time() - t0:.0f}s")
        check("real-setup: one header per part", page.locator(".sch-part").count() == len(parts), str(page.locator(".sch-part").count()))
        check("real-setup: long book starts collapsed", page.locator(".sch-part.closed").count() == len(parts) and page.locator(".sch-part-body .sch").count() == 0)
        head = page.locator(".sch-part-head").first.inner_text().replace("\n", " | ")
        first = next(c for c in book["chapters"] if c["part"] == parts[0])
        check("real-setup: the header carries pages and a count", f"pages {first['first_page']}–" in head and "chapters" in head, head)
        shot(page, "real-50-setup-collapsed")
        page.locator(".sch-part-head").first.click()
        page.wait_for_timeout(400)
        n = len([c for c in book["chapters"] if c["part"] == parts[0]])
        check("real-setup: opening a part shows its chapters", page.locator(".sch-part").nth(0).locator(".sch").count() == n, str(page.locator(".sch-part").nth(0).locator(".sch").count()))
        row = page.locator(".sch-part").nth(0).locator(".sch").first.inner_text().replace("\n", " | ")
        check("real-setup: rows show the book's own page numbers", f"pages {first['first_page']}–{first['last_page']}" in row, row)
        shot(page, "real-51-setup-grouped")
        page.locator(".sch-part-top .switch").first.click()
        page.wait_for_timeout(300)
        totals = page.locator(".totals-text").inner_text().replace("\n", " · ")
        check("real-setup: the part toggle updates the totals", totals.startswith(f"{len([c for c in book['chapters'] if c['include']]) - n} of "), totals)
        page.locator(".sch-part").nth(0).locator(".sch .switch").first.click()
        page.wait_for_timeout(300)
        check("real-setup: one chapter back on leaves the part mixed", page.evaluate("document.querySelector('.sch-part-top .switch').indeterminate"))
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
