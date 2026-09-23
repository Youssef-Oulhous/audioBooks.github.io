"""PDF → clean paragraphs with layout hints (page, position, font size) for chapter detection.

Removes running headers/footers and page numbers, drops superscript footnote markers,
re-joins words hyphenated across lines and paragraphs split across pages.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from .textprep import clean_text


# Excerpts and odd PDFs often have bookmarks pointing at missing pages; MuPDF would print an
# "error" line for each. They're harmless (those entries are skipped), so keep the log quiet.
pymupdf.TOOLS.mupdf_display_errors(False)


@dataclass
class Line:
    page: int
    x0: float
    y0: float
    y1: float
    text: str
    size: float
    bold: bool
    block: int
    page_height: float


@dataclass
class Para:
    page: int
    y: float
    text: str
    size: float
    bold: bool
    n_lines: int
    first_on_page: bool = False
    lines: list[str] = field(default_factory=list)
    # where a new PDF page starts inside this paragraph: (character offset in `text`, page index)
    breaks: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Extracted:
    paras: list[Para]
    body_size: float
    pages: int
    title: str
    author: str
    toc: list[tuple[int, str, int]]  # (level, title, 1-based page)
    labels: list[str] = field(default_factory=list)  # page number printed in the book, per PDF page


_CJK_CHAR = re.compile(r"[㐀-鿿豈-﫿぀-ヿ]")
_PAGE_NUMBER = re.compile(r"^\W*(?:page\s*)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:of|/)\s*\d{1,4})?\W*$", re.IGNORECASE)
_TERMINAL = re.compile(r"[.!?…:;。！？；]['\"”’)\]」』]*$")


def _unspace_letters(raw: str) -> str:
    """Decorative letter-spacing ("S T .   M A R T I N 'S   P R E S S") would be read letter by letter.
    Letters are one space apart and words wider apart, so the words can be rebuilt exactly."""
    tokens = [t for t in raw.strip().split(" ") if t]
    singles = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
    if len(tokens) < 4 or singles < 0.7 * len(tokens) or not re.search(r"\S {2,}\S", raw.strip()) and len(tokens) > 12:
        return raw
    words = [w.replace(" ", "") for w in re.split(r" {2,}", raw.strip()) if w.strip()]
    return " ".join(words)


def _line_from_spans(spans: list[dict], page_no: int, block_no: int, page_height: float, bbox) -> Line | None:
    parts, sizes, bold_chars, total_chars = [], [], 0, 0
    normal = [s for s in spans if s["text"].strip()]
    if not normal:
        return None
    base = max(normal, key=lambda s: len(s["text"].strip()))["size"]
    for span in spans:
        text = span["text"]
        # superscript footnote markers: flag bit 0, or a tiny raised digit run
        if text.strip() and (span["flags"] & 1 or span["size"] < base * 0.75) and re.fullmatch(r"\s*[\d*†‡§]+\s*", text):
            continue
        parts.append(text)
        n = len(text.strip())
        if n:
            sizes.append((span["size"], n))
            total_chars += n
            if span["flags"] & 16 or re.search(r"bold|black|heavy|semibold", span.get("font", ""), re.IGNORECASE):
                bold_chars += n
    text = clean_text(_unspace_letters("".join(parts)))
    if not text:
        return None
    size = sum(s * n for s, n in sizes) / max(1, sum(n for _, n in sizes))
    return Line(page_no, bbox[0], bbox[1], bbox[3], text, round(size, 1), bold_chars > total_chars / 2, block_no, page_height)


def _read_lines(doc: pymupdf.Document) -> list[list[Line]]:
    pages: list[list[Line]] = []
    for page_no, page in enumerate(doc):
        data = page.get_text("dict", sort=True)
        lines: list[Line] = []
        for block_no, block in enumerate(data["blocks"]):
            if block.get("type") != 0:
                continue
            for raw in block["lines"]:
                wdir = raw.get("dir", (1, 0))
                if abs(wdir[1]) > 0.2:  # skip rotated/vertical marginalia
                    continue
                line = _line_from_spans(raw["spans"], page_no, block_no, page.rect.height, raw["bbox"])
                if line and not any(
                    # the same text printed again on top of itself (fake-bold / shadow effects)
                    o.text == line.text and abs(o.x0 - line.x0) < 0.5 * line.size and abs(o.y0 - line.y0) < 0.3 * line.size
                    for o in lines
                ):
                    lines.append(line)
        pages.append(lines)
    return pages


def _edge_key(text: str) -> str:
    key = re.sub(r"\d+", "#", text.lower())
    key = re.sub(r"^\W*[ivxlcdm]+\W+|\W+[ivxlcdm]+\W*$", "", key)
    return re.sub(r"\s+", " ", key).strip(" .|-–—")


def _strip_headers_footers(pages: list[list[Line]], body_size: float, printed: dict[int, str] | None = None) -> None:
    """Remove page numbers and lines repeated in the top/bottom edge of 3+ pages.

    The page numbers are kept (in `printed`): they are what the book's own index refers to."""
    edges: list[tuple[int, Line]] = []
    for lines in pages:
        if not lines:
            continue
        ordered = sorted(lines, key=lambda l: l.y0)
        for line in ordered[:2] + ordered[-2:]:
            zone = line.page_height * 0.16
            if line.y1 < zone or line.y0 > line.page_height - zone:
                edges.append((id(line), line))
    counts = Counter(_edge_key(line.text) for _, line in edges)
    doomed: set[int] = set()
    for key_id, line in sorted(edges, key=lambda e: (e[1].page, e[1].y0)):
        if _PAGE_NUMBER.match(line.text):
            doomed.add(key_id)
            if printed is not None:
                number = re.sub(r"(?i)^\W*(?:page\s*)?|\s*(?:of|/)\s*\d+\W*$|\W+$", "", line.text).strip()
                if number and (line.page not in printed or len(number) > len(printed[line.page])):
                    printed[line.page] = number
            continue
        key = _edge_key(line.text)
        if not key or counts[key] < 3 or line.size >= body_size * 1.25:
            continue  # a big line in the margin is a real heading, not a running header
        doomed.add(key_id)
    for lines in pages:
        lines[:] = [l for l in lines if id(l) not in doomed]


