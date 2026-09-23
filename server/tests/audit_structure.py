"""Audit chapter/part/section detection on full books against the book's own table of contents.

    .venv/bin/python tests/audit_structure.py [--no-outline] [book.pdf ...]

For each book it reports, per its bookmarks (the ground truth):
  * every chapter found, with its part and its page range
  * sections found inside chapters
  * text coverage: what share of the extracted words ends up in a narrated chapter
  * ordering: page ranges increasing, no overlaps, no gaps
With --no-outline the bookmarks are stripped first, so detection has to work from the printed
headings (what happens with PDFs that have no outline).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audiobook.chapters import detect  # noqa: E402
from audiobook.pdf_extract import extract  # noqa: E402

pymupdf.TOOLS.mupdf_display_errors(False)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def ground_truth(path: str) -> list[tuple[int, str, int]]:
    doc = pymupdf.open(path)
    try:
        return [(lvl, t.strip(), p) for lvl, t, p in doc.get_toc() if t.strip() and p >= 1]
    finally:
        doc.close()


def audit(path: str, drop_outline: bool) -> dict:
    source = path
    if drop_outline:
        doc = pymupdf.open(path)
        doc.set_toc([])
        source = f"/tmp/no-outline-{Path(path).name}"
        doc.save(source)
        doc.close()

    ex = extract(source, Path(path).stem)
    d = detect(ex, "en")
    included = [c for c in d.chapters if c.include]
    total_words = len(re.findall(r"[A-Za-zÀ-ÿ0-9']+", " ".join(p.text for p in ex.paras)))
    kept_words = sum(c.words for c in included)
    toc = ground_truth(path)

    # which bookmark titles came back as chapters or sections?
    found = ({norm(c.title) for c in d.chapters} | {norm(t) for c in d.chapters for t, _ in c.sections}
             | {norm(c.part) for c in d.chapters if c.part} | {norm(c.spoken_title) for c in d.chapters if c.spoken_title})
    missing = [(lvl, t, p) for lvl, t, p in toc if norm(t) and norm(t) not in found
               and not any(norm(t) in f or f in norm(t) for f in found if f)]

    starts = [c.pages[0] for c in included if c.pages]
    out_of_order = sum(1 for a, b in zip(starts, starts[1:]) if b < a)
    gaps = [(a.title, a.pages[-1], b.pages[0]) for a, b in zip(included, included[1:])
            if a.pages and b.pages and b.pages[0] - a.pages[-1] > 2]

    print(f"\n{'=' * 78}\n{Path(path).name}{'  (bookmarks removed)' if drop_outline else ''}")
    print(f"  {ex.pages} pages, printed numbers: {ex.labels[len(ex.labels)//2] if ex.labels else 'none'!r} in the middle")
    print(f"  detection: {d.method} | {d.note}")
    parts = [p for p in dict.fromkeys(c.part for c in included if c.part) if p]
    print(f"  parts ({len(parts)}): {parts[:9]}{' …' if len(parts) > 9 else ''}")
    print(f"  chapters narrated: {len(included)} of {len(d.chapters)} detected "
          f"| sections: {sum(len(c.sections) for c in included)}")
    print(f"  words kept: {kept_words:,} of {total_words:,} ({100 * kept_words / max(1, total_words):.1f}%)")
    print(f"  order: {'increasing' if not out_of_order else f'{out_of_order} out of order'}"
          f" | gaps > 2 pages: {len(gaps)}")
    print(f"  bookmark entries: {len(toc)} | not matched anywhere: {len(missing)}")
    for lvl, t, p in missing[:6]:
        print(f"      L{lvl} p{p} {t[:60]!r}")
    for c in included[:4] + (["…"] if len(included) > 8 else []) + included[-4:]:
        if c == "…":
            print("      …")
            continue
        pages = f"{c.pages[0]}-{c.pages[-1]}" if c.pages else "?"
        print(f"      {str(c.part or '-')[:12]:12s} {c.title[:40]:40s} pdf {pages:>9s} {c.words:>6,}w"
              f"{f' [{len(c.sections)} sections]' if c.sections else ''}")
    return {"method": d.method, "chapters": len(included), "parts": len(parts),
            "sections": sum(len(c.sections) for c in included), "missing": len(missing),
            "coverage": kept_words / max(1, total_words), "out_of_order": out_of_order, "gaps": len(gaps)}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    books = args or sorted(str(p) for p in (Path(__file__).parent / "fixtures").glob("*.pdf")
                           if not p.name.startswith("synthetic"))
    for book in books:
        audit(book, drop_outline="--no-outline" in sys.argv)
