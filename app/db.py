"""SQLite store. Plain SQL with nothing SQLite-only beyond `INSERT OR IGNORE`/`ON CONFLICT`, so Postgres is a short move."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import config
from .arabic import soft_fold
from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    fp TEXT UNIQUE NOT NULL,          -- fingerprint: the same job from several channels is one row
    title TEXT NOT NULL,
    source TEXT NOT NULL,             -- the first source we saw it in
    country TEXT DEFAULT 'sy',        -- sy | tr
    sector TEXT DEFAULT 'private',    -- gov | ngo | private
    apply_types TEXT DEFAULT '',      -- |whatsapp|form|site|email|call|
    education TEXT,
    education_from TEXT,
    field TEXT,
    subfield TEXT,
    kind TEXT,
    official INTEGER DEFAULT 0,
    cities TEXT DEFAULT '',           -- |دمشق|حلب|
    experience INTEGER,
    deadline TEXT,
    posted_at TEXT,
    expires_on TEXT,
    status TEXT DEFAULT 'live',       -- live | review | rejected
    reports INTEGER DEFAULT 0,
    data TEXT NOT NULL,               -- the full Job as JSON
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_list ON jobs(status, expires_on, posted_at);
CREATE TABLE IF NOT EXISTS job_keys (
    k TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS job_keys_id ON job_keys(job_id);
CREATE TABLE IF NOT EXISTS job_sources (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    source TEXT NOT NULL,
    post_url TEXT NOT NULL,
    posted_at TEXT,
    UNIQUE(job_id, post_url)
);
CREATE TABLE IF NOT EXISTS posts (            -- every post we read, so extraction can be re-run after a fix
    channel TEXT NOT NULL,
    id INTEGER NOT NULL,
    date TEXT,
    text TEXT,
    links TEXT,
    jobs INTEGER DEFAULT 0,
    PRIMARY KEY(channel, id)
);
CREATE TABLE IF NOT EXISTS pdfs (name TEXT PRIMARY KEY, status TEXT, cards INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS reports (job_id INTEGER, reason TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bot_users (        -- Telegram alert bot: only the choices people make, nothing else
    chat_id INTEGER PRIMARY KEY,
    education TEXT,
    field TEXT,
    cities TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    step TEXT,
    created TEXT
);
CREATE TABLE IF NOT EXISTS bot_sent (chat_id INTEGER, job_id INTEGER, at TEXT, PRIMARY KEY(chat_id, job_id));
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    job_id UNINDEXED,
    title_text,
    employer_text,
    specs_text,
    body_text,
    aux_text,
    tokenize='unicode61'
);
"""


def sync_fts(con: sqlite3.Connection, job_id: int, job_dict: dict | None = None) -> None:
    """Keep the FTS5 index synchronized with clean, normalized fields (no raw JSON keys)."""
    from .search import build_fts_fields

    if job_dict is None:
        row = con.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            con.execute("DELETE FROM jobs_fts WHERE job_id=?", (job_id,))
            return
        job_dict = json.loads(row["data"])
    title_t, emp_t, specs_t, body_t, aux_t = build_fts_fields(job_dict)
    con.execute("DELETE FROM jobs_fts WHERE job_id=?", (job_id,))
    con.execute(
        "INSERT INTO jobs_fts (job_id, title_text, employer_text, specs_text, body_text, aux_text) VALUES (?,?,?,?,?,?)",
        (job_id, title_t, emp_t, specs_t, body_t, aux_t),
    )


def rebuild_fts(con: sqlite3.Connection) -> int:
    """Drop and rebuild the entire FTS5 search index from the jobs table."""
    from .search import build_fts_fields

    con.execute("DELETE FROM jobs_fts")
    rows = con.execute("SELECT id, data FROM jobs").fetchall()
    batch = []
    for r in rows:
        jd = json.loads(r["data"])
        t, e, s, b, a = build_fts_fields(jd)
        batch.append((r["id"], t, e, s, b, a))
    if batch:
        con.executemany(
            "INSERT INTO jobs_fts (job_id, title_text, employer_text, specs_text, body_text, aux_text) VALUES (?,?,?,?,?,?)",
            batch,
        )
    return len(batch)


