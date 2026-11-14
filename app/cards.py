"""Turn the text of a vacancy-card PDF into one record per job card.

Each card in the ministry's PDFs is a small table: a row of labels (job title,
governorate, grade, qualification, general and precise specialty, years of
experience, preferred age) followed by a row of values, then "main tasks" and
"preferred skills". We split on the last label ("العمر المفضل"), read the value
row that follows, and pull the parts out of it with patterns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .arabic import to_logical, fold
from .classify import classify, is_computer_degree, level_of

GOVERNORATES = ["ريف دمشق", "دمشق", "حمص", "حماة", "حلب", "إدلب", "اللاذقية", "طرطوس", "القنيطرة", "درعا",
                "السويداء", "دير الزور", "الحسكة", "الرقة"]
# Letters may come out separated by stray spaces ("ر ف دمشق"), so allow spaces between every letter.
_GOV_PATTERNS = [(g, re.compile(r"\s*".join(map(re.escape, fold(g).replace(" ", ""))))) for g in GOVERNORATES]
_ALL_GOVS = re.compile(fold("جميع المحافظات|كافة المحافظات"))

_SPLIT = re.compile(r"(?:العمر|العم[ر]?)\s*المفضل(?!ة)")
_STOP = re.compile("|".join(map(re.escape, ["المسمى الوظيفي", "المهام الرئيسية", "المهارات المفضلة", "ملاحظات للمتقدم",
                                             "الشواغر الوظيفية المتاحة", "صفحة"])))
_LABELS = re.compile("|".join(map(re.escape, ["محافظة العمل", "الفئة الوظيفية", "المؤهل العلمي", "المستوى التعليمي",
                                               "الاختصاص العام", "الاختصاص الدقيق", "سنوات الخبرة", "المفضل"])))
_GRADE = re.compile(r"الفئة\s*(?:ال)?(?:أولى|اولى|أوىل|اول|ثانية|ثانة|ثالثة|رابعة|خامسة)[^\s]*")
_AGE = re.compile(r"\b([1-6]\d)\s*-\s*([1-6]\d)\b")
_SMALL_NUM = re.compile(r"(?<![\d-])\b(\d{1,2})\b(?![\d-])")
_QUAL = re.compile(r"(إجازة جامعية|إجازة جامعة|دراسات عليا|دراسات علا|ماجستير|دكتوراه|معهد متوسط|دبلوم[^-]*|شهادة ثانوية|ثانوية عامة|"
                   r"يجيد القراءة والكتابة|شهادة التعليم الأساسي)")


@dataclass
class Card:
    title: str
    governorates: list[str] = field(default_factory=list)
    grade: str = ""
    level: str = "1"
    experience: int | None = None
    age_min: int | None = None
    age_max: int | None = None
    specialty: str = ""
    category: str | None = None
    degree: str = "other"  # computer | university | institute | other
    needs_review: bool = False
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _governorates(row: str) -> tuple[list[str], int]:
    f = fold(row)
    found, first = [], len(f)
    if m := _ALL_GOVS.search(f):
        return ["جميع المحافظات"], m.start()
    taken = f
    for name, pat in _GOV_PATTERNS:  # "ريف دمشق" is tried before "دمشق"
        if m := pat.search(taken):
            found.append(name)
            first = min(first, m.start())
            taken = taken[:m.start()] + " " * (m.end() - m.start()) + taken[m.end():]
    return found, first


def parse_row(row: str) -> Card | None:
    row = re.sub(r"\s+", " ", row).strip(" -")
    if len(row) < 8:
        return None
    raw = row
    age_min = age_max = None
    if m := _AGE.search(row):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        if 15 <= a < b <= 65:
            age_min, age_max = a, b
            row = row[:m.start()] + " " + row[m.end():]
    experience = None
    for m in _SMALL_NUM.finditer(row):
        n = int(m.group(1))
        if 0 <= n <= 15:
            experience = n
            row = row[:m.start()] + " " + row[m.end():]
            break
    grade_m = _GRADE.search(row)
    grade = grade_m.group(0) if grade_m else ""
    govs, gov_pos_folded = _governorates(row)

    # The title is usually what comes before the governorate, grade or qualification.
    cut = [len(row)]
    if grade_m:
        cut.append(grade_m.start())
    if q := _QUAL.search(row):
        cut.append(q.start())
    folded = fold(row)
    title_end_folded = min([gov_pos_folded] + [len(fold(row[:c])) for c in cut])
    # Map the folded index back to the original string approximately.
    title = row
    for i in range(len(row), 0, -1):
        if len(fold(row[:i])) <= title_end_folded:
            title = row[:i]
            break
    title = _LABELS.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    if title.count("(") > title.count(")"):
        title += ")"
    title = title.strip("()") if title.startswith("(") and title.endswith(")") else title
    specialty = row[len(title):]
    needs_review = len(title) < 4 or len(title) > 80
    if needs_review:
        title = title or row[:60]
    card = Card(title=title, governorates=govs, grade=grade, level=level_of(title, grade or row),
                experience=experience, age_min=age_min, age_max=age_max, specialty=specialty.strip(),
                needs_review=needs_review, raw=raw)
    card.category = classify(card.title, card.specialty)
    if card.level in ("1", "L"):
        card.degree = "computer" if is_computer_degree(raw) else "university"
    elif card.level == "2":
        card.degree = "institute"
    if card.degree == "computer" and card.category in (None, "DE"):
        card.category = "DT"  # a computer-degree job whose title gives no field, e.g. "مهندس دعم تقني"
    return card


def parse_text(text: str) -> list[Card]:
    """Parse the full extracted text of one PDF into cards (technical and not)."""
    text = to_logical(text)
    cards = []
    chunks = _SPLIT.split(text)[1:]
    if not chunks:  # labels broken across lines: fall back to any "المفضل"
        chunks = re.split(r"المفضل(?!ة)", text)[1:]
    for chunk in chunks:
        row = _STOP.split(chunk, maxsplit=1)[0]
        if card := parse_row(row):
            cards.append(card)
    return cards


def parse_pdf(path: str) -> list[Card]:
    import pymupdf  # imported here so the rest of the package works without it

    with pymupdf.open(path) as doc:
        text = "\n".join(page.get_text("text", sort=True) for page in doc)
    return parse_text(text)
