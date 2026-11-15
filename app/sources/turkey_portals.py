"""Verified job-portal scrapers for Arabic-friendly & international jobs in Turkey."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
import logging
import re
from urllib.parse import urljoin

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
        details_url=details_url or post_url,
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


def scrape_jobsintr_web(client: httpx.Client, max_pages: int = 4) -> list[Job]:
    """Scrape active Arabic & Turkish listings from jobsintr.net with direct forms/WhatsApp."""
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    now_dt = datetime.now(timezone.utc)

    for page in range(1, max_pages + 1):
        list_url = f"https://jobsintr.net/jobs?page={page}" if page > 1 else "https://jobsintr.net/jobs"
        try:
            r = client.get(list_url, headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                break
        except Exception as e:
            log.warning("jobsintr list p%d failed: %s", page, e)
            break

        soup = BeautifulSoup(_decode_html(r), "html.parser")
        page_links = []
        for a in soup.select('a[href*="/jobs/"]'):
            href = (a.get("href") or "").split("?", 1)[0].rstrip("/")
            if not re.fullmatch(r"https?://(?:www\.)?jobsintr\.net/jobs/[A-Za-z0-9_-]+", href):
                continue
            if href not in seen_urls:
                seen_urls.add(href)
                page_links.append(href)
        if not page_links:
            break

        recent_on_page = 0
        for u in page_links:
            try:
                dr = client.get(u, headers=_HEADERS, timeout=18, follow_redirects=True)
                if dr.status_code != 200:
                    continue
                dhtml = _decode_html(dr)
                ds = BeautifulSoup(dhtml, "html.parser")
                header_el = ds.select_one(".job-header-content")
                header_txt = header_el.get_text(" ", strip=True) if header_el else ""
                posted_at, is_recent = _parse_relative_ar_date(header_txt, now_dt=now_dt)
                if not is_recent:
                    continue
                recent_on_page += 1

                h1_el = header_el.select_one("h1, h2") if header_el else None
                raw_title = (h1_el.get_text(" ", strip=True) if h1_el else re.sub(r"\s*منذ\s+.+$", "", header_txt)).strip()
                if not raw_title:
                    og = ds.select_one('meta[property="og:title"]')
                    raw_title = og.get("content", "") if og else ""

                content_node = ds.select_one(".ck-content") or ds.select_one(".content-single")
                if not content_node:
                    continue
                overview_node = ds.select_one(".job-overview")
                overview_txt = overview_node.get_text("\n", strip=True) if overview_node else ""
                body_lines = content_node.get_text("\n", strip=True)
                full_text = f"{body_lines}\n{overview_txt}".strip() if overview_txt else body_lines

                info = da.extract_apply_from_html(str(content_node), u, country="tr")
                apply_u = info.get("apply_url")
                if not apply_u and da._has_application_form(ds):
                    apply_u = u

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
                if job:
                    jobs.append(job)
            except Exception as e:
                log.debug("jobsintr detail %s failed: %s", u, e)
    return jobs


def scrape_adwhit_web(client: httpx.Client, max_pages: int = 4) -> list[Job]:
    """Scrape structured active jobs from Adwhit via Next.js SSR JSON."""
    jobs: list[Job] = []
    seen_ids: set[str] = set()
    shift_map = {"fulltime": "دوام كامل", "parttime": "دوام جزئي", "freelance": "عمل حر"}

    for page in range(1, max_pages + 1):
        list_url = f"https://www.adwhit.com/ar/jobs?page={page}" if page > 1 else "https://www.adwhit.com/ar/jobs"
        try:
            r = client.get(list_url, headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                break
            nd_el = BeautifulSoup(_decode_html(r), "html.parser").select_one("#__NEXT_DATA__")
            if not nd_el:
                break
            items = json.loads(nd_el.get_text()).get("props", {}).get("pageProps", {}).get("data", {}).get("jobs") or []
            if not items:
                break
        except Exception as e:
            log.warning("adwhit list p%d failed: %s", page, e)
            break

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
            if not _is_recent_iso(posted_at):
                continue

            preview_text = f"{it.get('title') or ''} {it.get('summary') or ''}"
            if spam.is_spam(preview_text) or spam.is_tr_citizenship_only(preview_text):
                continue

            detail_url = f"https://www.adwhit.com/ar/{slug}"
            try:
                dr = client.get(detail_url, headers=_HEADERS, timeout=18, follow_redirects=True)
                if dr.status_code != 200:
                    continue
                dhtml = _decode_html(dr)
                ds = BeautifulSoup(dhtml, "html.parser")
                dnd = ds.select_one("#__NEXT_DATA__")
                if not dnd:
                    continue
                jdata = json.loads(dnd.get_text()).get("props", {}).get("pageProps", {}).get("data", {}).get("job") or {}
                dsc_html = jdata.get("dsc") or it.get("summary") or ""
                dsc_txt = BeautifulSoup(dsc_html, "html.parser").get_text("\n", strip=True)

                wa_phones: list[str] = []
                if jdata.get("contactPhone"):
                    wa_phones.extend(tx.phones_tr(str(jdata["contactPhone"])))
                dsc_soup = BeautifulSoup(dsc_html, "html.parser")
                for a in dsc_soup.select("a[href*='whatsapp'], a[href*='wa.me'], a[href^='tel:']"):
                    href = a.get("href") or ""
                    if href.lower().startswith("tel:"):
                        wa_phones.extend(tx.phones_tr(href[4:]))
                    else:
                        wa_phones.extend(da.extract_phone_from_wa(href, country="tr"))

                c_name = (jdata.get("city") or it.get("city") or {}).get("name") or ""
                a_name = (jdata.get("area") or it.get("area") or {}).get("name") or ""
                cat_name = (jdata.get("cat") or it.get("cat") or {}).get("name") or ""
                extra_tags = ["عن بعد"] if (jdata.get("jobLocation") == "remote" or it.get("jobLocation") == "remote") else []

                job = _build_tr_job(
                    title=jdata.get("title") or it.get("title") or "",
                    source="adwhit_web",
                    post_url=detail_url,
                    posted_at=posted_at,
                    body_text=dsc_txt,
                    city_hint=f"{c_name} {a_name}",
                    shift_hint=shift_map.get(jdata.get("jobType") or it.get("jobType") or ""),
                    details_url=detail_url,
                    phones=wa_phones,
                    email=jdata.get("contactEmail"),
                    tags=extra_tags + ([cat_name] if cat_name and cat_name != "متنوعة" else []),
                )
                if job:
                    jobs.append(job)
            except Exception as e:
                log.debug("adwhit detail %s failed: %s", detail_url, e)
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


def scrape_salamjobs_tr(client: httpx.Client, max_pages: int = 4) -> list[Job]:
    """Scrape Turkey jobs from salamjobs.com, decoding UTF-8 strictly & isolating contacts from sidebar."""
    jobs: list[Job] = []
    seen_urls: set[str] = set()

    for page in range(1, max_pages + 1):
        list_url = f"https://salamjobs.com/search?country_id=1&page={page}"
        try:
            r = client.get(list_url, headers=_HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                break
        except Exception as e:
            log.warning("salamjobs p%d failed: %s", page, e)
            break

        soup = BeautifulSoup(_decode_html(r), "html.parser")
        links = []
        for a in soup.select('a[href*="/jobs/"]'):
            u = urljoin("https://salamjobs.com", (a.get("href") or "").split("?", 1)[0])
            if re.search(r"/jobs/[^/]+-\d+$", u) and u not in seen_urls:
                seen_urls.add(u)
                links.append(u)
        if not links:
            break

        for u in links:
            try:
                dr = client.get(u, headers=_HEADERS, timeout=18, follow_redirects=True)
                if dr.status_code != 200:
                    continue
                ds = BeautifulSoup(_decode_html(dr), "html.parser")
                ld = _salam_jsonld(ds)
                posted_raw = str(ld.get("datePosted") or "")
                posted_at = posted_raw[:19] + "+00:00" if len(posted_raw) >= 19 else datetime.now(timezone.utc).isoformat(timespec="seconds")
                if not _is_recent_iso(posted_at):
                    continue

                # Strip similar-jobs cards & nav before reading contact info
                art = ds.select_one("article.min-w-0")
                if not art:
                    continue
                for noise in art.select(".sj-card, .similar-jobs, .related-jobs, header, footer, nav"):
                    noise.decompose()

                desc_el = art.select_one(".job-description")
                desc_txt = desc_el.get_text("\n", strip=True) if desc_el else BeautifulSoup(str(ld.get("description") or ""), "html.parser").get_text("\n", strip=True)
                title = ld.get("title") or (art.select_one("h1").get_text(" ", strip=True) if art.select_one("h1") else "")

                if spam.is_tr_citizenship_only(f"{title}\n{desc_txt}") or spam.is_spam(f"{title}\n{desc_txt}"):
                    continue

                phones: list[str] = []
                emails: list[str] = []
                out_url = None
                apply_url = None
                for a in art.select("a[href]"):
                    href = (a.get("href") or "").strip()
                    low = href.lower()
                    if low.startswith("tel:"):
                        phones.extend(tx.phones_tr(href[4:]))
                    elif "whatsapp" in low or "wa.me/" in low:
                        phones.extend(da.extract_phone_from_wa(href, country="tr"))
                    elif low.startswith("mailto:"):
                        em = href[7:].split("?", 1)[0].strip()
                        if da._good_email(em):
                            emails.append(em)
                    elif "/out/jobs/" in low and not out_url:
                        out_url = urljoin("https://salamjobs.com", href)

                # Only follow /out/jobs/ if no direct phone/email was already found inside the card
                if not phones and not emails and out_url:
                    try:
                        ro = client.get(out_url, headers=_HEADERS, timeout=10, follow_redirects=True)
                        final_u = da.strip_tracking(str(ro.url))
                        if da.is_disallowed_offline_url(final_u):
                            continue
                        if ro.status_code == 200:
                            info = da.extract_apply_from_html(_decode_html(ro), final_u, country="tr")
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
                if job:
                    jobs.append(job)
            except Exception as e:
                log.debug("salamjobs detail %s failed: %s", u, e)
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


def fetch_all_tr_portal_jobs(
    client: httpx.Client | None = None,
    cache: dict | None = None,
    max_pages: int = 3,
) -> list[Job]:
    """Fetch verified active jobs across all reliable Turkey web portals."""
    close_after = client is None
    cli = client or httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS)
    jobs: list[Job] = []
    try:
        for name, fn in (
            ("jobsintr_web", lambda c: scrape_jobsintr_web(c, max_pages=max_pages)),
            ("adwhit_web", lambda c: scrape_adwhit_web(c, max_pages=max_pages)),
            ("salamjobs", lambda c: scrape_salamjobs_tr(c, max_pages=max_pages)),
            ("sened_tr", scrape_sened_tr),
        ):
            try:
                res = fn(cli)
                log.info("TR portal %s yielded %d jobs", name, len(res))
                jobs.extend(res)
            except Exception as e:
                log.warning("TR portal %s failed: %s", name, e)
    finally:
        if close_after:
            cli.close()
    return jobs
