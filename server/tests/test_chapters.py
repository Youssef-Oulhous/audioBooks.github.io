import pymupdf
import pytest

from audiobook.chapters import _word_count, detect
from audiobook.pdf_extract import extract
from tests.conftest import FIXTURES, real_fixture


def run(path):
    ex = extract(str(path), "x")
    return ex, detect(ex, "zh" if "chinese" in str(path) else "en")


@pytest.mark.parametrize("name,count,first,last", [
    ("alices-adventures-in-wonderland.pdf", 12, "Chapter I. Down the Rabbit-Hole", "Chapter XII. Alice’s Evidence"),
    ("frankenstein.pdf", 28, "Letter 1", "Chapter 24"),
    ("the-great-gatsby.pdf", 9, "Chapter 1", "Chapter 9"),
])
@pytest.mark.parametrize("outline", [True, False])
def test_real_books(tmp_path, name, count, first, last, outline):
    path = real_fixture(name)
    if not outline:
        doc = pymupdf.open(path)
        doc.set_toc([])
        path = tmp_path / name
        doc.save(path)
    _, d = run(path)
    included = [c for c in d.chapters if c.include]
    assert d.method == ("outline" if outline else "headings")
    assert len(included) == count
    assert included[0].title.startswith(first)
    assert included[-1].title.startswith(last)


def test_drop_caps_are_rejoined():
    ex, d = run(real_fixture("frankenstein.pdf"))
    starts = {c.title: c.preview[:20] for c in d.chapters}
    assert starts["Chapter 1"].startswith("I am by birth")  # 'I' kept as a word
    assert starts["Chapter 2"].startswith("We were brought")  # 'W' + 'e' joined
    assert starts["Chapter 5"].startswith("It was on a dreary")
    text = " ".join(p.text for p in ex.paras)
    assert "Free eBooks at Planet eBook" not in text  # running footer removed


def test_fontsize_headings():
    _, d = run(FIXTURES / "synthetic_fontsize.pdf")
    assert d.method == "fontsize"
    assert [c.title for c in d.chapters] == ["The Storm", "Morning at the Mill", "A Letter Arrives", "The Long Road Home"]


def test_parts_contents_page_and_running_headers():
    _, d = run(FIXTURES / "synthetic_parts.pdf")
    assert [c.title for c in d.chapters if c.include] == ["Chapter 1: The Storm", "Chapter 2: Morning", "Chapter 3: The Letter", "Chapter 4: Home"]
    assert d.chapters[0].spoken_title == "Part One. Chapter 1: The Storm"
    assert all(not c.preview.upper().startswith("CHAPTER") for c in d.chapters)


def test_chinese_chapters():
    _, d = run(FIXTURES / "synthetic_chinese.pdf")
    assert [c.title for c in d.chapters] == ["第一章 回家", "第二章 河边", "第三章 远方"]


def test_no_headings_falls_back_to_sections():
    _, d = run(FIXTURES / "synthetic_plain.pdf")
    assert d.method == "sections"
    assert len(d.chapters) >= 3
    assert all(c.est_seconds <= 22 * 60 for c in d.chapters)


def test_letter_spaced_titles_are_rebuilt_not_spelled():
    from audiobook.pdf_extract import _unspace_letters

    assert _unspace_letters("S T .   M A R T I N ‘S   P R E S S") == "ST. MARTIN‘S PRESS"
    assert _unspace_letters("C H A P T E R   O N E") == "CHAPTER ONE"
    assert _unspace_letters("I am a cat and I sat.") == "I am a cat and I sat."
    assert _unspace_letters("x = 1 2 3 4") == "x = 1 2 3 4"


