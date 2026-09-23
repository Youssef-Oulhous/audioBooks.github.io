"""Draw the AuK Audiobooks glyph as SVG and rasterize it to the PWA icons.

Run with the server venv:  ../server/.venv/bin/python make_icons.py
Writes app/icons/icon.svg, icon-180.png (apple-touch, full bleed), icon-192.png,
icon-512.png (rounded, "any") and icon-maskable-512.png (full bleed, glyph in safe zone).
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ICONS = Path(__file__).resolve().parent.parent / "app" / "icons"

DEFS = """
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="0.35" y2="1">
    <stop offset="0" stop-color="#153038"/>
    <stop offset="1" stop-color="#0B1519"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.5" cy="0.36" r="0.6">
    <stop offset="0" stop-color="#46D3C3" stop-opacity="0.30"/>
    <stop offset="1" stop-color="#46D3C3" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="brass" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#8CEFE2"/>
    <stop offset="1" stop-color="#2BB3A4"/>
  </linearGradient>
  <linearGradient id="page" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#16333A"/>
    <stop offset="1" stop-color="#0E2126"/>
  </linearGradient>
  <clipPath id="round"><rect width="512" height="512" rx="114"/></clipPath>
</defs>
"""

GLYPH = """
<g fill="url(#brass)">
  <rect x="166" y="150" width="26" height="52" rx="13"/>
  <rect x="205" y="122" width="26" height="108" rx="13"/>
  <rect x="243" y="92" width="26" height="168" rx="13"/>
  <rect x="281" y="122" width="26" height="108" rx="13"/>
  <rect x="320" y="150" width="26" height="52" rx="13"/>
</g>
<g stroke="url(#brass)" stroke-width="15" stroke-linejoin="round" stroke-linecap="round">
  <path d="M256 300 C 222 278, 170 270, 110 278 L 110 408 C 170 400, 222 406, 256 430 Z" fill="url(#page)"/>
  <path d="M256 300 C 290 278, 342 270, 402 278 L 402 408 C 342 400, 290 406, 256 430 Z" fill="url(#page)"/>
</g>
<g stroke="#46D3C3" stroke-opacity="0.4" stroke-width="7" stroke-linecap="round" fill="none">
  <path d="M142 318 C 176 314, 206 318, 230 330"/>
  <path d="M142 350 C 176 346, 206 350, 230 362"/>
  <path d="M370 318 C 336 314, 306 318, 282 330"/>
  <path d="M370 350 C 336 346, 306 350, 282 362"/>
</g>
"""


def svg(kind: str) -> str:
    if kind == "rounded":
        body = f'<g clip-path="url(#round)"><rect width="512" height="512" fill="url(#bg)"/><rect width="512" height="512" fill="url(#glow)"/>{GLYPH}</g>'
    elif kind == "maskable":
        body = (
            '<rect width="512" height="512" fill="url(#bg)"/><rect width="512" height="512" fill="url(#glow)"/>'
            f'<g transform="translate(256 261) scale(0.76) translate(-256 -261)">{GLYPH}</g>'
        )
    else:  # full bleed (apple-touch-icon: iOS rounds the corners itself)
        body = f'<rect width="512" height="512" fill="url(#bg)"/><rect width="512" height="512" fill="url(#glow)"/>{GLYPH}'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">{DEFS}{body}</svg>'


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / "icon.svg").write_text(svg("rounded"))
    targets = [
        ("icon-180.png", 180, "full"),
        ("icon-192.png", 192, "rounded"),
        ("icon-512.png", 512, "rounded"),
        ("icon-maskable-512.png", 512, "maskable"),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for name, size, kind in targets:
            page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
            markup = svg(kind).replace('width="512" height="512">', f'width="{size}" height="{size}">', 1)
            page.set_content(f'<html><body style="margin:0;background:transparent">{markup}</body></html>')
            page.screenshot(path=str(ICONS / name), omit_background=True)
            page.close()
            print("wrote", name)
        browser.close()


if __name__ == "__main__":
    main()
