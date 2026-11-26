"""Understand a search typed the way people talk.

«بدي شغل محاسبة بدمشق دوام جزئي بلا خبرة» → keywords «محاسبة» + filters (city دمشق, part-time, no experience).
Rules only, no AI: fast, free, and predictable. Words it doesn't recognise stay as keywords, so nothing is lost.
Each recognised piece comes back as a chip with the exact words it came from (`span`), so a client can drop a chip by
removing those words from the query and searching again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import taxonomy as tx
from .arabic import fold

# Words that carry no meaning in a job search («بدي شغل…»).
_STOP = {fold(w) for w in """
بدي بدّي بدنا أبحث ابحث عم دور دوّر ادور أدور دورلي لاقيلي لقيلي فرجيني شوفلي جبلي اريد أريد ابغى ابي بغيت
شغل شغلة شغله وظيفة وظيفه وظائف وظايف عمل فرصة فرصه فرص مطلوب مطلوبة مطلوبين شاغر شواغر
في فى ب بـ ل لـ على عن من مع او أو و يا شي اي أي كم انا أنا عندي اله إله الي إلي لي يكون تكون ضمن
""".split()}

_EDU_WORDS: list[tuple[str, str]] = [
    # (phrase, education code) — what the person HAS; they see jobs asking for this or less.
    ("بلا شهادة", "none"), ("بدون شهادة", "none"), ("ما معي شهادة", "none"), ("مامعي شهادة", "none"),
    ("تعليم اساسي", "basic"), ("شهادة تاسع", "basic"), ("تاسع", "basic"), ("اعدادي", "basic"), ("اعدادية", "basic"),
    ("بكالوريا", "secondary"), ("بكلوريا", "secondary"), ("ثانوي", "secondary"), ("ثانوية", "secondary"),
    ("شهادة ثانوية", "secondary"),
    # «معهد» and «جامعة» alone are often places («مدرس معهد»), so they need «معي/خريج/شهادة» before them.
    ("معي معهد", "institute"), ("خريج معهد", "institute"), ("خريجة معهد", "institute"), ("شهادة معهد", "institute"),
    ("دبلوم", "institute"),
    ("معي جامعة", "bachelor"), ("جامعي", "bachelor"), ("جامعية", "bachelor"), ("خريج جامعة", "bachelor"),
    ("خريجة جامعة", "bachelor"), ("اجازة جامعية", "bachelor"), ("ليسانس", "bachelor"), ("بكالوريوس", "bachelor"),
    ("ماجستير", "master"), ("ماستر", "master"), ("دكتوراه", "phd"), ("دكتوراة", "phd"),
]

_SECTOR_WORDS: list[tuple[str, str]] = [
    ("منظمة", "ngo"), ("منظمات", "ngo"), ("منظمه", "ngo"), ("ngo", "ngo"), ("انساني", "ngo"), ("عمل انساني", "ngo"),
    ("حكومي", "gov"), ("حكومية", "gov"), ("حكومة", "gov"), ("دولة", "gov"), ("قطاع عام", "gov"), ("مسابقة", "gov"),
    ("قطاع خاص", "private"), ("شركة خاصة", "private"), ("شركات خاصة", "private"),  # not «خاص» alone: «مدرس خاص»
]

_GENDER_WORDS: list[tuple[str, str]] = [
    ("بنات", "female"), ("بنت", "female"), ("نسائي", "female"), ("نساء", "female"), ("سيدات", "female"),
    ("انثى", "female"), ("اناث", "female"), ("صبية", "female"), ("صبايا", "female"),
    ("شباب", "male"), ("شب", "male"), ("رجال", "male"), ("ذكور", "male"), ("ذكر", "male"),
]

# (phrase, shift code, label)
_SHIFT_WORDS: list[tuple[str, str, str]] = [
    ("دوام جزئي", "part", "دوام جزئي"), ("جزئي", "part", "دوام جزئي"), ("بارت تايم", "part", "دوام جزئي"),
    ("part time", "part", "دوام جزئي"), ("نص دوام", "part", "دوام جزئي"), ("نصف دوام", "part", "دوام جزئي"),
    ("دوام كامل", "full", "دوام كامل"), ("فل تايم", "full", "دوام كامل"), ("full time", "full", "دوام كامل"),
    ("دوام مسائي", "evening", "دوام مسائي"), ("مسائي", "evening", "دوام مسائي"), ("مسا", "evening", "دوام مسائي"),
    ("دوام صباحي", "morning", "دوام صباحي"), ("صباحي", "morning", "دوام صباحي"),
    ("دوام ليلي", "night", "دوام ليلي"), ("ليلي", "night", "دوام ليلي"),
    ("من البيت", "remote", "من البيت / أونلاين"), ("من المنزل", "remote", "من البيت / أونلاين"),
    ("عن بعد", "remote", "من البيت / أونلاين"), ("اونلاين", "remote", "من البيت / أونلاين"),
    ("أونلاين", "remote", "من البيت / أونلاين"), ("online", "remote", "من البيت / أونلاين"),
    ("remote", "remote", "من البيت / أونلاين"),
]

_NEW_WORDS = ["اليوم", "جديد", "جديدة", "جديده", "هلق", "هلأ", "مبارح", "اخر يوم", "آخر يوم"]

_COUNTRY_WORDS: list[tuple[str, str]] = [("تركيا", "tr"), ("تركية", "tr"), ("سوريا", "sy"), ("سورية", "sy")]

_NO_EXP = ["بلا خبرة", "بدون خبرة", "ما عندي خبرة", "ماعندي خبرة", "مافي خبرة", "ما في خبرة", "بلا خبره",
           "حديث تخرج", "حديثة تخرج", "حديث التخرج", "خريج جديد", "خريجة جديدة", "اول شغل", "أول شغل", "اول وظيفة"]
_EXP_RE = re.compile(fold(r"^(?:خبرتي|عندي خبره|عندي خبرة|معي خبره|معي خبرة|خبره|خبرة)\s*(?P<n>\d+|سنه|سنة|سنتين|تلات|ثلاث|اربع|خمس|ست|سبع|عشر)"
                          r"(?:\s*(?:سنين|سنوات|سنه|سنة))?$"))
_NUM_WORDS = {fold("سنه"): 1, fold("سنة"): 1, fold("سنتين"): 2, fold("تلات"): 3, fold("ثلاث"): 3, fold("اربع"): 4,
              fold("خمس"): 5, fold("ست"): 6, fold("سبع"): 7, fold("عشر"): 10}

# One-letter Arabic proclitics people glue to a word: بدمشق، للبنات، بالمنظمات، والشباب.
_PREFIXES = ("بال", "لل", "وال", "وب", "ول", "ال", "ب", "ل", "و")


def _variants(phrase: str) -> set[str]:
    f = fold(phrase.lower())
    out = {f}
    for p in _PREFIXES:
        p = fold(p)
        if f.startswith(p) and len(f) - len(p) >= 2:
            out.add(f[len(p):])
    return out


def _table(rows):
    return {fold(r[0].lower()): r[1:] for r in rows}


_EDU = _table(_EDU_WORDS)
_SECTOR = _table(_SECTOR_WORDS)
_GENDER = _table(_GENDER_WORDS)
_SHIFT = _table(_SHIFT_WORDS)
_COUNTRY = _table(_COUNTRY_WORDS)
_NEW = {fold(w) for w in _NEW_WORDS}
_NOEXP = {fold(w) for w in _NO_EXP}


@dataclass
class Chip:
    kind: str     # city | country | edu | sector | gender | shift | exp | new
    value: str
    label: str
    span: str     # the words in the query this came from

    def as_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "label": self.label, "span": self.span}


@dataclass
class Parsed:
    text: str = ""                      # what is left to search as keywords
    chips: list[Chip] = field(default_factory=list)

    def get(self, kind: str) -> list[str]:
        return [c.value for c in self.chips if c.kind == kind]

    def first(self, kind: str) -> str | None:
        v = self.get(kind)
        return v[0] if v else None

    def as_dict(self) -> dict:
        return {"text": self.text, "chips": [c.as_dict() for c in self.chips]}


def _lookup(table: dict, phrase: str):
    for v in _variants(phrase):
        if v in table:
            return table[v]
    return None


def _exp_years(phrase: str) -> int | None:
    m = _EXP_RE.match(fold(phrase))
    if not m:
        return None
    n = m.group("n")
    return int(n) if n.isdigit() else _NUM_WORDS.get(n)


def _match(phrase: str) -> Chip | None:
    """What a 1–4 word piece of the query means, if anything."""
    f = fold(phrase.lower())
    if any(v in _NOEXP for v in _variants(phrase)):
        return Chip("exp", "none", "بلا خبرة", phrase)
    if (years := _exp_years(phrase)) is not None:
        return Chip("exp", str(min(years, 50)), f"خبرتي {years} سنين" if years > 2 else ("خبرتي سنة" if years == 1 else "خبرتي سنتين"), phrase)
    if r := _lookup(_SHIFT, phrase):
        return Chip("shift", r[0], r[1], phrase)
    if r := _lookup(_EDU, phrase):
        return Chip("edu", r[0], "شهادتي: " + tx.EDU_LABEL[r[0]], phrase)
    if r := _lookup(_SECTOR, phrase):
        return Chip("sector", r[0], tx.SECTOR_LABEL[r[0]], phrase)
    if r := _lookup(_GENDER, phrase):
        return Chip("gender", r[0], "للبنات" if r[0] == "female" else "للشباب", phrase)
    if r := _lookup(_COUNTRY, phrase):
        return Chip("country", r[0], "تركيا" if r[0] == "tr" else "سوريا", phrase)
    if any(v in _NEW for v in _variants(phrase)):
        return Chip("new", "1", "الجديدة", phrase)
    if len(f) >= 3:
        if cs := tx.cities_from_text(phrase):
            if len(cs) == 1:
                return Chip("city", cs[0], cs[0], phrase)
        if cs := tx.tr_cities_from_text(phrase):
            if len(cs) == 1:
                return Chip("city", cs[0], cs[0], phrase)
    return None


def _same(a: Chip | None, b: Chip | None) -> bool:
    return a is not None and b is not None and a.kind == b.kind and a.value == b.value


def parse(q: str) -> Parsed:
    words = [w for w in re.split(r"\s+", (q or "").strip().replace("،", " ").replace(",", " ")) if w]
    out = Parsed()
    rest: list[str] = []
    i = 0
    while i < len(words):
        found = None
        for n in (4, 3, 2, 1):
            if i + n > len(words):
                continue
            phrase = " ".join(words[i:i + n])
            chip = _match(phrase)
            if chip is None:
                continue
            # A longer piece only counts if every word is needed: «بدمشق دوام» is just the city plus a spare word.
            # (Cities are found inside text, so they need this check; the word lists match whole phrases only.)
            if chip.kind == "city" and n > 1 and (_same(chip, _match(" ".join(words[i:i + n - 1])))
                                                  or _same(chip, _match(" ".join(words[i + 1:i + n])))):
                continue
            found = (chip, n)
            break
        if found:
            chip, n = found
            if not any(_same(chip, c) for c in out.chips):
                out.chips.append(chip)
            i += n
            continue
        w = words[i]
        if not any(v in _STOP for v in _variants(w)) and fold(w) not in _STOP:
            rest.append(w)
        i += 1
    # Cities decide the country: «باسطنبول» means Turkey even if «سوريا» isn't said.
    cities = out.get("city")
    if cities and not out.first("country"):
        if all(c in tx.TR_CITIES for c in cities):
            out.chips.append(Chip("country", "tr", "تركيا", ""))
    out.text = " ".join(rest)
    return out


def exp_codes(chip_value: str | None) -> str | None:
    """«بلا خبرة» → ads asking for none; «خبرتي 3» → ads asking for up to 4 years (the API's exp buckets)."""
    if chip_value is None:
        return None
    if chip_value == "none":
        return "unk"
    n = int(chip_value)
    if n <= 2:
        return "unk,low"
    if n <= 4:
        return "unk,low,mid"
    return None  # 5+ years: every ad fits
