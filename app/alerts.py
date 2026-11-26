"""Job alerts for app accounts: a phone notification and/or a Telegram message when new jobs fit the person.

Who gets what:
- mode «daily» (the default): one summary a day at the hour they picked (Damascus/Istanbul time).
- mode «instant»: a notification soon after a matching job arrives, at most MAX_INSTANT_PER_DAY a day, never at night.
- mode «off»: nothing.
Every job reaches a person at most once (alert_sent). Matching uses the same rules as the Telegram bot.

Phone notifications go through Firebase Cloud Messaging (FCM) when FCM_SERVICE_ACCOUNT is configured; until then
they are written to the log and to OUTBOX, so everything can be built and tested without it.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from . import config, db
from . import taxonomy as tx

log = logging.getLogger("alerts")

LOCAL = timezone(timedelta(hours=3))        # Syria and Turkey are both UTC+3 all year
QUIET_FROM, QUIET_TO = 22, 8                # no instant notifications from 10pm to 8am
MAX_INSTANT_PER_DAY = 6
LOOKBACK_HOURS = 48                         # a job older than this is no longer «new»
LINK_CODE_MINUTES = 30
MODES = ("daily", "instant", "off")
HOURS = (8, 12, 16, 19, 21)

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_alerts (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'daily' CHECK (mode IN ('daily', 'instant', 'off')),
    daily_hour INTEGER NOT NULL DEFAULT 19,
    last_daily TEXT,                        -- local date of the last daily summary
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_devices (   -- one row per phone; the token is what FCM needs to reach it
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS push_devices_user ON push_devices(user_id);
CREATE TABLE IF NOT EXISTS alert_sent (     -- each job reaches a person once
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_id)
);
CREATE TABLE IF NOT EXISTS telegram_links ( -- an app account that also gets its alerts on Telegram
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL UNIQUE,
    linked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_link_codes (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
"""

OUTBOX: list[dict] = []  # notifications «sent» while FCM isn't configured (dev and tests)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------- settings
def get_settings(con, user_id: int) -> dict:
    row = con.execute("SELECT * FROM user_alerts WHERE user_id=?", (user_id,)).fetchone()
    linked = con.execute("SELECT 1 FROM telegram_links WHERE user_id=?", (user_id,)).fetchone() is not None
    devices = con.execute("SELECT COUNT(*) FROM push_devices WHERE user_id=?", (user_id,)).fetchone()[0]
    return {
        "mode": row["mode"] if row else "daily",
        "daily_hour": row["daily_hour"] if row else 19,
        "hours": list(HOURS),
        "telegram_linked": linked,
        "telegram_available": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_BOT_USERNAME),
        "push_devices": devices,
    }


def save_settings(con, user_id: int, mode: str | None = None, daily_hour: int | None = None) -> dict:
    from .accounts import AccountError
    if mode is not None and mode not in MODES:
        raise AccountError("اختيار غير معروف", field="mode")
    if daily_hour is not None and daily_hour not in HOURS:
        raise AccountError("اختار ساعة من القائمة", field="daily_hour")
    now = _now().isoformat()
    con.execute("INSERT OR IGNORE INTO user_alerts (user_id, created_at, updated_at) VALUES (?,?,?)", (user_id, now, now))
    if mode is not None:
        con.execute("UPDATE user_alerts SET mode=?, updated_at=? WHERE user_id=?", (mode, now, user_id))
    if daily_hour is not None:
        con.execute("UPDATE user_alerts SET daily_hour=?, updated_at=? WHERE user_id=?", (daily_hour, now, user_id))
    con.commit()
    return get_settings(con, user_id)


def register_device(con, user_id: int, token: str, platform: str = "") -> None:
    from .accounts import AccountError
    token = str(token or "").strip()
    if not 20 <= len(token) <= 4096:
        raise AccountError("رمز الجهاز مو صحيح", field="token")
    now = _now().isoformat()
    # A phone moves to whoever is logged in on it now.
    con.execute("INSERT INTO push_devices (token, user_id, platform, created_at, last_seen) VALUES (?,?,?,?,?) "
                "ON CONFLICT(token) DO UPDATE SET user_id=excluded.user_id, platform=excluded.platform, last_seen=excluded.last_seen",
                (token, user_id, str(platform or "")[:20], now, now))
    con.execute("INSERT OR IGNORE INTO user_alerts (user_id, created_at, updated_at) VALUES (?,?,?)", (user_id, now, now))
    con.commit()


def remove_device(con, user_id: int, token: str) -> None:
    con.execute("DELETE FROM push_devices WHERE token=? AND user_id=?", (str(token or ""), user_id))
    con.commit()


