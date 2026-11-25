"""The shared vocabulary: education levels, cities, fields of work, and how to read them from Arabic text."""
from __future__ import annotations

import re
import unicodedata

from .arabic import soft_fold as fold  # posts are typed text: keep every letter

# ---------------------------------------------------------------- education
# Ordered from least to most. A person qualifies for a job when their rank is at least the job's.
EDUCATION = [
    ("none", "بدون شهادة"),
    ("basic", "تعليم أساسي"),
    ("secondary", "بكالوريا"),
    ("institute", "معهد"),
    ("bachelor", "جامعة"),
    ("master", "ماجستير"),
    ("phd", "دكتوراه"),
]
EDU_RANK = {code: i for i, (code, _) in enumerate(EDUCATION)}
EDU_LABEL = dict(EDUCATION)

# The ministry's job grades map to education levels.
MOAD_GRADE_EDU = {"1": "bachelor", "L": "bachelor", "2": "institute", "3": "secondary", "4": "basic", "5": "none"}

# Phrases in an ad that state the education asked for, most demanding first.
_EDU_PHRASES = [
    ("phd", [r"دكتوراه", r"دكتوراة"]),
    ("master", [r"ماجستير", r"ماجستر", r"دراسات عليا"]),
    ("bachelor", [r"اجازه جامع", r"اجازه في", r"شهاده جامع", r"خريج[هة]? جامع", r"خريج[هة]? كليه", r"بكالوريوس", r"\bجامعي[هة]?\b",
                  r"حمله الاجازه", r"خريج[هة]? (هندسه|اداره|اقتصاد|حقوق|اداب|تجاره|صيدله|طب|كليه)", r"درجه البكالوريوس",
                  r"شهاده (في|ب)?\s*(الهندسه|الاقتصاد|الحقوق|الصيدله|الطب|اداره الاعمال|المحاسبه|علم)", r"bachelor"]),
    ("institute", [r"\bمعهد (تقاني|متوسط|صحي|تمريض|فني|تجاري|هندسي|مهني)", r"خريج[هة]? معهد", r"شهاده معهد", r"\bدبلوم\b", r"معهد او (جامعه|جامعيه)", r"(جامعه|جامعيه) او معهد", r"[-–(]\s*معهد\s*\)", r"او معهد"]),
    ("secondary", [r"بكالوريا", r"ثانويه", r"شهاده ثانوي"]),
    ("basic", [r"تعليم اساس", r"اعداديه", r"شهاده التعليم الاساس"]),
    ("none", [r"بدون شهاده", r"لا يشترط شهاده", r"بدون خبره وبدون شهاده", r"يجيد القراءه والكتابه", r"لا يشترط الشهاده", r"بدون شهادات"]),
]
_EDU_RE = [(code, re.compile("|".join(fold(p) for p in pats))) for code, pats in _EDU_PHRASES]

_CRAFT_TAIL = r"(?:تلييس|لياسه|بلوك|مكابس|جبصين|دهان|بلاط|سيراميك|حداده|نجاره|المنيوم|بيتون|باطون|عمار|بناء|صحيه|تمديدات|كهرباء|ميكانيك|لحام|خراطه|خياطه|حبكه|درزه|كوي|فرن|مخبز|معجنات|فطائر|عجين|شاورما|فلافل|بيتزا|مشاوي|حلويات|سلطات|عصائر|بسطرما|قصاب|لحم|مشغل|ورشه)"

# What a school teacher teaches. «معلم» followed by anything else is a craft master (معلم سناك، معلم كنافة، معلم صباغة).
_TEACH_SUBJECT = (r"(?:صف|ماده|مواد|لغه|اللغه|رياضيات|فيزياء|كيمياء|علوم|احياء|انكليزي|انجليزي|فرنسي|عربي|حلقه|قران|تربيه|"
                  r"موسيقا|موسيقي|رسم|حاسوب|معلوماتيه|اطفال|روضه|مدرسه|اجتماعيات|تاريخ|جغرافيا|ديانه|ابتدائي|تعليم|صفوف|"
                  r"مرحله|اساسي|ثانوي|خصوصي|دعم|تقويه)")

# When an ad does not say, the job title is a good hint. (pattern, minimum education)
_TITLE_EDU = [
    (rf"طبيب|اطباء|اخصائي|أخصائي|صيدلان|صيدلي|مهندس(?! مساعد)|محام|محاسب قانون|مدير|مستشار|مدرس|\bمعلم[هة]\b|معلمات|\bمعلم(?:ين|ي|و)?\s+{_TEACH_SUBJECT}|\bمعلمين$|دكتور|\bانسات لمدرسه|"
     r"developer|engineer|مطور|محلل|مسؤول(?!\s*(?:تنظيف|نظافه|ضيافه|مطبخ|صاله|صالة|تغليف))|\bmeal\b|منسق|ضابط|باحث|مترجم|قابله|معالج|مشرف|رئيس (قسم|شعبه|دائره)|مختص|"
     r"محرر|موارد بشريه|\bcto\b|officer|manager|architect|accountant|analyst|ميسر|اداره حاله|عامل حاله|معد تقارير|خريج|مراسلات", "bachelor"),
    (r"ممرض|فني|تقني|مساعد[هة]? (مهندس|صيدل|سني|طبيب|جراح|فني|تمريض)|مخبري|قابله", "institute"),
    (r"محاسب|سكرتير|سكرتاريا|مدخل[هة]? بيانات|data\s*entry|امين (مستودع|صندوق|مكتبه)|مصمم|مونتير|مصور|كول سنتر|خدمه عملاء|"
     r"استقبال|كاشير|مساعد|موظف[هة]? (اداري|مكتب|تسجيل|توزيع|توعيه)|كاتب|مراقب ميداني", "secondary"),
    (rf"عامل|سائق|حارس|مستخدم|ناطور|عتال|توصيل|دليفري|ديلفري|طباخ|مساعد شيف|شيف|نادل|جرسون|كوفي|باريستا|خياط|"
     r"ورشه|معمل|مبيعات|بائع|موظف[هة]? (مبيعات|صاله|محل)|تنظيف|نظافه|مربي[هة]?|جليس|مندوب|مستخدم|مروج|"
     rf"مسؤول[هة]? (تنظيف|نظافه|ضيافه)|ترويج|مقدم رعايه|مطعم|كافي|كوفي|\bمحل\b|\bمول\b|ماركت|(?:معلم|معلمين|معلمي|معلمو)\s+{_CRAFT_TAIL}|\bمعلم(?:ين|ي|و)?\s+(?!{_TEACH_SUBJECT}\b|و?معلم|قر[اآ]ن)(?=\S)|سناك|صاج|بروستد|كريب|كنافه|بوظه|كواء|اخصائي[هة]? (?:بشره|تجميل|ليزر|مساج|شعر|اظافر|مكياج)|\bكوا\b|مكوجي|\bكوي\b|حلاق|كوافير|صباغ|تلييس|لياسه|مكابس|بلوك|باريستا|كومي|بيوت بلاستيك|مغسل|كابتن|كباتن|ماكينه|حبكه|مشغل|فرن|مخبز|حلويات|صانع|نجار|حداد|دهان|بلاط|لحام", "none"),
]
_TITLE_EDU_RE = [(re.compile(fold(p), re.I), edu) for p, edu in _TITLE_EDU]


def education_from_text(text: str) -> str | None:
    """The lowest education level an ad clearly accepts, or None if it doesn't say."""
    t = fold(text)
    found = [code for code, rx in _EDU_RE if rx.search(t)]
    if not found:
        return None
    # "بكالوريا" must not be mistaken for "بكالوريوس" (university): handled by separate patterns above.
    return min(found, key=lambda c: EDU_RANK[c])