def delete_jobs(con: sqlite3.Connection, job_ids: list[int]) -> int:
    """Safely remove jobs and all child index rows."""
    if not job_ids:
        return 0
    ph = ",".join("?" * len(job_ids))
    con.execute(f"DELETE FROM jobs_fts WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM job_sources WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM job_keys WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM jobs WHERE id IN ({ph})", job_ids)
    return len(job_ids)


def _migrate(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    for col, ddl in (("country", "TEXT DEFAULT 'sy'"), ("sector", "TEXT DEFAULT 'private'"), ("apply_types", "TEXT DEFAULT ''")):
        if col not in cols:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
    con.execute("CREATE INDEX IF NOT EXISTS jobs_filter ON jobs(status, country, sector, expires_on, posted_at)")
    # Auto-backfill jobs_fts once if jobs exist while FTS5 is empty or lacks aux_text column
    fts_cols = {r[1] for r in con.execute("PRAGMA table_info(jobs_fts)")}
    if "aux_text" not in fts_cols:
        con.execute("DROP TABLE IF EXISTS jobs_fts")
        con.execute(
            "CREATE VIRTUAL TABLE jobs_fts USING fts5("
            "job_id UNINDEXED, title_text, employer_text, specs_text, body_text, aux_text, tokenize='unicode61')"
        )
    has_jobs = con.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
    has_fts = con.execute("SELECT 1 FROM jobs_fts LIMIT 1").fetchone()
    if has_jobs and not has_fts:
        rebuild_fts(con)
        con.commit()


def _bootstrap_seed_if_empty(con: sqlite3.Connection, path) -> None:
    """When deployed to a cloud host (e.g. Render) with no existing DB, load data/seed_jobs.json.gz on first boot."""
    import gzip
    import os
    import sys

    if str(path) == ":memory:" or str(path).endswith("test.db") or "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return
    if con.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
        return
    seed_path = config.ROOT / "data" / "seed_jobs.json.gz"
    if not seed_path.exists():
        return
    try:
        with gzip.open(seed_path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        jobs_rows = payload.get("jobs") or []
        if not jobs_rows:
            return
        cols = list(jobs_rows[0].keys())
        col_list = ",".join(cols)
        placeholders = ",".join("?" * len(cols))
        con.executemany(
            f"INSERT OR IGNORE INTO jobs ({col_list}) VALUES ({placeholders})",
            [[r.get(c) for c in cols] for r in jobs_rows],
        )
        for s in payload.get("sources") or []:
            con.execute(
                "INSERT OR IGNORE INTO job_sources (job_id, source, post_url, posted_at) VALUES (?,?,?,?)",
                (s.get("job_id"), s.get("source"), s.get("post_url"), s.get("posted_at")),
            )
        for k in payload.get("keys") or []:
            con.execute("INSERT OR IGNORE INTO job_keys (k, job_id) VALUES (?,?)", (k.get("k"), k.get("job_id")))
        for m in payload.get("meta") or []:
            con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (m.get("key"), m.get("value")))
        rebuild_fts(con)
        con.commit()
    except Exception:
        pass


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.executescript(SCHEMA)
    _migrate(con)
    _bootstrap_seed_if_empty(con, path)
    return con


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str | None) -> str:
    return re.sub(r"[^\w]", "", soft_fold(s or "")).replace("_", "")


