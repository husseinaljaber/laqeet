"""HTTP API and the website.  uvicorn app.api:app --port 8000"""
from __future__ import annotations

import hmac
import html
import ipaddress
import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, direct_apply as da, search as sq, spam
from . import taxonomy as tx
from .arabic import soft_fold
from .classify import CATEGORIES

log = logging.getLogger("api")
con = db.connect()


def _auto_collect_loop(stop: threading.Event) -> None:
    """Keep the database fresh even when `uvicorn app.api:app` is run on its own without `app.run`."""
    from .collect import collect

    gap = max(60, config.UPDATE_MINUTES * 60)
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
                res = collect()
                _stats_cache.clear()
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


def _query(edu=None, lower=False, field=None, sub=None, city=None, kind=None, q=None, official=None,
           expired=False, status="live", since_days=None, gender=None, country=None, sector=None, apply=None,
           edu_group=None, source=None, sort: str = "relevance"):
    if expired and status == "live":
        sql = ["SELECT * FROM jobs WHERE status IN ('live', 'expired') AND posted_at >= ?"]
        args: list = [_since()]
    else:
        sql = ["SELECT * FROM jobs WHERE status=? AND posted_at >= ?"]
        args: list = [status, _since()]
    if not expired:
        sql.append("AND expires_on >= ?")
        args.append(_today())
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
    if (levels := _levels(edu, lower or bool(q))) is not None:
        if edu_group == "degree":
            levels = [c for c in levels if c in ("phd", "master", "bachelor", "institute", "secondary")]
        elif edu_group == "nodegree":
            levels = [c for c in levels if c in ("basic", "none")]
        if levels:
            # jobs that don't say the education are shown with «ما ذكر الشهادة» when asking for more or searching
            sql.append(f"AND (education IN ({','.join('?' * len(levels))})" + (" OR education IS NULL)" if (lower or q or edu_group == "nodegree") else ")"))
            args += levels
    elif edu_group == "degree":
        sql.append("AND (education IN ('phd', 'master', 'bachelor', 'institute', 'secondary')" + (" OR education IS NULL)" if (lower or q) else ")"))
    elif edu_group == "nodegree":
        sql.append("AND (education IN ('basic', 'none') OR education IS NULL)")
    cs = [c.strip() for c in (city or "").split(",") if c.strip()]
    if cs:
        sql.append("AND (cities LIKE '%|عدة محافظات|%' OR " + " OR ".join(["cities LIKE ?"] * len(cs)) + ")")
        args += [f"%|{c}|%" for c in cs]
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


@app.get("/api/meta")
def meta():
    open_rows = con.execute(
        "SELECT education, sector FROM jobs WHERE status='live' AND country='sy' AND expires_on>=? AND posted_at>=?",
        (_today(), _since())).fetchall()
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
         sort: str = "relevance", limit: int = Query(40, le=200), offset: int = 0):
    q_clean = (q or "").strip() or None
    sort_mode = "recent" if sort == "recent" else "relevance"
    base = _query(edu, lower, None, None, city, kind, q_clean, official, expired, since_days=1 if new else None,
                  gender=gender, country=country, sector=None, apply=apply, edu_group=edu_group, source=source, sort=sort_mode)
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
    return {"total": len(rows), "jobs": [db.row_to_dict(con, r) for r in rows[offset:offset + limit]],
            "terms": hl_terms,
            "suggestion": suggestion,
            "facets": {"field": [{"code": f, "label": tx.FIELD_LABEL.get(f, f), "count": n} for f, n in fields.most_common()],
                       "sub": [{"code": f, "label": CATEGORIES.get(f, f), "count": n} for f, n in subs.most_common()],
                       "sector": [{"code": s, "label": tx.SECTOR_LABEL.get(s, s), "count": sectors.get(s, 0)} for s, _ in tx.SECTORS],
                       "apply": [{"code": a, "label": tx.APPLY_LABEL.get(a, a), "count": applies.get(a, 0)} for a, _ in tx.APPLY_TYPES if applies.get(a, 0)],
                       "sources": [{"code": s, "label": config.SOURCE_LABEL.get(s, s), "count": n} for s, n in sources_cnt.most_common()]}}


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["status"] == "rejected":
        raise HTTPException(404)
    return db.row_to_dict(con, row)


