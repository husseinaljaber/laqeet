"""One process for the background work: collect every UPDATE_MINUTES, then send bot alerts; the bot answers people
in a thread alongside.   python -m app.run"""
from __future__ import annotations

import logging
import threading
import time

from . import bot, config, db
from .collect import collect

log = logging.getLogger("run")


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
            if tg:
                log.info("alerts sent: %d", bot.notify(con, tg))
        except Exception:
            log.exception("run failed; trying again next round")
        time.sleep(max(60, config.UPDATE_MINUTES * 60 - (time.time() - t0)))


if __name__ == "__main__":
    main()