def _first_match(text: str, rules):
    """The label whose pattern matches earliest in the text (longest match on a tie): the job is what's named first."""
    best = None
    for rx, label in rules:
        if m := rx.search(text):
            key = (m.start(), -(m.end() - m.start()))
            if best is None or key < best[0]:
                best = (key, label)
    return best[1] if best else None


def _title_form(s: str) -> str:
    """'مدخل/ة بيانات' → 'مدخل بيانات', for matching titles."""
    return fold(re.sub(r"\s*/\s*ـ?ة|\(ة\)|\s*/\s*ه\b", "", s))


def education_from_title(title: str) -> str | None:
    return _first_match(_title_form(title), _TITLE_EDU_RE)


def guess_education(title: str, text: str = "", default: str | None = None) -> tuple[str | None, str]:
    """Education for one ad and how we know it: (code, "stated" | "title" | "source" | "unknown").

    A craft or service title («عامل», «معلم شاورما», «كوّاء») wins over a degree word somewhere in the body, because
    the body is often a company blurb. When nothing is said, return `default` (only pass one for sources where it
    is really true, e.g. UN/NGO portals), otherwise None: the site shows «الشهادة مو مذكورة» instead of a guess.
    """
    title_edu = education_from_title(title)
    if title_edu == "none":
        return "none", "title"
    if stated := education_from_text(f"{title} {text}" if text else title):
        return stated, "stated"
    if title_edu:
        return title_edu, "title"
    return (default, "source") if default else (None, "unknown")


# ---------------------------------------------------------------- cities
CITIES = ["دمشق", "ريف دمشق", "حلب", "حمص", "حماة", "اللاذقية", "طرطوس", "إدلب", "درعا", "السويداء", "القنيطرة",
          "دير الزور", "الحسكة", "الرقة"]
_CITY_ALIASES = {
    "دمشق": ["دمشق", "الشام", "جرمانا", "صحنايا", "قدسيا", "damascus"],
    "ريف دمشق": [
        "ريف دمشق", "دوما", "حرستا", "مدينه التل", "منطقه التل", "ضاحيه التل", "في التل", "بالتل",
        "الكسوه", "يبرود", "النبك", "قطنا", "عدرا", "داريا", "الزبداني", "صيدنايا", "عربين", "المعضميه",
        "زملكا", "عين ترما", "كفربطنا", "سقبا", "حزة", "جسرين", "ببيلا", "يلدا", "بيت سحم",
        "الهامة", "معضمية الشام", "أشرفية صحنايا", "اشرفية صحنايا", "جديدة عرطوز", "عرطوز",
        "السيدة زينب", "السبينة", "القطيفة", "دير عطية", "قارة", "الرحيبة", "الضمير", "rural damascus",
    ],
    "حلب": ["حلب", "اعزاز", "منبج", "الباب", "عفرين", "جبل سمعان", "جرابلس", "الاتارب", "مارع", "داره عزه", "دارةعزة", "السفيره", "كوباني", "عين العرب", "قباسين", "أخترين", "اخترين", "جنديرس", "صوران", "تل رفعت", "aleppo"],
    "حمص": ["حمص", "تدمر", "القصير", "تلكلخ", "الرستن", "تلبيسه", "homs"],
    "حماة": ["حماه", "سلميه", "مصياف", "محرده", "السقيلبيه", "hama", "hamah"],
    "اللاذقية": ["اللاذقيه", "لاذقيه", "جبله", "القرداحه", "الحفه", "كسب", "latakia", "lattakia", "jableh"],
    "طرطوس": ["طرطوس", "بانياس", "صافيتا", "الدريكيش", "الشيخ بدر", "مشتى الحلو", "القدموس", "tartous", "tartus", "banias"],
    "إدلب": ["ادلب", "معره النعمان", "سرمدا", "الدانا", "حارم", "جسر الشغور", "اريحا", "سراقب", "اطمه", "سلقين", "كفرنبل", "بنش", "معره مصرين", "معرشورين", "idlib", "idleb", "sarmada"],
    "درعا": ["درعا", "ازرع", "الصنمين", "بصرى", "نوى", "طفس", "جاسم", "الحراك", "daraa", "deraa"],
    "السويداء": ["السويداء", "شهبا البلد", "شهبا", "صلخد", "sweida", "suwayda"],
    "القنيطرة": ["القنيطره", "خان ارنبه", "quneitra"],
    "دير الزور": ["دير الزور", "ديرالزور", "الميادين", "البوكمال", "هجين", "deir ez-zor", "deir ezzor", "deir alzor", "deir al-zor", "hajeen"],
    "الحسكة": ["الحسكه", "القامشلي", "قامشلي", "قامشلو", "راس العين", "المالكيه", "عامودا", "الدرباسيه", "hasakah", "hasakeh", "qamishli", "al qamishli"],
    "الرقة": ["الرقه", "الطبقه", "تل ابيض", "raqqa", "tabqa"],
}
# Tier 2 fallback: unambiguous city neighborhoods checked ONLY when Tier 1 returns no governorate/district.
_NEIGHBORHOOD_ALIASES = {
    "دمشق": [
        "المزه", "البرامكه", "القيمريه", "باب توما", "المالكي", "دمر", "مشروع دمر",
        "المزرعه", "القصاع", "باب شرقي", "باب مصلى", "برزه", "ركن الدين", "كفرسوسه", "ابو رمانه",
        "الشعلان", "المهاجرين", "منطقه التضامن", "حي التضامن", "ساحه المحافظه", "المجتهد",
    ],
    "حلب": [
        "الفرقان", "الحمدانيه", "سيف الدوله", "الجميليه", "السليمانيه", "العزيزيه",
        "الموكامبو", "حلب الجديده", "حي صلاح الدين", "السريان", "الليرمون", "الشيخ نجار",
        "جمعيه الزهراء",
    ],
}
_AR_PROCLITIC = r"(?:[بلو](?=(?:ريف|دمشق|حلب|حمص|حماه|ادلب|درعا|طرطوس|اللاذقيه|اللاذقية|دوما|حرستا|داريا|زملكا|سقبا|عرطوز|عربين|قدسيا|جرمانا|صحنايا|معظميه|قطنا|كسوه|الكسوه|النبك|يبرود|زبداني|كفربطنا|يلدا|ببيلا|عفرين|اعزاز|منبج|سرمدا|دانا|الدانا|سلقين|حارم|سلميه|مصياف|تلكلخ|القصير|بانياس|صافيتا|الدريكيش|قامشلي|قامشلو|البوكمال|الميادين|الطبقه|السفيره|اسطنبول|إسطنبول|الفاتح|شيشلي|تقسيم|اكسراي|مرسين|بورصه|بورصة|انقره|أنقرة|غازي|عنتاب|انطاليا|أنطاليا|ازمير|إزمير|انطاكيا|أنطاكيا|قونيا|اضنه|أضنة|قيصري|طرابزون|باشاك|اسنيورت|زيتون|بيليك|بهتشه|اورتاكوي|بشكتاش|كاديكوي|اسكودار|باغجلار|عمرانيه)))?"
_CITY_RE = [(city, re.compile(r"(?<![\w])(?:" + _AR_PROCLITIC + r"|#)(" + "|".join(re.escape(fold(a)).replace(r"\ ", r"[\s_]*") for a in aliases) + r")(?![\w])", re.I))
            for city, aliases in _CITY_ALIASES.items()]
