"""Scrapers for verified Syrian organization & platform web portals: SEMA, Ataa Relief, ReliefWeb, IDA."""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import warnings
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import httpx

from .. import config
from .. import direct_apply as da
from .. import extract as ex
from .. import spam
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


def _extract_clean_html_text(content_html: str) -> str:
    """Extract clean visible body text from HTML, stripping CSS, JS, and header/footer/nav chrome."""
    if not content_html:
        return ""
    low = content_html.lower()
    if any(tag in low for tag in ("<style", "<script", "<html", "<body")):
        soup = BeautifulSoup(content_html, "html.parser")
        for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
            t.decompose()
        main_el = soup.find("article") or soup.find("main")
        if main_el is not None:
            main_txt = main_el.get_text("\n", strip=True)
            raw_txt = main_txt if len(main_txt) >= 80 else soup.get_text("\n", strip=True)
        else:
            raw_txt = soup.get_text("\n", strip=True)
        txt = html.unescape(raw_txt)
    else:
        txt = html.unescape(re.sub(r"<[^>]+>", " ", content_html))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in txt.splitlines()]
    return "\n".join(ln for ln in lines if ln)


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
    body_txt = _extract_clean_html_text(content_html)
    combined = f"{clean_t} {body_txt[:4000]}"
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
    emp = emp.strip()
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
        tags=["شواغر موثقة"],
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
                emp = parts[1] if len(parts) > 1 else ""
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
                    tags=["شواغر موثقة"],
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


def scrape_ngosquare(client: httpx.Client, max_pages: int = 15) -> list[Job]:
    """Scrape humanitarian & UN vacancies from NGO Square across paginated listing pages."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
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
    return _scrape_laravel_api_board(client, "https://api.jobsyria.org/api/jobs", "jobsyria", ["دمشق"], "ngo", max_pages=25)


def scrape_nsjobs_web(client: httpx.Client) -> list[Job]:
    """Scrape active open jobs in North-East Syria from NSJobs REST API."""
    return _scrape_laravel_api_board(client, "https://api.nsjobs.net/api/jobs", "nsjobs_web", ["الحسكة", "دير الزور"], "ngo", max_pages=20)


_JOBINSYRIA_LOC_EMP_RE = re.compile(
    r"^(?:منطقة|منطقه|حي|شارع|ساحة|ساحه|كراج|خلف|قرب|مقابل|بجانب|بجوار|طريق|أوتوستراد|اوتستراد|دوار|جسر|مفترق|طلعة|نزلة|مشروع\s+دمر)\b|"
    r"^(?:السليمانية|الجميلية|المزة|المزرعة|الحميدية|القصاع|الزاهرة|الميدان|جرمانا|صحنايا|دوما|باب شرقي|برزة|ركن الدين|كفرسوسة|المالكي|أبو رمانة|الشعلان|البرامكة|المهاجرين|الأشرفية|الموكامبو|الفرقان|الحمدانية|سيف الدولة|صلاح الدين|الأعظمية|ميسلون|السريان|العزيزية|الإذاعة|الكلاسة|أشرفية صحنايا|جمعية الزهراء|الشيخ نجار|حلب الجديدة)$",
    re.I,
)


def _extract_jobinsyria_employer(raw_title: str) -> str:
    clean = html.unescape(re.sub(r"<[^>]+>", " ", raw_title or "")).strip()
    if "—" in clean:
        tail = clean.split("—", 1)[1].strip()
        first = re.split(r"[،,\-–]", tail)[0].strip()
        if 3 <= len(first) <= 60 and not tx.cities_from_text(first) and not _JOBINSYRIA_LOC_EMP_RE.search(first):
            return first
    return ""


def scrape_jobinsyria(client: httpx.Client, max_pages: int = 25) -> list[Job]:
    """Scrape active Syrian jobs across pages via Job In Syria WP REST API."""
    jobs: list[Job] = []
    seen_links: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    for page in range(1, max_pages + 1):
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
                    tags=["شواغر محلية"],
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
    """Scrape active Syria Relief and Development (SRD) vacancies via EN & AR WP APIs."""
    t_start = time.monotonic()
    endpoints = (
        "https://srd.org.tr/wp-json/wp/v2/jobs-vacancies?per_page=60",
        "https://srd.org.tr/ar/wp-json/wp/v2/jobs-vacancies?per_page=60",
    )
    jobs: list[Job] = []
    seen: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    candidates: list[dict] = []
    for url in endpoints:
        try:
            r = client.get(url, timeout=20)
            if r.status_code != 200 or not r.text.lstrip().startswith("["):
                continue
            for it in r.json():
                link = (it.get("link") or "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                posted = (it.get("date_gmt") or it.get("date") or "")[:19]
                posted_iso = (posted + "+00:00") if posted else None
                if not _is_recent_iso(posted_iso):
                    continue
                raw_t = (it.get("title") or {}).get("rendered") or ""
                plain_t = html.unescape(re.sub(r"<[^>]+>", " ", raw_t)).strip()
                if not plain_t or _TENDER_NOTICE_RE.search(plain_t) or re.search(r"متطوع|تطوع|volunteer", plain_t, re.I):
                    continue
                candidates.append({
                    "link": link,
                    "posted_iso": posted_iso,
                    "raw_t": raw_t,
                    "content_html": (it.get("content") or {}).get("rendered") or "",
                })
        except Exception as e:
            log.warning("SRD scrape (%s) failed: %s", url, e)

    need_fetch = [c for c in candidates if not c["content_html"]]
    if need_fetch:
        if isinstance(client, httpx.Client):
            def _fetch_detail(item: dict) -> tuple[str, str | None]:
                try:
                    with httpx.Client(
                        headers={"User-Agent": USER_AGENT, "Connection": "close"},
                        timeout=httpx.Timeout(8.0, connect=4.0, read=8.0, write=4.0, pool=4.0),
                        verify=False,
                        follow_redirects=True,
                    ) as c:
                        pr = c.get(item["link"])
                        if pr.status_code == 200:
                            clean_txt = _extract_clean_html_text(pr.text)
                            dl = ex.parse_deadline(clean_txt, item["posted_iso"])
                            return pr.text, dl
                except Exception as pe:
                    log.debug("SRD detail fetch (%s) failed: %s", item["link"], pe)
                return "", None

            pool = ThreadPoolExecutor(max_workers=2)
            try:
                futures = [(item, pool.submit(_fetch_detail, item)) for item in need_fetch]
                for item, fut in futures:
                    if time.monotonic() - t_start > 60:
                        log.warning("SRD detail fetch hit 60s budget; stopping remaining fetches")
                        break
                    try:
                        html_body, dl = fut.result(timeout=12)
                        item["content_html"] = html_body
                        item["deadline"] = dl
                    except Exception as pe:
                        log.debug("SRD detail future (%s) timed out/failed: %s", item["link"], pe)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        else:
            for item in need_fetch:
                if time.monotonic() - t_start > 45:
                    break
                try:
                    pr = client.get(item["link"], timeout=8)
                    if pr.status_code == 200:
                        item["content_html"] = pr.text
                        clean_txt = _extract_clean_html_text(pr.text)
                        item["deadline"] = ex.parse_deadline(clean_txt, item["posted_iso"])
                except Exception as pe:
                    log.debug("SRD detail fetch (%s) failed: %s", item["link"], pe)

    for c in candidates:
        deadline = c.get("deadline")
        if deadline and deadline < today_str:
            continue
        job = _make_wp_job(
            title=c["raw_t"],
            source="srd",
            link=c["link"],
            posted_iso=c["posted_iso"],
            employer="منظمة سوريا للإغاثة والتنمية (SRD)",
            content_html=c["content_html"],
            default_cities=["إدلب", "حلب"],
            default_field="health",
            tags=["قطاع إنساني", "SRD"],
            deadline=deadline,
        )
        if job:
            jobs.append(job)
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
                cities=cities or ["إدلب", "حلب"],
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
            if len(jobs) >= 60:
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
    """Scrape recent UN & INGO vacancies across pages 1-19 of NGOsSyria.com."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, 20):
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
                    employer="",
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


