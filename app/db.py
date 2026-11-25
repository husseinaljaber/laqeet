"""SQLite store. Plain SQL with nothing SQLite-only beyond `INSERT OR IGNORE`/`ON CONFLICT`, so Postgres is a short move."""
from __future__ import annotations

import gc
import json
import logging
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
CREATE INDEX IF NOT EXISTS jobs_country_status ON jobs(country, status, expires_on, posted_at);
CREATE TABLE IF NOT EXISTS job_keys (
    k TEXT NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    PRIMARY KEY (k, job_id)
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
CREATE TABLE IF NOT EXISTS employer_tokens (  -- self-service token so employers can track & close their own job ads
    token TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    verified INTEGER DEFAULT 0,
    contact_note TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS employer_tokens_job ON employer_tokens(job_id);
CREATE TABLE IF NOT EXISTS source_health (    -- one row per source: did the last runs bring anything?
    source TEXT PRIMARY KEY,
    kind TEXT,                    -- telegram | reliefweb | ingo_ats | web_portals | turkey_portals
    last_run TEXT,
    last_count INTEGER DEFAULT 0,
    last_items_at TEXT,           -- last run that brought at least one post/job
    last_error TEXT,
    fail_streak INTEGER DEFAULT 0
);
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
    """Drop and rebuild the entire FTS5 search index from the jobs table in memory-bounded batches."""
    from .search import build_fts_fields

    con.execute("DELETE FROM jobs_fts")
    cur = con.execute("SELECT id, data FROM jobs")
    total = 0
    while True:
        chunk = cur.fetchmany(400)
        if not chunk:
            break
        batch = []
        for r in chunk:
            jd = json.loads(r["data"])
            t, e, s, b, a = build_fts_fields(jd)
            batch.append((r["id"], t, e, s, b, a))
        if batch:
            con.executemany(
                "INSERT INTO jobs_fts (job_id, title_text, employer_text, specs_text, body_text, aux_text) VALUES (?,?,?,?,?,?)",
                batch,
            )
            total += len(batch)
    return total


def delete_jobs(con: sqlite3.Connection, job_ids: list[int]) -> int:
    """Safely remove jobs and all child index rows."""
    if not job_ids:
        return 0
    ph = ",".join("?" * len(job_ids))
    con.execute(f"DELETE FROM jobs_fts WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM job_sources WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM job_keys WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM employer_tokens WHERE job_id IN ({ph})", job_ids)
    con.execute(f"DELETE FROM jobs WHERE id IN ({ph})", job_ids)
    return len(job_ids)


def rebuild_dedupe_keys(con: sqlite3.Connection) -> int:
    """Backfill job_keys for all stored rows so phone/email dedup works across historical ads."""
    from .models import Job

    valid_fields = {f.name for f in Job.__dataclass_fields__.values()}
    cur = con.execute("SELECT id, data FROM jobs")
    total = 0
    while True:
        chunk = cur.fetchmany(400)
        if not chunk:
            break
        batch: list[tuple[str, int]] = []
        for r in chunk:
            d = json.loads(r["data"])
            kwargs = {k: v for k, v in d.items() if k in valid_fields and v is not None}
            kwargs.setdefault("title", "")
            kwargs.setdefault("source", "unknown")
            kwargs.setdefault("post_url", "")
            kwargs.setdefault("posted_at", "")
            job = Job(**kwargs)
            for k in dedupe_keys(job):
                batch.append((k, r["id"]))
        if batch:
            con.executemany("INSERT OR IGNORE INTO job_keys (k, job_id) VALUES (?,?)", batch)
            total += len(batch)
    return total


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
    jk_sql_row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='job_keys'").fetchone()
    jk_needs_upgrade = bool(jk_sql_row and "PRIMARY KEY (k, job_id)" not in jk_sql_row["sql"])
    if jk_needs_upgrade:
        con.execute("DROP TABLE IF EXISTS job_keys")
        con.execute(
            "CREATE TABLE job_keys (k TEXT NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id), PRIMARY KEY (k, job_id))"
        )
        con.execute("CREATE INDEX IF NOT EXISTS job_keys_id ON job_keys(job_id)")
    kv = con.execute("SELECT value FROM meta WHERE key='keys_version'").fetchone()
    if has_jobs and (jk_needs_upgrade or not kv or kv["value"] != "3"):
        rebuild_dedupe_keys(con)
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('keys_version', '3')")
        con.commit()


def _bootstrap_seed_if_empty(con: sqlite3.Connection, path) -> None:
    """When deployed to a cloud host (e.g. Render) with no existing DB, stream seed_jobs.jsonl.gz on first boot."""
    import gzip
    import os
    import sys

    if str(path) == ":memory:" or str(path).endswith("test.db") or "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return
    if con.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
        return
    jsonl_path = config.ROOT / "data" / "seed_jobs.jsonl.gz"
    json_path = config.ROOT / "data" / "seed_jobs.json.gz"
    seed_path = jsonl_path if jsonl_path.exists() else json_path
    if not seed_path.exists():
        return
    try:
        if seed_path == jsonl_path:
            jobs_buf, fts_buf, src_buf, key_buf, meta_buf = [], [], [], [], []
            jobs_sql = ""
            has_fts = False

            def _flush_jobs() -> None:
                if jobs_buf and jobs_sql:
                    con.executemany(jobs_sql, jobs_buf)
                    jobs_buf.clear()

            def _flush_fts() -> None:
                if fts_buf:
                    con.executemany(
                        "INSERT INTO jobs_fts (job_id, title_text, employer_text, specs_text, body_text, aux_text) VALUES (?,?,?,?,?,?)",
                        fts_buf,
                    )
                    fts_buf.clear()

            def _flush_src() -> None:
                if src_buf:
                    con.executemany(
                        "INSERT OR IGNORE INTO job_sources (job_id, source, post_url, posted_at) VALUES (?,?,?,?)",
                        src_buf,
                    )
                    src_buf.clear()

            def _flush_keys() -> None:
                if key_buf:
                    con.executemany("INSERT OR IGNORE INTO job_keys (k, job_id) VALUES (?,?)", key_buf)
                    key_buf.clear()

            with gzip.open(seed_path, "rt", encoding="utf-8") as f:
                header = json.loads(f.readline())
                cols = header["jobs_cols"]
                has_fts = bool(header.get("fts"))
                jobs_sql = f"INSERT OR IGNORE INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for line in f:
                    tag, raw_val = line.split("\t", 1)
                    val = json.loads(raw_val)
                    if tag == "j":
                        jobs_buf.append(val)
                        if len(jobs_buf) >= 400:
                            _flush_jobs()
                    elif tag == "f":
                        fts_buf.append(val)
                        if len(fts_buf) >= 400:
                            _flush_fts()
                    elif tag == "s":
                        src_buf.append(val)
                        if len(src_buf) >= 1000:
                            _flush_src()
                    elif tag == "k":
                        key_buf.append(val)
                        if len(key_buf) >= 1000:
                            _flush_keys()
                    elif tag == "m":
                        meta_buf.append(val)
            _flush_jobs()
            _flush_fts()
            _flush_src()
            _flush_keys()
            if meta_buf:
                con.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", meta_buf)
            if not has_fts:
                rebuild_fts(con)
            expire_past(con)
            con.commit()
            return

        with gzip.open(seed_path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        jobs_rows = payload.get("jobs") or []
        if not jobs_rows:
            return
        cols = list(jobs_rows[0].keys())
        col_list = ",".join(cols)
        placeholders = ",".join("?" * len(cols))
        for i in range(0, len(jobs_rows), 500):
            chunk = jobs_rows[i:i + 500]
            con.executemany(
                f"INSERT OR IGNORE INTO jobs ({col_list}) VALUES ({placeholders})",
                [[r.get(c) for c in cols] for r in chunk],
            )
        sources_rows = payload.get("sources") or []
        for i in range(0, len(sources_rows), 1000):
            chunk = sources_rows[i:i + 1000]
            con.executemany(
                "INSERT OR IGNORE INTO job_sources (job_id, source, post_url, posted_at) VALUES (?,?,?,?)",
                [(s.get("job_id"), s.get("source"), s.get("post_url"), s.get("posted_at")) for s in chunk],
            )
        key_rows = payload.get("keys") or []
        for i in range(0, len(key_rows), 1000):
            chunk = key_rows[i:i + 1000]
            con.executemany(
                "INSERT OR IGNORE INTO job_keys (k, job_id) VALUES (?,?)",
                [(k.get("k"), k.get("job_id")) for k in chunk],
            )
        meta_rows = payload.get("meta") or []
        if meta_rows:
            con.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                [(m.get("key"), m.get("value")) for m in meta_rows],
            )
        del payload, jobs_rows, sources_rows, key_rows, meta_rows
        gc.collect()
        rebuild_fts(con)
        expire_past(con)
        con.commit()
    except Exception:
        logging.getLogger("db").exception("Seed bootstrap failed for %s", seed_path)


# Private job sites whose adapters used to default to «جامعة» when the ad said nothing (fixed Nov 2026).
_GUESSED_BACHELOR_SOURCES = frozenset({
    "jobinsyria", "jobsyria", "nsjobs_web", "powerholding", "hcd_syria", "beeorder", "hknsyria", "worklink",
})
EDU_FIX_VERSION = "1"


def repair_education(con: sqlite3.Connection) -> int:
    """One-off fix for rows stored before the craft/teacher rules: a craft title means no degree, and a private-site
    ad that never named a degree is «unknown», not «جامعة». Ministry grades are never touched. Runs once per DB."""
    from . import taxonomy as tx

    done = con.execute("SELECT value FROM meta WHERE key='edu_fix_version'").fetchone()
    if done and done["value"] == EDU_FIX_VERSION:
        return 0
    changed = 0
    rows = con.execute("SELECT id, title, source, education, education_from, data FROM jobs").fetchall()
    for r in rows:
        if r["education_from"] == "grade":
            continue
        d = json.loads(r["data"])
        guess, how = tx.guess_education(r["title"] or "", (d.get("raw") or "")[:1500])
        new = None
        if how == "title" and guess == "none" and r["education"] != "none":
            new = ("none", "title")
        elif r["education"] == "bachelor" and r["source"] in _GUESSED_BACHELOR_SOURCES and guess is None:
            new = (None, "unknown")
        if new is None:
            continue
        d["education"], d["education_from"] = new
        con.execute("UPDATE jobs SET education=?, education_from=?, data=? WHERE id=?",
                    (new[0], new[1], json.dumps(d, ensure_ascii=False), r["id"]))
        changed += 1
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('edu_fix_version', ?)", (EDU_FIX_VERSION,))
    con.commit()
    if changed:
        logging.getLogger("db").info("repair_education: fixed %d rows", changed)
    return changed


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA cache_size=-8000")
    con.executescript(SCHEMA)
    _migrate(con)
    _bootstrap_seed_if_empty(con, path)
    repair_education(con)
    return con


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str | None) -> str:
    return re.sub(r"[^\w]", "", soft_fold(s or "").lower()).replace("_", "")


_STOP_CORE_WORDS = frozenset({
    "مطلوب", "مطلوبه", "مطلوبين", "مطلوبون", "يلزمنا", "موظف", "موظفه", "موظفين", "موظفات",
    "فرصه", "عمل", "للعمل", "شركه", "لشركه", "بشركه", "مكتب", "مركز", "عياده", "في", "لدي", "لدى",
    "مع", "من", "الي", "الى", "على", "عن", "ضمن", "حسب", "تركيا", "سوريا", "اسطنبول", "الاوروبيه",
    "الاسيويه", "باشاك", "شهير", "اسنيورت", "شيشلي", "الفاتح", "بيليك", "دوزو", "بيليكدوزو", "باغجلار",
    "غازي", "عنتاب", "مرسين", "بورصه", "دمشق", "حلب", "ادلب", "حمص", "حماه", "دوام", "كامل",
    "جزئي", "جزئى", "بعد", "حضوري", "عاجل", "فورا", "بخبره", "خبره", "انثي", "انثى", "ذكور", "اناث",
    "شاب", "صبيه", "منطقه", "بمنطقه", "باستطنبول", "باسطنبول", "بدمشق", "بحلب", "و", "او", "في",
})


def _strip_title_noise(t: str) -> str:
    """Canonicalize title for dedup matching; keeps distinguishing Arabic specialty brackets intact."""
    import unicodedata

    s = unicodedata.normalize("NFKC", t or "").replace("ـ", "")
    s = re.sub("[\u064B-\u0652]", "", s)
    s = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿←-⇿■-◿]", " ", s)
    s = re.sub(r"^\s*(?:\d{1,2}|[٠-٩]{1,2})\s*[-.)]?\s*", "", s)
    for _ in range(3):
        s = re.sub(
            r"^\s*(?:إعلان\s*توظيف|اعلان\s*توظيف|إعلان\s*شاغر|اعلان\s*شاغر|إعلان|اعلان|فرص[ةه]\s*عمل|شاغر\s*وظيف[يى]|وظيف[ةه]|نحن\s*نوظ[ّ]?ف|hiring|vacancy(?:\s*announcement)?|position(?:\s*title)?|job\s*title)\s*[:\-–|/|]*\s*",
            "",
            s,
            flags=re.I,
        )
        s = re.sub(
            r"^\s*(?:تعلن|يعلن|نعلن)\s+(?:شركة|مؤسسة|منظمة|عيادة|مركز|مطعم|مصنع|معمل|مجموعة|جمعية)[^:\n]{2,55}?(?:عن\s+حاجتها\s*(?:إلى|الى|لـ|ل)|عن\s+توفر\s+(?:فرصة\s+عمل|شاغر(?:\s+وظيفي)?)\s*(?:بمسمى|لـ|ل|بصفة)?|لشغل\s+وظيفة|لتوظيف)\s*[:\-–]?\s*",
            "",
            s,
            flags=re.I,
        )
        s = re.sub(r"^\s*(?:مطلوب(?:ة|ون|ين)?|يلزمنا)\s*[:\-–/]*\s*", "", s, flags=re.I)
    s = soft_fold(s).lower()
    if re.search(r"[ء-ي]", s):
        s = re.sub(r"\([^)]*[a-z][^)]*\)", " ", s)
        s = re.sub(r"\s+[–\-|]\s*[a-z0-9 &/,.-]{5,}$", " ", s)
    s = re.sub(
        r"\([^)]*(?:دوام|عن\s*بعد|حضوري|عاجل|syria|سوريا|turkey|t[uü]rkiye|تركيا|re-?advertised|maternity\s+cover)[^)]*\)",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"\([^)]*$", " ", s)
    s = re.sub(r"/\s*[ةه]\b", "", s)
    s = re.sub(r"(?:دوام\s*(?:كامل|جزئي|جزئى|حضوري)|عن\s*بعد|عمل\s*حر)\s*$", " ", s)
    s = re.sub(
        r"(?:\s+(?:في|بـ?|منطق[ةه]|ضمن)|\s+[-–/]+)?\s*(?:اسطنبول(?:\s+(?:الاوروبيه|الاسيويه))?|تركيا|سوريا|دمشق|حلب|غازي\s+عنتاب|مرسين|بورصه|انطاليا|انقره|ازمير|سقاريا|ادلب)\b.*$",
        " ",
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


def _title_key(t: str) -> str:
    return _norm(_strip_title_noise(t))


def _specific_core_key(t: str) -> str:
    """For distinct >=3-word (or long >=2-word) titles, return a city/filler-agnostic key for mirror dedup."""
    cleaned = _strip_title_noise(t)
    words = [w for w in re.findall(r"[ء-يa-z0-9]+", cleaned) if len(w) >= 2 and w not in _STOP_CORE_WORDS]
    joined = "".join(words)
    if len(words) >= 3 or (len(words) >= 2 and len(joined) >= 16):
        return joined
    return ""


def _norm_phone(p: str | None, country: str = "sy") -> str:
    d = re.sub(r"\D", "", p or "")
    if country == "tr" and (m := re.match(r"^(?:00)?90(5\d{9})$", d)):
        return "0" + m.group(1)
    if country != "tr" and (m := re.match(r"^(?:00)?963(9\d{8})$", d)):
        return "0" + m.group(1)
    return d or (p or "").strip()


def _adjacent_months(iso_str: str | None) -> list[str]:
    """Return [month, prev_month, next_month] so late-month reposts never miss a boundary match."""
    try:
        d = datetime.fromisoformat((iso_str or "")[:10]).date() if iso_str else date.today()
    except ValueError:
        d = date.today()
    cur = d.strftime("%Y-%m")
    prev_m = (d.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    next_m = (d.replace(day=28) + timedelta(days=5)).strftime("%Y-%m")
    return list(dict.fromkeys([cur, prev_m, next_m]))


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
    who = "gov" if job.official and job.deadline else (_norm(job.employer)[:20] or (_norm_phone(job.phones[0], job.country) if job.phones else ""))
    return "|".join([_title_key(job.title), _norm(job.cities[0] if job.cities else ""), who, when or ""])


_ROLE_STEM = {
    "محاسبه": "محاسب",
    "مندوبات": "مندوب",
    "مندوبه": "مندوب",
    "مترجمه": "مترجم",
    "مسوقه": "مسوق",
    "مصممه": "مصمم",
    "معلمه": "معلم",
    "مدرسه": "مدرس",
    "صيدلانيه": "صيدلاني",
    "طبيبه": "طبيب",
    "مبرمجه": "مبرمج",
    "ممرضه": "ممرض",
    "كاشيره": "كاشير",
    "بائعه": "بائع",
    "كوافيره": "كوافير",
    "حلاقه": "حلاق",
    "لمعمل": "معمل",
    "بمعمل": "معمل",
    "للمعامل": "معمل",
    "لمطعم": "مطعم",
    "بمطعم": "مطعم",
    "للمطعم": "مطعم",
    "لمركز": "مركز",
    "بمركز": "مركز",
    "لعياده": "عياده",
    "بعياده": "عياده",
    "لمحل": "محل",
    "بمحل": "محل",
    "لشركه": "شركه",
    "بشركه": "شركه",
    "للشركه": "شركه",
    "لمكتب": "مكتب",
    "بمكتب": "مكتب",
}


def _role_tokens_ordered(title: str) -> list[str]:
    """Ordered distinguishing tokens of a job title after stripping boilerplate and venue/salary modifiers."""
    s = _strip_title_noise(title)
    s = re.sub(r"\b(?:براتب|راتب)\s+[\d,.]+(?:\s*(?:الف|ليره|ليرة|دولار|tl|try|usd|\$))?", " ", s, flags=re.I)
    s = re.sub(r"\b(?:في\s+)?منطق[هة]\s+\S+", " ", s)
    boiler = {
        "مطلوب", "مطلوبه", "مطلوبين", "مطلوبون", "يلزمنا", "فرصه", "نبحث", "تبحث", "اعلان", "تعلن", "يعلن", "نعلن",
        "موظف", "موظفه", "موظفين", "موظفات", "شاب", "فتاه", "سيده", "عامل", "عامله", "عمال", "معلم", "حصرا",
        "انثى", "انثي", "ذكر", "ذكور", "اناث", "متفرغ", "متفرغه", "فريقنا", "ذو", "توفر", "امكانيه",
        "للاعمال", "للانضمام", "للجنسين", "موثوقه", "احترافيه", "شخص", "اشخاص", "كادر", "مميز", "متميز",
        "قادر", "لديها", "لديه", "شغوف", "العربيه", "عربي",
        "عمل", "للعمل", "ضمن", "فريق", "العمل", "في", "من", "الى", "الي", "على", "عن", "مع", "او", "لدى", "لدي",
        "بخبره", "خبره", "دوام", "بدوام", "صباحي", "مسائي", "كامل", "جزئي", "جزئى", "حضوري", "عاجل", "فورا", "بعد",
        "تركيا", "سوريا", "اسطنبول", "لشركه", "بشركه", "شركه", "شركتنا", "متخصصه", "المتخصصه", "رائده", "ناشئه",
        "للاعلان", "تعيين", "انضمام", "مجال", "بمشاريع", "فعليه", "مثبته", "قانونيه", "اوراق",
        "براتب", "ليره", "تركيه", "كيملك", "اقامه", "سياحيه", "لاحد", "احد", "بدون", "حضور", "ولباقه",
        "بحضور", "بمستوى", "جيد", "جدا", "المجال", "والعمل", "ذات", "سابقه",
    }
    raw_words = re.findall(r"[ء-يa-z0-9]+", s)
    out: list[str] = []
    for w in raw_words:
        if len(w) < 2 or w in boiler or w.isdigit():
            continue
        mapped = _ROLE_STEM.get(w, w)
        if mapped not in boiler and mapped not in out:
            out.append(mapped)
    return out


_ENTITY_NOUNS = frozenset({
    "صيدليه", "بصيدليه", "مستشفى", "مشفى", "عياده", "مركز", "مكتب", "منظمه", "جمعيه",
    "مؤسسه", "مطعم", "كافيه", "محل", "معمل", "مصنع", "مدرسه", "معهد", "روضة", "شبكه", "شركه",
})
_UMBRELLA_ROLE_TOKENS = frozenset({
    "موظف", "عامل", "مهندس", "مدير", "مسؤول", "منسق", "مشرف", "مستشار", "مختص", "متخصص",
    "خبير", "مندوب", "معلم", "مدرس", "دكتور", "طبيب", "صيدلاني", "استشاري", "عضو", "مساعد", "فني",
    "manager", "officer", "coordinator", "advisor", "assistant", "specialist", "engineer", "director", "staff", "intern",
})
_BLURB_TITLE_RE = re.compile(
    r"^\s*(?:تعلن|يعلن|نعلن|فرص[ةه]\s+للتعاون|نحن\s+\S)|\.\.\.\s*$|…\s*$",
    re.I,
)
_UMBRELLA_TITLE_PHRASE_RE = re.compile(
    r"^(?:مطلوب\s+)?(?:موظف(?:ين|ات|ة)?|كادر|فريق|عمال|شباب|بنات|صبايا|مساعدين)\s+(?:للعمل\s+)?(?:في|لدى|بـ?)\s+\S+|"
    r"(?:تعلن|يعلن|نعلن)\s+.{2,60}(?:عن\s+توفر|عن\s+حاجتها|عن\s+وظائف\s+شاغر[ةه]|للانضمام\s+إلى\s+فريق|رغبتها\s+في)|"
    r"(?:^|\s)(?:تعلن|يعلن|نعلن)\s+عن\s+(?:وظائف|شواغر)\s+شاغر[ةه]?",
    re.I,
)
_MULTI_ROLE_RAW_RE = re.compile(r"^\s*(?:🔹|🔸|▪|▫|[1-9]\s*[-.)]|o\s+)\s*\S+", re.M)


def _looks_multi_role_raw(raw: str | None) -> bool:
    return bool(raw and len(_MULTI_ROLE_RAW_RE.findall(raw)) >= 2)


def _is_prose_blurb_title(title: str | None) -> bool:
    t = (title or "").strip()
    return bool(t and (_BLURB_TITLE_RE.search(t) or len(t) > 80))


def _is_umbrella_title(title: str | None) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    return bool(_is_prose_blurb_title(t) or _UMBRELLA_TITLE_PHRASE_RE.search(t))


def _raw_headline(raw: str | None) -> str:
    if not raw:
        return ""
    for ln in raw.splitlines()[:6]:
        s = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿■-◿]", " ", ln)
        s = re.sub(r"^[\s•\-–*▪♦✓✔🔸🔹📌📣📢#]+", "", s).strip()
        if len(s) < 10 or len(s) > 140:
            continue
        if re.search(r"https?://|@|\b0[59]\d{7,}", s, re.I):
            continue
        if re.match(r"^(?:فرص[ةه]\s*عمل|إعلان\s*توظيف|اعلان\s*شاغر|التفاصيل)\s*[:：\-]*$", s, re.I):
            continue
        if re.match(r"^[\w\s]+:$", s):
            continue
        return s
    return ""


def _title_pair_matches(a: str, b: str, raw_a: str = "", raw_b: str = "") -> bool:
    tk1, tk2 = _title_key(a), _title_key(b)
    if tk1 and tk1 == tk2:
        return True
    ctk1, ctk2 = _specific_core_key(a), _specific_core_key(b)
    if ctk1 and ctk1 == ctk2:
        return True
    s1 = set(_role_tokens_ordered(a))
    s2 = set(_role_tokens_ordered(b))
    if not s1 or not s2:
        return False
    # Mutually exclusive tokens (e.g. 'junior' vs 'technical', 'الطلياني' vs 'اليرموك'): never merge
    if not (s1 <= s2 or s2 <= s1):
        return False
    smaller, larger = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    smaller_raw = raw_a if smaller is s1 else raw_b
    non_entity_smaller = [w for w in smaller if w not in _ENTITY_NOUNS]
    if len(non_entity_smaller) >= 2:
        return True
    if len(non_entity_smaller) == 1 and non_entity_smaller[0] not in _UMBRELLA_ROLE_TOKENS:
        if smaller == larger:
            return True
        extra = larger - smaller
        # Entity + non-umbrella role match (e.g. 'كاشير مطعم' vs 'كاشير مطعم الفاتح') if smaller_raw has no conflicting location, or extra tokens appear in smaller_raw
        smaller_norm = _norm(smaller_raw)
        if extra and smaller_norm and any(t in smaller_norm for t in extra):
            return True
        if len(smaller) >= 2 and "+" not in a and "+" not in b:
            return True
    return False


def _same_contact_posting(
    old_title: str,
    old_raw: str,
    new_title: str,
    new_raw: str,
    old_post_url: str = "",
    new_post_url: str = "",
) -> bool:
    """Confirm two ads sharing a phone/email are the same cross-source posting, not multi-role/syndicate siblings."""
    if old_post_url and new_post_url and old_post_url == new_post_url:
        if (
            "t.me/" in old_post_url
            or "moad.gov.sy" in old_post_url
            or old_post_url.rstrip("/").endswith(("/jobs", "/vacancies", "/careers"))
        ):
            return _title_key(old_title) == _title_key(new_title)
        return True
    if _title_pair_matches(old_title, new_title, old_raw, new_raw):
        return True
    u1, u2 = _is_umbrella_title(old_title), _is_umbrella_title(new_title)
    h1 = _raw_headline(old_raw) if (u1 and not _looks_multi_role_raw(old_raw)) else ""
    h2 = _raw_headline(new_raw) if (u2 and not _looks_multi_role_raw(new_raw)) else ""
    if (h1 or h2) and _title_pair_matches(h1 or old_title, h2 or new_title, old_raw, new_raw):
        return True
    if (u1 or u2) and old_raw and new_raw:
        s1 = set(_role_tokens_ordered(old_title))
        s2 = set(_role_tokens_ordered(new_title))
        w1 = {w for w in re.findall(r"[ء-يa-z0-9]+", soft_fold(old_raw).lower()) if len(w) >= 2}
        w2 = {w for w in re.findall(r"[ء-يa-z0-9]+", soft_fold(new_raw).lower()) if len(w) >= 2}
        low = min(len(w1), len(w2))
        if low >= 12:
            overlap = len(w1 & w2) / low
            if (not s1 or not s2) and (_looks_multi_role_raw(old_raw) or _looks_multi_role_raw(new_raw)):
                return False
            if (not s1 or not s2 or s1 <= s2 or s2 <= s1) and overlap >= 0.80:
                return True
            if u1 != u2 and overlap >= 0.85:
                return True
    return False


def dedupe_keys(job: Job) -> list[str]:
    """Extra exact-match & specific-core keys so mirrors merge cleanly across channels."""
    tk = _title_key(job.title)
    if not tk:
        return []
    ctk = _specific_core_key(job.title)
    month = (job.posted_at or "")[:7] or date.today().isoformat()[:7]
    keys = []
    if u := _norm_url(job.apply_url):
        keys.append(f"url:{tk}|{u}")
        if ctk and ctk != tk:
            keys.append(f"urc:{ctk}|{u}")
    for raw_p in (job.phones or [])[:2]:
        np = _norm_phone(raw_p, job.country)
        if np:
            keys.append(f"ph:{tk}|{np}|{month}")
            keys.append(f"cp:{job.country}:{np}|{month}")
            if ctk and ctk != tk:
                keys.append(f"phc:{ctk}|{np}|{month}")
    if job.email:
        em = job.email.strip().lower()
        keys.append(f"em:{tk}|{em}|{month}")
        keys.append(f"ce:{job.country}:{em}|{month}")
        if ctk and ctk != tk:
            keys.append(f"emc:{ctk}|{em}|{month}")
    if job.deadline and job.cities:
        keys.append(f"dl:{tk}|{_norm(job.cities[0])}|{job.deadline}")
    if ctk:
        keys.append(f"sp:{job.country}:{ctk}|{month}")
    return list(dict.fromkeys(keys))


def _dedupe_lookup_keys(job: Job, include_contact: bool = False) -> tuple[list[str], list[str]]:
    """Expand month-bucketed keys across adjacent months for lookup without polluting stored keys."""
    tk = _title_key(job.title)
    if not tk:
        return ([], []) if include_contact else []
    ctk = _specific_core_key(job.title)
    months = _adjacent_months(job.posted_at)
    keys = []
    contact_keys = []
    if u := _norm_url(job.apply_url):
        keys.append(f"url:{tk}|{u}")
        if ctk and ctk != tk:
            keys.append(f"urc:{ctk}|{u}")
    for raw_p in (job.phones or [])[:2]:
        np = _norm_phone(raw_p, job.country)
        if np:
            for m in months:
                keys.append(f"ph:{tk}|{np}|{m}")
                contact_keys.append(f"cp:{job.country}:{np}|{m}")
                if ctk and ctk != tk:
                    keys.append(f"phc:{ctk}|{np}|{m}")
    if job.email:
        em = job.email.strip().lower()
        for m in months:
            keys.append(f"em:{tk}|{em}|{m}")
            contact_keys.append(f"ce:{job.country}:{em}|{m}")
            if ctk and ctk != tk:
                keys.append(f"emc:{ctk}|{em}|{m}")
    if job.deadline and job.cities:
        keys.append(f"dl:{tk}|{_norm(job.cities[0])}|{job.deadline}")
    if ctk:
        for m in months:
            keys.append(f"sp:{job.country}:{ctk}|{m}")
    k_list = list(dict.fromkeys(keys))
    if include_contact:
        return k_list, list(dict.fromkeys(contact_keys))
    return k_list


_PORTAL_EMP_RE = re.compile(
    r"^(?:جهة\s*توظيف|منظمة\s*أممية\s*/\s*دولية|فرص\s*عمل|شواغر|وظائف|توظيف)(?:\s+\S+){0,2}\s+عبر\s+.+$|"
    r"(?:job\s*in\s*syria|worklink|ngos?\s*syria|ngosquare|reliefweb|unjobs|unjobnet|impactpool|syria-?jobs\.com|"
    r"shamhr|forsa-?sy|salam\s*jobs|jobsintr|adwhit|ab-ilan|sqrjobs|nsjobs|jobsyria|wazifturk|وظيفتورك)",
    re.I,
)
_PLACEHOLDER_EMP = {
    "غير معلن", "اعلانات", "إعلانات", "شواغر سوريا", "",
    "منظمات دولية ووكالات أممية (reliefweb)",
    "منصة ورك لينك (worklink syria)",
    "منصة ساحة المنظمات (ngo square)",
    "منصة فرص عمل في سوريا",
    "موقع فرص عمل في تركيا",
    "موقع وظائف سوريا (syria jobs)",
    "منصة سلام جوبز",
    "منصة وظيفتورك",
}
_LOCATION_AS_EMP_RE = re.compile(
    r"^(?:منطقة|منطقه|حي|شارع|ساحة|ساحه|كراج|خلف|قرب|مقابل|بجانب|بجوار|طريق|أوتوستراد|اوتستراد|دوار|جسر|مفترق|طلعة|نزلة|مشروع\s+دمر)\b|"
    r"^(?:السليمانية|الجميلية|المزة|المزرعة|الحميدية|القصاع|الزاهرة|الميدان|جرمانا|صحنايا|دوما|باب شرقي|برزة|ركن الدين|كفرسوسة|المالكي|أبو رمانة|الشعلان|البرامكة|المهاجرين|الأشرفية|الموكامبو|الفرقان|الحمدانية|سيف الدولة|صلاح الدين|الأعظمية|ميسلون|السريان|العزيزية|الإذاعة|الكلاسة|أشرفية صحنايا|جمعية الزهراء|الشيخ نجار|حلب الجديدة"
    r"|الشعار|الشهباء|الصاخور|الحيدرية|الشيخ مقصود|بستان القصر|المشارقة|السكري|باب النيرب|باب الفرج|الإسماعيلية|السبيل"
    r"|الجابرية|الكسوة|قدسيا|ضاحية قدسيا|مساكن برزة|الدويلعة|باب توما|القيمرية|ساروجة|الصالحية|المرجة|الحريقة|البحصة"
    r"|الشاغور|القدم|نهر عيشة|الزبلطاني|الطلياني|الحلبوني|الروضة|المالكي|الزاهرة الجديدة|التضامن|الدحاديل)$|"
    # pay, shift and hiring words that the title splitter sometimes leaves where the company name should be
    r"^(?:أجر|اجر|الأجر|الاجر|راتب|براتب|الراتب|بالقطعة|بالقطعه|دوام|الدوام|بخبرة|بخبره|خبرة|خبره|مطلوب|مطلوبة|فوري|فورا|فوراً|عاجل"
    r"|يومي|يومية|أسبوعي|اسبوعي|شهري|ذكور|إناث|اناث|للجنسين|شباب|صبايا|سكن|مع سكن|تأمين|توصيل|مواصلات)\b",
    re.I,
)
_SUMMARY_SOURCE_TAIL_RE = re.compile(
    r"(?:\s*[·•\-|]\s*|\s+)(?:💎+\s*)?المصدر\s*[:：]\s*[^·\n]+|"
    r"(?:\s*[·•\-|]\s*|\s+)(?:للمزيد من فرص العمل|وبإمكانكم متابعة قناتنا|تابعوا قنواتنا|قناة التلغرام|قناة الواتساب|تابعونا على تلغرام)[^·\n]*|"
    r"(?:\s*[·•\-|]\s*|\s+)تفاصيل المهام في ملف الوصف الوظيفي\s*[:：]?\s*\S*|"
    r"\s*لدى\s+(?:جهة\s*توظيف|منظمة\s*أممية(?:\s*/\s*دولية)?)\s+عبر\s+[^\s·,،.]+[^·\n]*|"
    r"\s*عبر\s+(?:منصة|موقع|قناة|بوابة)\s+(?:فريلانس\s*سوريا|ورك\s*لينك|ساحة\s*المنظمات|سلام\s*جوبز|"
    r"وظائف\s*سوريا|فرص\s*عمل(?:\s+في\s+(?:تركيا|سوريا))?(?:\s+الرسمية)?|ngo(?:s)?\s*syria|job\s*in\s*syria|"
    r"worklink|salam\s*jobs|jobsintr|reliefweb|unjobnet|syria-?jobs)[^·\n]*|"
    r"https?://\S+|(?<!\S)#[^\s]+",
    re.I,
)
_UI_KEYCAP_RE = re.compile(r"[0-9#*]\ufe0f?\u20e3")
_UI_JUNK_RE = re.compile(r"[\U0001F000-\U0001FAFF\u231A-\u23FA\u25AA-\u25FE\u2600-\u27BF\u2B00-\u2BFF\uFE00-\uFE0F\u200B-\u200F\u202A-\u202E\uFFFD]")
_PORTAL_TAGS = frozenset({"Job In Syria", "ورك لينك", "NGOs Syria"})


def _strip_ui_junk(text: str | None) -> str:
    """Remove emojis, keycap digits, variation selectors, zero-width marks, and replacement chars cleanly."""
    if not text:
        return ""
    s = str(text).replace("<PRIVATE_PERSON>", "")
    s = _UI_KEYCAP_RE.sub(" ", s)
    s = _UI_JUNK_RE.sub(" ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def _clean_employer_val(emp: str | None, official: bool = False) -> str:
    raw = _strip_ui_junk(emp)
    raw = re.sub(r"\s+(?:عبر|عن)$", "", raw).strip(" -–:،,")
    raw_stripped = re.sub(
        r"\s+(?:في|ضمن)\s+(?:محافظة\s+)?(?:دمشق|ريف\s+دمشق|حلب|حمص|حماة|اللاذقية|طرطوس|إدلب|درعا|السويداء|القنيطرة|دير\s*الزور|الحسكة|الرقة)\s*$",
        "",
        raw,
    ).strip(" -–:،,")
    if (
        raw_stripped != raw
        and len(raw_stripped) >= 8
        and not re.match(r"^(?:شركة|مؤسسة|مكتب|مركز|معمل|مطعم|مصنع|صاحب عمل|جهة|فرع)$", raw_stripped)
    ):
        raw = raw_stripped
    if not raw or raw.lower() in _PLACEHOLDER_EMP or _PORTAL_EMP_RE.search(raw) or _LOCATION_AS_EMP_RE.search(raw):
        return "جهة حكومية رسمية" if official else ""
    return raw


def _clean_summary_text(text: str | None) -> str:
    if not text:
        return ""
    s = _strip_ui_junk(text)
    s = _SUMMARY_SOURCE_TAIL_RE.sub("", s)
    s = re.sub(r"(?:\s*[:：]?\s*\bht{1,2}p?s?\b|\s*:\s*)$", "", s, flags=re.I)
    s = re.sub(r"^(?:\s*[·•\-:]\s*)+|(?:\s*[·•\-:]\s*)+$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _strip_wa_query(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if m := re.match(r"^https?://wa\.me/(\d{7,15})\b", u, flags=re.I):
        return f"https://wa.me/{m.group(1)}"
    return u


_GENERIC_EMP = frozenset({
    "", "غير معلن", "شركه خاصه", "شركة خاصة", "فرص عمل في تركيا الرسميه", "فرص عمل في تركيا",
    "منظمات ومؤسسات في تركيا", "شركات ومصانع غازي عنتاب", "منظمه دوليه / وكاله امميه",
    "منظمة دولية / وكالة أممية", "موقع فرص عمل في تركيا", "منصه سلام جوبز", "موقع ادويت",
})


def _compatible_duplicate(old: dict, job: Job) -> bool:
    """Prevent false merges between distinct employers or provinces when matching on sp: keys."""
    if _norm_url(old.get("apply_url")) and _norm_url(old.get("apply_url")) == _norm_url(job.apply_url):
        return True
    old_ph = {_norm_phone(p, job.country) for p in (old.get("phones") or []) if _norm_phone(p, job.country)}
    new_ph = {_norm_phone(p, job.country) for p in (job.phones or []) if _norm_phone(p, job.country)}
    if old_ph and new_ph and old_ph.isdisjoint(new_ph):
        return False
    old_em = (old.get("email") or "").strip().lower()
    new_em = (job.email or "").strip().lower()
    if old_em and new_em and old_em != new_em:
        return False
    if (old_ph and old_ph == new_ph) or (old_em and old_em == new_em):
        return True
    generic_cities = {"", "غير محدد", "عدة محافظات", "عدة ولايات في تركيا"}
    old_c = {c for c in (old.get("cities") or []) if c not in generic_cities}
    new_c = {c for c in (job.cities or []) if c not in generic_cities}
    if old_c and new_c and old_c.isdisjoint(new_c):
        return False
    old_e = _norm(_clean_employer_val(old.get("employer"), bool(old.get("official"))))
    new_e = _norm(_clean_employer_val(job.employer, job.official))
    gen_emp_norm = {_norm(x) for x in _GENERIC_EMP}
    if old_e and new_e and old_e not in gen_emp_norm and new_e not in gen_emp_norm:
        if old_e not in new_e and new_e not in old_e:
            return False
    return True


def expires_on(job: Job) -> str:
    start = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00")).date() if job.posted_at else date.today()
    max_day = (start + timedelta(days=config.HISTORY_DAYS)).isoformat()
    if job.deadline:
        return min(job.deadline, max_day)
    return min((start + timedelta(days=config.QUICK_DAYS if job.kind == "quick" else config.FORMAL_DAYS)).isoformat(), max_day)


def _apply_str(job: Job) -> str:
    return "|" + "|".join(job.apply_types) + "|" if job.apply_types else ""


_SINGLE_POST_URL_SOURCES = frozenset({
    "jobsintr_web", "adwhit_web", "salamjobs", "wazifturk",
    "unjobnet_sy", "unjobnet_tr", "impactpool_sy", "impactpool_tr",
    "shafak", "drc", "acted", "wfp",
    "syria_jobs", "ssf_gov", "jobinsyria", "worklink", "reliefweb",
})


def upsert(con: sqlite3.Connection, job: Job, status: str | None = None) -> tuple[int, bool]:
    """Store a job, or attach its source to the row it duplicates. Returns (id, is_new)."""
    from . import direct_apply as da
    from . import taxonomy as tx

    if job.apply_url:
        cleaned_u = _strip_wa_query(da.strip_tracking(job.apply_url))
        job.apply_url = None if (not cleaned_u or da.is_disallowed_offline_url(cleaned_u)) else cleaned_u
    job.employer = _clean_employer_val(job.employer, job.official) or None
    if job.tags:
        job.tags = [t for t in job.tags if t not in _PORTAL_TAGS]
    if not job.apply_types:
        job.apply_types = tx.infer_apply_types(job.apply_url, job.phones, job.whatsapp, job.email)
    if not job.sector or job.sector == "private":
        job.sector = tx.infer_sector(job.official, job.field, job.employer, job.raw, hint=job.sector if job.sector in ("gov", "ngo") else None)
    job.country = "tr" if job.country == "tr" else "sy"
    if job.raw:
        job.raw = job.raw.replace("\ufffd", "").replace("<PRIVATE_PERSON>", "")
    if "\ufffd" in (job.title or "") or not re.search(r"[\u0621-\u064Aa-zA-Z]{3,}", job.title or ""):
        status = "rejected"

    fp = fingerprint(job)
    keys = dedupe_keys(job)
    lookup_keys, contact_keys = _dedupe_lookup_keys(job, include_contact=True)
    row = con.execute("SELECT id, data, status, expires_on, deadline FROM jobs WHERE fp=?", (fp,)).fetchone()
    if not row and job.post_url and job.source in _SINGLE_POST_URL_SOURCES:
        row = con.execute(
            "SELECT j.id, j.data, j.status, j.expires_on, j.deadline "
            "FROM job_sources s JOIN jobs j ON j.id=s.job_id WHERE s.post_url=? AND s.source=? AND j.country=? LIMIT 1",
            (job.post_url, job.source, job.country),
        ).fetchone()
    if not row and lookup_keys:
        q = f"SELECT j.id, j.data, j.status, j.expires_on, j.deadline FROM job_keys k JOIN jobs j ON j.id=k.job_id WHERE k.k IN ({','.join('?' * len(lookup_keys))}) AND j.country=?"
        for cand in con.execute(q, lookup_keys + [job.country]):
            old_cand = json.loads(cand["data"])
            if _compatible_duplicate(old_cand, job):
                row = cand
                break
    if not row and contact_keys:
        q = f"SELECT j.id, j.data, j.status, j.expires_on, j.deadline FROM job_keys k JOIN jobs j ON j.id=k.job_id WHERE k.k IN ({','.join('?' * len(contact_keys))}) AND j.country=?"
        for cand in con.execute(q, contact_keys + [job.country]):
            old_cand = json.loads(cand["data"])
            if _compatible_duplicate(old_cand, job) and _same_contact_posting(
                old_cand.get("title") or "",
                old_cand.get("raw") or "",
                job.title,
                job.raw or "",
                old_post_url=old_cand.get("post_url") or "",
                new_post_url=job.post_url or "",
            ):
                row = cand
                break
    t = now()
    if row:
        jid, new = row["id"], False
        old = json.loads(row["data"])
        jd = job.to_dict()
        if old.get("apply_url") and da.is_disallowed_offline_url(old["apply_url"]):
            old["apply_url"] = None
        old["apply_url"] = _strip_wa_query(old.get("apply_url"))
        old["employer"] = _clean_employer_val(old.get("employer"), bool(old.get("official"))) or None
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
        # Avoid replacing a crisp role headline with a truncated prose blurb ("تعلن عيادة...").
        old_t = (old.get("title") or "").strip()
        new_t = (jd.get("title") or "").strip()
        if _is_prose_blurb_title(merged.get("title")):
            crisp = [t for t in (old_t, new_t) if len(t) >= 8 and not _is_prose_blurb_title(t)]
            if crisp:
                merged["title"] = crisp[0]
        elif not merged.get("title"):
            merged["title"] = new_t or old_t
        # Prefer specific cities over generic country/multi-province placeholders
        generic_cities = {"غير محدد", "عدة محافظات", "عدة ولايات في تركيا"}
        old_cs = old.get("cities") or []
        new_cs = jd.get("cities") or []
        if new_cs and not any(c in generic_cities for c in new_cs) and (not old_cs or any(c in generic_cities for c in old_cs)):
            merged["cities"] = new_cs
        elif old_cs and not any(c in generic_cities for c in old_cs):
            merged["cities"] = old_cs
        merged["phones"] = sorted({
            _norm_phone(p, job.country)
            for p in (old.get("phones") or []) + (jd.get("phones") or [])
            if _norm_phone(p, job.country)
        })
        if jd.get("apply_types") or old.get("apply_types"):
            merged["apply_types"] = sorted(set(old.get("apply_types") or []) | set(jd.get("apply_types") or []))
        if jd.get("tags") or old.get("tags"):
            merged["tags"] = sorted({t for t in (old.get("tags") or []) + (jd.get("tags") or []) if t not in _PORTAL_TAGS})
        # Prefer direct ATS/form apply URL when either version had one
        if jd.get("apply_url") and da.is_direct_form_or_ats(jd["apply_url"]) and not (merged.get("apply_url") and da.is_direct_form_or_ats(merged["apply_url"])):
            merged["apply_url"] = jd["apply_url"]
        if merged.get("apply_url") and da.is_disallowed_offline_url(merged["apply_url"]):
            merged["apply_url"] = None
        if merged.get("apply_url"):
            merged["apply_url"] = _strip_wa_query(merged["apply_url"])
        merged["employer"] = _clean_employer_val(merged.get("employer"), bool(merged.get("official"))) or None
        if merged.get("summary"):
            merged["summary"] = _clean_summary_text(merged["summary"]) or None
        merged["apply_types"] = tx.infer_apply_types(
            merged.get("apply_url"), merged.get("phones") or [], bool(merged.get("whatsapp")), merged.get("email")
        )
        merged["country"] = job.country
        today_str = date.today().isoformat()
        old_expired_dl = bool(row["deadline"] and row["deadline"] < today_str)
        has_valid_new_dl = bool(jd.get("deadline") and jd["deadline"] >= today_str and (not old_expired_dl or job.official))
        eff_dl = jd.get("deadline") if has_valid_new_dl else (row["deadline"] or jd.get("deadline"))
        if row["status"] == "expired" and not has_valid_new_dl:
            new_exp = row["expires_on"] or expires_on(job)
            new_status = "expired"
        else:
            new_exp = max(row["expires_on"] or "", expires_on(job))
            if eff_dl:
                new_exp = min(new_exp, eff_dl)
            still_open = new_exp >= today_str and not (eff_dl and eff_dl < today_str)
            has_merged_contact = bool(merged.get("apply_url") or merged.get("phones") or merged.get("email"))
            new_status = status if status is not None else (
                "live" if row["status"] == "expired" and still_open else (
                    ("review" if job.confidence < config.REVIEW_BELOW else "live")
                    if row["status"] == "rejected" and still_open and has_merged_contact and not job.spam else (
                        "expired" if row["status"] == "live" and not still_open else row["status"]
                    )
                )
            )
        merged_cities_str = "|" + "|".join(merged.get("cities") or []) + "|" if merged.get("cities") else ""
        con.execute(
            "UPDATE jobs SET title=?, cities=?, data=?, country=?, sector=?, apply_types=?, deadline=?, expires_on=?, status=?, updated_at=? WHERE id=?",
            (merged.get("title", job.title), merged_cities_str,
             json.dumps(merged, ensure_ascii=False),
             job.country,
             merged.get("sector", job.sector),
             "|" + "|".join(merged.get("apply_types") or []) + "|" if merged.get("apply_types") else "",
             eff_dl, new_exp, new_status, t, jid),
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
    cur = con.execute(
        "UPDATE jobs SET status='expired' WHERE status='live' AND (expires_on < ? OR (deadline IS NOT NULL AND deadline != '' AND deadline < ?))",
        (today, today),
    )
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


# A Telegram channel only returns posts newer than the last one we read, so a quiet week is normal there.
# Web portals and ATS boards return every open job on each run, so one empty day is already suspicious.
STALE_DAYS = {"telegram": 7}
STALE_DAYS_DEFAULT = 2


def record_health(con: sqlite3.Connection, source: str, kind: str, count: int, error: str | None = None) -> None:
    ts = now()
    con.execute(
        "INSERT INTO source_health (source, kind, last_run, last_count, last_items_at, last_error, fail_streak) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET kind=excluded.kind, last_run=excluded.last_run, "
        "last_count=excluded.last_count, last_items_at=COALESCE(excluded.last_items_at, source_health.last_items_at), "
        "last_error=excluded.last_error, fail_streak=CASE WHEN excluded.last_error IS NULL THEN 0 ELSE source_health.fail_streak + 1 END",
        # First sighting counts as the start of the quiet clock, so a source is only called stale after a full
        # STALE_DAYS of tracking (not on day one just because nothing was new since the snapshot).
        (source, kind, ts, count, ts if (count > 0 or not _health_known(con, source)) else None,
         (error or None) and str(error)[:300], 1 if error else 0),
    )


def _health_known(con: sqlite3.Connection, source: str) -> bool:
    return con.execute("SELECT 1 FROM source_health WHERE source=?", (source,)).fetchone() is not None


def record_batch_health(con: sqlite3.Connection, kind: str, jobs: list, error: str | None = None) -> None:
    """Per-source counts for one group of scrapers. A source seen before in this group that brought nothing now is
    recorded with 0, so a scraper that broke quietly (site redesign, block) shows up as stale."""
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.source] = counts.get(j.source, 0) + 1
    known = {r["source"] for r in con.execute("SELECT source FROM source_health WHERE kind=?", (kind,))}
    for src in known | set(counts):
        record_health(con, src, kind, counts.get(src, 0), error)


def health_report(con: sqlite3.Connection) -> list[dict]:
    """Every source with a status: fail (errors 3 runs in a row), stale (nothing for too long), ok."""
    now_dt = datetime.now(timezone.utc)
    out = []
    for r in con.execute("SELECT * FROM source_health ORDER BY source"):
        days = STALE_DAYS.get(r["kind"], STALE_DAYS_DEFAULT)
        last = r["last_items_at"]
        quiet_days = None
        if last:
            quiet_days = (now_dt - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 86400
        if r["fail_streak"] >= 3:
            status = "fail"
        elif quiet_days is None or quiet_days >= days:
            status = "stale"
        else:
            status = "ok"
        out.append({
            "source": r["source"], "kind": r["kind"],
            "label": (config.CHANNELS.get(r["source"]) or {}).get("label") or config.SOURCE_LABEL.get(r["source"], r["source"]),
            "status": status, "last_run": r["last_run"], "last_count": r["last_count"],
            "last_items_at": last, "quiet_days": round(quiet_days, 1) if quiet_days is not None else None,
            "last_error": r["last_error"], "fail_streak": r["fail_streak"],
        })
    order = {"fail": 0, "stale": 1, "ok": 2}
    return sorted(out, key=lambda x: (order[x["status"]], x["source"]))


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
    r"^(?:سلام عليكم|السلام عليكم|وعليكم السلام|حياكم الله|تحية طيبة|التفاصيل|قبل أن تتقدّم|قبل ان تتقدم|"
    r"(?:التقديم|للتقديم|للتواصل والتقديم|تقدم الآن|تقدّم الآن|طريقة التقديم(?:\s+والاستفسار)?|للتقديم والاستفسار(?:\s*[،,]\s*يرجى\s+التواصل)?|للتواصل والاستفسار|للاطلاع على التفاصيل|للاطلاع على التفاصيل يمكن زيارة الرابط التالي|للمزيد من التفاصيل|رابط التقديم)\s*:?\s*$|"
    r"(?:يتم\s+)?التواصل\s+عبر\s+(?:واتساب|الرقم).*?(?:التالي)?\s*:?\s*$|"
    r"للتواصل والتقديم عبر الرقم المرفق|"
    r"وظيفة جديدة|الوصف المستخرج|🔗|🏢\s*الجهة|📍\s*الموقع|📅\s*آخر موعد|الفئة الوظيفية|محافظة العمل|"
    r"(?:المسم[ىي]\s*الوظيف[يى]|المسميات\s*الوظيفية|الشواغر\s*(?:الوظيفية|المطلوبة(?:\s*والشروط)?)|"
    r"الشروط\s*(?:المطلوبة|والمؤهلات|اللازمة)?|المتطلبات(?:\s*والشروط)?|المهام(?:\s*والمسؤوليات(?:\s*الرئيسية)?)?|"
    r"أوقات\s*(?:العمل|الدوام)|ساعات\s*(?:العمل|الدوام)|مواعيد\s*(?:العمل|الدوام)|"
    r"المزايا|المميزات|طبيعة\s+العمل|طبيعة\s+الدوام|شروط\s+ومعلومات\s+العمل|معلومات\s+التواصل(?:\s+والموقع)?|لاعطاء\s+المواد(?:\s+التالية)?)\s*:?\s*$|"
    r"(?:محافظة|مدينة)\s+(?:دمشق|ريف\s+دمشق|حلب|حمص|حماة|طرطوس|اللاذقية|إدلب|درعا|السويداء|القنيطرة|دير\s*الزور|الحسكة|الرقة)\s*$|"
    r"نصيحة\s+\S+|تم\s+نشر\s+الإعلان|لا\s+تتحمل\s+(?:منصة|قناة)|إذا\s+كنت\s+تبحث\s+عن\s+فرصة|^عند التواصل(?:،|\s+للتقدم)|"
    r"تواصل معنا.*لنشر فرص العمل|للعمل ضمن كوادره،?\s*وفق الشروط التالية|"
    r"(?:https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}/\S*$|#|\*?\s*(?:للتواصل|الاتصال|مطلوب)\s*:?\s*$)|"
    r"للمزيد من فرص العمل|وبإمكانكم متابعة قناتنا|تابعوا قنواتنا|قناة التلغرام|قناة الواتساب|تابعونا على تلغرام|"
    r"تفاصيل المهام في ملف الوصف الوظيفي|"
    r"لنشر ما لديكم|راسلونا دايركت|للتواصل ولمعلومات|الاتصال على الرقم|"
    r"(?:المصدر|عبر\s+(?:Job\s*In\s*Syria|WorkLink|NGOs?\s*Syria|salam\s*jobs|jobsintr))\s*[:：]?|"
    r"(?:^|[\s(])@[A-Za-z0-9_]{3,}(?!\.[A-Za-z0-9-])",
    re.I,
)
_BOILERPLATE_PREFIX_RE = re.compile(
    r"^(?:فرص[ةه]?\s+عمل(?:\s+في\s+مجال|\s+إداري[ةه]|\s+جديد[ةه])?[^|\n]*[|·–\-]?\s*|"
    r"إعلان\s+(?:توظيف|شاغر)[^|\n—–\-]*[|—–\-]?\s*|"
    r"دعوة\s+للتعاقد[^:\n]*[:：]?\s*|"
    r"فرص\s+وظيفية\s+جديدة\s+في\s+[^\n·]+|"
    r"(?:تُ?عل[ن]?|يُ?عل[ن]?|نُ?عل[ن]?)\s+[^\n.،]{3,85}?\s+(?:عن\s+(?:حاجتها|حاجته|رغبتها|توفر|توافر|فتح\s+باب\s+التقديم))\s*)",
    re.I,
)
_BOILERPLATE_SUM_RE = re.compile(
    r"(?:التقديم متاح مباشرة عبر الرابط المرفق|فرص[ةه]?\s+عمل\s+في\s+مجال|"
    r"للانضمام\s+[إا]ل[ىي]\s+فريق|عن\s+فتح\s+باب\s+التقديم\s+لاستقطاب|"
    r"عن\s+توفر\s+عدد\s+من\s+الشواغر|دعوة\s+للتعاقد\s+مع\s+مزود\s+خدمة\s+مستقل|"
    r"(?:تُ?علن|يُ?علن)\s+.{3,70}\s+عن\s+(?:حاجتها|رغبتها)|إعلان\s+توظيف\s*[—–\-])"
)
_DANGLING_TAIL_RE = re.compile(r"\s+(?:في|من|إلى|الى|عن|على|مع|لدى|عند|و|أو)\s*$", re.I)

_NUMBERED_LIST_ITEM_RE = re.compile(
    r"^(?:[1-9]\d?|[١-٩][٠-٩]?|1️⃣|2️⃣|3️⃣|4️⃣|5️⃣|6️⃣|7️⃣|8️⃣|9️⃣|🔟)\s*(?:[-.)–]|\s+(?=[ء-يA-Za-z]))|^(?:أولا|اولا|ثانيا|ثالثا|رابعا|خامسا)\s*[:\-–]"
)
_SPAM_BULLET_RE = re.compile(r"ادعمولنا|ادعمونا|عبتستفيد|شاركوا البوست|لايك|شير", re.I)
_APPLY_LEAK_RE = re.compile(r"للتقديم والاستفسار|يرجى إرسال السيرة الذاتية|أو عبر البريد الإلكتروني|^طريقة التقديم", re.I)


def _clean_bullets(block: str, limit: int = 5) -> list[str]:
    out: list[str] = []
    for ln in (block or "").splitlines():
        if _SPAM_BULLET_RE.search(ln):
            continue
        s = re.sub(r"^[\s•\-–*▪▫♦✓✔\d.)+]+", "", ln).strip()
        if 8 <= len(s) <= 220 and not s.startswith("http") and not _SKIP_SUMMARY_LINE.search(s):
            if s not in out:
                out.append(s)
        if len(out) >= limit:
            break
    return out


_EDU_AR = {
    "none": "بدون شهادة",
    "basic": "تعليم أساسي",
    "secondary": "ثانوية عامة",
    "institute": "معهد تقاني / متوسط",
    "bachelor": "إجازة جامعية",
    "master": "ماجستير",
    "phd": "دكتوراه",
}


def _summarize_skills(skills: list[str]) -> str:
    """Turn requirement/skill bullets into a concise card summary, preferring concrete specs over generic degree lines."""
    clean = [_clean_summary_text(str(s)) for s in (skills or []) if str(s).strip()]
    clean = [s for s in clean if len(s) >= 18 and len(s.split()) >= 3]
    if not clean:
        return ""
    substantive = [s for s in clean if not re.match(r"^(?:خريج|شهاد[ةه]|الحصول\s+على\s+شهاد[ةه]|إجاز[ةه]\s+في)\b", s)]
    chosen = substantive[:2] if substantive else clean[:2]
    return _clip_clause(" · ".join(chosen), 185)


def _fallback_summary(d: dict) -> str:
    emp = _clean_employer_val(d.get("employer"), bool(d.get("official")))
    default_country = "تركيا" if d.get("country") == "tr" else "سوريا"
    cs = "، ".join((d.get("cities") or [])[:3]) or default_country

    facts: list[str] = []
    if d.get("specialty"):
        spec = _strip_ui_junk(d["specialty"])
        spec = re.sub(r"\bالتقان(?=\s|$)", "التقاني", spec)
        facts.append(f"التخصص: {spec[:70]}")
    if d.get("grade") and d.get("education") and d["education"] in _EDU_AR:
        facts.append(f"المؤهل: {_EDU_AR[d['education']]} (الفئة {d['grade']})")
    if d.get("experience"):
        facts.append(f"الخبرة: {d['experience']} سنوات")
    if d.get("shift"):
        facts.append(f"الدوام: {d['shift']}")
    if d.get("salary"):
        facts.append(f"الراتب: {d['salary']}")
    if d.get("deadline"):
        facts.append(f"آخر موعد: {d['deadline']}")

    has_link = bool(d.get("apply_url") and not str(d["apply_url"]).lower().startswith("https://wa.me/"))
    has_email = bool(d.get("email"))
    phones = d.get("phones") or []

    if facts:
        prefix = f"فرصة عمل لدى {emp} ({cs})" if emp else f"فرصة عمل في {cs}"
        if has_email and len(facts) < 2:
            facts.append(f"للتقديم: {d['email']}")
        elif phones and not has_link and len(facts) < 2:
            facts.append(f"للتواصل: {phones[0]}")
        elif has_link and not d.get("tasks") and not d.get("skills") and len(facts) < 2:
            facts.append("المهام والشروط ضمن الرابط")
        return _clip_clause(f"{prefix} · " + " · ".join(facts), 185)

    if d.get("skills"):
        sk_sum = _summarize_skills(d["skills"])
        if sk_sum:
            return sk_sum

    if has_email and not has_link:
        return f"للتقديم: إرسال السيرة الذاتية إلى {d['email']} · اضغط على «المواصفات» للتفاصيل"
    if phones and not has_link and not has_email:
        return f"للتواصل المباشر والتقديم: {phones[0]} · اضغط على «المواصفات» للتفاصيل"
    if has_link:
        return "بطاقة الوصف والمهام الكاملة متوفرة في رابط التقديم الرسمي."
    return "اضغط على «المواصفات» للاطلاع على تفاصيل الشاغر وطريقة التقديم."


def _clip_clause(text: str, max_len: int = 185) -> str:
    """Shorten text strictly at clause/word boundaries without trailing dangling prepositions."""
    s = re.sub(r"\s{2,}", " ", (text or "")).strip(" ·•-–:،,.")
    if len(s) > max_len:
        parts = [p.strip() for p in s.split(" · ") if p.strip()]
        kept: list[str] = []
        for p in parts:
            cand = " · ".join(kept + [p])
            if len(cand) <= max_len or not kept:
                kept.append(p)
            else:
                break
        s = " · ".join(kept)
        if len(s) > max_len:
            cut = s[:max_len]
            if not s[max_len:max_len + 1].isspace() and " " in cut[30:]:
                cut = cut.rsplit(" ", 1)[0]
            s = cut.strip(" ·•-–:،,.")
    while _DANGLING_TAIL_RE.search(s):
        s = _DANGLING_TAIL_RE.sub("", s).strip(" ·•-–:،,.")
    return s


def _strip_intro_fluff(line: str, emp: str = "") -> str:
    """Extract the useful core from a promo/announcement sentence, or return '' if pure fluff."""
    s = _strip_ui_junk(re.sub(r"https?://\S+", "", line))
    s = re.sub(r"\s*[:：]\s*$", "", s).strip(" •-*–\t\r")
    if not s or _SKIP_SUMMARY_LINE.search(s):
        return ""
    if re.match(r"^(?:فرص[ةه]?\s+عمل|إعلان\s+(?:توظيف|شاغر)|فرص\s+وظيفية)\b", s):
        if "|" in s:
            parts = [p.strip() for p in s.split("|") if p.strip()]
            rest = [p for p in parts[1:] if (not emp or emp not in p) and not re.match(r"^(?:شركة|مؤسسة|منظمة|مطعم)\s+", p)]
            rest = [p for p in rest if len(p) >= 12 and not re.match(r"^(?:دمشق|حلب|حمص|حماة|طرطوس|اللاذقية|إدلب)\s*[–\-]", p)]
            return " · ".join(rest) if rest else ""
        return ""
    if re.match(r"^(?:تُ?عل[ن]?|يُ?عل[ن]?|نُ?عل[ن]?)\b", s):
        if m := re.search(
            r"(?:لتقديم\s+(خدمات[^.\n]+)|(?:بتشغيل|بصيانة|لبيع|في\s+مجال\s+[^\n.]+))",
            s,
        ):
            core = (m.group(1) or m.group(0)).strip(" .-–،,:")
            if core.startswith("خدمات "):
                core = "تقديم " + core
            core = re.sub(r"\s+للانضمام\s+[إا]ل[ىي]\s+فريق.*$", "", core).strip()
            return core if len(core) >= 18 else ""
        if re.search(r"(?:للانضمام\s+[إا]ل[ىي]\s+فريق|فتح\s+باب\s+التقديم|توفر\s+عدد\s+من\s+الشواغر|بالوظائف\s+الشاغرة\s+التالية|الشاغر\s+التالي|عن\s+حاجته[ا]?\s+(?:إلى|الى|ل|لتعيين))", s):
            return ""
    s = _BOILERPLATE_PREFIX_RE.sub("", s).strip(" ·•—-–:،,")
    s = re.sub(r"^(?:إل[ىي]|عن\s+حاجته[ا]?)\s+", "", s).strip(" ·•—-–:،,")
    s = re.sub(r"\s+للانضمام\s+[إا]ل[ىي]\s+فريق.*$", "", s).strip(" ·•—-–:،,")
    if s and _SKIP_SUMMARY_LINE.search(s):
        return ""
    return s


_COLON_EXCLUDE_RE = re.compile(
    r"^(?:الشروط.*|المتطلبات.*|المهام.*|معلومات\s+التواصل.*|طريقة\s+التقديم.*|طبيعة\s+(?:العمل|الدوام).*|"
    r"الموقع|العنوان|مكان\s+العمل|للتواصل.*|للتقديم.*|الشواغر\s+المطلوبة.*|ملاحظات.*)$",
    re.I,
)


def _repair_title_and_emp(d: dict) -> tuple[str, str]:
    """Repair noisy, generic, or clipped stored job titles and employers cleanly."""
    from . import extract as ex

    raw = "\n".join(_strip_ui_junk(ln) for ln in (d.get("raw") or "").splitlines()).strip()
    title = _strip_ui_junk(d.get("title") or "")
    title = re.sub(r"(?:\s+|^)(?:09|٠٩|\+963|00963|\+90|0090)[0-9٠-٩\s\-]{7,}", "", title)
    title = re.sub(r"\s*#\S+", "", title).strip(" -–:،,.|/")
    # Fix dropped PDF glyph final ي and cut titles containing housing/inquiry suffixes
    title = re.sub(r"\bلوجست(?=\s|$|\))", "لوجستي", title)
    title = re.sub(r"\b(?:إداري|مساعد|باحث|فني)\s+ثان(?=\s|\(|$)", lambda m: m.group(0) + "ي", title)
    title = re.sub(r"\s+\b(?:المنام[ةه]\s+مؤمن[ةه]|السكن\s+مؤم[ن]?|ل[إأا]?ستفسار|الاتصال\s+على)\b.*$", "", title).strip(" -–:،,.|/")
    title = ex.strip_person_role_prefix(title)
    emp = _clean_employer_val(d.get("employer"), bool(d.get("official")))
    if not emp and raw:
        emp = _clean_employer_val(ex._employer(raw), bool(d.get("official")))

    if m_dom := re.search(r"\s+مجال\s+العمل\s+(.+)$", title):
        base_t = title[:m_dom.start()].strip(" -–:،,.")
        dom = m_dom.group(1).strip()
        if raw and (m_raw_dom := re.search(r"مجال\s+العمل\s+([^\n]+)", raw)):
            dom = re.split(r"\s*\*?\s*للتواصل\b", m_raw_dom.group(1))[0].strip(" .،,-")
        if len(base_t) >= 8:
            title = base_t
            if not d.get("summary"):
                d["_extra_scope"] = f"مجال العمل: {dom}"

    title = ex.rewrite_freelance_title(title, raw)

    # Upgrade vague generic person titles («صبية للعمل في المعمل الرئيسي...») when raw gives explicit «المسمى الوظيفي: ...»
    if raw and re.match(r"^(?:صبي[ةه]|شاب|شباب|آنس[ةه]|بنت|مطلوب)\s+للعمل\b", title):
        if m_explicit := re.search(r"(?:المسم[ىي]|المنصب|الشاغر)\s*الوظيف[يى]\s*[:：]\s*([^\n]+)", raw):
            explicit_t = ex.normalize_title_display(m_explicit.group(1))
            if explicit_t and len(explicit_t) >= 4:
                title = explicit_t

    bad_title_re = re.compile(
        r"الشواغر\s+المطلوبة|التفاصيل\s+حول\s+الشواغر|تقديم\s+طلب\s+التوظيف|ضمن\s+الرابط\s+الآتي|"
        r"موظفين\s+للعمل\s+ضمن\s+الاختصاصات|إرسال\s+السيرة\s+الذاتية|^ارسال\s+السير|^مركز\s+تعليمي\s+في\s+ح[يى]|"
        r"^فنيين\s+لديهم$|^نمذجة$|^شاغر\s+وظيفي$|^إعلان\s+توظيف$|^فرصة\s+للعطاء\s+المعرفي$",
        re.I,
    )
    if raw and (bad_title_re.search(title) or _DANGLING_TAIL_RE.search(title)):
        if "وزارة " in raw and ("الرابط الآتي" in title or "الشواغر" in title):
            if m_min := re.search(r"لصالح\s+(وزارة\s+[^\n،,.(]{4,45})", raw):
                title = f"شواغر {m_min.group(1).strip()}"
            elif m_min2 := re.search(r"(وزارة\s+[^\n،,.(]{4,45})", raw):
                title = f"شواغر {m_min2.group(1).strip()}"
        elif "مركز تعليمي" in title and (m_r := re.search(r"عن\s+حاجت[ه]?\s+إل[ىي]\s+([^\n]{3,45})", raw)):
            cand_r = ex.normalize_title_display(m_r.group(1))
            if cand_r:
                title = cand_r
        elif title == "فنيين لديهم" and (m_fn := re.search(r"مطلوب\s+(فنيين\s+[^\n–\-]{4,45})", raw)):
            title = ex.normalize_title_display(m_fn.group(1))
        elif title == "فنيين لديهم" and re.search(r"محاسب\s+تكاليف", raw):
            title = "محاسب تكاليف (Cost Accountant)"
        elif title == "نمذجة" and (m_eng := re.search(r"مطلوب\s+(مهندسين\s+[^\n]{4,35})", raw)):
            cand = re.sub(r"\s+لصالح.*$", "", m_eng.group(1)).strip()
            title = f"{cand} (تدريس مواد هندسية)"
        elif "الشواغر المطلوبة" in title:
            roles = [
                m_c.group(1).strip()
                for ln in raw.splitlines()
                if (m_c := re.match(r"^\s*([ء-يA-Za-z /\-()]{3,28})\s*[:：]\s*$", ln))
                and not _COLON_EXCLUDE_RE.match(m_c.group(1).strip())
            ]
            if roles:
                title = " / ".join(roles[:4])
                d["_colon_roles"] = roles
        elif "ضمن الاختصاصات التالية" in title:
            after_hdr = re.split(r"الاختصاصات\s+التالية\s*[:：]?", raw, maxsplit=1)[-1]
            role_cands: list[str] = []
            for ln in after_hdr.splitlines():
                p = _strip_ui_junk(ln).strip(" .،,-")
                if not p or len(p) < 3 or len(p) > 35:
                    continue
                if re.match(r"^(?:يشترط|يفضل)\b", p):
                    continue
                if _COLON_EXCLUDE_RE.match(p.rstrip(":：")) or re.match(r"^(?:طبيعة\s+العمل|طبيعة\s+الدوام|شروط|طريقة\s+التقديم|للتواصل)\b", p):
                    break
                role_cands.append(p)
            if role_cands:
                title = " / ".join(role_cands[:4])
                d["_colon_roles"] = role_cands
        elif "إرسال السيرة" in title or "ارسال السير" in title:
            re_jobs = ex.extract(raw, d.get("source") or "telegram", d.get("post_url") or "", d.get("posted_at"))
            if re_jobs and not bad_title_re.search(re_jobs[0].title):
                title = re_jobs[0].title

    while _DANGLING_TAIL_RE.search(title) and len(title) > 12:
        title = _DANGLING_TAIL_RE.sub("", title).strip(" -–:،,.")
    d["title"] = title or d.get("title") or ""
    d["employer"] = emp or None
    return d["title"], emp


def _find_multi_item_context(raw_orig: str, title: str) -> str:
    """When a post lists numbered vacancies with per-item sublines, extract only those sublines."""
    raw_norm = _UI_KEYCAP_RE.sub(lambda m: f"{m.group(0)[0]}. ", raw_orig or "")
    lines = [_strip_ui_junk(ln) for ln in raw_norm.splitlines()]
    t_core = re.split(r"[–\-/|(]", title)[0].strip()
    if len(t_core) < 4:
        return ""
    for idx, ln in enumerate(lines):
        if _NUMBERED_LIST_ITEM_RE.match(ln) and t_core in ln:
            parts: list[str] = []
            clean_head = re.sub(r"^\s*\d{1,2}\s*[-.)–]\s*", "", ln).strip(" .")
            if clean_head and clean_head != title and "–" in clean_head:
                parts.append(clean_head)
            for sub in lines[idx + 1: idx + 5]:
                if not sub or _NUMBERED_LIST_ITEM_RE.match(sub):
                    break
                if re.match(r"^(?:الوصف الوظيفي والتقديم|طريقة التقديم|للتقديم|رابط)\s*[:：]?$", sub):
                    continue
                sub_c = re.sub(r"https?://\S+", "", sub).strip(" .·")
                if sub_c and not _SKIP_SUMMARY_LINE.search(sub_c):
                    parts.append(sub_c)
            if parts:
                return _clean_summary_text(" · ".join(parts))
    return ""


def _enrich_from_raw(d: dict) -> None:
    raw_orig = d.get("raw") or ""
    raw = "\n".join(_strip_ui_junk(ln) for ln in raw_orig.splitlines()).strip()
    if d.get("raw"):
        d["raw"] = raw
    title, emp = _repair_title_and_emp(d)
    if d.get("specialty"):
        d["specialty"] = re.sub(r"\bالتقان(?=\s|$)", "التقاني", _strip_ui_junk(d["specialty"]))
    from . import taxonomy as tx
    if not d.get("cities") and d.get("country", "sy") == "sy":
        inferred_cs = tx.cities_from_text("\n".join(filter(None, (title, emp, raw))))
        if inferred_cs:
            d["cities"] = inferred_cs
    if d.get("shift") is None and raw:
        sh = tx.shift(raw)
        if sh:
            d["shift"] = sh
    if d.get("tags"):
        d["tags"] = [t for t in d["tags"] if t not in _PORTAL_TAGS]
    if d.get("apply_url"):
        d["apply_url"] = _strip_wa_query(d["apply_url"])
    city_set = set(d.get("cities") or [])
    if raw:
        if not d.get("specialty"):
            if m := _SPEC_RE.search(raw):
                spec = _strip_ui_junk(m.group(1)).strip(" .-–|")
                if 2 < len(spec) <= 90:
                    d["specialty"] = spec
        if not d.get("tasks"):
            if m := _TASKS_SEC_RE.search(raw):
                d["tasks"] = _clean_bullets(m.group(1))
        if not d.get("skills"):
            if m := _SKILLS_SEC_RE.search(raw):
                d["skills"] = _clean_bullets(m.group(1))
    if d.get("tasks"):
        d["tasks"] = [
            _strip_ui_junk(re.sub(r"^\s*(?:[1-9]\d?|[١-٩][٠-٩]?)\s*[-.)–]\s*", "", str(t))).strip()
            for t in d["tasks"]
            if not _SPAM_BULLET_RE.search(str(t)) and not _APPLY_LEAK_RE.search(str(t))
        ]
    if d.get("skills"):
        d["skills"] = [
            _strip_ui_junk(str(s))
            for s in d["skills"]
            if not _SPAM_BULLET_RE.search(str(s)) and not _APPLY_LEAK_RE.search(str(s))
        ]

    multi_list_raw = bool(
        raw_orig and len(re.findall(r"(?m)^\s*(?:[1-9]\d?|[١-٩][٠-٩]?|1️⃣|2️⃣|3️⃣|4️⃣|5️⃣)\s*(?:[-.)–]|\ufe0f?\u20e3|\s+[ء-يA-Za-z])", raw_orig)) >= 2
    )

    cur_raw_sum = d.get("summary") or ""
    title_core = re.split(r"[—–\-|(]", title)[0].strip()
    is_bare_edu_fallback = bool(
        re.match(r"^فرصة عمل (?:لدى |في )", cur_raw_sum)
        and ("المؤهل:" in cur_raw_sum or "الخبرة: غير مطلوبة" in cur_raw_sum)
        and not any(k in cur_raw_sum for k in ("التخصص:", "الراتب:", "الدوام:", "آخر موعد:", "الفئة "))
    )
    has_bad_cur = bool(
        "\ufffd" in cur_raw_sum
        or _UI_JUNK_RE.search(cur_raw_sum)
        or _BOILERPLATE_SUM_RE.search(cur_raw_sum)
        or _DANGLING_TAIL_RE.search(cur_raw_sum)
        or is_bare_edu_fallback
        or "الجهة:" in cur_raw_sum
        or "أوقات الدوام" in cur_raw_sum and "من " not in cur_raw_sum
        or (title_core and cur_raw_sum.startswith(title_core) and len(cur_raw_sum) < len(title) + 38)
    )
    cur_sum = _clean_summary_text(cur_raw_sum)
    if cur_sum and (
        has_bad_cur
        or len(cur_sum) < 20
        or cur_sum == title
        or (multi_list_raw and not d.get("tasks"))
    ):
        d["summary"] = None
    else:
        d["summary"] = _clip_clause(cur_sum) if cur_sum else None

    if not d.get("summary"):
        if d.get("tasks"):
            d["summary"] = _clip_clause(_clean_summary_text(" · ".join(d["tasks"][:2])))
        elif d.get("skills"):
            d["summary"] = _summarize_skills(d["skills"]) or _fallback_summary(d)
        elif d.get("_extra_scope"):
            scope = d.pop("_extra_scope")
            d["summary"] = _clip_clause(scope)
        elif d.get("_colon_roles"):
            roles = d.pop("_colon_roles")
            default_c = "تركيا" if d.get("country") == "tr" else "سوريا"
            cs = "، ".join((d.get("cities") or [])[:2]) or default_c
            d["summary"] = _clip_clause(f"الشواغر المطلوبة ({cs}): " + " · ".join(roles[:5]))
        elif multi_list_raw and raw_orig:
            item_ctx = _find_multi_item_context(raw_orig, title)
            d["summary"] = _clip_clause(item_ctx) if item_ctx else _fallback_summary(d)
        elif raw:
            # If two distinct posts were glued into raw, start from the section matching this job's title
            raw_scan = raw
            t_kw = next((w for w in re.split(r"\s+", re.split(r"[–\-/|(]", title)[0]) if len(w) >= 4), "")
            if t_kw and raw.count("مطلوب ") >= 2:
                idx_hit = raw.find(t_kw)
                if idx_hit > 60:
                    raw_scan = raw[max(0, raw.rfind("\n", 0, idx_hit)):]
            raw_lines = [ln.strip() for ln in raw_scan.splitlines()]
            lines: list[str] = []
            title_head = title.split("—")[0].strip()
            li = 0
            while li < len(raw_lines):
                ln = raw_lines[li]
                li += 1
                if not ln or _NUMBERED_LIST_ITEM_RE.match(ln):
                    continue
                # Merge standalone colon labels like «أوقات الدوام:\nمن الساعة 9 صباحاً حتى الساعة 6 مساءً»
                if ln.endswith((":", "：")) and len(ln) <= 32 and li < len(raw_lines):
                    nxt_v = raw_lines[li].strip(" •-*–\t\r")
                    hdr_plain = ln.rstrip(":： ").strip()
                    if (
                        re.match(r"^(?:أوقات\s*(?:العمل|الدوام)|ساعات\s*(?:العمل|الدوام)|مكان\s*العمل|الراتب(?:\s*والمزايا)?|المؤهل(?:\s*المطلوب)?)$", hdr_plain)
                        and nxt_v and len(nxt_v) >= 4 and not _SKIP_SUMMARY_LINE.search(nxt_v)
                    ):
                        lines.append(_clean_summary_text(f"{hdr_plain}: {nxt_v}"))
                        li += 1
                        if sum(len(x) for x in lines) >= 160 or len(lines) >= 2:
                            break
                        continue
                core = _strip_intro_fluff(ln, emp)
                if not core:
                    continue
                s = _clean_summary_text(core)
                if not s or len(s) < 12 or _NUMBERED_LIST_ITEM_RE.match(s):
                    continue
                if re.match(r"^(?:المسم[ىي]\s*الوظيف[يى]|Job\s*Title)\s*[:：]", s, re.I) and title_head and title_head in s:
                    continue
                if s == title or (title_head and s == title_head) or s == emp or s in city_set:
                    continue
                if re.fullmatch(r"[\d\s+\-()،,./:]+", s):
                    continue
                if s.startswith("الفئة الوظيفية:") or s.startswith("محافظة العمل:") or s.startswith("المستوى التعليمي:"):
                    continue
                lines.append(s)
                if sum(len(x) for x in lines) >= 160 or len(lines) >= 2:
                    break
            if lines:
                joined = _clean_summary_text(" · ".join(lines))
                # Don't let summary merely duplicate title + location/phone when skills or structured specs exist
                if not (title_head and joined.startswith("شاغر " + title_head) and not d.get("skills")):
                    d["summary"] = _clip_clause(joined)
        d.pop("_extra_scope", None)
        d.pop("_colon_roles", None)
        if not d.get("summary") and d.get("skills"):
            d["summary"] = _clip_clause(_clean_summary_text(" · ".join(d["skills"][:2])))
        if not d.get("summary"):
            d["summary"] = _fallback_summary(d)

    cur_s = d.get("summary") or ""
    if not cur_s or len(cur_s) < 18 or cur_s == title:
        d["summary"] = _fallback_summary(d)
    elif not re.search(r"[ء-ي]", cur_s):
        emp_ar = _clean_employer_val(d.get("employer"), bool(d.get("official")))
        default_country = "تركيا" if d.get("country") == "tr" else "سوريا"
        cs_ar = "، ".join((d.get("cities") or [])[:2]) or default_country
        prefix = f"شاغر لدى {emp_ar} في {cs_ar}" if emp_ar else f"شاغر عمل في {cs_ar}"
        d["summary"] = _clip_clause(f"{prefix} — {cur_s}")


def trust_metrics(con: sqlite3.Connection) -> dict:
    """Real-time transparency & quality telemetry computed directly from SQLite."""
    today_s = date.today().isoformat()
    open_total = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='live' AND expires_on>=? AND (deadline IS NULL OR deadline='' OR deadline>=?)",
        (today_s, today_s),
    ).fetchone()[0]
    official_verified = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='live' AND sector IN ('gov','ngo') "
        "AND expires_on>=? AND (deadline IS NULL OR deadline='' OR deadline>=?)",
        (today_s, today_s),
    ).fetchone()[0]
    verified_employers = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='live' AND source='employer' "
        "AND expires_on>=? AND (deadline IS NULL OR deadline='' OR deadline>=?)",
        (today_s, today_s),
    ).fetchone()[0]
    jobs_total = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    sources_total = con.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0]
    posts_total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    expired_filtered = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='expired' OR expires_on<? OR (deadline IS NOT NULL AND deadline!='' AND deadline<?)",
        (today_s, today_s),
    ).fetchone()[0]
    rejected_cnt = con.execute("SELECT COUNT(*) FROM jobs WHERE status='rejected'").fetchone()[0]
    reports_cnt = con.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    return {
        "open_total": open_total,
        "scanned_total": max(posts_total, sources_total, jobs_total),
        "duplicates_merged": max(0, sources_total - jobs_total),
        "expired_filtered": expired_filtered,
        "rejected_filtered": rejected_cnt + reports_cnt,
        "official_verified": official_verified,
        "verified_employers": verified_employers,
        "updated_at": get_meta(con, "updated_at"),
    }