# ---------------------------------------------------------------- Telegram linking
def telegram_link_url(con, user_id: int) -> dict:
    """A one-time link: opening it in Telegram tells our bot which account this chat belongs to."""
    from .accounts import AccountError
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_BOT_USERNAME):
        raise AccountError("التلغرام مو مفعّل لهلق", status=503, code="telegram_unavailable")
    con.execute("DELETE FROM telegram_link_codes WHERE user_id=? OR expires_at < ?", (user_id, _now().isoformat()))
    code = secrets.token_urlsafe(18)
    expires = _now() + timedelta(minutes=LINK_CODE_MINUTES)
    con.execute("INSERT INTO telegram_link_codes VALUES (?,?,?)", (code, user_id, expires.isoformat()))
    con.commit()
    return {"url": f"https://t.me/{config.TELEGRAM_BOT_USERNAME}?start=link_{code}", "expires_at": expires.isoformat()}


def link_telegram(con, code: str, chat_id: int) -> str | None:
    """Called by the bot for «/start link_<code>». Returns the person's first name, or None if the code is bad."""
    row = con.execute("SELECT * FROM telegram_link_codes WHERE code=?", (code,)).fetchone()
    if not row or row["expires_at"] < _now().isoformat():
        return None
    con.execute("DELETE FROM telegram_link_codes WHERE code=?", (code,))
    con.execute("DELETE FROM telegram_links WHERE chat_id=? OR user_id=?", (chat_id, row["user_id"]))
    now = _now().isoformat()
    con.execute("INSERT INTO telegram_links VALUES (?,?,?)", (row["user_id"], chat_id, now))
    con.execute("INSERT OR IGNORE INTO user_alerts (user_id, created_at, updated_at) VALUES (?,?,?)", (row["user_id"], now, now))
    con.commit()
    user = con.execute("SELECT first_name FROM users WHERE id=?", (row["user_id"],)).fetchone()
    return user["first_name"] if user else ""


def unlink_telegram(con, user_id: int | None = None, chat_id: int | None = None) -> None:
    if user_id is not None:
        con.execute("DELETE FROM telegram_links WHERE user_id=?", (user_id,))
    if chat_id is not None:
        con.execute("DELETE FROM telegram_links WHERE chat_id=?", (chat_id,))
    con.commit()


# ---------------------------------------------------------------- matching
def matches(profile: sqlite3.Row | dict, job: dict) -> bool:
    """Country, then degrees, fields and cities, each only if the person chose some (several allowed).
    One degree follows the bot's rule (that level and the levels it usually covers); several = any of them."""
    from .bot import matches as bot_matches
    if (job.get("country") or "sy") != (profile["country"] or "sy"):
        return False
    split = lambda v: [x for x in (v or "").split("|") if x]
    keys = profile.keys() if hasattr(profile, "keys") else []
    educations = split(profile["educations"]) if "educations" in keys else []
    educations = educations or ([profile["education"]] if profile["education"] else [])
    fields = split(profile["fields"]) if "fields" in keys else []
    fields = fields or ([profile["field"]] if profile["field"] else [])
    if len(educations) == 1:
        if not bot_matches({"education": educations[0], "field": None, "cities": ""}, job):
            return False
    elif educations and job.get("education") and job["education"] not in educations:
        return False
    if fields and job.get("field") not in fields:
        return False
    cities = split(profile["cities"])
    if cities and job.get("cities") and not set(cities) & set(job["cities"]):
        return False
    return True


