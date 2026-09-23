"""Text cleanup, language detection, English number reading, sentence splitting and
AuK-sized chunking.

Duration estimates reuse AuK's own calibration (src/auk/infer/pe.config.yaml):
seconds_per_utf8_byte en=0.0656, zh=0.0803, and texts under 10 bytes are spoken at 0.3x speed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


SEC_PER_UTF8_BYTE = {"en": 0.0656, "zh": 0.0803}
SHORT_TEXT_BYTE_THRESHOLD = 10
SHORT_TEXT_SPEED = 0.3

_CJK = "㐀-䶿一-鿿豈-﫿぀-ヿ"
_CJK_RE = re.compile(f"[{_CJK}]")

# ----------------------------------------------------------------------------- cleanup

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    "­": "", "​": "", "‌": "", "‍": "", "﻿": "", " ": " ", " ": " ", " ": " ",
}
_LIGATURE_RE = re.compile("|".join(map(re.escape, _LIGATURES)))


_PRIVATE_USE_RE = re.compile("[\ue000-\uf8ff\U000f0000-\U0010ffff\ufffd]")


def clean_text(text: str) -> str:
    text = _PRIVATE_USE_RE.sub("", text)  # icon-font glyphs, ornaments, replacement chars
    text = _LIGATURE_RE.sub(lambda m: _LIGATURES[m.group(0)], text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text.strip()


def is_scene_break(paragraph: str) -> bool:
    """'* * *', '###', '~', '—' style separators between scenes."""
    stripped = paragraph.strip()
    return bool(stripped) and len(stripped) <= 12 and not re.search(rf"[A-Za-z0-9{_CJK}]", stripped)


# ----------------------------------------------------------------------------- language

_STOPWORDS = {
    "en": "the and of to a in is was that he it for with as his on be at by had not are but from or have an they which you were her she".split(),
    "fr": "le la les des est et une que pas pour dans du un il elle qui sur au avec ne se son sa ce mais nous vous".split(),
    "es": "el la los las de que y en un una es por con no se su para al lo como más pero sus le ya".split(),
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch es an werden aus".split(),
    "it": "il di che la e in un per non una sono le si con del della gli lo ma mi ha anche".split(),
    "pt": "de que e o a do da em um para com não uma os no se na por mais as dos como mas ao".split(),
    "nl": "de het een en van ik te dat die in is niet zijn op aan met voor er maar om".split(),
}
_LANGUAGE_NAMES = {
    "en": "English", "zh": "Chinese", "fr": "French", "es": "Spanish", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "ar": "Arabic", "ru": "Russian", "hi": "Hindi", "ja": "Japanese",
    "other": "an unrecognised language",
}


def detect_language(text: str) -> str:
    sample = text[:300_000]
    cjk = len(_CJK_RE.findall(sample))
    arabic = len(re.findall(r"[؀-ۿݐ-ݿ]", sample))
    cyrillic = len(re.findall(r"[Ѐ-ӿ]", sample))
    latin = len(re.findall(r"[A-Za-zÀ-ɏ]", sample))
    devanagari = len(re.findall(r"[\u0900-\u097f]", sample))
    letters = cjk + arabic + cyrillic + latin
    if devanagari > 0.3 * (letters + devanagari):
        return "hi"
    if letters == 0:
        return "other"
    if cjk / letters > 0.2:
        kana = len(re.findall(r"[\u3040-\u30ff]", sample))
        return "ja" if kana > 0.1 * cjk else "zh"
    if arabic / letters > 0.3:
        return "ar"
    if cyrillic / letters > 0.3:
        return "ru"
    words = re.findall(r"[a-zà-ÿ]+", sample.lower())
    if not words:
        return "other"
    counts = {}
    for lang, stops in _STOPWORDS.items():
        stopset = set(stops)
        counts[lang] = sum(1 for w in words if w in stopset)
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "en"


def narration_language(detected: str, supported=("en", "zh")) -> tuple[str, str | None]:
    """Pick the narration language for a book. Returns (voice language, warning or None)."""
    if detected in supported:
        return detected, None
    name = _LANGUAGE_NAMES.get(detected, _LANGUAGE_NAMES["other"])
    known = ", ".join(_LANGUAGE_NAMES[code] for code in sorted(supported) if code in _LANGUAGE_NAMES)
    return "en", (
        f"This book looks like it's in {name}. The current voice engine reads {known}, "
        f"so {name} narration may sound wrong or heavily accented. Try one chapter first."
    )


# ----------------------------------------------------------------------------- English numbers → words

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand")]
_ORDINAL_FIX = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


def number_to_words(n: int) -> str:
    if n < 0:
        return "minus " + number_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("-" + _ONES[ones] if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + (" " + number_to_words(rest) if rest else "")
    for value, name in _SCALES:
        if n >= value:
            head, rest = divmod(n, value)
            return number_to_words(head) + " " + name + (" " + number_to_words(rest) if rest else "")
    return str(n)


def ordinal_words(n: int) -> str:
    words = number_to_words(n)
    head, _, last = words.rpartition(" ")
    prefix, dash, tail = last.rpartition("-")
    if tail in _ORDINAL_FIX:
        tail = _ORDINAL_FIX[tail]
    elif tail.endswith("y"):
        tail = tail[:-1] + "ieth"
    else:
        tail = tail + "th"
    last = prefix + dash + tail
    return (head + " " + last) if head else last


def year_to_words(n: int) -> str:
    if 2000 <= n <= 2009:
        return "two thousand" + (" " + number_to_words(n - 2000) if n > 2000 else "")
    high, low = divmod(n, 100)
    if low == 0:
        return number_to_words(high) + " hundred"
    if low < 10:
        return number_to_words(high) + " oh " + number_to_words(low)
    return number_to_words(high) + " " + number_to_words(low)


def _digits_words(digits: str) -> str:
    return " ".join(_ONES[int(d)] for d in digits)


_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_ROMAN_RE = re.compile(r"^(?=[mdclxvi]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.IGNORECASE)


def roman_to_int(token: str) -> int | None:
    if not token or not _ROMAN_RE.match(token):
        return None
    total, prev = 0, 0
    for ch in reversed(token.lower()):
        value = _ROMAN_VALUES[ch]
        total = total - value if value < prev else total + value
        prev = max(prev, value)
    return total or None


_CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"), "€": ("euro", "euros", "cent", "cents")}
_MONTHS = {
    "Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "Jun": "June", "Jul": "July", "Aug": "August",
    "Sep": "September", "Sept": "September", "Oct": "October", "Nov": "November", "Dec": "December",
}
_HEADING_WORDS = r"(?:chapter|part|book|volume|section|act|scene|canto|letter)"


def _money(match: re.Match) -> str:
    symbol, whole, frac = match.group(1), match.group(2).replace(",", ""), match.group(3)
    one, many, sub_one, sub_many = _CURRENCY[symbol]
    amount = int(whole)
    text = number_to_words(amount) + " " + (one if amount == 1 else many)
    if frac and int(frac):
        cents = int(frac.ljust(2, "0")[:2])
        text += f" and {number_to_words(cents)} " + (sub_one if cents == 1 else sub_many)
    return text


def _plain_number(match: re.Match) -> str:
    return _number_text(match.group(0))


def _number_text(raw: str) -> str:
    if "." in raw:
        whole, frac = raw.split(".", 1)
        return number_to_words(int(whole.replace(",", ""))) + " point " + _digits_words(frac)
    if "," in raw:
        return number_to_words(int(raw.replace(",", "")))
    if len(raw) > 1 and raw.startswith("0"):
        return _digits_words(raw)
    if len(raw) > 15:
        return _digits_words(raw)
    n = int(raw)
    if len(raw) == 4 and 1100 <= n <= 2099:
        return year_to_words(n)
    return number_to_words(n)


def normalize_english(text: str) -> str:
    text = re.sub(r"https?://(?:www\.)?([^/\s]+)\S*", r"\1", text)
    text = re.sub(r"\[\d+\]", "", text)  # [12] footnote marks
    text = re.sub(r"\be\.g\.,?", "for example,", text, flags=re.IGNORECASE)
    text = re.sub(r"\bi\.e\.,?", "that is,", text, flags=re.IGNORECASE)
    text = re.sub(r"\betc\.", "et cetera.", text, flags=re.IGNORECASE)
    text = text.replace("&", " and ")
    text = re.sub(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.(?=\s*\d)", lambda m: _MONTHS[m.group(1)], text)
    text = re.sub(
        rf"\b({_HEADING_WORDS})\s+([IVXLCDM]+)\b",
        lambda m: f"{m.group(1)} {number_to_words(roman_to_int(m.group(2)))}" if roman_to_int(m.group(2)) else m.group(0),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"([$£€])\s?(\d[\d,]*)(?:\.(\d{1,2}))?\b", _money, text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s?%", lambda m: _number_text(m.group(1)) + " percent", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: number_to_words(int(m.group(1))) + " " + (
        "o'clock" if m.group(2) == "00" else ("oh " + number_to_words(int(m.group(2))) if m.group(2).startswith("0") else number_to_words(int(m.group(2))))
    ), text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: ordinal_words(int(m.group(1))), text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b", _plain_number, text)
    return re.sub(r"\s{2,}", " ", text).strip()


def normalize_heading(title: str, lang: str) -> str:
    """How a chapter title is read aloud: 'CHAPTER IV' -> 'Chapter four.', 'XII' -> 'Twelve.'"""
    title = clean_text(title)
    if lang == "en":
        core = title.strip(" .")
        if core.isupper() and roman_to_int(core):
            title = number_to_words(roman_to_int(core))
        else:
            # a roman numeral standing alone at the end: "Epilogue. II" -> "Epilogue. Two"
            title = re.sub(
                r"(^|[.:]\s+)([IVXLCDM]+)\s*\.?$",
                lambda m: m.group(1) + (number_to_words(roman_to_int(m.group(2))).capitalize() if roman_to_int(m.group(2)) else m.group(2)),
                title,
            )
        if title.isupper() and len(title) > 3:
            title = title.title()
        title = normalize_english(title)
    title = title.strip()
    if title and title[-1] not in ".!?。！？":
        title += "。" if _no_spaces(lang) else "."
    return title[:1].upper() + title[1:] if title else title


def normalize_for_speech(text: str, lang: str) -> str:
    text = clean_text(text)
    if lang == "en":
        text = normalize_english(text)
    return text


# ----------------------------------------------------------------------------- duration

def tts_weight(text: str, lang: str) -> float:
    """Port of AuK's _tts_utf8_weight: bytes of each char × seconds-per-byte of its script."""
    if not text.strip():
        return 0.0
    fallback = "zh" if lang == "zh" else "en"
    scripts: list[str | None] = [
        "zh" if _CJK_RE.match(c) else ("en" if c.isascii() and c.isalpha() else None) for c in text
    ]
    nxt: list[str | None] = [None] * len(text)
    following = None
    for i in range(len(text) - 1, -1, -1):
        if scripts[i]:
            following = scripts[i]
        nxt[i] = following
    weight, previous = 0.0, None
    for i, c in enumerate(text):
        script = scripts[i]
        if script is None:
            script = previous or nxt[i] or fallback
        else:
            previous = script
        weight += len(c.encode("utf-8")) * SEC_PER_UTF8_BYTE[script]
    return weight


