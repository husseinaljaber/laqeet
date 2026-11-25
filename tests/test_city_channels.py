"""Posting new jobs to per-city Telegram channels."""
from datetime import datetime, timezone

from app import city_channels as cc
from app import db
from app.models import Job

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
CMAP = {"دمشق": "@d", "حلب": "@a", "*sy": "@sy", "*tr": "@tr"}


class FakeTG:
    def __init__(self, fail=()):
        self.sent, self.fail = [], set(fail)

    def call(self, method, **kw):
        if kw.get("chat_id") in self.fail:
            return None
        self.sent.append((kw["chat_id"], kw["text"]))
        return {"message_id": len(self.sent)}


def _db():
    return db.connect(":memory:")


def _job(con, title, cities, country="sy", **kw):
    jid, _ = db.upsert(con, Job(title=title, source="syrianjobs", post_url=f"https://t.me/x/{abs(hash(title)) % 99999}",
                                posted_at=NOW, cities=cities, country=country, phones=["0944111222"], education="none", **kw))
    con.commit()
    return jid


def test_routing():
    assert cc.targets_for(["دمشق", "حلب"], "sy", CMAP) == ["@d", "@a", "@sy"]
    assert cc.targets_for(["عدة محافظات"], "sy", CMAP) == ["@sy"]
    assert cc.targets_for(["إسطنبول"], "tr", CMAP) == ["@tr"]
    assert cc.targets_for(["حمص"], "sy", {"دمشق": "@d"}) == []
    assert cc.load_map("not json") == {} and cc.load_map('{"دمشق": "@d"}') == {"دمشق": "@d"}


def test_first_run_starts_now_then_posts_each_item_once():
    con = _db()
    _job(con, "كاشير قديم", ["دمشق"])  # was there before the feature was switched on
    tg = FakeTG()
    assert cc.publish(con, tg, CMAP, sleep=lambda s: None) == {}  # first run only records the start time
    con.execute("UPDATE meta SET value='2000-01-01' WHERE key='city_pub_since'")  # pretend it started long ago…
    con.execute("UPDATE jobs SET first_seen='1999-01-01'")                        # …but the old job is older still
    _job(con, "معلم شاورما", ["دمشق"])
    con.commit()
    sent = cc.publish(con, tg, CMAP, sleep=lambda s: None)
    assert sent == {"@d": 1, "@sy": 1}
    texts = dict((ch, t) for ch, t in tg.sent)
    assert "معلم شاورما" in texts["@d"] and "/j/" in texts["@d"] and "#دمشق" in texts["@d"]
    assert "كاشير قديم" not in " ".join(texts.values())
    assert cc.publish(con, tg, CMAP, sleep=lambda s: None) == {}  # nothing twice


def test_broken_channel_is_retried_next_run_and_caps_hold(monkeypatch):
    con = _db()
    db.set_meta(con, "city_pub_since", "2000-01-01")
    con.commit()
    monkeypatch.setattr(cc, "MAX_PER_CHANNEL", 2)
    for title, phone in [("عامل مستودع", "0944111201"), ("كاشير", "0944111202"), ("سائق توصيل", "0944111203"), ("محاسب", "0944111204")]:
        db.upsert(con, Job(title=title, source="syrianjobs", post_url=f"https://t.me/x/{phone}", posted_at=NOW,
                           cities=["حلب"], phones=[phone], education="none"))
    con.commit()
    tg = FakeTG(fail={"@a"})
    assert cc.publish(con, tg, CMAP, sleep=lambda s: None) == {"@sy": 2}
    tg.fail.clear()
    assert cc.publish(con, tg, CMAP, sleep=lambda s: None) == {"@a": 2, "@sy": 2}
    assert cc.publish(con, tg, CMAP, sleep=lambda s: None) == {"@a": 2}