def _join_lines(texts: list[str]) -> str:
    out = ""
    for text in texts:
        if not out:
            out = text
        elif out.endswith("-") and not out.endswith("--") and text[:1].islower():
            out = out[:-1] + text  # re-join hyphenated word
        elif out.endswith("-") and not out.endswith("--") and text[:1].isupper():
            out += text  # compound split at its hyphen: "Croquet-" + "Ground"
        elif _CJK_CHAR.match(out[-1]) or _CJK_CHAR.match(text[0]):
            out += text
        else:
            out += " " + text
    return out


def _paragraphs(pages: list[list[Line]], body_size: float) -> list[Para]:
    paras: list[Para] = []
    for lines in pages:
        first = True
        by_block: dict[int, list[Line]] = {}
        for line in lines:
            by_block.setdefault(line.block, []).append(line)
        for block_lines in by_block.values():
            block_lines.sort(key=lambda l: (round(l.y0, 0), l.x0))
            # merge fragments that sit on the same visual line
            merged: list[Line] = []
            for line in block_lines:
                if merged and abs(line.y0 - merged[-1].y0) < 2 and line.x0 > merged[-1].x0:
                    prev = merged[-1]
                    merged[-1] = Line(prev.page, prev.x0, prev.y0, max(prev.y1, line.y1), prev.text + " " + line.text,
                                      max(prev.size, line.size), prev.bold and line.bold, prev.block, prev.page_height)
                else:
                    merged.append(line)
            left = statistics.median(l.x0 for l in merged)
            current: list[Line] = []

            def flush() -> None:
                nonlocal first
                if not current:
                    return
                text = _join_lines([l.text for l in current])
                size = max(l.size for l in current)
                paras.append(Para(current[0].page, current[0].y0, text, size, all(l.bold for l in current),
                                  len(current), first, [l.text for l in current]))
                first = False
                current.clear()

            for line in merged:
                if current:
                    prev = current[-1]
                    gap = line.y0 - prev.y1
                    indent = line.x0 - left > max(6.0, line.size * 0.8)
                    size_jump = abs(line.size - prev.size) > max(1.0, 0.15 * body_size)
                    if size_jump or gap > line.size * 1.1 or (indent and _TERMINAL.search(prev.text)):
                        flush()
                current.append(line)
            flush()
    return paras


def _merge_across_pages(paras: list[Para]) -> list[Para]:
    """Re-join a sentence split by a page break or by a block boundary (e.g. beside a drop cap)."""
    out: list[Para] = []
    for para in paras:
        if (
            out
            and 0 <= para.page - out[-1].page <= 2  # a text-less page (picture, ad) may sit between
            and not _TERMINAL.search(out[-1].text)
            and para.text[:1].islower()
            and abs(para.size - out[-1].size) < 1
        ):
            prev = out[-1]
            joined = _join_lines([prev.text, para.text])
            if para.page != prev.page:  # the paragraph continues on the next page: remember where
                prev.breaks.append((len(joined) - len(para.text), para.page))
            prev.breaks += [(off + len(joined) - len(para.text), pg) for off, pg in para.breaks]
            prev.text = joined
            prev.n_lines += para.n_lines
            continue
        out.append(para)
    return out


_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ’']+")
_OPEN_QUOTES = "‘“'\""