_NEIGHBORHOOD_RE = [
    (city, re.compile(r"(?<![\w])(?:[بلو]|#)?(" + "|".join(re.escape(fold(a)).replace(r"\ ", r"[\s_]*") for a in aliases) + r")(?![\w])", re.I))
    for city, aliases in _NEIGHBORHOOD_ALIASES.items()
]
_ALL_CITIES = re.compile(fold(r"جميع المحافظات|كافه المحافظات|عده محافظات|عده من المحافظات|محافظات مختلفه|كل المحافظات"))
_FALSE_CITY_CONTEXT = re.compile(
    fold(
        r"(?:عمال|عامل|معلم|معلمو|معلمي|معلمين|خلط|خلاطه|صب|كيس|اكياس)\s+جبله|"
        r"جبله\s+(?:باطون|بيتون|اسمنت|طين)|"
        r"(?:جامعه|بنك|مؤسسه|شركه|مجموعه|تلفزيون|اذاعه|مستشفى|مشفى|فندق|مطعم|معهد|مدارس|مدرسه|كليه|فرع|تكافل|درع|اهل|أهل|شباب|نسور|سواعد|ابطال|أبطال|نور|عطر|سوريا|صقور|جمعي[ةه]|منظم[ةه])\s+الشام(?:\s+الخاصه|\s+الخيري[ةه]|\s+الانساني[ةه])?|"
        r"شام\s+(?:كاش|اف ام|fm)|"
        r"(?:فتح|غلق|اغلاق|عند|امام|وراء|خلف|عبر|من|الى|لباب|ابواب)\s+الباب|"
        r"الباب\s+(?:المفتوح|الرئيسي|الخلفي|الامامي|الاول|الثاني|الشرقي|الغربي)|"
        r"فول\s+و?حمص|حمص\s+و?(?:فول|مسبحه|فلافل|فت[ةه])|معلم\s+حمص|محل\s+حمص|فت[ةه]\s+حمص|"
        r"(?:طريق|اوتوستراد|اتستراد|مفرق|كراج)\s+(?:دمشق|حلب|حمص|حماه|درعا|ادلب|اللاذقيه|طرطوس)(?:\s*[-–]?\s*(?:دمشق|حلب|حمص|حماه|درعا|ادلب|اللاذقيه|طرطوس))?"
    ),
    re.I,
)
_BUSRA_SHAM_RE = re.compile(fold(r"\bبصري\s+الشام\b"), re.I)
_MOADAMIYA_SHAM_RE = re.compile(fold(r"\bمعضمي[ةه]\s+الشام\b"), re.I)
_EXPLICIT_LOC_RE = re.compile(
    r"(?:^|\n)[^\w\n]{0,6}(?:مكان\s+العمل|موقع\s+العمل|محافظ[ةه]\s+العمل|منطق[ةه]\s+العمل|المدين[ةه]|المحافظ[ةه]|العنوان|الموقع|"
    r"work\s*location|duty\s*station|location)\s*[:：\-–]?\s*(?:\n\s*)?([^\n]{2,140})",
    re.I,
)
_LOC_SPILL_RE = re.compile(
    r"(?:عدد\s*الشواغر|الفئ[ةه]\s*الوظيفي[ةه]|المستو[ىي]\s*التعليم[يى]|المسم[ىي]|تاريخ|آخر\s*موعد|اخر\s*موعد|المؤهل|الشروط|المهام|للتقديم|للتواصل)"
)


_HASHTAG_SPAM_LINE = re.compile(r"(?m)^(?:[^#\n]*#){5,}[^\n]*$")


def _strip_hashtag_spam_lines(text: str) -> str:
    return "\n".join(ln for ln in (text or "").splitlines() if ln.count("#") < 5)


def cities_from_text(text: str) -> list[str]:
    raw_t = text or ""
    stripped_hashtags = _strip_hashtag_spam_lines(raw_t)
    cleaned_raw = re.sub(r"(?<![ء-ي])(?:ال)?بصر(?:ي|ية)(?![ء-ي])", " ", stripped_hashtags if stripped_hashtags.strip() else raw_t)
    t = _FALSE_CITY_CONTEXT.sub(" ", fold(cleaned_raw))
    t = _BUSRA_SHAM_RE.sub(fold("بصري"), t)
    t = _MOADAMIYA_SHAM_RE.sub(fold("المعضميه"), t)
    if _ALL_CITIES.search(t):
        return ["عدة محافظات"]
    found = []
    rif = fold("ريف دمشق")
    for city, rx in _CITY_RE:
        # "ريف دمشق" contains "دمشق": look for Damascus only in what is left after removing it.
        hay = t.replace(rif, " ") if city == "دمشق" else t
        if rx.search(hay):
            found.append(city)
    if found:
        return found
    for city, rx in _NEIGHBORHOOD_RE:
        if rx.search(t):
            found.append(city)
    return found


def extract_explicit_location_cities(text: str, country: str = "sy") -> list[str]:
    """Extract canonical cities from explicit 'مكان العمل:' / 'Location:' lines before body heuristics."""
    raw_t = text or ""
    out: list[str] = []
    generic = "عدة ولايات في تركيا" if country == "tr" else "عدة محافظات"
    for m in _EXPLICIT_LOC_RE.finditer(raw_t):
        val = _LOC_SPILL_RE.split(m.group(1), maxsplit=1)[0].strip(" .،,-–:|")
        if not val:
            continue
        cs = tr_cities_from_text(val) if country == "tr" else cities_from_text(val)
        for c in cs:
            if c not in out:
                out.append(c)
    specific = [c for c in out if c != generic]
    return specific or out


def city_label(cities: list[str]) -> str:
    if not cities:
        return "غير محدد"
    return cities[0] if len(cities) == 1 else "عدة محافظات"


TR_CITIES = [
    "إسطنبول", "غازي عنتاب", "مرسين", "أنقرة", "بورصة", "هاتاي (أنطاكيا)",
    "إزمير", "قونيا", "شانلي أورفا", "أضنة", "قيصري", "كليس",
    "طرابزون", "أنطاليا", "قوجه إيلي", "ساكاريا", "سامسون",
]
_TR_FOLD = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "ğ": "g", "Ğ": "g",
    "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
    "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


def _fold_tr(s: str) -> str:
    return fold((s or "").translate(_TR_FOLD)).lower()