_report_times: dict[str, list[float]] = defaultdict(list)
_reported: set[tuple[str, int]] = set()


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
    recent = [t for t in _report_times[ip] if t > time.time() - 3600]
    if len(recent) >= 20:
        raise HTTPException(429, "كتير بلاغات، جرّب بعد شوي")
    if not con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        raise HTTPException(404)
    _report_times[ip] = recent + [time.time()]
    if (ip, job_id) in _reported:
        return {"ok": True}
    _reported.add((ip, job_id))
    reason = str(body.get("reason", ""))[:40]
    con.execute("INSERT INTO reports VALUES (?,?,?)", (job_id, reason, db.now()))
    con.execute("UPDATE jobs SET reports=reports+1 WHERE id=?", (job_id,))
    con.execute("UPDATE jobs SET status='review' WHERE id=? AND reports>=? AND status='live'", (job_id, config.REPORTS_TO_HIDE))
    con.commit()
    _stats_cache.clear()
    return {"ok": True}


_stats_cache: dict[str, tuple[float, dict]] = {}


@app.get("/api/stats")
def stats(country: str | None = None):
    """Numbers for the opening screen (cached briefly: they change every 30 minutes at most)."""
    key = f"{country or 'sy'}:{_today()}"
    if (hit := _stats_cache.get(key)) and time.time() - hit[0] < 10 and hit[1].get("updated_at") == db.get_meta(con, "updated_at"):
        return hit[1]
    open_rows = _query(country=country)
    all_rows = _query(expired=True, country=country)
    sy_rows = open_rows if (country or "sy") != "tr" else _query(country="sy")
    tr_rows = open_rows if country == "tr" else _query(country="tr")
    demand = Counter()
    city_counts = Counter()
    for r in open_rows:
        demand[r["field"]] += 1
        for c in filter(None, (r["cities"] or "").split("|")):
            city_counts[c] += 1
    city_counts_tr = Counter()
    for r in tr_rows:
        for c in filter(None, (r["cities"] or "").split("|")):
            city_counts_tr[c] += 1
    out = {
        "open": len(open_rows),
        "open_sy": len(sy_rows),
        "open_tr": len(tr_rows),
        "six_months": len(all_rows),
        "junior": sum(1 for r in open_rows if r["experience"] is not None and r["experience"] <= 2),
        "employers": len({json.loads(r["data"]).get("employer") for r in all_rows} - {None}),
        "today": sum(1 for r in open_rows if (r["posted_at"] or "") >= (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()),
        "official": sum(1 for r in open_rows if r["official"]),
        "gov_count": sum(1 for r in open_rows if r["sector"] == "gov"),
        "ngo_count": sum(1 for r in open_rows if r["sector"] == "ngo"),
        "private_count": sum(1 for r in open_rows if r["sector"] == "private"),
        "city_counts": dict(city_counts),
        "city_counts_tr": dict(city_counts_tr),
        "demand": [{"field": f, "label": tx.FIELD_LABEL.get(f, f), "count": n}
                   for f, n in ([x for x in demand.most_common() if x[0] != "other"][:8] or demand.most_common(8))],
        "updated_at": db.get_meta(con, "updated_at"),
    }
    _stats_cache[key] = (time.time(), out)
    return out


# ------------------------------------------------------------------ Phase 3: employers, review queue, market pulse
_post_times: dict[str, list[float]] = defaultdict(list)


@app.post("/api/employer/jobs")
def employer_post(request: Request, body: dict = Body(...)):
    """An employer posts a job directly. It waits in the review queue until an admin approves it."""
    from .models import Job

    if body.get("website"):  # honeypot field, hidden from people
        return {"ok": True}
    ip = _client_ip(request)
    recent = [t for t in _post_times[ip] if t > time.time() - 86400]
    if len(recent) >= 10:
        raise HTTPException(429, "وصلت للحد اليومي")
    _post_times[ip] = recent + [time.time()]
    clip = lambda k, n=200: (str(body.get(k) or "").strip()[:n] or None)
    title, employer, phone = clip("title", 120), clip("employer", 120), clip("phone", 20)
    apply_url = da.strip_tracking(clip("apply_url", 300) or "") or None
    email = clip("email", 120)
    parsed_phones = tx.phones(phone or "")
    if not title or not employer or not (phone or apply_url or email):
        raise HTTPException(400, "لازم المسمى واسم الجهة وطريقة تقديم (رقم تلفون أو رابط أو إيميل)")
    if apply_url and da.is_disallowed_offline_url(apply_url):
        raise HTTPException(400, "رابط التقديم لازم يكون فورم أو موقع توظيف مباشر، مو صفحة تواصل اجتماعي أو منصة تجميع")
    if email and "@" not in email:
        raise HTTPException(400, "صيغة الإيميل غير صحيحة")
    if not (parsed_phones or apply_url or email):
        raise HTTPException(400, "رقم التلفون لازم يكون رقم سوري صحيح (يبدأ بـ 09)")
    deadline = clip("deadline", 10)
    if deadline:
        try:
            date.fromisoformat(deadline)
        except ValueError:
            raise HTTPException(400, "تاريخ الانتهاء لازم يكون YYYY-MM-DD")
    city = clip("city", 30)
    edu = body.get("education") if body.get("education") in tx.EDU_RANK else None
    text = clip("details", 3000) or ""
    job = Job(title=title, source="employer", post_url=config.PUBLIC_URL, posted_at=db.now(), employer=employer,
              cities=[city] if city in tx.CITIES else [], education=edu, education_from="stated" if edu else "unknown",
              kind="quick" if body.get("kind") == "quick" else "formal", deadline=deadline,
              salary=clip("salary", 60), shift=clip("shift", 60), apply_url=apply_url,
              phones=parsed_phones, whatsapp=bool(body.get("whatsapp")), email=email,
              experience=int(body["experience"]) if str(body.get("experience") or "").isdigit() else None,
              gender=body.get("gender") if body.get("gender") in ("male", "female") else None,
              country="sy",
              tasks=[l.strip("-• ") for l in text.splitlines() if l.strip()][:8], raw=text)
    job.field = tx.field_from_text(title, text)
    job.sector = tx.infer_sector(False, job.field, job.employer, text, hint=body.get("sector"))
    da.resolve_job_apply(job, client=None)
    jid, _ = db.upsert(con, job, status="review")
    con.execute("UPDATE job_sources SET post_url=? WHERE job_id=? AND source='employer'", (f"{config.PUBLIC_URL}/#job-{jid}", jid))
    con.commit()
    return {"ok": True, "id": jid}


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
    db.sync_fts(con, job_id)
    con.commit()
    _stats_cache.clear()
    return {"ok": True}


@app.get("/api/pulse")
def pulse(city: str | None = None, edu: str | None = None, country: str | None = None):
    """«نبض السوق»: what is asked for most, by month, field, city and education, over the last 6 months."""
    rows = _query(expired=True, country=country)
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
        for c in filter(None, r["cities"].split("|")):
            if c in tx.CITIES or c == "عدة محافظات":
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
    open_now = [r for r in rows if r["status"] == "live" and (r["expires_on"] or "") >= today]
    remote_open = sum(1 for r in open_now if "عن بعد" in r["data"])
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
def scam_check(body: dict = Body(...)):
    """Paste any job ad from WhatsApp/Facebook/Telegram and check for scam signals and repeated phone numbers."""
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


@app.get("/j/{job_id}", response_class=HTMLResponse)
def job_page(job_id: int, request: Request):
    """Shareable single-job page with Open Graph tags so WhatsApp/Telegram previews show the real title and city."""
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["status"] == "rejected":
        raise HTTPException(404)
    j = db.row_to_dict(con, row)
    e = html.escape
    cities = "، ".join(j.get("cities") or []) or "سوريا (عام)"
    edu = tx.EDU_LABEL.get(j.get("education") or "", "غير مشترط / غير مذكور")
    emp_raw = (j.get("employer") or "").strip()
    emp = emp_raw if emp_raw not in db._PLACEHOLDER_EMP else ("جهة حكومية رسمية" if j.get("official") else "")
    desc_parts = [emp or None, cities, f"المؤهل: {edu}", f"الراتب: {j['salary']}" if j.get("salary") else None,
                  f"آخر موعد: {j['deadline']}" if j.get("deadline") else None]
    desc = " · ".join(p for p in desc_parts if p)
    og_desc = (f"{desc} — {j['summary']}" if j.get("summary") else desc)[:260]
    share_url = f"{str(request.base_url).rstrip('/')}/j/{job_id}"

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
    if j.get("deadline"):
        ad_lines.append(f"📅 آخر موعد للتقديم: {j['deadline']}")
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

    btns = []
    for p in (j.get("phones") or [])[:1]:
        if p.startswith("09"):
            btns.append(f'<a class="wa" target="_blank" rel="noopener" href="https://wa.me/963{p[1:]}">راسل واتساب ({e(p)})</a>')
        btns.append(f'<a class="call" href="tel:{e(p)}">اتصل {e(p)}</a>')
    if j.get("email"):
        btns.append(f'<a class="btn primary" href="mailto:{e(j["email"])}">إرسال CV بالإيميل ({e(j["email"])})</a>')
    if has_direct_link:
        btns.append(f'<a class="btn primary" target="_blank" rel="noopener" href="{e(j["apply_url"])}">قدّم عالوظيفة</a>')
    btns.append(f'<a class="btn" style="border:1px solid var(--line)" href="/cv.html?job={job_id}">جهّز سيرة ذاتية إلها</a>')
    summary_html = f'<h3>نبذة ووصف الشاغر</h3><p style="line-height:1.7">{e(j["summary"])}</p>' if j.get("summary") else ""
    tasks_html = ("<h3>المهام</h3><ul>" + "".join(f"<li>{e(t)}</li>" for t in j.get("tasks") or []) + "</ul>") if j.get("tasks") else ""
    skills_html = ("<h3>المهارات المطلوبة</h3><ul>" + "".join(f"<li>{e(s)}</li>" for s in j.get("skills") or []) + "</ul>") if j.get("skills") else ""
    srcs_html = " · ".join(f'<a target="_blank" rel="noopener" href="{e(s["post_url"])}">{e(s["label"])}</a>' for s in j.get("sources") or [])
    return HTMLResponse(f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(j['title'])} · {e(config.SITE_NAME)}</title>
<meta name="description" content="{e(og_desc)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(j['title'])} — {e(cities)}">
<meta property="og:description" content="{e(og_desc)}">
<meta property="og:url" content="{e(share_url)}">
<meta name="twitter:card" content="summary">
<link rel="icon" href="/icons/icon.svg"><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/page.css">
</head><body>
<section class="hero slim"><div class="inner">
  <div class="eyebrow"><a class="link" href="/">← كل الوظائف على {e(config.SITE_NAME)}</a></div>
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
    <div class="share-box" style="margin-top:16px">
      <div class="share-box-title">📤 مشاركة ونسخ الإعلان مع الوصف وطريقة التقديم:</div>
      <textarea id="shareAdText" class="share-preview" dir="rtl">{e(share_text)}</textarea>
      <div class="share-actions">
        <button type="button" class="btn primary" onclick="navigator.clipboard.writeText(document.getElementById('shareAdText').value).then(()=>this.textContent='تم نسخ الإعلان مع الوصف ✅')">📋 نسخ الإعلان مع الوصف</button>
        <button type="button" class="btn ghost" onclick="navigator.clipboard.writeText('{e(primary_link)}').then(()=>this.textContent='تم نسخ الرابط ✅')">🔗 نسخ الرابط فقط</button>
        <a class="btn ghost" target="_blank" rel="noopener" href="{wa_share}">💬 مشاركة عبر واتساب</a>
        <a class="btn ghost" target="_blank" rel="noopener" href="{tg_share}">✈️ مشاركة عبر تيليجرام</a>
      </div>
    </div>
    <p class="note" style="color:var(--muted)">المصدر: {srcs_html}</p>
  </div>
</main></body></html>""")


@app.middleware("http")
async def _cache_headers(request, call_next):
    """Ensure shell assets always revalidate and API responses are never HTTP-cached."""
    resp = await call_next(request)
    p = request.url.path
    if p.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif p == "/" or p.endswith((".html", ".js", ".css", ".webmanifest")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="site")