_UNJOBNET_ID_RE = re.compile(r"/jobs/detail/.+-(\d+)(?:[/?#]|$)")
_TENDER_NOTICE_RE = re.compile(r"\b(?:RFP|RFQ|ITB|EOI|IC-\d+)\b|استدراج\s+عروض|مناقصة", re.I)
_SEEKER_AD_RE = re.compile(r"^\s*(?:أنا\s+|مغتربة\s+|طالبة\s+جامعية\s+|أبحث\s+عن\s+(?:عمل|عقد\s+عمل|شركاء))", re.M)


def _extract_ld_job_posting(soup: BeautifulSoup) -> dict | None:
    """Return the first Schema.org JobPosting dict embedded in HTML."""
    for sc in soup.find_all("script", type="application/ld+json"):
        raw = sc.string or sc.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            if isinstance(obj, dict) and obj.get("@type") == "JobPosting" and obj.get("title"):
                return obj
    return None


def scrape_unjobnet_country(
    client: httpx.Client,
    slug: str = "syria",
    country: str = "sy",
    source: str = "unjobnet_sy",
    max_pages: int = 6,
    max_details: int = 120,
) -> list[Job]:
    """Scrape UNjobnet country listings via JobPosting JSON-LD and /apply/<id> redirect verification."""
    base = "https://www.unjobnet.org"
    detail_urls: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for page_no in range(1, max_pages + 1):
        page_url = f"{base}/countries/{slug}" + (f"?page={page_no}" if page_no > 1 else "")
        try:
            r = client.get(page_url, timeout=20)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            found_on_page = 0
            for a in soup.select("a[href*='/jobs/detail/']"):
                href = urljoin(base, a["href"].strip())
                m = _UNJOBNET_ID_RE.search(href)
                if not m or m.group(1) in seen_ids:
                    continue
                seen_ids.add(m.group(1))
                detail_urls.append((href, m.group(1)))
                found_on_page += 1
            if found_on_page == 0:
                break
        except Exception as e:
            log.warning("UNjobnet (%s) list %s failed: %s", slug, page_url, e)
            break

    today_str = datetime.now(timezone.utc).date().isoformat()
    formal_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
    targets = detail_urls[:max_details]

    def _parse_detail(pair: tuple[str, str], http_c: httpx.Client) -> Job | None:
        detail_url, job_id = pair
        try:
            rd = http_c.get(detail_url, timeout=20)
            if rd.status_code != 200:
                return None
            sd = BeautifulSoup(rd.text, "html.parser")
            ld = _extract_ld_job_posting(sd)
            if not ld:
                return None
            title = html.unescape(str(ld.get("title") or "")).strip()
            if not title or _TENDER_NOTICE_RE.search(title):
                return None

            desc_html = html.unescape(str(ld.get("description") or ""))
            desc_soup = BeautifulSoup(desc_html, "html.parser")
            desc_txt = desc_soup.get_text("\n", strip=True)
            if spam.is_spam(f"{title}\n{desc_txt[:500]}"):
                return None
            if country == "tr" and spam.is_tr_citizenship_only(f"{title}\n{desc_txt[:800]}"):
                return None

            posted_raw = str(ld.get("datePosted") or "").strip()
            deadline_raw = str(ld.get("validThrough") or "").strip()
            posted_at = posted_raw if len(posted_raw) >= 10 else None
            deadline = deadline_raw[:10] if len(deadline_raw) >= 10 and re.match(r"\d{4}-\d{2}-\d{2}", deadline_raw) else None
            if deadline and deadline < today_str:
                return None
            if not deadline and posted_at and posted_at[:10] < formal_cutoff:
                return None

            org_obj = ld.get("hiringOrganization") or {}
            employer = html.unescape(str(org_obj.get("name") or "")).strip() if isinstance(org_obj, dict) else ""

            loc_parts: list[str] = []
            loc_obj = ld.get("jobLocation")
            locs = loc_obj if isinstance(loc_obj, list) else ([loc_obj] if isinstance(loc_obj, dict) else [])
            for loc in locs:
                if not isinstance(loc, dict):
                    continue
                addr = loc.get("address") or {}
                if isinstance(addr, dict):
                    for k in ("addressLocality", "addressRegion", "addressCountry"):
                        if addr.get(k):
                            loc_parts.append(str(addr[k]))
            loc_hay = " ".join(loc_parts) + " " + title + " " + desc_txt[:400]
            cities = (
                tx.tr_cities_from_text(loc_hay) or ["عدة ولايات في تركيا"]
                if country == "tr"
                else tx.cities_from_text(loc_hay) or ["عدة محافظات"]
            )

            emails = [e for e in tx.emails(desc_txt) if da._good_email(e)]
            for mail_a in desc_soup.select("a[href^='mailto:']"):
                cand = mail_a["href"][7:].split("?")[0].strip()
                if da._good_email(cand) and cand not in emails:
                    emails.append(cand)
            phones = tx.phones_tr(desc_txt) if country == "tr" else tx.phones(desc_txt)
            gov_links = da.extract_gov_links(desc_txt)
            direct_apply = str(gov_links["best_apply_url"]) if gov_links["best_apply_url"] else f"{base}/apply/{job_id}"

            edu = tx.education_from_text(desc_txt) or tx.education_from_title(title) or "bachelor"
            fld = tx.field_from_text(title, desc_txt[:600])
            if fld == "other":
                fld = "ngo"
            sub = computer_subfield(title, desc_txt[:400])
            if sub:
                fld = "computer"

            return Job(
                title=title[:120],
                source=source,
                post_url=detail_url,
                posted_at=posted_at,
                employer=employer or None,
                cities=cities,
                education=edu,
                education_from="stated" if tx.education_from_text(desc_txt) else "title",
                field=fld,
                subfield=sub,
                kind="formal",
                official=False,
                country=country,
                sector="ngo",
                apply_url=direct_apply,
                details_url=detail_url,
                email=emails[0] if emails else None,
                phones=phones,
                apply_types=tx.infer_apply_types(direct_apply, phones, False, emails[0] if emails else None),
                deadline=deadline,
                confidence=0.92,
                tags=["الأمم المتحدة", "منظمات دولية"] + (["تركيا"] if country == "tr" else []),
                raw=f"{title}\n{employer}\n{desc_txt[:1500]}".strip(),
            )
        except Exception as e:
            log.warning("UNjobnet detail %s failed: %s", detail_url, e)
            return None

    jobs: list[Job] = []
    if isinstance(client, httpx.Client) and len(targets) > 4:
        tl = threading.local()
        lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _worker(pair: tuple[str, str]) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=18, verify=False, follow_redirects=True)
                tl.client = c
                with lock:
                    pool_clients.append(c)
            return _parse_detail(pair, c)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for j in pool.map(_worker, targets):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
    else:
        for pair in targets:
            j = _parse_detail(pair, client)
            if j is not None:
                jobs.append(j)
    return jobs


