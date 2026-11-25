"""Optional AI reader for the posts the rules can't read well.

Off unless ANTHROPIC_API_KEY is set (and the `anthropic` package is installed). It is only asked about posts the rule
extractor was unsure of, and at most LLM_DAILY_LIMIT times a day, so the bill stays small and predictable.
Contacts (phones, emails, links) are never taken from the model: they come from the post text by the same rules as
always, so the AI cannot invent a phone number.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import config, db
from . import taxonomy as tx
from .models import Job

log = logging.getLogger("llm")

EduCode = Literal["none", "basic", "secondary", "institute", "bachelor", "master", "phd"]
FieldCode = Literal[tuple(code for code, _ in tx.FIELDS)]  # type: ignore[valid-type]
CityName = Literal[tuple(tx.CITIES + ["عدة محافظات"] + tx.TR_CITIES + ["عدة ولايات في تركيا"])]  # type: ignore[valid-type]


class LlmJob(BaseModel):
    title: str = Field(description="المسمى الوظيفي كما يُكتب على بطاقة، قصير، بدون اسم الجهة أو المدينة")
    employer: Optional[str] = Field(description="اسم الجهة أو الشركة إذا ذُكر صراحة، وإلا null")
    cities: list[CityName] = Field(description="المحافظات/الولايات المذكورة لهذه الوظيفة")
    education: Optional[EduCode] = Field(description="أدنى شهادة يطلبها الإعلان صراحة، أو null إذا لم يذكر")
    field: FieldCode
    experience_years: Optional[int] = Field(description="سنوات الخبرة المطلوبة إذا ذُكرت")
    age_min: Optional[int]
    age_max: Optional[int]
    gender: Optional[Literal["male", "female"]] = Field(description="فقط إذا كان الإعلان لجنس واحد صراحة")
    salary: Optional[str]
    deadline: Optional[str] = Field(description="آخر موعد للتقديم بصيغة YYYY-MM-DD إذا ذُكر")
    kind: Literal["formal", "quick"] = Field(description="quick: شغل سريع بالتواصل المباشر بلا موعد نهائي؛ formal: تقديم رسمي")


class LlmPost(BaseModel):
    is_job_ad: bool = Field(description="false للتدريبات والمناقصات والإعلانات التجارية وعروض الربح والنصب")
    jobs: list[LlmJob]


SYSTEM = """أنت تقرأ منشورات إعلانات توظيف عربية (سوريا وتركيا) من قنوات تلغرام ومواقع، وتستخرج الوظائف كبيانات.

القواعد:
- منشور واحد قد يحوي عدة وظائف: كل مسمى وظيفي مستقل = عنصر في jobs. المتطلبات والمهام ليست وظائف.
- لا تخترع شيئاً غير مكتوب. الحقل غير المذكور = null.
- education: أدنى شهادة يقبلها الإعلان صراحة. المهن والحِرف (عامل، معلم شاورما، خياط، سائق، نادل…) بدون ذكر شهادة = "none".
  «معلم» في المطاعم والورش تعني صاحب صنعة وليست مدرّس. الفئة الأولى=bachelor، الثانية=institute، الثالثة=secondary، الرابعة=basic، الخامسة=none.
- cities: استخدم الأسماء من القائمة المسموحة فقط. «جميع المحافظات» = "عدة محافظات".
- is_job_ad=false للدورات التدريبية، المناقصات، الإعلانات التجارية، عروض الربح من البيت، التداول والعملات الرقمية، وطلب رسوم مقابل التوظيف.
- title بالعربية كما يُفهم للباحث عن عمل، بدون رموز أو هاشتاغ."""

_JOB_HINT = re.compile(r"مطلوب|شاغر|وظيف|توظيف|فرص[ةه] عمل|للعمل|نبحث عن|بحاجة|hiring|vacanc", re.I)


def available() -> bool:
    if not config.ANTHROPIC_API_KEY:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def worth_asking(text: str, rule_jobs: list[Job]) -> bool:
    """Only posts the rules were unsure about: a low-confidence job, or a job-looking post where rules found nothing."""
    if not text or len(text) < 60:
        return False
    if any(j.confidence < config.REVIEW_BELOW for j in rule_jobs):
        return True
    return not rule_jobs and bool(_JOB_HINT.search(text))


def _budget_ok(con) -> bool:
    key = f"llm_calls:{date.today().isoformat()}"
    used = int(db.get_meta(con, key, 0) or 0)
    if used >= config.LLM_DAILY_LIMIT:
        return False
    db.set_meta(con, key, used + 1)
    return True


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)
    return _client


def ask(text: str) -> LlmPost | None:
    """One request to the model; None on any failure (the caller keeps the rule result)."""
    import anthropic

    try:
        response = _get_client().messages.parse(
            model=config.LLM_MODEL,
            max_tokens=4000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"المنشور:\n\n{text[:6000]}"}],
            output_format=LlmPost,
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as e:
        log.warning("llm request failed: %s", e)
        return None
    except Exception as e:  # a reply that doesn't match the schema
        log.warning("llm reply unusable: %s", e)
        return None
    if response.stop_reason == "refusal":
        log.info("llm declined a post (%s)", getattr(response.stop_details, "category", None))
        return None
    if response.stop_reason == "max_tokens":
        return None
    return response.parsed_output


def to_jobs(parsed: LlmPost, text: str, source: str, post_url: str, posted_at: str | None,
            default_kind: str, links: list[str] | None, country: str | None, sector: str | None) -> list[Job]:
    """Turn the model's reading into Job rows; contacts come from the text by rule, never from the model."""
    if not parsed.is_job_ad:
        return []
    ctry = country or "sy"
    phones = tx.phones_tr(text) if ctry == "tr" else tx.phones(text)
    emails = tx.emails(text)
    urls = [u for u in (tx.urls(text) + list(links or [])) if "t.me/" not in u]
    out = []
    for lj in parsed.jobs:
        title = (lj.title or "").strip()
        if len(title) < 2:
            continue
        deadline = lj.deadline if lj.deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", lj.deadline) else None
        job = Job(
            title=title[:120], source=source, post_url=post_url, posted_at=posted_at,
            employer=(lj.employer or None), cities=list(dict.fromkeys(lj.cities)),
            education=lj.education, education_from="stated" if lj.education else "unknown",
            field=lj.field, experience=lj.experience_years, age_min=lj.age_min, age_max=lj.age_max,
            gender=lj.gender, salary=lj.salary, deadline=deadline,
            kind=lj.kind if default_kind != "quick" else "quick",
            phones=phones, whatsapp=tx.whatsapp_only(text) or bool(phones), email=emails[0] if emails else None,
            apply_url=urls[0] if urls else None, country=ctry, sector=sector or "private",
            confidence=0.75, tags=["قراءة آلية"], raw=text,
        )
        job.sector = tx.infer_sector(False, job.field, job.employer, text, hint=sector, country=ctry)
        out.append(job)
    return out


def improve(con, text: str, rule_jobs: list[Job], *, source: str, post_url: str, posted_at: str | None,
            default_kind: str, links: list[str] | None, country: str | None, sector: str | None) -> list[Job]:
    """The rule result, or the model's reading when the rules were unsure and the model answered."""
    if not (available() and worth_asking(text, rule_jobs) and _budget_ok(con)):
        return rule_jobs
    parsed = ask(text)
    if parsed is None:
        return rule_jobs
    return to_jobs(parsed, text, source, post_url, posted_at, default_kind, links, country, sector)
