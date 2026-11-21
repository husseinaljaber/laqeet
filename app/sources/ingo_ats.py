"""Direct INGO, UN, and Relief NGO ATS connectors for Syria and Turkey."""
from __future__ import annotations

import html
import logging
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .. import config
from .. import direct_apply as da
from .. import extract as ex
from .. import spam
from .. import taxonomy as tx
from ..classify import classify as computer_subfield
from ..models import Job

log = logging.getLogger("ingo_ats")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SKIP_NON_JOB_RE = re.compile(
    r"\b(?:RFP|RFQ|ITB|EOI|IC-\d+|UNV)\b|"
    r"\b(?:volunteer(?:ing)?|intern(?:ship)?|unpaid)\b|"
    r"for\s+internal\s+candidates\s+only|internal\s+(?:only|vacancy|candidates)|"
    r"مناقصة|عروض\s+أسعار|استدراج\s+عروض|طلب\s+عروض|"
    r"متطوع|تطوع|تقرير|دراسة\s+تقييم|تدشين|حملة|ورشة|جلسة\s+(?:تعريفية|توعوية)",
    re.I,
)

_SY_EXPLICIT_RE = re.compile(
    r"\bsyria(?:n\s+arab\s+republic)?\b|(?<![a-z])syr(?![a-z])|"
    r"سوري[اآة]|الأراضي\s+السورية|شمال\s+غرب\s+سوريا|شمال\s+شرق\s+سوريا|"
    r"خان\s+شيخون|جسر\s+الشغور|معرة\s+النعمان|السفيرة|أخترين|إعزاز|سرمدا",
    re.I,
)

_TR_EXPLICIT_RE = re.compile(
    r"\b(?:turk(?:ey|iye)|türkiye)\b|تركيا",
    re.I,
)

_FOREIGN_STATION_RE = re.compile(
    r"\b(?:amman|jordan|cairo|egypt|beirut|lebanon|ramallah|jerusalem|west\s+bank|gaza|palestine|"
    r"geneva|switzerland|new\s+york|nairobi|kenya|baghdad|erbil|iraq|yemen|aden|sanaa|sudan|khartoum|"
    r"libya|tripoli|tunis|tunisia|london|paris|brussels|berlin|copenhagen|oslo|remote\s*-\s*uk|manchester)\b",
    re.I,
)

_RELATIVE_POSTED_RE = re.compile(
    r"posted\s+(today|yesterday|(\d+)\+?\s+(days?|weeks?|months?|years?)\s+ago)",
    re.I,
)

_IHSAN_CLOSE_RE = re.compile(
    r"(Closes|Closed)\s*[:：|]*\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _is_recent_iso(iso_str: str | None, max_days: int | None = None) -> bool:
    if not iso_str:
        return True
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_days or config.FORMAL_DAYS)).date().isoformat()
    return iso_str[:10] >= cutoff


def _skip_non_job_title(title: str) -> bool:
    clean = (title or "").strip()
    if len(clean) < 3:
        return True
    return bool(_SKIP_NON_JOB_RE.search(clean))


def _parse_workday_posted_on(text: str | None) -> str:
    now = datetime.now(timezone.utc)
    raw = (text or "").strip().lower()
    if not raw or "today" in raw:
        return now.isoformat(timespec="seconds")
    if "yesterday" in raw:
        return (now - timedelta(days=1)).isoformat(timespec="seconds")
    m = re.search(r"(\d+)\s*(\+)?\s*day", raw)
    if m:
        days = int(m.group(1))
        if m.group(2):
            days = min(days, 20)
        return (now - timedelta(days=days)).isoformat(timespec="seconds")
    m_w = re.search(r"(\d+)\s*week", raw)
    if m_w:
        return (now - timedelta(days=int(m_w.group(1)) * 7)).isoformat(timespec="seconds")
    m_mo = re.search(r"(\d+)\s*month", raw)
    if m_mo:
        return (now - timedelta(days=int(m_mo.group(1)) * 30)).isoformat(timespec="seconds")
    m_y = re.search(r"(\d+)\s*year", raw)
    if m_y:
        return (now - timedelta(days=int(m_y.group(1)) * 365)).isoformat(timespec="seconds")
    return now.isoformat(timespec="seconds")


def _detect_sy_or_tr(text: str, preferred_country: str | None = None) -> tuple[str, list[str]] | None:
    """Strictly validate that a posting belongs to Syria or Turkey (never Jordan/Palestine/Geneva)."""
    txt = (text or "").strip()
    if not txt:
        return None
    sy_cities = tx.cities_from_text(txt)
    tr_cities = tx.tr_cities_from_text(txt)
    has_sy = bool(sy_cities) or bool(_SY_EXPLICIT_RE.search(txt))
    has_tr = bool(tr_cities) or bool(_TR_EXPLICIT_RE.search(txt))

    if preferred_country == "sy":
        if has_sy:
            return "sy", sy_cities or ["عدة محافظات"]
        return None
    if preferred_country == "tr":
        if _SY_EXPLICIT_RE.search(txt) and not has_tr:
            return None
        if sy_cities and not tr_cities and not _TR_EXPLICIT_RE.search(txt):
            return None
        if has_tr:
            return "tr", tr_cities or ["عدة ولايات في تركيا"]
        return None

    if has_sy and has_tr:
        # Cross-border listings pointing to Damascus/Idlib belong in Syria unless Turkish city leads
        if (sy_cities or _SY_EXPLICIT_RE.search(txt)) and not tr_cities:
            return "sy", sy_cities or ["عدة محافظات"]
        return "tr", tr_cities or ["عدة ولايات في تركيا"]
    if has_sy:
        return "sy", sy_cities or ["عدة محافظات"]
    if has_tr:
        return "tr", tr_cities or ["عدة ولايات في تركيا"]
    if _FOREIGN_STATION_RE.search(txt):
        return None
    return None


def _infer_field_and_sub(title: str, body: str = "", default_field: str = "ngo") -> tuple[str, str | None]:
    fld = tx.field_from_text(title, body[:600])
    sub = computer_subfield(title, body[:400])
    if sub or fld == "computer":
        return "computer", sub or "DT"
    if fld == "other":
        return default_field, None
    return fld, None