def _title_key(t: str) -> str:
    """«مدير/ة تقنية المعلومات (IT Manager)» and «مدير تقنية المعلومات» are the same job.
    Arabic brackets stay: «سائق آليات (جرار زراعي)» and «سائق آليات (آلية نظافة)» are two jobs."""
    if re.search(r"[ء-ي]", t or ""):
        t = re.sub(r"\([^)]*[A-Za-z][^)]*\)|/\s*ة|[A-Za-z]+", " ", t or "")
    return _norm(t)


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
    if re.search(r"^https?://(?:t\.me|wa\.me|facebook\.com|instagram\.com)/?$", u):
        return ""
    return re.sub(r"^https?://(?:www\.)?", "", u)


def fingerprint(job: Job) -> str:
    """Normalized title + city + who (employer, else first phone) + deadline or posting week."""
    when = job.deadline
    if not when and job.posted_at:
        d = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00")).date()
        when = "w%d-%02d" % d.isocalendar()[:2]
    who = "gov" if job.official and job.deadline else (_norm(job.employer)[:20] or (job.phones[0] if job.phones else ""))
    return "|".join([_title_key(job.title), _norm(job.cities[0] if job.cities else ""), who, when or ""])


def dedupe_keys(job: Job) -> list[str]:
    """Extra exact-match keys so the same job reposted across 300-400 channels merges even if one lacks employer."""
    tk = _title_key(job.title)
    if not tk:
        return []
    keys = []
    if u := _norm_url(job.apply_url):
        keys.append(f"url:{tk}|{u}")
    if job.phones:
        month = (job.posted_at or "")[:7] or date.today().isoformat()[:7]
        keys.append(f"ph:{tk}|{job.phones[0]}|{month}")
    if job.deadline and job.cities:
        keys.append(f"dl:{tk}|{_norm(job.cities[0])}|{job.deadline}")
    return keys


def expires_on(job: Job) -> str:
    start = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00")).date() if job.posted_at else date.today()
    max_day = (start + timedelta(days=config.HISTORY_DAYS)).isoformat()
    if job.deadline:
        return min(job.deadline, max_day)
    return min((start + timedelta(days=config.QUICK_DAYS if job.kind == "quick" else config.FORMAL_DAYS)).isoformat(), max_day)


def _apply_str(job: Job) -> str:
    return "|" + "|".join(job.apply_types) + "|" if job.apply_types else ""


