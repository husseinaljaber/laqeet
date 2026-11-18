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
_SUMMARY_SOURCE_TAIL_RE = re.compile(
    r"(?:\s*[·•\-|]\s*|\s+)(?:💎+\s*)?المصدر\s*[:：]\s*[^·\n]+|"
    r"\s*لدى\s+(?:جهة\s*توظيف|منظمة\s*أممية(?:\s*/\s*دولية)?)\s+عبر\s+[^\s·,،.]+[^·\n]*|"
    r"\s*عبر\s+(?:منصة|موقع|قناة|بوابة)\s+(?:فريلانس\s*سوريا|ورك\s*لينك|ساحة\s*المنظمات|سلام\s*جوبز|"
    r"وظائف\s*سوريا|فرص\s*عمل(?:\s+في\s+(?:تركيا|سوريا))?(?:\s+الرسمية)?|ngo(?:s)?\s*syria|job\s*in\s*syria|"
    r"worklink|salam\s*jobs|jobsintr|reliefweb|unjobnet|syria-?jobs)[^·\n]*",
    re.I,
)
_PORTAL_TAGS = frozenset({"Job In Syria", "ورك لينك", "NGOs Syria"})


def _clean_employer_val(emp: str | None, official: bool = False) -> str:
    raw = (emp or "").strip()
    if not raw or raw.lower() in _PLACEHOLDER_EMP or _PORTAL_EMP_RE.search(raw):
        return "جهة حكومية رسمية" if official else ""
    return raw


def _clean_summary_text(text: str | None) -> str:
    if not text:
        return ""
    s = _SUMMARY_SOURCE_TAIL_RE.sub("", text)
    s = re.sub(r"^(?:\s*[·•\-]\s*)+|(?:\s*[·•\-]\s*)+$", "", s)
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
    r"لنشر ما لديكم|راسلونا دايركت|للتواصل ولمعلومات|الاتصال على الرقم|"
    r"(?:💎+\s*)?المصدر\s*[:：]|عبر\s+(?:Job\s*In\s*Syria|WorkLink|NGOs?\s*Syria|salam\s*jobs|jobsintr)|"
    r"(?:^|[\s(])@[A-Za-z0-9_]{3,}(?!\.[A-Za-z0-9-])",
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


def _fallback_summary(d: dict) -> str:
    title = (d.get("title") or "شاغر وظيفي").strip()
    emp = _clean_employer_val(d.get("employer"), bool(d.get("official")))
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
    cleaned_emp = _clean_employer_val(d.get("employer"), bool(d.get("official")))
    if cleaned_emp != emp_raw:
        d["employer"] = cleaned_emp or None
    if d.get("tags"):
        d["tags"] = [t for t in d["tags"] if t not in _PORTAL_TAGS]
    if d.get("apply_url"):
        d["apply_url"] = _strip_wa_query(d["apply_url"])
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

    # Replace any stale synthetic, corrupted, or third-party-source summary
    cur_sum = _clean_summary_text((d.get("summary") or "").replace("\ufffd", "").replace("<PRIVATE_PERSON>", "").strip())
    if cur_sum and ("التقديم متاح مباشرة عبر الرابط المرفق" in cur_sum or len(cur_sum) < 10):
        d["summary"] = None
    else:
        d["summary"] = cur_sum or None

    if not d.get("summary"):
        if d.get("tasks"):
            d["summary"] = _clean_summary_text(" · ".join(d["tasks"][:2])[:240]) or None
        elif raw:
            lines: list[str] = []
            title_head = title.split("—")[0].strip()
            for ln in raw.splitlines():
                s = _clean_summary_text(re.sub(r"https?://\S+", "", ln))
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
                d["summary"] = _clean_summary_text(" · ".join(lines)[:240]) or None
        if not d.get("summary") and d.get("skills"):
            d["summary"] = _clean_summary_text(" · ".join(d["skills"][:2])[:240]) or None
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