_TR_CITY_ALIASES = {
    "إسطنبول": [
        "اسطنبول", "إسطنبول", "اسطنبول الاوروبية", "اسطنبول الاسيوية", "istanbul",
        "basaksehir", "باشاك شهير", "باشاكشهير", "بشاك شهير", "esenyurt", "اسنيورت", "إسنيورت", "اسينيورت",
        "bahcesehir", "بهتشه شهير", "بهشة شهير", "بهجه شهير",
        "sisli", "شيشلي", "fatih", "الفاتح", "zeytinburnu", "زيتون بورنو",
        "beylikduzu", "بيليك دوزو", "بيلك دوزو", "بيليكدوزو", "merter", "مرتر", "مارتر",
        "avcilar", "أفجلار", "افجلار", "bagcilar", "باغجلار", "yenibosna", "ينيبوسنا", "يني بوسنا", "ينيبوسنه",
        "sirinevler", "شيرين إيفلر", "شيرين ايفلر", "شيرينيفلر", "شيرين ايفر", "شيرين أيفر",
        "kayasehir", "كيا شهير", "كاياشهير", "كايا شهير",
        "taksim", "تقسيم", "aksaray", "أكسراي", "اكسراي", "اكساراي", "uskudar", "أوسكودار", "اسكودار",
        "kadikoy", "كاديكوي", "قاضي كوي", "umraniye", "عمرانية", "pendik", "بنديك", "istoc", "إستوش", "استوش",
        "ortakoy", "أورتاكوي", "اورتاكوي", "besiktas", "بشكتاش",
        "mecidiyekoy", "مجيدية كوي", "maslak", "مسلك", "maltepe", "مال تبه", "مالتبه",
        "topkapi", "توب كابي", "توبكابي", "sultanahmet", "السلطان أحمد", "سلطان أحمد", "سلطان احمد",
        "laleli", "لالالي", "لاللي", "بيازيد", "بايزيد",
        "arnavutkoy", "ارناؤوط كوي", "kucukcekmece", "كوتشوك تشكمجه", "bahcelievler", "بهجلي ايفلر",
        "sultangazi", "سلطان غازي", "bayrampasa", "بايرام باشا", "kagithane", "كاغت هانه",
        "eyup", "eyupsultan", "ايوب سلطان", "سلطان ايوب", "findikzade", "فندق زادة", "فندق زاده",
        "gaziosmanpasa", "غازي عثمان باشا",
        "sariyer", "ساريير", "beykoz", "بيكوز", "tuzla", "توزلا", "kartal", "كارتال",
        "gungoren", "غونغورين", "غونغران", "esenler", "إسنلر", "اتاشهير", "atasehir", "bakirkoy", "بكركوي", "باكيركوي",
        "tahtakale", "تحت القلعة",
    ],
    "غازي عنتاب": [
        "غازي عنتاب", "عنتاب", "gaziantep", "antep", "baspinar", "باشبينار", "منطقة باشبينار",
        "sahinbey", "شاهين بيه", "sehitkamil", "شهيد كامل", "nizip", "نزيب",
    ],
    "مرسين": ["مرسين", "mersin", "mezitli", "مزيتلي", "tarsus", "طرسوس", "yenisehir mersin"],
    "أنقرة": ["أنقرة", "انقرة", "ankara", "cankaya", "جانكايا", "kizilay", "كزلاي", "yenimahalle"],
    "بورصة": ["بورصة", "بورصه", "bursa", "nilufer", "نيلوفر", "osmangazi", "عثمان غازي", "inegol", "إينيغول", "اينيغول"],
    "هاتاي (أنطاكيا)": [
        "هاتاي", "خطاي", "أنطاكيا", "انطاكيا", "الريحانية", "ريحانية", "اسكندرون", "إسكندرون",
        "كركخان", "hatay", "antakya", "reyhanli", "iskenderun", "kirikhan",
    ],
    "إزمير": ["إزمير", "ازمير", "izmir", "bornova", "konak"],
    "قونيا": ["قونيا", "konya", "selcuklu"],
    "شانلي أورفا": ["شانلي أورفا", "شانلي اورفا", "أورفا", "اورفا", "sanliurfa", "urfa"],
    "أضنة": ["أضنة", "اضنة", "أضنه", "اضنه", "adana", "seyhan"],
    "قيصري": ["قيصري", "kayseri"],
    "كليس": ["كليس", "كيليس", "كلس", "kilis"],
    "طرابزون": ["طرابزون", "trabzon"],
    "أنطاليا": ["أنطاليا", "انطاليا", "antalya", "alanya", "ألانيا"],
    "قوجه إيلي": ["قوجه إيلي", "قوجالي", "كوجالي", "كوجه ايلي", "إزميت", "ازميت", "kocaeli", "izmit", "gebze", "غبزة"],
    "ساكاريا": ["ساكاريا", "سكاريا", "صقاريا", "آدابازاري", "sakarya", "adapazari"],
    "سامسون": ["سامسون", "samsun"],
}
_TR_CITY_RE = [
    (city, re.compile(r"(?<![\w])(?:" + _AR_PROCLITIC + r"|#)(" + "|".join(re.escape(_fold_tr(a)).replace(r"\ ", r"[\s_-]*") for a in aliases) + r")(?![\w])", re.I))
    for city, aliases in _TR_CITY_ALIASES.items()
]
_ALL_TR_CITIES = re.compile(fold(r"جميع الولايات|كافه الولايات|كل الولايات|عده ولايات|عدة مدن تركية|farkli sehirler"), re.I)

_FOREIGN_EMAIL_TLD = re.compile(r"@[^@\s]+\.(?:eg|sa|ae|kw|qa|bh|om|jo|lb|iq|ye|ma|tn|dz|ly|sd)\b", re.I)
_FOREIGN_URL_RE = re.compile(r"https?://(?:[^/\s]+\.)?(?:cibeg\.taleo\.net|[^/\s]+\.gov\.eg|wuzzuf\.net|forasna\.com|bayt\.com|tanqeeb\.com)\b", re.I)
_FOREIGN_COUNTRY_RE = re.compile(
    r"(?<![ء-ي])(?:القاهرة|الإسكندرية|الاسكندرية|الجيزة|6\s*أكتوبر|التجمع\s+الخامس|شبرا|المنصورة|"
    r"جمهورية\s+مصر|في\s+مصر|السعودية|الرياض|جدة|الدمام|الإمارات|دبي|أبوظبي|الكويت|الدوحة|قطر|عمان\s*-\s*الأردن)(?![ء-ي])",
    re.I,
)
_FOREIGN_EN_ONSITE_RE = re.compile(
    r"(?:location\s*[:：][^\n]{0,40}\b(?:cairo|giza|egypt|riyadh|jeddah|dubai|abu\s+dhabi|doha|qatar|kuwait|amman|jordan|beirut|lebanon|baghdad|erbil|iraq)\b|\bcairo\s*,\s*egypt\b)",
    re.I,
)


def is_foreign_non_target(text: str = "", apply_url: str | None = None, email: str | None = None, has_local_city: bool = False) -> bool:
    """Return True for clearly non-Syria/non-Turkey listings (e.g. Egypt CIB/Intersport-EG, Gulf)."""
    if email and _FOREIGN_EMAIL_TLD.search(email):
        return True
    if apply_url and _FOREIGN_URL_RE.search(apply_url):
        return True
    if not has_local_city and text:
        norm_text = unicodedata.normalize("NFKC", text)
        if _FOREIGN_COUNTRY_RE.search(norm_text):
            return True
        if _FOREIGN_EN_ONSITE_RE.search(norm_text):
            is_remote = bool(re.search(r"\bremote\b|عن\s*بعد|أونلاين|اونلاين|من\s*المنزل", norm_text, re.I))
            is_onsite = bool(re.search(r"\bon-?site\b|حضوري", norm_text, re.I))
            if is_onsite or not is_remote:
                return True
    return False


def tr_cities_from_text(text: str) -> list[str]:
    """Detect canonical Turkish provinces from Arabic, English, or Turkish text/districts."""
    raw_t = text or ""
    stripped = _strip_hashtag_spam_lines(raw_t)
    t = _fold_tr(stripped if stripped.strip() else raw_t)
    if _ALL_TR_CITIES.search(t):
        return ["عدة ولايات في تركيا"]
    found: list[str] = []
    for city, rx in _TR_CITY_RE:
        if rx.search(t):
            found.append(city)
    return found


