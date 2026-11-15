"""Scrapers for verified Syrian organization & platform web portals: SEMA, Ataa Relief, ReliefWeb, IDA."""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import httpx

from .. import config
from .. import direct_apply as da
from .. import extract as ex
from .. import taxonomy as tx
from ..classify import classify as computer_subfield
from ..models import Job

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
log = logging.getLogger("web_portals")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def scrape_sema(client: httpx.Client) -> list[Job]:
    """Scrape SEMA medical and humanitarian vacancies."""
    url = "https://sema-sy.org/ar/vacancies/"
    jobs: list[Job] = []
    try:
        r = client.get(url, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.find_all("tr")[1:]
        for row in rows:
            tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(tds) < 8:
                continue
            title = tds[0]
            code = tds[1] if len(tds) > 1 else ""
            center = tds[2] if len(tds) > 2 else ""
            gender_txt = tds[5] if len(tds) > 5 else ""
            start_date = tds[7] if len(tds) > 7 else ""
            deadline = tds[8] if len(tds) > 8 else ""

            # Filter out older than 6 months or expired
            if start_date and start_date < "2026-05-01":
                continue
            today_str = datetime.now(timezone.utc).date().isoformat()
            if deadline and deadline < today_str:
                continue

            a_tag = row.select_one("a.link-as-btn[href]") or next(
                (a for a in row.find_all("a", href=True)
                 if not a["href"].strip().lower().startswith(("javascript:", "#"))),
                None,
            )
            if not a_tag:
                continue
            apply_url = urljoin(url, a_tag["href"].strip())

            cities = tx.cities_from_text(center) or ["إدلب"]
            gender = "female" if "أنثى" in gender_txt else "male" if "ذكر" in gender_txt else None
            edu = tx.education_from_text(title) or tx.education_from_title(title) or "institute"

            fld = tx.field_from_text(title, center)
            if fld == "other":
                fld = "health"

            job = Job(
                title=title[:120],
                source="sema",
                post_url=apply_url,
                posted_at=(start_date + "T09:00:00+00:00") if start_date else None,
                employer="الرابطة الطبية للمغتربين السوريين (SEMA)",
                cities=cities,
                education=edu,
                education_from="stated",
                field=fld,
                kind="formal",
                official=False,
                country="sy",
                sector="ngo",
                apply_url=apply_url,
                details_url=apply_url,
                apply_types=["site", "form"],
                deadline=deadline or None,
                gender=gender,
                confidence=0.95,
                tags=["قطاع صحي", "منظمات إنسانية"],
                raw=f"{title} - كود الشاغر: {code} - المركز: {center}",
            )
            jobs.append(job)
    except Exception as e:
        log.warning("SEMA scrape failed: %s", e)
    return jobs


def _is_recent_iso(iso_str: str | None, max_days: int | None = None) -> bool:
    if not iso_str:
        return True
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_days or config.FORMAL_DAYS)).date().isoformat()
    return iso_str[:10] >= cutoff


def _make_wp_job(
    *,
    title: str,
    source: str,
    link: str,
    posted_iso: str | None,
    employer: str,
    content_html: str,
    default_cities: list[str],
    default_field: str = "ngo",
    tags: list[str] | None = None,
    deadline: str | None = None,
    sector: str = "ngo",
) -> Job | None:
    """Build a normalized Job from a WordPress post or custom listing payload."""
    clean_t = ex.normalize_title_display(html.unescape(re.sub(r"<[^>]+>", " ", title or "")), keep_matlob=True)
    if len(clean_t) < 3 or not ex._good_title(clean_t):
        return None
    body_txt = html.unescape(re.sub(r"<[^>]+>", " ", content_html or "")).strip()
    combined = f"{clean_t} {body_txt}"
    title_cities = tx.cities_from_text(clean_t.split("—", 1)[-1] if "—" in clean_t else clean_t)
    cities = title_cities or tx.cities_from_text(combined)
    if re.search(r"في تركيا|غازي عنتاب|إسطنبول|اسطنبول|مرسين|هاتاي|أضنة|أورفا|gaziantep|istanbul|hatay|mersin", combined, re.I) and not cities:
        return None
    if not cities:
        cities = list(default_cities)
    info = da.extract_apply_from_html(content_html, link) if content_html else {}
    email = info.get("email")
    phones = info.get("phones") or []
    apply_u = info.get("apply_url") or (None if (content_html and (email or phones)) else link)
    if not phones and re.search(r"(?:\+?90|0090|wa\.me/90)[\s-]?5\d{2}", combined) and not (apply_u and da.is_direct_form_or_ats(apply_u)) and not email:
        return None
    title_edu = tx.education_from_title(clean_t)
    edu = ("none" if title_edu == "none" else (tx.education_from_text(combined) or title_edu)) or "bachelor"
    fld = tx.field_from_text(clean_t, body_txt[:300])
    if fld == "other":
        fld = default_field
    return Job(
        title=clean_t[:120],
        source=source,
        post_url=link,
        posted_at=posted_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        employer=employer,
        cities=cities,
        education=edu,
        education_from="stated",
        field=fld,
        subfield=computer_subfield(clean_t) if fld == "computer" else None,
        kind="formal",
        official=False,
        country="sy",
        sector=sector,
        apply_url=apply_u,
        details_url=link,
        email=email,
        phones=phones,
        deadline=deadline,
        apply_types=tx.infer_apply_types(apply_u, phones, False, email),
        confidence=0.92,
        tags=tags or ["منظمات إنسانية"],
        raw=f"{clean_t}\n{body_txt[:800]}" if body_txt else clean_t,
    )


