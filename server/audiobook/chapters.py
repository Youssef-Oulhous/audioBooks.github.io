"""Find the chapters of a book.

Strategies, best first:
  1. outline   – the PDF's bookmarks (most reliable when present)
  2. headings  – lines like "Chapter 12", "CHAPTER XII", "Prologue", "第三章", "12." on their own
  3. fontsize  – short lines set noticeably larger than the body text
  4. sections  – no structure found: ~20-minute sections split at paragraph breaks
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf_extract import Extracted, Para
from .textprep import estimate_seconds, roman_to_int


@dataclass
class Chapter:
    title: str
    paragraphs: list[str]
    start_page: int
    spoken_title: str | None = None  # heading read aloud; defaults to title
    include: bool = True
    words: int = 0
    est_seconds: float = 0.0
    preview: str = ""
    pages: list[int] = field(default_factory=list)  # 1-based PDF page where each paragraph starts
    para_breaks: list[list[tuple[int, int]]] = field(default_factory=list)  # per paragraph: (char offset, 1-based page)
    part: str | None = None  # the part / book / volume this chapter belongs to
    sections: list[tuple[str, int]] = field(default_factory=list)  # (title, 1-based page) inside the chapter


@dataclass
class Detection:
    method: str
    note: str
    chapters: list[Chapter] = field(default_factory=list)


_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    "eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|first|second|third|fourth|"
    "fifth|sixth|seventh|eighth|ninth|tenth|last|final"
)
_CHAPTER_RE = re.compile(
    rf"^(chapter|chap\.|part|book|letter|volume|act|canto|chapitre|partie|livre|cap[ií]tulo|capitolo|parte|libro)\s+(\d{{1,3}}|[ivxlcdm]{{1,8}}|(?:{_NUMBER_WORDS})(?:[\s-](?:{_NUMBER_WORDS}))*)\b[.:\s—–-]*(.{{0,70}})$",
    re.IGNORECASE,
)
_SPECIAL_RE = re.compile(
    r"^(prologue|epilogue|introduction|preface|foreword|afterword|interlude|conclusion|appendix(?: [a-z0-9]+)?|"
    r"postscript|coda|author'?s note|a note from the author|acknowledge?ments|"
    r"[ée]pilogue|pr[ée]face|avant-propos|pr[óo]logo|ep[íi]logo|introducci[óo]n|prefacio|introduzione|prefazione|introdu[çc][ãa]o|pref[áa]cio)\W*$",
    re.IGNORECASE,
)
_ZH_RE = re.compile(r"^(第[一二三四五六七八九十百千零〇两\d]+[章节回卷部篇集幕]|序章|序言|序|楔子|尾声|后记|前言|引子|番外|终章)(.{0,40})$")
_NUMERIC_RE = re.compile(r"^(\d{1,3}|[IVXLC]{1,7})\.?$")
_TOC_LINE_RE = re.compile(r"(\.{3,}|…|\s{3,}|\t)\s*\d{1,4}$")
_PART_RE = re.compile(r"^(part|book|volume|partie|livre|parte|libro)\b|^第[一二三四五六七八九十百千零〇两\d]+[部卷篇]", re.IGNORECASE)
_SKIP_TITLE_RE = re.compile(
    r"^((table of )?contents|index|copyright.*|bibliography|references|works cited|(end)?notes|acknowledge?ments|"
    r"about the (author|authors|publisher)|also by .*|other (books|titles) by .*|books by .*|praise for .*|cover|"
    r"title page|half title|glossary|colophon|目录|版权.*|索引|参考文献)$",
    re.IGNORECASE,
)
_JUNK_TEXT_RE = re.compile(r"all rights reserved|isbn|printed in|library of congress|copyright ©|©", re.IGNORECASE)

SECTION_SECONDS = 20 * 60


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z㐀-鿿]+", "", text.lower())


def _word_count(text: str) -> int:
    cjk = len(re.findall(r"[㐀-鿿]", text))
    return cjk + len(re.findall(r"[A-Za-zÀ-ÿ0-9']+", text))


def _looks_like_subtitle(para: Para, body_size: float) -> bool:
    words = para.text.split()
    if para.n_lines > 2 or not (0 < len(words) <= 10) or re.search(r"[.!?;。！？]['\"”’]?$", para.text):
        return False
    if _CHAPTER_RE.match(para.text) or _SPECIAL_RE.match(para.text) or _ZH_RE.match(para.text):
        return False
    long_words = [w for w in words if len(w) > 3]
    title_case = bool(long_words) and sum(w[0].isupper() for w in long_words) / len(long_words) >= 0.6
    return para.size >= body_size * 1.08 or para.bold or para.text.isupper() or title_case


# ----------------------------------------------------------------------------- boundaries

@dataclass
class _Boundary:
    index: int  # first paragraph of the heading
    title: str
    skip: int = 1  # heading paragraphs to drop from the body
    is_part: bool = False
    part: str | None = None  # the part this chapter sits in ("Part II")
    sections: list[tuple[str, int]] = field(default_factory=list)  # deeper headings, for navigation


def _outline_levels(toc: list[tuple[int, str, int]]) -> tuple[list[tuple[int, str, int]], int, bool]:
    """Strip wrapper roots and work out which level is a chapter. Returns (toc, chapter level, has parts)."""
    while True:
        top = min(level for level, _, _ in toc)
        roots = [e for e in toc if e[0] == top]
        if len(roots) == 1 and len(toc) > 2:
            toc = [e for e in toc if e is not roots[0]]
        else:
            break
    top = min(level for level, _, _ in toc)
    tops = [(i, e) for i, e in enumerate(toc) if e[0] == top]
    has_children = [i for i, e in tops if i + 1 < len(toc) and toc[i + 1][0] > top]
    part_named = sum(1 for _, e in tops if _PART_RE.match(e[1]) or roman_to_int(e[1].strip(". ")))
    parts = bool(has_children) and part_named >= len(tops) / 2 and any(e[0] == top + 1 for e in toc)
    return toc, (top + 1 if parts else top), parts


def _from_outline(ex: Extracted) -> list[_Boundary] | None:
    """Chapters from the PDF's bookmarks, keeping the book's own shape: parts group chapters, and
    anything deeper (numbered sections, subheadings) is kept as navigation points inside a chapter."""
    toc = [(lvl, title, page) for lvl, title, page in ex.toc if title and 1 <= page <= ex.pages]
    if len(toc) < 2:
        return None
    toc, chapter_level, has_parts = _outline_levels(toc)
    wanted = []  # (index in toc, level, title, page)
    for i, (level, title, page) in enumerate(toc):
        childless_part = level == chapter_level - 1 and (i + 1 >= len(toc) or toc[i + 1][0] <= level)
        if level == chapter_level or (has_parts and childless_part):
            wanted.append((i, level, title, page))
    if len(wanted) < 2:
        return None

    by_page: dict[int, list[int]] = {}
    for i, p in enumerate(ex.paras):
        by_page.setdefault(p.page, []).append(i)

    boundaries: list[_Boundary] = []
    part_title = None
    for n, (i, level, title, page) in enumerate(wanted):
        if has_parts:
            above = [e for e in toc[:i] if e[0] == chapter_level - 1]
            part_title = above[-1][1] if above and level == chapter_level else None
        # deeper bookmarks up to the next chapter belong to this chapter
        nxt = wanted[n + 1][0] if n + 1 < len(wanted) else len(toc)
        sections = [(t, p) for lvl, t, p in toc[i + 1:nxt] if lvl > chapter_level]
        page0 = page - 1
        start, skip = None, 0
        key = _norm(title)[:40]
        for idx in by_page.get(page0, []):
            text_key = _norm(ex.paras[idx].text)
            if key and len(ex.paras[idx].text) <= 200 and (text_key.startswith(key) or (len(text_key) >= 3 and key.startswith(text_key))):
                start, skip = idx, 1
                j = idx + 1
                while j < len(ex.paras) and j - idx < 3 and ex.paras[j].page == page0 and _looks_like_subtitle(ex.paras[j], ex.body_size) and len(_norm(ex.paras[j].text)) >= 3 and _norm(ex.paras[j].text) in _norm(title):
                    skip += 1
                    j += 1
                break
        if start is None:
            later = [idx for p2 in range(page0, min(ex.pages, page0 + 3)) for idx in by_page.get(p2, [])]
            if not later:
                continue
            start = later[0]
        boundaries.append(_Boundary(start, title, skip, part=part_title, sections=sections))
    return boundaries if len(boundaries) >= 2 else None


def _heading_candidates(ex: Extracted) -> list[_Boundary]:
    found: list[_Boundary] = []
    for i, para in enumerate(ex.paras):
        text = para.text.strip()
        big = para.size >= ex.body_size * 1.3
        if para.n_lines > (4 if big else 2) or len(text) > 110 or _TOC_LINE_RE.search(text):
            continue
        m = _CHAPTER_RE.match(text)
        if m and re.search(r"(^|\s)\d{1,4}$", m.group(3) or "") and not big:
            continue  # "Chapter 3 The Storm 41" is a contents line
        if m or _ZH_RE.match(text) or (_SPECIAL_RE.match(text) and len(text.split()) <= 6):
            found.append(_Boundary(i, text, 1, bool(_PART_RE.match(text))))
    # a page listing 3+ chapter names is a table of contents, not the chapters themselves
    per_page: dict[int, int] = {}
    for b in found:
        per_page[ex.paras[b.index].page] = per_page.get(ex.paras[b.index].page, 0) + 1
    found = [b for b in found if per_page[ex.paras[b.index].page] < 3]
    return found


def _numeric_candidates(ex: Extracted) -> list[_Boundary]:
    found, values = [], []
    for i, para in enumerate(ex.paras):
        m = _NUMERIC_RE.match(para.text.strip())
        if not m or para.n_lines != 1:
            continue
        if not (para.size >= ex.body_size * 1.15 or para.bold or para.first_on_page):
            continue
        token = m.group(1)
        value = int(token) if token.isdigit() else roman_to_int(token)
        if value:
            found.append(_Boundary(i, token))
            values.append(value)
    if len(found) < 3:
        return []
    rising = sum(1 for a, b in zip(values, values[1:]) if b == a + 1)
    return found if rising >= 0.7 * (len(values) - 1) else []


def _fontsize_candidates(ex: Extracted) -> list[_Boundary]:
    large = [
        (i, p) for i, p in enumerate(ex.paras)
        if p.size >= ex.body_size * 1.3 and p.n_lines <= 3 and 0 < len(p.text.split()) <= 14 and re.search(r"[^\W\d_]", p.text)
    ]
    if not large:
        return []
    tiers = sorted({round(p.size) for _, p in large}, reverse=True)
    best: list[_Boundary] = []
    for tier in tiers:
        picked: list[_Boundary] = []
        for i, p in large:
            if round(p.size) < tier:
                continue
            prev = picked[-1] if picked else None
            if prev and i == prev.index + prev.skip and ex.paras[prev.index].page == p.page:
                prev.title += " " + p.text  # heading wrapped onto two paragraphs
                prev.skip += 1
            else:
                picked.append(_Boundary(i, p.text))
        if len(picked) > max(3, ex.pages / 2.5):
            break
        if len(picked) >= 2:
            best = picked
    return best


def _attach_subtitles(ex: Extracted, boundaries: list[_Boundary]) -> None:
    starts = {b.index for b in boundaries}
    for b in boundaries:
        j = b.index + b.skip
        if j < len(ex.paras) and j not in starts and ex.paras[j].page == ex.paras[b.index].page and _looks_like_subtitle(ex.paras[j], ex.body_size):
            sep = " " if re.search(r"[㐀-鿿]", b.title) else ": "
            b.title = f"{b.title.rstrip(' .:')}{sep}{ex.paras[j].text.strip()}"
            b.skip += 1


RUNNING_HEADER_PAGES = 3


def _dedupe(ex: Extracted, boundaries: list[_Boundary]) -> list[_Boundary]:
    """Drop running headers without losing real repeats.

    A chapter title often repeats at the top of every page of that chapter; those copies sit within
    a page or two of each other, so only the largest-set one of each such run is kept. The same title
    appearing again much later is a different chapter (novels number chapters per part: "Chapter I"
    exists in every part), and is kept."""
    runs: dict[str, list[list[_Boundary]]] = {}
    for b in sorted(boundaries, key=lambda b: b.index):
        key = _norm(b.title)
        page = ex.paras[b.index].page
        groups = runs.setdefault(key, [])
        if groups and page - ex.paras[groups[-1][-1].index].page <= RUNNING_HEADER_PAGES:
            groups[-1].append(b)
        else:
            groups.append([b])
    kept = [max(group, key=lambda b: (ex.paras[b.index].size, -b.index)) for groups in runs.values() for group in groups]
    return sorted(kept, key=lambda b: b.index)


# ----------------------------------------------------------------------------- assembly

def _pretty(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    if ": " in title:
        return ": ".join(_pretty(part) for part in title.split(": "))
    letters = [c for c in title if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 3:
        small = {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for", "by", "with"}
        words = title.lower().split(" ")
        title = " ".join(w if (k and w in small) else (w[:1].upper() + w[1:]) for k, w in enumerate(words))
        # "Chapter Xii" -> "Chapter XII"
        title = re.sub(
            r"\b(Chapter|Part|Book|Letter|Volume|Act|Canto|Section)\s+([ivxlcdm]+)\b",
            lambda m: f"{m.group(1)} {m.group(2).upper()}" if roman_to_int(m.group(2)) else m.group(0),
            title,
            flags=re.IGNORECASE,
        )
    return title


def _finish(chapter: Chapter, lang: str) -> Chapter:
    body = " ".join(chapter.paragraphs)
    chapter.words = _word_count(body)
    chapter.est_seconds = round(estimate_seconds(body, lang) + estimate_seconds(chapter.spoken_title or chapter.title, lang), 1)
    chapter.preview = body[:220]
    if chapter.words < 40 or _SKIP_TITLE_RE.match(chapter.title.strip(" .:")):
        chapter.include = False
    return chapter


def _build(ex: Extracted, boundaries: list[_Boundary], lang: str) -> list[Chapter]:
    paras = ex.paras
    boundaries = sorted(boundaries, key=lambda b: b.index)
    chapters: list[Chapter] = []
    first = boundaries[0].index if boundaries else len(paras)
    front = [p.text for p in paras[:first]]
    if _word_count(" ".join(front)) >= 50:
        text = " ".join(front)
        opening = _finish(Chapter("Opening pages", front, 1, pages=[p.page + 1 for p in paras[:first]],
                                  para_breaks=[[(o, pg + 1) for o, pg in p.breaks] for p in paras[:first]]), lang)
        opening.include = opening.words >= 400 and not _JUNK_TEXT_RE.search(text[:3000])
        chapters.append(opening)

    pending_part: str | None = None
    pending_part_title: str | None = None
    announced_part: str | None = None
    for k, b in enumerate(boundaries):
        end = boundaries[k + 1].index if k + 1 < len(boundaries) else len(paras)
        body_paras = paras[b.index + b.skip:end]
        body = [p.text for p in body_paras]
        body_pages = [p.page + 1 for p in body_paras]
        body_breaks = [[(o, pg + 1) for o, pg in p.breaks] for p in body_paras]
        title = _pretty(b.title)
        if b.is_part and _word_count(" ".join(body)) < 60:
            pending_part = title  # "Part One" page with no text: announce it with the next chapter
            pending_part_title = title
            continue
        part_name = _pretty(b.part) if b.part else pending_part_title
        # the first chapter of a part announces it: novels restart chapter numbering in every part
        opens_part = part_name and part_name != announced_part
        spoken = f"{pending_part}. {title}" if pending_part else (f"{part_name}. {title}" if opens_part else None)
        announced_part = part_name or announced_part
        pending_part = None
        chapters.append(_finish(Chapter(title, body, paras[b.index].page + 1, spoken_title=spoken, pages=body_pages,
                                        para_breaks=body_breaks, part=_pretty(b.part) if b.part else pending_part_title,
                                        sections=[(_pretty(t), p) for t, p in b.sections]), lang))
    return chapters


def _sections(ex: Extracted, lang: str) -> list[Chapter]:
    groups: list[list[Para]] = []
    current: list[Para] = []
    seconds = 0.0
    for para in ex.paras:
        current.append(para)
        seconds += estimate_seconds(para.text, lang)
        if seconds >= SECTION_SECONDS:
            groups.append(current)
            current, seconds = [], 0.0
    if current:
        groups.append(current)
    return [
        _finish(Chapter(f"Section {n}", [p.text for p in g], g[0].page + 1, spoken_title=f"Section {n}",
                        pages=[p.page + 1 for p in g], para_breaks=[[(o, pg + 1) for o, pg in p.breaks] for p in g]), lang)
        for n, g in enumerate(groups, 1)
    ]


def detect(ex: Extracted, lang: str) -> Detection:
    outline = _from_outline(ex)
    if outline:
        chapters = _build(ex, outline, lang)
        n = sum(1 for c in chapters if c.include)
        parts = len({c.part for c in chapters if c.include and c.part})
        sections = sum(len(c.sections) for c in chapters if c.include)
        extra = "".join([f" in {parts} parts" if parts else "", f", with {sections} sections" if sections else ""])
        return Detection("outline", f"Found {n} chapters{extra} in the PDF's bookmarks.", chapters)

    headings = _dedupe(ex, _heading_candidates(ex))
    if len(headings) < 2:
        headings = _dedupe(ex, _numeric_candidates(ex))
    if len(headings) >= 2:
        _attach_subtitles(ex, headings)
        chapters = _build(ex, headings, lang)
        n = sum(1 for c in chapters if c.include)
        return Detection("headings", f"Found {n} chapters from headings like “{_pretty(headings[0].title)}”.", chapters)

    big = _dedupe(ex, _fontsize_candidates(ex))
    if len(big) >= 2:
        chapters = _build(ex, big, lang)
        n = sum(1 for c in chapters if c.include)
        return Detection("fontsize", f"Found {n} chapters from the large headings in the book.", chapters)

    chapters = _sections(ex, lang)
    return Detection(
        "sections",
        f"No chapter headings found, so the book is split into {len(chapters)} sections of about 20 minutes.",
        chapters,
    )
