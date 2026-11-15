"""Spot posts that are not real jobs (ads, crypto, "earn from home") or that ask job seekers for money."""
from __future__ import annotations

import re

from .arabic import soft_fold as fold

# Each pattern adds to a score. A post at or above THRESHOLD is hidden from the site and the bot.
_SIGNALS = [
    (3, r"binance|بينانس|باينانس|كريبتو|crypto|عملات رقميه|usdt|بيتكوين|bitcoin|تداول (?:العملات|الذهب|الاسهم|عبر|الكتروني|فوركس|رقم)|منص[هة] تداول|التداول (?:الالكتروني|عبر الانترنت)|فوركس|forex|xauusd|ذهب فوركس|وساطه ماليه",
     "يذكر عملات رقمية أو منصات تداول / فوركس (XAUUSD / Binance)"),
    (3, r"اربح|ربح يومي|دخل يومي|بدون راس مال|من البيت واربح|سجل واربح|مكافاه تسجيل|بدون ايداع",
     "يوعد بربح يومي أو دخل سريع بدون رأس مال (علامة احتيال شائعة)"),
    (3, r"رسوم (تسجيل|اشتراك|توظيف)|ادفع|دفع مبلغ|تامين مسبق|عمول[هة] توظيف|مقابل مبلغ",
     "يطلب رسوم تسجيل أو دفع مبلغ أو تأمين مسبق من المتقدم"),
    (2, r"اشتراك(ات)? (ادوبي|adobe|نتفلكس|netflix|chatgpt|gemini|كانفا|canva)|عرض (لمده|محدود)|خصم",
     "يبدو كإعلان ترويجي لاشتراكات أو عروض مدفوعة"),
    (3, r"(اشتراك|باقه)[^\n]{0,40}(gemini|جيميني|جميني|chatgpt|شات جي بي تي|canva|كانفا|adobe|netflix|نتفلكس)|اشتراك لمده \S+ بسعر|بسعر \d",
     "إعلان بيع اشتراكات وليس فرصة عمل حقيقية"),
    (2, r"\bbot\b|بوت تلغرام|t\.me/\w+bot",
     "يحوّل المستخدم إلى بوت تلغرام خارجي"),
    (1, r"شام كاش|سيرياتيل كاش|ام تي ان كاش|حواله",
     "يذكر تحويل أموال أو محافظ إلكترونية"),
    (1, r"كورس|دوره (تدريبيه|اونلاين)|شهاده دوليه|سجل الان في الدوره",
     "يروّج لدورة تدريبية أو كورس"),
    (3, r"شجره الاعضاء|التسويق الشبكي|بونص شهري",
     "يبدو كإعلان تسويق شبكي أو هرمي وليس فرصة عمل حقيقية"),
    (2, r"واقي شمس|سيروم|كافيار|كولاجين",
     "يبدو كإعلان ترويجي لمنتجات تجميل وليس فرصة عمل حقيقية"),
    (1, r"عنايه بالبشره",
     "يذكر مجال العناية بالبشرة"),
    (2, r"تسويق بالعمول[هة]|ربح من الانترنت|لقاء تعريفي عن العمل الحر|جلسه تعريفيه عبر زوم",
     "إعلان ترويجي للتسويق بالعمولة أو جلسات عامة وليس شاغراً واضحاً"),
]
_COMPILED = [(w, re.compile(fold(p), re.I), reason) for w, p, reason in _SIGNALS]
_ANTI_SCAM_WARNING_RE = re.compile(
    fold(r"(?:اعتذري?|احذروا?|تنويه|تحذير|تجنب|يمنع|ممنوع|لا\s+تدفع|بدون\s+اي)[^\n.!،]{0,55}(?:دفع\s+مبلغ|رسوم(?:\s+تسجيل|\s+توظيف|\s+اشتراك)?|تامين\s+مسبق)[^\n.!،]{0,35}"),
    re.I,
)
THRESHOLD = 3
_JOB_WORDS = re.compile(fold(r"مطلوب|شواغر|شاغر|وظيف|توظيف|بحاجه (الى|ل)|حاجت(ها|ه) (الى|الي|ل)|نبحث عن|فرصه عمل|فرص عمل|للعمل|يلزمنا|"
                               r"بالانضمام (الى|ل)|الانضمام (الى|ل)(فريق|كادر|اسره)|يرغب بالانضمام|التعاقد مع|closing date|vacancy|job|hiring|position|"
                               r"iş ilanı|is ilani|personel alımı|\b(?:officer|coordinator|manager|advisor|assistant|specialist)\b"), re.I)


def spam_score(text: str) -> int:
    t = _ANTI_SCAM_WARNING_RE.sub(" ", fold(text))
    score = sum(w for w, rx, _ in _COMPILED if rx.search(t))
    if not _JOB_WORDS.search(t):
        score += 1  # no job wording at all
    return score


