"""Telegram alert bot: people pick their education, specialty and cities with buttons, then get each new matching job once.

    TELEGRAM_BOT_TOKEN=... python -m app.bot          # answer people (long polling)
The scheduler (app/run.py) calls notify() after each collection run.
Only the choices people make with the buttons are stored (chat id, education, field, cities), nothing else.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from . import config, db
from . import taxonomy as tx

log = logging.getLogger("bot")
API = "https://api.telegram.org/bot{token}/{method}"
NEEDS_FIELD = {"institute", "bachelor", "master", "phd"}
FIELD_CHOICES = ["computer", "engineering", "health", "business", "education", "media", "law", "ngo", "sales"]
MAX_PER_RUN = 8


class Telegram:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.token, self.client = token, client or httpx.Client(timeout=70)

    def call(self, method: str, **params):
        r = self.client.post(API.format(token=self.token, method=method), json=params)
        data = r.json()
        if not data.get("ok"):
            log.warning("%s: %s", method, data.get("description"))
        return data.get("result")


def _kb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def _pairs(items):
    return [items[i:i + 2] for i in range(0, len(items), 2)]


def edu_keyboard():
    return _kb(_pairs([(label, f"edu:{code}") for code, label in reversed(tx.EDUCATION)]))


def field_keyboard():
    return _kb(_pairs([(tx.FIELD_LABEL[f], f"field:{f}") for f in FIELD_CHOICES]) + [[("كل الاختصاصات", "field:")]])


def city_keyboard(chosen: list[str]):
    btn = [(("✓ " if c in chosen else "") + c, f"city:{c}") for c in tx.CITIES]
    return _kb(_pairs(btn) + [[("كل سوريا", "city:*"), ("تم ✅", "done")]])


def _user(con, chat_id):
    row = con.execute("SELECT * FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        con.execute("INSERT INTO bot_users (chat_id, created, active) VALUES (?,?,0)", (chat_id, db.now()))
        row = con.execute("SELECT * FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
    return row


def handle(con, tg: Telegram, update: dict) -> None:
    """One update from Telegram: a command or a button press."""
    if msg := update.get("message"):
        chat, text = msg["chat"]["id"], (msg.get("text") or "").strip()
        if text.startswith("/stop"):
            con.execute("UPDATE bot_users SET active=0 WHERE chat_id=?", (chat,))
            tg.call("sendMessage", chat_id=chat, text="وقّفنا التنبيهات. إذا بدك ترجع ابعت /start")
        elif text.startswith(("/start", "/settings")):
            _user(con, chat)
            tg.call("sendMessage", chat_id=chat, reply_markup=edu_keyboard(),
                    text=f"أهلا فيك بـ«{config.SITE_NAME}» 👋\nمنبعتلك كل شغل جديد بيناسبك، مرة وحدة بس.\n\nشو شهادتك؟")
        elif text.startswith("/jobs"):
            sent = notify_one(con, tg, chat, fresh_only=False)
            if not sent:
                tg.call("sendMessage", chat_id=chat, text="ما في شغل جديد بيناسبك هلأ. منبعتلك أول ما ينزل شي.")
        else:
            tg.call("sendMessage", chat_id=chat, text="الأوامر: /settings لتغيّر اختياراتك، /jobs لآخر الشغل، /stop لتوقف التنبيهات.")
        con.commit()
        return
    cb = update.get("callback_query")
    if not cb:
        return
    chat, data, mid = cb["message"]["chat"]["id"], cb.get("data", ""), cb["message"]["message_id"]
    user = _user(con, chat)
    tg.call("answerCallbackQuery", callback_query_id=cb["id"])
    if data.startswith("edu:"):
        edu = data[4:]
        con.execute("UPDATE bot_users SET education=?, field=NULL, cities='' WHERE chat_id=?", (edu, chat))
        if edu in NEEDS_FIELD:
            tg.call("editMessageText", chat_id=chat, message_id=mid, text="شو اختصاصك؟", reply_markup=field_keyboard())
        else:
            tg.call("editMessageText", chat_id=chat, message_id=mid, text="وين بدك تشتغل؟ اختار مدينة أو أكتر، وبعدين «تم».",
                    reply_markup=city_keyboard([]))
    elif data.startswith("field:"):
        con.execute("UPDATE bot_users SET field=? WHERE chat_id=?", (data[6:] or None, chat))
        tg.call("editMessageText", chat_id=chat, message_id=mid, text="وين بدك تشتغل؟ اختار مدينة أو أكتر، وبعدين «تم».",
                reply_markup=city_keyboard([]))
    elif data.startswith("city:"):
        city = data[5:]
        chosen = [c for c in (user["cities"] or "").split("|") if c]
        chosen = [] if city == "*" else (sorted(set(chosen) ^ {city}))
        con.execute("UPDATE bot_users SET cities=? WHERE chat_id=?", ("|".join(chosen), chat))
        tg.call("editMessageReplyMarkup", chat_id=chat, message_id=mid, reply_markup=city_keyboard(chosen))
    elif data == "done":
        con.execute("UPDATE bot_users SET active=1 WHERE chat_id=?", (chat,))
        u = _user(con, chat)
        cities = (u["cities"] or "").replace("|", "، ") or "كل سوريا"
        what = " · ".join(x for x in [tx.EDU_LABEL.get(u["education"], ""), tx.FIELD_LABEL.get(u["field"] or "", ""), cities] if x)
        tg.call("editMessageText", chat_id=chat, message_id=mid,
                text=f"تمام ✅ ({what})\nمنبعتلك كل شغل جديد بيناسبك.\n/jobs آخر الشغل · /settings تغيير · /stop توقيف")
    con.commit()


def matches(user, job: dict) -> bool:
    """Same rules as the website: your level (a degree holder also sees lower-level formal jobs only if they ask)."""
    edu = user["education"]
    if edu:
        need = job.get("education")
        if job.get("kind") == "quick" or edu in ("none", "basic", "secondary"):
            if need and tx.EDU_RANK[need] > tx.EDU_RANK[edu]:
                return False
        elif need and need not in ({"phd": ["phd", "master", "bachelor"], "master": ["master", "bachelor"]}.get(edu, [edu])):
            return False
    if user["field"] and job.get("field") != user["field"]:
        return False
    cities = [c for c in (user["cities"] or "").split("|") if c]
    if cities and job.get("cities") and not set(cities) & set(job["cities"]):
        return False
    return True


def message(job: dict) -> str:
    lines = [("🏛 رسمي · " if job.get("official") else "") + f"<b>{_h(job['title'])}</b>"]
    where = " · ".join(x for x in [job.get("employer"), "، ".join(job.get("cities") or [])] if x)
    if where:
        lines.append(_h(where))
    bits = []
    if job.get("education"):
        bits.append(tx.EDU_LABEL[job["education"]])
    if job.get("experience") is not None:
        bits.append(f"خبرة {job['experience']} سنين")
    if job.get("deadline"):
        bits.append(f"آخر موعد {job['deadline']}")
    if bits:
        lines.append(" · ".join(bits))
    links = []
    if job.get("apply_url"):
        links.append(f'<a href="{_h(job["apply_url"])}">قدّم</a>')
    if job.get("country") == "tr":
        phones = [p for p in job.get("phones") or [] if p.startswith("05")]
        if phones:
            links.append(f'<a href="https://wa.me/90{phones[0][1:]}">واتساب</a>')
    else:
        phones = [p for p in job.get("phones") or [] if p.startswith("09")]
        if phones:
            links.append(f'<a href="https://wa.me/963{phones[0][1:]}">واتساب</a>')
    if job.get("email"):
        links.append(f'<a href="mailto:{_h(job["email"])}">إيميل</a>')
    for s in job.get("sources", [])[:2]:
        links.append(f'<a href="{_h(s["post_url"])}">الإعلان على {_h(s["label"])}</a>')
    links.append(f'<a href="{config.PUBLIC_URL}/#job-{job["id"]}">على الموقع</a>')
    lines.append(" · ".join(links))
    return "\n".join(lines)


def _h(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def notify_one(con, tg: Telegram, chat_id: int, fresh_only: bool = True) -> int:
    user = con.execute("SELECT * FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
    if not user or not user["active"]:
        return 0
    since = user["created"] if fresh_only else "0"
    rows = con.execute(
        """SELECT * FROM jobs WHERE status='live' AND expires_on >= date('now') AND first_seen >= ?
           AND id NOT IN (SELECT job_id FROM bot_sent WHERE chat_id=?) ORDER BY posted_at DESC LIMIT 400""",
        (since, chat_id)).fetchall()
    sent = 0
    for row in rows:
        job = db.row_to_dict(con, row)
        if not matches(user, job):
            continue
        tg.call("sendMessage", chat_id=chat_id, text=message(job), parse_mode="HTML", disable_web_page_preview=True)
        con.execute("INSERT OR IGNORE INTO bot_sent VALUES (?,?,?)", (chat_id, row["id"], db.now()))
        sent += 1
        if sent >= MAX_PER_RUN:
            break
    con.commit()
    return sent


def notify(con, tg: Telegram) -> int:
    """Send every active user the new jobs that match them (each job once)."""
    total = 0
    for u in con.execute("SELECT chat_id FROM bot_users WHERE active=1").fetchall():
        total += notify_one(con, tg, u["chat_id"])
        time.sleep(0.05)  # Telegram allows ~30 messages a second
    return total


def poll(con, tg: Telegram) -> None:
    offset = int(db.get_meta(con, "bot_offset", 0))
    while True:
        try:
            updates = tg.call("getUpdates", offset=offset, timeout=50) or []
        except httpx.HTTPError as e:
            log.warning("getUpdates: %s", e)
            time.sleep(5)
            continue
        for u in updates:
            try:
                handle(con, tg, u)
            except Exception:
                log.exception("update %s", json.dumps(u)[:300])
            offset = u["update_id"] + 1
            db.set_meta(con, "bot_offset", offset)
            con.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("حط TELEGRAM_BOT_TOKEN (من @BotFather) وجرّب كمان مرة")
    poll(db.connect(), Telegram(config.TELEGRAM_BOT_TOKEN))