def _clean_html_text(content_html: str) -> str:
    if not content_html:
        return ""
    soup = BeautifulSoup(content_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in soup.get_text("\n", strip=True).splitlines()]
    return "\n".join(ln for ln in lines if ln)


_TR_CITIZEN_ONLY_EN_RE = re.compile(
    r"\b(?:only\s+for\s+turkish\s+citizens?|turkish\s+(?:citizens?|nationals?)\s+only|"
    r"must\s+(?:be|hold)\s+turkish\s+citizenship|open\s+to\s+turkish\s+nationals?\s+only)\b",
    re.I,
)


def _make_ats_job(
    *,
    title: str,
    source: str,
    employer: str,
    country: str,
    cities: list[str],
    apply_url: str | None,
    details_url: str,
    posted_at: str | None = None,
    deadline: str | None = None,
    body_text: str = "",
    email: str | None = None,
    phones: list[str] | None = None,
    default_field: str = "ngo",
    tags: list[str] | None = None,
    confidence: float = 0.94,
) -> Job | None:
    clean_title = ex.normalize_title_display(html.unescape(title or ""), keep_matlob=True).strip()
    if _skip_non_job_title(clean_title):
        return None
    if country == "tr" and (
        spam.is_tr_citizenship_only(f"{clean_title}\n{body_text[:1200]}")
        or _TR_CITIZEN_ONLY_EN_RE.search(f"{clean_title}\n{body_text[:1200]}")
    ):
        return None
    if deadline and deadline < _today_str():
        return None
    phones_list = list(phones or [])
    if not (apply_url or email or phones_list):
        return None

    fld, sub = _infer_field_and_sub(clean_title, body_text, default_field=default_field)
    edu = tx.education_from_text(body_text[:1500]) or tx.education_from_title(clean_title) or "bachelor"
    app_types = tx.infer_apply_types(apply_url, phones_list, False, email)
    base_tags = list(tags or ["منظمات دولية"])
    if country == "tr" and "تركيا" not in base_tags:
        base_tags.append("تركيا")

    return Job(
        title=clean_title[:120],
        source=source,
        post_url=details_url,
        posted_at=posted_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        employer=employer[:100],
        cities=cities,
        education=edu,
        education_from="stated" if tx.education_from_text(body_text[:1500]) else "title",
        field=fld,
        subfield=sub,
        kind="formal",
        official=False,
        country=country,
        sector="ngo",
        apply_url=apply_url,
        details_url=details_url,
        email=email,
        phones=phones_list,
        deadline=deadline,
        apply_types=app_types,
        confidence=confidence,
        tags=base_tags,
        raw=f"{clean_title}\n{employer}\n{body_text[:1200]}".strip(),
    )


# ---------------------------------------------------------------- Workday CXS
_WORKDAY_BOARDS: tuple[dict[str, str], ...] = (
    {
        "source": "npa",
        "employer": "المساعدات الشعبية النرويجية (NPA)",
        "host": "folkehjelp.wd103.myworkdayjobs.com",
        "tenant": "folkehjelp",
        "site": "NPA_Career",
    },
    {
        "source": "wvi",
        "employer": "الرؤية العالمية (World Vision)",
        "host": "worldvision.wd1.myworkdayjobs.com",
        "tenant": "worldvision",
        "site": "WorldVisionInternational",
    },
    {
        "source": "unhcr",
        "employer": "المفوضية السامية لشؤون اللاجئين (UNHCR)",
        "host": "unhcr.wd3.myworkdayjobs.com",
        "tenant": "unhcr",
        "site": "External",
    },
    {
        "source": "irc",
        "employer": "لجنة الإنقاذ الدولية (IRC)",
        "host": "theirc.wd1.myworkdayjobs.com",
        "tenant": "theirc",
        "site": "External_Careers",
    },
)


def scrape_workday_board(
    client: httpx.Client,
    *,
    source: str,
    employer: str,
    host: str,
    tenant: str,
    site: str,
    keywords: tuple[str, ...] = ("Syria", "Turkiye"),
) -> list[Job]:
    """Scrape active Workday CXS board jobs for Syria and Turkey (limit capped at 20)."""
    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    base_url = f"https://{host}/{site}"
    jobs: list[Job] = []
    seen: set[str] = set()
    for kw in keywords:
        pref = "tr" if "turk" in kw.lower() else "sy"
        try:
            r = client.post(
                api_url,
                json={"limit": 20, "offset": 0, "searchText": kw},
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=18,
            )
            if r.status_code != 200:
                continue
            for it in (r.json().get("jobPostings") or []):
                title = html.unescape(str(it.get("title") or "")).strip()
                ext_path = str(it.get("externalPath") or "").strip()
                loc_txt = str(it.get("locationsText") or "").strip()
                if not title or not ext_path:
                    continue
                apply_u = base_url + ext_path
                if apply_u in seen:
                    continue
                detected = _detect_sy_or_tr(loc_txt or title, preferred_country=pref)
                if not detected:
                    # Only fall back to automatic detection if search keyword didn't match an unrelated station
                    if _FOREIGN_STATION_RE.search(loc_txt):
                        continue
                    detected = _detect_sy_or_tr(f"{loc_txt} {title}")
                if not detected:
                    continue
                country, cities = detected
                if cities == ["عدة محافظات"] and title:
                    t_cs = tx.cities_from_text(title)
                    if t_cs:
                        cities = t_cs
                if cities == ["عدة ولايات في تركيا"] and title:
                    t_cs = tx.tr_cities_from_text(title)
                    if t_cs:
                        cities = t_cs
                seen.add(apply_u)
                posted_at = _parse_workday_posted_on(str(it.get("postedOn") or ""))
                job = _make_ats_job(
                    title=title,
                    source=source,
                    employer=employer,
                    country=country,
                    cities=cities,
                    apply_url=apply_u,
                    details_url=apply_u,
                    posted_at=posted_at,
                    body_text=loc_txt,
                    tags=["منظمات دولية", source.upper()],
                )
                if job:
                    jobs.append(job)
        except Exception as e:
            log.warning("Workday %s (%s) failed: %s", source, kw, e)
    return jobs


