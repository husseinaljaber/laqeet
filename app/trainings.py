"""Trainings, internships and scholarships («تدريبات ومنح») found in the same channel posts as jobs.

Job channels also post courses, internships and grants. The job extractor drops them on purpose; this module keeps
the real ones in their own table so they never mix with job numbers.

    python -m app.trainings --backfill      # read the posts already stored and pick out the trainings
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

from . import config, db
from . import taxonomy as tx
from .arabic import soft_fold as F

TRAINING_DAYS = 21  # without a deadline, a training stays listed this long after posting

SCHEMA = """
CREATE TABLE IF NOT EXISTS trainings (
    id INTEGER PRIMARY KEY,
    fp TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    provider TEXT,
    kind TEXT NOT NULL,          -- course | internship | scholarship | volunteer
    cost TEXT NOT NULL,          -- free | paid | unknown
    online INTEGER DEFAULT 0,
    cities TEXT DEFAULT '',      -- |دمشق|حلب|
    country TEXT DEFAULT 'sy',
    deadline TEXT,
    apply_url TEXT,
    phones TEXT DEFAULT '',
    source TEXT NOT NULL,
    post_url TEXT NOT NULL,
    posted_at TEXT,
    expires_on TEXT NOT NULL,
    text TEXT,
    first_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trainings_open ON trainings(expires_on, posted_at);
"""

KINDS = [("course", "دورات"), ("internship", "تدريب عملي"), ("scholarship", "منح"), ("volunteer", "تطوع")]
KIND_LABEL = dict(KINDS)

# What the post is about, from its first lines (hashtags and headline), not from a requirement deep in a job ad.
_KIND_RX = [
    ("scholarship", re.compile(F(r"منحه دراسيه|منح دراسيه|#منح|#منحه|\bscholarship|\bfellowship|منحه (?:ماجستير|دكتوراه|جامعيه|كامله)"), re.I)),
    ("internship", re.compile(F(r"تدريب عملي|فرصه تدريب|فرص تدريب|برنامج (?:التدريب|تدريب) (?:الصيفي|المهني|العملي)|#تدريب_عملي|\binternship|\binterns?\b|متدربين"), re.I)),
    ("volunteer", re.compile(F(r"فرصه تطوع|فرص تطوع|#تطوع|مطلوب متطوعين|\bvolunteer"), re.I)),
    ("course", re.compile(F(r"#تدريب|تدريب مجاني|تدريبا مجانيا|دوره تدريبيه|دورات تدريبيه|ورشه تدريبيه|برنامج تدريبي|كورس|دبلوم|\bbootcamp|\btraining program|التسجيل في (?:دوره|برنامج)|تعلن عن (?:بدء التسجيل|اطلاق) (?:دوره|برنامج)"), re.I)),
]
_NOT_TRAINING = re.compile(F(
    r"مناقصه|استدراج عروض|استقبل|التقي|التقى|زار |بحث (?:وزير|محافظ)|نتائج (?:الاختبار|المسابقه)|"
    r"اعلن الان على قناتنا|للاعلان في القناه|اعلانات مموله|"
    r"تعلن .{0,60}عن (?:حاجتها|توفر شاغر|توافر شاغر|شواغر)|شاغر وظيفي|الشاغر الوظيفي|شواغر وظيفيه|الشواغر التاليه|"
    r"المسمى الوظيفي|يفضل من لديه دورات|مطلوب موظف|مطلوب للعمل|نبحث عن موظف|"
    r"مفاضله|قبول الطلاب|التسجيل (?:الالكتروني )?للطلاب|الطلاب الجدد"
), re.I)
_FREE = re.compile(F(r"مجاني|مجانا|مجانيا|بدون رسوم|بلا رسوم|\bfree\b|ممول بالكامل|منحه كامله|fully funded"), re.I)
_PAID = re.compile(F(r"رسوم (?:الدوره|التسجيل|الاشتراك|رمزيه)|القسط|التكلفه|بقيمه|بسعر|\d[\d,.]*\s*(?:\$|دولار|ل\.س|ليره|\bTL\b)|\$\s*\d|خصم \d"), re.I)
_ONLINE = re.compile(F(r"اونلاين|أونلاين|عن بعد|عن بُعد|\bonline\b|zoom|زوم|google meet|واتساب|whatsapp|تلغرام"), re.I)
_TITLE_QUOTED = re.compile(r"[«\"“]([^»\"”\n]{4,80})[»\"”]")
_TITLE_AFTER = re.compile(F(r"(?:بعنوان|عنوان التدريب|اسم الدوره|اسم البرنامج)\s*[:：]?\s*([^\n]{4,80})"))
_TITLE_REGISTER = re.compile(r"(?:فتح باب (?:التسجيل|التقديم|التطوع) (?:في|على|ل)|عن (?:إطلاق|اطلاق|بدء التسجيل في|افتتاح))\s+([^\n،.]{6,90})")
_TITLE_LEAD = re.compile(r"(?:^|\n)\s*[^\w\n]{0,4}\s*((?:كورس|دبلوم|دورة|دوره|برنامج|ورشة|منحة|منح|تدريب)\s+[^\n]{3,70})")
_PROVIDER = re.compile(r"((?:أكاديمية|اكاديمية|منظمة|معهد|جامعة|مركز|مؤسسة|جمعية|برنامج الأمم|الهيئة)\s+[^\n،,.:!؟]{2,40})")
_LINK_SKIP = re.compile(r"t\.me/|telegram\.me|whatsapp\.com/channel|chat\.whatsapp|facebook\.com|instagram\.com|youtube\.com|youtu\.be", re.I)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


def classify(text: str) -> str | None:
    """The training kind of a post, or None if it is not a training/internship/grant announcement."""
    if not text or len(text) < 40:
        return None
    head = F("\n".join(text.splitlines()[:8]))[:600]
    whole = F(text)
    if _NOT_TRAINING.search(head) or _NOT_TRAINING.search(whole[:1500]) and not re.search(F(r"#تدريب|تدريب مجاني|منحه"), head):
        return None
    for kind, rx in _KIND_RX:
        if rx.search(head):
            return kind
    return None


def _clean(s: str) -> str:
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F#*_«»\"“”]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -–:：،,.!")


def _title(text: str) -> str | None:
    for rx in (_TITLE_AFTER, _TITLE_REGISTER, _TITLE_QUOTED, _TITLE_LEAD):
        if m := rx.search(text):
            raw = m.group(1)
            if q := _TITLE_QUOTED.search(raw):  # «… بعنوان: «X»، حيث …» → X
                raw = q.group(1)
            t = _clean(raw)
            if 4 <= len(t) <= 90:
                return t
    for line in text.splitlines():
        t = _clean(line.split("|")[0])
        if 8 <= len(t) <= 90 and not t.startswith(("http", "يبدأ", "هل ")) and not re.fullmatch(r"[\W\d]+", t):
            return t
    return None


def parse(text: str, source: str, post_url: str, posted_at: str | None, country: str = "sy") -> dict | None:
    kind = classify(text)
    if not kind:
        return None
    from .extract import parse_deadline  # local import: extract is heavy

    title = _title(text)
    if not title:
        return None
    provider = None
    if m := _PROVIDER.search(text):
        provider = re.split(r"\s+(?:في سوريا|عن|تعلن|بالتعاون|بالتنسيق)\b", _clean(m.group(1).replace("«", "").replace("»", "")))[0][:60]
    whole = F(text)
    cost = "free" if _FREE.search(whole) else ("paid" if _PAID.search(whole) else "unknown")
    if kind in ("scholarship", "volunteer") and cost == "unknown":
        cost = "free"
    links = [u for u in tx.urls(text) if not _LINK_SKIP.search(u)]
    cities = (tx.tr_cities_from_text(text) if country == "tr" else tx.cities_from_text(text))[:5]
    deadline = parse_deadline(text, posted_at)
    phones = (tx.phones_tr(text) if country == "tr" else tx.phones(text))[:2]
    posted_day = (posted_at or db.now())[:10]
    expires = deadline or (date.fromisoformat(posted_day) + timedelta(days=TRAINING_DAYS)).isoformat()
    fp = hashlib.sha1(F(f"{title}|{provider or ''}").encode("utf-8")).hexdigest()[:20]
    return {
        "fp": fp, "title": title[:120], "provider": provider, "kind": kind, "cost": cost,
        "online": int(bool(_ONLINE.search(whole))), "cities": cities, "country": country, "deadline": deadline,
        "apply_url": links[0] if links else None, "phones": phones, "source": source, "post_url": post_url,
        "posted_at": posted_at, "expires_on": expires, "text": text[:2000],
    }


def store(con: sqlite3.Connection, t: dict) -> bool:
    """Insert a training; the same one seen again (another channel) keeps the first copy. True if new."""
    ensure_schema(con)
    cur = con.execute(
        "INSERT OR IGNORE INTO trainings (fp, title, provider, kind, cost, online, cities, country, deadline, apply_url, "
        "phones, source, post_url, posted_at, expires_on, text, first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (t["fp"], t["title"], t["provider"], t["kind"], t["cost"], t["online"],
         "|" + "|".join(t["cities"]) + "|" if t["cities"] else "", t["country"], t["deadline"], t["apply_url"],
         "|".join(t["phones"]), t["source"], t["post_url"], t["posted_at"], t["expires_on"], t["text"], db.now()),
    )
    return cur.rowcount > 0


def maybe_store_post(con: sqlite3.Connection, channel: str, text: str, post_url: str, posted_at: str | None) -> bool:
    ch = config.CHANNELS.get(channel) or {}
    if ch.get("sector") == "gov" and ch.get("source") == "moad":
        return False
    t = parse(text, ch.get("source", channel), post_url, posted_at, ch.get("country") or "sy")
    if not t or t["expires_on"] < date.today().isoformat():
        return False
    from .spam import is_spam

    if is_spam(text) and t["cost"] != "free":
        return False
    return store(con, t)


def _row(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in r.keys() if k not in ("text", "fp")}
    d["cities"] = [c for c in (r["cities"] or "").split("|") if c]
    d["phones"] = [p for p in (r["phones"] or "").split("|") if p]
    d["kind_label"] = KIND_LABEL.get(r["kind"], r["kind"])
    d["source_label"] = config.SOURCE_LABEL.get(r["source"], r["source"])
    d["online"] = bool(r["online"])
    return d


def search(con: sqlite3.Connection, q: str = "", kind: str = "", city: str = "", free: bool = False,
           online: bool = False, limit: int = 60) -> dict:
    ensure_schema(con)
    today = date.today().isoformat()
    sql, args = ["SELECT * FROM trainings WHERE expires_on >= ?"], [today]
    if kind in KIND_LABEL:
        sql.append("AND kind=?"); args.append(kind)
    if city:
        sql.append("AND (cities LIKE ? OR online=1)"); args.append(f"%|{city}|%")
    if free:
        sql.append("AND cost='free'")
    if online:
        sql.append("AND online=1")
    rows = con.execute(" ".join(sql) + " ORDER BY posted_at DESC", args).fetchall()
    if q.strip():
        words = [w for w in F(q).split() if len(w) >= 2]
        rows = [r for r in rows if all(w in F(f"{r['title']} {r['provider'] or ''} {r['text'] or ''}") for w in words)]
    counts = {k: 0 for k, _ in KINDS}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"total": len(rows), "counts": counts, "items": [_row(r) for r in rows[:limit]]}


def backfill(con: sqlite3.Connection) -> int:
    """Pick trainings out of every stored post (after deploying this, or after improving the rules)."""
    ensure_schema(con)
    since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    n = 0
    for r in con.execute("SELECT channel, id, date, text FROM posts WHERE date >= ? AND text IS NOT NULL AND jobs = 0",
                         (since,)).fetchall():
        n += maybe_store_post(con, r["channel"], r["text"], f"https://t.me/{r['channel']}/{r['id']}", r["date"])
    con.commit()
    return n


def prune(con: sqlite3.Connection, keep_days: int = 30) -> int:
    ensure_schema(con)
    cut = (date.today() - timedelta(days=keep_days)).isoformat()
    return con.execute("DELETE FROM trainings WHERE expires_on < ?", (cut,)).rowcount


def export_rows(con: sqlite3.Connection) -> list[list]:
    ensure_schema(con)
    cur = con.execute("SELECT * FROM trainings WHERE expires_on >= ?", (date.today().isoformat(),))
    cols = [c[0] for c in cur.description]
    return [cols] + [list(r) for r in cur.fetchall()]


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        c = db.connect()
        print(f"trainings found: {backfill(c)}")
        print(json.dumps(search(c)["counts"], ensure_ascii=False))
    else:
        print(__doc__)
