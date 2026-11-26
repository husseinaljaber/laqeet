"""HTTP API and the website.  uvicorn app.api:app --port 8000"""
from __future__ import annotations

import gc
import hmac
import html
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, db, direct_apply as da, nlsearch as nls, search as sq, seo, spam
from . import taxonomy as tx
from .arabic import soft_fold
from .classify import CATEGORIES

log = logging.getLogger("api")
con = db.connect()
from . import accounts as _accounts  # noqa: E402

_accounts.ensure_schema(con)


def _auto_collect_loop(stop: threading.Event) -> None:
    """Keep the database fresh even when `uvicorn app.api:app` is run on its own without `app.run`."""
    from .collect import collect

    gap = max(60, config.UPDATE_MINUTES * 60)
    # Yield on startup so low-RAM hosts finish booting and pass healthchecks before scraping begins.
    if stop.wait(90):
        return
    while not stop.is_set():
        wait = gap
        try:
            c = db.connect()
            try:
                last = db.get_meta(c, "updated_at")
                expired = db.expire_past(c)
                if expired:
                    c.commit()
                    _stats_cache.clear()
            finally:
                c.close()
            stale = True
            if last:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
                stale = age >= gap
                if not stale:
                    wait = max(30, int(gap - age))
            if stale:
                log.info("auto-collect starting...")
                res = collect(quick=bool(last), verify_limit=20)
                _stats_cache.clear()
                gc.collect()
                log.info("auto-collect done: %s", res)
                wait = gap
        except Exception:
            log.exception("auto-collect failed")
        stop.wait(wait)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    stop = threading.Event()
    if not os.getenv("PYTEST_CURRENT_TEST") and os.getenv("AUTO_COLLECT", "1") == "1":
        threading.Thread(target=_auto_collect_loop, args=(stop,), daemon=True, name="auto-collect").start()
    try:
        yield
    finally:
        stop.set()


app = FastAPI(title=config.SITE_NAME_EN, docs_url="/api/docs", lifespan=_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # slow connections: ~600KB first load shrinks to a fraction

# what each education level sees by default (a master's holder still applies to «إجازة جامعية» jobs)
_DEFAULT_LEVELS = {"phd": ["phd", "master", "bachelor"], "master": ["master", "bachelor"]}


def _today() -> str:
    return date.today().isoformat()


def _since() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).isoformat()


def _levels(edu: str | None, lower: bool) -> list[str] | None:
    if not edu:
        return None
    edus = [e.strip() for e in edu.split(",") if e.strip() in tx.EDU_RANK]
    if not edus:
        return None
    if len(edus) == 1:
        if lower:
            return [c for c, _ in tx.EDUCATION if tx.EDU_RANK[c] <= tx.EDU_RANK[edus[0]]]
        return _DEFAULT_LEVELS.get(edus[0], [edus[0]])
    out: list[str] = []
    for e in edus:
        if e not in out:
            out.append(e)
    return out


_LIGHT_COLS = (
    "id, fp, title, source, country, sector, apply_types, education, education_from, "
    "field, subfield, kind, official, cities, experience, deadline, posted_at, expires_on, "
    "status, reports, first_seen, updated_at"
)


# «دوام جزئي»، «من البيت»… from the plain-language search (app/nlsearch.py).
_SHIFT_SQL = {
    "part": "AND json_extract(data, '$.shift') LIKE '%جزئي%'",
    "full": "AND json_extract(data, '$.shift') LIKE '%كامل%'",
    "evening": "AND json_extract(data, '$.shift') LIKE '%مسائي%'",
    "morning": "AND json_extract(data, '$.shift') LIKE '%صباحي%'",
    "night": "AND json_extract(data, '$.shift') LIKE '%ليلي%'",
    "remote": "AND json_extract(data, '$.tags') LIKE '%عن بعد%'",
}


