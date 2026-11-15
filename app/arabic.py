"""Helpers for the messy Arabic text that comes out of the ministry's PDF files.

The vacancy-card PDFs are exported from Excel with an Arabic font whose glyphs
often come back from text extraction in *visual* order (each line reversed),
with presentation-form characters and sometimes stray glyphs from other scripts.
"""
from __future__ import annotations

import re
import unicodedata

_ARABIC_LETTERS = re.compile(r"[ء-ي]")
_DIGIT_RUN = re.compile(r"\d+")
# Characters we keep: Arabic letters and marks, Latin + Extended Latin (Turkish İıŞşĞğÇçÖöÜü), digits, basic punctuation.
_JUNK = re.compile(r"[^\u0600-\u06FF0-9A-Za-z\u00C0-\u024F\s\-\(\)/.,:+&']")

# A few frequent words, written backwards. If they appear, the text is in visual order.
_REVERSED_MARKERS = ("ةئفلا", "لمعلا ةظفاحم", "ةيسيئرلا ماهملا", "لضفملا", "قشمد")
_LOGICAL_MARKERS = ("الفئة", "محافظة العمل", "المهام الرئيسية", "المفضل", "دمشق")


def normalize(text: str) -> str:
    """NFKC-fold presentation forms, drop junk glyphs, unify a few letters, collapse spaces."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("ـ", "")  # tatweel
    text = _JUNK.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def looks_reversed(text: str) -> bool:
    rev = sum(text.count(m) for m in _REVERSED_MARKERS)
    log = sum(text.count(m) for m in _LOGICAL_MARKERS)
    return rev > log


def unreverse_line(line: str) -> str:
    """Reverse a visual-order line into logical order, keeping each number's digits in place."""
    flipped = line[::-1]
    flipped = _DIGIT_RUN.sub(lambda m: m.group(0)[::-1], flipped)
    # Brackets swap meaning when a line is mirrored.
    return flipped.translate(str.maketrans("()", ")("))


def to_logical(text: str) -> str:
    """Return the text in logical reading order, whatever order it came in."""
    text = normalize(text)
    if looks_reversed(text):
        text = "\n".join(unreverse_line(l) for l in text.splitlines())
    return text


def fold(s: str) -> str:
    """Loose form for matching: drop the letters that extraction most often loses or swaps."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub("[إأآ]", "ا", s)
    s = s.replace("ة", "ه").replace("ى", "ي")
    s = re.sub("[يى]", "", s)  # extraction often drops ي entirely, so match without it
    return re.sub(r"\s+", " ", s).strip()


def has_arabic(s: str) -> bool:
    return bool(_ARABIC_LETTERS.search(s))


def soft_fold(s: str) -> str:
    """Loose form for matching typed text (Telegram posts): unify alef/ta marbuta/alef maqsura, keep every letter."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("ـ", "")
    s = re.sub("[\u064B-\u0652]", "", s)  # harakat and shadda
    s = re.sub("[إأآ]", "ا", s)
    s = s.replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"[ \t]+", " ", s).strip()


def search_fold(s: str) -> str:
    """Canonical form for full-text search: includes hamza-on-carrier folding and lowercasing."""
    s = soft_fold(s).lower()
    s = s.replace("ؤ", "و").replace("ئ", "ي").replace("ء", "")
    return re.sub(r"\s+", " ", s).strip()
