"""Spot posts that are not real jobs (ads, crypto, "earn from home") or that ask job seekers for money."""
from __future__ import annotations

import re

from .arabic import soft_fold as fold

# Each pattern adds to a score. A post at or above THRESHOLD is hidden from the site and the bot.
_SIGNALS = [
    (3, r"binance|بينانس|باينانس|كريبتو|crypto|عملات رقميه|usdt|بيتكوين|bitcoin|تداول|فوركس|forex",
     "يذكر عملات رقمية أو منصات تداول (بينانس / كريبتو / فوركس)"),
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
    (3, r"واقي شمس|سيروم|كافيار|عنايه بالبشره|كولاجين|شجره الاعضاء|التسويق الشبكي|بونص شهري",
     "يبدو كإعلان ترويجي لمنتجات تجميل أو تسويق شبكي وليس فرصة عمل حقيقية"),
    (2, r"تسويق بالعمول[هة]|ربح من الانترنت|لقاء تعريفي عن العمل الحر|جلسه تعريفيه عبر زوم",
     "إعلان ترويجي للتسويق بالعمولة أو جلسات عامة وليس شاغراً واضحاً"),
]
_COMPILED = [(w, re.compile(fold(p), re.I), reason) for w, p, reason in _SIGNALS]
THRESHOLD = 3
_JOB_WORDS = re.compile(fold(r"مطلوب|شواغر|شاغر|وظيف|توظيف|بحاجه (الى|ل)|حاجت(ها|ه) (الى|الي|ل)|نبحث عن|فرصه عمل|فرص عمل|للعمل|يلزمنا|"
                               r"بالانضمام (الى|ل)|الانضمام (الى|ل)(فريق|كادر|اسره)|يرغب بالانضمام|التعاقد مع|closing date|vacancy|job|hiring|position"), re.I)


def spam_score(text: str) -> int:
    t = fold(text)
    score = sum(w for w, rx, _ in _COMPILED if rx.search(t))
    if not _JOB_WORDS.search(t):
        score += 1  # no job wording at all
    return score


def analyze_ad(text: str) -> dict:
    """Explain why an ad looks safe, suspicious, or likely a scam."""
    t = fold(text)
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


def is_job_post(text: str) -> bool:
    """A post that announces work (as opposed to greetings, news, or trainings)."""
    t = fold(text)
    if not _JOB_WORDS.search(t):
        return False
    if re.search(fold(r"^(صباح|مساء) الخير|دوره تدريبيه|ورشه عمل حول|مناقصه|اعلان مناقصه"), t) and not re.search(fold("مطلوب|شواغر"), t):
        return False
    return True