def local_speed(text: str) -> float:
    return SHORT_TEXT_SPEED if len(text.strip().encode("utf-8")) < SHORT_TEXT_BYTE_THRESHOLD else 1.0


def estimate_seconds(text: str, lang: str) -> float:
    """Natural-pace speaking time, as AuK's F5 baseline estimates it."""
    weight = tts_weight(text, lang)
    return weight / local_speed(text) if weight else 0.0


# ----------------------------------------------------------------------------- sentences

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc", "inc", "ltd", "co", "no", "vol", "ch",
    "pp", "gen", "col", "capt", "lt", "sgt", "rev", "hon", "fig", "jan", "feb", "mar", "apr", "jun", "jul",
    "aug", "sep", "sept", "oct", "nov", "dec", "mme", "mlle", "messrs", "esq", "ave", "approx", "dept", "est",
}
_EN_BOUNDARY = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s+[\"'“‘(\[]?[A-Z0-9À-Ý])")
_ZH_BOUNDARY = re.compile(r"[。！？；…]+[”’」』）)]*")


_HI_BOUNDARY = re.compile(r"[।॥!?]+[\"'”’)]*(?=\s)")


def _no_spaces(lang: str) -> bool:
    return lang in ("zh", "ja")


def split_sentences(text: str, lang: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if lang == "hi":
        parts, start = [], 0
        for m in _HI_BOUNDARY.finditer(text):
            parts.append(text[start:m.end()])
            start = m.end()
        parts.append(text[start:])
        return [p.strip() for p in parts if p.strip()]
    if _no_spaces(lang):
        parts, start = [], 0
        for m in _ZH_BOUNDARY.finditer(text):
            parts.append(text[start:m.end()])
            start = m.end()
        parts.append(text[start:])
        return [p.strip() for p in parts if p.strip()]
    sentences, start = [], 0
    for m in _EN_BOUNDARY.finditer(text):
        before = text[start:m.start()].rstrip()
        last_word = re.split(r"[\s(\"'“‘]", before)[-1].lower().rstrip(".")
        if m.group(0).startswith(".") and (last_word in _ABBREVIATIONS or re.fullmatch(r"[a-z]", last_word)):
            continue  # "Mr. Smith", "J. R. R. Tolkien"
        sentences.append(text[start:m.end()].strip())
        start = m.end()
    sentences.append(text[start:].strip())
    return [s for s in sentences if s]


def _split_long(sentence: str, lang: str, limit: float) -> list[str]:
    """Break a sentence that is too long to voice in one go at clause marks, then at word gaps."""
    if estimate_seconds(sentence, lang) <= limit:
        return [sentence]
    clause = r"(?<=[，、；：,;:—–])" if _no_spaces(lang) else r"(?<=[,;:—–])\s*"
    pieces = [p for p in re.split(clause, sentence) if p.strip()]
    if len(pieces) == 1:
        tokens = list(sentence) if _no_spaces(lang) else sentence.split(" ")
        joiner = "" if _no_spaces(lang) else " "
        out, current = [], []
        for tok in tokens:
            if current and estimate_seconds(joiner.join(current + [tok]), lang) > limit:
                out.append(joiner.join(current))
                current = []
            current.append(tok)
        if current:
            out.append(joiner.join(current))
        return out
    out, current = [], ""
    joiner = "" if _no_spaces(lang) else " "
    for piece in pieces:
        piece = piece.strip()
        candidate = (current + joiner + piece) if current else piece
        if current and estimate_seconds(candidate, lang) > limit:
            out.extend(_split_long(current, lang, limit))
            current = piece
        else:
            current = candidate
    if current:
        out.extend(_split_long(current, lang, limit))
    return out


# ----------------------------------------------------------------------------- chunks

PAUSE_SENTENCE = 0.28
PAUSE_PARAGRAPH = 0.75
PAUSE_SCENE = 1.6
PAUSE_HEADING = 1.3


@dataclass
class Chunk:
    text: str
    est_seconds: float  # natural-pace estimate (before voice rate / pace adjustments)
    pause_after: float
    page: int | None = None  # page where this chunk's first sentence starts
    # pages starting inside this chunk: (character offset in `text`, page)
    page_breaks: list[tuple[int, int]] = field(default_factory=list)


def _normalized_paragraph(paragraph: str, breaks: list, lang: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalise a paragraph for speech and keep track of where each new page starts inside it.

    Returns (text, [(character offset in text, page)])."""
    cuts = sorted((int(o), int(p)) for o, p in (breaks or []) if 0 < int(o) < len(paragraph))
    bounds = [0] + [o for o, _ in cuts] + [len(paragraph)]
    page_of = [None] + [p for _, p in cuts]
    text, marks = "", []
    for i in range(len(bounds) - 1):
        raw = paragraph[bounds[i]:bounds[i + 1]]
        piece = normalize_for_speech(raw, lang)
        if not piece:
            continue
        if text:
            # a page can break in the middle of a word (hyphenated across pages): then don't add a space
            glued = paragraph[bounds[i] - 1:bounds[i]].strip() and raw[:1].strip()
            text += "" if _no_spaces(lang) or glued else " "
        if page_of[i] is not None:
            marks.append((len(text), page_of[i]))
        text += piece
    return text, marks


def _offsets(haystack: str, needles: list[str]) -> list[int]:
    """Where each piece sits in the text it came from (they are substrings, in order)."""
    out, cursor = [], 0
    for piece in needles:
        found = haystack.find(piece, cursor)
        if found < 0:
            found = cursor
        out.append(found)
        cursor = found + len(piece)
    return out


def chunk_chapter(title: str | None, paragraphs: list[str], lang: str, limit: float, pages: list[int] | None = None,
                  breaks: list[list] | None = None) -> list[Chunk]:
    """Pack sentences into chunks of at most `limit` estimated seconds.

    A new chunk starts at a paragraph break once the current one is past 60% of the limit,
    so most chunk boundaries fall where a reader would pause anyway. Each chunk records the page
    it starts on and where inside it any further pages begin.
    """
    chunks: list[Chunk] = []
    pages = pages if pages and len(pages) == len(paragraphs) else None
    breaks = breaks if breaks and len(breaks) == len(paragraphs) else None
    if title:
        heading = normalize_heading(title, lang)
        if heading:
            chunks.append(Chunk(heading, estimate_seconds(heading, lang), PAUSE_HEADING, pages[0] if pages else None))

    joiner = "" if _no_spaces(lang) else " "
    current: list[str] = []
    marks: list[tuple[int, int]] = []
    state = {"page": None, "running": None}

    def flush(pause: float) -> None:
        if current:
            text = joiner.join(current)
            chunks.append(Chunk(text, estimate_seconds(text, lang), pause, state["page"], list(marks)))
            current.clear()
            marks.clear()
            state["page"] = state["running"] = None

    def add(piece: str, piece_page: int | None, inside: list[tuple[int, int]]) -> None:
        start = sum(len(x) for x in current) + len(joiner) * len(current)
        if not current:
            state["page"] = state["running"] = piece_page
        elif piece_page is not None and piece_page != state["running"]:
            marks.append((start, piece_page))
            state["running"] = piece_page
        for off, page in inside:
            marks.append((start + off, page))
            state["running"] = page
        current.append(piece)

    for p_index, paragraph in enumerate(paragraphs):
        if is_scene_break(paragraph):
            flush(PAUSE_SCENE)
            if chunks:
                chunks[-1].pause_after = max(chunks[-1].pause_after, PAUSE_SCENE)
            continue
        para_page = pages[p_index] if pages else None
        text, para_marks = _normalized_paragraph(paragraph, breaks[p_index] if breaks else None, lang)
        if not text:
            continue
        if current and estimate_seconds(joiner.join(current), lang) >= 0.6 * limit:
            flush(PAUSE_PARAGRAPH)
        sentences = split_sentences(text, lang)
        for sentence, s_at in zip(sentences, _offsets(text, sentences)):
            pieces = _split_long(sentence, lang, limit)
            for piece, p_at in zip(pieces, _offsets(sentence, pieces)):
                at = s_at + p_at
                page_here = next((pg for off, pg in reversed(para_marks) if off <= at), para_page)
                inside = [(off - at, pg) for off, pg in para_marks if at < off < at + len(piece)]
                if current and estimate_seconds(joiner.join(current + [piece]), lang) > limit:
                    flush(PAUSE_SENTENCE)
                add(piece, page_here, inside)
        if current and estimate_seconds(joiner.join(current), lang) >= 0.6 * limit:
            flush(PAUSE_PARAGRAPH)
    flush(PAUSE_PARAGRAPH)
    return chunks


def speech_text(text: str) -> str:
    """Text as placed inside AuK's quoted instruction: straight double quotes would end the quote early."""
    out, opening = [], True
    for ch in text:
        if ch == '"':
            out.append("“" if opening else "”")
            opening = not opening
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()
