"""Accounts for the mobile app: sign-up, verification, login, sessions, reset, profile, saved jobs, deletion."""
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import accounts as acc
from app import api, api_v1, db, mailer
from app.models import Job

client = TestClient(api.app)
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def fresh_limits():
    api_v1._RL = None  # each test starts with clean rate limits
    mailer.OUTBOX.clear()
    yield


def _register(address="rami@example.com", **kw):
    body = {"first_name": "رامي", "last_name": "الحلبي", "gender": "male", "email": address,
            "password": "Sham-2026-jobs", "accepted_terms": True, **kw}
    return client.post("/api/v1/auth/register", json=body)


def _code():
    import re
    return re.search(r"\b(\d{6})\b", mailer.OUTBOX[-1]["text"]).group(1)


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _cleanup(email):
    con = db.raw_connect()
    row = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row:
        for t in ("user_jobs", "user_profiles", "refresh_tokens", "email_codes"):
            con.execute(f"DELETE FROM {t} WHERE user_id=?", (row["id"],))
        con.execute("DELETE FROM users WHERE id=?", (row["id"],))
        con.commit()
    con.close()


def test_register_verify_login_flow():
    email = "flow@example.com"
    _cleanup(email)
    r = _register(email, first_name="  رامي   ", email="  Flow@Example.COM ")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["user"]["first_name"] == "رامي" and s["user"]["email"] == email and not s["user"]["email_verified"]
    assert s["access_token"] and s["refresh_token"] and s["token_type"] == "bearer"
    # the code went by email, never in the response
    assert mailer.OUTBOX and mailer.OUTBOX[-1]["to"] == email and _code() not in r.text
    wrong = client.post("/api/v1/auth/verify-email", json={"code": "000000" if _code() != "000000" else "111111"},
                        headers=_auth(s["access_token"]))
    assert wrong.status_code == 400 and wrong.json()["field"] == "code"
    ok = client.post("/api/v1/auth/verify-email", json={"code": _code()}, headers=_auth(s["access_token"]))
    assert ok.status_code == 200 and ok.json()["email_verified"] is True
    # the database never holds the password or the code in clear
    con = db.raw_connect()
    u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    assert "Sham-2026-jobs" not in u["password_hash"] and u["password_hash"].startswith("scrypt$")
    con.close()
    li = client.post("/api/v1/auth/login", json={"email": "FLOW@example.com", "password": "Sham-2026-jobs"})
    assert li.status_code == 200 and li.json()["user"]["email_verified"] is True
    _cleanup(email)


@pytest.mark.parametrize("field,value,err_field", [
    ("first_name", "R", "first_name"), ("first_name", "رامي123", "first_name"), ("last_name", "", "last_name"),
    ("gender", "other", "gender"), ("email", "not-an-email", "email"), ("email", "a@b", "email"),
    ("password", "short", "password"), ("password", "12345678", "password"), ("password", "aaaaaaaaaa", "password"),
    ("accepted_terms", False, "accepted_terms"),
])
def test_register_validation(field, value, err_field):
    r = _register("val@example.com", **{field: value})
    assert r.status_code == 400 and r.json()["field"] == err_field and r.json()["detail"]


def test_duplicate_email_and_login_does_not_leak_accounts():
    email = "dup@example.com"
    _cleanup(email)
    assert _register(email).status_code == 201
    dup = _register(email.upper())
    assert dup.status_code == 409 and dup.json()["code"] == "email_taken"
    a = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})
    b = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert a.status_code == b.status_code == 401 and a.json()["detail"] == b.json()["detail"]
    _cleanup(email)


def test_refresh_rotates_and_reuse_ends_all_sessions():
    email = "rot@example.com"
    _cleanup(email)
    s = _register(email).json()
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert r1.status_code == 200 and r1.json()["refresh_token"] != s["refresh_token"]
    # the old one again = stolen? every session ends
    again = client.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]})
    assert again.status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "made-up"}).status_code == 401
    _cleanup(email)


def test_logout_and_bad_tokens():
    email = "out@example.com"
    _cleanup(email)
    s = _register(email).json()
    assert client.get("/api/v1/me", headers=_auth(s["access_token"])).status_code == 200
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers=_auth(s["access_token"] + "x")).status_code == 401
    forged = s["access_token"].rsplit(".", 1)[0] + ".AAAA"
    assert client.get("/api/v1/me", headers=_auth(forged)).status_code == 401
    client.post("/api/v1/auth/logout", json={"refresh_token": s["refresh_token"]})
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": s["refresh_token"]}).status_code == 401
    _cleanup(email)


