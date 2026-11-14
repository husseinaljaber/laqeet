"""Fetch Syria humanitarian & UN job opportunities from ReliefWeb RSS feed."""
from __future__ import annotations

import html
import logging
import re
import warnings
from datetime import datetime, timezone
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import httpx

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .. import direct_apply as da
from .. import taxonomy as tx
from ..classify import classify as computer_subfield
from ..models import Job

log = logging.getLogger("reliefweb")

RELIEFWEB_RSS_URL = "https://reliefweb.int/jobs/rss.xml?country=226"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def parse_reliefweb_rss(xml_text: str) -> list[Job]:
    soup = BeautifulSoup(xml_text, "html.parser")
    items = soup.find_all("item")
    jobs: list[Job] = []

    for item in items:
        try:
            title_tag = item.find("title")
            link_tag = item.find("link")
            desc_tag = item.find("description")
            pubdate_tag = item.find("pubdate") or item.find("dc:date")

            if not title_tag:
                continue

            raw_title = title_tag.get_text().strip()
            # Often in format "Job Title - Organization Name" or "Job Title (Syria)"
            title = raw_title
            employer = "منظمة دولية / وكالة أممية"
            if " - " in raw_title:
                parts = raw_title.rsplit(" - ", 1)
                title = parts[0].strip()
                employer = parts[1].strip()
            elif " (" in raw_title and raw_title.endswith(")"):
                m = re.match(r"^(.*?)\s*\((.*?)\)$", raw_title)
                if m:
                    title = m.group(1).strip()
                    loc_or_emp = m.group(2).strip()
                    if loc_or_emp and "syria" not in loc_or_emp.lower():
                        employer = loc_or_emp

            url = ""
            if link_tag:
                # In RSS with html.parser, link text might be in next_sibling or text
                url = link_tag.get_text().strip() or (link_tag.next_sibling.strip() if link_tag.next_sibling else "")
            if not url and item.find("guid"):
                url = item.find("guid").get_text().strip()

            desc = desc_tag.get_text().strip() if desc_tag else ""
            raw_html = html.unescape(desc)
            info = da.extract_apply_from_html(raw_html, url) if raw_html else {}
            desc_clean = re.sub(r"<[^>]+>", " ", raw_html)

            # Date
            posted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if pubdate_tag:
                try:
                    # e.g. Wed, 11 Nov 2026 09:30:00 +0000
                    dt = datetime.strptime(pubdate_tag.get_text()[:25].strip(), "%a, %d %b %Y %H:%M:%S")
                    posted_at = dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
                except Exception:
                    pass

            cities = tx.cities_from_text(raw_title + " " + desc_clean)
            if not cities:
                if "damascus" in (raw_title + " " + desc_clean).lower() or "دمشق" in (raw_title + " " + desc_clean):
                    cities = ["دمشق"]
                elif "aleppo" in (raw_title + " " + desc_clean).lower() or "حلب" in (raw_title + " " + desc_clean):
                    cities = ["حلب"]
                elif "homs" in (raw_title + " " + desc_clean).lower() or "حمص" in (raw_title + " " + desc_clean):
                    cities = ["حمص"]
                else:
                    cities = ["عدة محافظات"]

            # NGO jobs usually require bachelor's unless stated otherwise
            edu = tx.education_from_text(desc_clean) or tx.education_from_title(title) or "bachelor"

            fld = tx.field_from_text(title, desc_clean)
            sub = computer_subfield(title, desc_clean)
            if sub or fld == "computer":
                fld, sub = "computer", sub or "DT"
            elif fld == "other":
                fld = "ngo"

            exp = tx.experience_years(desc_clean)
            email = info.get("email")
            phones = info.get("phones") or []
            extracted_url = info.get("apply_url")
            # If RSS description already gave an email/phone without a direct form URL, don't mask it behind the ReliefWeb page URL
            apply_url = extracted_url if extracted_url else (None if (email or phones) else url)
            app_types = tx.infer_apply_types(apply_url, phones, False, email)

            job = Job(
                title=title[:120],
                source="reliefweb",
                post_url=url,
                posted_at=posted_at,
                employer=employer[:100],
                cities=cities,
                education=edu,
                education_from="stated",
                field=fld,
                subfield=sub,
                kind="formal",
                official=False,
                country="sy",
                sector="ngo",
                apply_url=apply_url,
                details_url=url,
                phones=phones,
                email=email,
                apply_types=app_types,
                experience=exp,
                confidence=0.9,
                tags=["عمل إنساني", "منظمات دولية"],
                raw=desc_clean[:2000],
            )
            jobs.append(job)
        except Exception as e:
            log.warning("reliefweb item failed: %s", e)
            continue

    return jobs


