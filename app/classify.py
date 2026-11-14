"""Decide whether a job title is technical, and which field it belongs to.

Rules are keyword lists, tried in order. They are matched on a folded form of the
text (see arabic.fold) so they survive letters that PDF extraction drops.
Tune them by running `python -m app.evaluate` against the hand-labelled seed data.
"""
from __future__ import annotations

import re

from .arabic import fold

CATEGORIES = {
    "DE": "إدخال بيانات",
    "CYBER": "أمن سيبراني",
    "AI": "ذكاء اصطناعي",
    "SWE": "تطوير برمجيات",
    "DATA": "بيانات وتحليل",
    "NET": "شبكات وأنظمة",
    "DT": "تحول رقمي",
    "SUPPORT": "دعم فني",
    "EDU": "تدريس وتدريب",
}

# (category, patterns on the title). First match wins, so order matters.
_RULES: list[tuple[str, list[str]]] = [
    ("DE", [r"مدخل[ه]? بانات", r"ادخال بانات", r"مدقق بانات"]),
    ("CYBER", [r"سبران", r"سربان", r"\bSOC\b", r"اختراق", r"penetration", r"امن المعلومات", r"امن معلومات",
               r"الامن الرقم", r"امن رقم", r"جنائ", r"forensic", r"malware", r"خبثه", r"threat", r"\bGRC\b",
               r"incident", r"الاستجابه للحوادث", r"امن بئات", r"معمار امن", r"امن شبكات", r"مهندس امن"]),
    ("AI", [r"ذكاء صنع", r"ذكاء اصطناع", r"ذاء صن", r"تعلم ال", r"machine learning", r"\bAI\b", r"لغات طبعه",
            r"معالجه الصور", r"رؤه حاسوب"]),
    ("NET", [r"شبكات", r"شبكه", r"نظم وشغل", r"انظمه وشغل", r"نظم شغل", r"انظمه وتشغل", r"نظم وتشغل", r"نظم تشغ",
             r"مخدمات", r"مراكز بانات", r"عملات ونظم", r"حواسب", r"انظمه حاسوب", r"نظم حاسوب", r"التشغل",
             r"النظم والمعلومات", r"اتصالات\)", r"\(اتصالات"]),
    ("SWE", [r"برمج", r"مطور", r"front", r"back", r"full", r"flutter", r"devops", r"\bQA\b", r"جوده تقن",
             r"تطبقات", r"مواقع", r"\bERP\b", r"انظمه رقمه", r"تطور انظمه", r"محلل نظم", r"محلل انظمه", r"CTO"]),
    ("DATA", [r"بانات", r"احصاء", r"احصائ", r"\bGIS\b", r"نظم معلومات جغراف", r"قواعد", r"نظم معلومات اداره",
              r"اداره المعلومات", r"اداره معلومات", r"نمذجه"]),
    ("DT", [r"تحول رق", r"التحول الرق", r"المعلوماته", r"التقانه", r"تقانه المعلومات", r"منصات", r"منصه", r"نفاذ", r"تقانه",
            r"تقنه المعلومات"]),
    ("SUPPORT", [r"صانه", r"صيانه", r"دعم فن", r"الدعم الفن", r"دعم تق", r"مخبر.*حاسوب", r"مخبر الحاسوب"]),
    ("EDU", [r"مدرس.*معلومات", r"مدرب", r"موجه اختصاص"]),
]
_COMPILED = [(cat, [re.compile(fold(p), re.I) for p in pats]) for cat, pats in _RULES]

# Degree / specialty words that make a borderline title count as technical.
_TECH_SPECIALTY = [re.compile(fold(p), re.I) for p in [
    r"معلوماتيه", r"معلوماته", r"حاسوب", r"حواسيب", r"تكنولوجيا المعلومات", r"تكنلوجا معلومات", r"برمجيات",
    r"ذكاء", r"شبكات", r"علوم البيانات", r"امن المعلومات", r"احصاء وبرمجه", r"نظم معلومات"]]

# Titles that look technical by keyword but are not (power engineering, telecom spectrum, admin).
_NOT_TECH = [re.compile(fold(p), re.I) for p in [r"كهرباء", r"القدره الكهربائ", r"قدره الهائ", r"طف ترددي", r"ترددي"]]


_COMPUTER_DEGREE = [re.compile(fold(p), re.I) for p in [
    r"هندسه معلومات", r"هندسة المعلومات", r"هندسه الحاسوب", r"هندسه حاسوب", r"هندسه الحواسب", r"حواسب واتمته",
    r"تكنولوج[ا]? المعلومات", r"تكنلوج[ا]? معلومات", r"هندسه الذكاء", r"هندسه الذ", r"علوم الحاسوب", r"برمجات ونظم",
    r"نظم وشبكات", r"امن المعلومات", r"علوم البانات", r"computer", r"informatics"]]


def is_computer_degree(text: str) -> bool:
    """True if the text names computer engineering or a close field as an accepted degree."""
    t = fold(text)
    return any(p.search(t) for p in _COMPUTER_DEGREE)


def classify(title: str, specialty: str = "") -> str | None:
    """Return a category code for a technical job, or None if it isn't one."""
    t = fold(title)
    spec = fold(specialty)
    if any(p.search(t) for p in _NOT_TECH) and not any(p.search(t) for p in _TECH_SPECIALTY):
        return None
    for cat, pats in _COMPILED:
        if any(p.search(t) for p in pats):
            # Generic words (e.g. "مهندس دعم تقني") only count when the degree is technical.
            if cat in ("SUPPORT", "DT", "DATA") and spec and not any(p.search(spec) for p in _TECH_SPECIALTY):
                if cat == "DATA" and re.search(fold("محلل|تحلل|معالج|اداره"), t):
                    return cat
                if cat != "DATA":
                    return None
            return cat
    return None


def level_of(title: str, grade_text: str) -> str:
    """'1' first category (university), '2' second (institute), 'L' leadership post, else the grade digit."""
    if re.match(fold(r"^(مدير|رئس|رئيس|معاون مدير)"), fold(title)):
        return "L"
    g = fold(grade_text)
    for word, code in (("الاول", "1"), ("الاو", "1"), ("الثان", "2"), ("الثالث", "3"), ("الرابع", "4"), ("الخامس", "5")):
        if fold("الفئه " + word) in g or fold("الفئة " + word) in g:
            return code
    return "1"
