"""Ministry PDF reader on real files (text-layer PDFs only; the OCR ones are slow and need Tesseract)."""
import os

import pytest

from app.sources.moad_pdf import fix_lost_tails, parse_pdf

PDFS = os.path.join(os.path.dirname(__file__), "..", "data", "pdfs")


def _cards(name):
    path = os.path.join(PDFS, name + ".pdf")
    if not os.path.exists(path):
        pytest.skip("real PDF not downloaded")
    return parse_pdf(path)


def test_ligatures_and_lost_letters():
    titles = [c.title for c in _cards("hiring20261104")]
    assert "مدير مكتب إعلامي" in titles
    assert "مسؤول منصات التواصل الاجتماعي" in titles


def test_final_ya_written_on_another_line():
    titles = [c.title for c in _cards("hiring20260830")]
    assert titles[0] == "باحث في الكيمياء"
    assert "أخصائي في الهندسة النووية" in titles


def test_grade_and_governorate():
    cards = _cards("hiring20261006")
    c = next(c for c in cards if c.title == "رئيس شعبة الصيانة والدعم الفني")
    assert c.grade and c.governorates


def test_word_fixes():
    assert fix_lost_tails("الدعم الفن") == "الدعم الفني"
    assert fix_lost_tails("مدرب تأهيل طنر") == "مدرب تأهيل طبي"