def parse_reliefweb_html(html_text: str) -> list[Job]:
    """Parse ReliefWeb Syria job cards from paginated HTML search results."""
    soup = BeautifulSoup(html_text, "html.parser")
    today_str = datetime.now(timezone.utc).date().isoformat()
    jobs: list[Job] = []
    for art in soup.select("article.rw-river-article--job, article.rw-river-article"):
        a = art.select_one("h3.rw-river-article__title a[href], h3 a[href]")
        if not a:
            continue
        url = a["href"].strip()
        if not url.startswith("http"):
            url = "https://reliefweb.int" + url
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        country_el = art.select_one(".rw-entity-country-slug")
        country_txt = country_el.get_text(" ", strip=True) if country_el else "Syria"
        cities = tx.cities_from_text(title)
        if "syria" not in country_txt.lower() and not cities:
            continue
        org_el = art.select_one(".rw-entity-meta__tag-value--source")
        employer = org_el.get_text(" ", strip=True) if org_el else "منظمة دولية / وكالة أممية"
        posted_el = art.select_one(".rw-entity-meta__tag-value--posted time[datetime]")
        posted_at = (posted_el["datetime"][:19] + "+00:00") if (posted_el and posted_el.get("datetime")) else datetime.now(timezone.utc).isoformat(timespec="seconds")
        close_el = art.select_one(".rw-entity-meta__tag-value--closing-date time[datetime]")
        deadline = close_el["datetime"][:10] if (close_el and close_el.get("datetime")) else None
        if deadline and deadline < today_str:
            continue
        if not cities:
            low = title.lower()
            if "damascus" in low:
                cities = ["دمشق"]
            elif "aleppo" in low:
                cities = ["حلب"]
            elif "homs" in low:
                cities = ["حمص"]
            else:
                cities = ["عدة محافظات"]
        fld = tx.field_from_text(title)
        sub = computer_subfield(title)
        if sub or fld == "computer":
            fld, sub = "computer", sub or "DT"
        elif fld == "other":
            fld = "ngo"
        jobs.append(Job(
            title=title[:120],
            source="reliefweb",
            post_url=url,
            posted_at=posted_at,
            employer=employer[:100],
            cities=cities,
            education=tx.education_from_title(title) or "bachelor",
            education_from="stated",
            field=fld,
            subfield=sub,
            kind="formal",
            official=False,
            country="sy",
            sector="ngo",
            apply_url=url,
            details_url=url,
            deadline=deadline,
            apply_types=["site"],
            confidence=0.9,
            tags=["عمل إنساني", "منظمات دولية"],
            raw=title,
        ))
    return jobs


def fetch_reliefweb_jobs(client: httpx.Client | None = None, cache: dict | None = None) -> list[Job]:
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    cache = cache if cache is not None else {}
    jobs: list[Job] = []
    seen: set[str] = set()
    try:
        try:
            r = client.get(RELIEFWEB_RSS_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
            if r.status_code == 200:
                for j in parse_reliefweb_rss(r.text):
                    if j.post_url not in seen:
                        seen.add(j.post_url)
                        jobs.append(j)
        except Exception as e:
            log.warning("reliefweb RSS failed: %s", e)

        for page in range(0, 5):
            try:
                hr = client.get(f"https://reliefweb.int/jobs?advanced-search=%28C226%29&page={page}")
                if hr.status_code != 200:
                    break
                page_jobs = parse_reliefweb_html(hr.text)
                if not page_jobs:
                    break
                for j in page_jobs:
                    if j.post_url not in seen:
                        seen.add(j.post_url)
                        jobs.append(j)
            except Exception as e:
                log.debug("reliefweb page %s failed: %s", page, e)
                break

        return [j for j in jobs if da.resolve_job_apply(j, client=client, cache=cache)]
    except Exception as e:
        log.warning("reliefweb fetch failed: %s", e)
        return []
    finally:
        if own:
            client.close()