# ---------------------------------------------------------------- Oracle HCM
_ORACLE_BOARDS: tuple[dict[str, str], ...] = (
    {
        "source": "nrc",
        "employer": "المجلس النرويجي للاجئين (NRC)",
        "host": "ekum.fa.em2.oraclecloud.com",
        "site": "CX_2019",
    },
    {
        "source": "undp",
        "employer": "برنامج الأمم المتحدة الإنمائي (UNDP)",
        "host": "estm.fa.em2.oraclecloud.com",
        "site": "CX_1",
    },
)


def scrape_oracle_hcm_board(
    client: httpx.Client,
    *,
    source: str,
    employer: str,
    host: str,
    site: str,
    keywords: tuple[str, ...] = ("Syria", "Turkiye"),
) -> list[Job]:
    """Scrape Oracle Cloud HCM recruiting requisitions with strict duty-station location gating."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for kw in keywords:
        pref = "tr" if "turk" in kw.lower() else "sy"
        url = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder=findReqs;siteNumber={site},limit=20,keyword=%22{kw}%22"
        )
        try:
            r = client.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            if r.status_code != 200:
                continue
            items = r.json().get("items") or []
            reqs = (items[0].get("requisitionList") if items else []) or []
            for req in reqs:
                req_id = str(req.get("Id") or "").strip()
                title = html.unescape(str(req.get("Title") or "")).strip()
                prim = str(req.get("PrimaryLocation") or "").strip()
                sec_locs = req.get("secondaryLocations") or []
                sec_txt = " ".join(str(s.get("Name") or "") for s in sec_locs if isinstance(s, dict))
                loc_all = f"{prim} {sec_txt}".strip()
                if not req_id or not title:
                    continue
                if _FOREIGN_STATION_RE.search(prim) and not (_SY_EXPLICIT_RE.search(prim) or _TR_EXPLICIT_RE.search(prim)):
                    continue
                detected = _detect_sy_or_tr(loc_all, preferred_country=pref)
                if not detected:
                    continue
                country, cities = detected
                apply_u = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}"
                if apply_u in seen:
                    continue
                seen.add(apply_u)
                posted_raw = str(req.get("PostedDate") or "").strip()
                posted_at = (posted_raw[:10] + "T09:00:00+00:00") if re.match(r"\d{4}-\d{2}-\d{2}", posted_raw) else None
                if posted_at and not _is_recent_iso(posted_at):
                    continue
                dl_raw = str(req.get("PostingEndDate") or "").strip()
                deadline = dl_raw[:10] if re.match(r"\d{4}-\d{2}-\d{2}", dl_raw) else None
                job = _make_ats_job(
                    title=title,
                    source=source,
                    employer=employer,
                    country=country,
                    cities=cities,
                    apply_url=apply_u,
                    details_url=apply_u,
                    posted_at=posted_at,
                    deadline=deadline,
                    body_text=f"{title}\n{loc_all}",
                    tags=["الأمم المتحدة" if source == "undp" else "منظمات دولية", source.upper()],
                )
                if job:
                    jobs.append(job)
        except Exception as e:
            log.warning("Oracle HCM %s (%s) failed: %s", source, kw, e)
    return jobs


# ---------------------------------------------------------------- UN Careers
def scrape_un_careers(client: httpx.Client) -> list[Job]:
    """Scrape public active UN Careers openings in Syria and Turkey."""
    url = "https://careers.un.org/api/public/opening/jo/list/filteredV2/en"
    jobs: list[Job] = []
    seen: set[str] = set()
    for kw, pref in (("Syria", "sy"), ("Turkiye", "tr")):
        try:
            r = client.post(
                url,
                json={
                    "filterConfig": {"keyword": kw},
                    "pagination": {"page": 0, "itemPerPage": 40, "sortBy": "startDate", "sortDirection": -1},
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=18,
            )
            if r.status_code != 200:
                continue
            lst = ((r.json().get("data") or {}).get("list")) or []
            for it in lst:
                jid = str(it.get("jobId") or "").strip()
                title = html.unescape(str(it.get("jobTitle") or "")).strip()
                ds_list = it.get("dutyStation") or []
                station_txt = " ".join(
                    str(s.get("description") or "") for s in ds_list if isinstance(s, dict)
                ).strip()
                if not jid or not title or not station_txt:
                    continue
                if _skip_non_job_title(title):
                    continue
                detected = _detect_sy_or_tr(station_txt, preferred_country=pref)
                if not detected:
                    continue
                country, cities = detected
                apply_u = f"https://careers.un.org/jobSearchDescription/{jid}?language=en"
                if apply_u in seen:
                    continue
                seen.add(apply_u)
                start_raw = str(it.get("startDate") or "").strip()
                posted_at = (start_raw[:19] + "+00:00") if len(start_raw) >= 19 else None
                end_raw = str(it.get("endDate") or "").strip()
                deadline = end_raw[:10] if re.match(r"\d{4}-\d{2}-\d{2}", end_raw) else None
                job = _make_ats_job(
                    title=title,
                    source="un_careers",
                    employer="الأمم المتحدة (UN Careers)",
                    country=country,
                    cities=cities,
                    apply_url=apply_u,
                    details_url=apply_u,
                    posted_at=posted_at,
                    deadline=deadline,
                    body_text=f"{title}\n{station_txt}",
                    tags=["الأمم المتحدة", "منظمات دولية"],
                )
                if job:
                    jobs.append(job)
        except Exception as e:
            log.warning("UN Careers (%s) failed: %s", kw, e)
    return jobs


# ---------------------------------------------------------------- BambooHR / Workable / Personio / Greenhouse
def scrape_relief_international(client: httpx.Client) -> list[Job]:
    """Scrape Relief International BambooHR JSON board for Syria and Turkey."""
    url = "https://ri.bamboohr.com/careers/list"
    jobs: list[Job] = []
    try:
        r = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=18)
        if r.status_code != 200:
            return []
        for it in (r.json().get("result") or []):
            jid = str(it.get("id") or "").strip()
            title = html.unescape(str(it.get("jobOpeningName") or "")).strip()
            loc = it.get("location") or {}
            loc_str = " ".join(str(loc.get(k) or "") for k in ("city", "state", "country")).strip()
            if not jid or not title:
                continue
            detected = _detect_sy_or_tr(f"{loc_str} {title}")
            if not detected:
                continue
            country, cities = detected
            apply_u = f"https://ri.bamboohr.com/careers/{jid}"
            job = _make_ats_job(
                title=title,
                source="relief_intl",
                employer="منظمة الإغاثة الدولية (Relief International)",
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=apply_u,
                body_text=f"{title}\n{loc_str}",
                tags=["منظمات دولية", "Relief International"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("Relief International scrape failed: %s", e)
    return jobs


def scrape_neareast_foundation(client: httpx.Client) -> list[Job]:
    """Scrape Near East Foundation (NEF) BambooHR JSON board for Syria and Turkey."""
    url = "https://neareast.bamboohr.com/careers/list"
    jobs: list[Job] = []
    try:
        r = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=18)
        if r.status_code != 200:
            return []
        for it in (r.json().get("result") or []):
            jid = str(it.get("id") or "").strip()
            title = html.unescape(str(it.get("jobOpeningName") or "")).strip()
            dept = html.unescape(str(it.get("departmentLabel") or "")).strip()
            loc = it.get("location") or {}
            loc_str = " ".join(str(loc.get(k) or "") for k in ("city", "state", "country")).strip()
            if not jid or not title:
                continue
            loc_dept = f"{dept} {loc_str}".strip()
            if re.search(r"\b(?:iraq|baghdad|anbar|morocco|marrakesh|merrakech|jordan|amman|lebanon|sudan|mali|armenia)\b", loc_dept, re.I):
                continue
            detected = _detect_sy_or_tr(f"{loc_dept} {title}")
            if not detected:
                continue
            country, cities = detected
            apply_u = f"https://neareast.bamboohr.com/careers/{jid}"
            job = _make_ats_job(
                title=title,
                source="nef",
                employer="مؤسسة الشرق الأدنى (Near East Foundation)",
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=apply_u,
                body_text=f"{title}\n{dept}\n{loc_str}",
                tags=["منظمات دولية", "NEF"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("Near East Foundation scrape failed: %s", e)
    return jobs


def scrape_mag_international(client: httpx.Client) -> list[Job]:
    """Scrape MAG International Workable API for Syria and Turkey."""
    url = "https://apply.workable.com/api/v3/accounts/mag-international/jobs"
    jobs: list[Job] = []
    try:
        r = client.post(
            url,
            json={"query": "", "location": [], "department": [], "worktype": [], "remote": []},
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=18,
        )
        if r.status_code != 200:
            return []
        for it in (r.json().get("results") or []):
            sc = str(it.get("shortcode") or "").strip()
            title = html.unescape(str(it.get("title") or "")).strip()
            loc = it.get("location") or {}
            cc = str(loc.get("countryCode") or "").strip().upper()
            loc_str = " ".join(str(loc.get(k) or "") for k in ("city", "region", "country")).strip()
            if not sc or not title:
                continue
            if cc == "SY":
                detected = ("sy", tx.cities_from_text(f"{loc_str} {title}") or ["عدة محافظات"])
            elif cc == "TR":
                detected = ("tr", tx.tr_cities_from_text(f"{loc_str} {title}") or ["عدة ولايات في تركيا"])
            else:
                detected = _detect_sy_or_tr(f"{loc_str} {title}")
            if not detected:
                continue
            country, cities = detected
            pub_raw = str(it.get("published") or "").strip()
            posted_at = (pub_raw[:19] + "+00:00") if len(pub_raw) >= 10 else None
            if posted_at and not _is_recent_iso(posted_at):
                continue
            apply_u = f"https://apply.workable.com/mag-international/j/{sc}/"
            job = _make_ats_job(
                title=title,
                source="mag_intl",
                employer="المجموعة الاستشارية للألغام (MAG International)",
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=apply_u,
                posted_at=posted_at,
                body_text=f"{title}\n{loc_str}",
                tags=["منظمات دولية", "MAG"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("MAG International scrape failed: %s", e)
    return jobs


def scrape_warchild(client: httpx.Client) -> list[Job]:
    """Scrape War Child Alliance Personio XML feed for Syria and Turkey."""
    url = "https://war-child-alliance.jobs.personio.de/xml"
    jobs: list[Job] = []
    try:
        r = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"}, timeout=18)
        if r.status_code != 200 or "<position>" not in r.text:
            return []
        root = ET.fromstring(r.text)
        for pos in root.findall(".//position"):
            pid = (pos.findtext("id") or "").strip()
            title = html.unescape((pos.findtext("name") or "").strip())
            office = html.unescape((pos.findtext("office") or "").strip())
            created = (pos.findtext("createdAt") or "").strip()
            if not pid or not title:
                continue
            detected = _detect_sy_or_tr(f"{office} {title}")
            if not detected:
                continue
            country, cities = detected
            posted_at = (created[:19] + "+00:00") if len(created) >= 19 else None
            apply_u = f"https://war-child-alliance.jobs.personio.de/job/{pid}"
            job = _make_ats_job(
                title=title,
                source="warchild",
                employer="منظمة وور تشايلد (War Child Alliance)",
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=apply_u,
                posted_at=posted_at,
                body_text=f"{title}\n{office}",
                tags=["منظمات دولية", "War Child"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("War Child scrape failed: %s", e)
    return jobs


def scrape_goal_syria(client: httpx.Client) -> list[Job]:
    """Scrape GOAL Syria Greenhouse board directly."""
    url = "https://boards-api.greenhouse.io/v1/boards/goalsyria/jobs?content=true"
    jobs: list[Job] = []
    try:
        r = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=18)
        if r.status_code != 200:
            return []
        for it in (r.json().get("jobs") or []):
            title = html.unescape(str(it.get("title") or "")).strip()
            apply_u = da.strip_tracking(str(it.get("absolute_url") or "").strip())
            loc_name = str((it.get("location") or {}).get("name") or "").strip()
            content = _clean_html_text(html.unescape(str(it.get("content") or "")))
            updated = str(it.get("updated_at") or "").strip()
            if not title or not apply_u:
                continue
            posted_at = (updated[:19] + "+00:00") if len(updated) >= 19 else None
            if posted_at and not _is_recent_iso(posted_at):
                continue
            cities = tx.cities_from_text(f"{loc_name} {title} {content[:600]}") or ["عدة محافظات"]
            deadline = ex.parse_deadline(content, posted_at)
            job = _make_ats_job(
                title=title,
                source="goal_syria",
                employer="منظمة غول الدولية (GOAL Syria)",
                country="sy",
                cities=cities,
                apply_url=apply_u,
                details_url=apply_u,
                posted_at=posted_at,
                deadline=deadline,
                body_text=f"{loc_name}\n{content[:1500]}",
                tags=["منظمات دولية", "GOAL"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("GOAL Syria scrape failed: %s", e)
    return jobs


# ---------------------------------------------------------------- Inoportal (MDM-T & Support to Life)
_INOPORTAL_BOARDS: tuple[tuple[str, str, str], ...] = (
    ("DunyaDoktorlari", "dunya_doktorlari", "أطباء العالم (Dünya Doktorları / MDM-T)"),
    ("HayataDestek", "hayata_destek", "جمعية دعم الحياة (Hayata Destek / Support to Life)"),
)


def scrape_inoportal_ngo(client: httpx.Client, slug: str, source: str, employer: str) -> list[Job]:
    """Scrape active INGO postings on cv.inoportal.com and extract direct /Basvuru/ forms."""
    list_url = f"https://cv.inoportal.com/{slug}/ilanlar"
    jobs: list[Job] = []
    detail_paths: list[str] = []
    seen_p: set[str] = set()
    try:
        r = client.get(list_url, headers={"User-Agent": USER_AGENT}, timeout=18)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        pat = re.compile(rf"/{re.escape(slug)}/IlanDetay/\d+/\d+", re.I)
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if pat.search(href) and href not in seen_p:
                seen_p.add(href)
                detail_paths.append(urljoin(list_url, href))
    except Exception as e:
        log.warning("Inoportal %s list failed: %s", slug, e)
        return []

    for du in detail_paths:
        try:
            rd = client.get(du, headers={"User-Agent": USER_AGENT}, timeout=18)
            if rd.status_code != 200:
                continue
            if da._CLOSED_PAGE_RE.search(rd.text):
                continue
            ds = BeautifulSoup(rd.text, "html.parser")
            basvuru_a = next(
                (a for a in ds.find_all("a", href=True) if re.search(rf"/{re.escape(slug)}/Basvuru/\d+/\d+", a["href"], re.I)),
                None,
            )
            if not basvuru_a:
                continue
            apply_u = urljoin(du, basvuru_a["href"].strip())
            h_el = ds.select_one("h1, h2")
            raw_title = html.unescape(h_el.get_text(" ", strip=True)) if h_el else ""
            raw_title = re.sub(r"\s*-\s*(?:DÜNYA DOKTORLARI|HAYATA DESTEK)\s*$", "", raw_title, flags=re.I).strip()
            if not raw_title or _skip_non_job_title(raw_title):
                continue
            body_txt = _clean_html_text(rd.text)
            deadline = ex.parse_deadline(body_txt)
            if deadline and deadline < _today_str():
                continue

            # DunyaDoktorlari hosts both Damascus (sy) and Istanbul (tr) positions
            head_txt = f"{raw_title}\n{body_txt[:900]}"
            detected = _detect_sy_or_tr(head_txt)
            if not detected:
                if slug == "HayataDestek" and re.search(r"Birden\s+Fazla|T[üu]rkiye|İstanbul|Hatay|Adana|Mersin|Gaziantep", head_txt, re.I):
                    detected = ("tr", tx.tr_cities_from_text(head_txt) or ["عدة ولايات في تركيا"])
                else:
                    continue
            country, cities = detected
            job = _make_ats_job(
                title=raw_title,
                source=source,
                employer=employer,
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=du,
                deadline=deadline,
                body_text=body_txt[:1500],
                tags=["منظمات دولية", "منظمات إنسانية"],
            )
            if job:
                jobs.append(job)
        except Exception as e:
            log.debug("Inoportal detail %s failed: %s", du, e)
    return jobs


# ---------------------------------------------------------------- IhsanRD (WP Job Manager)
def _parse_ihsan_date(raw: str) -> str | None:
    val = (raw or "").replace(",", "").strip()
    for fmt in ("%B %d %Y", "%b %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def scrape_ihsanrd(client: httpx.Client) -> list[Job]:
    """Scrape active Syria/Turkey NGO vacancies from career.ihsanrd.org WP Job Manager."""
    ajax_url = "https://career.ihsanrd.org/jm-ajax/get_listings/"
    jobs: list[Job] = []
    today_str = _today_str()
    try:
        r = client.post(
            ajax_url,
            data={"per_page": 50, "page": 1},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=20,
        )
        if r.status_code != 200:
            return []
        html_frag = (r.json().get("html") or "").strip()
        if not html_frag:
            return []
        soup = BeautifulSoup(html_frag, "html.parser")
        for li in soup.select("li.job_listing"):
            a = li.find("a", href=True)
            if not a:
                continue
            job_url = a["href"].strip()
            h3 = li.select_one("h3")
            title = html.unescape(h3.get_text(" ", strip=True)) if h3 else a.get_text(" ", strip=True)
            if not title or _skip_non_job_title(title):
                continue
            type_el = li.select_one(".job-type")
            if type_el and re.search(r"volunteer|تطوع|متطوع", type_el.get_text(" ", strip=True), re.I):
                continue
            card_txt = li.get_text(" | ", strip=True)
            if re.search(r"posted\s+\d+\s+years?\s+ago", card_txt, re.I):
                continue

            deadline: str | None = None
            m_close = _IHSAN_CLOSE_RE.search(card_txt)
            if m_close:
                kind_word = m_close.group(1).lower()
                dl_iso = _parse_ihsan_date(m_close.group(2))
                if kind_word == "closed":
                    continue
                if dl_iso:
                    if dl_iso < today_str:
                        continue
                    deadline = dl_iso

            loc_el = li.select_one(".location")
            loc_txt = html.unescape(loc_el.get_text(" ", strip=True)) if loc_el else ""
            detected = _detect_sy_or_tr(f"{loc_txt} {title}")
            if not detected:
                detected = ("sy", tx.cities_from_text(f"{loc_txt} {title}") or ["عدة محافظات"])
            country, cities = detected
            posted_m = _RELATIVE_POSTED_RE.search(card_txt)
            posted_at = _parse_workday_posted_on(posted_m.group(0)) if posted_m else datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not _is_recent_iso(posted_at):
                continue

            clean_t = re.sub(r"\s*[-–]\s*26\d{4,6}\s*$", "", title).strip()
            job = _make_ats_job(
                title=clean_t or title,
                source="ihsanrd",
                employer="منظمة إحسان للإغاثة والتنمية (IhsanRD)",
                country=country,
                cities=cities,
                apply_url=job_url,
                details_url=job_url,
                posted_at=posted_at,
                deadline=deadline,
                body_text=f"{title}\n{loc_txt}",
                tags=["منظمات إنسانية", "إحسان"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("IhsanRD scrape failed: %s", e)
    return jobs


# ---------------------------------------------------------------- MedGlobal Syria
def scrape_medglobal_sy(client: httpx.Client) -> list[Job]:
    """Scrape active Syria health/relief openings from sy.medglobal.org."""
    list_url = "https://sy.medglobal.org/en/jobs"
    jobs: list[Job] = []
    detail_urls: list[str] = []
    seen: set[str] = set()
    try:
        r = client.get(list_url, headers={"User-Agent": USER_AGENT}, timeout=18)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/jobs/" in href and "/jobs/apply/" not in href:
                full = urljoin(list_url, href)
                if full not in seen:
                    seen.add(full)
                    detail_urls.append(full)
    except Exception as e:
        log.warning("MedGlobal list failed: %s", e)
        return []

    for du in detail_urls:
        try:
            rd = client.get(du, headers={"User-Agent": USER_AGENT}, timeout=18)
            if rd.status_code != 200 or da._CLOSED_PAGE_RE.search(rd.text):
                continue
            ds = BeautifulSoup(rd.text, "html.parser")
            apply_a = next(
                (a for a in ds.find_all("a", href=True) if "/jobs/apply/" in a["href"].lower()),
                None,
            )
            if not apply_a:
                continue
            apply_u = urljoin(du, apply_a["href"].strip())
            h1 = ds.select_one("h1, h2")
            raw_title = html.unescape(h1.get_text(" ", strip=True)) if h1 else du.rstrip("/").split("/")[-1].replace("-", " ").title()
            raw_title = re.sub(r"\s*--\s*MedGlobal.*$", "", raw_title, flags=re.I).strip()
            body_txt = _clean_html_text(rd.text)
            deadline = ex.parse_deadline(body_txt)
            cities = tx.cities_from_text(f"{raw_title} {body_txt[:600]}") or ["عدة محافظات"]
            job = _make_ats_job(
                title=raw_title,
                source="medglobal",
                employer="منظمة ميدغلوبال (MedGlobal Syria)",
                country="sy",
                cities=cities,
                apply_url=apply_u,
                details_url=du,
                deadline=deadline,
                body_text=body_txt[:1500],
                default_field="health",
                tags=["قطاع صحي", "منظمات دولية"],
            )
            if job:
                jobs.append(job)
        except Exception as e:
            log.debug("MedGlobal detail %s failed: %s", du, e)
    return jobs


# ---------------------------------------------------------------- Syrian NGO WP APIs
_WP_NGO_SITES: tuple[tuple[str, str, str, list[str]], ...] = (
    ("https://ahf.ngo", "ahf", "مؤسسة الأمين للمساندة الإنسانية (AHF)", ["عدة محافظات"]),
    ("https://hro.ngo", "hro", "منظمة إحياء الأمل (Hope Revival)", ["إدلب", "حلب"]),
    ("https://salam-sy.org", "salam_sy", "منظمة سلام سوريا (Salam)", ["حلب", "إدلب"]),
    ("https://www.ghirasalkhaeer.com", "ghiras", "منظمة غراس الخير الإنسانية", ["دمشق", "إدلب"]),
)


def scrape_wp_ngo_vacancies(
    client: httpx.Client,
    *,
    base_url: str,
    source: str,
    employer: str,
    default_cities: list[str],
) -> list[Job]:
    """Scrape direct-apply NGO vacancies from a Syrian NGO WordPress REST endpoint."""
    api_url = f"{base_url.rstrip('/')}/wp-json/wp/v2/posts?per_page=30"
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get(api_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=18)
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
            raw_t = re.sub(r"^(?:Vacancy\s+Announcement|إعلان\s+توظيف|Job\s+Opportunit(?:y|ies))\s*[–\-:|]+\s*", "", raw_t, flags=re.I).strip()
            if _skip_non_job_title(raw_t):
                continue

            content_html = (it.get("content") or {}).get("rendered") or ""
            plain = _clean_html_text(content_html)
            if _SKIP_NON_JOB_RE.search(plain[:350]) and not re.search(r"vacancy|job|شاغر|توظيف|مسؤول|منسق|ممرض|عامل|طبيب", raw_t, re.I):
                continue
            deadline = ex.parse_deadline(plain, posted_iso)
            if deadline and deadline < _today_str():
                continue

            info = da.extract_apply_from_html(content_html, link)
            email = info.get("email")
            phones = info.get("phones") or []
            cand_u = info.get("apply_url")
            # Keep external ATS / form links only, never bare homepage/wrapper blog post URLs
            if cand_u and (cand_u.rstrip("/") == link.rstrip("/") or da.is_strict_portal_wrapper(cand_u)):
                cand_u = None
            if not (cand_u or email or phones):
                continue

            cities = tx.cities_from_text(f"{raw_t} {plain[:900]}") or list(default_cities)
            job = _make_ats_job(
                title=raw_t,
                source=source,
                employer=employer,
                country="sy",
                cities=cities,
                apply_url=cand_u,
                details_url=link,
                posted_at=posted_iso,
                deadline=deadline,
                body_text=plain[:1500],
                email=email,
                phones=phones,
                tags=["منظمات إنسانية", "عمل إنساني"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("WP NGO %s failed: %s", source, e)
    return jobs


# ---------------------------------------------------------------- Solidarités / SARD / IPV Syria / IHH
def scrape_solidarites(client: httpx.Client) -> list[Job]:
    """Scrape Solidarités International Syria & Turkey openings and extract direct TalentSoft application forms."""
    list_url = "https://www.solidarites.org/en/since-1980/join-us/jobs/"
    jobs: list[Job] = []
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        r = client.get(list_url, headers={"User-Agent": USER_AGENT}, timeout=18)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/jobs/job-detail/" not in href:
                continue
            txt = html.unescape(a.get_text(" ", strip=True))
            if not txt or not (_SY_EXPLICIT_RE.search(txt) or _TR_EXPLICIT_RE.search(txt) or tx.cities_from_text(txt)):
                continue
            full_u = urljoin(list_url, href)
            if full_u not in seen:
                seen.add(full_u)
                candidates.append((txt, full_u))
    except Exception as e:
        log.warning("Solidarites list failed: %s", e)
        return []

    for card_title, detail_url in candidates:
        try:
            dr = client.get(detail_url, headers={"User-Agent": USER_AGENT}, timeout=18)
            if dr.status_code != 200 or da._CLOSED_PAGE_RE.search(dr.text):
                continue
            ds = BeautifulSoup(dr.text, "html.parser")
            apply_a = next(
                (a for a in ds.find_all("a", href=True) if "submit-your-application" in a["href"].lower()),
                None,
            )
            apply_u = urljoin(detail_url, apply_a["href"].strip()) if apply_a else detail_url
            h1 = ds.select_one("h1")
            raw_title = html.unescape(h1.get_text(" ", strip=True)) if h1 else card_title
            clean_t = re.sub(r"^(?:SYRIA|Syria|TURKEY|Turkiye)\s*[-–:]\s*", "", raw_title).strip()
            detected = _detect_sy_or_tr(f"{raw_title} {card_title}")
            if not detected:
                continue
            country, cities = detected
            body_txt = _clean_html_text(dr.text)
            deadline = ex.parse_deadline(body_txt)
            job = _make_ats_job(
                title=clean_t or raw_title,
                source="solidarites",
                employer="منظمة Solidarités International",
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=detail_url,
                deadline=deadline,
                body_text=body_txt[:1500],
                tags=["منظمات دولية", "Solidarités"],
            )
            if job:
                jobs.append(job)
        except Exception as e:
            log.debug("Solidarites detail %s failed: %s", detail_url, e)
    return jobs


def scrape_sard(client: httpx.Client) -> list[Job]:
    """Scrape active Syria & Turkey vacancies from SARD Careers portal (sarderp.baseilowcloud.com)."""
    list_url = "https://sarderp.baseilowcloud.com/careers/"
    jobs: list[Job] = []
    today_str = _today_str()
    try:
        r = client.get(list_url, headers={"User-Agent": USER_AGENT}, timeout=18)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select("article.card"):
            h2 = card.select_one("h2, h3")
            title = html.unescape(h2.get_text(" ", strip=True)) if h2 else ""
            if not title or _skip_non_job_title(title):
                continue
            summary_el = card.select_one("p.summary")
            summary = html.unescape(summary_el.get_text(" ", strip=True)) if summary_el else ""
            meta_txt = " | ".join(sp.get_text(" ", strip=True) for sp in card.select(".meta span"))
            deadline: str | None = None
            m_close = _IHSAN_CLOSE_RE.search(meta_txt.replace(".", ""))
            if m_close:
                dl_iso = _parse_ihsan_date(m_close.group(2))
                if dl_iso:
                    if dl_iso < today_str:
                        continue
                    deadline = dl_iso
            apply_a = next((a for a in card.find_all("a", href=True) if a["href"].rstrip("/").endswith("/apply")), None)
            detail_a = next((a for a in card.find_all("a", href=True) if "/careers/" in a["href"] and not a["href"].rstrip("/").endswith("/apply")), None)
            if not (apply_a or detail_a):
                continue
            apply_u = urljoin(list_url, (apply_a or detail_a)["href"].strip())
            detail_u = urljoin(list_url, (detail_a or apply_a)["href"].strip())
            detected = _detect_sy_or_tr(f"{title} {summary}")
            if not detected:
                detected = ("sy", tx.cities_from_text(f"{title} {summary}") or ["حلب", "إدلب"])
            country, cities = detected
            job = _make_ats_job(
                title=title,
                source="sard",
                employer="منظمة سارد للإغاثة والتنمية (SARD)",
                country=country,
                cities=cities,
                apply_url=apply_u,
                details_url=detail_u,
                deadline=deadline,
                body_text=f"{meta_txt}\n{summary}",
                tags=["منظمات إنسانية", "SARD"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("SARD scrape failed: %s", e)
    return jobs


def scrape_ipv_sy(client: httpx.Client) -> list[Job]:
    """Scrape active North-East Syria (Deir ez-Zor & Raqqa) openings from ipv-sy.org WP Simple Job Board API."""
    api_url = "https://ipv-sy.org/wp-json/wp/v2/jobpost?per_page=30"
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get(api_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=18)
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
            if not raw_t or _skip_non_job_title(raw_t):
                continue
            content_html = (it.get("content") or {}).get("rendered") or ""
            plain = _clean_html_text(content_html)
            deadline = ex.parse_deadline(plain, posted_iso)
            if deadline and deadline < _today_str():
                continue
            cities = tx.cities_from_text(f"{raw_t} {plain[:900]}") or ["الرقة", "دير الزور"]
            job = _make_ats_job(
                title=raw_t,
                source="ipv_sy",
                employer="منظمة IPV Syria",
                country="sy",
                cities=cities,
                apply_url=link,
                details_url=link,
                posted_at=posted_iso,
                deadline=deadline,
                body_text=plain[:1500],
                tags=["منظمات إنسانية", "شمال شرق سوريا"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("IPV Syria scrape failed: %s", e)
    return jobs


def scrape_ihh_hrpeak(client: httpx.Client) -> list[Job]:
    """Scrape active Syria & Turkey openings from IHH Humanitarian Relief Foundation (ihh.hrpeak.com)."""
    list_url = "https://ihh.hrpeak.com/jobs"
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        r = client.get(list_url, headers={"User-Agent": USER_AGENT}, timeout=18)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.endswith(".job"):
                continue
            title = html.unescape(a.get_text(" ", strip=True))
            if not title or len(title) < 3 or _skip_non_job_title(title):
                continue
            full_u = urljoin(list_url, href)
            if full_u in seen:
                continue
            seen.add(full_u)
            row = a.find_parent(["tr", "li", "div"])
            row_txt = html.unescape(row.get_text(" | ", strip=True)) if row else title
            posted_iso: str | None = None
            m_dt = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", row_txt)
            if m_dt:
                posted_iso = f"{m_dt.group(3)}-{m_dt.group(2)}-{m_dt.group(1)}T09:00:00+00:00"
                if not _is_recent_iso(posted_iso):
                    continue
            detected = _detect_sy_or_tr(f"{title} {row_txt}")
            if not detected:
                detected = ("tr", tx.tr_cities_from_text(row_txt) or ["إسطنبول"])
            country, cities = detected
            job = _make_ats_job(
                title=title,
                source="ihh",
                employer="هيئة الإغاثة الإنسانية (IHH)",
                country=country,
                cities=cities,
                apply_url=full_u,
                details_url=full_u,
                posted_at=posted_iso,
                body_text=row_txt,
                tags=["منظمات إنسانية", "IHH"],
            )
            if job:
                jobs.append(job)
    except Exception as e:
        log.warning("IHH HRPeak scrape failed: %s", e)
    return jobs


# ---------------------------------------------------------------- Entrypoint
def _warm_resolve_cache(jobs: list[Job], cache: dict, max_workers: int = 6) -> None:
    pending: list[tuple[str, str]] = []
    seen_u: set[tuple[str, str]] = set()
    for j in jobs:
        u = da.strip_tracking(j.apply_url or "")
        ctry = getattr(j, "country", "sy") or "sy"
        if not u or (u, ctry) in seen_u or (u, ctry) in cache:
            continue
        if da.is_disallowed_offline_url(u) or da._host(u) == "wa.me":
            continue
        if da.is_shortener(u) or da.is_wrapper_portal(u) or not da.is_direct_form_or_ats(u):
            seen_u.add((u, ctry))
            pending.append((u, ctry))
    if len(pending) <= 4:
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


def fetch_all_ingo_ats_jobs(
    client: httpx.Client | None = None,
    cache: dict | None = None,
    quick: bool = False,
) -> list[Job]:
    """Fetch verified Syria & Turkey openings from direct INGO, UN, and NGO career portals."""
    own = client is None
    cli = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, verify=False, follow_redirects=True)
    res_cache = cache if cache is not None else {}
    jobs: list[Job] = []
    try:
        tasks: list[tuple[str, callable]] = [
            (
                b["source"],
                lambda c, b=b: scrape_workday_board(
                    c, source=b["source"], employer=b["employer"], host=b["host"], tenant=b["tenant"], site=b["site"]
                ),
            )
            for b in _WORKDAY_BOARDS
        ]
        tasks.extend(
            (
                b["source"],
                lambda c, b=b: scrape_oracle_hcm_board(
                    c, source=b["source"], employer=b["employer"], host=b["host"], site=b["site"]
                ),
            )
            for b in _ORACLE_BOARDS
        )
        tasks.extend([
            ("un_careers", scrape_un_careers),
            ("relief_intl", scrape_relief_international),
            ("nef", scrape_neareast_foundation),
            ("mag_intl", scrape_mag_international),
            ("warchild", scrape_warchild),
            ("goal_syria", scrape_goal_syria),
            ("ihsanrd", scrape_ihsanrd),
            ("medglobal", scrape_medglobal_sy),
            ("solidarites", scrape_solidarites),
            ("sard", scrape_sard),
            ("ipv_sy", scrape_ipv_sy),
            ("ihh", scrape_ihh_hrpeak),
            *[
                (src, lambda c, sl=sl, src=src, emp=emp: scrape_inoportal_ngo(c, sl, src, emp))
                for sl, src, emp in _INOPORTAL_BOARDS
            ],
            *[
                (
                    src,
                    lambda c, bu=bu, src=src, emp=emp, dc=dc: scrape_wp_ngo_vacancies(
                        c, base_url=bu, source=src, employer=emp, default_cities=dc
                    ),
                )
                for bu, src, emp, dc in _WP_NGO_SITES
            ],
        ])

        for name, fn in tasks:
            try:
                batch = fn(cli)
                log.info("INGO ATS %s yielded %d jobs", name, len(batch))
                jobs.extend(batch)
            except Exception as e:
                log.warning("INGO ATS %s failed: %s", name, e)

        today_str = _today_str()
        formal_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.FORMAL_DAYS)).date().isoformat()
        jobs = [
            j
            for j in jobs
            if not (j.deadline and j.deadline < today_str)
            and not (not j.deadline and j.posted_at and j.posted_at[:10] < formal_cutoff)
        ]
        if isinstance(cli, httpx.Client):
            _warm_resolve_cache(jobs, res_cache)
        return [j for j in jobs if da.resolve_job_apply(j, client=cli, cache=res_cache)]
    finally:
        if own:
            cli.close()