def test_overprinted_text_is_read_once(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    for dx in (0, 1.5, 3.5, 4.8):  # a fake-bold shadow effect: the same line drawn four times
        page.insert_text((100 + dx, 100), "Carmine Gallo", fontsize=18)
    page.insert_text((100, 200), "The body text starts here and goes on for a while.", fontsize=11)
    path = tmp_path / "shadow.pdf"
    doc.save(path)
    ex = extract(str(path), "x")
    assert " ".join(p.text for p in ex.paras).count("Carmine Gallo") == 1


def test_parts_chapters_sections_and_printed_page_numbers():
    """A novel in parts and a textbook with sections, both using the page numbers they print."""
    ex = extract(str(real_fixture("crime-and-punishment.pdf")), "x")
    d = detect(ex, "en")
    included = [c for c in d.chapters if c.include]
    assert d.method == "outline" and len(included) >= 40
    assert [c.part for c in included].count("Part I") == 7  # Part I has seven chapters
    assert {c.part for c in included} >= {"Part I", "Part VI", "Epilogue"}
    assert ex.labels[18] == "19"  # page numbers come from the page footers

    ex2 = extract(str(real_fixture("thinkpython2.pdf")), "x")
    d2 = detect(ex2, "en")
    chapter = next(c for c in d2.chapters if c.title.startswith("The way of the program"))
    assert [t for t, _ in chapter.sections][:2] == ["What is a program?", "Running Python"]
    assert ex2.labels[0] == "i" and ex2.labels[22] == "1"  # front matter in roman, then the printed numbers
    # pages that start in the middle of a paragraph are recorded, so "go to page" can be exact
    assert sum(len(b) for c in d2.chapters for b in c.para_breaks) > 20


@pytest.mark.parametrize("name,chapters,parts,sections", [
    ("crime-and-punishment.pdf", 42, 7, 0),      # a novel in parts, chapter numbering restarts in each part
    ("pride-and-prejudice.pdf", 61, 0, 0),       # chapters only
    ("thinkpython2.pdf", 22, 0, 218),            # a textbook: chapters with numbered sections
    ("war-and-peace.pdf", 355, 17, 0),           # 2,299 pages in 17 books
])
def test_full_books_match_their_own_contents(name, chapters, parts, sections):
    """Every chapter, part and section of a full book, checked against the book's own bookmarks."""
    ex = extract(str(real_fixture(name)), "x")
    d = detect(ex, "en")
    included = [c for c in d.chapters if c.include]
    assert d.method == "outline"
    assert len(included) == chapters
    assert len({c.part for c in included if c.part}) == parts
    assert sum(len(c.sections) for c in included) == sections
    # no text falls between chapters, and chapters run in reading order
    total = _word_count(" ".join(p.text for p in ex.paras))
    assert sum(c.words for c in d.chapters) > 0.99 * total   # every word lands in some chapter
    assert sum(c.words for c in included) > 0.95 * total     # and nearly all of it is narrated
    starts = [c.pages[0] for c in included]
    assert starts == sorted(starts)


@pytest.mark.parametrize("name,chapters", [
    ("crime-and-punishment.pdf", 41),   # "Chapter I" exists in every part: repeats must survive
    ("pride-and-prejudice.pdf", 61),
    ("thinkpython2.pdf", 22),           # running headers must not split chapters in two
])
def test_full_books_without_bookmarks(tmp_path, name, chapters):
    """The same books with their bookmarks removed: the structure comes from the printed headings."""
    doc = pymupdf.open(real_fixture(name))
    doc.set_toc([])
    path = tmp_path / name
    doc.save(path)
    ex = extract(str(path), "x")
    d = detect(ex, "en")
    included = [c for c in d.chapters if c.include]
    total = _word_count(" ".join(p.text for p in ex.paras))
    assert d.method == "headings" and len(included) == chapters
    assert sum(c.words for c in d.chapters) > 0.99 * total  # front matter may be skipped, but never lost
    assert sum(c.words for c in included) > 0.95 * total


def test_placeholder_pdf_titles_fall_back_to_the_filename(tmp_path):
    """Many PDFs carry a placeholder title; the file name is a better guess."""
    from audiobook.pdf_extract import _clean_meta

    for junk in ("(anonymous)", "Anonymous", "Untitled", "Microsoft Word - draft.docx", "Document1", "unknown"):
        assert _clean_meta(junk) == ""
    assert _clean_meta("  Crime and Punishment ") == "Crime and Punishment"

    doc = pymupdf.open()
    doc.new_page().insert_text((60, 80), "Chapter 1\n\nThe book begins here with enough words to count.", fontsize=11)
    doc.set_metadata({"title": "(anonymous)"})
    path = tmp_path / "Brutally_Honest_Advice.pdf"
    doc.save(path)
    assert extract(str(path), "Brutally Honest Advice").title == "Brutally Honest Advice"