def test_access_token_expiry(monkeypatch):
    email = "exp@example.com"
    _cleanup(email)
    s = _register(email).json()
    user_id = s["user"]["id"]
    old = acc.make_access_token(user_id, 1, now=int(time.time()) - 3600)
    assert client.get("/api/v1/me", headers=_auth(old)).status_code == 401
    _cleanup(email)


def test_forgot_and_reset_password():
    email = "reset@example.com"
    _cleanup(email)
    s = _register(email).json()
    mailer.OUTBOX.clear()
    con = db.raw_connect()
    con.execute("UPDATE email_codes SET created_at='2000-01-01T00:00:00+00:00'")  # skip the 60 s wait
    con.commit()
    con.close()
    same = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
    r = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert r.status_code == same.status_code == 200 and r.json() == same.json()
    code = _code()
    weak = client.post("/api/v1/auth/reset-password", json={"email": email, "code": code, "new_password": "123"})
    assert weak.status_code == 400 and weak.json()["field"] == "password"
    ok = client.post("/api/v1/auth/reset-password", json={"email": email, "code": code, "new_password": "New-Pass-2026"})
    assert ok.status_code == 200
    # old password and old sessions are gone, the new password works, the code can't be reused
    assert client.get("/api/v1/me", headers=_auth(s["access_token"])).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "Sham-2026-jobs"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "New-Pass-2026"}).status_code == 200
    reuse = client.post("/api/v1/auth/reset-password", json={"email": email, "code": code, "new_password": "Other-Pass-2026"})
    assert reuse.status_code == 400
    _cleanup(email)


def test_code_attempts_are_limited():
    email = "brute@example.com"
    _cleanup(email)
    s = _register(email).json()
    real = _code()
    wrong = "123456" if real != "123456" else "654321"
    for _ in range(acc.CODE_MAX_ATTEMPTS):
        client.post("/api/v1/auth/verify-email", json={"code": wrong}, headers=_auth(s["access_token"]))
    locked = client.post("/api/v1/auth/verify-email", json={"code": real}, headers=_auth(s["access_token"]))
    assert locked.status_code == 429 and locked.json()["code"] == "code_locked"
    _cleanup(email)


def test_profile_validation_and_update():
    email = "prof@example.com"
    _cleanup(email)
    s = _register(email).json()
    h = _auth(s["access_token"])
    bad = client.patch("/api/v1/me", json={"profile": {"education": "phd-ish"}}, headers=h)
    assert bad.status_code == 400 and bad.json()["field"] == "education"
    bad_city = client.patch("/api/v1/me", json={"profile": {"education": "none", "cities": ["باريس"]}}, headers=h)
    assert bad_city.status_code == 400 and bad_city.json()["field"] == "cities"
    ok = client.patch("/api/v1/me", json={"first_name": "سامي", "profile": {"education": "bachelor", "field": "computer",
                                          "cities": ["حلب", "حلب", "دمشق"], "experience_years": "3", "birth_year": 1998}}, headers=h)
    assert ok.status_code == 200, ok.text
    p = ok.json()["profile"]
    assert ok.json()["first_name"] == "سامي" and p["cities"] == ["حلب", "دمشق"] and p["experience_years"] == 3 and p["complete"]
    tr = client.patch("/api/v1/me", json={"profile": {"education": "none", "country": "tr", "cities": ["إسطنبول"]}}, headers=h)
    assert tr.status_code == 200 and tr.json()["profile"]["country"] == "tr"
    _cleanup(email)


def test_saved_jobs_and_feed():
    email = "jobs@example.com"
    _cleanup(email)
    jid, _ = db.upsert(api.con, Job(title="مبرمج تطبيقات فلاتر", source="wazafne", post_url="https://t.me/w/880001",
                                    posted_at=NOW, cities=["حلب"], education="bachelor", field="computer",
                                    apply_url="https://forms.gle/abc"))
    api.con.commit()
    s = _register(email).json()
    h = _auth(s["access_token"])
    client.patch("/api/v1/me", json={"profile": {"education": "bachelor", "field": "computer", "cities": ["حلب"]}}, headers=h)
    feed = client.get("/api/v1/me/feed", headers=h).json()
    assert any(j["id"] == jid for j in feed["jobs"])
    assert client.put(f"/api/v1/me/jobs/{jid}", json={"stage": "saved"}, headers=h).json()["stage"] == "saved"
    up = client.put(f"/api/v1/me/jobs/{jid}", json={"stage": "applied", "note": "بعتت CV"}, headers=h).json()
    assert up["stage"] == "applied" and up["note"] == "بعتت CV"
    items = client.get("/api/v1/me/jobs", headers=h).json()["items"]
    assert items[0]["job"]["title"] == "مبرمج تطبيقات فلاتر" and items[0]["stage"] == "applied"
    assert client.put(f"/api/v1/me/jobs/{jid}", json={"stage": "hired!"}, headers=h).status_code == 400
    assert client.put("/api/v1/me/jobs/999999999", json={"stage": "saved"}, headers=h).status_code == 404
    client.delete(f"/api/v1/me/jobs/{jid}", headers=h)
    assert client.get("/api/v1/me/jobs", headers=h).json()["items"] == []
    _cleanup(email)
    db.delete_jobs(api.con, [jid])
    api.con.commit()