# ---------------------------------------------------------------- fields of work
FIELDS = [
    ("computer", "هندسة حاسوب ومعلوماتية"),
    ("engineering", "هندسة (مدني، معماري، كهرباء، ميكانيك)"),
    ("health", "طب وصحة وصيدلة"),
    ("business", "محاسبة وإدارة ومالية"),
    ("sales", "مبيعات وتسويق وخدمة عملاء"),
    ("education", "تعليم وتدريب"),
    ("media", "إعلام وتصميم ومونتاج"),
    ("law", "حقوق وقانون"),
    ("ngo", "منظمات وعمل إنساني"),
    ("hospitality", "مطاعم وكافيهات وفنادق"),
    ("retail", "محلات وصالات بيع"),
    ("industry", "معامل وورش وصناعة"),
    ("driving", "سائقين وتوصيل"),
    ("security", "حراسة وأمن"),
    ("cleaning", "نظافة وخدمات"),
    ("care", "رعاية منزلية وأطفال"),
    ("translation", "ترجمة ولغات"),
    ("other", "غير ذلك"),
]
FIELD_LABEL = dict(FIELDS)
_FIELD_WORDS = [
    ("computer", r"مهندس (برمجيات|شبكات|معلوماتيه|حاسوب|اتصالات|ذكاء|بيانات|امن)|دعم (تقني|فني)|\bcto\b|مدير تقني|صيانه (شبكات|حواسيب|كمبيوتر|موبايل)|معلوماتيه|حاسوب|برمج|مطور|developer|software|شبكات|سيبران|ذكاء (صنع|اصطناع)|بيانات رقم|مدخل[هة]? بيانات|ادخال بيانات|محلل\s+بيانات|تحليل\s+ال?بيانات|data\s*entry|devops|backend|"
                 r"front|full.?stack|flutter|\bit\b|\bdata\s*analyst\b|\bdata\s*scien(?:tist|ce)\b|\bdata\s*engineer\b|\bmachine\s+learning\b|\bsoftware\s+engineer\b|\bit\s+(?:officer|manager|specialist|support|assistant)\b|\binformation\s+management\b|\bprogrammer\b|\bcyber\b|\bweb\s+developer\b|\bqa\s+engineer\b|تقانه|تقنيه المعلومات|تحول رقم|linux|سيرفر"),
    ("health", r"طبيب|ممرض|صيدل|مخبر|مخابر|اشعه|قابله|تمريض|عياده|مشفى|مستشفي|مستشفى|اسنان|\bسني\b|تغذيه|اسعاف|ليزر|"
               r"علاج فيزيائ|معالج|تخدير|تعقيم|بصريات|نظارات|جراح|تصوير طبقي|رنين|طبي\b|صحي\b|اطراف صناعيه|غسيل كلي|"
               r"صح[ةه]\s+مجتمعي[ةه]|صرف\s+ادوي[ةه]|ادوي[ةه]|وحد[ةه]\s+العناي[ةه]|عناي[ةه]\s+(?:مركزه|مشدده)|"
               r"\b(?:doctor|physician|pediatrician|nurse|nursing|midwife|pharmacist|medical(?:\s+advisor)?|public\s+health|health(?:\s+officer)?|nutrition|clinic|medic|paramedic|dentist|physiotherapist|conseiller\s+m[ée]dical|trauma\s+and\s+healing)\b"),
    ("engineering", r"\bمهندس|مهندس (مدني|معماري|ميكانيك|كهرب|تنفيذ|اشراف|مساح|طبوغراف|ديكور)|مهندس[هة]?$|هندسه (مدنيه|معماريه|ميكانيك)|"
                    r"اوتوكاد|autocad|مساح|الكترون|إلكترون|طاق[ةه]\s+شمسي[ةه]|تبريد\s+وتكييف|مساعد\s+تقني|"
                    r"\b(?:civil|mechanical|electrical|architect|architectural|structural|site|environmental|hse|hvac|agronomist|procurement|field)\s+engineer\b|\btechnical\s+(?:assistant|officer)\b|\barchitect\b|\bagronomist\b"),
    ("business", r"سلسله (التوريد|الامداد)|محاسب|مالي|اداري|سكرتير|سكرتاريا|موارد بشريه|مشتريات|مستودع|امين صندوق|تدقيق|مكتب|لوجست|سلسله الامداد|"
                 r"توظيف|شؤون الموظفين|تامينات|عمليات|قروض|تمويل\s+صغير|(?:مساعد|مدير|منسق|مشرف)\s+(?:مشروع|مشاريع)|مشرف\s+عمليات|مدير\s+(?:ال)?برامج|منظم\s+معاملات|(?:قسم|مدير)\s+التخطيط|"
                 r"\b(?:accountant|finance|financial|audit|auditor|hr|human\s+resources|admin|administration|administrative|procurement|supply\s+chain|logistics|operations?(?:\s+(?:officer|assistant|manager))?|contracts|employee\s+relations|budget|loan\s+officer|credit\s+officer|fleet\s+(?:officer|assistant|manager|coordinator)|p&c|people\s+and\s+culture|recruitment|talent\s+acquisition|project\s+(?:assistant|officer|manager|coordinator)|programs?\s+manager|board\s+member)\b"),
    ("translation", r"مترجم|ترجمه|translator|interpreter"),
    ("sales", r"مبيعات|مندوب|تسويق|ترويج|مروج|خدمه (ال)?(عملاء|زبائن)|كول سنتر|call center|كاشير|استقبال|تذاكر|سياحه(?:\s+علاجيه)?|مستشار\s+(?:طبي|عقاري)|وسيط\s+عقاري|استشارات عقاريه|عقارات|سيلز|تواصل\s+هاتفي|رد\s+على\s+(?:الرسائل|الصفحات)|ادار[ةه]\s+صفحات|في\s+مجال\s+البيع|تطوير\s+اعمال|علاقات\s+(?:عملاء|عام[ةه])|مراسلات\s+تجاري[ةه]|استيراد\s+وتصدير|وكال[ةه]\s+سفر|\b(?:sales|marketing|customer\s+service|business\s+development|accounts?\s+manager|real\s+estate|media\s+buyer|seo|ihracat\s+sorumlusu)\b"),
    ("education", rf"مدرس|\bمعلم[هة]\b|معلمات|\bمعلم(?:ين|ي|و)?\s+{_TEACH_SUBJECT}|\bمعلمين$|مدرب|تعليم|روضه|حضانه|مربي|تدريس|شريعه|ميسر[ةه]?\s+(?:لغ[ةه]|تعليم|اطفال|صف)|\b(?:teacher|trainer|facilitator|educator|instructor|tutor|professor|tvet|education\s+consultant)\b"),
    ("media", r"انتاج (و ?مونتاج|فيديو|اعلامي|مرئي)|اعلام|مصمم|تصميم|مونتير|مونتاج|مصور|محتوي|محتوى|سوشال|سوشيال|غراف|graphic|محرر|منصات التواصل|اخبار|\b(?:graphic\s+designer|video\s+editor|photographer|copywriter|content\s+creator|social\s+media|communications?|journalist|ui/ux)\b"),
    ("law", r"محام|قانون|حقوق|\b(?:lawyer|legal|attorney|compliance)\b"),
    ("ngo", r"متابعه وتقييم|مراقبه وتقييم|رصد والتقييم|\bmeal\b|\bm&e\b|حمايه\s+(?:الطفل|اجتماعيه|من العنف|انسانيه)|(?:رصد|برنامج|فريق|مسؤول|مشرف|منسق|مختص)\s+(?:ال)?حماي[ةه]|حمايه\s+ميداني|"
            r"ادار[ةه]\s+(?:ال)?حال[ةه]|مدير\s+حال[ةه]|عامل\s+حال[ةه]|اخصائ[يى]\s+حال[ةه]|case\s*(?:management|worker|manager|officer)|دعم نفسي|\bpss\b|\bmhps\b|مسؤول تسجيل|موظف\s+تسجيل|"
            r"(?:عامل|موظف|منسق|ميسر|مثقف)[ةه]?\s+توعي[ةه]|منسق\s+(?:المنح|الشراكات|ميداني|حماي[ةه])|شراكات|اتصال\s+مجتمعي|اعمال\s+انساني[ةه]|الغام|تماسك\s+مجتمعي|"
            r"سبل العيش|\blivelihoods?\b|النوع الاجتماعي|\bgbv\b|المساعدات الانسانيه|الامن الغذائي|\bfsl\b|\bfsal\b|المياه والاصحاح|فريق\s+المياه|اعاد[ةه]\s+التاهيل|\bwash\b|\bshelter\b|المأوي|ماوي|الاصحاح|ازاله الالغام|الذخائر|mine\s*action|explosive\s*ordnance|humanitarian|advocacy|مناصره|جامع بيانات|\benumerator\b|استجابه طارئه|العمل الانساني|منسق\s+(?:برنامج|برامج|مشاريع)\s+(?:انساني|اغاث)|"
            r"\bfood\s+(?:and\s+economic\s+)?security\b|\beconomic\s+opportunities\b|\bgrants?\s+(?:officer|coordinator|manager)\b|\bgrants?\b|\bpartnerships?\b|\bresource\s+mobilis|\bemergency\s+coordinator\b|\bcountry\s+directore?\b|\bhead\s+of\s+(?:base|support\s+services|mission|programme)\b|\bprograms?\s+(?:senior\s+|junior\s+)?(?:officer|manager|coordinator|assistant)\b|\bprogramme?\s+(?:coordinator|assistant|officer|manager|management)\b|\btechnical\s+coordinator\b|\bnts\s+surveyor\b|\bfield\s+(?:\([^)]+\)\s*)?(?:officer|coordinator|assistant)\b|\bcommunity\s+(?:mobilizer|outreach|liaison)\b|\bcamp\s+management\b|\bcccm\b|\beore\b|\bcash\s+(?:assistant|officer)\b|\bsocial\s+cohesion\b|\bliaison\s+manager\b|\bconflict\s+sensitivity\b|\bprevention\s+of\s+.{0,25}sexual\b"),
    ("hospitality", r"مطعم|كافيه|كافتيريا|كوفي|شيف|طباخ|نادل|جرسون|غرسون|كابتن\s*صاله|باريستا|فلافل|فول\s+وحمص|بوفيه|فندق|حلويات|مشاوي|شاورما|سلطات|عصائر|عصير|مطبخ|كومي|سناك|كفتيريا|كافيتريا|مسبحه|سكيب|ساندويش|سندويش|برغر|همبرغر|بيتزا|معجنات|فطائر|كباب|شوايه|مناقيش|مقهي|مقهى|\b(?:steward|waiter|kitchen|chef|cook|barista|hospitality)\b|(?:معلم|معلمين|معلمي|معلمو|اسطى) (شاورما|فلافل|بيتزا|مشاوي|حلويات|سلطات|معجنات|فطائر|عصائر)"),
    ("retail", r"(?<![ء-يa-z0-9])(?:محل|محلات)(?![ء-يa-z0-9])|صاله|البسه|ألبسه|سوبر\s*ماركت|ماركت|دكان|بائع[هة]?|سوبر|\bمول\b|البان|اجبان|بقاليه|معرض|بوتيك|مكياج|عطور|مبيعات داخل"),
    ("industry", r"معمل|ورشه|خياط|حبكه|درز[هة]|اورطجي|امبلاج|\bكوي\b|قصاص|موديليست|\bاوفر\b|تطريز|رشمه|سنجر|ماكين|انتاج|تصنيع|تلييس|لياسه|جبصين|مكابس|بلوك|بيتون|باطون|سيراميك|بلاط|نجار|حداد|دهان|كهرباء|كهربائي|تمديدات|ميكانيكي|لحام|المنيوم|"
                 r"صيانه|فني\s+تركيب|فني\s+(?:صيان[ةه]|انتاج|ماكينات|تشغيل|مكافح[ةه])|ميكانيك\s+مصاعد|معلم\s+كهربا\b|صانع\s+(?:صحي[ةه]|جاكيت)|عامل\s+مولد[ةه]|عمال\s+(?:تركيب|قطاف)|موبيليا|كوافير|حلاق|نسيج|جوارب|مصنع|صناعه|منشره|مشغل الات|مشغل|فرن|مخبز|سكاكر|مشرف(?:ين|ي)?\s+عمال|تعبئ[ةه]\s+وتغليف|معلم\s+(?:تنجيد|بخ)|تغيير\s+زيت|تنخيل|\bmaintenance\s+(?:agent|technician|worker)\b|(?:معلم|معلمين|معلمي|معلمو) (تلييس|لياسه|بلوك|مكابس|جبصين|دهان|بلاط|سيراميك|حداده|نجاره|المنيوم|بيتون|باطون|عمار|بناء|صحيه)"),
    ("driving", r"سائق|شوفير|توصيل|دليفري|ديلفري|ديليفري|كابتن|كباتن|موزع|درايفر|\b(?:driver|chauffeur|delivery)\b"),
    ("security", r"حارس|ناطور|امن وحمايه|حراسه|(?<!food\s)(?<!economic\s)(?<!social\s)(?<!cyber\s)(?<!information\s)(?<!data\s)\bsecurity\s+(?:guard|officer|manager|coordinator|assistant|specialist|advisor|focal\s+point)\b|\bguard\b|\bsafety\s+officer\b"),
    ("cleaning", r"تنظيف|نظافه|نضافه|مستخدم|عامل[هة]? خدم|\b(?:cleaner|janitor|housekeeping)\b"),
    ("care", r"رعايه|جليس|مسن|مربي[هة]? اطفال|مشرف[ةه]?\s+اطفال|ست\s+كبير[ةه]\s+بالعمر|خادمه|عامله منزل|مدبره منزل"),
]
_FIELD_RE = [(f, re.compile(fold(p), re.I)) for f, p in _FIELD_WORDS]
_LANDMARK_NOISE_RE = re.compile(
    r"(?:خلف|قرب|مقابل|بجانب|بجوار|امام|أمام|قريب\s+من)\s+(?:مستشفى|مشفى|جامعة|مدرسة|معهد|صيدلية|فندق|مطعم|معمل)\s+\S+(?:\s+\S+)?|مشروع\s+دمر",
    re.I,
)