# ---------------------------------------------------------------- sending
class Pusher:
    """Sends phone notifications. Without FCM credentials it records them in OUTBOX (development and tests)."""

    def __init__(self):
        self._session = None
        self._project = None
        if config.FCM_SERVICE_ACCOUNT:
            try:
                from google.oauth2 import service_account
                from google.auth.transport.requests import AuthorizedSession
                info = json.loads(config.FCM_SERVICE_ACCOUNT)
                creds = service_account.Credentials.from_service_account_info(
                    info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
                self._session, self._project = AuthorizedSession(creds), info["project_id"]
            except Exception:
                log.exception("FCM is configured but could not start; notifications go to the log only")

    @property
    def live(self) -> bool:
        return self._session is not None

    def send(self, token: str, title: str, body: str, route: str) -> str:
        """'ok', 'gone' (the app was uninstalled: forget this phone) or 'error'."""
        if not self.live:
            OUTBOX.append({"token": token, "title": title, "body": body, "route": route})
            log.info("PUSH (FCM not configured) %s | %s | %s", title, body, route)
            return "ok"
        msg = {"message": {"token": token, "notification": {"title": title, "body": body},
                           "data": {"route": route},
                           "android": {"priority": "high", "notification": {"channel_id": "jobs"}}}}
        try:
            r = self._session.post(f"https://fcm.googleapis.com/v1/projects/{self._project}/messages:send", json=msg, timeout=20)
        except Exception:
            log.exception("FCM send failed")
            return "error"
        if r.status_code == 200:
            return "ok"
        if r.status_code in (404, 400) and ("UNREGISTERED" in r.text or "INVALID_ARGUMENT" in r.text):
            return "gone"
        log.warning("FCM %s: %s", r.status_code, r.text[:300])
        return "error"


def _summary_text(jobs: list[dict]) -> tuple[str, str]:
    if len(jobs) == 1:
        j = jobs[0]
        where = " · ".join(x for x in [j.get("employer"), "، ".join((j.get("cities") or [])[:2])] if x)
        return "وظيفة جديدة على قد شهادتك", f"{j['title']}" + (f" — {where}" if where else "")
    n = len(jobs)
    head = f"{n} وظائف جديدة إلك" if n <= 10 else f"{n} وظيفة جديدة إلك"
    titles = "، ".join(j["title"] for j in jobs[:3])
    return head, titles + ("…" if n > 3 else "")


def _telegram_digest(jobs: list[dict]) -> str:
    from .bot import message
    title, _ = _summary_text(jobs)
    parts = [f"<b>{title}</b>"] + [message(j) for j in jobs[:8]]
    if len(jobs) > 8:
        parts.append(f"و{len(jobs) - 8} غيرهن بالتطبيق.")
    return "\n\n".join(parts)


def run(con, pusher: Pusher | None = None, tg=None, now: datetime | None = None) -> dict:
    """Called after each collection round (and on a timer): send whatever is due. Returns counts for the log."""
    now = now or _now()
    local = now.astimezone(LOCAL)
    pusher = pusher or Pusher()
    since = (now - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    today = local.date().isoformat()
    day_start = datetime.combine(local.date(), datetime.min.time(), LOCAL).astimezone(timezone.utc).isoformat()
    candidates = [db.row_to_dict(con, r) for r in con.execute(
        "SELECT * FROM jobs WHERE status='live' AND first_seen >= ? AND expires_on >= ? "
        "AND (deadline IS NULL OR deadline='' OR deadline >= ?) ORDER BY posted_at DESC LIMIT 1500",
        (since, today, today))]
    quiet = local.hour >= QUIET_FROM or local.hour < QUIET_TO
    stats = {"users": 0, "push": 0, "telegram": 0, "jobs": 0}
    users = con.execute(
        """SELECT u.id AS user_id, u.created_at, a.mode, a.daily_hour, a.last_daily,
                  p.education, p.field, p.country, p.cities, p.educations, p.fields
           FROM users u JOIN user_profiles p ON p.user_id=u.id
           LEFT JOIN user_alerts a ON a.user_id=u.id
           WHERE u.email_verified_at IS NOT NULL""").fetchall()
    for u in users:
        mode = u["mode"] or "daily"
        if mode == "off":
            continue
        if mode == "daily":
            if u["last_daily"] == today or local.hour < (u["daily_hour"] or 19):
                continue
        elif quiet:
            continue
        else:
            sent_today = con.execute("SELECT COUNT(DISTINCT sent_at) FROM alert_sent WHERE user_id=? AND sent_at >= ?",
                                     (u["user_id"], day_start)).fetchone()[0]
            if sent_today >= MAX_INSTANT_PER_DAY:
                continue
        devices = [r["token"] for r in con.execute("SELECT token FROM push_devices WHERE user_id=?", (u["user_id"],))]
        chat = con.execute("SELECT chat_id FROM telegram_links WHERE user_id=?", (u["user_id"],)).fetchone()
        if not devices and not chat:
            continue
        already = {r[0] for r in con.execute("SELECT job_id FROM alert_sent WHERE user_id=?", (u["user_id"],))}
        fresh = [j for j in candidates if j["id"] not in already and j["first_seen"] >= u["created_at"] and matches(u, j)]
        if mode == "daily":
            con.execute("UPDATE user_alerts SET last_daily=? WHERE user_id=?", (today, u["user_id"]))
            if u["mode"] is None:  # never saved settings: start a row so «once a day» holds
                con.execute("INSERT OR IGNORE INTO user_alerts (user_id, last_daily, created_at, updated_at) VALUES (?,?,?,?)",
                            (u["user_id"], today, now.isoformat(), now.isoformat()))
        if not fresh:
            con.commit()
            continue
        stats["users"] += 1
        stats["jobs"] += len(fresh)
        title, body = _summary_text(fresh)
        route = f"/job/{fresh[0]['id']}" if len(fresh) == 1 else "/jobs"
        channel = []
        for token in devices:
            result = pusher.send(token, title, body, route)
            if result == "gone":
                con.execute("DELETE FROM push_devices WHERE token=?", (token,))
            elif result == "ok":
                stats["push"] += 1
                channel.append("push")
        if chat and tg is not None:
            tg.call("sendMessage", chat_id=chat["chat_id"], text=_telegram_digest(fresh), parse_mode="HTML",
                    disable_web_page_preview=True)
            stats["telegram"] += 1
            channel.append("telegram")
        if channel:
            stamp = now.isoformat()
            con.executemany("INSERT OR IGNORE INTO alert_sent VALUES (?,?,?,?)",
                            [(u["user_id"], j["id"], "+".join(dict.fromkeys(channel)), stamp) for j in fresh])
        con.commit()
    # keep the «already sent» list small: older than the lookback window can never match again
    con.execute("DELETE FROM alert_sent WHERE sent_at < ?", ((now - timedelta(days=14)).isoformat(),))
    con.commit()
    return stats