def _fix_drop_caps(paras: list[Para], body_size: float) -> list[Para]:
    """Re-attach decorative initials: 'W' + 'e were…' -> 'We were…', but 'I' + 'am…' -> 'I am…'.

    Whether to join is decided by the book's own vocabulary: 'we' is common in the text, 'e' is not.
    """
    vocab = Counter(w.lower() for p in paras for w in _WORD_RE.findall(p.text))

    def attach(cap: str, rest: str) -> str:
        rest = rest.lstrip()
        m = _WORD_RE.match(rest)
        if not m:
            return f"{cap} {rest}"
        fragment = m.group(0).lower()
        joined = (cap.lstrip(_OPEN_QUOTES) + fragment).lower()
        return cap + rest if vocab[joined] >= vocab[fragment] else f"{cap} {rest}"

    out: list[Para] = []
    skip_next = False
    for i, para in enumerate(paras):
        if skip_next:
            skip_next = False
            continue
        text = para.text.strip()
        nxt = paras[i + 1] if i + 1 < len(paras) else None
        letter = text.lstrip(_OPEN_QUOTES)
        if (
            len(letter) == 1 and len(text) <= 2 and letter.isalpha() and letter.isupper() and para.size >= body_size * 1.5
            and nxt is not None and nxt.page == para.page and nxt.size < para.size
        ):
            nxt.text = attach(text, nxt.text)
            nxt.first_on_page = nxt.first_on_page or para.first_on_page
            out.append(nxt)
            skip_next = True
            continue
        # initial kept on the same line: "W e were", "I t was"
        m = re.match(rf"^([{_OPEN_QUOTES}]?)([A-Z]) ([a-z’']+)", text)
        if m and vocab[(m.group(2) + m.group(3)).lower()] > 2 * vocab[m.group(3).lower()]:
            para.text = m.group(1) + m.group(2) + text[m.end(2) + 1:]
        out.append(para)
    return out


def _body_size(pages: list[list[Line]]) -> float:
    weights: Counter = Counter()
    for lines in pages:
        for line in lines:
            weights[round(line.size * 2) / 2] += len(line.text)
    return weights.most_common(1)[0][0] if weights else 11.0


_META_JUNK = re.compile(
    r"^(unknown|anonymous|untitled|none|null|document\s*\d*|book\s*\d*|pdf|scan(ned)?( document)?|"
    r"converted( by| with)?.*|adobe.*|calibre.*|created by.*)$",
    re.IGNORECASE,
)


def _clean_meta(value: str | None) -> str:
    """PDF metadata is often a placeholder ("(anonymous)", "Microsoft Word - draft.docx"): ignore those."""
    value = clean_text(value or "").strip("()[]{}<>\"'“”‘’ \t-_")
    if not value or re.search(r"microsoft word|\.docx?$|\.pdf$|\.indd$", value, re.IGNORECASE) or _META_JUNK.match(value):
        return ""
    return value


def _page_labels(doc: pymupdf.Document, printed: dict[int, str]) -> list[str]:
    """What page number the book itself shows on each PDF page: its page labels when the PDF has
    them, otherwise the numbers printed in the margins, filled in by the offset they follow."""
    labels = []
    for i in range(doc.page_count):
        try:
            labels.append((doc[i].get_label() or "").strip())
        except Exception:
            labels.append("")
    if sum(bool(x) for x in labels) >= 0.5 * doc.page_count:
        return labels
    offsets = Counter(int(v) - i for i, v in printed.items() if v.isdigit())
    if not offsets:
        return [""] * doc.page_count
    offset, seen = offsets.most_common(1)[0]
    if seen < max(3, 0.2 * len(printed)):
        return [""] * doc.page_count
    # pages that print no number (chapter openers, blanks) still belong to the numbering: fill them in
    return [printed.get(i) or (str(i + offset) if i + offset >= 1 else "") for i in range(doc.page_count)]


def extract(path: str, fallback_title: str) -> Extracted:
    doc = pymupdf.open(path)
    try:
        if doc.needs_pass:
            raise ValueError("This PDF is password-protected. Remove the password and try again.")
        pages = _read_lines(doc)
        body = _body_size(pages)
        printed: dict[int, str] = {}
        _strip_headers_footers(pages, body, printed)
        paras = _merge_across_pages(_fix_drop_caps(_paragraphs(pages, body), body))
        meta = doc.metadata or {}
        toc = [(int(level), clean_text(title), int(page)) for level, title, page in doc.get_toc(simple=True)]
        return Extracted(
            paras=paras,
            body_size=body,
            pages=doc.page_count,
            title=_clean_meta(meta.get("title")) or fallback_title,
            author=_clean_meta(meta.get("author")),
            toc=toc,
            labels=_page_labels(doc, printed),
        )
    finally:
        doc.close()


def render_cover(path: str, out_path: str, width: int = 600) -> bool:
    doc = pymupdf.open(path)
    try:
        if doc.page_count == 0:
            return False
        page = doc[0]
        zoom = width / max(1.0, page.rect.width)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pix.save(out_path, jpg_quality=85)
        return True
    finally:
        doc.close()
