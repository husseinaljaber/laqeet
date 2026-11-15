"""The shared vocabulary: education levels, cities, fields of work, and how to read them from Arabic text."""
from __future__ import annotations

import re

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

# When an ad does not say, the job title is a good hint. (pattern, minimum education)
_TITLE_EDU = [
    (rf"طبيب|اطباء|اخصائي|أخصائي|صيدلان|صيدلي|مهندس(?! مساعد)|محام|محاسب قانون|مدير|مستشار|مدرس|\bمعلم[هة]\b|معلمات|معلمين(?!\s*{_CRAFT_TAIL})|\bمعلم (صف|ماده|لغه|رياضيات|فيزياء|كيمياء|علوم|انكليزي|عربي|حلقه)|دكتور|\bانسات لمدرسه|"
     r"developer|engineer|مطور|محلل|مسؤول(?!\s*(?:تنظيف|نظافه|ضيافه|مطبخ|صاله|صالة|تغليف))|\bmeal\b|منسق|ضابط|باحث|مترجم|قابله|معالج|مشرف|رئيس (قسم|شعبه|دائره)|مختص|"
     r"محرر|موارد بشريه|\bcto\b|officer|manager|ميسر|اداره حاله|عامل حاله|معد تقارير|خريج|مراسلات", "bachelor"),
    (r"ممرض|فني|تقني|مساعد[هة]? (مهندس|صيدل|سني|طبيب|جراح|فني|تمريض)|مخبري|قابله", "institute"),
    (r"محاسب|سكرتير|سكرتاريا|مدخل[هة]? بيانات|data\s*entry|امين (مستودع|صندوق|مكتبه)|مصمم|مونتير|مصور|كول سنتر|خدمه عملاء|"
     r"استقبال|كاشير|مساعد|موظف[هة]? (اداري|مكتب|تسجيل|توزيع|توعيه)|كاتب|مراقب ميداني", "secondary"),
    (rf"عامل|سائق|حارس|مستخدم|ناطور|عتال|توصيل|دليفري|ديلفري|طباخ|مساعد شيف|شيف|نادل|جرسون|كوفي|باريستا|خياط|"
     r"ورشه|معمل|مبيعات|بائع|موظف[هة]? (مبيعات|صاله|محل)|تنظيف|نظافه|مربي[هة]?|جليس|مندوب|مستخدم|مروج|"
     rf"مسؤول[هة]? (تنظيف|نظافه|ضيافه)|ترويج|مقدم رعايه|مطعم|كافي|كوفي|محل|مول|ماركت|(?:معلم|معلمين|معلمي|معلمو)\s+{_CRAFT_TAIL}|تلييس|لياسه|مكابس|بلوك|باريستا|كومي|بيوت بلاستيك|مغسل|كابتن|كباتن|ماكينه|حبكه|مشغل|فرن|مخبز|حلويات|صانع|نجار|حداد|دهان|بلاط|لحام", "none"),
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


# ---------------------------------------------------------------- cities
CITIES = ["دمشق", "ريف دمشق", "حلب", "حمص", "حماة", "اللاذقية", "طرطوس", "إدلب", "درعا", "السويداء", "القنيطرة",
          "دير الزور", "الحسكة", "الرقة"]
_CITY_ALIASES = {
    "دمشق": ["دمشق", "الشام", "المزه", "البرامكه", "القيمريه", "باب توما", "المالكي", "جرمانا", "صحنايا", "قدسيا", "دمر", "damascus"],
    "ريف دمشق": ["ريف دمشق", "دوما", "حرستا", "مدينه التل", "منطقه التل", "ضاحيه التل", "الكسوه", "يبرود", "النبك", "قطنا", "عدرا", "داريا", "الزبداني", "صيدنايا", "rural damascus"],
    "حلب": ["حلب", "اعزاز", "منبج", "الباب", "عفرين", "جبل سمعان", "جرابلس", "الاتارب", "مارع", "aleppo"],
    "حمص": ["حمص", "تدمر", "القصير", "تلكلخ", "الرستن", "تلبيسه", "homs"],
    "حماة": ["حماه", "سلميه", "مصياف", "محرده", "السقيلبيه", "hama", "hamah"],
    "اللاذقية": ["اللاذقيه", "لاذقيه", "جبله", "القرداحه", "الحفه", "كسب", "latakia", "lattakia", "jableh"],
    "طرطوس": ["طرطوس", "بانياس", "صافيتا", "الدريكيش", "الشيخ بدر", "مشتى الحلو", "القدموس", "tartous", "tartus", "banias"],
    "إدلب": ["ادلب", "معره النعمان", "سرمدا", "الدانا", "حارم", "جسر الشغور", "اريحا", "سراقب", "اطمه", "سلقين", "idlib", "idleb", "sarmada"],
    "درعا": ["درعا", "ازرع", "الصنمين", "بصرى", "نوى", "طفس", "جاسم", "الحراك", "daraa", "deraa"],
    "السويداء": ["السويداء", "شهبا البلد", "شهبا", "صلخد", "sweida", "suwayda"],
    "القنيطرة": ["القنيطره", "خان ارنبه", "quneitra"],
    "دير الزور": ["دير الزور", "ديرالزور", "الميادين", "البوكمال", "هجين", "deir ez-zor", "deir ezzor", "deir alzor", "deir al-zor", "hajeen"],
    "الحسكة": ["الحسكه", "القامشلي", "قامشلي", "راس العين", "المالكيه", "عامودا", "الدرباسيه", "hasakah", "hasakeh", "qamishli", "al qamishli"],
    "الرقة": ["الرقه", "الطبقه", "تل ابيض", "raqqa", "tabqa"],
}
_AR_PROCLITIC = r"(?:[بلو](?=(?:دمشق|حلب|حمص|حماه|ادلب|درعا|طرطوس|اللاذقيه|اللاذقية|اسطنبول|إسطنبول|الفاتح|شيشلي|تقسيم|اكسراي|مرسين|بورصه|بورصة|انقره|أنقرة|غازي|عنتاب|انطاليا|أنطاليا|ازمير|إزمير|انطاكيا|أنطاكيا|قونيا|اضنه|أضنة|قيصري|طرابزون|باشاك|اسنيورت|زيتون|بيليك|بهتشه|اورتاكوي|بشكتاش|كاديكوي|اسكودار|باغجلار|عمرانيه)))?"
_CITY_RE = [(city, re.compile(r"(?<![\w])(?:" + _AR_PROCLITIC + r"|#)(" + "|".join(re.escape(fold(a)).replace(r"\ ", r"[\s_]*") for a in aliases) + r")(?![\w])", re.I))
            for city, aliases in _CITY_ALIASES.items()]
_ALL_CITIES = re.compile(fold(r"جميع المحافظات|كافه المحافظات|عده محافظات|عده من المحافظات|محافظات مختلفه|كل المحافظات"))
_FALSE_CITY_CONTEXT = re.compile(
    fold(
        r"(?:عمال|عامل|معلم|معلمو|معلمي|معلمين|خلط|خلاطه|صب|كيس|اكياس)\s+جبله|"
        r"جبله\s+(?:باطون|بيتون|اسمنت|طين)|"
        r"(?:جامعه|بنك|مؤسسه|شركه|مجموعه|تلفزيون|اذاعه|مستشفى|مشفى|فندق|مطعم|معهد|مدارس|مدرسه|كليه|فرع)\s+الشام(?:\s+الخاصه)?|"
        r"شام\s+(?:كاش|اف ام|fm)|"
        r"(?:فتح|غلق|اغلاق|عند|امام|وراء|خلف|عبر|من|الى|لباب|ابواب)\s+الباب|"
        r"الباب\s+(?:المفتوح|الرئيسي|الخلفي|الامامي|الاول|الثاني|الشرقي|الغربي)"
    ),
    re.I,
)


_HASHTAG_SPAM_LINE = re.compile(r"(?m)^(?:[^\n]*#[^\n]*){5,}$")


def cities_from_text(text: str) -> list[str]:
    raw_t = text or ""
    stripped_hashtags = _HASHTAG_SPAM_LINE.sub(" ", raw_t)
    cleaned_raw = re.sub(r"(?<![ء-ي])(?:ال)?بصر(?:ي|ية)(?![ء-ي])", " ", stripped_hashtags if stripped_hashtags.strip() else raw_t)
    t = _FALSE_CITY_CONTEXT.sub(" ", fold(cleaned_raw))
    if _ALL_CITIES.search(t):
        return ["عدة محافظات"]
    found = []
    rif = fold("ريف دمشق")
    for city, rx in _CITY_RE:
        # "ريف دمشق" contains "دمشق": look for Damascus only in what is left after removing it.
        hay = t.replace(rif, " ") if city == "دمشق" else t
        if rx.search(hay):
            found.append(city)
    return found


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
        "basaksehir", "باشاك شهير", "باشاكشهير", "esenyurt", "اسنيورت", "إسنيورت",
        "bahcesehir", "بهتشه شهير", "بهشة شهير", "بهجه شهير",
        "sisli", "شيشلي", "fatih", "الفاتح", "zeytinburnu", "زيتون بورنو",
        "beylikduzu", "بيليك دوزو", "بيلك دوزو", "بيليكدوزو", "merter", "مرتر", "مارتر",
        "avcilar", "أفجلار", "افجلار", "bagcilar", "باغجلار", "yenibosna", "ينيبوسنا", "يني بوسنا", "ينيبوسنه",
        "sirinevler", "شيرين إيفلر", "شيرين ايفلر", "شيرينيفلر",
        "kayasehir", "كيا شهير", "كاياشهير", "كايا شهير",
        "taksim", "تقسيم", "aksaray", "أكسراي", "اكسراي", "uskudar", "أوسكودار", "اسكودار",
        "kadikoy", "كاديكوي", "umraniye", "عمرانية", "pendik", "بنديك", "istoc", "إستوش", "استوش",
        "ortakoy", "أورتاكوي", "اورتاكوي", "besiktas", "بشكتاش",
        "mecidiyekoy", "مجيدية كوي", "maslak", "مسلك", "maltepe", "مال تبه", "مالتبه",
        "topkapi", "توب كابي", "توبكابي", "sultanahmet", "السلطان أحمد", "سلطان أحمد", "سلطان احمد",
        "laleli", "لالالي", "لاللي",
        "arnavutkoy", "ارناؤوط كوي", "kucukcekmece", "كوتشوك تشكمجه", "bahcelievler", "بهجلي ايفلر",
        "sultangazi", "سلطان غازي", "bayrampasa", "بايرام باشا", "kagithane", "كاغت هانه",
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
    "بورصة": ["بورصة", "بورصه", "bursa", "nilufer", "نيلوفر", "osmangazi", "عثمان غازي", "inegol"],
    "هاتاي (أنطاكيا)": [
        "هاتاي", "أنطاكيا", "انطاكيا", "الريحانية", "ريحانية", "اسكندرون", "إسكندرون",
        "كركخان", "hatay", "antakya", "reyhanli", "iskenderun", "kirikhan",
    ],
    "إزمير": ["إزمير", "ازمير", "izmir", "bornova", "konak"],
    "قونيا": ["قونيا", "konya", "selcuklu"],
    "شانلي أورفا": ["شانلي أورفا", "شانلي اورفا", "أورفا", "اورفا", "sanliurfa", "urfa"],
    "أضنة": ["أضنة", "اضنة", "أضنه", "اضنه", "adana", "seyhan"],
    "قيصري": ["قيصري", "kayseri"],
    "كليس": ["كليس", "كيليس", "kilis"],
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


def is_foreign_non_target(text: str = "", apply_url: str | None = None, email: str | None = None, has_local_city: bool = False) -> bool:
    """Return True for clearly non-Syria/non-Turkey listings (e.g. Egypt CIB/Intersport-EG, Gulf)."""
    if email and _FOREIGN_EMAIL_TLD.search(email):
        return True
    if apply_url and _FOREIGN_URL_RE.search(apply_url):
        return True
    if not has_local_city and text and _FOREIGN_COUNTRY_RE.search(text):
        return True
    return False


def tr_cities_from_text(text: str) -> list[str]:
    """Detect canonical Turkish provinces from Arabic, English, or Turkish text/districts."""
    raw_t = text or ""
    stripped = _HASHTAG_SPAM_LINE.sub(" ", raw_t)
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
    ("computer", r"مهندس (برمجيات|شبكات|معلوماتيه|حاسوب|اتصالات|ذكاء|بيانات|امن)|دعم (تقني|فني)|\bcto\b|مدير تقني|صيانه (شبكات|حواسيب|كمبيوتر|موبايل)|معلوماتيه|حاسوب|برمج|مطور|developer|software|شبكات|سيبران|ذكاء (صنع|اصطناع)|بيانات رقم|مدخل[هة]? بيانات|ادخال بيانات|data\s*entry|devops|backend|"
                 r"front|full.?stack|flutter|\bit\b|تقانه|تقنيه المعلومات|تحول رقم|linux|سيرفر"),
    ("health", r"طبيب|ممرض|صيدل|مخبر|مخابر|اشعه|قابله|تمريض|عياده|مشفى|مستشفي|مستشفى|اسنان|\bسني\b|تغذيه|اسعاف|ليزر|"
               r"علاج فيزيائ|معالج|تخدير|تعقيم|بصريات|نظارات|جراح|تصوير طبقي|رنين|طبي\b|صحي\b|اطراف صناعيه|غسيل كلي"),
    ("engineering", r"\bمهندس|مهندس (مدني|معماري|ميكانيك|كهرب|تنفيذ|اشراف|مساح|طبوغراف|ديكور)|مهندس[هة]?$|هندسه (مدنيه|معماريه|ميكانيك)|"
                    r"اوتوكاد|autocad|مساح"),
    ("business", r"سلسله (التوريد|الامداد)|محاسب|مالي|اداري|سكرتير|سكرتاريا|موارد بشريه|مشتريات|مستودع|امين صندوق|تدقيق|مكتب|لوجست|سلسله الامداد|"
                 r"توظيف|شؤون الموظفين|تامينات|عمليات"),
    ("translation", r"مترجم|ترجمه|translator|interpreter"),
    ("sales", r"مبيعات|مندوب|تسويق|ترويج|مروج|خدمه (ال)?(عملاء|زبائن)|كول سنتر|call center|كاشير|استقبال|تذاكر|سياحه|استشارات عقاريه|عقارات|سيلز|\bsales\b"),
    ("education", rf"مدرس|\bمعلم[هة]\b|معلمات|معلمين(?!\s*{_CRAFT_TAIL})|\bمعلم (صف|ماده|لغه|رياضيات|علوم|حلقه)|مدرب|تعليم|روضه|حضانه|مربي|تدريس|شريعه"),
    ("media", r"انتاج (و ?مونتاج|فيديو|اعلامي|مرئي)|اعلام|مصمم|تصميم|مونتير|مونتاج|مصور|محتوي|محتوى|سوشال|سوشيال|غراف|graphic|محرر|منصات التواصل|اخبار"),
    ("law", r"محام|قانون|حقوق"),
    ("ngo", r"متابعه وتقييم|مراقبه وتقييم|رصد والتقييم|\bmeal\b|حمايه|اداره حال|دعم نفسي|مسؤول تسجيل|توعيه|"
            r"سبل العيش|المساعدات|النوع الاجتماعي|مرشد مهني|مشاريع|\bمشروع\b|مسؤول برامج|ميداني|ميسر|المأوي|ماوي|الاصحاح|الذخائر"),
    ("hospitality", r"مطعم|كافيه|كافتيريا|كوفي|شيف|طباخ|نادل|جرسون|باريستا|فلافل|بوفيه|فندق|حلويات|مشاوي|شاورما|سلطات|عصائر|عصير|مطبخ|كومي|سناك|كفتيريا|كافتيريا|كافيه|كافيتريا|مسبحه|سكيب|(?:معلم|معلمين|معلمي|معلمو) (شاورما|فلافل|بيتزا|مشاوي|حلويات|سلطات|معجنات|فطائر|عصائر)"),
    ("retail", r"محل|صاله|البسه|ألبسه|ماركت|سوبر|\bمول\b|البان|اجبان|بقاليه|معرض|بوتيك|مكياج|عطور|مبيعات داخل"),
    ("industry", r"معمل|ورشه|خياط|حبكه|ماكين|انتاج|تصنيع|تلييس|لياسه|جبصين|مكابس|بلوك|بيتون|باطون|سيراميك|بلاط|نجار|حداد|دهان|كهرباء|كهربائي|تمديدات|ميكانيكي|لحام|المنيوم|"
                 r"صيانه|نسيج|جوارب|مصنع|صناعه|منشره|موبيليا|مشغل الات|مشغل|فرن|مخبز|سكاكر|(?:معلم|معلمين|معلمي|معلمو) (تلييس|لياسه|بلوك|مكابس|جبصين|دهان|بلاط|سيراميك|حداده|نجاره|المنيوم|بيتون|باطون|عمار|بناء|صحيه)"),
    ("driving", r"سائق|توصيل|دليفري|ديلفري|كابتن|كباتن|درايفر"),
    ("security", r"حارس|ناطور|امن وحمايه|حراسه"),
    ("cleaning", r"تنظيف|نظافه|نضافه|مستخدم|عامل[هة]? خدم"),
    ("care", r"رعايه|جليس|مسن|مربي[هة]? اطفال|خادمه|عامله منزل|مدبره منزل"),
]
_FIELD_RE = [(f, re.compile(fold(p), re.I)) for f, p in _FIELD_WORDS]


