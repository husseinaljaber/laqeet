"""Direct-apply URL verification and wrapper unwrapping.

Guarantees that every job displayed on Laqeet offers a real, verified way to apply:
- a direct application form / ATS / official recruitment link, OR
- a real email address, OR
- a verified Syrian phone / WhatsApp number.

Listing aggregators (tawzeefportal, job.sy, hanwazafak, baaeed, indeed, linkedin),
social feeds/channels (facebook, instagram, t.me, whatsapp.com/channel, wa.me/message),
and bare Drive folders are never kept as `apply_url`. Wrapper/detail pages (ataarelief,
worklink, ngosquare, sarc, shafak, srd, ida, sema, reliefweb) and URL shorteners are
unwrapped to the real inner application link, email, or phone.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import httpx

from . import taxonomy as tx
from .models import Job

log = logging.getLogger("direct_apply")

_SOCIAL_HOSTS = {
    "t.me", "telegram.me", "telegram.dog", "facebook.com", "fb.com", "fb.watch",
    "m.facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "youtu.be", "tiktok.com", "snapchat.com", "threads.net", "pinterest.com",
}

_AGGREGATOR_HOSTS = {
    "tawzeefportal.com", "job.sy", "hanwazafak.com", "baaeed.com", "indeed.com",
    "eg.indeed.com", "tr.indeed.com", "jooble.org", "bayt.com", "wuzzuf.net", "forasna.com",
    "akhtaboot.com", "tanqeeb.com", "unjobnet.org", "gulftalent.com",
    "naukrigulf.com", "linkedin.com", "glassdoor.com", "remotive.com",
    "freelancer.com", "upwork.com", "mostaql.com", "khamsat.com",
    "syrianjobs.net", "jobs.sy", "phoenijobs.sy", "lmo.sy",
    "mahereen.com", "wazifturk.com", "kariyer.net", "eleman.net", "yenibiris.com",
    "isinolsun.com", "opensooq.com", "tr.opensooq.com", "impactpool.org", "ureed.com",
}

_SHORTENER_HOSTS = {
    "bit.ly", "shorturl.at", "tinyurl.com", "share.google", "cutt.ly",
    "is.gd", "rebrand.ly", "rb.gy", "t.co", "ow.ly", "lnkd.in", "tr.ee", "goo.gl", "q.me-qr.com",
}

_WRAPPER_HOSTS = {
    "ataarelief.org", "worklink.sy", "ngosquare.com", "sarc.sy", "shafak.org",
    "srd.org.tr", "ahf.ngo", "dan-ngo.org", "sanad.ngo", "sened.ngo", "bahar.ngo", "sema-sy.org",
    "ida-org.com", "mikrotiksyria.com", "reliefweb.int", "sana.sy", "mohe.gov.sy",
    "spu.edu.sy", "unicef.org", "solidarites.org", "acu-sy.org", "beyazeller.org",
    "ipv-sy.org", "medglobal.org", "ghirasalkhaeer.com", "sycac.org", "ghawth.org",
    "salam-sy.org", "medica.sy", "lgsprefabsy.com", "almaqala.blog", "arabicline.com",
    "sqrjobs.net", "jobsyria.org", "nsjobs.net", "jobinsyria.com", "ngossyria.com",
    "syria.un.org", "au.edu.sy", "jude.edu.sy",
    "jobsintr.net", "salamjobs.com", "adwhit.com", "ab-ilan.com",
}

_DIRECT_FORM_OR_ATS_RE = re.compile(
    r"(?:docs\.google\.com/forms|forms\.gle|forms\.office\.com|forms\.cloud\.microsoft|"
    r"ee(?:-eu)?\.kobotoolbox\.org|ee\.humanitarianresponse\.info|enketo\.ona\.io|"
    r"(?:form\.)?jotform\.com|typeform\.com|airtable\.com/(?:app|shr)|tally\.so|surveymonkey\.com|"
    r"zfrmz\.com|script\.google\.com/macros/|"
    r"apply\.hiring\.moad\.gov\.sy|moad\.gov\.sy/recruitment/jobs|recruitment\.ssf\.gov\.sy|"
    r"career\.ihsanrd\.org|jobs\.unicef\.org|careers\.un\.org|inspira\.un\.org|"
    r"bamboohr\.com|hrpeak\.com|hr-manager\.net|baseilowcloud\.com|emply\.com|jobs\.tdh\.org|"
    r"greenhouse\.io|lever\.co|myworkdayjobs\.com|myworkdaysite\.com|smartrecruiters\.com|workable\.com|"
    r"recruitee\.com|taleo\.net|successfactors\.(?:com|eu)|oraclecloud\.com|ultipro\.com|csod\.com|"
    r"zohorecruit\.com|odoo\.com/jobs|webcruiter\.no|employ\.acf-e\.org|careers-page\.com|"
    r"sams-hr\.com|talkpu\.sh|erp\.bonyan-ngo\.org|sssd-ngo\.net|alignerr\.com|mercor\.com|creativetaskflow\.com|"
    r"sened\.org\.tr/job_ad/|jobsintr\.net/jobs/[^/?#]+$|"
    r"ee\.moi-sy\.com|au\.edu\.sy/job/)",
    re.I,
)

_IGNORE_EMAIL_SUBSTRS = (
    "ngosquare.com", "ida-org.com", "worklink.sy", "tawzeefportal", "hanwazafak",
    "contact@jobinsyria.com", "info@phoenitech.", "info@jobsyria.org", "info@ngossyria.com", "info@lmo.sy",
    "info@sarc-sy.org", "info@shafak.org", "info@srd.org.tr", "info@ataarelief.org",
    "info@sema-sy.org", "info@bahar.ngo", "info@sened.ngo",
    "info@jobsintr.net", "info@salamjobs.com", "info@mahereen.com", "info@wazifturk.com",
    "info@ab-ilan.com", "info@adwhit.com", "employee@gmail.com", "kvkk@",
    "noreply@", "no-reply@", "donotreply@", "webmaster@", "wordpress@", "wixpress",
    "sentry.io", "example.com", "domain.com", ".png", ".jpg", ".svg",
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

_CLOSED_PAGE_RE = re.compile(
    r"no longer accepting applications|this job has expired|expired job|position has been filled|"
    r"انتهى موعد التقديم|تم إغلاق الشاغر|تمت تعبئة الشاغر|الشاغر لم يعد متاح|"
    r"başvuru süresi dolmuştur|ilan yayından kaldırılmıştır|ilan süresi doldu",
    re.I,
)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")


def _matches_domain_set(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def strip_tracking(url: str) -> str:
    """Drop noisy query parameters (fbclid, utm_*, highlightedUpdateUrn) without altering form URLs."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    qs = parse_qs(p.query, keep_blank_values=True)
    drop = {k for k in qs if k.lower().startswith("utm_") or k.lower() in {"fbclid", "gclid", "mc_cid", "mc_eid", "rcm"}}
    if not drop:
        return url.strip()
    kept = [(k, v) for k, vals in qs.items() if k not in drop for v in vals]
    return urlunparse(p._replace(query=urlencode(kept)))


