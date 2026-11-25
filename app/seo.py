"""Search engines: schema.org JobPosting for each job page (Google for Jobs), sitemap.xml and robots.txt.

Google's rules we follow: markup only on a page that shows one job in full, only while the job is open, and only
with facts the ad really states (no invented employer or salary). Jobs without an employer name get no markup.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape as xml_escape

from . import config

COUNTRY_ISO = {"sy": "SY", "tr": "TR"}
_MULTI_CITY = {"عدة محافظات", "عدة ولايات في تركيا"}
_REMOTE_WORDS = ("عن بعد", "عن بُعد", "اونلاين", "أونلاين", "remote")
SITEMAP_LIMIT = 45_000  # one file holds up to 50,000 URLs


def public_base(request_base: str) -> str:
    """The site's public address: PUBLIC_URL when it is set to a real domain, otherwise what the request came to."""
    base = (config.PUBLIC_URL or "").rstrip("/")
    if base and "localhost" not in base and "127.0.0.1" not in base:
        return base
    return request_base.rstrip("/")


def is_open(j: dict, today: str | None = None) -> bool:
    today = today or date.today().isoformat()
    if j.get("status") != "live":
        return False
    if (j.get("expires_on") or "9999") < today:
        return False
    return not (j.get("deadline") and j["deadline"] < today)


def _employment_type(j: dict) -> str | None:
    shift = (j.get("shift") or "")
    if "جزئي" in shift or "part" in shift.lower():
        return "PART_TIME"
    if "كامل" in shift or "full" in shift.lower():
        return "FULL_TIME"
    return None


def _description_html(j: dict, cities: str, edu_label: str) -> str:
    e = html.escape
    parts = []
    if j.get("summary"):
        parts.append(f"<p>{e(j['summary'])}</p>")
    facts = [f"المدينة: {cities}", f"الشهادة: {edu_label}"]
    if j.get("experience"):
        facts.append(f"الخبرة: {j['experience']} سنوات")
    if j.get("shift"):
        facts.append(f"الدوام: {j['shift']}")
    if j.get("salary"):
        facts.append(f"الراتب: {j['salary']}")
    parts.append("<ul>" + "".join(f"<li>{e(f)}</li>" for f in facts) + "</ul>")
    if j.get("tasks"):
        parts.append("<p>المهام:</p><ul>" + "".join(f"<li>{e(t)}</li>" for t in j["tasks"]) + "</ul>")
    if j.get("skills"):
        parts.append("<p>المهارات المطلوبة:</p><ul>" + "".join(f"<li>{e(s)}</li>" for s in j["skills"]) + "</ul>")
    return "".join(parts)


def job_posting(j: dict, url: str, employer: str, cities: list[str], edu_label: str) -> dict | None:
    """schema.org JobPosting for one open job, or None when Google's required fields are missing."""
    if not employer or not is_open(j) or not j.get("title") or not j.get("posted_at"):
        return None
    country = COUNTRY_ISO.get(j.get("country") or "sy", "SY")
    real_cities = [c for c in cities if c not in _MULTI_CITY]
    city_txt = "، ".join(cities) if cities else ("سوريا" if country == "SY" else "تركيا")
    ld: dict = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": j["title"],
        "description": _description_html(j, city_txt, edu_label),
        "datePosted": str(j["posted_at"])[:10],
        "hiringOrganization": {"@type": "Organization", "name": employer},
        "url": url,
        "identifier": {"@type": "PropertyValue", "name": config.SITE_NAME_EN, "value": str(j.get("id"))},
    }
    valid = j.get("deadline") or j.get("expires_on")
    if valid:
        ld["validThrough"] = f"{str(valid)[:10]}T23:59:59+03:00"
    if et := _employment_type(j):
        ld["employmentType"] = et
    remote = any(w in " ".join([j.get("title") or "", *(j.get("tags") or [])]).lower() for w in _REMOTE_WORDS)
    if remote:
        ld["jobLocationType"] = "TELECOMMUTE"
        ld["applicantLocationRequirements"] = {"@type": "Country", "name": country}
    else:
        places = real_cities[:5] or [None]
        ld["jobLocation"] = [
            {"@type": "Place", "address": {"@type": "PostalAddress", **({"addressLocality": c} if c else {}),
                                           "addressCountry": country}}
            for c in places
        ]
    if j.get("experience"):
        ld["experienceRequirements"] = {"@type": "OccupationalExperienceRequirements",
                                        "monthsOfExperience": int(j["experience"]) * 12}
    return ld


def ld_script(ld: dict | None) -> str:
    """The <script> tag, safe to drop into HTML (no «</script>» can end it early)."""
    if not ld:
        return ""
    body = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{body}</script>'


def sitemap_xml(con, base: str) -> str:
    """Home, the tool pages, and every open job page."""
    today = date.today().isoformat()
    since = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).isoformat()
    rows = con.execute(
        "SELECT id, updated_at FROM jobs WHERE status='live' AND expires_on>=? "
        "AND (deadline IS NULL OR deadline='' OR deadline>=?) AND posted_at>=? ORDER BY posted_at DESC LIMIT ?",
        (today, today, since, SITEMAP_LIMIT),
    ).fetchall()
    urls = [(f"{base}/", today), (f"{base}/?view=jobs", today), (f"{base}/pulse.html", today),
            (f"{base}/scam.html", None), (f"{base}/cv.html", None), (f"{base}/employer.html", None),
            (f"{base}/sources.html", today)]
    urls += [(f"{base}/j/{r['id']}", (r["updated_at"] or "")[:10] or None) for r in rows]
    items = "".join(
        f"<url><loc>{xml_escape(u)}</loc>" + (f"<lastmod>{m}</lastmod>" if m else "") + "</url>" for u, m in urls
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'


def robots_txt(base: str) -> str:
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin.html\n\nSitemap: {base}/sitemap.xml\n"