def field_from_text(title: str, context: str = "") -> str:
    """The main field of a job, from its title first and then the rest of the ad."""
    for text in (title, context):
        if f := _first_match(_title_form(text), [(rx, f) for f, rx in _FIELD_RE]):
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
_URL = re.compile(r"https?://[^\s<>\"'\]]+")
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
_HOURS = re.compile(r"(?:من|الساعه|الساعة)?\s*(\d{1,2})(?::\d{2})?\s*(?:-|الى|إلى|حتى|لل|ل)\s*(\d{1,2})(?::\d{2})?")


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
    return [u.rstrip(".,)،") for u in _URL.findall(text)]


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
    r"انقاذ الطفل|save the children|mercy corps|شفق|بنفسج|احسان|بهار|الاطباء المستقلين|عمل انساني|مؤسسه خيريه|القطاع الانساني|منظمات دوليه|"
    r"الهيئه العامه للمنظمات|سوريون بلا حدود"
), re.I)
_FORM_RE = re.compile(
    r"docs\.google\.com/forms|forms\.gle|forms\.office\.com|forms\.cloud\.microsoft|"
    r"ee\.humanitarianresponse\.info|kobotoolbox|typeform\.com|jotform\.com|airtable\.com|tally\.so|surveymonkey",
    re.I,
)


def infer_sector(official: bool, field: str, employer: str | None, text: str = "", hint: str | None = None) -> str:
    if hint in ("gov", "ngo", "private"):
        return hint
    emp_f = fold(employer or "")
    if _NGO_RE.search(emp_f):
        return "ngo"
    if official:
        return "gov"
    if field == "ngo" or _NGO_RE.search(fold(text[:700])):
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
