"""App alerts: who gets a notification, when, and only once."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import accounts as acc, alerts, api, bot, config, db, mailer
from app.models import Job

client = TestClient(api.app)
_IDS: list[int] = []


def _user(email, edu="secondary", cities=("حلب",), country="sy", field=None, created_days_ago=3):
    """A verified account with a complete profile, created a few days ago (so today's jobs are «new» for it)."""
    u = acc.register(api.con, first_name="سارة", last_name="علي", gender="female", email=email,
                     password="Yasmin-2026x", accepted_terms=True)
    uid = u["user"]["id"]
    past = (datetime.now(timezone.utc) - timedelta(days=created_days_ago)).isoformat()
    api.con.execute("UPDATE users SET email_verified_at=?, created_at=? WHERE id=?", (past, past, uid))
    acc.save_profile(api.con, uid, {"education": edu, "cities": list(cities), "country": country, "field": field})
    api.con.commit()
    return uid, u["access_token"]


def _job(title, **kw):
    jid, _ = db.upsert(api.con, Job(title=title, source="wazafne", post_url=f"https://t.me/al/{abs(hash(title)) % 10**7}",
                                    posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), **kw))
    _IDS.append(jid)
    api.con.commit()
    return jid


def teardown_module():
    for jid in _IDS:
        for t in ("job_sources", "jobs_fts"):
            api.con.execute(f"DELETE FROM {t} WHERE job_id=?", (jid,))
        api.con.execute("DELETE FROM jobs WHERE id=?", (jid,))
    api.con.commit()


class FakeTG:
    def __init__(self):
        self.sent = []

    def call(self, method, **p):
        self.sent.append((method, p))
        return {}


