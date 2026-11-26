"""Backup and restore of what people put into the site (everything the collector can't rebuild by itself).

    python -m app.backup export backup.json.gz
    python -m app.backup import backup.json.gz

What goes in: app accounts (with password hashes, never clear passwords), their profiles and saved jobs,
employer posts with their management codes, reports, moderation decisions on collected ads,
Telegram bot subscribers and what they were already sent, anonymous usage counters, source health, meta.
Login sessions are not kept: people simply log in again after a restore.
Jobs are matched by fingerprint, not by id, so a backup restores into a database rebuilt from the seed.
The file holds employers' phone numbers and bot chat ids: keep it private (never commit it).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from . import db

VERSION = 1


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def export_data(con) -> dict:
    employer_jobs = _rows(con, "SELECT * FROM jobs WHERE source='employer'")
    emp_ids = [j["id"] for j in employer_jobs]
    fp_of = {r["id"]: r["fp"] for r in con.execute("SELECT id, fp FROM jobs")}
    q = ",".join("?" * len(emp_ids))
    return {
        "version": VERSION,
        "exported_at": db.now(),
        "employer_jobs": employer_jobs,
        "employer_sources": _rows(con, f"SELECT * FROM job_sources WHERE job_id IN ({q})", emp_ids) if emp_ids else [],
        "employer_tokens": [dict(r, fp=fp_of.get(r["job_id"])) for r in _rows(con, "SELECT * FROM employer_tokens")],
        # admin decisions and report counts on collected ads (the seed doesn't know about them)
        "moderation": _rows(con, "SELECT fp, status, reports FROM jobs WHERE source != 'employer' "
                                 "AND (status IN ('review', 'rejected') OR reports > 0)"),
        "reports": [dict(r, fp=fp_of.get(r["job_id"])) for r in _rows(con, "SELECT * FROM reports")],
        "bot_users": _rows(con, "SELECT * FROM bot_users"),
        "bot_sent": [dict(r, fp=fp_of.get(r["job_id"])) for r in _rows(con, "SELECT * FROM bot_sent")],
        "events_daily": _rows(con, "SELECT * FROM events_daily"),
        "source_health": _rows(con, "SELECT * FROM source_health"),
        "meta": _rows(con, "SELECT * FROM meta"),
        "users": _rows(con, "SELECT * FROM users") if _has_table(con, "users") else [],
        "user_profiles": _rows(con, "SELECT * FROM user_profiles") if _has_table(con, "user_profiles") else [],
        "user_jobs": [dict(r, fp=fp_of.get(r["job_id"])) for r in _rows(con, "SELECT * FROM user_jobs")]
        if _has_table(con, "user_jobs") else [],
    }


def import_data(con, data: dict) -> dict:
    """Merge a backup into `con`. Safe to run twice: existing rows are updated, not duplicated."""
    if data.get("version") != VERSION:
        raise ValueError(f"unknown backup version {data.get('version')}")
    id_by_fp = {r["fp"]: r["id"] for r in con.execute("SELECT id, fp FROM jobs")}
    old_to_new: dict[int, int] = {}
    stats = dict.fromkeys(("employer_jobs", "tokens", "moderation", "reports", "bot_users", "bot_sent", "events"), 0)

    cols = [r[1] for r in con.execute("PRAGMA table_info(jobs)")]
    for j in data.get("employer_jobs", []):
        old_id = j["id"]
        if j["fp"] in id_by_fp:
            new_id = id_by_fp[j["fp"]]
            con.execute("UPDATE jobs SET status=?, reports=?, expires_on=?, data=?, updated_at=? WHERE id=?",
                        (j["status"], j["reports"], j["expires_on"], j["data"], j["updated_at"], new_id))
        else:
            row = {c: j.get(c) for c in cols if c != "id"}
            cur = con.execute(f"INSERT INTO jobs ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
            new_id = cur.lastrowid
            id_by_fp[j["fp"]] = new_id
        old_to_new[old_id] = new_id
        db.sync_fts(con, new_id)
        stats["employer_jobs"] += 1
    for s in data.get("employer_sources", []):
        if s["job_id"] in old_to_new:
            con.execute("INSERT OR IGNORE INTO job_sources (job_id, source, post_url, posted_at) VALUES (?,?,?,?)",
                        (old_to_new[s["job_id"]], s["source"], s["post_url"], s["posted_at"]))
    for t in data.get("employer_tokens", []):
        jid = id_by_fp.get(t.get("fp"))
        if jid is None:
            continue
        con.execute("INSERT INTO employer_tokens (token, job_id, verified, contact_note, created_at, closed_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET job_id=excluded.job_id, "
                    "verified=excluded.verified, closed_at=excluded.closed_at",
                    (t["token"], jid, t["verified"], t["contact_note"], t["created_at"], t["closed_at"]))
        stats["tokens"] += 1
    for m in data.get("moderation", []):
        if m["fp"] in id_by_fp:
            con.execute("UPDATE jobs SET status=?, reports=MAX(reports, ?) WHERE fp=?", (m["status"], m["reports"], m["fp"]))
            stats["moderation"] += 1
    have_reports = {(r["job_id"], r["at"]) for r in con.execute("SELECT job_id, at FROM reports")}
    for r in data.get("reports", []):
        jid = id_by_fp.get(r.get("fp"))
        if jid is not None and (jid, r["at"]) not in have_reports:
            con.execute("INSERT INTO reports VALUES (?,?,?)", (jid, r["reason"], r["at"]))
            stats["reports"] += 1
    for u in data.get("bot_users", []):
        con.execute("INSERT OR REPLACE INTO bot_users (chat_id, education, field, cities, kind, active, step, created) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (u["chat_id"], u["education"], u["field"], u["cities"], u["kind"], u["active"], u["step"], u["created"]))
        stats["bot_users"] += 1
    for s in data.get("bot_sent", []):
        jid = id_by_fp.get(s.get("fp"))
        if jid is not None:
            con.execute("INSERT OR IGNORE INTO bot_sent (chat_id, job_id, at) VALUES (?,?,?)", (s["chat_id"], jid, s["at"]))
            stats["bot_sent"] += 1
    for e in data.get("events_daily", []):
        con.execute("INSERT INTO events_daily (day, event, key, n) VALUES (?,?,?,?) "
                    "ON CONFLICT(day, event, key) DO UPDATE SET n = MAX(n, excluded.n)",
                    (e["day"], e["event"], e["key"], e["n"]))
        stats["events"] += 1
    for h in data.get("source_health", []):
        con.execute("INSERT OR IGNORE INTO source_health (source, kind, last_run, last_count, last_items_at, last_error, "
                    "fail_streak) VALUES (?,?,?,?,?,?,?)",
                    (h["source"], h["kind"], h["last_run"], h["last_count"], h["last_items_at"], h["last_error"], h["fail_streak"]))
    if data.get("users"):
        from . import accounts
        accounts.ensure_schema(con)
        user_id_map: dict[int, int] = {}
        for u in data["users"]:
            row = con.execute("SELECT id FROM users WHERE email=?", (u["email"],)).fetchone()
            if row:
                user_id_map[u["id"]] = row["id"]
                continue
            cols = [c for c in u if c != "id"]
            cur = con.execute(f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [u[c] for c in cols])
            user_id_map[u["id"]] = cur.lastrowid
            stats["users"] = stats.get("users", 0) + 1
        for p in data.get("user_profiles", []):
            if p["user_id"] in user_id_map:
                cols = list(p)
                con.execute(f"INSERT OR REPLACE INTO user_profiles ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [user_id_map[p["user_id"]] if c == "user_id" else p[c] for c in cols])
        for uj in data.get("user_jobs", []):
            jid = id_by_fp.get(uj.get("fp"))
            if jid is not None and uj["user_id"] in user_id_map:
                con.execute("INSERT OR IGNORE INTO user_jobs (user_id, job_id, stage, note, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                            (user_id_map[uj["user_id"]], jid, uj["stage"], uj["note"], uj["created_at"], uj["updated_at"]))
    for m in data.get("meta", []):
        if m["key"].startswith(("bot_offset", "llm_calls:", "health_alert_day")):
            con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (m["key"], m["value"]))
    con.commit()
    return stats


def write_file(data: dict, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_file(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("export", "import"):
        print(__doc__)
        return 2
    con = db.connect()
    path = Path(argv[1])
    if argv[0] == "export":
        data = export_data(con)
        write_file(data, path)
        print(f"backup written: {path} ({len(data['employer_jobs'])} employer posts, {len(data['bot_users'])} bot users)")
    else:
        print("restored:", import_data(con, read_file(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
