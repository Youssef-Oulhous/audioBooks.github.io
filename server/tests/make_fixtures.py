"""Synthetic PDFs covering layouts the real fixtures don't: font-size-only headings,
no headings at all, Chinese chapters, a contents page + running headers + 'Part' pages."""

from __future__ import annotations

import random
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent / "fixtures"
WORDS = ("the river ran quietly past the old mill while children laughed and the wind moved through tall "
         "grass near a small house where she waited for news from the city beyond the hills").split()


def sentences(n: int, rng: random.Random) -> str:
    out = []
    for _ in range(n):
        words = [rng.choice(WORDS) for _ in range(rng.randint(8, 18))]
        out.append(" ".join(words).capitalize() + rng.choice([".", ".", ".", "?", "!"]))
    return " ".join(out)


class Writer:
    def __init__(self, fontname: str = "helv", header: str | None = None):
        self.doc = pymupdf.open()
        self.fontname = fontname
        self.header = header
        self.page = None
        self.y = 0.0
        self.chapter_header = None

    def new_page(self):
        self.page = self.doc.new_page(width=420, height=640)
        self.y = 60
        n = self.doc.page_count
        if self.header and n > 2:
            running = self.header if n % 2 == 0 else (self.chapter_header or self.header)
            self.page.insert_text((150, 30), running, fontsize=8, fontname=self.fontname)
        if n > 1:
            self.page.insert_text((205, 620), str(n), fontsize=8, fontname=self.fontname)

    def text(self, text: str, size: float = 11, gap: float = 8):
        if self.page is None:
            self.new_page()
        while True:
            rect = pymupdf.Rect(40, self.y, 380, 590)
            height_needed = self._height(text, size)
            if self.y + height_needed <= 590:
                self.page.insert_textbox(rect, text, fontsize=size, fontname=self.fontname)
                self.y += height_needed + gap
                return
            # split the paragraph at a word boundary to fill the rest of the page
            words = text.split(" ") if self.fontname == "helv" else list(text)
            joiner = " " if self.fontname == "helv" else ""
            lo = 0
            for k in range(len(words), 0, -1):
                if self.y + self._height(joiner.join(words[:k]), size) <= 590:
                    lo = k
                    break
            if lo:
                self.page.insert_textbox(rect, joiner.join(words[:lo]), fontsize=size, fontname=self.fontname)
            text = joiner.join(words[lo:])
            self.new_page()

    def _height(self, text: str, size: float) -> float:
        width = pymupdf.get_text_length(text, fontname=self.fontname, fontsize=size)
        lines = int(width / 330) + 1
        return lines * size * 1.45 + 4

    def save(self, name: str, toc=None):
        if toc:
            self.doc.set_toc(toc)
        HERE.mkdir(exist_ok=True)
        self.doc.save(HERE / name)


def fontsize_book():
    rng = random.Random(1)
    w = Writer()
    for title in ["The Storm", "Morning at the Mill", "A Letter Arrives", "The Long Road Home"]:
        w.new_page()
        w.y = 140
        w.text(title, size=22, gap=30)
        for _ in range(8):
            w.text(sentences(6, rng))
    w.save("synthetic_fontsize.pdf")


def plain_book():
    rng = random.Random(2)
    w = Writer()
    for _ in range(420):
        w.text(sentences(5, rng))
    w.save("synthetic_plain.pdf")


def chinese_book():
    w = Writer(fontname="china-s")
    body = "那座老房子静静地立在小路的尽头，仿佛一直在耐心地等待着有人回家。风吹过田野，孩子们在河边奔跑。"
    for n in ["第一章 回家", "第二章 河边", "第三章 远方"]:
        w.new_page()
        w.y = 120
        w.text(n, size=18, gap=24)
        for _ in range(10):
            w.text(body * 3, size=11)
    w.save("synthetic_chinese.pdf")


def parts_book():
    rng = random.Random(3)
    w = Writer(header="THE MILL ON THE RIVER")
    w.new_page()
    w.text("THE MILL ON THE RIVER", size=24)
    w.text("A Novel")
    w.new_page()
    w.text("Contents", size=16)
    chapters = [("PART ONE", None), ("CHAPTER 1", "The Storm"), ("CHAPTER 2", "Morning"), ("PART TWO", None),
                ("CHAPTER 3", "The Letter"), ("CHAPTER 4", "Home")]
    for i, (a, b) in enumerate(chapters):
        w.text(f"{a}{' ' + b if b else ''} {5 + 4 * i}", gap=2)
    for a, b in chapters:
        w.chapter_header = a
        w.new_page()
        w.y = 150
        w.text(a, size=16, gap=6)
        if b:
            w.text(b, size=14, gap=24)
            for _ in range(9):
                w.text(sentences(6, rng))
    w.save("synthetic_parts.pdf")


if __name__ == "__main__":
    fontsize_book()
    plain_book()
    chinese_book()
    parts_book()
    print("fixtures written to", HERE)
