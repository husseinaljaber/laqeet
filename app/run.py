"""One process for the background work: collect every UPDATE_MINUTES, then send bot alerts; the bot answers people
in a thread alongside.   python -m app.run"""
from __future__ import annotations

import logging
import threading
import time

from . import alerts, bot, city_channels, config, db
from .collect import collect

log = logging.getLogger("run")


def health_alert(con, tg) -> bool:
    """At most once a day, tell the admin on Telegram which sources failed or went quiet."""
    if not config.ADMIN_CHAT_ID:
        return False
    today = time.strftime("%Y-%m-%d")
    if db.get_meta(con, "health_alert_day") == today:
        return False
    bad = [r for r in db.health_report(con) if r["status"] != "ok"]
    if not bad:
        return False
    lines = [f"⚠️ {len(bad)} مصدر بحاجة لنظرة:"]
    for r in bad[:25]:
        why = "أخطاء متتالية" if r["status"] == "fail" else (f"ما جاب شي من {r['quiet_days']:.0f} يوم" if r["quiet_days"] is not None else "ما جاب شي أبداً")
        lines.append(f"• {r['label']}: {why}")
    lines.append("التفاصيل: /admin.html")
    tg.call("sendMessage", chat_id=config.ADMIN_CHAT_ID, text="\n".join(lines))
    db.set_meta(con, "health_alert_day", today)
    con.commit()
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    tg = bot.Telegram(config.TELEGRAM_BOT_TOKEN) if config.TELEGRAM_BOT_TOKEN else None
    if tg:
        threading.Thread(target=bot.poll, args=(db.connect(), tg), daemon=True).start()
    con = db.connect()
    while True:
        t0 = time.time()
        try:
            log.info("collected: %s", collect())
            log.info("app alerts: %s", alerts.run(con, tg=tg))
            if tg:
                log.info("alerts sent: %d", bot.notify(con, tg))
                if posted := city_channels.publish(con, tg):
                    log.info("city channels: %s", posted)
                health_alert(con, tg)
        except Exception:
            log.exception("run failed; trying again next round")
        time.sleep(max(60, config.UPDATE_MINUTES * 60 - (time.time() - t0)))


if __name__ == "__main__":
    main()