def field_from_text(title: str, context: str = "") -> str:
    """The main field of a job, from its title first and then the cleaned context of the ad."""
    if f := _first_match(_title_form(title), [(rx, f) for f, rx in _FIELD_RE]):
        return f
    cleaned_ctx = _LANDMARK_NOISE_RE.sub(" ", context or "")
    if f := _first_match(_title_form(cleaned_ctx), [(rx, f) for f, rx in _FIELD_RE]):
        return f
    return "other"


# ---------------------------------------------------------------- other details
_PHONE = re.compile(r"(?<!\d)(?:\+?963\s?|0)?(9\d{2})[\s-]?(\d{3})[\s-]?(\d{3})(?!\d)")
_TR_PHONE = re.compile(
    r"(?<!\d)(?:(?:\+|00?)?90|0)\s*[-./(]*?(5\d{2})[\s.\-)/]*?(\d{3})[\s.\-]*?(\d{2})[\s.\-]*?(\d{2})(?!\d)"
    r"|(?:wa\.me/|phone=(?:%2B|\+)?)(?:90|0090)(5\d{2})(\d{3})(\d{2})(\d{2})(?!\d)",
    re.I,
)
_TR_BAD_PHONES = {"05551234567", "05321234567", "05000000000", "05559999999"}
_RTL_CONTROL_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]+")
_URL_TRIM_CHARS = ".,،؛;:!()[]{}<>«»\"' \t\r\n"
_URL = re.compile(
    r"(?:https?://[^\s<>\"'\]]+|(?<![a-z0-9./-])bit\.ly/(?:applying|hiring)[A-Za-z0-9_-]+)",
    re.I,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_WHATSAPP = re.compile(fold(r"واتس|whatsapp|وتس"), re.I)
_AGE = re.compile(r"(?:عمر|العمر|السن|بعمر|اعمار)[^\d]{0,15}(\d{2})\s*(?:-|الى|حتى|و|لـ|ل)\s*(\d{2})|(?:تحت|اقل من|دون)\s*(\d{2})")
_EXPERIENCE = re.compile(r"خبر[ةه][^\d]{0,15}(\d{1,2})\s*(?:سن|سنوات|عام|أعوام)|(\d{1,2})\s*(?:سنوات|سنه|سنة|عام)\s*خبر")
_SALARY = re.compile(
    r"(?:راتب|الراتب|الاجر|الأجر|أجر|salary)[^\n]{0,45}?"
    r"((?:\d[\d,.]*(?:\s*[-–إلىو]\s*\d[\d,.]*)?\s*(?:الف|ألف|مليون|TL|TRY|₺|\$|USD|دولار|يورو|€|ل\.س|ليرة(?:\s+(?:تركية|سورية))?)"
    r"|(?:\d{1,3}(?:[.,]\d{3})+|\d{4,6})(?:\s*[-–إلىو]\s*(?:\d{1,3}(?:[.,]\d{3})+|\d{4,6}))?)"
    r"(?!\s*(?:يوم|ايام|أيام|ساعة|ساعات|عام|عمر|سنة|سنوات))[^\n]{0,18})",
    re.I,
)
_TR_SALARY_INLINE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+(?:\.\d{2})?(?:\s*[-–إلى]\s*\d{1,3}(?:[.,]\d{3})+(?:\.\d{2})?)?)\s*(TL|TRY|₺|ليرة(?:\s+تركية)?|دولار|USD|\$)(?![A-Za-z0-9_])"
    r"|(?<!\d)(\d{1,3}\s*(?:الف|ألف)\s*(?:ليرة(?:\s+تركية)?|ليرة))"
    r"|(?<!\d)(\d{4,6}(?:\s*[-–إلى]\s*\d{4,6})?)\s*(?:TL|TRY|₺|ليرة(?:\s+تركية)?)(?![A-Za-z0-9_])"
    r"|(?<!\d)(\d{3,5}(?:\s*[-–إلى]\s*\d{3,5})?)\s*(?:\$|USD|دولار)(?![A-Za-z0-9_])",
    re.I,
)
_FEMALE = re.compile(fold(r"\bاناث\b|\b(صبيه|صبايا|فتاه|فتيات|بنت|بنات|بنتين|انسه|انسات|سيده|سيدات|امراه|نساء|موظفه|موظفات|عامله|عاملات|سكرتيره|"
                          r"مندوبه|مروجه|مدخله|ممرضه|مساعده|معلمه|مدرسه|مدرسات|معلمات|محاسبه|صيدلانيه|مربيه|اخصائيه|مستخدمه|"
                          r"قابله|كوميكيه|مضيفه|بائعه|كاشيره)\b|\(اناث\)|اناث فقط|للاناث|للسيدات|الاخوات"))
