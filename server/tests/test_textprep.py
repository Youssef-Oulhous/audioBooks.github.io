from audiobook.textprep import (
    chunk_chapter, detect_language, estimate_seconds, normalize_english, normalize_heading, ordinal_words,
    speech_text, split_sentences, year_to_words,
)


def test_numbers_money_dates():
    text = normalize_english("Mr. Smith paid $1,250.50 on Dec. 3rd, 1984 at 10:30 — about 45% of the 2,000,000 total.")
    assert "one thousand two hundred fifty dollars and fifty cents" in text
    assert "December third, nineteen eighty-four" in text
    assert "ten thirty" in text and "forty-five percent" in text and "two million" in text


def test_ordinals_and_years():
    assert ordinal_words(21) == "twenty-first"
    assert ordinal_words(12) == "twelfth"
    assert year_to_words(1905) == "nineteen oh five"
    assert year_to_words(2007) == "two thousand seven"
    assert year_to_words(1900) == "nineteen hundred"


def test_headings_read_aloud():
    assert normalize_heading("CHAPTER XII", "en") == "Chapter twelve."
    assert normalize_heading("IV", "en") == "Four."
    assert normalize_heading("Mix", "en") == "Mix."  # not a Roman numeral in a title
    assert normalize_heading("第三章 回家", "zh") == "第三章 回家。"


def test_sentences_respect_abbreviations():
    parts = split_sentences("Mr. Smith met Dr. J. R. R. Tolkien. They talked! Did it rain? Yes.", "en")
    assert parts == ["Mr. Smith met Dr. J. R. R. Tolkien.", "They talked!", "Did it rain?", "Yes."]
    assert split_sentences("他回家了。天黑了！你好吗？", "zh") == ["他回家了。", "天黑了！", "你好吗？"]


def test_estimates_follow_auk_calibration():
    # AuK pe.config.yaml: 0.0656 s per UTF-8 byte for English, short texts slowed to 0.3x
    assert abs(estimate_seconds("It was a bright cold day in April.", "en") - 34 * 0.0656) < 1e-6
    assert abs(estimate_seconds("Yes.", "en") - 4 * 0.0656 / 0.3) < 1e-6


def test_chunks_stay_under_limit_and_keep_all_words():
    paragraphs = ["This is a sentence that goes on for a while, with commas, and more words. " * 6, "* * *", "Short one."]
    chunks = chunk_chapter("Chapter 1", paragraphs, "en", limit=12.0)
    assert chunks[0].text == "Chapter one."
    assert all(c.est_seconds <= 12.0 + 1e-6 for c in chunks)
    assert max(c.pause_after for c in chunks) >= 1.5  # scene break
    words_in = sum(len(p.split()) for p in paragraphs if p != "* * *")
    assert sum(len(c.text.split()) for c in chunks[1:]) == words_in


def test_quotes_are_made_safe_for_the_instruction():
    assert speech_text('He said "hi".') == "He said “hi”."


def test_language_detection():
    assert detect_language("The cat sat on the mat and it was happy with the day.") == "en"
    assert detect_language("Le chat est sur la table et il mange une souris dans la maison.") == "fr"
    assert detect_language("他们在房子里等着有人回家。") == "zh"
