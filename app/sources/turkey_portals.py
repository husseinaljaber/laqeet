"""Verified job-portal scrapers for Arabic-friendly & international jobs in Turkey."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import html
import json
import logging
import re
import threading
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
import httpx

from .. import config
from .. import direct_apply as da
from .. import extract as ex
from .. import spam
from .. import taxonomy as tx
from ..classify import classify as computer_subfield, is_computer_degree
from ..models import Job

log = logging.getLogger("turkey_portals")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,tr;q=0.9,en;q=0.8",
}

_AR_REL_NUMS = {"واحد": 1, "واحدة": 1, "ساعة": 1, "يوم": 1, "أسبوع": 1, "اسبوع": 1, "شهر": 1, "سنة": 1, "عام": 1}


def _decode_html(r: httpx.Response) -> str:
    return r.content.decode("utf-8", "replace")


def _is_recent_iso(iso_str: str | None, max_days: int | None = None) -> bool:
    if not iso_str:
        return True
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_days or config.HISTORY_DAYS)
        return dt >= cutoff
    except Exception:
        return True


def _parse_relative_ar_date(text: str, now_dt: datetime | None = None) -> tuple[str, bool]:
    """Parse Arabic relative timestamps ('منذ يوم', 'منذ 5 أيام', 'منذ 7 أشهر') -> (iso, is_recent)."""
    ref = now_dt or datetime.now(timezone.utc)
    norm = (text or "").translate(tx._ARABIC_DIGITS)
    m = re.search(
        r"منذ\s+(?:(\d+)\s+)?(ثانية|ثوان|دقيقة|دقائق|ساعة|ساعات|ساعتين|يوم|أيام|ايام|يومين|أسبوع|اسبوع|أسابيع|اسابيع|أسبوعين|اسبوعين|شهر|أشهر|اشهر|شهور|شهرين|سنة|سنوات|سنتين)",
        norm,
    )
    if not m:
        return ref.isoformat(timespec="seconds"), True
    n = int(m.group(1)) if m.group(1) else None
    unit = m.group(2)
    days = 0
    if unit in ("ثانية", "ثوان", "دقيقة", "دقائق", "ساعة", "ساعات", "ساعتين"):
        days = 0
    elif unit == "يومين":
        days = 2
    elif unit in ("يوم", "أيام", "ايام"):
        days = n or 1
    elif unit in ("أسبوعين", "اسبوعين"):
        days = 14
    elif unit in ("أسبوع", "اسبوع", "أسابيع", "اسابيع"):
        days = (n or 1) * 7
    elif unit == "شهرين":
        days = 60
    elif unit in ("شهر", "أشهر", "اشهر", "شهور"):
        days = (n or 1) * 30
    elif unit == "سنتين":
        days = 730
    elif unit in ("سنة", "سنوات"):
        days = (n or 1) * 365
    dt = ref - timedelta(days=days)
    return dt.isoformat(timespec="seconds"), days <= config.HISTORY_DAYS


_PORTAL_SITE_PHONES = {"05385844484"}  # Adwhit site-wide floating WhatsApp support icon


def _clean_tr_title(title: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", title or ""))
    t = re.sub(r"\s*[-|–]\s*(?:فرص عمل في تركيا|أدويت|ادويت|Adwhit|سلام جوبز|SalamJobs).*$", "", t, flags=re.I)
    return ex.normalize_title_display(t, keep_matlob=True)


def _is_clean_tr_posting(title: str, body: str) -> bool:
    combined = f"{title}\n{body}"
    if not title or len(title) < 3 or "\ufffd" in title or not ex._good_title(title):
        return False
    if spam.is_spam(combined) or spam.is_tr_citizenship_only(combined):
        return False
    if not spam.is_job_post(combined):
        return False
    return True


def _build_tr_job(
    *,
    title: str,
    source: str,
    post_url: str,
    posted_at: str,
    body_text: str,
    employer: str | None = None,
    city_hint: str = "",
    salary_hint: str | None = None,
    shift_hint: str | None = None,
    deadline: str | None = None,
    apply_url: str | None = None,
    details_url: str | None = None,
    phones: list[str] | None = None,
    email: str | None = None,
    sector: str | None = None,
    tags: list[str] | None = None,
) -> Job | None:
    clean_t = _clean_tr_title(title)
    clean_body = re.sub(r"\r\n?", "\n", body_text or "").strip()
    if not _is_clean_tr_posting(clean_t, clean_body):
        return None

    combined = f"{clean_t}\n{clean_body}"
    explicit_tr = tx.tr_cities_from_text(f"{city_hint}\n{clean_t}") or tx.tr_cities_from_text(clean_body)
    sy_cities = tx.cities_from_text(f"{city_hint}\n{clean_t}\n{clean_body}")
    # Reject Syria or non-Turkey foreign listings mis-posted to Turkish portals
    if sy_cities and (not explicit_tr or sy_cities and re.search(r"\bسوريا\b|\bsyria\b", combined, re.I)):
        if not tx.tr_cities_from_text(f"{city_hint}\n{clean_t}"):
            return None
    if re.search(r"\b(?:دمشق|حلب|سوريا|syria|damascus|aleppo|درعا)\b", f"{city_hint} {clean_t}", re.I) and not explicit_tr:
        return None
    cities = explicit_tr or ["عدة ولايات في تركيا"]
    found_phones = sorted({
        p for p in (phones or []) + tx.phones_tr(clean_body)
        if p not in tx._TR_BAD_PHONES and p not in _PORTAL_SITE_PHONES
    })
    clean_email = email.strip() if email and da._good_email(email) else None
    if not clean_email:
        for em in tx.emails(clean_body):
            if da._good_email(em):
                clean_email = em
                break
    if tx.is_foreign_non_target(combined, apply_url, clean_email, has_local_city=bool(explicit_tr)):
        return None
    if apply_url:
        apply_url = da.strip_tracking(apply_url) or None
        if apply_url and da.is_disallowed_offline_url(apply_url):
            apply_url = None

    if not apply_url and not found_phones and not clean_email:
        return None

    fld = tx.field_from_text(clean_t, clean_body[:400])
    sub = computer_subfield(clean_t, clean_body[:400])
    if fld == "computer" or sub or is_computer_degree(clean_t):
        fld = "computer"
        sub = sub or "DT"

    title_edu = tx.education_from_title(clean_t)
    edu = ("none" if title_edu == "none" else (tx.education_from_text(combined) or title_edu))
    sal = salary_hint or tx.salary(combined)
    sh = shift_hint or tx.shift(clean_body)
    exp = tx.experience_years(clean_body)
    gdr = tx.gender(clean_t) or tx.gender(clean_body)
    tasks, skills = ex._extract_tasks_and_skills(clean_body)
    clean_emp = employer.strip()[:100] if employer and employer.strip().lower() not in ("confidential", "غير محدد", "") else ex._employer(clean_body)
    if clean_emp and ex._PORTAL_EMPLOYER.search(ex.F(clean_emp)):
        clean_emp = None

    job_tags = list(dict.fromkeys((tags or []) + tx.job_tags(combined)))
    sec = sector or tx.infer_sector(False, fld, clean_emp, clean_body)
    wa = (tx.whatsapp_only(clean_body) or "wa.me/" in (apply_url or "").lower() or "whatsapp" in (apply_url or "").lower()) and bool(found_phones)

    return Job(
        title=clean_t[:120],
        source=source,
        post_url=post_url,
        posted_at=posted_at,
        employer=clean_emp,
        cities=cities,
        education=edu,
        education_from="stated" if edu else None,
        field=fld,
        subfield=sub,
        kind="formal",
        official=False,
        country="tr",
        sector=sec,
        apply_url=apply_url,
        details_url=details_url if (details_url and details_url != post_url) else None,
        phones=found_phones,
        whatsapp=wa,
        email=clean_email,
        deadline=deadline or ex.parse_deadline(clean_body, posted_at),
        salary=sal,
        shift=sh,
        experience=exp,
        gender=gdr,
        tasks=tasks,
        skills=skills,
        apply_types=tx.infer_apply_types(apply_url, found_phones, wa, clean_email),
        confidence=0.92,
        tags=job_tags,
        raw=clean_body[:2400],
    )


_JOBSINTR_EXTRA_PATHS = (
    "/jobs/state/" + quote("اسطنبول-istanbul"),
    "/jobs/state/" + quote("غازي-عنتاب-gaziantep"),
    "/jobs/state/" + quote("مرسين-mersin"),
    "/jobs/state/" + quote("بورصة-bursa"),
    "/jobs/state/" + quote("انقرة-ankara"),
    "/jobs/state/" + quote("هاتاي-hatay"),
    "/jobs/state/" + quote("أضنة-adana"),
    "/jobs/state/" + quote("أنطاليا-antalya"),
    "/jobs/state/" + quote("إزمير-izmir"),
    "/jobs/state/" + quote("قونيا-konya"),
    "/jobs/state/" + quote("أورفا-urfa"),
    "/job-cat/" + quote("خياطة"),
    "/job-cat/" + quote("المطاعم"),
    "/job-cat/" + quote("عامل"),
    "/job-cat/" + quote("المبيعات-الاستشارات"),
    "/job-cat/" + quote("السياحة-العلاجية"),
    "/job-cat/" + quote("محاسبة"),
    "/job-cat/" + quote("التسويق-الالكتروني"),
    "/job-cat/" + quote("التصميم-التصوير"),
    "/job-cat/" + quote("سائق"),
    "/job-cat/" + quote("عامل-توصيل-طلبات"),
    "/job-cat/" + quote("حلاقة-وتجميل"),
    "/job-cat/" + quote("خدمات"),
    "/job-cat/" + quote("برمجة"),
    "/job-cat/" + quote("الطب-الصيدلة"),
    "/job-cat/" + quote("التدريس"),
    "/job-cat/" + quote("ترجمة"),
    "/job-cat/" + quote("سكرتارية"),
    "/job-cat/" + quote("هندسة"),
    "/job-cat/" + quote("إدخال-بيانات"),
    "/job-cat/" + quote("سوشيال-ميديا"),
    "/job-cat/" + quote("الموارد-البشرية"),
    "/job-cat/" + quote("قانونية"),
    "/job-cat/" + quote("سياحة"),
    "/jobs?keyword=" + quote("غازي عنتاب"),
    "/jobs?keyword=" + quote("مرسين"),
    "/jobs?keyword=" + quote("بورصة"),
    "/jobs?keyword=" + quote("أنقرة"),
    "/jobs?keyword=" + quote("أنطاليا"),
    "/jobs?keyword=" + quote("إزمير"),
    "/jobs?keyword=" + quote("قونيا"),
    "/jobs?keyword=" + quote("هاتاي"),
    "/jobs?keyword=" + quote("أورفا"),
    "/jobs?keyword=" + quote("أضنة"),
    "/jobs?keyword=" + quote("سكاريا"),
    "/jobs?keyword=" + quote("كوجالي"),
    "/jobs?keyword=" + quote("طرابزون"),
    "/jobs?keyword=" + quote("قيصري"),
)


_JOBSINTR_TOP_CATS = (
    "/job-cat/" + quote("المبيعات-الاستشارات"),
    "/job-cat/" + quote("المطاعم"),
    "/job-cat/" + quote("خياطة"),
    "/job-cat/" + quote("عامل"),
    "/job-cat/" + quote("السياحة-العلاجية"),
    "/job-cat/" + quote("محاسبة"),
    "/job-cat/" + quote("التسويق-الالكتروني"),
    "/job-cat/" + quote("خدمات"),
    "/job-cat/" + quote("سائق"),
)


def scrape_jobsintr_web(client: httpx.Client, max_pages: int = 5, extra_limit: int | None = None) -> list[Job]:
    """Scrape active Arabic & Turkish listings from jobsintr.net across main pages, Gaziantep, and categories."""
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    now_dt = datetime.now(timezone.utc)
    today_str = now_dt.date().isoformat()
    main_pages = min(max_pages, 8)
    extras = _JOBSINTR_EXTRA_PATHS[:extra_limit] if extra_limit else _JOBSINTR_EXTRA_PATHS
    feed_urls = [
        f"https://jobsintr.net/jobs?page={page}" if page > 1 else "https://jobsintr.net/jobs"
        for page in range(1, main_pages + 1)
    ] + [f"https://jobsintr.net{p}" for p in extras]
    if not extra_limit and max_pages >= 4:
        feed_urls += [f"https://jobsintr.net{p}?page=2" for p in _JOBSINTR_TOP_CATS]

    all_detail_urls: list[str] = []
    for list_url in feed_urls:
        try:
            r = client.get(list_url, headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                continue
        except Exception as e:
            log.warning("jobsintr list %s failed: %s", list_url, e)
            continue

        soup = BeautifulSoup(_decode_html(r), "html.parser")
        for a in soup.select('a[href*="/jobs/"]'):
            href = (a.get("href") or "").split("?", 1)[0].rstrip("/")
            if not re.fullmatch(r"https?://(?:www\.)?jobsintr\.net/jobs/[A-Za-z0-9_-]+", href):
                continue
            if href not in seen_urls:
                seen_urls.add(href)
                all_detail_urls.append(href)

    def _fetch_detail(u: str, http_c: httpx.Client) -> Job | None:
        try:
            dr = http_c.get(u, headers=_HEADERS, timeout=18, follow_redirects=True)
            if dr.status_code != 200:
                return None
            dhtml = _decode_html(dr)
            ds = BeautifulSoup(dhtml, "html.parser")
            header_el = ds.select_one(".job-header-content")
            header_txt = header_el.get_text(" ", strip=True) if header_el else ""
            posted_at, is_recent = _parse_relative_ar_date(header_txt, now_dt=now_dt)
            if not is_recent:
                return None

            h1_el = header_el.select_one("h1, h2") if header_el else None
            raw_title = (h1_el.get_text(" ", strip=True) if h1_el else re.sub(r"\s*منذ\s+.+$", "", header_txt)).strip()
            if not raw_title:
                og = ds.select_one('meta[property="og:title"]')
                raw_title = og.get("content", "") if og else ""

            content_node = ds.select_one(".ck-content") or ds.select_one(".content-single")
            if not content_node:
                return None
            overview_node = ds.select_one(".job-overview")
            overview_txt = overview_node.get_text("\n", strip=True) if overview_node else ""
            body_lines = content_node.get_text("\n", strip=True)

            main_scope = ds.find("main") or ds.find("article") or ds
            info = da.extract_apply_from_html(str(main_scope), u, country="tr")
            apply_u = info.get("apply_url")
            if info.get("closed"):
                return None

            job = _build_tr_job(
                title=raw_title,
                source="jobsintr_web",
                post_url=u,
                posted_at=posted_at,
                body_text=body_lines,
                city_hint=overview_txt,
                salary_hint=tx.salary(overview_txt + "\n" + body_lines),
                apply_url=apply_u,
                details_url=u,
                phones=info.get("phones") or [],
                email=info.get("email"),
                tags=["تركيا"],
            )
            if job and ex.expires_on(job) >= today_str:
                return job
            return None
        except Exception as e:
            log.debug("jobsintr detail %s failed: %s", u, e)
            return None

    if isinstance(client, httpx.Client) and len(all_detail_urls) > 6:
        tl = threading.local()
        lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _worker(u: str) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers=_HEADERS, timeout=18, verify=False, follow_redirects=True)
                tl.client = c
                with lock:
                    pool_clients.append(c)
            return _fetch_detail(u, c)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for j in pool.map(_worker, all_detail_urls):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
    else:
        for u in all_detail_urls:
            j = _fetch_detail(u, client)
            if j is not None:
                jobs.append(j)
    return jobs


_ADWHIT_EXTRA_CATS = (
    "وظائف-المبيعات-والتسويق",
    "وظائف-المطاعم",
    "وظائف-الشحن-والنقل",
    "وظائف-السكرتارية-والادارة",
    "وظائف-التكنولوجيا",
    "وظائف-الغزل-و-النسيج-و-الخياطة",
    "وظائف-السياحة",
    "وظائف-العقارات",
    "وظائف-المحاسبة-والمالية",
    "وظائف-الترجمة",
    "وظائف-التدريس",
    "وظائف-التخصصات-الهندسية",
    "وظائف-التخصصات-الطبية",
    "وظائف-قانونية-واستشارية",
    "وظائف-الفنادق",
    "وظائف-مشاريع-البناء",
    "وظائف-الرعاية-الصحية",
    "وظائف-خدمة-المنازل-والمكاتب",
)
_ADWHIT_CITIES = (
    "gaziantep",
    "bursa",
    "antalya",
    "mersin",
    "trabzon",
    "sakarya",
    "kocaeli",
    "ankara",
    "izmir",
    "hatay",
    "konya",
    "yalova",
    "sanliurfa",
    "adana",
)
_NEXT_DATA_RE = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S)


def _extract_next_data(html_text: str) -> dict:
    m = _NEXT_DATA_RE.search(html_text)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def scrape_adwhit_web(client: httpx.Client, max_pages: int = 8) -> list[Job]:
    """Scrape structured active jobs from Adwhit main listings, Turkish provinces, and categories via Next.js SSR JSON."""
    jobs: list[Job] = []
    seen_ids: set[str] = set()
    shift_map = {"fulltime": "دوام كامل", "parttime": "دوام جزئي", "freelance": "عمل حر"}
    today_str = datetime.now(timezone.utc).date().isoformat()
    main_pages = min(max_pages, 8)
    feed_urls = (
        [
            f"https://www.adwhit.com/ar/jobs?page={page}" if page > 1 else "https://www.adwhit.com/ar/jobs"
            for page in range(1, main_pages + 1)
        ]
        + [f"https://www.adwhit.com/ar/jobs?city={city}" for city in _ADWHIT_CITIES]
        + [
            "https://www.adwhit.com/ar/jobs?city=gaziantep&page=2",
            "https://www.adwhit.com/ar/jobs?city=bursa&page=2",
        ]
        + [f"https://www.adwhit.com/ar/{quote(slug)}" for slug in _ADWHIT_EXTRA_CATS]
    )

    pending_items: list[tuple[dict, str, str]] = []
    for list_url in feed_urls:
        try:
            r = client.get(list_url, headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                continue
            items = _extract_next_data(_decode_html(r)).get("props", {}).get("pageProps", {}).get("data", {}).get("jobs") or []
            if not items:
                continue
        except Exception as e:
            log.warning("adwhit list %s failed: %s", list_url, e)
            continue

        for it in items:
            jid = str(it.get("id") or "")
            slug = it.get("url") or ""
            if not jid or not slug or jid in seen_ids:
                continue
            seen_ids.add(jid)

            added_ts = int(it.get("added") or 0)
            posted_at = (
                datetime.fromtimestamp(added_ts, tz=timezone.utc).isoformat(timespec="seconds")
                if added_ts else datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
            if not _is_recent_iso(posted_at, max_days=config.FORMAL_DAYS):
                continue

            preview_text = f"{it.get('title') or ''} {it.get('summary') or ''}"
            if spam.is_spam(preview_text) or spam.is_tr_citizenship_only(preview_text):
                continue

            pending_items.append((it, posted_at, f"https://www.adwhit.com/ar/{slug}"))

    def _fetch_detail(entry: tuple[dict, str, str], http_c: httpx.Client) -> Job | None:
        it, posted_at, detail_url = entry
        try:
            dr = http_c.get(detail_url, headers=_HEADERS, timeout=18, follow_redirects=True)
            if dr.status_code != 200:
                return None
            jdata = _extract_next_data(_decode_html(dr)).get("props", {}).get("pageProps", {}).get("data", {}).get("job") or {}
            dsc_html = jdata.get("dsc") or it.get("summary") or ""
            dsc_soup = BeautifulSoup(dsc_html, "html.parser")
            dsc_txt = dsc_soup.get_text("\n", strip=True)

            wa_phones: list[str] = []
            if jdata.get("contactPhone"):
                wa_phones.extend(tx.phones_tr(str(jdata["contactPhone"])))
            found_email = str(jdata.get("contactEmail") or "").strip() or None
            for el in dsc_soup.select("[data-cfemail]"):
                cand = da._decode_cf_email(el.get("data-cfemail") or "")
                if not found_email and da._good_email(cand):
                    found_email = cand
            for a in dsc_soup.select("a[href*='whatsapp'], a[href*='wa.me'], a[href^='tel:'], a[href^='mailto:'], a[href*='email-protection']"):
                href = a.get("href") or ""
                low_h = href.lower()
                if low_h.startswith("tel:"):
                    wa_phones.extend(tx.phones_tr(href[4:]))
                elif "/cdn-cgi/l/email-protection#" in low_h:
                    cand = da._decode_cf_email(href.split("#", 1)[1].split("?", 1)[0])
                    if not found_email and da._good_email(cand):
                        found_email = cand
                elif low_h.startswith("mailto:"):
                    cand = href[7:].split("?", 1)[0].strip()
                    if not found_email and da._good_email(cand):
                        found_email = cand
                else:
                    wa_phones.extend(da.extract_phone_from_wa(href, country="tr"))

            c_name = (jdata.get("city") or it.get("city") or {}).get("name") or ""
            a_name = (jdata.get("area") or it.get("area") or {}).get("name") or ""
            cat_name = (jdata.get("cat") or it.get("cat") or {}).get("name") or ""
            extra_tags = ["عن بعد"] if (jdata.get("jobLocation") == "remote" or it.get("jobLocation") == "remote") else []
            ext_apply: str | None = None
            raw_cu = da.strip_tracking(str(jdata.get("contactUrl") or "").strip())
            if raw_cu and not da.is_disallowed_offline_url(raw_cu) and da.is_direct_form_or_ats(raw_cu):
                ext_apply = raw_cu

            job = _build_tr_job(
                title=jdata.get("title") or it.get("title") or "",
                source="adwhit_web",
                post_url=detail_url,
                posted_at=posted_at,
                body_text=dsc_txt,
                city_hint=f"{c_name} {a_name}",
                shift_hint=shift_map.get(jdata.get("jobType") or it.get("jobType") or ""),
                apply_url=ext_apply,
                details_url=detail_url,
                phones=wa_phones,
                email=found_email,
                tags=extra_tags + ([cat_name] if cat_name and cat_name != "متنوعة" else []),
            )
            if job and ex.expires_on(job) >= today_str:
                return job
            return None
        except Exception as e:
            log.debug("adwhit detail %s failed: %s", detail_url, e)
            return None

    if isinstance(client, httpx.Client) and len(pending_items) > 6:
        tl = threading.local()
        lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _worker(entry: tuple[dict, str, str]) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers=_HEADERS, timeout=18, verify=False, follow_redirects=True)
                tl.client = c
                with lock:
                    pool_clients.append(c)
            return _fetch_detail(entry, c)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for j in pool.map(_worker, pending_items):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
    else:
        for entry in pending_items:
            j = _fetch_detail(entry, client)
            if j is not None:
                jobs.append(j)
    return jobs


def _salam_jsonld(soup: BeautifulSoup) -> dict:
    for sc in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(sc.get_text(strip=True))
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return data
        except Exception:
            continue
    return {}


def scrape_salamjobs_tr(client: httpx.Client, max_pages: int = 8) -> list[Job]:
    """Scrape Turkey jobs from salamjobs.com across country and all 10 Turkey cities with parallel detail fetching."""
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    ist_pages = min(5, max_pages)
    gaz_pages = min(3, max_pages)
    feed_urls = (
        [f"https://salamjobs.com/search?country_id=1&page={page}" for page in range(1, max_pages + 1)]
        + [f"https://salamjobs.com/search?country_id=1&city_id=4&page={page}" for page in range(1, ist_pages + 1)]
        + [f"https://salamjobs.com/search?country_id=1&city_id=8&page={page}" for page in range(1, gaz_pages + 1)]
        + [f"https://salamjobs.com/search?country_id=1&city_id={cid}" for cid in (2, 10, 3, 11, 1, 6, 7, 9)]
        + [f"https://salamjobs.com/search?category_id={cat_id}" for cat_id in (2, 7, 13, 12, 8, 18, 21, 3, 4, 11, 10, 14, 6, 9, 17)]
    )

    all_links: list[str] = []
    for list_url in feed_urls:
        try:
            r = client.get(list_url, headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                continue
        except Exception as e:
            log.warning("salamjobs list %s failed: %s", list_url, e)
            continue

        soup = BeautifulSoup(_decode_html(r), "html.parser")
        for a in soup.select('a[href*="/jobs/"]'):
            u = urljoin("https://salamjobs.com", (a.get("href") or "").split("?", 1)[0])
            if re.search(r"/jobs/[^/]+-\d+$", u) and u not in seen_urls:
                seen_urls.add(u)
                all_links.append(u)

    def _fetch_detail(u: str, http_c: httpx.Client) -> Job | None:
        try:
            dr = http_c.get(u, headers=_HEADERS, timeout=18, follow_redirects=True)
            if dr.status_code != 200:
                return None
            ds = BeautifulSoup(_decode_html(dr), "html.parser")
            ld = _salam_jsonld(ds)
            posted_raw = str(ld.get("datePosted") or "")
            posted_at = posted_raw[:19] + "+00:00" if len(posted_raw) >= 19 else datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not _is_recent_iso(posted_at):
                return None

            # Strip similar-jobs cards & nav before reading contact info
            art = ds.select_one("article.min-w-0")
            if not art:
                return None
            for noise in art.select(".sj-card, .similar-jobs, .related-jobs, header, footer, nav"):
                noise.decompose()

            desc_el = art.select_one(".job-description")
            desc_txt = desc_el.get_text("\n", strip=True) if desc_el else BeautifulSoup(str(ld.get("description") or ""), "html.parser").get_text("\n", strip=True)
            title = ld.get("title") or (art.select_one("h1").get_text(" ", strip=True) if art.select_one("h1") else "")

            if spam.is_tr_citizenship_only(f"{title}\n{desc_txt}") or spam.is_spam(f"{title}\n{desc_txt}"):
                return None

            phones: list[str] = []
            emails: list[str] = []
            out_url = None
            apply_url = None
            for el in art.select("[data-cfemail]"):
                em = da._decode_cf_email(el.get("data-cfemail") or "")
                if da._good_email(em) and em not in emails:
                    emails.append(em)
            for a in art.select("a[href]"):
                href = (a.get("href") or "").strip()
                low = href.lower()
                if low.startswith("tel:"):
                    phones.extend(tx.phones_tr(href[4:]))
                elif "whatsapp" in low or "wa.me/" in low:
                    phones.extend(da.extract_phone_from_wa(href, country="tr"))
                elif "/cdn-cgi/l/email-protection#" in low:
                    em = da._decode_cf_email(href.split("#", 1)[1].split("?", 1)[0])
                    if da._good_email(em) and em not in emails:
                        emails.append(em)
                elif low.startswith("mailto:"):
                    em = href[7:].split("?", 1)[0].strip()
                    if da._good_email(em):
                        emails.append(em)
                elif "/out/jobs/" in low and not out_url:
                    out_url = urljoin("https://salamjobs.com", href)

            # Only follow /out/jobs/ if no direct phone/email was already found inside the card
            if not phones and not emails and out_url:
                try:
                    ro = http_c.get(out_url, headers=_HEADERS, timeout=10, follow_redirects=True)
                    final_u = da.strip_tracking(str(ro.url))
                    if da.is_disallowed_offline_url(final_u):
                        return None
                    if ro.status_code == 200:
                        out_html = _decode_html(ro)
                        if "adwhit.com" in final_u.lower():
                            jdata = _extract_next_data(out_html).get("props", {}).get("pageProps", {}).get("data", {}).get("job") or {}
                            if jdata.get("contactPhone"):
                                phones.extend(tx.phones_tr(str(jdata["contactPhone"])))
                            if jdata.get("contactEmail") and da._good_email(str(jdata["contactEmail"])):
                                emails.append(str(jdata["contactEmail"]).strip())
                            dsc_sp = BeautifulSoup(jdata.get("dsc") or "", "html.parser")
                            for ca in dsc_sp.select("a[href*='whatsapp'], a[href*='wa.me'], a[href^='tel:'], a[href^='mailto:']"):
                                href_c = (ca.get("href") or "").strip()
                                low_c = href_c.lower()
                                if low_c.startswith("tel:"):
                                    phones.extend(tx.phones_tr(href_c[4:]))
                                elif low_c.startswith("mailto:"):
                                    em_c = href_c[7:].split("?", 1)[0].strip()
                                    if da._good_email(em_c):
                                        emails.append(em_c)
                                else:
                                    phones.extend(da.extract_phone_from_wa(href_c, country="tr"))
                            if not phones and not emails:
                                return None
                        else:
                            info = da.extract_apply_from_html(out_html, final_u, country="tr")
                            phones.extend(info.get("phones") or [])
                            if info.get("email"):
                                emails.append(info["email"])
                            apply_url = info.get("apply_url") or (final_u if (info.get("has_internal_form") or da.is_direct_form_or_ats(final_u)) else None)
                except Exception:
                    pass

            org_name = ((ld.get("hiringOrganization") or {}).get("name") if isinstance(ld.get("hiringOrganization"), dict) else None) or None
            loc = ""
            if isinstance(ld.get("jobLocation"), dict):
                addr = ld["jobLocation"].get("address") or {}
                loc = f"{ld['jobLocation'].get('name', '')} {addr.get('addressLocality', '')}"
            sal = None
            if isinstance(ld.get("baseSalary"), dict):
                val = (ld["baseSalary"].get("value") or {})
                if isinstance(val, dict) and (val.get("value") or val.get("minValue")):
                    amt = val.get("value") or f"{val.get('minValue')}-{val.get('maxValue', '')}"
                    sal = f"{amt} {ld['baseSalary'].get('currency', 'TL')}".strip()

            job = _build_tr_job(
                title=title,
                source="salamjobs",
                post_url=u,
                posted_at=posted_at,
                body_text=desc_txt,
                employer=org_name,
                city_hint=loc,
                salary_hint=sal,
                deadline=str(ld.get("validThrough") or "")[:10] or None,
                apply_url=apply_url,
                details_url=u,
                phones=phones,
                email=emails[0] if emails else None,
                tags=["تركيا"],
            )
            if job and ex.expires_on(job) >= today_str:
                return job
            return None
        except Exception as e:
            log.debug("salamjobs detail %s failed: %s", u, e)
            return None

    if isinstance(client, httpx.Client) and len(all_links) > 6:
        tl = threading.local()
        lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _worker(u: str) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers=_HEADERS, timeout=18, verify=False, follow_redirects=True)
                tl.client = c
                with lock:
                    pool_clients.append(c)
            return _fetch_detail(u, c)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for j in pool.map(_worker, all_links):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
    else:
        for u in all_links:
            j = _fetch_detail(u, client)
            if j is not None:
                jobs.append(j)
    return jobs


_WAZIFTURK_PATHS = (
    "/search",
    "/jobs/istanbul",
    "/jobs/ankara",
    "/jobs/gaziantep",
    "/jobs/mersin",
    "/jobs/bursa",
    "/jobs/antalya",
    "/jobs/izmir",
    "/jobs/konya",
    "/jobs/kocaeli",
    "/jobs/adana",
    "/jobs/category/sales-marketing",
    "/jobs/category/accounting-finance",
    "/jobs/category/technology",
    "/jobs/category/customer-service",
    "/jobs/category/restaurants-hotels",
    "/jobs/category/transport-logistics",
    "/jobs/category/education",
    "/jobs/category/translation-languages",
)
_WAZIF_TS_RE = re.compile(r'\\?"(postedAt|applicationDeadline|expiresAt)\\?"\s*:\s*\\?"(\d{10,13})\\?"')
_WAZIF_CITY_RE = re.compile(r'\\?"city\\?"\s*:\s*\\?"([^"\\]{2,40})\\?"')


def scrape_wazifturk_tr(client: httpx.Client) -> list[Job]:
    """Scrape active Turkey jobs from wazifturk.com ONLY when direct contact (phone/email/external form) exists without login."""
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    now_dt = datetime.now(timezone.utc)
    today_str = now_dt.date().isoformat()

    for path in _WAZIFTURK_PATHS:
        try:
            r = client.get(f"https://wazifturk.com{path}", headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                continue
            sp = BeautifulSoup(_decode_html(r), "html.parser")
            for a in sp.select("a[href*='/job/']"):
                u = urljoin("https://wazifturk.com", (a.get("href") or "").split("?", 1)[0]).rstrip("/")
                if re.search(r"wazifturk\.com/(?:(?:ar|en|tr)/)?job/\d+", u, re.I) and u not in seen_urls:
                    seen_urls.add(u)
        except Exception as e:
            log.debug("wazifturk list %s failed: %s", path, e)

    for u in list(seen_urls):
        try:
            dr = client.get(u, headers=_HEADERS, timeout=18, follow_redirects=True)
            if dr.status_code != 200:
                continue
            raw_html = _decode_html(dr)
            ts_map: dict[str, str] = {}
            for k, val in _WAZIF_TS_RE.findall(raw_html):
                sec = int(val) / (1000.0 if len(val) >= 12 else 1.0)
                dt = datetime.fromtimestamp(sec, tz=timezone.utc)
                ts_map[k] = dt.isoformat(timespec="seconds") if k == "postedAt" else dt.date().isoformat()

            posted_at = ts_map.get("postedAt") or now_dt.isoformat(timespec="seconds")
            deadline = ts_map.get("applicationDeadline") or ts_map.get("expiresAt")
            if deadline and deadline < today_str:
                continue
            if not _is_recent_iso(posted_at, max_days=config.FORMAL_DAYS):
                continue

            ds = BeautifulSoup(raw_html, "html.parser")
            main = ds.select_one("main") or ds
            for noise in main.select("nav, footer, header, script, style, aside"):
                noise.decompose()
            txt = main.get_text("\n", strip=True)
            title = (main.select_one("h1").get_text(" ", strip=True) if main.select_one("h1") else "")
            if not title and ds.title:
                title = ds.title.get_text(" ", strip=True).split("|", 1)[0].split("–", 1)[0]
            if not title or spam.is_spam(f"{title}\n{txt}") or spam.is_tr_citizenship_only(f"{title}\n{txt}"):
                continue

            phones: list[str] = tx.phones_tr(txt)
            emails: list[str] = [
                e for e in tx.emails(txt)
                if da._good_email(e) and "wazifturk" not in e.lower()
            ]
            ext_apply: str | None = None
            for el in main.select("[data-cfemail]"):
                em = da._decode_cf_email(el.get("data-cfemail") or "")
                if da._good_email(em) and "wazifturk" not in em.lower():
                    emails.append(em)
            for a in main.select("a[href]"):
                href = (a.get("href") or "").strip()
                low = href.lower()
                if low.startswith("tel:"):
                    phones.extend(tx.phones_tr(href[4:]))
                elif "wa.me/" in low or "whatsapp" in low:
                    phones.extend(da.extract_phone_from_wa(href, country="tr"))
                elif "/cdn-cgi/l/email-protection#" in low:
                    em = da._decode_cf_email(href.split("#", 1)[1].split("?", 1)[0])
                    if da._good_email(em) and "wazifturk" not in em.lower():
                        emails.append(em)
                elif low.startswith("mailto:"):
                    em = href[7:].split("?", 1)[0].strip()
                    if da._good_email(em) and "wazifturk" not in em.lower():
                        emails.append(em)
                elif low.startswith(("http://", "https://")) and not any(
                    bad in low for bad in ("wazifturk.com", "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com", "t.me/", "wa.me/")
                ):
                    cu = da.strip_tracking(href)
                    if cu and not da.is_disallowed_offline_url(cu) and da.is_direct_form_or_ats(cu):
                        ext_apply = cu
                        break

            phones = sorted(set(phones))
            emails = list(dict.fromkeys(emails))
            # Enforce direct-apply-only rule: never add login-gated SignIn postings
            if not phones and not emails and not ext_apply:
                continue

            cm = _WAZIF_CITY_RE.search(raw_html)
            city_hint = cm.group(1) if cm else ""
            job = _build_tr_job(
                title=title,
                source="wazifturk",
                post_url=u,
                posted_at=posted_at,
                body_text=txt,
                city_hint=city_hint,
                deadline=deadline,
                apply_url=ext_apply,
                details_url=None,
                phones=phones,
                email=emails[0] if emails else None,
                tags=["تركيا"],
            )
            if job and ex.expires_on(job) >= today_str:
                jobs.append(job)
        except Exception as e:
            log.debug("wazifturk detail %s failed: %s", u, e)
    return jobs


def scrape_sened_tr(client: httpx.Client) -> list[Job]:
    """Scrape Turkey NGO vacancies published on sened.ngo/ar/jobs."""
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get("https://sened.ngo/ar/jobs", headers=_HEADERS, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(_decode_html(r), "html.parser")
        for a in soup.find_all("a", href=True):
            href = da.strip_tracking(a["href"].strip())
            if not href or href in seen:
                continue
            if not re.search(r"airtable\.com/(?:app|shr)|forms\.gle|docs\.google\.com/forms|kobotoolbox\.org|sened\.org\.tr", href, re.I):
                continue
            seen.add(href)
            h = a.find_previous(["h1", "h2", "h3", "h4"])
            raw_t = ((h.get_text(" ", strip=True) if h else "") or a.get_text(" ", strip=True)).replace("- Airtable", "").strip(" /-–|")
            card = a.find_parent(["section", "article"]) or a.find_parent("div")
            card_txt = card.get_text(" ", strip=True) if card else raw_t
            tr_cities = tx.tr_cities_from_text(raw_t) or tx.tr_cities_from_text(card_txt[:220])
            if not tr_cities or tx.cities_from_text(raw_t):
                continue
            if re.search(r"\b202[0-5]\b", raw_t + " " + card_txt[:200]):
                continue
            job = _build_tr_job(
                title=raw_t,
                source="sened",
                post_url="https://sened.ngo/ar/jobs",
                posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                body_text=card_txt,
                employer="منظمة سند (Sened)",
                city_hint=" ".join(tr_cities),
                apply_url=href,
                details_url="https://sened.ngo/ar/jobs",
                sector="ngo",
                tags=["منظمات إنسانية", "سند"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("sened TR scrape failed: %s", e)
    return jobs


_ABILAN_HEADERS = {
    **_HEADERS,
    "Accept": "application/json, text/html, */*;q=0.8",
    "Accept-Language": "tr,en-US;q=0.9,en;q=0.8",
}
_ABILAN_TENDER_RE = re.compile(r"\b(?:ihale|teklif|rfq|rfp|itb|hizmet alımı|mal alımı|satın alma|tedarik)\b", re.I)


def scrape_abilan_tr(client: httpx.Client, max_items: int = 140) -> list[Job]:
    """Scrape active humanitarian/NGO vacancies in Turkey from ab-ilan.com WP REST API + detail fields."""
    jobs: list[Job] = []
    today_str = datetime.now(timezone.utc).date().isoformat()
    items: list[dict] = []
    pages = (1,) if max_items <= 40 else (1, 2)
    for page in pages:
        api_u = (
            "https://ab-ilan.com/wp-json/wp/v2/job?per_page=100"
            if page == 1
            else f"https://ab-ilan.com/wp-json/wp/v2/job?per_page=100&page={page}"
        )
        try:
            r = client.get(api_u, headers=_ABILAN_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                break
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
        except Exception as e:
            if page == 1:
                log.warning("ab-ilan WP API fetch failed: %s", e)
            break
    if not items:
        return []

    candidates: list[tuple[dict, str, str, str, str]] = []
    seen_links: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        link = da.strip_tracking(str(it.get("link") or ""))
        if not link or link in seen_links:
            continue
        posted_raw = str(it.get("date_gmt") or it.get("date") or "")
        posted_at = (posted_raw[:19] + "+00:00") if len(posted_raw) >= 19 else datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not _is_recent_iso(posted_at, max_days=config.FORMAL_DAYS):
            continue
        raw_title_html = (it.get("title") or {}).get("rendered") or ""
        title = BeautifulSoup(raw_title_html, "html.parser").get_text(" ", strip=True)
        content_html = (it.get("content") or {}).get("rendered") or ""
        content_text = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)
        if not title or _ABILAN_TENDER_RE.search(f"{title}\n{content_text}"):
            continue
        if spam.is_spam(f"{title}\n{content_text}") or spam.is_tr_citizenship_only(f"{title}\n{content_text}"):
            continue
        seen_links.add(link)
        candidates.append((it, link, posted_at, title, content_text))
        if len(candidates) >= max_items:
            break

    def _fetch_detail(entry: tuple[dict, str, str, str, str], http_c: httpx.Client) -> Job | None:
        _it, link, posted_at, title, content_text = entry
        try:
            dr = http_c.get(link, headers=_ABILAN_HEADERS, timeout=18, follow_redirects=True)
            if dr.status_code != 200:
                return None
            dhtml = _decode_html(dr)
            ds = BeautifulSoup(dhtml, "html.parser")

            emp: str | None = None
            deadline: str | None = None
            city_hints: list[str] = []
            apply_u: str | None = None
            emails: list[str] = []

            for blk in ds.select(".tdb_single_custom_field"):
                cls = " ".join(blk.get("class") or [])
                txt_el = blk.select_one(".tdb-sacff-txt")
                txt_val = txt_el.get_text(" ", strip=True) if txt_el else blk.get_text(" ", strip=True)
                if "tdb-sacff-type-date_picker" in cls and not deadline:
                    dm = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt_val)
                    if dm:
                        dd, mm, yyyy = dm.group(1), dm.group(2), dm.group(3)
                        try:
                            dt_d = datetime.strptime(f"{yyyy}-{mm}-{dd}", "%Y-%m-%d").date()
                            dl_iso = dt_d.isoformat()
                            if dl_iso < today_str:
                                return None
                            deadline = dl_iso
                        except ValueError:
                            pass
                elif "tdb-sacff-type-select" in cls and txt_val:
                    if txt_val not in city_hints:
                        city_hints.append(txt_val)
                elif "tdb-sacff-type-url" in cls and not apply_u:
                    a_tag = blk.select_one("a[href]")
                    if a_tag:
                        cu = da.strip_tracking((a_tag.get("href") or "").strip())
                        if cu.startswith(("http://", "https://")) and not da.is_disallowed_offline_url(cu) and not da.is_strict_portal_wrapper(cu):
                            apply_u = cu
                elif "tdb-sacff-type-email" in cls:
                    for el in blk.select("[data-cfemail]"):
                        cand = da._decode_cf_email(el.get("data-cfemail") or "")
                        if da._good_email(cand) and cand not in emails:
                            emails.append(cand)
                    for a_tag in blk.select("a[href]"):
                        href = (a_tag.get("href") or "").strip()
                        low = href.lower()
                        if "/cdn-cgi/l/email-protection#" in low:
                            cand = da._decode_cf_email(href.split("#", 1)[1].split("?", 1)[0])
                            if da._good_email(cand) and cand not in emails:
                                emails.append(cand)
                        elif low.startswith("mailto:"):
                            cand = href[7:].split("?", 1)[0].strip()
                            if da._good_email(cand) and cand not in emails:
                                emails.append(cand)
                elif "tdb-sacff-type-text" in cls and not emp and txt_val:
                    full_b = blk.get_text(" ", strip=True)
                    if not re.search(r"referans|referans numaras|ref\s*[:：]", full_b, re.I) and len(txt_val) >= 2:
                        emp = txt_val

            info = da.extract_apply_from_html(dhtml, link, country="tr")
            if info.get("closed"):
                return None
            if not apply_u and info.get("apply_url") and not da.is_strict_portal_wrapper(info["apply_url"]):
                apply_u = info["apply_url"]
            if not emails and info.get("email"):
                emails.append(info["email"])
            phones = info.get("phones") or []
            if not apply_u and not emails and not phones:
                return None

            body_for_job = content_text
            if deadline:
                body_for_job = f"{content_text}\nSon Başvuru Tarihi: {deadline}".strip()

            job = _build_tr_job(
                title=title,
                source="abilan_tr",
                post_url=link,
                posted_at=posted_at,
                body_text=body_for_job,
                employer=emp,
                city_hint=" ".join(city_hints),
                deadline=deadline,
                apply_url=apply_u,
                details_url=link,
                phones=phones,
                email=emails[0] if emails else None,
                sector="ngo",
                tags=["منظمات إنسانية", "تركيا"],
            )
            if job and ex.expires_on(job) >= today_str:
                return job
            return None
        except Exception as e:
            log.debug("ab-ilan detail %s failed: %s", link, e)
            return None

    if isinstance(client, httpx.Client) and len(candidates) > 4:
        tl = threading.local()
        lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _worker(entry: tuple[dict, str, str, str, str]) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers=_ABILAN_HEADERS, timeout=18, verify=False, follow_redirects=True)
                tl.client = c
                with lock:
                    pool_clients.append(c)
            return _fetch_detail(entry, c)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for j in pool.map(_worker, candidates):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
    else:
        for entry in candidates:
            j = _fetch_detail(entry, client)
            if j is not None:
                jobs.append(j)
    return jobs


def fetch_all_tr_portal_jobs(
    client: httpx.Client | None = None,
    cache: dict | None = None,
    max_pages: int = 8,
    quick: bool = False,
) -> list[Job]:
    """Fetch verified active jobs across all reliable Turkey web portals."""
    from .web_portals import (
        scrape_acted,
        scrape_drc,
        scrape_impactpool_turkey,
        scrape_unjobnet_turkey,
        scrape_wfp,
    )

    close_after = client is None
    cli = client or httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS)
    jobs: list[Job] = []
    try:
        for name, fn in (
            ("jobsintr_web", lambda c: scrape_jobsintr_web(c, max_pages=min(max_pages, 8), extra_limit=14 if quick else None)),
            ("adwhit_web", lambda c: scrape_adwhit_web(c, max_pages=min(max_pages, 8))),
            ("salamjobs", lambda c: scrape_salamjobs_tr(c, max_pages=max_pages)),
            ("wazifturk", scrape_wazifturk_tr),
            ("sened_tr", scrape_sened_tr),
            ("abilan_tr", lambda c: scrape_abilan_tr(c, max_items=35 if quick else 140)),
            ("unjobnet_tr", scrape_unjobnet_turkey),
            ("impactpool_tr", scrape_impactpool_turkey),
            ("drc_tr", lambda c: scrape_drc(c, country="tr")),
            ("acted_tr", lambda c: scrape_acted(c, country="tr")),
            ("wfp_tr", lambda c: scrape_wfp(c, country="tr")),
        ):
            try:
                res = fn(cli)
                log.info("TR portal %s yielded %d jobs", name, len(res))
                jobs.extend(res)
            except Exception as e:
                log.warning("TR portal %s failed: %s", name, e)
        res_cache = cache if cache is not None else {}
        return [j for j in jobs if da.resolve_job_apply(j, client=cli, cache=res_cache)]
    finally:
        if close_after:
            cli.close()
