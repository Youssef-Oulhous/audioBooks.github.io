"""Vendor the Phosphor icons the app uses into app/js/icons.js (run once when the set changes).

Phosphor Icons, MIT License, https://phosphoricons.com. Paths are copied verbatim from
@phosphor-icons/core@2 (256x256 viewBox, fill="currentColor").
"""

import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "js" / "icons.js"
BASE = "https://cdn.jsdelivr.net/npm/@phosphor-icons/core@2/assets/{weight}/{file}.svg"

# app name -> (phosphor file, weight)
ICONS = {
    "books": ("books", "regular"),
    "stats": ("calendar-dots", "regular"),
    "settings": ("gear-six", "regular"),
    "plus": ("plus", "regular"),
    "chevronLeft": ("caret-left", "regular"),
    "chevronRight": ("caret-right", "regular"),
    "chevronDown": ("caret-down", "regular"),
    "close": ("x", "regular"),
    "more": ("dots-three", "bold"),
    "download": ("download-simple", "regular"),
    "downloaded": ("check-circle", "regular"),
    "checkFill": ("check-circle", "fill"),
    "check": ("check", "bold"),
    "trash": ("trash", "regular"),
    "moon": ("moon", "regular"),
    "list": ("list-bullets", "regular"),
    "pencil": ("pencil-simple", "regular"),
    "refresh": ("arrows-clockwise", "regular"),
    "wave": ("waveform", "regular"),
    "book": ("book-open-text", "regular"),
    "file": ("file-pdf", "regular"),
    "info": ("info", "regular"),
    "alert": ("warning-circle", "regular"),
    "cloudOff": ("cloud-slash", "regular"),
    "sparkle": ("sparkle", "regular"),
    "share": ("export", "regular"),
    "headphones": ("headphones", "regular"),
    "speed": ("gauge", "regular"),
    "link": ("link-simple", "regular"),
    "play": ("play", "fill"),
    "pause": ("pause", "fill"),
    "stop": ("stop", "fill"),
    "prevChapter": ("skip-back", "fill"),
    "nextChapter": ("skip-forward", "fill"),
    "rewind": ("arrow-counter-clockwise", "regular"),
    "forward": ("arrow-clockwise", "regular"),
    "flame": ("flame", "fill"),
    "flameLine": ("flame", "regular"),
    "target": ("target", "regular"),
    "clock": ("clock", "regular"),
    "calendar": ("calendar-check", "regular"),
    "device": ("device-mobile", "regular"),
    "trophy": ("trophy", "regular"),
    "zip": ("file-zip", "regular"),
    "seal": ("seal-check", "regular"),
    "sealFill": ("seal-check", "fill"),
    "hourglass": ("hourglass-medium", "regular"),
    "user": ("user-circle", "regular"),
}


def fetch(file: str, weight: str) -> str:
    name = file if weight == "regular" else f"{file}-{weight}"
    with urllib.request.urlopen(BASE.format(weight=weight, file=name)) as r:
        svg = r.read().decode()
    inner = re.sub(r"^<svg[^>]*>|</svg>\s*$", "", svg.strip())
    return inner


def main() -> None:
    paths = {}
    for key, (file, weight) in ICONS.items():
        paths[key] = fetch(file, weight)
        print("ok", key, file, weight)
    body = ",\n".join(f"  {k}: {json.dumps(v)}" for k, v in paths.items())
    OUT.write_text(
        f"""// Icons: Phosphor Icons (https://phosphoricons.com), MIT License,
// Copyright (c) 2023 Phosphor Icons. Paths vendored from @phosphor-icons/core@2 by
// app-tests/vendor_icons.py; regular weight for UI, fill for play/pause/flame/badges.
//
// Permission is hereby granted, free of charge, to any person obtaining a copy of this
// software and associated documentation files, to deal in the Software without restriction.
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

const PATHS = {{
{body},
}};

// Skip back / forward: Phosphor's circular arrows with the number of seconds set inside.
const NUM = (n) =>
  `<text x="128" y="156" text-anchor="middle" font-size="76" font-weight="700" fill="currentColor" font-family="Outfit, -apple-system, system-ui, sans-serif">${{n}}</text>`;
PATHS.back15 = PATHS.rewind + NUM(15);
PATHS.fwd30 = PATHS.forward + NUM(30);

export function iconSvg(name, size = 24) {{
  return `<svg class="ic ic-${{name}}" viewBox="0 0 256 256" width="${{size}}" height="${{size}}" fill="currentColor" aria-hidden="true" focusable="false">${{PATHS[name] || ''}}</svg>`;
}}

const tpl = document.createElement('template');

export function icon(name, size = 24) {{
  tpl.innerHTML = iconSvg(name, size);
  return tpl.content.firstElementChild;
}}
"""
    )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