def scrape_ataa(client: httpx.Client) -> list[Job]:
    """Scrape Ataa Relief vacancies via WP REST API with HTML category fallback."""
    api_url = "https://ataarelief.org/wp-json/wp/v2/posts?per_page=100"
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get(api_url, timeout=20)
        if r.status_code == 200 and r.text.lstrip().startswith("["):
            for it in r.json():
                raw_t = (it.get("title") or {}).get("rendered") or ""
                clean_t = html.unescape(re.sub(r"<[^>]+>", " ", raw_t)).strip()
                if not re.search(r"إعلان توظيف|اعلان توظيف|شاغر|توظيف|فرصة عمل", clean_t):
                    continue
                posted = (it.get("date_gmt") or it.get("date") or "")[:19]
                posted_iso = (posted + "+00:00") if posted else None
                if not _is_recent_iso(posted_iso):
                    continue
                clean_t = re.sub(r"^(?:إعلان|اعلان)\s+توظيف\s*[–\-:]\s*", "", clean_t).strip()
                link = (it.get("link") or "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                content_html = (it.get("content") or {}).get("rendered") or ""
                job = _make_wp_job(
                    title=clean_t,
                    source="ataa",
                    link=link,
                    posted_iso=posted_iso,
                    employer="جمعية عطاء للإغاثة الإنسانية",
                    content_html=content_html,
                    default_cities=["عدة محافظات"],
                    default_field="ngo",
                    tags=["عمل إنساني", "إغاثة"],
                )
                if job:
                    jobs.append(job)
    except Exception as e:
        log.debug("Ataa WP API failed, trying HTML fallback: %s", e)

    if jobs:
        return jobs

    url = "https://ataarelief.org/category/jobs/"
    try:
        r = client.get(url, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            t = a.get_text().strip()
            if "إعلان توظيف" not in t:
                continue
            clean_title = re.sub(r"^إعلان توظيف\s*[–\-:]\s*", "", t).strip()
            if len(clean_title) < 4 or "في تركيا" in clean_title:
                continue
            link = a["href"]
            job = _make_wp_job(
                title=clean_title,
                source="ataa",
                link=link,
                posted_iso=None,
                employer="جمعية عطاء للإغاثة الإنسانية",
                content_html="",
                default_cities=["عدة محافظات"],
                default_field="ngo",
                tags=["عمل إنساني", "إغاثة"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("Ataa scrape failed: %s", e)
    return jobs


def _ida_ar_title(raw_title: str, link: str) -> str:
    ar_map = {
        "Data Entry": "مدخل بيانات (Data Entry)",
        "Mhpss": "أخصائي دعم نفسي (MHPSS)",
        "Internist": "طبيب أخصائي داخلية",
        "Community Health Worker": "عامل صحة مجتمعية",
        "Procurement Logistics Assistant": "مساعد مشتريات ولوجستيات",
        "Midwife": "قابلة قانونية",
        "Nurse": "ممرض / ممرضة",
        "Nurse Assistant": "مساعد ممرض",
        "X Ray Technician": "فني أشعة",
        "Pediatrcian": "طبيب أطفال",
        "Lab Technician": "فني مخبر وتحاليل",
        "Gp Doctor": "طبيب عام (GP)",
        "Sterilization Technician": "فني تعقيم",
        "Pharmacy Technician": "فني صيدلي",
    }
    clean = html.unescape(raw_title or "").strip()
    if clean:
        return ar_map.get(clean.title(), clean)
    slug = link.rstrip("/").split("/")[-1]
    slug_words = re.sub(r"-\d+$", "", slug).replace("-", " ").title()
    return ar_map.get(slug_words, slug_words)


def scrape_ida(client: httpx.Client) -> list[Job]:
    """Scrape Independent Doctors Association (IDA) vacancies via WP REST API with HTML fallback."""
    api_url = "https://ida-org.com/wp-json/wp/v2/jobs?per_page=100"
    board_url = "https://ida-org.com/jobs-board/"
    jobs: list[Job] = []
    seen_links: set[str] = set()
    entries: list[tuple[str, str, str | None, str]] = []
    try:
        r = client.get(api_url, timeout=20)
        if r.status_code == 200 and r.text.lstrip().startswith("["):
            for it in r.json():
                link = (it.get("link") or "").strip()
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                raw_t = (it.get("title") or {}).get("rendered") or ""
                content_html = (it.get("content") or {}).get("rendered") or ""
                posted = (it.get("date_gmt") or it.get("date") or "")[:19]
                posted_iso = (posted + "+00:00") if posted else None
                entries.append((_ida_ar_title(raw_t, link), link, posted_iso, content_html))
    except Exception as e:
        log.debug("IDA WP API failed, trying HTML fallback: %s", e)

    if not entries:
        try:
            r = client.get(board_url, timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=lambda h: h and "/jobs/" in h):
                    link = a["href"].strip()
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    title = _ida_ar_title(a.get_text(" ", strip=True), link)
                    if title:
                        entries.append((title, link, None, ""))
        except Exception as e:
            log.warning("IDA scrape failed: %s", e)

    for title, link, posted_iso, content_html in entries:
        info = da.extract_apply_from_html(content_html, link) if content_html else {}
        body_text = re.sub(r"<[^>]+>", " ", html.unescape(content_html or ""))
        cities = tx.cities_from_text(f"{title} {body_text}") or ["حلب", "إدلب"]
        edu = tx.education_from_text(title) or tx.education_from_title(title) or "institute"
        fld = tx.field_from_text(title)
        if fld == "other":
            fld = "health"
        email = info.get("email")
        phones = info.get("phones") or []
        apply_u = info.get("apply_url") or (None if (content_html and (email or phones)) else link)
        jobs.append(Job(
            title=title[:120],
            source="ida",
            post_url=link,
            posted_at=posted_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            employer="رابطة الأطباء المستقلين (IDA)",
            cities=cities,
            education=edu,
            education_from="stated",
            field=fld,
            kind="formal",
            official=False,
            country="sy",
            sector="ngo",
            apply_url=apply_u,
            details_url=link,
            email=email,
            phones=phones,
            apply_types=tx.infer_apply_types(apply_u, phones, False, email),
            confidence=0.9,
            tags=["قطاع طبي", "منظمات إنسانية"],
            raw=f"شاغر لدى رابطة الأطباء المستقلين: {title}" + (f"\n{body_text[:400]}" if body_text.strip() else ""),
        ))
    return jobs


_WORKLINK_FOREIGN_LOC_RE = re.compile(
    r"(?:actual\s+job\s+location|work\s+location|موقع\s+العمل\s+الفعلي)\s*[:\-]?\s*"
    r"(?:qatar|uae|dubai|abu\s+dhabi|saudi|riyadh|jeddah|kuwait|oman|bahrain|jordan|turkey|istanbul|gaziantep|"
    r"قطر|الإمارات|دبي|أبوظبي|السعودية|الرياض|جدة|الكويت|عمان|البحرين|الأردن|تركيا|إسطنبول)",
    re.I,
)


def _extract_worklink_ld(soup: BeautifulSoup) -> dict | None:
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.get_text(strip=True) or "{}")
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _parse_worklink_detail(full_url: str, html_text: str, today_str: str) -> Job | None:
    """Parse one WorkLink Syria job page into a verified Job, skipping foreign or expired posts."""
    soup = BeautifulSoup(html_text, "html.parser")
    ld = _extract_worklink_ld(soup)
    if not ld:
        return None
    valid_through = (ld.get("validThrough") or "").strip()[:10]
    if valid_through and valid_through < today_str:
        return None
    title = html.unescape(str(ld.get("title") or "")).strip()
    if len(title) < 3:
        return None
    desc_raw = str(ld.get("description") or "")
    desc_txt = html.unescape(re.sub(r"<[^>]+>", " ", desc_raw)).strip()
    if _WORKLINK_FOREIGN_LOC_RE.search(f"{title} {desc_txt[:500]}"):
        return None

    locs = ld.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    loc_names: list[str] = []
    has_sy = False
    for loc in locs:
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address") or {}
        cc = str(addr.get("addressCountry") or "").upper()
        if cc == "SY":
            has_sy = True
        locality = str(addr.get("addressLocality") or "").strip()
        if locality and locality != "عن بعد":
            loc_names.append(locality)
    if locs and not has_sy and not tx.cities_from_text(" ".join(loc_names) + " " + title):
        return None

    info = da.extract_apply_from_html(html_text, full_url)
    if info.get("closed"):
        return None
    email = info.get("email")
    phones = info.get("phones") or []
    apply_u = info.get("apply_url")
    if not (email or phones or apply_u):
        return None

    org = ld.get("hiringOrganization")
    emp = html.unescape(str(org.get("name") or "")) if isinstance(org, dict) else ""
    emp = emp.strip() or "جهة توظيف عبر WorkLink"
    date_posted = (ld.get("datePosted") or "").strip()[:10]
    posted_at = (date_posted + "T09:00:00+00:00") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_posted) else datetime.now(timezone.utc).isoformat(timespec="seconds")

    city_text = " ".join(loc_names)
    if len(loc_names) >= 10:
        cities = ["عدة محافظات"]
    else:
        cities = tx.cities_from_text(city_text) or tx.cities_from_text(f"{title} {desc_txt[:300]}") or ["دمشق"]

    edu = tx.education_from_text(f"{title} {desc_txt[:800]}") or tx.education_from_title(title) or "bachelor"
    fld = tx.field_from_text(title, desc_txt[:400])
    if "وزارة" in emp or "التنمية الإدارية" in emp or "حكوم" in emp:
        sector, official = "gov", True
        if fld == "other":
            fld = "business"
    elif any(w in emp.lower() for w in ("منظمة", "مؤسسة", "جمعية", "إغاثة", "اللجنة", "الأمم", "ngo", "un ", "relief", "irc", "giz", "care", "mercy")):
        sector, official = "ngo", False
        if fld == "other":
            fld = "ngo"
    else:
        sector, official = "private", False

    sub = computer_subfield(title, desc_txt[:200]) if fld == "computer" else None
    app_types = tx.infer_apply_types(apply_u, phones, False, email)
    return Job(
        title=title[:120],
        source="worklink",
        post_url=full_url,
        posted_at=posted_at,
        employer=emp[:100],
        cities=cities,
        education=edu,
        education_from="stated",
        field=fld,
        subfield=sub,
        kind="formal",
        official=official,
        country="sy",
        sector=sector,
        apply_url=apply_u,
        details_url=full_url,
        email=email,
        phones=phones,
        deadline=valid_through or None,
        apply_types=app_types or ["site"],
        confidence=0.95,
        tags=["شواغر موثقة", "ورك لينك"],
        raw=f"{title} — {emp}\n{desc_txt[:800]}",
    )


def scrape_worklink(client: httpx.Client) -> list[Job]:
    """Scrape verified Syrian vacancies from WorkLink Syria via public sitemap + page fallback."""
    today_str = datetime.now(timezone.utc).date().isoformat()
    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
    jobs: list[Job] = []
    seen_urls: set[str] = set()

    sitemap_urls: list[str] = []
    try:
        sm = client.get("https://worklink.sy/sitemap.xml", timeout=20)
        if sm.status_code == 200 and "<urlset" in sm.text[:400]:
            for u, lm in re.findall(r"<url>\s*<loc>(.*?)</loc>(?:\s*<lastmod>(.*?)</lastmod>)?", sm.text, re.S):
                if "/jobs/" in u and (not lm or lm[:10] >= cutoff_str) and u not in seen_urls:
                    seen_urls.add(u)
                    sitemap_urls.append(u)
    except Exception as e:
        log.debug("WorkLink sitemap fetch failed: %s", e)

    if sitemap_urls:
        tl = threading.local()
        clients_lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _fetch_one(u: str) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15, verify=False, follow_redirects=True)
                tl.client = c
                with clients_lock:
                    pool_clients.append(c)
            try:
                r = c.get(u)
                if r.status_code != 200:
                    return None
                return _parse_worklink_detail(u, r.text, today_str)
            except Exception:
                return None

        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for j in pool.map(_fetch_one, sitemap_urls):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
        if jobs:
            return jobs

    url = "https://worklink.sy/jobs-in-syria"
    try:
        r = client.get(url, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/jobs/" in href and href != "/jobs-in-syria":
                full_url = urljoin("https://worklink.sy", href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                raw_text = a.get_text(separator=" | ", strip=True)
                parts = [p.strip() for p in raw_text.split("|") if p.strip()]
                if not parts:
                    continue
                title = parts[0]
                emp = parts[1] if len(parts) > 1 else "منظمة / جهة شريكة عبر ورك لينك"
                loc = parts[3] if len(parts) > 3 else (parts[2] if len(parts) > 2 else "")
                cities = tx.cities_from_text(loc or title) or ["عدة محافظات"]
                edu = tx.education_from_text(title) or tx.education_from_title(title) or "bachelor"
                fld = tx.field_from_text(title)
                job = Job(
                    title=title[:120],
                    source="worklink",
                    post_url=full_url,
                    posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    employer=emp,
                    cities=cities,
                    education=edu,
                    education_from="stated" if edu else "unknown",
                    field=fld if fld != "other" else "ngo",
                    kind="formal",
                    official=False,
                    country="sy",
                    sector="ngo" if any(w in emp for w in ["منظمة", "مؤسسة", "جمعية", "إغاثة", "شفق", "الشام"]) else "private",
                    apply_url=full_url,
                    details_url=full_url,
                    apply_types=["site", "form"],
                    confidence=0.95,
                    tags=["شواغر موثقة", "ورك لينك"],
                    raw=raw_text,
                )
                jobs.append(job)
    except Exception as e:
        log.warning("WorkLink scrape failed: %s", e)
    return jobs


_NGO_SLUG_EMPLOYER = (
    ("bahar", "منظمة بهار الإنسانية"),
    ("ihsan", "مؤسسة إحسان للإغاثة والتنمية"),
    ("ataa", "جمعية عطاء للإغاثة الإنسانية"),
    ("acted", "منظمة ACTED الدولية"),
    ("giz", "منظمة GIZ الألمانية"),
    ("onsur", "منظمة أنصر (Onsur)"),
    ("dca", "منظمة DanChurchAid (DCA)"),
    ("amal", "منظمة الأمل الإنسانية"),
    ("wsa", "منظمة دعم النساء والشباب (WSA)"),
    ("united-nations", "وكالة الأمم المتحدة (UN)"),
    ("unrwa", "وكالة الأمم المتحدة (UN)"),
    ("solidarites", "منظمة Solidarités International"),
    ("si-", "منظمة Solidarités International"),
)


def scrape_ngosquare(client: httpx.Client) -> list[Job]:
    """Scrape humanitarian & UN vacancies from NGO Square across pages 1-4."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, 5):
        url = f"https://ngosquare.com/jobs/?page={page}" if page > 1 else "https://ngosquare.com/jobs/"
        try:
            r = client.get(url, timeout=20)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            found_on_page = 0
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not (href.startswith("/jobs/") and len(href) > 8 and not any(href.endswith(x) for x in ["/jobs/", "?type=consultancy"])):
                    continue
                link = urljoin("https://ngosquare.com", href)
                if link in seen:
                    continue
                title = a.get_text(strip=True)
                if len(title) <= 3 or any(w in title for w in ["الوظائف", "العربية", "English", "Türkçe", "جميع", "إعادة"]):
                    continue
                seen.add(link)
                found_on_page += 1
                slug = href.split("/")[2] if len(href.split("/")) > 2 else ""
                emp = next((name for key, name in _NGO_SLUG_EMPLOYER if key in slug), "منظمة إنسانية دولية / محلية")
                cities = tx.cities_from_text(title) or (
                    ["حلب"] if "aleppo" in title.lower() else ["دمشق"] if "damascus" in title.lower() else ["عدة محافظات"]
                )
                edu = tx.education_from_text(title) or tx.education_from_title(title) or "bachelor"
                fld = tx.field_from_text(title)
                jobs.append(Job(
                    title=title[:120],
                    source="ngosquare",
                    post_url=link,
                    posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    employer=emp,
                    cities=cities,
                    education=edu,
                    education_from="stated",
                    field=fld if fld != "other" else "ngo",
                    kind="formal",
                    official=False,
                    country="sy",
                    sector="ngo",
                    apply_url=link,
                    details_url=link,
                    apply_types=["site", "form"],
                    confidence=0.95,
                    tags=["قطاع إنساني", "منظمات دولية"],
                    raw=f"{title} - {emp}",
                ))
            if found_on_page == 0:
                break
        except Exception as e:
            log.warning("NGOSquare page %d scrape failed: %s", page, e)
            break
    return jobs


def scrape_sarc(client: httpx.Client) -> list[Job]:
    """Scrape official vacancies from Syrian Arab Red Crescent (SARC) via WP API + HTML fallback."""
    api_url = "https://sarc.sy/wp-json/wp/v2/posts?per_page=100"
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get(api_url, timeout=20)
        if r.status_code == 200 and r.text.lstrip().startswith("["):
            for it in r.json():
                posted = (it.get("date_gmt") or it.get("date") or "")[:19]
                posted_iso = (posted + "+00:00") if posted else None
                if not _is_recent_iso(posted_iso):
                    continue
                link = (it.get("link") or "").strip()
                if not link or link in seen:
                    continue
                raw_t = (it.get("title") or {}).get("rendered") or ""
                clean_t = html.unescape(re.sub(r"<[^>]+>", " ", raw_t)).strip()
                clean_t = re.sub(r"^(?:شاغر|وظيفة|إعلان توظيف)\s*[:\-–]?\s*", "", clean_t).strip()
                content_html = (it.get("content") or {}).get("rendered") or ""
                info = da.extract_apply_from_html(content_html, link)
                has_job_sig = bool(info.get("email") or info.get("apply_url")) and bool(
                    re.search(
                        r"شاغر|وظيف|توظيف|مسؤول|طبيب|محاسب|مدير|مهندس|منسق|مساعد|فني|معالج|سكرتير|"
                        r"officer|assistant|coordinator|engineer|doctor|nurse|midwife|manager|technician|pharmacist|accountant",
                        clean_t, re.I,
                    )
                )
                if not has_job_sig:
                    continue
                seen.add(link)
                job = _make_wp_job(
                    title=clean_t,
                    source="sarc",
                    link=link,
                    posted_iso=posted_iso,
                    employer="الهلال الأحمر العربي السوري (SARC)",
                    content_html=content_html,
                    default_cities=["دمشق"],
                    default_field="ngo",
                    tags=["الهلال الأحمر", "إغاثة وطبية"],
                )
                if job:
                    jobs.append(job)
    except Exception as e:
        log.debug("SARC WP API failed, trying HTML fallback: %s", e)

    if jobs:
        return jobs

    url = "https://sarc.sy/category/%D9%88%D8%B8%D8%A7%D8%A6%D9%81/"
    try:
        r = client.get(url, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for h in soup.find_all(["h2", "h3"]):
            a = h.find("a", href=True)
            if a:
                t = a.get_text(strip=True)
                l = a["href"]
                if any(w in t for w in ["شاغر", "وظيفة", "مسؤول", "طبيب", "محاسب", "مدير", "مهندس", "منسق", "مساعد", "فني"]):
                    clean_title = re.sub(r"^(?:شاغر|وظيفة|إعلان توظيف)\s*[:\-–]?\s*", "", t).strip()
                    job = _make_wp_job(
                        title=clean_title,
                        source="sarc",
                        link=l,
                        posted_iso=None,
                        employer="الهلال الأحمر العربي السوري (SARC)",
                        content_html="",
                        default_cities=["دمشق"],
                        default_field="ngo",
                        tags=["الهلال الأحمر", "إغاثة وطبية"],
                    )
                    if job:
                        jobs.append(job)
    except Exception as e:
        log.warning("SARC scrape failed: %s", e)
    return jobs


def _scrape_laravel_api_board(
    client: httpx.Client,
    api_url: str,
    source: str,
    default_cities: list[str],
    default_sector: str = "ngo",
    max_pages: int = 12,
) -> list[Job]:
    """Shared JSON API scraper for JobSyria and NSJobs boards."""
    jobs: list[Job] = []
    seen: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    for page in range(1, max_pages + 1):
        try:
            r = client.get(f"{api_url}?page={page}", timeout=20)
            if r.status_code != 200:
                break
            payload = r.json()
            items = ((payload.get("data") or {}).get("data") or [])
            if not items:
                break
            open_on_page = 0
            for it in items:
                if (it.get("status") or "").lower() != "open":
                    continue
                dl_raw = (it.get("deadline") or "").strip()
                deadline = dl_raw[:10] if re.match(r"\d{4}-\d{2}-\d{2}", dl_raw) else None
                if deadline and deadline < today_str:
                    continue
                open_on_page += 1
                title = html.unescape((it.get("title") or "").strip()).strip(". ")
                if len(title) < 3:
                    continue
                details_u = (it.get("permanent_link") or "").strip() or f"{api_url}/{it.get('id')}"
                if details_u in seen:
                    continue
                seen.add(details_u)
                app_html = it.get("application_link") or ""
                info = da.extract_apply_from_html(app_html, details_u) if app_html else {}
                app_txt = html.unescape(re.sub(r"<[^>]+>", " ", app_html))
                emails = [info["email"]] if info.get("email") else [e for e in tx.emails(app_txt) if da._good_email(e)]
                phones = sorted(set((info.get("phones") or []) + tx.phones(app_txt)))
                apply_u = info.get("apply_url")
                if not apply_u:
                    for u in tx.urls(app_txt):
                        cu = da.strip_tracking(u)
                        if cu and not da.is_disallowed_offline_url(cu):
                            apply_u = cu
                            break
                if not apply_u and not emails and not phones:
                    continue
                cn = it.get("cities_names")
                cn_str = " ".join(cn) if isinstance(cn, list) else str(cn or "")
                cities = tx.cities_from_text(f"{cn_str} {title}") or list(default_cities)
                if re.search(r"تركيا|gaziantep|istanbul|hatay|mersin", f"{cn_str} {title}", re.I) and not tx.cities_from_text(f"{cn_str} {title}"):
                    continue
                emp = ((it.get("client") or {}).get("name") or "").strip() or None
                pub_raw = (it.get("published_at") or "").strip()
                posted_iso = (
                    f"{pub_raw[:10]}T{(pub_raw[11:16] if len(pub_raw) >= 16 else '09:00')}:00+00:00"
                    if re.match(r"\d{4}-\d{2}-\d{2}", pub_raw) else None
                )
                sal_raw = str(it.get("monthly_salary") or "").strip()
                salary = sal_raw if sal_raw and sal_raw not in ("0", "None", "null") else None
                fld = tx.field_from_text(title, emp or "")
                edu = tx.education_from_text(title) or tx.education_from_title(title) or "bachelor"
                sec = tx.infer_sector(False, fld, emp, f"{title} {emp or ''}", hint=default_sector)
                jobs.append(Job(
                    title=title[:120],
                    source=source,
                    post_url=details_u,
                    posted_at=posted_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    employer=emp,
                    cities=cities,
                    education=edu,
                    education_from="stated" if edu else "unknown",
                    field=fld,
                    subfield=computer_subfield(title) if fld == "computer" else None,
                    kind="formal",
                    official=False,
                    country="sy",
                    sector=sec,
                    apply_url=apply_u,
                    details_url=details_u,
                    email=emails[0] if emails else None,
                    phones=phones,
                    deadline=deadline,
                    salary=salary,
                    apply_types=tx.infer_apply_types(apply_u, phones, False, emails[0] if emails else None),
                    confidence=0.95,
                    tags=[emp] if emp else ["شواغر سوريا"],
                    raw=f"{title}\n{emp or ''}\n{cn_str}\n{app_txt[:600]}".strip(),
                ))
            if open_on_page == 0:
                break
        except Exception as e:
            log.warning("%s page %d scrape failed: %s", source, page, e)
            break
    return jobs


def scrape_jobsyria(client: httpx.Client) -> list[Job]:
    """Scrape active open jobs in Syria from JobSyria REST API."""
    return _scrape_laravel_api_board(client, "https://api.jobsyria.org/api/jobs", "jobsyria", ["دمشق"], "ngo", max_pages=15)


def scrape_nsjobs_web(client: httpx.Client) -> list[Job]:
    """Scrape active open jobs in North-East Syria from NSJobs REST API."""
    return _scrape_laravel_api_board(client, "https://api.nsjobs.net/api/jobs", "nsjobs_web", ["الحسكة", "دير الزور"], "ngo", max_pages=8)


def _extract_jobinsyria_employer(raw_title: str) -> str:
    clean = html.unescape(re.sub(r"<[^>]+>", " ", raw_title or "")).strip()
    if "—" in clean:
        tail = clean.split("—", 1)[1].strip()
        first = re.split(r"[،,\-–]", tail)[0].strip()
        if 3 <= len(first) <= 60 and not tx.cities_from_text(first):
            return first
    return "جهة توظيف عبر Job In Syria"


def scrape_jobinsyria(client: httpx.Client) -> list[Job]:
    """Scrape active Syrian jobs across pages via Job In Syria WP REST API."""
    jobs: list[Job] = []
    seen_links: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    for page in range(1, 26):
        try:
            r = client.get(f"https://jobinsyria.com/wp-json/wp/v2/job_listing?per_page=100&page={page}", timeout=20)
            if r.status_code != 200 or not r.text.lstrip().startswith("["):
                break
            batch = r.json()
            if not batch:
                break
            open_on_page = 0
            recent_on_page = 0
            for it in batch:
                metas = it.get("metas") or {}
                if str(metas.get("_job_filled") or "") in ("1", "true", "yes"):
                    continue
                exp = (metas.get("_job_expiry_date") or "").strip()[:10]
                if exp and exp < today_str:
                    continue
                link = (it.get("link") or "").strip()
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                raw_t = (it.get("title") or {}).get("rendered") or ""
                content_html = (it.get("content") or {}).get("rendered") or ""
                posted = (it.get("date_gmt") or it.get("date") or "")[:19]
                posted_iso = (posted + "+00:00") if posted else None
                dl = (metas.get("_job_application_deadline_date") or exp or "").strip()[:10] or None
                if not _is_recent_iso(posted_iso, max_days=config.HISTORY_DAYS if (dl and dl >= today_str) else config.FORMAL_DAYS):
                    continue
                recent_on_page += 1
                open_on_page += 1
                loc_meta = metas.get("_job_location")
                loc_str = " ".join(loc_meta.values()) if isinstance(loc_meta, dict) else (
                    " ".join(loc_meta) if isinstance(loc_meta, list) else str(loc_meta or "")
                )
                extra_html = f"<p>{loc_str}</p><p>{metas.get('_job_apply_email') or ''}</p><p>{metas.get('_job_phone') or ''}</p>"
                job = _make_wp_job(
                    title=raw_t,
                    source="jobinsyria",
                    link=link,
                    posted_iso=posted_iso,
                    employer=_extract_jobinsyria_employer(raw_t),
                    content_html=content_html + extra_html,
                    default_cities=tx.cities_from_text(loc_str) or ["دمشق"],
                    default_field="other",
                    tags=["شواغر محلية", "Job In Syria"],
                    deadline=dl,
                    sector="private",
                )
                if job:
                    sal = str(metas.get("_job_salary") or "").strip()
                    if sal:
                        job.salary = sal
                    jobs.append(job)
            if page > 1 and (open_on_page == 0 or recent_on_page == 0):
                break
        except Exception as e:
            log.warning("JobInSyria page %s failed: %s", page, e)
            break
    return jobs


def scrape_srd(client: httpx.Client) -> list[Job]:
    """Scrape active Syria Relief and Development (SRD) vacancies via WP API."""
    jobs: list[Job] = []
    try:
        r = client.get("https://srd.org.tr/wp-json/wp/v2/jobs-vacancies?per_page=50", timeout=20)
        if r.status_code != 200 or not r.text.lstrip().startswith("["):
            return []
        for it in r.json():
            posted = (it.get("date_gmt") or it.get("date") or "")[:19]
            posted_iso = (posted + "+00:00") if posted else None
            if not _is_recent_iso(posted_iso):
                continue
            job = _make_wp_job(
                title=(it.get("title") or {}).get("rendered") or "",
                source="srd",
                link=(it.get("link") or "").strip(),
                posted_iso=posted_iso,
                employer="منظمة سوريا للإغاثة والتنمية (SRD)",
                content_html=(it.get("content") or {}).get("rendered") or "",
                default_cities=["إدلب", "حلب"],
                default_field="health",
                tags=["قطاع إنساني", "SRD"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("SRD scrape failed: %s", e)
    return jobs


def scrape_sened(client: httpx.Client) -> list[Job]:
    """Scrape current Syrian humanitarian vacancies from Sened NGO."""
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get("https://sened.ngo/ar/jobs", timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = da.strip_tracking(a["href"].strip())
            if not href or not re.search(r"airtable\.com/(?:app|shr)|forms\.gle|docs\.google\.com/forms|kobotoolbox\.org", href, re.I):
                continue
            if href in seen:
                continue
            seen.add(href)
            h = a.find_previous(["h1", "h2", "h3", "h4"])
            raw_t = (h.get_text(" ", strip=True) if h else "") or a.get_text(" ", strip=True)
            raw_t = raw_t.replace("- Airtable", "").strip(" /-–|")
            if len(raw_t) < 3 or re.search(r"متطوع|volunteer", raw_t, re.I):
                continue
            cities = tx.cities_from_text(raw_t)
            if re.search(r"gaziantep|hatay|mersin|adana|urfa|adiyaman|غازي عنتاب|هاتاي|مرسين|أضنة|أورفا|أديامان", raw_t, re.I) and not cities:
                continue
            card = a.find_parent(["section", "article"]) or a.find_parent("div")
            card_txt = card.get_text(" ", strip=True) if card else ""
            if re.search(r"\b202[0-5]\b", raw_t + " " + card_txt[:200]):
                continue
            fld = tx.field_from_text(raw_t)
            jobs.append(Job(
                title=raw_t[:120],
                source="sened",
                post_url="https://sened.ngo/ar/jobs",
                posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                employer="منظمة سند (Sened)",
                cities=cities or ["إدلب", "اعزاز"],
                education=tx.education_from_text(raw_t) or tx.education_from_title(raw_t) or "bachelor",
                education_from="stated",
                field=fld if fld != "other" else "ngo",
                kind="formal",
                official=False,
                country="sy",
                sector="ngo",
                apply_url=href,
                details_url="https://sened.ngo/ar/jobs",
                apply_types=["form", "site"],
                confidence=0.9,
                tags=["منظمات إنسانية", "سند"],
                raw=raw_t,
            ))
            if len(jobs) >= 20:
                break
    except Exception as e:
        log.warning("Sened scrape failed: %s", e)
    return jobs


def scrape_bahar(client: httpx.Client) -> list[Job]:
    """Scrape direct vacancies from Bahar Organization careers page."""
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get("https://bahar.ngo/careers", timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/careers/" not in href or href.rstrip("/").endswith("/careers"):
                continue
            full_u = urljoin("https://bahar.ngo", href)
            if full_u in seen:
                continue
            seen.add(full_u)
            card = a.find_parent(["div", "article", "li"])
            card_txt = card.get_text(" | ", strip=True) if card else ""
            slug_t = href.rstrip("/").split("/")[-1].replace("-", " ")
            title = ""
            if card:
                h = card.find(["h1", "h2", "h3", "h4", "strong"])
                if h:
                    title = h.get_text(" ", strip=True)
            if not title or len(title) < 4 or "details" in title.lower():
                title = slug_t
            if len(title) < 3 or "\ufffd" in title or not re.search(r"[\u0621-\u064Aa-zA-Z]{3,}", title):
                continue
            cities = tx.cities_from_text(card_txt + " " + title) or ["إدلب", "حلب"]
            fld = tx.field_from_text(title)
            jobs.append(Job(
                title=title[:120],
                source="bahar",
                post_url=full_u,
                posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                employer="منظمة بهار (Bahar)",
                cities=cities,
                education=tx.education_from_text(title) or tx.education_from_title(title) or "bachelor",
                education_from="stated",
                field=fld if fld != "other" else "ngo",
                kind="formal",
                official=False,
                country="sy",
                sector="ngo",
                apply_url=full_u,
                details_url=full_u,
                apply_types=["site"],
                confidence=0.9,
                tags=["منظمات إنسانية", "بهار"],
                raw=card_txt[:500] or title,
            ))
    except Exception as e:
        log.warning("Bahar scrape failed: %s", e)
    return jobs


def scrape_ngossyria(client: httpx.Client) -> list[Job]:
    """Scrape recent UN & INGO vacancies across pages 1-7 of NGOsSyria.com."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, 8):
        url = "https://ngossyria.com/" if page == 1 else f"https://ngossyria.com/?page={page}"
        try:
            r = client.get(url, timeout=20)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            new_on_page = 0
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if "/jobs/" not in href:
                    continue
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 4 or "view & apply" in title.lower():
                    continue
                if "\ufffd" in title or not re.search(r"[\u0621-\u064Aa-zA-Z]{3,}", title):
                    continue
                link = urljoin("https://ngossyria.com", href)
                if link in seen:
                    continue
                seen.add(link)
                new_on_page += 1
                cities = tx.cities_from_text(title) or ["دمشق"]
                fld = tx.field_from_text(title)
                jobs.append(Job(
                    title=title[:120],
                    source="ngossyria",
                    post_url=link,
                    posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    employer="منظمة أممية / دولية عبر NGOs Syria",
                    cities=cities,
                    education=tx.education_from_text(title) or tx.education_from_title(title) or "bachelor",
                    education_from="stated",
                    field=fld if fld != "other" else "ngo",
                    kind="formal",
                    official=False,
                    country="sy",
                    sector="ngo",
                    apply_url=link,
                    details_url=link,
                    apply_types=["site"],
                    confidence=0.92,
                    tags=["الأمم المتحدة", "منظمات دولية"],
                    raw=title,
                ))
            if new_on_page == 0:
                break
        except Exception as e:
            log.warning("NGOsSyria page %s failed: %s", page, e)
            break
    return jobs


def scrape_syria_un(client: httpx.Client) -> list[Job]:
    """Scrape active UN agency vacancies published on the UN Syria official portal."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page_url in ("https://syria.un.org/ar/jobs", "https://syria.un.org/en/jobs"):
        try:
            r = client.get(page_url, timeout=20)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = da.strip_tracking(a["href"].strip())
                if not href or not re.search(r"jobs\.unicef\.org/.+/job/|inspira\.un\.org|jobs\.undp\.org|careers\.un\.org/.+jobdetail", href, re.I):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 6:
                    continue
                emp = "منظمة اليونيسف (UNICEF)" if "unicef.org" in href.lower() else "الأمم المتحدة في سوريا (UN Syria)"
                cities = tx.cities_from_text(title) or ["دمشق"]
                fld = tx.field_from_text(title)
                jobs.append(Job(
                    title=title[:120],
                    source="un_syria",
                    post_url=page_url,
                    posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    employer=emp,
                    cities=cities,
                    education=tx.education_from_text(title) or tx.education_from_title(title) or "bachelor",
                    education_from="stated",
                    field=fld if fld != "other" else "ngo",
                    kind="formal",
                    official=False,
                    country="sy",
                    sector="ngo",
                    apply_url=href,
                    details_url=page_url,
                    apply_types=["site"],
                    confidence=0.95,
                    tags=["الأمم المتحدة", "UN Syria"],
                    raw=title,
                ))
        except Exception as e:
            log.warning("UN Syria scrape %s failed: %s", page_url, e)
    return jobs


def _warm_resolve_cache(jobs: list[Job], cache: dict, max_workers: int = 4) -> None:
    """Resolve pending wrapper/shortener URLs concurrently into cache for large batches."""
    today_str = datetime.now(timezone.utc).date().isoformat()
    formal_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
    pending: list[tuple[str, str]] = []
    seen_u: set[tuple[str, str]] = set()
    for j in jobs:
        if j.deadline and j.deadline < today_str:
            continue
        if not j.deadline and j.posted_at and j.posted_at[:10] < formal_cutoff:
            continue
        u = da.strip_tracking(j.apply_url or "")
        ctry = getattr(j, "country", "sy") or "sy"
        if not u or (u, ctry) in seen_u or (u, ctry) in cache:
            continue
        if da.is_disallowed_offline_url(u) or da._host(u) == "wa.me":
            continue
        if da.is_shortener(u) or da.is_wrapper_portal(u) or not da.is_direct_form_or_ats(u):
            seen_u.add((u, ctry))
            pending.append((u, ctry))
    if len(pending) <= 15:
        return
    tl = threading.local()
    lock = threading.Lock()
    clients: list[httpx.Client] = []

    def _worker(pair: tuple[str, str]) -> tuple[tuple[str, str], dict]:
        u, ctry = pair
        c = getattr(tl, "client", None)
        if c is None:
            c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=12, verify=False, follow_redirects=True)
            tl.client = c
            with lock:
                clients.append(c)
        return pair, da._resolve_url_live(u, c, {}, country=ctry)

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for pair, res in pool.map(_worker, pending):
                cache[pair] = res
    finally:
        for c in clients:
            c.close()


def fetch_all_web_portal_jobs(client: httpx.Client | None = None, cache: dict | None = None) -> list[Job]:
    """Fetch all verified Syrian web portal jobs and unwrap every link to a direct apply path."""
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=25, verify=False, follow_redirects=True)
    cache = cache if cache is not None else {}
    all_jobs: list[Job] = []
    scrapers = (
        scrape_sema,
        scrape_ataa,
        scrape_ida,
        scrape_worklink,
        scrape_ngosquare,
        scrape_sarc,
        scrape_jobsyria,
        scrape_nsjobs_web,
        scrape_jobinsyria,
        scrape_srd,
        scrape_sened,
        scrape_bahar,
        scrape_ngossyria,
        scrape_syria_un,
    )
    try:
        for fn in scrapers:
            try:
                all_jobs.extend(fn(client))
            except Exception as e:
                log.warning("%s failed: %s", fn.__name__, e)
        today_str = datetime.now(timezone.utc).date().isoformat()
        formal_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
        all_jobs = [
            j for j in all_jobs
            if not (j.deadline and j.deadline < today_str)
            and not (not j.deadline and j.posted_at and j.posted_at[:10] < formal_cutoff)
        ]
        if isinstance(client, httpx.Client):
            _warm_resolve_cache(all_jobs, cache)
        return [j for j in all_jobs if da.resolve_job_apply(j, client=client, cache=cache)]
    finally:
        if own:
            client.close()