def row_to_dict(con, row) -> dict:
    from . import direct_apply as da, taxonomy as tx

    d = json.loads(row["data"])
    _enrich_from_raw(d)
    d.pop("raw", None)
    if d.get("apply_url") and da.is_disallowed_offline_url(d["apply_url"]):
        d["apply_url"] = None
        d["apply_types"] = tx.infer_apply_types(None, d.get("phones") or [], bool(d.get("whatsapp")), d.get("email"))
    d["id"] = row["id"]
    d["status"] = row["status"]
    d["expires_on"] = row["expires_on"]
    d["first_seen"] = row["first_seen"]
    d.setdefault("country", row["country"] if "country" in row.keys() else "sy")
    d.setdefault("sector", row["sector"] if "sector" in row.keys() else ("gov" if d.get("official") else "private"))
    d.setdefault("apply_types", [x for x in (row["apply_types"] or "").split("|") if x] if "apply_types" in row.keys() else [])
    d["url_requires_account"] = da.requires_portal_account(d.get("apply_url"))
    d["requires_account"] = bool(
        d["url_requires_account"]
        and not (set(d.get("apply_types") or []) & {"whatsapp", "email", "call"})
    )
    sec = d["sector"]
    srcs = []
    seen_src = set()
    for s in con.execute("SELECT source, post_url FROM job_sources WHERE job_id=? ORDER BY posted_at", (row["id"],)):
        lbl = config.SOURCE_LABEL.get(s["source"], s["source"])
        if lbl in seen_src:
            continue
        seen_src.add(lbl)
        is_official_public = bool(
            sec in ("gov", "ngo")
            and (s["source"] in config.OFFICIAL_GOV_SOURCES or s["source"] in config.OFFICIAL_NGO_SOURCES)
        )
        srcs.append(dict(source=s["source"], post_url=s["post_url"], label=lbl, official_public=is_official_public))
    d["sources"] = srcs
    d["sources_count"] = len(srcs)
    is_verified_emp = bool(row["source"] == "employer" and row["status"] == "live")
    d["verified_employer"] = is_verified_emp
    d["trust_tier"] = "gov" if sec == "gov" else ("ngo" if sec == "ngo" else ("verified_employer" if is_verified_emp else "standard"))
    return d
