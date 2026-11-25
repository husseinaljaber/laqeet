"""Post new jobs to public «لقيت» Telegram channels, one per city.

Set CITY_CHANNELS to a JSON map from city to channel, plus optional catch-all keys:

    CITY_CHANNELS={"دمشق": "@laqeet_damascus", "حلب": "@laqeet_aleppo", "*sy": "@laqeet_syria",
                   "*tr": "@laqeet_turkey"}

- a job goes to the channel of each of its cities, and to "*sy" / "*tr" (its country's channel) if set
- only what arrives after the feature is switched on is posted (no flood of old ads), each item once per channel
- the bot must be an admin of every channel:  python -m app.city_channels --check
The scheduler (app/run.py) calls publish() after each collection run.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date

from . import config, db
from . import taxonomy as tx
from .bot import Telegram, _h, message

log = logging.getLogger("city_channels")
MAX_PER_CHANNEL = 15        # per run; runs every UPDATE_MINUTES, so a busy channel still catches up
GAP_SECONDS = 3.1           # Telegram allows about 20 posts a minute in one channel

SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_posts (
    channel TEXT NOT NULL,
    kind TEXT NOT NULL,          -- job
    item_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (channel, kind, item_id)
);
"""


def load_map(raw: str | None = None) -> dict[str, str]:
    raw = config.CITY_CHANNELS if raw is None else raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("CITY_CHANNELS is not valid JSON; city channels are off")
        return {}
    return {str(k).strip(): str(v).strip() for k, v in data.items() if str(v).strip()}


def targets_for(cities: list[str], country: str, cmap: dict[str, str]) -> list[str]:
    """Channels a job goes to, without repeats."""
    out: list[str] = []
    for c in cities or []:
        if c in cmap and cmap[c] not in out:
            out.append(cmap[c])
    wide = cmap.get("*tr" if country == "tr" else "*sy")
    if wide and wide not in out:
        out.append(wide)
    return out


def _tag(s: str) -> str:
    return "#" + "_".join(str(s).replace("،", " ").split())


def job_post(job: dict) -> str:
    """The bot's job message, plus hashtags people can tap to see more of the same, and the shareable page."""
    tags = [_tag(c) for c in (job.get("cities") or []) if not c.startswith("عدة")][:3]
    if job.get("education"):
        tags.append(_tag(tx.EDU_LABEL.get(job["education"], "")))
    if job.get("field") and job["field"] != "other":
        tags.append(_tag(tx.FIELD_LABEL.get(job["field"], "").split("(")[0]))
    body = message(job).replace(f'{config.PUBLIC_URL}/#job-{job["id"]}', f'{config.PUBLIC_URL}/j/{job["id"]}')
    return body + ("\n" + " ".join(_h(t) for t in tags if len(t) > 1) if tags else "")


def _posted(con, channel: str, kind: str, item_id: int) -> bool:
    return con.execute("SELECT 1 FROM channel_posts WHERE channel=? AND kind=? AND item_id=?",
                       (channel, kind, item_id)).fetchone() is not None


def publish(con, tg: Telegram, cmap: dict[str, str] | None = None, sleep=time.sleep) -> dict[str, int]:
    """Post what's new since the feature was switched on. Returns posts sent per channel."""
    cmap = load_map() if cmap is None else cmap
    if not cmap:
        return {}
    con.executescript(SCHEMA)
    since = db.get_meta(con, "city_pub_since")
    if not since:  # first run: start from now, so the channels don't get thousands of old ads at once
        db.set_meta(con, "city_pub_since", db.now())
        con.commit()
        return {}
    today = date.today().isoformat()
    queue: list[tuple[str, str, int, str]] = []  # (channel, kind, id, text)
    jobs = con.execute(
        "SELECT * FROM jobs WHERE status='live' AND first_seen >= ? AND expires_on >= ? "
        "AND (deadline IS NULL OR deadline='' OR deadline >= ?) ORDER BY first_seen", (since, today, today)).fetchall()
    for row in jobs:
        job = db.row_to_dict(con, row)
        for ch in targets_for(job.get("cities") or [], job.get("country") or "sy", cmap):
            if not _posted(con, ch, "job", row["id"]):
                queue.append((ch, "job", row["id"], job_post(job)))
    sent: dict[str, int] = {}
    broken: set[str] = set()
    for ch, kind, item_id, text in queue:
        if ch in broken or sent.get(ch, 0) >= MAX_PER_CHANNEL:
            continue
        if sent:
            sleep(GAP_SECONDS)
        res = tg.call("sendMessage", chat_id=ch, text=text, parse_mode="HTML", disable_web_page_preview=True)
        if not res:  # not an admin, wrong name, or rate-limited: try this channel again next run
            broken.add(ch)
            continue
        con.execute("INSERT OR IGNORE INTO channel_posts VALUES (?,?,?,?)", (ch, kind, item_id, db.now()))
        con.commit()
        sent[ch] = sent.get(ch, 0) + 1
    if broken:
        log.warning("city channels skipped this run (check the bot is admin): %s", ", ".join(sorted(broken)))
    return sent


def check(tg: Telegram, cmap: dict[str, str]) -> list[tuple[str, str, str]]:
    """For each configured channel: can the bot post there?"""
    me = tg.call("getMe") or {}
    out = []
    for key, ch in cmap.items():
        chat = tg.call("getChat", chat_id=ch)
        if not chat:
            out.append((key, ch, "ما لقيت القناة (الاسم غلط أو خاصة)"))
            continue
        member = tg.call("getChatMember", chat_id=ch, user_id=me.get("id")) or {}
        ok = member.get("status") in ("administrator", "creator") and member.get("can_post_messages", True)
        out.append((key, ch, "تمام" if ok else "البوت مو أدمن أو ما معه صلاحية النشر"))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("حط TELEGRAM_BOT_TOKEN أول")
    tg = Telegram(config.TELEGRAM_BOT_TOKEN)
    cmap = load_map()
    if not cmap:
        raise SystemExit('حط CITY_CHANNELS، مثلاً: {"دمشق": "@laqeet_damascus", "*sy": "@laqeet_syria"}')
    if "--check" in sys.argv:
        for key, ch, status in check(tg, cmap):
            print(f"{key:>12}  {ch:<28} {status}")
    else:
        print(publish(db.connect(), tg, cmap))