_MALE = re.compile(fold(r"\b(شاب|شب|شباب|رجال|ذكور)\b|ذكور فقط|للذكور"))
_BOTH = re.compile(r"/\s*ة|/ة|\(ة\)|/\s*موظفة|ذكر\s*/\s*أنثى|ذكر\s*/\s*انثى|ذكور واناث|شباب وصبايا|شاب او صبيه|للجنسين")
_SHIFT = re.compile(fold(r"(دوام|وردي[هة]|شفت)[^\n.،]{0,40}|(صباحي|مسائي|ليلي|جزئي|كامل)"))
_HOURS = re.compile(
    r"(?:من|الساعه|الساعة)?\s*(\d{1,2})(?::\d{2})?\s*(?:صباحا|صباحاً|ص|ظهرا|ظهراً|عصرا|عصراً|مساء|مساءً|م)?\s*"
    r"(?:-|الى|إلى|حتى|حتي|لل|ل)\s*(?:الساعه|الساعة)?\s*(\d{1,2})(?::\d{2})?"
)


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def phones(text: str) -> list[str]:
    norm = (text or "").translate(_ARABIC_DIGITS)
    return sorted({"0" + "".join(m.groups()) for m in _PHONE.finditer(norm)})


def phones_tr(text: str) -> list[str]:
    """Extract normalized Turkish 05XXXXXXXXX mobile numbers, filtering placeholders."""
    norm = (text or "").translate(_ARABIC_DIGITS)
    out: set[str] = set()
    for m in _TR_PHONE.finditer(norm):
        digits = "".join(g for g in m.groups() if g)
        if len(digits) == 10 and digits.startswith("5"):
            p = "0" + digits
            if p not in _TR_BAD_PHONES and len(set(p[2:])) > 2:
                out.add(p)
    return sorted(out)