def scrape_unjobnet_syria(client: httpx.Client) -> list[Job]:
    """Scrape UNjobnet Syria listings via shared JobPosting extractor."""
    return scrape_unjobnet_country(client, slug="syria", country="sy", source="unjobnet_sy", max_pages=6, max_details=120)


def scrape_unjobnet_turkey(client: httpx.Client) -> list[Job]:
    """Scrape UNjobnet Turkey listings via shared JobPosting extractor."""
    return scrape_unjobnet_country(client, slug="turkey", country="tr", source="unjobnet_tr", max_pages=6, max_details=120)


def scrape_shafak(client: httpx.Client) -> list[Job]:
    """Scrape active Syria NGO vacancies from Shafak Organization WP REST API (category 65)."""
    api_url = "https://shafak.org/wp-json/wp/v2/posts?categories=65&per_page=100"
    jobs: list[Job] = []
    seen: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    try:
        r = client.get(api_url, timeout=20)
        if r.status_code != 200 or not r.text.lstrip().startswith("["):
            return []
        for it in r.json():
            posted = (it.get("date_gmt") or it.get("date") or "")[:19]
            posted_iso = (posted + "+00:00") if posted else None
            if not _is_recent_iso(posted_iso):
                continue
            link = (it.get("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            raw_t = html.unescape(re.sub(r"<[^>]+>", " ", (it.get("title") or {}).get("rendered") or "")).strip()
            if not raw_t or _TENDER_NOTICE_RE.search(raw_t) or re.search(r"متطوع|تطوع|volunteer", raw_t, re.I):
                continue
            content_html = (it.get("content") or {}).get("rendered") or ""
            content_html = re.sub(
                r"(https?://docs\.google\.com/forms/d/[A-Za-z0-9_-]+)/preview\b",
                r"\1/viewform",
                content_html,
            )
            plain = html.unescape(re.sub(r"<[^>]+>", " ", content_html))
            deadline = ex.parse_deadline(plain, posted_iso)
            if deadline and deadline < today_str:
                continue
            job = _make_wp_job(
                title=raw_t,
                source="shafak",
                link=link,
                posted_iso=posted_iso,
                employer="منظمة شفق الإنسانية (Shafak)",
                content_html=content_html,
                default_cities=["حلب", "إدلب"],
                default_field="ngo",
                tags=["منظمات إنسانية", "شفق"],
                deadline=deadline,
                sector="ngo",
            )
            if job and (job.apply_url != link or job.email or job.phones):
                jobs.append(job)
    except Exception as e:
        log.warning("Shafak scrape failed: %s", e)
    return jobs


def _parse_drc_date(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def scrape_drc(client: httpx.Client, country: str = "sy") -> list[Job]:
    """Scrape active direct-apply vacancies from Danish Refugee Council (job.drc.ngo) for Syria or Turkey."""
    base = "https://job.drc.ngo"
    today_str = datetime.now(timezone.utc).date().isoformat()
    jobs: list[Job] = []
    try:
        r = client.get(f"{base}/jobs/", timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        matches: list[tuple[str, str, str | None, str | None]] = []
        seen: set[str] = set()
        for art in soup.select("article.jobList__item"):
            ctry = (art.get("data-country") or "").strip().lower()
            if country == "sy":
                if "syria" not in ctry:
                    continue
            else:
                if not any(k in ctry for k in ("turkey", "turkiye", "türkiye")):
                    continue
            title = html.unescape((art.get("data-title") or "").strip())
            if not title or _TENDER_NOTICE_RE.search(title) or re.search(r"\b(?:volunteer|intern(?:ship)?)\b", title, re.I):
                continue
            dl = _parse_drc_date(art.get("data-deadline") or "")
            if dl and dl < today_str:
                continue
            pub_d = _parse_drc_date(art.get("data-published") or "")
            posted_iso = (pub_d + "T09:00:00+00:00") if pub_d else datetime.now(timezone.utc).isoformat(timespec="seconds")
            a = art.find("a", href=True)
            if not a:
                continue
            detail_url = urljoin(base, a["href"].strip())
            if detail_url in seen:
                continue
            seen.add(detail_url)
            matches.append((title, detail_url, posted_iso, dl))

        for title, detail_url, posted_iso, dl in matches:
            try:
                rd = client.get(detail_url, timeout=18)
                if rd.status_code != 200:
                    continue
                ds = BeautifulSoup(rd.text, "html.parser")
                body_txt = ds.get_text("\n", strip=True)
                if country == "tr" and spam.is_tr_citizenship_only(f"{title}\n{body_txt[:1000]}"):
                    continue
                apply_u = None
                for a in ds.find_all("a", href=True):
                    href = da.strip_tracking(a["href"].strip())
                    if href and ("hr-manager.net" in href.lower() or da.is_direct_form_or_ats(href)):
                        apply_u = href
                        break
                if not apply_u:
                    continue
                cities = (
                    tx.tr_cities_from_text(f"{title} {body_txt[:600]}") or ["عدة ولايات في تركيا"]
                    if country == "tr"
                    else tx.cities_from_text(f"{title} {body_txt[:600]}") or ["دمشق"]
                )
                fld = tx.field_from_text(title, body_txt[:400])
                sub = computer_subfield(title, body_txt[:400])
                if sub or fld == "computer":
                    fld, sub = "computer", sub or "DT"
                elif fld == "other":
                    fld = "ngo"
                jobs.append(Job(
                    title=title[:120],
                    source="drc",
                    post_url=detail_url,
                    posted_at=posted_iso,
                    employer="المجلس الدنماركي للاجئين (DRC)",
                    cities=cities,
                    education=tx.education_from_text(body_txt[:1200]) or tx.education_from_title(title) or "bachelor",
                    education_from="stated",
                    field=fld,
                    subfield=sub,
                    kind="formal",
                    official=False,
                    country=country,
                    sector="ngo",
                    apply_url=apply_u,
                    details_url=detail_url,
                    deadline=dl,
                    apply_types=["form", "site"],
                    confidence=0.95,
                    tags=["منظمات دولية", "DRC"] + (["تركيا"] if country == "tr" else []),
                    raw=f"{title}\n{body_txt[:1200]}",
                ))
            except Exception as e:
                log.debug("DRC detail %s failed: %s", detail_url, e)
    except Exception as e:
        log.warning("DRC (%s) scrape failed: %s", country, e)
    return jobs


def scrape_acted(client: httpx.Client, country: str = "sy") -> list[Job]:
    """Scrape active direct-apply vacancies from ACTED official vacancies board."""
    slugs = ("syria",) if country == "sy" else ("turkey", "turkiye")
    jobs: list[Job] = []
    seen: set[str] = set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    for slug in slugs:
        list_url = f"https://www.acted.org/en/get-involved/join-us/vacancies/?tax-country[]={slug}"
        try:
            r = client.get(list_url, timeout=20)
            if r.status_code != 200:
                continue
            detail_urls = sorted(set(re.findall(r"https://www\.acted\.org/en/jobs/[a-z0-9-]+/", r.text)))
            for du in detail_urls:
                if du in seen:
                    continue
                seen.add(du)
                try:
                    rd = client.get(du, timeout=18)
                    if rd.status_code != 200:
                        continue
                    ds = BeautifulSoup(rd.text, "html.parser")
                    h1 = ds.select_one("h1")
                    title = html.unescape(h1.get_text(" ", strip=True)) if h1 else du.rstrip("/").split("/")[-1].replace("-", " ").title()
                    if not title or len(title) < 4 or re.search(r"\b(?:volunteer|intern(?:ship)?)\b", title, re.I):
                        continue
                    body_txt = _extract_clean_html_text(rd.text)
                    if country == "tr" and spam.is_tr_citizenship_only(f"{title}\n{body_txt[:800]}"):
                        continue
                    dl = ex.parse_deadline(body_txt)
                    if dl and dl < today_str:
                        continue
                    apply_u = du.rstrip("/") + "/#form"
                    cities = (
                        tx.tr_cities_from_text(f"{title} {body_txt[:500]}") or ["عدة ولايات في تركيا"]
                        if country == "tr"
                        else tx.cities_from_text(f"{title} {body_txt[:500]}") or ["دمشق"]
                    )
                    fld = tx.field_from_text(title, body_txt[:400])
                    sub = computer_subfield(title, body_txt[:400])
                    if sub or fld == "computer":
                        fld, sub = "computer", sub or "DT"
                    elif fld == "other":
                        fld = "ngo"
                    jobs.append(Job(
                        title=title[:120],
                        source="acted",
                        post_url=du,
                        posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        employer="منظمة أكتيد الدولية (ACTED)",
                        cities=cities,
                        education=tx.education_from_text(body_txt[:1000]) or tx.education_from_title(title) or "bachelor",
                        education_from="stated",
                        field=fld,
                        subfield=sub,
                        kind="formal",
                        official=False,
                        country=country,
                        sector="ngo",
                        apply_url=apply_u,
                        details_url=du,
                        deadline=dl,
                        apply_types=["form", "site"],
                        confidence=0.93,
                        tags=["منظمات دولية", "ACTED"] + (["تركيا"] if country == "tr" else []),
                        raw=f"{title}\n{body_txt[:1200]}",
                    ))
                except Exception as e:
                    log.debug("ACTED detail %s failed: %s", du, e)
        except Exception as e:
            log.warning("ACTED (%s) scrape failed: %s", slug, e)
    return jobs


def scrape_wfp(client: httpx.Client, country: str = "sy") -> list[Job]:
    """Scrape active World Food Programme (WFP) official Workday openings strictly inside Syria or Turkey."""
    api_url = "https://wd3.myworkdaysite.com/wday/cxs/wfp/job_openings/jobs"
    base_apply = "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings"
    kw = "Syria" if country == "sy" else "Turkiye"
    jobs: list[Job] = []
    try:
        r = client.post(
            api_url,
            json={"limit": 40, "offset": 0, "searchText": kw},
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=18,
        )
        if r.status_code != 200:
            return []
        for it in (r.json().get("jobPostings") or []):
            title = html.unescape(str(it.get("title") or "")).strip()
            ext_path = str(it.get("externalPath") or "").strip()
            loc_txt = str(it.get("locationsText") or "").strip()
            if not title or not ext_path:
                continue
            if re.search(r"\b(?:volunteer|intern(?:ship)?)\b", title, re.I):
                continue
            low_loc = loc_txt.lower()
            if country == "sy":
                if "syria" not in low_loc and not tx.cities_from_text(loc_txt):
                    continue
                cities = tx.cities_from_text(f"{loc_txt} {title}") or ["دمشق"]
            else:
                if not any(k in low_loc for k in ("turkiye", "türkiye", "turkey")) and not tx.tr_cities_from_text(loc_txt):
                    continue
                if spam.is_tr_citizenship_only(f"{title} {loc_txt}"):
                    continue
                cities = tx.tr_cities_from_text(f"{loc_txt} {title}") or ["أنقرة"]
            apply_u = base_apply + ext_path
            fld = tx.field_from_text(title, loc_txt)
            sub = computer_subfield(title)
            if sub or fld == "computer":
                fld, sub = "computer", sub or "DT"
            elif fld == "other":
                fld = "ngo"
            jobs.append(Job(
                title=title[:120],
                source="wfp",
                post_url=apply_u,
                posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                employer="برنامج الأغذية العالمي (WFP)",
                cities=cities,
                education=tx.education_from_title(title) or "bachelor",
                education_from="title",
                field=fld,
                subfield=sub,
                kind="formal",
                official=False,
                country=country,
                sector="ngo",
                apply_url=apply_u,
                details_url=apply_u,
                apply_types=["form", "site"],
                confidence=0.95,
                tags=["الأمم المتحدة", "WFP"] + (["تركيا"] if country == "tr" else []),
                raw=f"{title}\n{loc_txt}",
            ))
    except Exception as e:
        log.warning("WFP (%s) scrape failed: %s", country, e)
    return jobs


_IMPACTPOOL_DL_RE = re.compile(
    r"(?:Application\s+deadline|Closing\s+date)\s*:\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
    re.I,
)


def _parse_impactpool_deadline(text: str) -> str | None:
    m = _IMPACTPOOL_DL_RE.search(text or "")
    if not m:
        return None
    val = m.group(1).replace(",", "").strip()
    for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def scrape_impactpool_country(
    client: httpx.Client,
    country_param: str = "Syria",
    country: str = "sy",
    source: str = "impactpool_sy",
    max_details: int = 80,
) -> list[Job]:
    """Scrape active UN/INGO vacancies via Impactpool and resolve /apply HTTP 301 straight to the official ATS."""
    base = "https://www.impactpool.org"
    search_url = f"{base}/search?countries%5B%5D={country_param}"
    job_paths: list[str] = []
    seen_ids: set[str] = set()
    try:
        r = client.get(search_url, timeout=20)
        if r.status_code != 200:
            return []
        for path in re.findall(r'href="(/jobs/(\d+))"', r.text):
            full_path, jid = path
            if jid not in seen_ids:
                seen_ids.add(jid)
                job_paths.append(full_path)
    except Exception as e:
        log.warning("Impactpool (%s) list failed: %s", country_param, e)
        return []

    today_str = datetime.now(timezone.utc).date().isoformat()
    targets = job_paths[:max_details]

    def _fetch_one(jpath: str, http_c: httpx.Client) -> Job | None:
        detail_url = base + jpath
        try:
            # 1. Resolve /apply 301 redirect straight to official UN/INGO ATS without login
            ra = http_c.get(detail_url + "/apply", timeout=15, follow_redirects=False)
            raw_loc = (ra.headers.get("location") or "").strip() if ra.status_code in (301, 302, 303, 307, 308) else ""
            if not raw_loc:
                return None
            apply_u = da.strip_tracking(urljoin(base, raw_loc))
            if not apply_u or "impactpool.org" in da._host(apply_u) or "app.unv.org" in apply_u.lower():
                return None
            if da.is_disallowed_offline_url(apply_u) or da.is_strict_portal_wrapper(apply_u):
                return None

            # 2. Parse detail page metadata (title, org, city, deadline, description)
            rd = http_c.get(detail_url, timeout=18)
            if rd.status_code != 200:
                return None
            ds = BeautifulSoup(rd.text, "html.parser")
            main = ds.select_one("main") or ds
            lines = [ln.strip() for ln in main.get_text("\n", strip=True).splitlines() if ln.strip()]
            h1 = ds.select_one("h1")
            title = html.unescape(h1.get_text(" ", strip=True)) if h1 else (lines[0] if lines else "")
            title = re.sub(r"(?<=[A-Za-z0-9])Remote$", "", title).strip()
            if not title or len(title) < 3 or _TENDER_NOTICE_RE.search(title):
                return None
            if re.search(r"\b(?:volunteer|unv|intern(?:ship)?)\b", title, re.I):
                return None

            employer = ""
            city_line = ""
            if lines:
                idx = 0
                for i, ln in enumerate(lines[:5]):
                    if ln == title:
                        idx = i
                        break
                if idx + 1 < len(lines) and len(lines[idx + 1]) <= 90:
                    employer = lines[idx + 1]
                if idx + 2 < len(lines) and len(lines[idx + 2]) <= 60:
                    city_line = lines[idx + 2]
            if re.search(r"\b(?:unv|united nations volunteers)\b", employer, re.I):
                return None

            body_txt = re.sub(r"(?<=[A-Za-z0-9])Remote\b", "", "\n".join(lines[:60]))
            if spam.is_spam(f"{title}\n{body_txt[:500]}"):
                return None
            if country == "tr" and spam.is_tr_citizenship_only(f"{title}\n{body_txt[:1200]}"):
                return None

            deadline = _parse_impactpool_deadline(body_txt)
            if deadline and deadline < today_str:
                return None

            loc_hay = f"{city_line} {title} {body_txt[:500]}"
            if country == "tr":
                tr_cs = tx.tr_cities_from_text(loc_hay)
                if not tr_cs and not re.search(r"\b(?:turkey|turkiye|türkiye|تركيا)\b", loc_hay, re.I):
                    return None
                cities = tr_cs or ["عدة ولايات في تركيا"]
            else:
                sy_cs = tx.cities_from_text(loc_hay)
                if not sy_cs and not re.search(r"\b(?:syria|سوريا)\b", loc_hay, re.I):
                    return None
                cities = sy_cs or ["عدة محافظات"]
            fld = tx.field_from_text(title, body_txt[:600])
            sub = computer_subfield(title, body_txt[:400])
            if sub or fld == "computer":
                fld, sub = "computer", sub or "DT"
            elif fld == "other":
                fld = "ngo"

            return Job(
                title=title[:120],
                source=source,
                post_url=detail_url,
                posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                employer=employer[:100] if employer else None,
                cities=cities,
                education=tx.education_from_text(body_txt) or tx.education_from_title(title) or "bachelor",
                education_from="stated",
                field=fld,
                subfield=sub,
                kind="formal",
                official=False,
                country=country,
                sector="ngo",
                apply_url=apply_u,
                details_url=detail_url,
                deadline=deadline,
                apply_types=["form", "site"],
                confidence=0.93,
                tags=["الأمم المتحدة", "منظمات دولية"] + (["تركيا"] if country == "tr" else []),
                raw=f"{title}\n{employer}\n{body_txt[:1400]}".strip(),
            )
        except Exception as e:
            log.debug("Impactpool detail %s failed: %s", detail_url, e)
            return None

    jobs: list[Job] = []
    if isinstance(client, httpx.Client) and len(targets) > 4:
        tl = threading.local()
        lock = threading.Lock()
        pool_clients: list[httpx.Client] = []

        def _worker(jpath: str) -> Job | None:
            c = getattr(tl, "client", None)
            if c is None:
                c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=18, verify=False, follow_redirects=True)
                tl.client = c
                with lock:
                    pool_clients.append(c)
            return _fetch_one(jpath, c)

        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for j in pool.map(_worker, targets):
                    if j is not None:
                        jobs.append(j)
        finally:
            for c in pool_clients:
                c.close()
    else:
        for jpath in targets:
            j = _fetch_one(jpath, client)
            if j is not None:
                jobs.append(j)
    return jobs


def scrape_impactpool_syria(client: httpx.Client) -> list[Job]:
    """Scrape active Syria UN/INGO jobs via Impactpool direct ATS redirects."""
    return scrape_impactpool_country(client, country_param="Syria", country="sy", source="impactpool_sy", max_details=80)


def scrape_impactpool_turkey(client: httpx.Client) -> list[Job]:
    """Scrape active Turkey UN/INGO jobs via Impactpool direct ATS redirects."""
    return scrape_impactpool_country(client, country_param="Turkey", country="tr", source="impactpool_tr", max_details=80)


def scrape_syria_jobs_com(client: httpx.Client) -> list[Job]:
    """Scrape active employer vacancies from syria-jobs.com vacancy category RSS feed."""
    feed_base = "https://syria-jobs.com/category/%d9%88%d8%b8%d9%8a%d9%81%d8%a9-%d8%b4%d8%a7%d8%ba%d8%b1%d8%a9-%d9%88%d9%81%d8%b1%d8%b5-%d8%b9%d9%85%d9%84/feed/"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": "https://www.google.com/",
    }
    today_str = datetime.now(timezone.utc).date().isoformat()
    formal_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
    jobs: list[Job] = []
    seen_links: set[str] = set()

    for paged in (1, 2):
        url = feed_base if paged == 1 else f"{feed_base}?paged={paged}"
        try:
            r = client.get(url, headers=headers, timeout=20)
            if r.status_code != 200 or "<item>" not in r.text:
                break
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            if not items:
                break
            recent_on_page = 0
            for it in items:
                title = html.unescape((it.findtext("title") or "").strip())
                link = da.strip_tracking(it.findtext("link") or "")
                pub_raw = (it.findtext("pubDate") or "").strip()
                if not title or not link or link in seen_links:
                    continue
                seen_links.add(link)

                posted_at = None
                if pub_raw:
                    try:
                        dt = parsedate_to_datetime(pub_raw)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        posted_at = dt.astimezone(timezone.utc).isoformat()
                    except Exception:
                        posted_at = None
                if posted_at and posted_at[:10] < formal_cutoff:
                    continue
                recent_on_page += 1

                body_html = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or it.findtext("description") or ""
                soup = BeautifulSoup(body_html, "html.parser")
                lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]
                txt = "\n".join(lines)
                if not txt or _SEEKER_AD_RE.search(txt) or spam.is_spam(f"{title}\n{txt[:500]}"):
                    continue
                if ex._ABROAD.search(ex.F(txt)) and not tx.cities_from_text(txt):
                    continue

                deadline = ex.parse_deadline(txt, posted_at)
                if deadline and deadline < today_str:
                    continue

                # Extract phones (href tel: + body text), emails, and non-portal direct apply links
                phones = tx.phones(txt)
                emails = [e for e in tx.emails(txt) if da._good_email(e)]
                ext_links: list[str] = []
                for a in soup.find_all("a", href=True):
                    href = da.clean_url(a["href"])
                    if not href:
                        continue
                    low = href.lower()
                    if low.startswith("tel:"):
                        for p in tx.phones(href[4:]):
                            if p not in phones:
                                phones.append(p)
                    elif low.startswith("mailto:"):
                        cand = href[7:].split("?")[0].strip()
                        if da._good_email(cand) and cand not in emails:
                            emails.append(cand)
                    elif href.startswith(("http://", "https://")) and "syria-jobs.com" not in da._host(href):
                        for p in da.extract_phone_from_wa(href, country="sy"):
                            if p not in phones:
                                phones.append(p)
                        if not da.is_disallowed_offline_url(href) and not da.is_strict_portal_wrapper(href):
                            ext_links.append(href)

                phones = sorted(set(phones))
                gov_links = da.extract_gov_links(txt, extra_links=ext_links)
                ext_links.sort(key=lambda u: 0 if da.is_direct_form_or_ats(u) else 1)
                apply_url = str(gov_links["best_apply_url"]) if gov_links["best_apply_url"] else (ext_links[0] if ext_links else None)
                if not (apply_url or phones or emails):
                    continue

                # Extract employer & city cleanly from structured labels in body
                emp_m = None
                if len(lines) >= 2 and any("الجن" in ln or "طريقة التقدي" in ln for ln in lines[2:5]):
                    cand_emp = lines[1]
                    if (
                        3 <= len(cand_emp) <= 80
                        and not re.match(r"^(?:الجنـ?س|طريقة\s+التقديم|مقر\s+المؤسسة|تفاصيل|المؤهلات)", cand_emp)
                        and "syria-jobs" not in cand_emp.lower()
                    ):
                        emp_m = cand_emp
                city_m = re.search(r"مقر\s+المؤسس[ةه]\s*[:：]\s*([^\n]+)", txt)
                city_hint = city_m.group(1) if city_m else ""
                cities = tx.cities_from_text(city_hint) or tx.cities_from_text(txt[:300]) or ["عدة محافظات"]
                gender = "female" if re.search(r"الجنـ?س\s*[:：]\s*أنثى", txt) else ("male" if re.search(r"الجنـ?س\s*[:：]\s*ذكر", txt) else None)
                edu = tx.education_from_text(txt) or tx.education_from_title(title) or "none"
                fld = tx.field_from_text(title, txt[:500])
                sub = computer_subfield(title, txt[:300])
                if sub or fld == "computer":
                    fld, sub = "computer", sub or "DT"

                jobs.append(Job(
                    title=ex.normalize_title_display(title)[:120],
                    source="syria_jobs",
                    post_url=link,
                    posted_at=posted_at,
                    employer=emp_m,
                    cities=cities,
                    education=edu,
                    education_from="stated" if tx.education_from_text(txt) else "unknown",
                    field=fld,
                    subfield=sub,
                    kind="formal" if (emails or apply_url or deadline) else "quick",
                    official=False,
                    country="sy",
                    sector=tx.infer_sector(False, fld, emp_m, txt),
                    apply_url=apply_url,
                    details_url=link,
                    phones=phones,
                    whatsapp=tx.whatsapp_only(txt) and bool(phones),
                    email=emails[0] if emails else None,
                    apply_types=tx.infer_apply_types(apply_url, phones, bool(phones and tx.whatsapp_only(txt)), emails[0] if emails else None),
                    deadline=deadline,
                    gender=gender,
                    confidence=0.85,
                    raw=txt[:1600],
                ))
            if recent_on_page == 0:
                break
        except Exception as e:
            log.warning("SyriaJobs RSS %s failed: %s", url, e)
            break
    return jobs


def _parse_powerholding_date(raw: str) -> str | None:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return None
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat() + "T09:00:00+00:00"
        except ValueError:
            continue
    return None


def scrape_powerholding_sy(client: httpx.Client, max_pages: int = 10) -> list[Job]:
    """Scrape active private-sector Syria jobs from Power International Holding SuccessFactors board."""
    base = "https://careers.powerholding-intl.com"
    formal_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
    jobs: list[Job] = []
    seen: set[str] = set()

    for page in range(max_pages):
        url = f"{base}/search/?q=Syria&sortColumn=referencedate&sortDirection=desc&startrow={page * 25}"
        try:
            r = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("tr.data-row")
            if not rows:
                break
            recent_on_page = 0
            for row in rows:
                a = row.select_one("a.jobTitle-link[href]")
                if not a:
                    continue
                job_url = da.strip_tracking(urljoin(base, a["href"].strip()))
                if not job_url or job_url in seen:
                    continue
                loc_el = row.select_one("span.jobLocation")
                loc_txt = re.sub(r"\s+", " ", loc_el.get_text(" ", strip=True)) if loc_el else ""
                if "syria" not in loc_txt.lower() and ", sy" not in loc_txt.lower():
                    continue
                dt_el = row.select_one("span.jobDate")
                posted_at = _parse_powerholding_date(dt_el.get_text(" ", strip=True) if dt_el else "")
                if posted_at and posted_at[:10] < formal_cutoff:
                    continue
                recent_on_page += 1
                raw_t = html.unescape(a.get_text(" ", strip=True))
                clean_t = ex.normalize_title_display(raw_t, keep_matlob=True).strip()
                if not clean_t or len(clean_t) < 3 or _TENDER_NOTICE_RE.search(clean_t):
                    continue
                if re.search(r"\b(?:volunteer|intern(?:ship)?)\b", clean_t, re.I):
                    continue
                seen.add(job_url)

                cities = tx.cities_from_text(f"{clean_t} {loc_txt}") or ["عدة محافظات"]
                fld = tx.field_from_text(clean_t, loc_txt)
                sub = computer_subfield(clean_t, loc_txt)
                if sub or fld == "computer":
                    fld, sub = "computer", sub or "DT"
                edu = tx.education_from_title(clean_t) or "bachelor"

                jobs.append(Job(
                    title=clean_t[:120],
                    source="powerholding",
                    post_url=job_url,
                    posted_at=posted_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    employer="Power International Holding",
                    cities=cities,
                    education=edu,
                    education_from="title",
                    field=fld,
                    subfield=sub,
                    kind="formal",
                    official=False,
                    country="sy",
                    sector="private",
                    apply_url=job_url,
                    details_url=job_url,
                    apply_types=["site", "form"],
                    confidence=0.92,
                    tags=["شركات كبرى", "قطاع خاص"],
                    raw=f"{clean_t}\n{loc_txt}",
                ))
            if recent_on_page == 0:
                break
        except Exception as e:
            log.warning("PowerHolding Syria page %s failed: %s", page, e)
            break
    return jobs


_JOBSYRIA_NET_EN_AGE_RE = re.compile(
    r"(\d+)\s+(seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago",
    re.I,
)
_JOBSYRIA_NET_AR_AGE_RE = re.compile(
    r"منذ\s+(?:(\d+)\s*)?(ثاني[ةه]|دقيق[ةه]|دقائق|ساع[ةه]|ساعات|يوم|يومين|أيام|ايام|أسبوع|اسبوع|أسبوعين|أسابيع|شهر|شهرين|أشهر|سنة)",
)
_JOBSYRIA_NET_SHARE_RE = re.compile(r"openShareModal\(\s*['\"]([^'\"]+)['\"]\s*\)")
_JOBSYRIA_NET_GENERIC_EMP_RE = re.compile(r"^(?:شركة|صالة|مكتب|مركز|محل|معمل|مؤسسة|ورشة)$")
_JOBSYRIA_NET_WEAK_TITLE_RE = re.compile(
    r"^(?:لشرك[ةه]\s+|أشخاص\s+يصنعون|مركز\s+\S+\s+يفتح|كفاء[ةه]\s+(?:مميز|مبدع)|لدي[هة]\s+خبر[ةه]|معلم$)"
)
_JOBSYRIA_NET_META_H2_RE = re.compile(r"اكثر\s+من|أكثر\s+من|شواغر\s+متعددة|فرص\s+عمل")


def _parse_jobsyria_net_posted_at(text: str, now_dt: datetime) -> str | None:
    s = text or ""
    m = _JOBSYRIA_NET_EN_AGE_RE.search(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("sec"):
            delta = timedelta(seconds=n)
        elif unit.startswith("min"):
            delta = timedelta(minutes=n)
        elif unit.startswith("hour"):
            delta = timedelta(hours=n)
        elif unit.startswith("day"):
            delta = timedelta(days=n)
        elif unit.startswith("week"):
            delta = timedelta(days=n * 7)
        elif unit.startswith("month"):
            delta = timedelta(days=n * 30)
        else:
            delta = timedelta(days=n * 365)
        return (now_dt - delta).isoformat(timespec="seconds")

    am = _JOBSYRIA_NET_AR_AGE_RE.search(s)
    if am:
        num = int(am.group(1)) if am.group(1) else 1
        unit = am.group(2)
        if unit in ("يومين", "أسبوعين", "شهرين") and not am.group(1):
            num = 2
        if "ثان" in unit:
            delta = timedelta(seconds=num)
        elif "دق" in unit:
            delta = timedelta(minutes=num)
        elif "ساع" in unit:
            delta = timedelta(hours=num)
        elif "يوم" in unit or "أيام" in unit or "ايام" in unit:
            delta = timedelta(days=num)
        elif "سب" in unit:
            delta = timedelta(days=num * 7)
        elif "شهر" in unit or "أشهر" in unit:
            delta = timedelta(days=num * 30)
        else:
            delta = timedelta(days=num * 365)
        return (now_dt - delta).isoformat(timespec="seconds")
    return None


def scrape_jobsyria_net(client: httpx.Client, max_pages: int = 20) -> list[Job]:
    """Scrape active Syria private-sector jobs from jobsyria.net keeping only direct phone/email/form paths."""
    base = "https://jobsyria.net"
    now_dt = datetime.now(timezone.utc)
    formal_cutoff = (now_dt - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
    jobs: list[Job] = []
    seen_cards: set[str] = set()

    for page in range(1, max_pages + 1):
        url = f"{base}/?page={page}"
        try:
            r = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("article.work-card")
            if not cards:
                break

            recent_on_page = 0
            for idx, card in enumerate(cards):
                share_url = ""
                btn = card.select_one("button[onclick*='openShareModal']")
                if btn:
                    sm = _JOBSYRIA_NET_SHARE_RE.search(btn.get("onclick") or "")
                    if sm:
                        share_url = da.strip_tracking(urljoin(base, sm.group(1).strip()))
                if not share_url:
                    title_a = card.select_one("a[href*='/وظائف/'], a[href*='%D9%88%D8%B8%D8%A7%D8%A6%D9%81']")
                    if title_a:
                        share_url = da.strip_tracking(urljoin(base, title_a["href"].strip()))
                if not share_url:
                    share_url = f"{base}/?page={page}#card-{idx}"
                if share_url in seen_cards:
                    continue
                seen_cards.add(share_url)

                card_meta_txt = card.get_text("\n", strip=True)
                posted_at = _parse_jobsyria_net_posted_at(card_meta_txt, now_dt) or now_dt.isoformat(timespec="seconds")
                if posted_at[:10] < formal_cutoff:
                    continue
                recent_on_page += 1

                full_el = card.select_one(".work-description-full") or card.select_one("p.work-description-text")
                body_txt = (full_el.get_text("\n", strip=True) if full_el else (card.get("data-search") or "")).strip()
                if not body_txt:
                    continue

                badge_city: list[str] = []
                card_emp: str | None = None
                for meta_p in card.select("p.new-style-data"):
                    line_t = meta_p.get_text(" ", strip=True)
                    if "المحافظة:" in line_t or "مكان العمل:" in line_t:
                        for c_found in tx.cities_from_text(line_t):
                            if c_found not in badge_city:
                                badge_city.append(c_found)
                    elif "الجهة المعنية:" in line_t:
                        b_tag = meta_p.find("b")
                        cand_e = (b_tag.get_text(" ", strip=True) if b_tag else line_t.split(":", 1)[-1]).strip()
                        if 3 <= len(cand_e) <= 80 and not _JOBSYRIA_NET_GENERIC_EMP_RE.match(cand_e):
                            card_emp = cand_e
                if not badge_city:
                    for sp in card.select("span"):
                        sp_t = sp.get_text(" ", strip=True)
                        if len(sp_t) <= 30:
                            cs = tx.cities_from_text(sp_t)
                            if cs:
                                badge_city.extend(c for c in cs if c not in badge_city)

                tel_phones: list[str] = []
                emails: list[str] = []
                ext_links: list[str] = []
                for a in card.find_all("a", href=True):
                    href = da.clean_url(a["href"])
                    if not href:
                        continue
                    low = href.lower()
                    if low.startswith("tel:"):
                        for p in tx.phones(href[4:]):
                            if p not in tel_phones:
                                tel_phones.append(p)
                    elif low.startswith("mailto:"):
                        em = href[7:].split("?")[0].strip()
                        if da._good_email(em) and em not in emails:
                            emails.append(em)
                    elif href.startswith(("http://", "https://")) and "jobsyria.net" not in da._host(href):
                        for p in da.extract_phone_from_wa(href, country="sy"):
                            if p not in tel_phones:
                                tel_phones.append(p)
                        if not da.is_disallowed_offline_url(href) and not da.is_strict_portal_wrapper(href):
                            ext_links.append(href)

                h2 = card.select_one("h2")
                h2_t = ex.normalize_title_display(h2.get_text(" ", strip=True), keep_matlob=True).strip() if h2 else ""
                extracted = ex.extract(
                    body_txt,
                    "jobsyria_net",
                    share_url,
                    posted_at,
                    "quick",
                    ext_links,
                    country="sy",
                    sector="private",
                )
                if not extracted:
                    if h2_t and not _JOBSYRIA_NET_META_H2_RE.search(h2_t):
                        extracted = ex.extract(
                            f"{h2_t}\n{body_txt}",
                            "jobsyria_net",
                            share_url,
                            posted_at,
                            "quick",
                            ext_links,
                            country="sy",
                            sector="private",
                        )
                elif (
                    len(extracted) == 1
                    and len(h2_t) >= 3
                    and not _JOBSYRIA_NET_META_H2_RE.search(h2_t)
                    and ex._good_title(h2_t)
                    and _JOBSYRIA_NET_WEAK_TITLE_RE.search(extracted[0].title)
                ):
                    extracted[0].title = h2_t[:120]
                    fld = tx.field_from_text(extracted[0].title, body_txt[:300])
                    sub = computer_subfield(extracted[0].title, body_txt[:300])
                    if sub or fld == "computer":
                        extracted[0].field, extracted[0].subfield = "computer", sub or "DT"
                    else:
                        extracted[0].field = fld
                    if extracted[0].education_from in ("title", "unknown"):
                        new_edu = tx.education_from_text(body_txt) or tx.education_from_title(h2_t)
                        if new_edu:
                            extracted[0].education = new_edu

                for j in extracted:
                    if j.spam:
                        continue
                    if not j.cities and badge_city:
                        j.cities = list(badge_city)
                    if not j.employer and card_emp:
                        j.employer = card_emp
                    for p in tel_phones:
                        if p not in j.phones:
                            j.phones.append(p)
                    for em in emails:
                        if not j.email:
                            j.email = em
                    if j.apply_url and (
                        "jobsyria.net" in da._host(j.apply_url)
                        or da.is_strict_portal_wrapper(j.apply_url)
                        or da.is_disallowed_offline_url(j.apply_url)
                    ):
                        j.apply_url = None
                    if not j.apply_url and ext_links:
                        j.apply_url = ext_links[0]
                    if not (j.phones or j.email or j.apply_url):
                        continue
                    j.details_url = share_url
                    j.apply_types = tx.infer_apply_types(j.apply_url, j.phones, j.whatsapp, j.email)
                    jobs.append(j)
            if recent_on_page == 0:
                break
        except Exception as e:
            log.warning("JobSyria.net page %s failed: %s", page, e)
            break
    return jobs


def _warm_resolve_cache(jobs: list[Job], cache: dict, max_workers: int = 8) -> None:
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


def fetch_all_web_portal_jobs(client: httpx.Client | None = None, cache: dict | None = None, quick: bool = False) -> list[Job]:
    """Fetch all verified Syrian web portal jobs and unwrap every link to a direct apply path."""
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=25, verify=False, follow_redirects=True)
    cache = cache if cache is not None else {}
    all_jobs: list[Job] = []
    scrapers = (
        scrape_sema,
        scrape_ataa,
        scrape_ida,
        scrape_shafak,
        scrape_worklink,
        (lambda c: scrape_ngosquare(c, max_pages=5 if quick else 25)),
        scrape_sarc,
        scrape_jobsyria,
        scrape_nsjobs_web,
        (lambda c: scrape_jobinsyria(c, max_pages=4 if quick else 28)),
        scrape_srd,
        scrape_sened,
        scrape_bahar,
        scrape_ngossyria,
        scrape_syria_un,
        scrape_unjobnet_syria,
        scrape_drc,
        scrape_acted,
        scrape_wfp,
        scrape_impactpool_syria,
        scrape_syria_jobs_com,
        (lambda c: scrape_powerholding_sy(c, max_pages=2 if quick else 10)),
        (lambda c: scrape_jobsyria_net(c, max_pages=3 if quick else 20)),
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