def test_change_password_ends_other_sessions_and_delete_account():
    email = "del@example.com"
    _cleanup(email)
    s = _register(email).json()
    h = _auth(s["access_token"])
    bad = client.post("/api/v1/me/password", json={"current_password": "nope-nope-1", "new_password": "Next-Pass-2026"}, headers=h)
    assert bad.status_code == 401
    ok = client.post("/api/v1/me/password", json={"current_password": "Sham-2026-jobs", "new_password": "Next-Pass-2026"}, headers=h)
    assert ok.status_code == 200
    assert client.get("/api/v1/me", headers=h).status_code == 401  # old token dead
    h2 = _auth(ok.json()["access_token"])
    assert client.post("/api/v1/me/delete", json={"password": "wrong"}, headers=h2).status_code == 401
    assert client.post("/api/v1/me/delete", json={"password": "Next-Pass-2026"}, headers=h2).status_code == 200
    con = db.raw_connect()
    assert con.execute("SELECT COUNT(*) FROM users WHERE email=?", (email,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM refresh_tokens WHERE user_id=?", (s["user"]["id"],)).fetchone()[0] == 0
    con.close()
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "Next-Pass-2026"}).status_code == 401


def test_rate_limits_on_login():
    codes = [client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "whatever-1"},
                         headers={"X-Forwarded-For": "203.0.113.50"}).status_code for _ in range(12)]
    assert codes[0] == 401 and 429 in codes


def test_app_config_and_admin_numbers():
    cfg = client.get("/api/v1/config").json()
    assert [e["code"] for e in cfg["education"]][0] == "none" and cfg["genders"][1]["code"] == "female"
    assert "حلب" in cfg["countries"][0]["cities"]
    assert client.get("/api/admin/users").status_code == 403
    n = client.get("/api/admin/users", headers={"X-Admin-Token": "test-token"}).json()
    assert "users" in n and "by_education" in n and "email" not in str(n)


def test_backup_keeps_accounts_profiles_and_saved_jobs(tmp_path):
    from app import backup
    src = db.connect(":memory:")
    acc.ensure_schema(src)
    jid, _ = db.upsert(src, Job(title="محاسب", source="wazafne", post_url="https://t.me/w/1", posted_at=NOW, cities=["دمشق"]))
    s = acc.register(src, first_name="هبة", last_name="الشامي", gender="female", email="heba@example.com",
                     password="Heba-Pass-2026", accepted_terms=True)
    acc.save_profile(src, s["user"]["id"], {"education": "bachelor", "cities": ["دمشق"]})
    acc.set_job_stage(src, s["user"]["id"], jid, "applied")
    from app import alerts
    alerts.save_settings(src, s["user"]["id"], mode="instant", daily_hour=8)
    src.commit()
    path = tmp_path / "b.json.gz"
    backup.write_file(backup.export_data(src), path)
    dst = db.connect(":memory:")
    db.upsert(dst, Job(title="مدير", source="wazafne", post_url="https://t.me/w/2", posted_at=NOW, cities=["حلب"]))
    db.upsert(dst, Job(title="محاسب", source="wazafne", post_url="https://t.me/w/1", posted_at=NOW, cities=["دمشق"]))
    dst.commit()
    backup.import_data(dst, backup.read_file(path))
    u = dst.execute("SELECT * FROM users WHERE email='heba@example.com'").fetchone()
    assert u and acc.verify_password("Heba-Pass-2026", u["password_hash"])
    assert dst.execute("SELECT education FROM user_profiles WHERE user_id=?", (u["id"],)).fetchone()[0] == "bachelor"
    saved = dst.execute("SELECT j.title FROM user_jobs uj JOIN jobs j ON j.id=uj.job_id WHERE uj.user_id=?", (u["id"],)).fetchone()
    assert saved[0] == "محاسب"
    assert tuple(dst.execute("SELECT mode, daily_hour FROM user_alerts WHERE user_id=?", (u["id"],)).fetchone()) == ("instant", 8)
    backup.import_data(dst, backup.read_file(path))  # twice: no duplicates
    assert dst.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_seed_snapshot_never_contains_the_signing_key(tmp_path):
    import gzip
    from app import seed
    src = db.connect(":memory:")
    acc.ensure_schema(src)  # creates dev_secret_key in meta when SECRET_KEY is unset
    db.set_meta(src, "dev_secret_key", "SUPER-SECRET-VALUE")
    src.commit()
    seed.export_seed(src, tmp_path / "s.jsonl.gz")
    body = gzip.open(tmp_path / "s.jsonl.gz", "rt", encoding="utf-8").read()
    assert "SUPER-SECRET-VALUE" not in body and "dev_secret_key" not in body