def analyze_ad(text: str) -> dict:
    """Explain why an ad looks safe, suspicious, or likely a scam."""
    t = _ANTI_SCAM_WARNING_RE.sub(" ", fold(text))
    score = 0
    reasons: list[str] = []
    for w, rx, reason in _COMPILED:
        if rx.search(t):
            score += w
            if reason not in reasons:
                reasons.append(reason)
    if not _JOB_WORDS.search(t):
        score += 1
        reasons.append("النص لا يحتوي على صياغة إعلان وظيفة واضح")
    verdict = "danger" if score >= THRESHOLD else ("caution" if score > 0 else "safe")
    return {"score": score, "verdict": verdict, "reasons": reasons}


def is_spam(text: str) -> bool:
    return spam_score(text) >= THRESHOLD


_NON_JOB_EXCLUDES = re.compile(
    fold(
        r"دعوه لتقديم عروض اسعار|تقديم عروض اسعار|عروض اسعار|\bitb\b|\brfp\b|\brfq\b|استدراج عروض|"
        r"حاله الطقس|درجات الحراره|توقعات الطقس|الارصاد الجويه|"
        r"\bبورس\b|منحه دراسيه|منح دراسيه|\bburslar[ıi]\b|\bburs\s+program|"
        r"egitimlerine kayitlar|eğitimlerine kayıtlar|ucretsiz ogrenci|ücretsiz öğrenci|\btubitak\b|\btübitak\b|ogrenci plan"
    ),
    re.I,
)
_TR_GOV_CIVIL_RE = re.compile(
    r"\bkpss\b|\bt\.?c\.?\s*vatanda[şs][ıi]\b|\btürkiye cumhuriyeti vatanda[şs][ıi]\b|"
    r"\b(?:kamu|memur|uzman yard[ıi]mc[ıi]s[ıi]|müfettiş yard[ıi]mc[ıi]s[ıi]|kontrolörü|sözleşmeli\s+(?:personel|mühendis|tekniker))\b[^\n]{0,40}\bal[ıi]m[ıi]\b|"
    r"(?:bakanlığ[ıi]|cumhurbaşkanlığ[ıi]|genel müdürlüğü|valiliğ[ıi]|kaymakamlığ[ıi]|belediye[^\n]{0,30}al[ıi]m[ıi]|\bsgk\b|işkur devlet memur|kamu ihale kurumu|savunma sanayii başkanlığ[ıi])",
    re.I,
)
_TR_CITIZEN_MANDATE_RE = re.compile(
    fold(
        r"(?:تحمل|يحمل|حامل[ةه]?|شروط التقديم[^\n]{0,40}|يشترط|شرط)[^\n]{0,25}(?:ال)?جنسيه(?:\s+ال)?تركيه|"
        r"(?:ال)?جنسيه(?:\s+ال)?تركيه\s*(?:حصرا|فقط)|"
        r"(?:مع|ب|لديه|لديها|ذو|ذات)[^\n]{0,12}(?:ال)?جنسيه(?:\s+ال)?تركيه"
    ),
    re.I,
)
_TR_CITIZEN_PREFER_RE = re.compile(fold(r"(?:يفضل|تفضل|اولويه|افضليه|أولوية)[^\n]{0,30}(?:ال)?جنسيه(?:\s+ال)?تركيه"), re.I)
_TR_NON_CITIZEN_OK_RE = re.compile(fold(r"كيملك|كملك|اقامه|بطاقه حمايه|حمايه مؤقته|اذن عمل|تصريح عمل|او جنسيه|أو إقامة|او اقامه"), re.I)


def is_tr_citizenship_only(text: str) -> bool:
    """Return True for Turkish civil-service/KPSS or strict Turkish-citizenship-only vacancies."""
    raw = text or ""
    if _TR_GOV_CIVIL_RE.search(raw):
        return True
    t = fold(raw)
    if _TR_CITIZEN_MANDATE_RE.search(t) and not _TR_CITIZEN_PREFER_RE.search(t) and not _TR_NON_CITIZEN_OK_RE.search(t):
        return True
    return False


def is_job_post(text: str) -> bool:
    """A post that announces work (as opposed to greetings, news, tenders, or trainings)."""
    t = fold(text)
    if _NON_JOB_EXCLUDES.search(t):
        return False
    if not _JOB_WORDS.search(t):
        return False
    if re.search(fold(r"^(صباح|مساء) الخير|دوره تدريبيه|ورشه عمل حول|مناقصه|اعلان مناقصه"), t) and not re.search(fold("مطلوب|شواغر"), t):
        return False
    return True