def upsert(con: sqlite3.Connection, job: Job, status: str | None = None) -> tuple[int, bool]:
    """Store a job, or attach its source to the row it duplicates. Returns (id, is_new)."""
    from . import direct_apply as da
    from . import taxonomy as tx

    if job.apply_url:
        cleaned_u = da.strip_tracking(job.apply_url)
        job.apply_url = None if (not cleaned_u or da.is_disallowed_offline_url(cleaned_u)) else cleaned_u
    if not job.apply_types:
        job.apply_types = tx.infer_apply_types(job.apply_url, job.phones, job.whatsapp, job.email)
    if not job.sector or job.sector == "private":
        job.sector = tx.infer_sector(job.official, job.field, job.employer, job.raw, hint=job.sector if job.sector in ("gov", "ngo") else None)
    job.country = "sy"
    if job.raw:
        job.raw = job.raw.replace("\ufffd", "").replace("<PRIVATE_PERSON>", "")
    if "\ufffd" in (job.title or "") or not re.search(r"[\u0621-\u064Aa-zA-Z]{3,}", job.title or ""):
        status = "rejected"

    fp = fingerprint(job)
    keys = dedupe_keys(job)
    row = con.execute("SELECT id, data, status, expires_on FROM jobs WHERE fp=?", (fp,)).fetchone()
    if not row and keys:
        q = f"SELECT j.id, j.data, j.status, j.expires_on FROM job_keys k JOIN jobs j ON j.id=k.job_id WHERE k.k IN ({','.join('?' * len(keys))}) LIMIT 1"
        row = con.execute(q, keys).fetchone()
    t = now()
    if row:
        jid, new = row["id"], False
        old = json.loads(row["data"])
        jd = job.to_dict()
        if old.get("apply_url") and da.is_disallowed_offline_url(old["apply_url"]):
            old["apply_url"] = None
        merged = dict(old)
        # Keep the richer record: official sources win, and missing fields get filled from duplicates.
        if (job.official and not old.get("official")) or len(json.dumps(jd)) > len(row["data"]) + 200:
            merged = jd
            for k in ("employer", "salary", "deadline", "education", "apply_url", "email", "shift"):
                if not merged.get(k) and old.get(k):
                    merged[k] = old[k]
        else:
            for k in ("employer", "salary", "deadline", "education", "apply_url", "email", "shift"):
                if not merged.get(k) and jd.get(k):
                    merged[k] = jd[k]
            if jd.get("phones") and not merged.get("phones"):
                merged["phones"] = jd["phones"]
            if jd.get("apply_types"):
                merged["apply_types"] = sorted(set(merged.get("apply_types") or []) | set(jd["apply_types"]))
            if jd.get("tags"):
                merged["tags"] = sorted(set(merged.get("tags") or []) | set(jd["tags"]))
        if merged.get("apply_url") and da.is_disallowed_offline_url(merged["apply_url"]):
            merged["apply_url"] = None
        merged["country"] = "sy"
        new_exp = max(row["expires_on"] or "", expires_on(job))
        new_status = status if status is not None else (
            "live" if row["status"] == "expired" and new_exp >= date.today().isoformat() else row["status"]
        )
        con.execute(
            "UPDATE jobs SET data=?, country='sy', sector=?, apply_types=?, expires_on=?, status=?, updated_at=? WHERE id=?",
            (json.dumps(merged, ensure_ascii=False),
             merged.get("sector", job.sector),
             "|" + "|".join(merged.get("apply_types") or []) + "|" if merged.get("apply_types") else "",
             new_exp, new_status, t, jid),
        )
    else:
        if status is None:
            status = "review" if job.confidence < config.REVIEW_BELOW else "live"
        cur = con.execute(
            """INSERT INTO jobs (fp, title, source, country, sector, apply_types, education, education_from, field, subfield,
               kind, official, cities, experience, deadline, posted_at, expires_on, status, data, first_seen, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fp, job.title, job.source, job.country, job.sector, _apply_str(job), job.education, job.education_from,
             job.field, job.subfield, job.kind, int(job.official), "|" + "|".join(job.cities) + "|" if job.cities else "",
             job.experience, job.deadline, job.posted_at, expires_on(job), status,
             json.dumps(job.to_dict(), ensure_ascii=False), t, t))
        jid, new = cur.lastrowid, True
    for k in keys:
        con.execute("INSERT OR IGNORE INTO job_keys VALUES (?,?)", (k, jid))
    con.execute("INSERT OR IGNORE INTO job_sources (job_id, source, post_url, posted_at) VALUES (?,?,?,?)",
                (jid, job.source, job.post_url, job.posted_at))
    sync_fts(con, jid, merged if row else job.to_dict())
    return jid, new


def expire_past(con: sqlite3.Connection) -> int:
    """Mark live jobs whose deadline / expiry date has already passed as expired."""
    today = date.today().isoformat()
    cur = con.execute("UPDATE jobs SET status='expired' WHERE status='live' AND expires_on < ?", (today,))
    return cur.rowcount


def prune_old(con: sqlite3.Connection, days: int = config.HISTORY_DAYS) -> int:
    """Drop jobs older than `days` (6 months) so old records don't pile up across channels, and expire past jobs."""
    expire_past(con)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    old_ids = [r[0] for r in con.execute("SELECT id FROM jobs WHERE posted_at < ?", (cutoff,)).fetchall()]
    if not old_ids:
        return 0
    delete_jobs(con, old_ids)
    con.execute("DELETE FROM posts WHERE date < ?", (cutoff,))
    return len(old_ids)


def get_meta(con, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key: str, value) -> None:
    con.execute("INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))


_SPEC_RE = re.compile(r"(?:الاختصاص الدقيق المفضل|الاختصاص العام المفضل|الاختصاص المطلوب|التخصص المطلوب|التخصص العلمي)\s*[:：]\s*([^\n\r|]+)")
_TASKS_SEC_RE = re.compile(
    r"(?:المهام الرئيسيه|المهام الرئيسية|المهام والمسؤوليات|ابرز المهام|أبرز المهام|الوصف الوظيفي)\s*[:：]?\s*[\r\n]+([\s\S]{15,700}?)(?=\r?\n\s*(?:الشروط|المؤهلات|المتطلبات|ملاحظات|التقديم|للتقديم|رابط|🔗|✅|📄|$))"
)
_SKILLS_SEC_RE = re.compile(
    r"(?:الشروط والمؤهلات|المؤهلات والخبرات|الشروط المطلوبه|الشروط المطلوبة|المتطلبات|ملاحظات للمتقدم)\s*[:：]?\s*([\s\S]{15,700}?)(?=\r?\n\s*(?:المهام|التقديم|للتقديم|رابط|🔗|📄|$))"
)
_SKIP_SUMMARY_LINE = re.compile(
    r"^(?:سلام عليكم|وعليكم السلام|التفاصيل|قبل أن تتقدّم|قبل ان تتقدم|"
    r"(?:التقديم|للتقديم|للتواصل والتقديم|تقدم الآن|تقدّم الآن)\s*:?\s*$|"
    r"للتواصل والتقديم عبر الرقم المرفق|"
    r"📢\s*وظيفة جديدة|الوصف المستخرج|🔗|🏢\s*الجهة|📍\s*الموقع|📅\s*آخر موعد|الفئة الوظيفية|محافظة العمل|"
    r"(?:https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}/\S*$|#|\*?\s*(?:للتواصل|الاتصال|مطلوب)\s*:?\s*$)|"
    r"لنشر ما لديكم|راسلونا دايركت|للتواصل ولمعلومات|الاتصال على الرقم|(?:^|[\s(])@[A-Za-z0-9_]{3,}(?!\.[A-Za-z0-9-])",
    re.I,
)


def _clean_bullets(block: str, limit: int = 5) -> list[str]:
    out: list[str] = []
    for ln in (block or "").splitlines():
        s = re.sub(r"^[\s•\-–*▪▫♦✓✔\d.)+]+", "", ln).strip()
        if 8 <= len(s) <= 220 and not s.startswith("http") and not _SKIP_SUMMARY_LINE.search(s):
            if s not in out:
                out.append(s)
        if len(out) >= limit:
            break
    return out


_PLACEHOLDER_EMP = {"غير معلن", "اعلانات", "إعلانات", "شواغر سوريا", ""}


def _fallback_summary(d: dict) -> str:
    title = (d.get("title") or "شاغر وظيفي").strip()
    emp_raw = (d.get("employer") or "").strip()
    emp = emp_raw if emp_raw not in _PLACEHOLDER_EMP else ("جهة حكومية رسمية" if d.get("official") else "")
    cs = "، ".join((d.get("cities") or [])[:3]) or "سوريا"
    dl = f" · آخر موعد للتقديم: {d['deadline']}" if d.get("deadline") else ""
    head = f"شاغر وظيفي ({title}) لدى {emp} في {cs}{dl}" if emp else f"شاغر وظيفي ({title}) في {cs}{dl}"

    has_link = bool(d.get("apply_url") and not str(d["apply_url"]).lower().startswith("https://wa.me/"))
    has_email = bool(d.get("email"))
    phones = d.get("phones") or []

    if has_email and not has_link:
        how = f"للتقديم: إرسال السيرة الذاتية (CV) إلى البريد الإلكتروني {d['email']}"
    elif phones and not has_link and not has_email:
        how = f"للتقديم والتواصل المباشر عبر الرقم {phones[0]}"
    elif has_link and has_email:
        how = f"التقديم عبر رابط التقديم المرفق أو إرسال السيرة الذاتية إلى {d['email']}"
    elif has_link:
        how = "التقديم عبر استمارة / رابط التقديم المباشر المرفق."
    else:
        how = ""
    return f"{head} — {how}" if how else head


def _enrich_from_raw(d: dict) -> None:
    raw = (d.get("raw") or "").replace("\ufffd", "").replace("<PRIVATE_PERSON>", "").strip()
    if d.get("raw"):
        d["raw"] = raw
    title = (d.get("title") or "").strip()
    emp_raw = (d.get("employer") or "").strip()
    city_set = set(d.get("cities") or [])
    if raw:
        if not d.get("specialty"):
            if m := _SPEC_RE.search(raw):
                spec = m.group(1).strip(" .-–|")
                if 2 < len(spec) <= 90:
                    d["specialty"] = spec
        if not d.get("tasks"):
            if m := _TASKS_SEC_RE.search(raw):
                d["tasks"] = _clean_bullets(m.group(1))
        if not d.get("skills"):
            if m := _SKILLS_SEC_RE.search(raw):
                d["skills"] = _clean_bullets(m.group(1))

    # Replace any stale synthetic summary or corrupted summary
    cur_sum = (d.get("summary") or "").replace("\ufffd", "").replace("<PRIVATE_PERSON>", "").strip()
    if cur_sum and ("التقديم متاح مباشرة عبر الرابط المرفق" in cur_sum or len(cur_sum) < 10):
        d["summary"] = None
    else:
        d["summary"] = cur_sum or None

    if not d.get("summary"):
        if d.get("tasks"):
            d["summary"] = " · ".join(d["tasks"][:2])[:240]
        elif raw:
            lines: list[str] = []
            title_head = title.split("—")[0].strip()
            for ln in raw.splitlines():
                s = re.sub(r"https?://\S+", "", ln)
                s = re.sub(r"\s+", " ", s).strip(" •-*–\t\r")
                if not s or len(s) < 12:
                    continue
                if s == title or (title_head and s == title_head) or s == emp_raw or s in city_set:
                    continue
                if _SKIP_SUMMARY_LINE.search(s):
                    continue
                if re.fullmatch(r"[\d\s+\-()،,./:]+", s):
                    continue
                if s.startswith("الفئة الوظيفية:") or s.startswith("محافظة العمل:") or s.startswith("المستوى التعليمي:"):
                    continue
                lines.append(s)
                if sum(len(x) for x in lines) >= 180 or len(lines) >= 2:
                    break
            if lines:
                d["summary"] = " · ".join(lines)[:240]
        if not d.get("summary") and d.get("skills"):
            d["summary"] = " · ".join(d["skills"][:2])[:240]
        if not d.get("summary"):
            d["summary"] = _fallback_summary(d)


def row_to_dict(con, row) -> dict:
    d = json.loads(row["data"])
    _enrich_from_raw(d)
    d.pop("raw", None)
    d["id"] = row["id"]
    d["status"] = row["status"]
    d["expires_on"] = row["expires_on"]
    d["first_seen"] = row["first_seen"]
    d.setdefault("country", row["country"] if "country" in row.keys() else "sy")
    d.setdefault("sector", row["sector"] if "sector" in row.keys() else ("gov" if d.get("official") else "private"))
    d.setdefault("apply_types", [x for x in (row["apply_types"] or "").split("|") if x] if "apply_types" in row.keys() else [])
    srcs = []
    seen_src = set()
    for s in con.execute("SELECT source, post_url FROM job_sources WHERE job_id=? ORDER BY posted_at", (row["id"],)):
        lbl = config.SOURCE_LABEL.get(s["source"], s["source"])
        if lbl in seen_src:
            continue
        seen_src.add(lbl)
        srcs.append(dict(source=s["source"], post_url=s["post_url"], label=lbl))
    d["sources"] = srcs
    return d