def is_social_or_channel(url: str) -> bool:
    u = url.lower()
    h = _host(u)
    if _matches_domain_set(h, _SOCIAL_HOSTS):
        return True
    if "whatsapp.com/channel" in u or "chat.whatsapp.com/" in u or "wa.me/message/" in u:
        return True
    if "whatsapp.com/send" in u and "phone=" not in u:
        return True
    # Pure wa.me/?text=... share buttons without phone recipient
    if h == "wa.me" and urlparse(u).path.strip("/") == "":
        return True
    return False


def is_file_folder_or_viewer(url: str) -> bool:
    h = _host(url)
    u = url.lower()
    if h == "drive.google.com" or h == "docs.google.com" and "/forms" not in u:
        return True
    if h.endswith("sharepoint.com") or h == "dropbox.com":
        return True
    return False


def is_aggregator_or_wall(url: str) -> bool:
    return _matches_domain_set(_host(url), _AGGREGATOR_HOSTS)


def is_shortener(url: str) -> bool:
    return _matches_domain_set(_host(url), _SHORTENER_HOSTS)


def is_wrapper_portal(url: str) -> bool:
    return _matches_domain_set(_host(url), _WRAPPER_HOSTS)


def is_direct_form_or_ats(url: str) -> bool:
    if is_social_or_channel(url) or is_aggregator_or_wall(url) or is_file_folder_or_viewer(url):
        return False
    if _DIRECT_FORM_OR_ATS_RE.search(url):
        return True
    h = _host(url)
    return h.startswith(("apply.", "career.", "careers.", "jobs.", "hire.", "hiring.", "recruitment."))