def _query(edu=None, lower=False, field=None, sub=None, city=None, kind=None, q=None, official=None,
           expired=False, status="live", since_days=None, gender=None, country=None, sector=None, apply=None,
           edu_group=None, source=None, exp=None, instant: bool = False, sort: str = "relevance", light: bool = False,
           shift: str | None = None):
    has_q = bool(q and q.strip())
    cols = "*" if (has_q or not light) else _LIGHT_COLS
    if expired and status == "live":
        sql = [f"SELECT {cols} FROM jobs WHERE status IN ('live', 'expired') AND posted_at >= ?"]
        args: list = [_since()]
    else:
        sql = [f"SELECT {cols} FROM jobs WHERE status=? AND posted_at >= ?"]
        args: list = [status, _since()]
    if not expired:
        today_s = _today()
        sql.append("AND expires_on >= ? AND (deadline IS NULL OR deadline='' OR deadline >= ?)")
        args.extend([today_s, today_s])
    sql.append("AND country=?")
    args.append("tr" if country == "tr" else "sy")
    if sector in ("gov", "ngo", "private"):
        sql.append("AND sector=?")
        args.append(sector)
    if source:
        sql.append("AND (source=? OR id IN (SELECT job_id FROM job_sources WHERE source=?))")
        args.extend([source, source])
    applies_sel = [a.strip() for a in (apply or "").split(",") if a.strip() in tx.APPLY_LABEL]
    if applies_sel:
        sql.append("AND (" + " OR ".join(["apply_types LIKE ?"] * len(applies_sel)) + ")")
        args.extend([f"%|{a}|%" for a in applies_sel])
    if (levels := _levels(edu, lower)) is not None:
        if edu_group == "degree":
            levels = [c for c in levels if c in ("phd", "master", "bachelor", "institute", "secondary")]
        elif edu_group == "nodegree":
            levels = [c for c in levels if c in ("basic", "none")]
        if levels:
            # Ads that don't name a degree («الشهادة مو مذكورة») stay visible to everyone, same as in the bot.
            sql.append(f"AND (education IN ({','.join('?' * len(levels))}) OR education IS NULL)")
            args += levels
    elif edu_group == "degree":
        sql.append("AND (education IN ('phd', 'master', 'bachelor', 'institute', 'secondary') OR education IS NULL)")
    elif edu_group == "nodegree":
        sql.append("AND (education IN ('basic', 'none') OR education IS NULL)")
    exp_clauses: list[str] = []
    for e_code in [x.strip() for x in (exp or "").split(",") if x.strip()]:
        if e_code == "unk":
            exp_clauses.append("(experience IS NULL OR experience=0)")
        elif e_code == "low":
            exp_clauses.append("(experience IS NOT NULL AND experience <= 2)")
        elif e_code == "mid":
            exp_clauses.append("(experience >= 3 AND experience <= 4)")
        elif e_code == "high":
            exp_clauses.append("experience >= 5")
    if exp_clauses:
        sql.append("AND (" + " OR ".join(exp_clauses) + ")")
    if instant:
        portal_checks = " AND ".join(["LOWER(COALESCE(json_extract(data, '$.apply_url'), '')) NOT LIKE ?"] * len(da.ACCOUNT_PORTAL_HOST_SUBSTRS))
        sql.append(
            "AND (apply_types LIKE '%|whatsapp|%' OR apply_types LIKE '%|email|%' OR apply_types LIKE '%|call|%' "
            f"OR (COALESCE(json_extract(data, '$.apply_url'), '') != '' AND {portal_checks}))"
        )
        args.extend([f"%{sub}%" for sub in da.ACCOUNT_PORTAL_HOST_SUBSTRS])
    cs = [c.strip() for c in (city or "").split(",") if c.strip()]
    if cs:
        multi_city = "%|عدة ولايات في تركيا|%" if country == "tr" else "%|عدة محافظات|%"
        sql.append("AND (cities LIKE ? OR " + " OR ".join(["cities LIKE ?"] * len(cs)) + ")")
        args.append(multi_city)
        args += [f"%|{c}|%" for c in cs]
    if shift in _SHIFT_SQL:
        sql.append(_SHIFT_SQL[shift])
    if gender in ("male", "female"):  # hide ads that are only for the other gender
        sql.append("AND (json_extract(data, '$.gender') IS NULL OR json_extract(data, '$.gender')=?)")
        args.append(gender)
    if kind:
        sql.append("AND kind=?")
        args.append(kind)
    if official is not None:
        sql.append("AND official=?")
        args.append(int(official))
    if since_days:
        sql.append("AND posted_at >= ?")
        args.append((datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat())
    fields_sel = [f.strip() for f in (field or "").split(",") if f.strip()]
    if fields_sel:
        sql.append(f"AND field IN ({','.join('?' * len(fields_sel))})")
        args.extend(fields_sel)
    subs_sel = [s.strip() for s in (sub or "").split(",") if s.strip()]
    if subs_sel:
        sql.append(f"AND subfield IN ({','.join('?' * len(subs_sel))})")
        args.extend(subs_sel)
    if len(cs) == 1:
        sql.append("ORDER BY CASE WHEN cities=? THEN 0 WHEN cities LIKE ? THEN 1 ELSE 2 END, posted_at DESC")
        args.extend([f"|{cs[0]}|", f"%|{cs[0]}|%"])
    elif len(cs) > 1:
        exact_ors = " OR ".join(["cities=?"] * len(cs))
        like_ors = " OR ".join(["cities LIKE ?"] * len(cs))
        sql.append(f"ORDER BY CASE WHEN ({exact_ors}) THEN 0 WHEN ({like_ors}) THEN 1 ELSE 2 END, posted_at DESC")
        args.extend([f"|{c}|" for c in cs] + [f"%|{c}|%" for c in cs])
    else:
        sql.append("ORDER BY posted_at DESC")
    rows = con.execute(" ".join(sql), args).fetchall()
    if q and q.strip():
        rows, _ = sq.search_and_rank(con, rows, q, sort=sort or "relevance")
    return rows


@app.get("/api/health")
def health():
    return {"ok": True, "updated_at": db.get_meta(con, "updated_at")}


@app.get("/api/meta")
def meta():
    today_s = _today()
    open_rows = con.execute(
        "SELECT education, sector FROM jobs WHERE status='live' AND country='sy' "
        "AND expires_on>=? AND (deadline IS NULL OR deadline='' OR deadline>=?) AND posted_at>=?",
        (today_s, today_s, _since())).fetchall()
    counts = Counter(r["education"] or "unknown" for r in open_rows)
    sy_sectors = Counter(r["sector"] for r in open_rows)
    return {
        "name": config.SITE_NAME, "name_en": config.SITE_NAME_EN,
        "education": [{"code": c, "label": l, "open": counts.get(c, 0), "open_sy": counts.get(c, 0)} for c, l in tx.EDUCATION],
        "fields": [{"code": c, "label": l} for c, l in tx.FIELDS],
        "subfields": [{"code": c, "label": l} for c, l in CATEGORIES.items()],
        "sectors": [{"code": c, "label": l, "open": sy_sectors.get(c, 0)} for c, l in tx.SECTORS],
        "apply_types": [{"code": c, "label": l} for c, l in tx.APPLY_TYPES],
        "cities": tx.CITIES,
        "countries": [
            {"code": "sy", "label": "سوريا", "open": len(open_rows)},
        ],
        "sources": [
            {"code": code, "label": label}
            for code, label in config.SOURCE_LABEL.items() if code != "employer"
        ],
        "updated_at": db.get_meta(con, "updated_at"),
        "update_minutes": config.UPDATE_MINUTES,
    }


@app.get("/api/suggest")
def suggest(q: str = "", limit: int = Query(8, le=15)):
    return {"suggestions": sq.suggest_queries(con, q, limit=limit)}


@app.get("/api/jobs")
def jobs(edu: str | None = None, lower: bool = False, field: str | None = None, sub: str | None = None,
         city: str | None = None, kind: str | None = None, q: str | None = None, official: bool | None = None,
         new: bool = False, expired: bool = False, gender: str | None = None, country: str | None = None,
         sector: str | None = None, apply: str | None = None, edu_group: str | None = None, source: str | None = None,
         exp: str | None = None, instant: bool = False, sort: str = "relevance", limit: int = Query(40, le=200), offset: int = 0,
         smart: bool = False, shift: str | None = None):
    """Job search. With smart=1 the query is read like speech («محاسب بدمشق دوام جزئي»): places, degree, shift,
    experience… become filters (returned as `understood` chips) and only the rest is searched as words. A query
    that finds nothing because of a typo is retried with the suggested spelling (`corrected`)."""
    understood: list[dict] = []
    if smart and q and q.strip():
        p = nls.parse(q)
        understood = [c.as_dict() for c in p.chips]
        q = p.text or None
        if c := p.first("country"):
            country = c
        if cities := p.get("city"):
            city = ",".join(cities)
        elif p.first("country"):
            city = None  # a country without a city: don't keep cities from the other country
        if e := p.first("edu"):
            edu, lower = e, True
        if v := p.first("sector"):
            sector = v
        if v := p.first("gender"):
            gender = v
        if v := p.first("shift"):
            shift = v
        if (v := p.first("exp")) is not None:
            exp = nls.exp_codes(v)
        if p.first("new"):
            new = True
    q_clean = (q or "").strip() or None
    sort_mode = "recent" if sort == "recent" else "relevance"
    base = _query(edu, lower, None, None, city, kind, q_clean, official, expired, since_days=1 if new else None,
                  gender=gender, country=country, sector=None, apply=apply, edu_group=edu_group, source=source,
                  exp=exp, instant=instant, sort=sort_mode, light=(q_clean is None), shift=shift)
    hl_terms: list[str] = []
    if q_clean:
        for g in sq.expand_query_groups(q_clean):
            for v in g["variants"][:6]:
                if len(v) >= 2 and v not in hl_terms:
                    hl_terms.append(v)
    fields_sel = {f.strip() for f in (field or "").split(",") if f.strip()}
    subs_sel = {s.strip() for s in (sub or "").split(",") if s.strip()}
    field_matched = [r for r in base if (not fields_sel or r["field"] in fields_sel) and (not subs_sel or r["subfield"] in subs_sel)]
    sectors = Counter(r["sector"] for r in field_matched)
    sectored = [r for r in base if not sector or r["sector"] == sector]
    rows = [r for r in sectored if (not fields_sel or r["field"] in fields_sel) and (not subs_sel or r["subfield"] in subs_sel)]
    suggestion = sq.fuzzy_suggestion(con, q_clean) if (q_clean and not rows) else None
    fields = Counter(r["field"] for r in sectored)
    subs = Counter(r["subfield"] for r in sectored if r["field"] == "computer" and r["subfield"])
    applies = Counter(a for r in rows for a in filter(None, (r["apply_types"] or "").split("|")))
    sources_cnt = Counter(r["source"] for r in rows)
    page_rows = rows[offset:offset + limit]
    if page_rows and "data" not in page_rows[0].keys():
        ids = [r["id"] for r in page_rows]
        full_map = {
            fr["id"]: fr
            for fr in con.execute(
                f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        }
        page_rows = [full_map[jid] for jid in ids if jid in full_map]
    if smart and suggestion and not rows:
        retry = jobs(edu=edu, lower=lower, field=field, sub=sub, city=city, kind=kind, q=suggestion, official=official,
                     new=new, expired=expired, gender=gender, country=country, sector=sector, apply=apply,
                     edu_group=edu_group, source=source, exp=exp, instant=instant, sort=sort, limit=limit,
                     offset=offset, smart=False, shift=shift)
        if retry["total"]:
            retry["understood"], retry["corrected"] = understood, suggestion
            return retry
    return {"total": len(rows), "jobs": [db.row_to_dict(con, r) for r in page_rows],
            "understood": understood, "corrected": None,
            "terms": hl_terms,
            "suggestion": suggestion,
            "facets": {"field": [{"code": f, "label": tx.FIELD_LABEL.get(f, f), "count": n} for f, n in fields.most_common()],
                       "sub": [{"code": f, "label": CATEGORIES.get(f, f), "count": n} for f, n in subs.most_common()],
                       "sector": [{"code": s, "label": tx.SECTOR_LABEL.get(s, s), "count": sectors.get(s, 0)} for s, _ in tx.SECTORS],
                       "apply": [{"code": a, "label": tx.APPLY_LABEL.get(a, a), "count": applies.get(a, 0)} for a, _ in tx.APPLY_TYPES if applies.get(a, 0)],
                       "sources": [{"code": s, "label": config.SOURCE_LABEL.get(s, s), "count": n} for s, n in sources_cnt.most_common()]}}


# Only ads that passed review (or came straight from a trusted source) are public. «review» covers both employer
# posts waiting for approval and ads hidden after reports; «rejected» never comes back.
PUBLIC_STATUSES = ("live", "expired")


def _is_public(row) -> bool:
    return bool(row) and row["status"] in PUBLIC_STATUSES


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not _is_public(row):
        raise HTTPException(404)
    return db.row_to_dict(con, row)


class _RateLimit:
    """At most `limit` hits per `window` seconds per key, in memory. Old keys are dropped so memory stays bounded."""

    def __init__(self, limit: int, window: float, max_keys: int = 50_000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self.hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self.hits.get(key, ()) if t > now - self.window]
            if len(recent) >= self.limit:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            if len(self.hits) > self.max_keys:
                cutoff = now - self.window
                self.hits = {k: v for k, v in self.hits.items() if v and v[-1] > cutoff}
                while len(self.hits) > self.max_keys:  # still too many: drop the oldest half
                    for k in list(self.hits)[: len(self.hits) // 2]:
                        del self.hits[k]
            return True


_report_limit = _RateLimit(20, 3600)
_reported: dict[tuple[str, int], float] = {}  # (ip, job) → when; one person counts once per ad for a day
_REPORTED_MAX = 200_000


def _is_trusted_proxy(host: str) -> bool:
    if host in ("testclient", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "?"
    if _is_trusted_proxy(host):
        if cf := request.headers.get("cf-connecting-ip"):
            return cf.strip()[:64]
        if xff := request.headers.get("x-forwarded-for"):
            return xff.split(",")[0].strip()[:64]
    return host


@app.post("/api/jobs/{job_id}/report")
def report(job_id: int, request: Request, body: dict = Body(default={})):
    """«بلّغ عن إعلان». Nothing about the reporter is stored; the address is only used in memory to slow down floods
    and so that one person counts once per ad (otherwise anyone could hide any ad by pressing three times)."""
    ip = _client_ip(request)
    if not con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        raise HTTPException(404)
    if not _report_limit.allow(ip):
        raise HTTPException(429, "كتير بلاغات، جرّب بعد شوي")
    now = time.time()
    if now - _reported.get((ip, job_id), 0) < 86400:
        return {"ok": True}
    if len(_reported) > _REPORTED_MAX:
        for k in [k for k, t in _reported.items() if now - t >= 86400] or list(_reported)[: _REPORTED_MAX // 2]:
            _reported.pop(k, None)
    _reported[(ip, job_id)] = now
    reason = str(body.get("reason", ""))[:40]
    con.execute("INSERT INTO reports VALUES (?,?,?)", (job_id, reason, db.now()))
    con.execute("UPDATE jobs SET reports=reports+1 WHERE id=?", (job_id,))
    con.execute("UPDATE jobs SET status='review' WHERE id=? AND reports>=? AND status='live'", (job_id, config.REPORTS_TO_HIDE))
    con.commit()
    _stats_cache.clear()
    return {"ok": True}


_stats_cache: dict[str, tuple[float, dict]] = {}
_stats_lock = threading.Lock()


def _country_summary(country_code: str) -> dict:
    """Compute live/6-month metrics, sector breakdown, and city counts for one market ('sy' | 'tr')."""
    open_rows = _query(country=country_code, light=True)
    all_rows = _query(expired=True, country=country_code, light=True)
    emp_count = con.execute(
        "SELECT COUNT(DISTINCT json_extract(data, '$.employer')) FROM jobs "
        "WHERE status IN ('live', 'expired') AND posted_at >= ? AND country=? "
        "AND json_extract(data, '$.employer') IS NOT NULL AND json_extract(data, '$.employer') != ''",
        (_since(), country_code),
    ).fetchone()[0]
    demand = Counter()
    city_counts = Counter()
    field_counts = Counter()
    no_degree = 0
    for r in open_rows:
        demand[r["field"]] += 1
        field_counts[r["field"]] += 1
        for c in filter(None, (r["cities"] or "").split("|")):
            city_counts[c] += 1
        if r["education"] in ("basic", "none") or r["education"] is None:
            no_degree += 1
    today_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {
        "open": len(open_rows),
        "six_months": len(all_rows),
        "junior": sum(1 for r in open_rows if r["experience"] is not None and r["experience"] <= 2),
        "employers": emp_count,
        "today": sum(1 for r in open_rows if (r["posted_at"] or "") >= today_iso),
        "official": sum(1 for r in open_rows if r["official"]),
        "gov_count": sum(1 for r in open_rows if r["sector"] == "gov"),
        "ngo_count": sum(1 for r in open_rows if r["sector"] == "ngo"),
        "private_count": sum(1 for r in open_rows if r["sector"] == "private"),
        "no_degree_count": no_degree,
        "industry_count": field_counts.get("industry", 0),
        "city_counts": dict(city_counts),
        "demand": [
            {"field": f, "label": tx.FIELD_LABEL.get(f, f), "count": n}
            for f, n in ([x for x in demand.most_common() if x[0] != "other"][:8] or demand.most_common(8))
        ],
    }


@app.get("/api/stats")
def stats(country: str | None = None):
    """Numbers for the opening screen (cached briefly: they change every 30 minutes at most)."""
    key = f"{country or 'sy'}:{_today()}"
    updated = db.get_meta(con, "updated_at")
    if (hit := _stats_cache.get(key)) and time.time() - hit[0] < 300 and hit[1].get("updated_at") == updated:
        return hit[1]
    with _stats_lock:
        updated = db.get_meta(con, "updated_at")
        if (hit := _stats_cache.get(key)) and time.time() - hit[0] < 300 and hit[1].get("updated_at") == updated:
            return hit[1]
        sy_sum = _country_summary("sy")
        tr_sum = _country_summary("tr")
        active = tr_sum if country == "tr" else sy_sum
        out = {
            **active,
            "open_sy": sy_sum["open"],
            "open_tr": tr_sum["open"],
            "city_counts_tr": tr_sum["city_counts"],
            "by_country": {"sy": sy_sum, "tr": tr_sum},
            "trust": db.trust_metrics(con),
            "updated_at": updated,
        }
        _stats_cache[key] = (time.time(), out)
        return out


# ------------------------------------------------------------------ Phase 3: employers, review queue, market pulse
_post_limit = _RateLimit(10, 86400)
_scam_limit = _RateLimit(30, 600)


@app.post("/api/employer/jobs")
def employer_post(request: Request, body: dict = Body(...)):
    """An employer posts a job directly. It waits in the review queue until an admin approves it."""
    from .models import Job

    if body.get("website"):  # honeypot field, hidden from people
        return {"ok": True}
    if not _post_limit.allow(_client_ip(request)):
        raise HTTPException(429, "وصلت للحد اليومي")
    clip = lambda k, n=200: (str(body.get(k) or "").strip()[:n] or None)
    country = "tr" if clip("country", 4) == "tr" else "sy"
    title, employer, phone = clip("title", 120), clip("employer", 120), clip("phone", 24)
    apply_url = da.strip_tracking(clip("apply_url", 300) or "") or None
    email = clip("email", 120)
    parsed_phones = tx.phones_tr(phone or "") if country == "tr" else tx.phones(phone or "")
    if not title or not employer or not (phone or apply_url or email):
        raise HTTPException(400, "لازم المسمى واسم الجهة وطريقة تقديم (رقم تلفون أو رابط أو إيميل)")
    if apply_url and (da.is_disallowed_offline_url(apply_url) or da.is_strict_portal_wrapper(apply_url) or da.is_shortener(apply_url)):
        raise HTTPException(400, "رابط التقديم لازم يكون فورم أو موقع توظيف مباشر، مو صفحة تواصل اجتماعي أو منصة تجميع")
    if email and "@" not in email:
        raise HTTPException(400, "صيغة الإيميل غير صحيحة")
    if not (parsed_phones or apply_url or email):
        msg = "رقم التلفون لازم يكون رقم تركي صحيح (يبدأ بـ 05)" if country == "tr" else "رقم التلفون لازم يكون رقم سوري صحيح (يبدأ بـ 09)"
        raise HTTPException(400, msg)
    deadline = clip("deadline", 10)
    if deadline:
        try:
            date.fromisoformat(deadline)
        except ValueError:
            raise HTTPException(400, "تاريخ الانتهاء لازم يكون YYYY-MM-DD")
    city = clip("city", 40)
    valid_cities = tx.TR_CITIES if country == "tr" else tx.CITIES
    edu = body.get("education") if body.get("education") in tx.EDU_RANK else None
    text = clip("details", 3000) or ""
    company_url = clip("company_url", 250)
    contact_note = clip("contact_note", 300)
    note_parts = [p for p in (company_url, contact_note) if p]
    sec_hint = clip("sector", 16)
    if country == "tr" and sec_hint == "gov":
        sec_hint = "private"
    job = Job(title=title, source="employer", post_url=config.PUBLIC_URL, posted_at=db.now(), employer=employer,
              cities=[city] if city in valid_cities else [], education=edu, education_from="stated" if edu else "unknown",
              kind="quick" if body.get("kind") == "quick" else "formal", deadline=deadline,
              salary=clip("salary", 60), shift=clip("shift", 60), apply_url=apply_url,
              phones=parsed_phones, whatsapp=bool(body.get("whatsapp")), email=email,
              experience=int(body["experience"]) if str(body.get("experience") or "").isdigit() else None,
              gender=body.get("gender") if body.get("gender") in ("male", "female") else None,
              country=country,
              tasks=[l.strip("-• ") for l in text.splitlines() if l.strip()][:8], raw=text)
    job.field = tx.field_from_text(title, text)
    job.sector = tx.infer_sector(False, job.field, job.employer, text, hint=sec_hint, country=country)
    da.resolve_job_apply(job, client=None)
    jid, _ = db.upsert(con, job, status="review")
    con.execute("UPDATE job_sources SET post_url=? WHERE job_id=? AND source='employer'", (f"{config.PUBLIC_URL}/#job-{jid}", jid))
    manage_token = secrets.token_urlsafe(18)
    con.execute(
        "INSERT INTO employer_tokens (token, job_id, verified, contact_note, created_at) VALUES (?,?,?,?,?)",
        (manage_token, jid, 0, " | ".join(note_parts) if note_parts else None, db.now()),
    )
    con.commit()
    return {"ok": True, "id": jid, "manage_token": manage_token, "status": "review"}


@app.get("/api/employer/manage/{token}")
def employer_manage_status(token: str):
    """Self-service status check for an employer's submitted job using their private management code."""
    tok = (token or "").strip()
    if len(tok) < 12:
        raise HTTPException(404, "رمز المتابعة غير صحيح")
    row = con.execute(
        "SELECT t.job_id, t.verified, t.created_at, t.closed_at, j.title, j.country, j.sector, j.cities, j.deadline, j.posted_at, j.status, j.data "
        "FROM employer_tokens t JOIN jobs j ON j.id = t.job_id WHERE t.token=?",
        (tok,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "لم يتم العثور على شاغر بهذا الرمز")
    d = json.loads(row["data"])
    return {
        "ok": True,
        "id": row["job_id"],
        "title": row["title"],
        "employer": d.get("employer") or "",
        "country": row["country"],
        "sector": row["sector"],
        "cities": [c for c in (row["cities"] or "").split("|") if c],
        "deadline": row["deadline"],
        "posted_at": row["posted_at"],
        "status": row["status"],
        "verified": bool(row["verified"] or row["status"] == "live"),
        "closed_at": row["closed_at"],
        "public_url": f"/j/{row['job_id']}" if row["status"] == "live" else None,
    }


@app.post("/api/employer/manage/{token}/close")
def employer_manage_close(token: str):
    """Allow the employer who posted a job to close/mark it filled immediately."""
    tok = (token or "").strip()
    if len(tok) < 12:
        raise HTTPException(404, "رمز المتابعة غير صحيح")
    row = con.execute(
        "SELECT t.job_id, j.status FROM employer_tokens t JOIN jobs j ON j.id = t.job_id WHERE t.token=?",
        (tok,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "لم يتم العثور على شاغر بهذا الرمز")
    closed_at = db.now()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    con.execute("UPDATE jobs SET status='expired', expires_on=?, updated_at=? WHERE id=?", (yesterday, closed_at, row["job_id"]))
    con.execute("UPDATE employer_tokens SET closed_at=? WHERE token=?", (closed_at, tok))
    db.sync_fts(con, row["job_id"])
    con.commit()
    _stats_cache.clear()
    return {"ok": True, "id": row["job_id"], "status": "expired", "closed_at": closed_at}


def _admin(token: str | None):
    if not config.ADMIN_TOKEN or not hmac.compare_digest(token or "", config.ADMIN_TOKEN):
        raise HTTPException(403, "admin token")


@app.get("/api/admin/queue")
def admin_queue(x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    rows = con.execute("SELECT * FROM jobs WHERE status='review' ORDER BY first_seen DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(con, r)
        d["reports"] = r["reports"]
        d["raw"] = json.loads(r["data"]).get("raw", "")[:1500]
        out.append(d)
    return {"jobs": out}


@app.post("/api/admin/jobs/{job_id}")
def admin_decide(job_id: int, body: dict = Body(...), x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    status = {"approve": "live", "reject": "rejected"}.get(body.get("action"))
    if not status:
        raise HTTPException(400)
    con.execute("UPDATE jobs SET status=?, reports=CASE WHEN ?='live' THEN 0 ELSE reports END, first_seen=CASE WHEN source='employer' AND ?='live' THEN ? ELSE first_seen END WHERE id=?",
                (status, status, status, db.now(), job_id))
    if status == "live":
        con.execute("UPDATE employer_tokens SET verified=1 WHERE job_id=?", (job_id,))
    db.sync_fts(con, job_id)
    con.commit()
    _stats_cache.clear()
    return {"ok": True}


# ------------------------------------------------------------------ anonymous usage counters
_EVENT_PAGES = {"landing", "board", "job", "cv", "pulse", "scam", "employer", "sources"}
_EVENT_APPLY = {"whatsapp", "call", "site", "form", "email", "portal"}
_event_limit = _RateLimit(300, 600)
_BOT_UA = ("bot", "crawl", "spider", "slurp", "preview", "facebookexternalhit", "whatsapp", "telegram")


def _event_key_ok(event: str, key: str) -> bool:
    if event == "view":
        return key in _EVENT_PAGES
    if event == "onboard":
        return key in tx.EDU_RANK or key == ""
    if event == "apply":
        return key in _EVENT_APPLY
    if event in ("source", "apply_src"):
        return key in config.SOURCE_LABEL
    if event == "install":
        return key in ("accepted", "dismissed")
    return event in ("search", "share") and key == ""


def _is_bot(request: Request) -> bool:
    ua = (request.headers.get("user-agent") or "").lower()
    return not ua or any(b in ua for b in _BOT_UA)


@app.post("/api/e")
def track_event(request: Request, body: dict = Body(default={})):
    """Count one anonymous event (page view, «قدّم» click…). No cookie, no IP stored, no per-person data."""
    event, key = str(body.get("e") or "")[:20], str(body.get("k") or "")[:40]
    if _is_bot(request) or not _event_key_ok(event, key) or not _event_limit.allow(_client_ip(request)):
        return {"ok": True}  # silently ignored: counters are best-effort
    db.count_event(con, event, key)
    if event == "apply" and (src := str(body.get("s") or "")[:40]) in config.SOURCE_LABEL:
        db.count_event(con, "apply_src", src)
    con.commit()
    return {"ok": True}


@app.get("/api/admin/stats")
def admin_stats(days: int = Query(30, ge=1, le=365), x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    out = db.event_stats(con, days)
    out["source_labels"] = {k: config.SOURCE_LABEL.get(k, k) for k in
                            set(out["totals"].get("source", {})) | set(out["totals"].get("apply_src", {}))}
    return out


@app.get("/api/sources")
def sources():
    """Public credit list: every channel and site we read, with how many open jobs came from it."""
    key = f"sources:{_today()}"
    updated = db.get_meta(con, "updated_at")
    if (hit := _stats_cache.get(key)) and time.time() - hit[0] < 600 and hit[1].get("updated_at") == updated:
        return hit[1]
    rows = [{k: r[k] for k in ("source", "label", "kind", "url", "open", "recent")} for r in db.source_overview(con)]
    out = {"sources": rows, "updated_at": updated}
    _stats_cache[key] = (time.time(), out)
    return out


@app.get("/api/admin/partners")
def admin_partners(days: int = Query(30, ge=1, le=365), x_admin_token: str | None = Header(None)):
    """Per source: jobs we showed, visits and «قدّم» clicks we sent, for talking to channel admins."""
    _admin(x_admin_token)
    ev = db.event_stats(con, days)["totals"]
    rows = db.source_overview(con, days)
    for r in rows:
        r["apply_clicks"] = ev.get("apply_src", {}).get(r["source"], 0)
        r["visits_sent"] = ev.get("source", {}).get(r["source"], 0)
    return {"days": days, "site_name": config.SITE_NAME, "public_url": config.PUBLIC_URL, "sources": rows}


@app.get("/api/admin/backup")
def admin_backup(x_admin_token: str | None = Header(None)):
    """Download everything people put into the site (employer posts, reports, bot users, counters) as one file.
    Keep it private: it holds employers' phone numbers and bot chat ids."""
    import gzip
    from fastapi.responses import Response
    from . import backup

    _admin(x_admin_token)
    body = gzip.compress(json.dumps(backup.export_data(con), ensure_ascii=False).encode("utf-8"))
    name = f"laqeet-backup-{_today()}.json.gz"
    return Response(body, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.get("/api/admin/health")
def admin_health(x_admin_token: str | None = Header(None)):
    """Which sources stopped bringing jobs, so a broken scraper is noticed in days, not months."""
    _admin(x_admin_token)
    rows = db.health_report(con)
    return {"summary": dict(Counter(r["status"] for r in rows)), "sources": rows,
            "updated_at": db.get_meta(con, "updated_at")}


@app.get("/api/pulse")
def pulse(city: str | None = None, edu: str | None = None, country: str | None = None):
    """«نبض السوق»: what is asked for most, by month, field, city and education, over the last 6 months."""
    c_code = "tr" if country == "tr" else "sy"
    rows = _query(expired=True, country=c_code, light=True)
    if city:
        rows = [r for r in rows if f"|{city}|" in r["cities"]]
    if edu:
        rows = [r for r in rows if r["education"] == edu]
    months = sorted({r["posted_at"][:7] for r in rows if r["posted_at"]})
    by_month = {m: Counter() for m in months}
    titles, cities, edus, exp = Counter(), Counter(), Counter(), Counter()
    for r in rows:
        if r["posted_at"]:
            by_month[r["posted_at"][:7]][r["field"]] += 1
        titles[soft_fold(r["title"]).split("(")[0].strip()] += 1
        valid_cities = tx.TR_CITIES + ["عدة ولايات في تركيا"] if c_code == "tr" else tx.CITIES + ["عدة محافظات"]
        for c in filter(None, r["cities"].split("|")):
            if c in valid_cities:
                cities[c] += 1
        edus[r["education"] or "unknown"] += 1
        exp["unknown" if r["experience"] is None else "0-2" if r["experience"] <= 2 else "3-4" if r["experience"] < 5 else "5+"] += 1
    field_cnt = Counter(r["field"] for r in rows)
    top_fields = [f for f, _ in field_cnt.most_common() if f != "other"][:8] or [f for f, _ in field_cnt.most_common(8)]
    trend, pace_day = {}, None
    if len(months) >= 2:
        prev_m, cur_m = months[-2], months[-1]
        cur_days = [int(r["posted_at"][8:10]) for r in rows if (r["posted_at"] or "").startswith(cur_m)]
        max_day = max(cur_days, default=31)
        if max_day < 28:
            # Compare same day-of-month window so a partial month doesn't show every field dropping.
            pace_day = max_day
            a = Counter(r["field"] for r in rows if (r["posted_at"] or "").startswith(prev_m) and int(r["posted_at"][8:10]) <= max_day)
            b = by_month[cur_m]
        else:
            a, b = by_month[prev_m], by_month[cur_m]
        trend = {f: b[f] - a[f] for f in top_fields}
    today = _today()
    open_now = [r for r in rows if r["status"] == "live" and (r["expires_on"] or "") >= today and (not r["deadline"] or r["deadline"] >= today)]
    open_ids = [r["id"] for r in open_now]
    remote_open = 0
    if open_ids:
        # Count remote live jobs without materializing the full JSON blob for all 6-month rows.
        for i in range(0, len(open_ids), 800):
            chunk = open_ids[i:i + 800]
            remote_open += con.execute(
                f"SELECT COUNT(*) FROM jobs WHERE id IN ({','.join('?' * len(chunk))}) AND data LIKE '%عن بعد%'",
                chunk,
            ).fetchone()[0]
    names = {soft_fold(r["title"]).split("(")[0].strip(): r["title"].split("(")[0].strip() for r in rows}
    return {
        "total": len(rows), "open_now": len(open_now), "remote_open": remote_open,
        "months": months, "pace_day": pace_day,
        "fields": [{"code": f, "label": tx.FIELD_LABEL.get(f, f), "series": [by_month[m][f] for m in months], "change": trend.get(f)}
                   for f in top_fields],
        "titles": [{"title": names[t], "count": n} for t, n in titles.most_common(15)],
        "cities": [{"city": c, "count": n} for c, n in cities.most_common()],
        "education": [{"code": e, "label": tx.EDU_LABEL.get(e, "مو مذكورة"), "count": n} for e, n in edus.most_common()],
        "experience": dict(exp),
    }


@app.post("/api/scam-check")
def scam_check(request: Request, body: dict = Body(...)):
    """Paste any job ad from WhatsApp/Facebook/Telegram and check for scam signals and repeated phone numbers."""
    if not _scam_limit.allow(_client_ip(request)):
        raise HTTPException(429, "كتير طلبات، جرّب بعد شوي")
    text = str(body.get("text") or "").strip()[:6000]
    if len(text) < 10:
        raise HTTPException(400, "الصق نص الإعلان كامل لنفحصه")
    res = spam.analyze_ad(text)
    phones = tx.phones(text)
    phone_hits = []
    for p in phones[:5]:
        rows = con.execute("SELECT cities FROM jobs WHERE data LIKE ?", (f'%"{p}"%',)).fetchall()
        cities = {c for r in rows for c in filter(None, r["cities"].split("|"))}
        if len(rows) >= 12 and len(cities) >= 4:
            res["score"] += 2
            res["reasons"].append(f"الرقم {p} تكرر في {len(rows)} إعلان عبر {len(cities)} مدن مختلفة (احتمال سمسار أو إعلان وهمي)")
        phone_hits.append({"phone": p, "ads": len(rows), "cities": len(cities)})
    res["verdict"] = "danger" if res["score"] >= spam.THRESHOLD else ("caution" if res["score"] > 0 else "safe")
    res["phones"] = phone_hits
    return res


@app.get("/sitemap.xml")
def sitemap(request: Request):
    from fastapi.responses import Response
    xml = seo.sitemap_xml(con, seo.public_base(str(request.base_url)))
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links for the Play Store app (TWA). 404 until TWA_PACKAGE and TWA_SHA256 are set."""
    if not (config.TWA_PACKAGE and config.TWA_SHA256):
        raise HTTPException(404)
    return [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": config.TWA_PACKAGE,
                        "sha256_cert_fingerprints": config.TWA_SHA256}}]


@app.get("/robots.txt")
def robots(request: Request):
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(seo.robots_txt(seo.public_base(str(request.base_url))))


@app.get("/j/{job_id}", response_class=HTMLResponse)
def job_page(job_id: int, request: Request):
    """Shareable single-job page with Open Graph tags so WhatsApp/Telegram previews show the real title and city."""
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not _is_public(row):
        raise HTTPException(404)
    j = db.row_to_dict(con, row)
    if not _is_bot(request):
        db.count_event(con, "view", "job")
        con.commit()
    e = html.escape
    cities = "، ".join(j.get("cities") or []) or ("تركيا (عام)" if j.get("country") == "tr" else "سوريا (عام)")
    edu = tx.EDU_LABEL.get(j.get("education") or "", "غير مشترط / غير مذكور")
    emp_raw = (j.get("employer") or "").strip()
    emp = emp_raw if emp_raw not in db._PLACEHOLDER_EMP else ("جهة حكومية رسمية" if j.get("official") else "")
    posted_day = str(j.get("posted_at") or "")[:10]
    exp_day = str(j.get("deadline") or j.get("expires_on") or "")[:10]
    desc_parts = [emp or None, cities, f"النشر: {posted_day}" if posted_day else None,
                  f"الانتهاء: {exp_day}" if exp_day else None, f"المؤهل: {edu}",
                  f"الراتب: {j['salary']}" if j.get("salary") else None]
    desc = " · ".join(p for p in desc_parts if p)
    og_desc = (f"{desc} — {j['summary']}" if j.get("summary") else desc)[:260]
    share_url = f"{seo.public_base(str(request.base_url))}/j/{job_id}"
    job_ld = seo.ld_script(seo.job_posting(j, share_url, emp, j.get("cities") or [], edu))
    robots_meta = "" if seo.is_open(j) else '<meta name="robots" content="noindex">'

    ad_lines = [
        f"📢 فرصة عمل: {j['title']}",
    ]
    if emp:
        ad_lines.append(f"🏢 الجهة: {emp}")
    ad_lines.extend([
        f"📍 المدينة: {cities}",
        f"🎓 المؤهل العلمي: {edu}",
    ])
    if j.get("specialty"):
        ad_lines.append(f"🎯 التخصص: {j['specialty']}")
    if j.get("salary"):
        ad_lines.append(f"💰 الراتب / الأجر: {j['salary']}")
    if j.get("shift"):
        ad_lines.append(f"🕒 الدوام: {j['shift']}")
    if posted_day:
        ad_lines.append(f"🗓️ تاريخ النشر: {posted_day}")
    if exp_day:
        ad_lines.append(f"📅 تاريخ الانتهاء: {exp_day}")
    if j.get("summary"):
        ad_lines.append(f"\n📝 وصف وتفاصيل العمل:\n{j['summary']}")
    if j.get("tasks"):
        ad_lines.append("\n📌 المهام والمسؤوليات:\n" + "\n".join(f"• {t}" for t in (j.get("tasks") or [])[:5]))
    if j.get("skills"):
        ad_lines.append("\n✅ الشروط والمهارات:\n" + "\n".join(f"• {s}" for s in (j.get("skills") or [])[:5]))

    has_direct_link = bool(j.get("apply_url") and not j["apply_url"].lower().startswith("https://wa.me/"))
    apply_lines = []
    if j.get("email"):
        apply_lines.append(f"✉️ للتقديم (إرسال السيرة الذاتية CV عبر البريد الإلكتروني): {j['email']}")
    if has_direct_link:
        apply_lines.append(f"🔗 رابط التقديم المباشر: {j['apply_url']}")
    if j.get("phones"):
        apply_lines.append(f"📞 للتواصل والتقديم (هاتف / واتساب): {j['phones'][0]}")
    if apply_lines:
        ad_lines.append("\n" + "\n".join(apply_lines))

    share_text = "\n".join(ad_lines)
    primary_link = j["apply_url"] if has_direct_link else share_url
    wa_share = "https://wa.me/?text=" + quote(share_text)
    tg_share = f"https://t.me/share/url?url={quote(primary_link)}&text={quote(share_text)}"

    org_ar = f"لدى {emp}" if emp else "لديكم"
    wa_pitch_raw = (
        f"السلام عليكم ورحمة الله وبركاته،\n"
        f"أتواصل معكم للتقديم على شاغر «{j.get('title', '')}» {org_ar} ({cities}) المنشور عبر منصة {config.SITE_NAME}.\n"
        f"أرفق لكم السيرة الذاتية للتفضل بالاطلاع، ويسعدني تواصلكم لتحديد موعد مقابلة. شكراً لكم."
    )
    wa_text = quote(wa_pitch_raw)
    email_subj_ar = f"التقدم لشاغر: {j.get('title', '')} — {cities}"
    email_body_ar = (
        f"السادة في قسم الموارد البشرية {org_ar} المحترمين،\n"
        f"تحية طيبة وبعد،\n\n"
        f"أود التقدم بطلبي لشغل شاغر «{j.get('title', '')}» في {cities}.\n"
        f"أرفق لكم سيرتي الذاتية المحدثة للاطلاع على مؤهلاتي وخبراتي العملية، وأتطلع لفرصة إجراء مقابلة عمل.\n\n"
        f"مع خالص التقدير والاحترام،"
    )
    mailto_prefilled = (
        f"mailto:{quote(j['email'])}?subject={quote(email_subj_ar)}&body={quote(email_body_ar)}"
        if j.get("email") else ""
    )
    btns = []
    for p in (j.get("phones") or [])[:1]:
        if p.startswith("09"):
            btns.append(f'<a class="wa" target="_blank" rel="noopener" href="https://wa.me/963{p[1:]}?text={wa_text}">راسل واتساب ({e(p)})</a>')
        elif j.get("country") == "tr" and p.startswith("05"):
            btns.append(f'<a class="wa" target="_blank" rel="noopener" href="https://wa.me/90{p[1:]}?text={wa_text}">راسل واتساب ({e(p)})</a>')
        btns.append(f'<a class="call" href="tel:{e(p)}">اتصل {e(p)}</a>')
    if j.get("email"):
        btns.append(f'<a class="btn primary" href="{e(mailto_prefilled)}">إرسال CV بالإيميل ({e(j["email"])})</a>')
    if has_direct_link:
        btns.append(f'<a class="btn primary" target="_blank" rel="noopener" href="{e(j["apply_url"])}">قدّم عالوظيفة</a>')
    btns.append(f'<a class="btn ghost" href="/cv.html?job={job_id}"><i data-ico="file-text" data-ico-cls="ico-sm"></i><span>جهّز سيرة ذاتية إلها</span></a>')
    summary_html = f'<h3>نبذة ووصف الشاغر</h3><p style="line-height:1.7">{e(j["summary"])}</p>' if j.get("summary") else ""
    tasks_html = ("<h3>المهام</h3><ul>" + "".join(f"<li>{e(t)}</li>" for t in j.get("tasks") or []) + "</ul>") if j.get("tasks") else ""
    skills_html = ("<h3>المهارات المطلوبة</h3><ul>" + "".join(f"<li>{e(s)}</li>" for s in j.get("skills") or []) + "</ul>") if j.get("skills") else ""
    official_srcs = [s for s in (j.get("sources") or []) if s.get("official_public")]
    srcs_html = " · ".join(f'<a target="_blank" rel="noopener" href="{e(s["post_url"])}">{e(s["label"])}</a>' for s in official_srcs)
    src_note_html = f'<p class="note" style="color:var(--muted)">المصدر: {srcs_html}</p>' if srcs_html else ""
    trust_map = {
        "gov": '<span class="trust-pill gov"><i data-ico="shield-check" data-ico-cls="ico-sm"></i><span>جهة حكومية رسمية موثّقة</span></span>',
        "ngo": '<span class="trust-pill ngo"><i data-ico="shield-check" data-ico-cls="ico-sm"></i><span>منظمة دولية / إنسانية موثّقة</span></span>',
        "verified_employer": '<span class="trust-pill verified"><i data-ico="badge-check" data-ico-cls="ico-sm"></i><span>صاحب عمل موثّق على لقيت</span></span>',
    }
    trust_badge_html = trust_map.get(j.get("trust_tier") or "", "")
    pitch_default = email_body_ar if j.get("email") else wa_pitch_raw
    return HTMLResponse(f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(j['title'])} · {e(config.SITE_NAME)}</title>
<meta name="description" content="{e(og_desc)}">
<meta name="theme-color" content="#0B1C16">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(j['title'])} — {e(cities)}">
<meta property="og:description" content="{e(og_desc)}">
<meta property="og:url" content="{e(share_url)}">
<link rel="canonical" href="{e(share_url)}">
{robots_meta}
<meta name="twitter:card" content="summary">
{job_ld}
<link rel="icon" href="/icons/icon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alexandria:wght@700;800&family=IBM+Plex+Sans+Arabic:wght@400;700&display=swap">
<link rel="stylesheet" href="/app.css?v=43"><link rel="stylesheet" href="/page.css?v=43">
<script src="/ui.js?v=43"></script>
</head><body>
<nav class="site-nav" aria-label="التنقل الرئيسي">
  <div class="inner site-nav-inner">
    <a class="site-brand" href="/">
      <img class="site-brand-mark" src="/icons/icon.svg" alt="" width="36" height="36">
      <span class="site-brand-text">
        <span class="site-brand-name">{e(config.SITE_NAME)}</span>
        <span class="site-brand-sub">سوريا وتركيا</span>
      </span>
    </a>
    <div class="site-nav-links">
      <a class="site-nav-link is-active" href="/?view=jobs"><i data-ico="briefcase" data-ico-cls="ico-sm"></i><span>الوظائف</span></a>
      <a class="site-nav-link" href="/cv.html"><i data-ico="file-text" data-ico-cls="ico-sm"></i><span>سيرتي الذاتية</span></a>
      <a class="site-nav-link" href="/pulse.html"><i data-ico="bar-chart" data-ico-cls="ico-sm"></i><span>نبض السوق</span></a>
      <a class="site-nav-link" href="/scam.html"><i data-ico="shield-check" data-ico-cls="ico-sm"></i><span>رادار النصب</span></a>
      <a class="site-nav-link" href="/employer.html"><i data-ico="megaphone" data-ico-cls="ico-sm"></i><span>انشر وظيفة</span></a>
    </div>
    <div class="site-nav-actions">
      <button type="button" class="nav-icon-btn" data-theme-toggle aria-label="تبديل وضع الألوان">
        <span class="theme-ico"></span><span class="theme-lbl">الليلي</span>
      </button>
      <button type="button" class="nav-icon-btn nav-menu-btn" data-nav-menu-toggle aria-label="القائمة" aria-expanded="false">
        <i data-ico="menu"></i>
      </button>
    </div>
  </div>
</nav>
<section class="hero slim"><div class="inner">
  <div class="eyebrow"><a class="link" href="/?view=jobs"><i data-ico="arrow-left" data-ico-cls="ico-sm"></i><span>كل الوظائف على {e(config.SITE_NAME)}</span></a>{trust_badge_html}</div>
  <h1>{e(j['title'])}</h1>
  <p class="sub">{e(desc)}</p>
</div></section>
<main class="inner page">
  <div class="box">
    <div class="badges">
      <span class="b">الشهادة: {e(edu)}</span>
      <span class="b">المدينة: {e(cities)}</span>
      {f'<span class="b">الجهة: {e(emp)}</span>' if emp else ''}
      {f'<span class="b">الراتب: {e(j["salary"])}</span>' if j.get("salary") else ''}
      {f'<span class="b">الدوام: {e(j["shift"])}</span>' if j.get("shift") else ''}
      {''.join(f'<span class="b">{e(t)}</span>' for t in (j.get("tags") or []) if t not in db._PLACEHOLDER_EMP)}
    </div>
    {summary_html}{tasks_html}{skills_html}
    <div class="row" style="margin-top:8px">{''.join(btns)}</div>
    <div class="tpl-box" style="margin-top:16px">
      <div class="tpl-head">
        <span class="tpl-title"><i data-ico="message-square" data-ico-cls="ico-sm"></i><span>مُولّد رسالة التقديم الجاهزة لهذا الشاغر:</span></span>
      </div>
      <div class="tpl-subject-row">
        <span class="tpl-subject-lbl">عنوان الإيميل (Subject):</span>
        <input id="pitchSubjectText" type="text" class="tpl-subject-input" dir="rtl" aria-label="عنوان الإيميل" value="{e(email_subj_ar)}">
        <button type="button" class="btn ghost tpl-copy-subj-btn" onclick="navigator.clipboard.writeText(document.getElementById('pitchSubjectText').value).then(()=>this.textContent='تم نسخ العنوان')">نسخ العنوان</button>
      </div>
      <textarea id="pitchBodyText" class="tpl-textarea" dir="rtl" aria-label="نص رسالة التقديم">{e(pitch_default)}</textarea>
      <div class="tpl-actions">
        <button type="button" class="btn primary" onclick="navigator.clipboard.writeText(document.getElementById('pitchBodyText').value).then(()=>this.textContent='تم نسخ نص الرسالة')"><i data-ico="copy" data-ico-cls="ico-sm"></i><span>نسخ نص رسالة التقديم</span></button>
      </div>
    </div>
    <div class="share-box" style="margin-top:16px">
      <div class="share-box-title"><i data-ico="share" data-ico-cls="ico-sm"></i><span>مشاركة ونسخ الإعلان مع الوصف وطريقة التقديم:</span></div>
      <textarea id="shareAdText" class="share-preview" dir="rtl" aria-label="نص الإعلان للمشاركة">{e(share_text)}</textarea>
      <div class="share-actions">
        <button type="button" class="btn primary" onclick="navigator.clipboard.writeText(document.getElementById('shareAdText').value).then(()=>this.textContent='تم نسخ الإعلان مع الوصف')"><i data-ico="copy" data-ico-cls="ico-sm"></i><span>نسخ الإعلان مع الوصف</span></button>
        <button type="button" class="btn ghost" data-copy="{e(primary_link)}" onclick="navigator.clipboard.writeText(this.dataset.copy).then(()=>this.textContent='تم نسخ الرابط')"><i data-ico="link" data-ico-cls="ico-sm"></i><span>نسخ الرابط فقط</span></button>
        <a class="btn ghost" target="_blank" rel="noopener" href="{wa_share}"><i data-ico="whatsapp" data-ico-cls="ico-sm"></i><span>مشاركة عبر واتساب</span></a>
        <a class="btn ghost" target="_blank" rel="noopener" href="{tg_share}"><i data-ico="send" data-ico-cls="ico-sm"></i><span>مشاركة عبر تيليجرام</span></a>
      </div>
    </div>
    {src_note_html}
  </div>
</main></body></html>""")


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


@app.middleware("http")
async def _cache_headers(request, call_next):
    """Ensure shell assets always revalidate and API responses are never HTTP-cached."""
    resp = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    p = request.url.path
    if p.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif p == "/" or p.endswith((".html", ".js", ".css", ".webmanifest")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# ------------------------------------------------------------------ mobile app API (/api/v1)
from . import api_v1  # noqa: E402

app.include_router(api_v1.router)
app.add_exception_handler(_accounts.AccountError, api_v1.account_error_handler)


@app.get("/api/admin/users")
def admin_users(x_admin_token: str | None = Header(None)):
    """App accounts in numbers only (no names, no emails): how many, verified, by education, city and gender."""
    _admin(x_admin_token)
    return _accounts.aggregate_stats(con)


if config.APP_WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=config.APP_WEB_DIR, html=True), name="app_web")
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="site")