def _at(hour):
    """A moment today at `hour` Damascus time."""
    local = datetime.now(alerts.LOCAL).replace(hour=hour, minute=5, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def test_daily_summary_once_with_matching_jobs_only():
    uid, _ = _user("daily@example.com")
    alerts.register_device(api.con, uid, "tok-daily-" + "x" * 30, "android")
    fit = _job("كاشير صالة", cities=["حلب"], education="secondary")
    _job("مهندس برمجيات أول", cities=["حلب"], education="master")       # above her level
    _job("كاشير مطعم", cities=["اللاذقية"], education="none")             # other city
    alerts.OUTBOX.clear()
    assert alerts.run(api.con, now=_at(10))["push"] == 0                   # before her hour (19:00)
    res = alerts.run(api.con, now=_at(19))
    mine = [m for m in alerts.OUTBOX if m["token"].startswith("tok-daily")]
    assert res["push"] >= 1 and len(mine) == 1
    assert "كاشير صالة" in mine[0]["body"] and "مهندس" not in mine[0]["body"] and "مطعم" not in mine[0]["body"]
    assert mine[0]["route"] == f"/job/{fit}"
    alerts.OUTBOX.clear()
    alerts.run(api.con, now=_at(20))                                       # same day: nothing again
    assert not [m for m in alerts.OUTBOX if m["token"].startswith("tok-daily")]


def test_instant_is_quiet_at_night_and_each_job_goes_once():
    uid, _ = _user("instant@example.com", cities=("طرطوس",))
    alerts.save_settings(api.con, uid, mode="instant")
    alerts.register_device(api.con, uid, "tok-inst-" + "y" * 30, "android")
    _job("ممرض/ة", cities=["طرطوس"], education="institute")
    _job("سائق توصيل", cities=["طرطوس"], education="none")
    alerts.OUTBOX.clear()
    alerts.run(api.con, now=_at(23))
    assert not [m for m in alerts.OUTBOX if m["token"].startswith("tok-inst")]  # 11pm: quiet
    alerts.run(api.con, now=_at(9))
    mine = [m for m in alerts.OUTBOX if m["token"].startswith("tok-inst")]
    assert len(mine) == 1 and mine[0]["title"] == "وظيفة جديدة على قد شهادتك"  # one push, one job
    assert "سائق توصيل" in mine[0]["body"] and "ممرض" not in mine[0]["body"]  # ممرض needs an institute degree
    alerts.OUTBOX.clear()
    alerts.run(api.con, now=_at(10))
    assert not [m for m in alerts.OUTBOX if m["token"].startswith("tok-inst")]


def test_off_means_nothing_and_turkey_jobs_dont_reach_syria_profiles():
    uid, _ = _user("off@example.com", cities=("حمص",))
    alerts.register_device(api.con, uid, "tok-off-" + "z" * 30)
    alerts.save_settings(api.con, uid, mode="off")
    _job("عامل مستودع", cities=["حمص"], education="none")
    uid2, _ = _user("tr@example.com", cities=(), country="sy")
    alerts.register_device(api.con, uid2, "tok-sy-" + "w" * 30)
    _job("عامل مصنع", cities=["إسطنبول"], country="tr", education="none")
    alerts.OUTBOX.clear()
    alerts.run(api.con, now=_at(21))
    assert not [m for m in alerts.OUTBOX if m["token"].startswith("tok-off")]
    assert all("مصنع" not in m["body"] for m in alerts.OUTBOX if m["token"].startswith("tok-sy"))


def test_telegram_link_and_digest(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "laqeet_bot")
    uid, token = _user("tg@example.com", cities=("درعا",))
    h = {"Authorization": f"Bearer {token}"}
    link = client.post("/api/v1/me/telegram/link", headers=h).json()["url"]
    assert link.startswith("https://t.me/laqeet_bot?start=link_")
    tg = FakeTG()
    bot.handle(api.con, tg, {"message": {"chat": {"id": 555}, "text": "/start " + link.split("start=")[1]}})
    assert "ربطنا حسابك" in tg.sent[-1][1]["text"]
    assert client.get("/api/v1/me/alerts", headers=h).json()["telegram_linked"] is True
    # an old or reused link doesn't work
    bot.handle(api.con, tg, {"message": {"chat": {"id": 556}, "text": "/start " + link.split("start=")[1]}})
    assert "انتهى" in tg.sent[-1][1]["text"]
    _job("مدرس رياضيات", cities=["درعا"], education="secondary")
    tg.sent.clear()
    alerts.run(api.con, tg=tg, now=_at(19))
    msgs = [p for m, p in tg.sent if p.get("chat_id") == 555]
    assert msgs and "مدرس رياضيات" in msgs[0]["text"]
    bot.handle(api.con, tg, {"message": {"chat": {"id": 555}, "text": "/stop"}})
    assert client.get("/api/v1/me/alerts", headers=h).json()["telegram_linked"] is False


def test_settings_api_and_device_registration():
    _, token = _user("api@example.com")
    h = {"Authorization": f"Bearer {token}"}
    s = client.get("/api/v1/me/alerts", headers=h).json()
    assert s["mode"] == "daily" and s["daily_hour"] == 19 and 19 in s["hours"]
    assert client.put("/api/v1/me/alerts", headers=h, json={"mode": "instant", "daily_hour": 8}).json()["mode"] == "instant"
    assert client.put("/api/v1/me/alerts", headers=h, json={"mode": "loud"}).status_code == 400
    assert client.put("/api/v1/me/alerts", headers=h, json={"daily_hour": 3}).status_code == 400
    assert client.post("/api/v1/me/devices", headers=h, json={"token": "short"}).status_code == 400
    assert client.post("/api/v1/me/devices", headers=h, json={"token": "d" * 40, "platform": "android"}).json()["ok"]
    assert client.get("/api/v1/me/alerts", headers=h).json()["push_devices"] == 1
    client.post("/api/v1/me/devices/remove", headers=h, json={"token": "d" * 40})
    assert client.get("/api/v1/me/alerts", headers=h).json()["push_devices"] == 0
    assert client.get("/api/v1/me/alerts").status_code == 401


def test_deleting_the_account_removes_alert_data():
    uid, token = _user("gone@example.com")
    alerts.register_device(api.con, uid, "tok-gone-" + "q" * 30)
    client.post("/api/v1/me/delete", headers={"Authorization": f"Bearer {token}"}, json={"password": "Yasmin-2026x"})
    for t in ("push_devices", "user_alerts", "alert_sent", "telegram_links"):
        assert api.con.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (uid,)).fetchone()[0] == 0
