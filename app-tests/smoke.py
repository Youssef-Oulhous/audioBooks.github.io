"""Quick smoke run: open a few routes, print console errors, save screenshots."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8799"
OUT = Path(__file__).parent / "screenshots" / "smoke"
OUT.mkdir(parents=True, exist_ok=True)

IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, user_agent=IPHONE_UA, color_scheme="dark")
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    for name, route in [("library", "#/library"), ("book", "#/book/bk_frank001"), ("ready", "#/book/bk_time002"), ("chapters", "#/new/bk_medit003/chapters"), ("voice", "#/new/bk_medit003/voice"), ("upload", "#/new"), ("settings", "#/settings"), ("stats", "#/stats")]:
        page.goto(f"{BASE}/{route}")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / f"{name}.png"))
        sw = page.evaluate("document.documentElement.scrollWidth")
        print(name, "scrollWidth", sw)
    print("\n".join(errors) or "no console errors")
    browser.close()