def test_legal_pages_linked_from_the_app_exist():
    """The app's sign-up screen and the stores link to these; they must describe what is actually stored."""
    for page, must in (("/privacy.html", ["احذف حسابي", "ما منبيع"]), ("/terms.html", ["مجانية", "ما بيطلب مصاري"])):
        r = client.get(page)
        assert r.status_code == 200
        for text in must:
            assert text in r.text


def test_brevo_api_is_used_when_configured(monkeypatch):
    """Free Render blocks SMTP ports; codes go through Brevo's HTTPS API instead."""
    import httpx
    from app import config
    sent = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        sent.update(url=url, body=json, headers=headers)
        return httpx.Response(201, json={"messageId": "x"})

    monkeypatch.setattr(config, "BREVO_API_KEY", "k-123")
    monkeypatch.setattr(config, "MAIL_FROM", "codes@example.com")
    monkeypatch.setattr(httpx, "post", fake_post)
    subject, text, html = mailer.code_email("<b>سارة</b>", "123456", "verify")
    assert mailer.send("a@b.co", subject, text, html) is True
    assert sent["url"] == mailer.BREVO_URL and sent["headers"]["api-key"] == "k-123"
    assert sent["body"]["to"] == [{"email": "a@b.co"}] and sent["body"]["sender"]["email"] == "codes@example.com"
    assert "123456" in sent["body"]["textContent"]
    assert "<b>سارة</b>" not in sent["body"]["htmlContent"]  # the name is escaped in the HTML
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, text="bad key"))
    assert mailer.send("a@b.co", subject, text, html) is False


def test_profile_takes_several_degrees_fields_and_cities_and_nothing_is_required():
    email = "multi@example.com"
    _cleanup(email)
    s = _register(email).json()
    h = _auth(s["access_token"])
    assert s["user"]["profile"]["complete"] is False
    skipped = client.patch("/api/v1/me", json={"profile": {}}, headers=h).json()["profile"]
    assert skipped["complete"] is True and skipped["educations"] == [] and skipped["education"] is None  # «تخطّي»
    many = client.patch("/api/v1/me", headers=h, json={"profile": {
        "educations": ["bachelor", "secondary", "bachelor"], "fields": ["computer", "business"],
        "cities": ["دمشق", "حلب", "حمص", "حماة", "طرطوس", "اللاذقية", "درعا"]}}).json()["profile"]
    assert many["educations"] == ["secondary", "bachelor"] and many["education"] == "bachelor"  # highest
    assert many["fields"] == ["computer", "business"] and many["field"] == "computer"
    assert len(many["cities"]) == 7
    bad = client.patch("/api/v1/me", headers=h, json={"profile": {"educations": ["bachelor", "x"]}})
    assert bad.status_code == 400 and bad.json()["field"] == "education"
    _cleanup(email)


def test_alerts_match_any_of_several_choices():
    from app import alerts
    prof = {"country": "sy", "education": "bachelor", "field": "computer", "cities": "|دمشق|حلب|",
            "educations": "|secondary|bachelor|", "fields": "|computer|business|"}
    job = lambda **k: {"country": "sy", "education": "secondary", "field": "business", "cities": ["حلب"], **k}
    assert alerts.matches(prof, job())
    assert alerts.matches(prof, job(education=None, field="computer"))
    assert not alerts.matches(prof, job(education="master"))
    assert not alerts.matches(prof, job(field="health"))
    assert not alerts.matches(prof, job(cities=["حمص"]))
    assert not alerts.matches(prof, job(country="tr"))
    nothing = {"country": "sy", "education": None, "field": None, "cities": "", "educations": "", "fields": ""}
    assert alerts.matches(nothing, job(education="phd", field="health", cities=["درعا"]))  # skipped = everything