def urls(text: str) -> list[str]:
    clean_text = _RTL_CONTROL_RE.sub("", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for raw_u in _URL.findall(clean_text):
        u = _RTL_CONTROL_RE.sub("", raw_u).strip(_URL_TRIM_CHARS)
        if not u:
            continue
        if not u.lower().startswith(("http://", "https://")):
            u = f"https://{u}"
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def emails(text: str) -> list[str]:
    return _EMAIL.findall(text)


def whatsapp_only(text: str) -> bool:
    return bool(_WHATSAPP.search(fold(text)))


def age_range(text: str) -> tuple[int | None, int | None]:
    m = _AGE.search(text)
    if not m:
        return None, None
    if m.group(3):
        return None, int(m.group(3))
    a, b = sorted((int(m.group(1)), int(m.group(2))))
    return (a, b) if 14 <= a < b <= 70 else (None, None)


_EXP_WORDS = re.compile(r"خبر[ةه][^\n\d]{0,25}?(سنه واحده|سنة واحدة|عام واحد|سنتين|سنتان|عامين|عامان|ثلاث|ثلاثة|اربع|أربع|خمس|ست|سبع|عشر)")
_WORD_NUM = {"سنه واحده": 1, "سنة واحدة": 1, "عام واحد": 1, "سنتين": 2, "سنتان": 2, "عامين": 2, "عامان": 2, "ثلاث": 3, "ثلاثة": 3,
             "اربع": 4, "أربع": 4, "خمس": 5, "ست": 6, "سبع": 7, "عشر": 10}


def experience_years(text: str) -> int | None:
    m = _EXPERIENCE.search(text)
    w = _EXP_WORDS.search(text)
    if w and (not m or w.start() < m.start()):
        return _WORD_NUM[w.group(1)]
    if m:
        n = int(m.group(1) or m.group(2))
        return n if n <= 20 else None
    if re.search(fold(r"بدون خبره|لا يشترط (وجود )?خبره|حديث التخرج|حديثي التخرج|خبره غير ضروريه|لا تشترط الخبره"), fold(text)):
        return 0
    return None


def salary(text: str) -> str | None:
    norm = (text or "").translate(_ARABIC_DIGITS)
    if m := _SALARY.search(norm):
        val = re.sub(r"(\d)\.00\b", r"\1", m.group(1).strip(" :.-،"))
        if val.endswith(")") and "(" not in val:
            val = val[:-1].strip()
        if re.search(r"\d", val):
            return val[:60]
    if m2 := _TR_SALARY_INLINE.search(norm):
        val = re.sub(r"\s+", " ", m2.group(0)).strip(" :.-،")
        if val.endswith(")") and "(" not in val:
            val = val[:-1].strip()
        val = re.sub(r"(\d)\.00\b", r"\1", val)
        return val[:60]
    return None


def gender(text: str) -> str | None:
    text = re.sub(r"(\S+)\s*/\s*(\S+)", lambda m: m.group(0) if m.group(2).startswith("ة") else m.group(0), text)
    if _BOTH.search(text) or _BOTH.search(fold(text)):
        return None
    t = fold(text)
    f, m = bool(_FEMALE.search(t)), bool(_MALE.search(t))
    if f and not m:
        return "female"
    if m and not f:
        return "male"
    return None


def shift(text: str) -> str | None:
    t = fold(text)
    parts = []
    for word, label in (("صباح", "صباحي"), ("مسائ", "مسائي"), ("ليل", "ليلي"), ("جزئ", "دوام جزئي"), ("كامل", "دوام كامل")):
        if re.search(fold("(دوام|ورديه|شفت|فتره)[^\n]{0,20}") + word, t) or re.search(word + "[^\n]{0,3}" + fold("(او|و)"), t):
            parts.append(label)
    if m := _HOURS.search(text):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 < a <= 24 and 0 < b <= 24 and a != b:
            parts.append(f"من {a} لـ {b}")
    return "، ".join(dict.fromkeys(parts)) or None


_REMOTE_RE = re.compile(fold(r"عن بعد|عن بُعد|اونلاين|من المنزل|من البيت|\bremote\b"), re.I)


def job_tags(text: str) -> list[str]:
    t = fold(text)
    return ["عن بعد"] if _REMOTE_RE.search(t) else []


# ---------------------------------------------------------------- sectors & apply methods
SECTORS = [
    ("gov", "قطاع حكومي"),
    ("ngo", "منظمات"),
    ("private", "قطاع خاص"),
]
SECTOR_LABEL = dict(SECTORS)

APPLY_TYPES = [
    ("whatsapp", "واتساب"),
    ("form", "فورم تقديم"),
    ("site", "موقع إلكتروني"),
    ("email", "إيميل"),
    ("call", "اتصال"),
]
APPLY_LABEL = dict(APPLY_TYPES)

_NGO_RE = re.compile(fold(
    r"منظم[ةه]|جمعيه|الهلال الاحمر|الصليب الاحمر|الامم المتحده|الاونروا|برنامج الاغذيه|"
    r"\b(unicef|unhcr|wfp|undp|unfpa|iom|who|ocha|msf|irc|care|oxfam|relief|ngo|ingos?)\b|"
    r"world\s*vision|solidarit|avsi|\bdrc\b|\bnrc\b|\bacted\b|\bmedglobal\b|\bsams\b|\bsarc\b|\bsema\b|\bsrd\b|\bsened\b|\bbahar\b|\bataa\b|\bida\b|"
    r"takaful|hand\.ngo|rescue\.org|gopa|adra|caritas|people\s*in\s*need|islamic\s*relief|human\s*appeal|qatar\s*charity|"
    r"terre\s*des\s*hommes|action\s*against\s*hunger|welthungerhilfe|relief\s+international|premiere\s+urgence|secours\s+islamique|muslim\s+aid|"
    r"انقاذ الطفل|save the children|mercy corps|منظمه شفق|جمعيه شفق|شفق للاغاثه|منظمه بنفسج|جمعيه بنفسج|فريق بنفسج|منظمه احسان|جمعيه احسان|احسان للاغاثه|منظمه بهار|جمعيه بهار|"
    r"الاطباء المستقلين|عمل انساني|مؤسسه خيريه|القطاع الانساني|منظمات دوليه|"
    r"الهيئه العالميه للاغاثه|مؤسسه نور للاغاثه|مؤسسه سلام للامل|مؤسسه seed|منظمه الجوده|"
    r"للاغاثه والتنميه|ازاله الالغام|للاغاثه الانسانيه|المجلس الدنماركي|المجلس النرويجي|"
    r"اللجنه الدوليه للصليب|منظمه الصحه العالميه|منظمه العمل الدوليه|منظمه الهجره الدوليه|"
    r"منظمه الاغذيه|برنامج الامم المتحده|المفوضيه الساميه|"
    r"الهيئه العامه للمنظمات|سوريون بلا حدود|جمعية عطاء|"
    r"international\s+medical\s+corps|\bimc\b|هيئه\s+الاغاثه|معا\s+نبني\s+الوطن|givedirectly|medair|icmpd|\bfao\b|المجلس\s+النرويجي\s+للاجئين|"
    r"كاريتاس|ميرسي\s*كور|سيف\s*ذا\s*تشيلدرن|أطباء\s*بلا\s*حدود|اطباء\s*بلا\s*حدود|أطباء\s*العالم|اطباء\s*العالم|أوكسفام|اوكسفام|"
    r"ميدير|وورلد\s*فيجن|هانديكاب|تير\s*دي\s*زوم|انترسوس|إنترسوس|العمل\s*ضد\s*الجوع|ميد\s*غلوبال|الشرق\s*الأدنى|إسعاف\s*أولي|اسعاف\s*اولي|"
    r"قطر\s*الخيري[ةه]|الإغاثة\s*الإسلامية|الاغاثة\s*الاسلامية"
), re.I)
_NON_NGO_PHRASE_RE = re.compile(fold(r"جمعيه\s+(?:الزهراء|سكنيه)"), re.I)
_PRIVATE_COMMERCIAL_EMP_RE = re.compile(fold(
    r"^(?:مطعم|كافيه|كافتيريا|محل|بوتيك|سوبر\s*ماركت|صالون|ورشه|معمل\s+(?:البسه|اكياس|شيبس|حلويات|خياطه|نسيج|بلاستيك|احذيه|منظفات)|"
    r"شركه\s+(?:تجاريه|مقاولات|تجاره|سياحه|توزيع|عقار|المانيا|قائمه|قابضه|محدوده|مستحضرات|تسويق|شحن|غذا|خاصه))|للتجاره|للمقاولات|للصناعات|القابضه|لتسويق|للمواد\s+الغذائيه"
), re.I)
_FORM_RE = re.compile(
    r"docs\.google\.com/forms|forms\.gle|forms\.office\.com|forms\.cloud\.microsoft|"
    r"ee\.humanitarianresponse\.info|kobotoolbox|typeform\.com|jotform\.com|airtable\.com|tally\.so|surveymonkey",
    re.I,
)


def infer_sector(
    official: bool,
    field: str,
    employer: str | None,
    text: str = "",
    hint: str | None = None,
    country: str = "sy",
    source: str | None = None,
) -> str:
    from . import config

    emp_f = fold(employer or "")
    txt_f = fold((text or "")[:700])
    emp_clean = _NON_NGO_PHRASE_RE.sub(" ", emp_f)
    txt_clean = _NON_NGO_PHRASE_RE.sub(" ", txt_f)
    is_priv_emp = bool(_PRIVATE_COMMERCIAL_EMP_RE.search(emp_f))
    if country == "tr":
        if source in config.OFFICIAL_NGO_SOURCES or _NGO_RE.search(emp_clean):
            return "ngo"
        if hint == "ngo" and not is_priv_emp:
            return "ngo"
        if (field == "ngo" or _NGO_RE.search(txt_clean)) and not is_priv_emp:
            return "ngo"
        return "private"
    if source in config.OFFICIAL_NGO_SOURCES or _NGO_RE.search(emp_clean):
        return "ngo"
    if (official or source in config.OFFICIAL_GOV_SOURCES) and not is_priv_emp:
        return "gov"
    if hint == "gov" and not is_priv_emp:
        return "gov"
    if hint == "ngo":
        return "private" if (is_priv_emp and not _NGO_RE.search(emp_clean)) else "ngo"
    if hint == "private":
        if not is_priv_emp and (field == "ngo" or _NGO_RE.search(emp_clean) or _NGO_RE.search(txt_clean)):
            return "ngo"
        return "private"
    if (field == "ngo" or _NGO_RE.search(txt_clean)) and not is_priv_emp:
        return "ngo"
    return "private"


def infer_apply_types(apply_url: str | None, phones: list[str], whatsapp: bool, email: str | None) -> list[str]:
    out = []
    u = apply_url or ""
    has_mobile = any(p.startswith(("09", "05")) for p in phones)
    if whatsapp or has_mobile or "wa.me/" in u.lower():
        out.append("whatsapp")
    if u and _FORM_RE.search(u):
        out.append("form")
    elif u and "wa.me/" not in u.lower():
        out.append("site")
    if email:
        out.append("email")
    if phones and not whatsapp:
        out.append("call")
    return out