def is_disallowed_offline_url(url: str) -> bool:
    """Fast static check when no HTTP client is available."""
    return is_social_or_channel(url) or is_aggregator_or_wall(url) or is_file_folder_or_viewer(url)


def extract_phone_from_wa(url: str, country: str = "sy") -> list[str]:
    if country == "tr":
        m = re.search(r"(?:wa\.me/|phone=(?:%2B|\+)?)(?:90|0090|\+90)?(5\d{9})\b", url, re.I)
        if m:
            p = "0" + m.group(1)
            return [] if p in tx._TR_BAD_PHONES else [p]
        return []
    m = re.search(r"(?:wa\.me/|phone=(?:%2B|\+)?)(?:963|00963|\+963)?(9\d{8})\b", url, re.I)
    return ["0" + m.group(1)] if m else []


def _decode_cf_email(hex_str: str) -> str:
    """Decode Cloudflare email-protection XOR hex strings served in HTML anchors."""
    try:
        data = bytes.fromhex(hex_str.strip())
        if len(data) < 4:
            return ""
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _good_email(em: str) -> bool:
    low = em.lower().strip(".,;:")
    if any(bad in low for bad in _IGNORE_EMAIL_SUBSTRS):
        return False
    return bool(re.fullmatch(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", low, re.I))


def _has_application_form(soup: BeautifulSoup) -> bool:
    """True when the page embeds an actual job application HTML form (not search/login/newsletter)."""
    for form in soup.find_all("form"):
        cls_id = " ".join(form.get("class") or []) + " " + (form.get("id") or "") + " " + (form.get("action") or "")
        if re.search(r"search|login|register|newsletter|subscribe|comment|forgot|filter|s=", cls_id, re.I):
            continue
        if form.find("input", attrs={"type": "file"}):
            return True
        inputs = [i.get("name", "") + " " + i.get("placeholder", "") + " " + i.get("type", "")
                  for i in form.find_all(["input", "textarea", "select"]) if i.get("type") not in ("hidden", "submit", "button")]
        joined = " ".join(inputs).lower()
        if len(inputs) >= 3 and re.search(r"name|الاسم|email|ايميل|البريد|phone|هاتف|cv|resume|سيرة|cover|تقديم", joined):
            return True
    return False


def _score_candidate_link(href: str, text: str, page_url: str) -> int:
    """Rank external/anchor candidates found on a job page; higher is better, <=0 ignored."""
    low_href = href.lower()
    low_txt = text.lower()
    if low_href.startswith(("#", "javascript:", "tel:", "mailto:")) or "wa.me/" in low_href or "whatsapp.com/" in low_href:
        return 0
    full = urljoin(page_url, href)
    if not full.startswith(("http://", "https://")):
        return 0
    if is_social_or_channel(full) or is_aggregator_or_wall(full) or is_file_folder_or_viewer(full):
        return 0
    # Avoid self links, pagination, language switchers, category pages
    if urlparse(full).path.rstrip("/") == urlparse(page_url).path.rstrip("/"):
        return 0
    if re.search(r"[?&](?:lang|replytocom|share)=|/category/|/tag/|/jobs/?$|/vacancies/?$|/wp-login|/login|/account/|/register|/signin", low_href):
        return 0
    if is_direct_form_or_ats(full):
        return 100
    if is_shortener(full):
        return 75
    if re.search(r"apply|تقديم|قد[ّ]?م|استمارة|التسجيل|توظيف|application|recruit|başvuru|basvuru|career|job_ad|vacanc|tıklay", low_txt + " " + low_href):
        return 60
    return 0


def extract_apply_from_html(html_text: str, page_url: str, country: str = "sy") -> dict:
    """Pull direct form/ATS URLs, career emails, phones, and internal form signal from HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    for noise in soup.select(".sj-card, .similar-jobs, .related-jobs"):
        noise.decompose()
    text = soup.get_text(" ", strip=True)
    closed = bool(_CLOSED_PAGE_RE.search(text))
    phone_fn = tx.phones_tr if country == "tr" else tx.phones

    best_url = None
    best_score = 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        sc = _score_candidate_link(href, a.get_text(" ", strip=True), page_url)
        if sc > best_score:
            best_score = sc
            best_url = strip_tracking(urljoin(page_url, href))

    # Also check embedded iframes (JotForm / Google Forms / Kobo)
    if best_score < 90:
        for fr in soup.find_all("iframe", src=True):
            src = fr["src"].strip()
            if _DIRECT_FORM_OR_ATS_RE.search(src):
                best_url = strip_tracking(urljoin(page_url, src))
                best_score = 95
                break

    # Extract mailto:, Cloudflare email-protection, tel:/wa.me phones, and body emails/phones
    emails: list[str] = []
    wa_phones: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            cand = unquote(href[7:].split("?", 1)[0]).strip()
            if _good_email(cand) and cand not in emails:
                emails.append(cand)
        elif "/cdn-cgi/l/email-protection#" in low:
            cand = _decode_cf_email(href.split("#", 1)[1].split("?", 1)[0])
            if _good_email(cand) and cand not in emails:
                emails.append(cand)
        else:
            for wp in extract_phone_from_wa(href, country=country):
                if wp not in wa_phones:
                    wa_phones.append(wp)
            if low.startswith("tel:"):
                for tp in phone_fn(unquote(href[4:])):
                    if tp not in wa_phones:
                        wa_phones.append(tp)

    for el in soup.find_all(attrs={"data-cfemail": True}):
        cand = _decode_cf_email(el.get("data-cfemail") or "")
        if _good_email(cand) and cand not in emails:
            emails.append(cand)

    main_node = soup.find("main") or soup.find("article") or soup.select_one(".entry-content,.post-content,.job-description") or soup
    for noise in main_node.select("header, footer, nav, .site-footer, .site-header"):
        noise.decompose()
    main_text = main_node.get_text(" ", strip=True)
    for em in tx.emails(main_text):
        if _good_email(em) and em not in emails:
            emails.append(em)

    # Prefer career/hr/job emails first
    emails.sort(key=lambda e: 0 if re.search(r"career|hr|job|recruit|apply|cv|talent|وظ|توظيف", e, re.I) else 1)
    phones = sorted(set(phone_fn(main_text) + wa_phones))

    # Also catch raw form URLs pasted as plain text without <a>
    if not best_url:
        for u in tx.urls(main_text):
            if is_direct_form_or_ats(u):
                best_url = strip_tracking(u)
                break

    return {
        "apply_url": best_url,
        "email": emails[0] if emails else None,
        "phones": phones,
        "has_internal_form": _has_application_form(soup),
        "closed": closed,
    }


def _resolve_url_live(url: str, client: httpx.Client, cache: dict, depth: int = 0, country: str = "sy") -> dict:
    """Resolve shorteners & wrapper portals with an in-memory per-run cache."""
    cache_key = (url, country)
    if cache_key in cache:
        return cache[cache_key]
    res = {"status": 0, "final_url": url, "apply_url": None, "email": None, "phones": [], "has_internal_form": False, "closed": False, "dead": False}
    try:
        r = client.get(url, headers=_BROWSER_HEADERS, timeout=12, follow_redirects=True)
        res["status"] = r.status_code
        final = strip_tracking(str(r.url)) or url
        res["final_url"] = final
        if r.status_code in (404, 410):
            res["dead"] = True
            cache[cache_key] = res
            return res
        if is_disallowed_offline_url(final):
            res["dead"] = True
            cache[cache_key] = res
            return res
        if is_direct_form_or_ats(final):
            res["apply_url"] = final
            cache[cache_key] = res
            return res
        if r.status_code == 200 and "html" in r.headers.get("content-type", "").lower():
            page_html = r.content.decode("utf-8", "replace") if (r.encoding or "").lower() in ("", "iso-8859-1", "latin-1") else r.text
            info = extract_apply_from_html(page_html, final, country=country)
            res.update(info)
            if res["apply_url"] and is_shortener(res["apply_url"]) and depth < 1:
                sub = _resolve_url_live(res["apply_url"], client, cache, depth + 1, country=country)
                if sub.get("apply_url"):
                    res["apply_url"] = sub["apply_url"]
                elif sub.get("dead"):
                    res["apply_url"] = None
    except Exception as e:
        log.debug("resolve %s failed: %s", url, e)
    cache[cache_key] = res
    return res


def resolve_job_apply(job: Job, client: httpx.Client | None = None, cache: dict | None = None) -> bool:
    """Normalize/unwrap a job's apply link and return True iff it has a real way to apply."""
    cache = cache if cache is not None else {}
    country = "tr" if getattr(job, "country", "sy") == "tr" else "sy"

    # 1. Clean URLs, rescue email mistyped as URL, & extract embedded wa.me phones
    if job.email:
        em = job.email.strip()
        job.email = em if _good_email(em) else None
    if job.apply_url:
        raw_netloc = urlparse(job.apply_url if "://" in job.apply_url else f"https://{job.apply_url}").netloc
        if "@" in raw_netloc and _good_email(raw_netloc):
            if not job.email:
                job.email = raw_netloc.strip()
            job.apply_url = None
        else:
            job.apply_url = strip_tracking(job.apply_url) or None
    if job.details_url:
        job.details_url = strip_tracking(job.details_url) or None
    if job.apply_url:
        for p in extract_phone_from_wa(job.apply_url, country=country):
            if p not in job.phones:
                job.phones.append(p)
        job.phones = sorted(set(job.phones))

    # 2. Enforce static rejections (aggregators, social, channels, folders/drive viewers)
    if job.apply_url and is_disallowed_offline_url(job.apply_url):
        if is_file_folder_or_viewer(job.apply_url) and not job.details_url:
            job.details_url = job.apply_url
        job.apply_url = None

    # 3. Live resolution for shorteners, wrapper pages, and unverified generic URLs when online client is passed
    should_resolve = (
        client is not None
        and job.apply_url
        and not _host(job.apply_url) == "wa.me"
        and (is_shortener(job.apply_url) or is_wrapper_portal(job.apply_url) or not is_direct_form_or_ats(job.apply_url))
    )
    if should_resolve:
        orig = job.apply_url
        res = _resolve_url_live(orig, client, cache, country=country)
        if res["closed"]:
            job.apply_url = None
            job.email = None
            job.phones = []
            return False
        if res["dead"] or res["status"] in (404, 410):
            job.apply_url = None
        elif res["status"] == 200:
            if res["email"] and not job.email:
                job.email = res["email"]
            for p in res["phones"]:
                if p not in job.phones:
                    job.phones.append(p)
            job.phones = sorted(set(job.phones))

            if res["apply_url"]:
                if orig != res["apply_url"] and not job.details_url and not is_shortener(orig):
                    job.details_url = orig
                job.apply_url = res["apply_url"]
            elif res["has_internal_form"] or is_direct_form_or_ats(res["final_url"]):
                job.apply_url = res["final_url"]
            elif res["email"] or res["phones"]:
                # Applying is via email or phone on the detail page; move article link to details_url.
                if not job.details_url:
                    job.details_url = res["final_url"]
                job.apply_url = None
            else:
                # Page returned 200 OK and had NO form, NO apply link, NO email, NO phone.
                job.apply_url = None
        elif is_shortener(orig):
            # Unreachable/blocked shortener cannot safely be kept as direct link.
            job.apply_url = None
        elif is_wrapper_portal(orig) and (job.email or job.phones):
            if not job.details_url:
                job.details_url = orig
            job.apply_url = None

    # 4. Final strict requirement: MUST have at least ONE real direct contact/apply method
    if job.apply_url and is_disallowed_offline_url(job.apply_url):
        job.apply_url = None
    has_way = bool(job.apply_url or job.phones or job.email)
    if not has_way:
        return False

    job.apply_types = tx.infer_apply_types(job.apply_url, job.phones, job.whatsapp, job.email)
    return True
