"""Regressions from the November 2026 audit: craft titles, employer names, and ads that don't name a degree."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, db
from app import taxonomy as tx
from app.models import Job


@pytest.mark.parametrize("title", [
    "معلم سناك غربي الشعلان، دمشق",
    "كوّاء فساتين سهرة (لصق سيليكون وستراس) الشعار، حلب",
    "معلم صاج", "معلم بروستد", "معلم كنافة", "معلم صباغة", "معلم مطبخ شرقي",
    "معلمين قص وخياطة", "مكوجي", "حلاق رجالي",
])
def test_craft_masters_need_no_degree(title):
    assert tx.guess_education(title) == ("none", "title")


@pytest.mark.parametrize("title", ["معلمة صف", "معلم لغة انكليزية", "معلم رياضيات", "مهندس مدني"])
def test_teachers_and_engineers_stay_university(title):
    assert tx.guess_education(title)[0] == "bachelor"


def test_unknown_education_is_not_guessed_as_university():
    assert tx.guess_education("موظف لمحل أدوات منزلية") in ((None, "unknown"), ("none", "title"))
    assert tx.guess_education("Program Officer", default="bachelor") == ("bachelor", "title")
    assert tx.guess_education("وظيفة شاغرة", default="bachelor") == ("bachelor", "source")


def test_body_degree_does_not_override_craft_title():
    assert tx.guess_education("معلم شاورما", "شركة رائدة تضم مهندسين وخريجين جامعيين")[0] == "none"


@pytest.mark.parametrize("bad", ["أجر بالقطعة", "الشعار", "دوام كامل", "الكسوة", "براتب ممتاز"])
def test_pay_words_and_neighbourhoods_are_not_employers(bad):
    assert db._clean_employer_val(bad) == ""


@pytest.mark.parametrize("good", ["معمل شيبس", "شركة البراق", "كافيه في المرجة", "شركة Golden Glass Company"])
def test_real_employers_are_kept(good):
    assert db._clean_employer_val(good) == good


def test_ads_without_a_degree_show_for_every_level():
    client = TestClient(api.app)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jid, _ = db.upsert(api.con, Job(title="موظف مستودع بلا شهادة مذكورة", source="wazafne", post_url="https://t.me/x/990001",
                                    posted_at=now, cities=["حماة"], education=None))
    api.con.commit()
    try:
        for edu in ("bachelor", "none", "institute"):
            titles = [j["title"] for j in client.get("/api/jobs", params={"edu": edu, "city": "حماة"}).json()["jobs"]]
            assert "موظف مستودع بلا شهادة مذكورة" in titles, edu
    finally:  # the API tests share one database
        db.delete_jobs(api.con, [jid])
        api.con.commit()


def test_copy_link_url_is_not_inside_javascript():
    client = TestClient(api.app)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jid, _ = db.upsert(api.con, Job(title="محاسب اختبار رابط", source="wazafne", post_url="https://t.me/x/990002", posted_at=now,
                                    cities=["حمص"], education="bachelor",
                                    apply_url="https://docs.google.com/forms/d/e/abc'+alert(1)+'/viewform"))
    api.con.commit()
    try:
        page = client.get(f"/j/{jid}").text
        assert "writeText('http" not in page and "writeText(this.dataset.copy)" in page
    finally:
        db.delete_jobs(api.con, [jid])
        api.con.commit()


def test_scam_check_is_rate_limited_and_headers_are_set():
    client = TestClient(api.app)
    h = {"X-Forwarded-For": "192.0.2.77"}
    codes = [client.post("/api/scam-check", json={"text": "مطلوب موظفين براتب ممتاز"}, headers=h).status_code for _ in range(32)]
    assert codes[0] == 200 and codes[-1] == 429
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_rate_limit_memory_stays_bounded():
    rl = api._RateLimit(5, 60, max_keys=100)
    for i in range(1000):
        rl.allow(f"k{i}")
    assert len(rl.hits) <= 100


# ---------------------------------------------------------------- phase 1: search engines
def test_job_page_has_jobposting_markup_and_sitemap_lists_it():
    import json as _json
    import re as _re
    client = TestClient(api.app)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jid, _ = db.upsert(api.con, Job(title="محاسب مالي للسيو", source="wazafne", post_url="https://t.me/x/990003", posted_at=now,
                                    employer="شركة النور للتجارة", cities=["حلب"], education="bachelor", experience=2,
                                    shift="دوام كامل", phones=["0933111222"]))
    api.con.commit()
    try:
        page = client.get(f"/j/{jid}").text
        m = _re.search(r'<script type="application/ld\+json">(.*?)</script>', page, _re.S)
        assert m, "JobPosting markup missing"
        ld = _json.loads(m.group(1))
        assert ld["@type"] == "JobPosting" and ld["hiringOrganization"]["name"] == "شركة النور للتجارة"
        assert ld["jobLocation"][0]["address"] == {"@type": "PostalAddress", "addressLocality": "حلب", "addressCountry": "SY"}
        assert ld["employmentType"] == "FULL_TIME" and ld["validThrough"] and ld["datePosted"] == now[:10]
        assert 'rel="canonical"' in page and 'name="robots"' not in page
        sm = client.get("/sitemap.xml")
        assert sm.status_code == 200 and f"/j/{jid}</loc>" in sm.text
        assert "Sitemap:" in client.get("/robots.txt").text
        # A closed job keeps its page for people but drops the markup and asks not to be indexed.
        api.con.execute("UPDATE jobs SET status='expired' WHERE id=?", (jid,))
        api.con.commit()
        closed = client.get(f"/j/{jid}").text
        assert "application/ld+json" not in closed and 'content="noindex"' in closed
        assert f"/j/{jid}</loc>" not in client.get("/sitemap.xml").text
    finally:
        db.delete_jobs(api.con, [jid])
        api.con.commit()


def test_no_markup_without_employer():
    from app import seo
    j = {"id": 1, "title": "عامل", "status": "live", "posted_at": "2026-11-20", "expires_on": "2099-01-01", "country": "sy"}
    assert seo.job_posting(j, "https://x/j/1", "", ["دمشق"], "بدون شهادة") is None
    assert seo.ld_script(None) == ""


# ---------------------------------------------------------------- phase 1: source health
def test_source_health_flags_quiet_and_failing_sources():
    con = db.connect(":memory:")
    j = lambda src: Job(title="x", source=src, post_url="u")
    db.record_batch_health(con, "web_portals", [j("jobinsyria"), j("jobinsyria"), j("worklink")])
    # next run: worklink brings nothing (site changed) and jobinsyria still works
    db.record_batch_health(con, "web_portals", [j("jobinsyria")])
    con.execute("UPDATE source_health SET last_items_at='2026-01-01T00:00:00+00:00' WHERE source='worklink'")
    for _ in range(3):
        db.record_health(con, "SyMOAD", "telegram", 0, "ConnectTimeout: timed out")
    db.record_health(con, "wazafne175", "telegram", 4)
    rep = {r["source"]: r for r in db.health_report(con)}
    assert rep["jobinsyria"]["status"] == "ok" and rep["jobinsyria"]["last_count"] == 1
    assert rep["worklink"]["status"] == "stale" and rep["worklink"]["last_count"] == 0
    assert rep["SyMOAD"]["status"] == "fail" and rep["SyMOAD"]["fail_streak"] == 3
    assert rep["wazafne175"]["status"] == "ok"
    db.record_health(con, "SyMOAD", "telegram", 2)  # recovers
    assert {r["source"]: r for r in db.health_report(con)}["SyMOAD"]["status"] == "ok"


def test_admin_health_endpoint_needs_token():
    client = TestClient(api.app)
    assert client.get("/api/admin/health").status_code == 403
    r = client.get("/api/admin/health", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200 and "sources" in r.json()


def test_health_alert_sends_once_a_day(monkeypatch):
    from app import config, run
    con = db.connect(":memory:")
    for _ in range(3):  # three failed runs in a row
        db.record_health(con, "SyMOAD", "telegram", 0, "boom")
    sent = []

    class TG:
        def call(self, method, **kw):
            sent.append(kw["text"])

    monkeypatch.setattr(config, "ADMIN_CHAT_ID", "123")
    assert run.health_alert(con, TG()) is True
    assert "وزارة التنمية الإدارية" in sent[0]
    assert run.health_alert(con, TG()) is False and len(sent) == 1


# ---------------------------------------------------------------- phase 1: optional AI reader
def test_llm_is_off_without_key_and_rules_stay(monkeypatch):
    from app import config, llm
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    con = db.connect(":memory:")
    rule = [Job(title="جملة طويلة جداً", source="wazafne", post_url="u", confidence=0.4)]
    assert llm.improve(con, "مطلوب موظفين " * 10, rule, source="wazafne", post_url="u", posted_at=None,
                       default_kind="formal", links=[], country="sy", sector=None) is rule


def test_llm_reading_replaces_unsure_rules_and_keeps_contacts_from_text(monkeypatch):
    from app import config, llm
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_DAILY_LIMIT", 2)
    calls = []

    def fake_ask(text):
        calls.append(text)
        return llm.LlmPost(is_job_ad=True, jobs=[
            llm.LlmJob(title="معلم شاورما", employer="مطعم الريم", cities=["حلب"], education="none", field="hospitality",
                       experience_years=2, age_min=None, age_max=None, gender="male", salary=None,
                       deadline=None, kind="quick"),
            llm.LlmJob(title="كاشير", employer="مطعم الريم", cities=["حلب"], education=None, field="retail",
                       experience_years=None, age_min=None, age_max=None, gender=None, salary=None,
                       deadline="2099-01-01", kind="quick"),
        ])

    monkeypatch.setattr(llm, "ask", fake_ask)
    con = db.connect(":memory:")
    text = "مطعم الريم في حلب بحاجة إلى معلم شاورما بخبرة سنتين وكاشير، للتواصل واتس 0944123456"
    jobs = llm.improve(con, text, [], source="syrianjobs", post_url="u", posted_at="2026-11-25",
                       default_kind="quick", links=[], country="sy", sector=None)
    assert [j.title for j in jobs] == ["معلم شاورما", "كاشير"]
    assert jobs[0].phones == ["0944123456"] and jobs[0].whatsapp and jobs[0].education == "none"
    assert jobs[1].education is None and jobs[1].education_from == "unknown"
    # budget: 2 a day, the third unsure post keeps its rule result
    llm.improve(con, text, [], source="syrianjobs", post_url="u", posted_at=None, default_kind="quick",
                links=[], country="sy", sector=None)
    assert llm.improve(con, text, [], source="syrianjobs", post_url="u", posted_at=None, default_kind="quick",
                       links=[], country="sy", sector=None) == []
    assert len(calls) == 2


def test_llm_not_asked_about_confident_posts(monkeypatch):
    from app import config, llm
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(llm, "ask", lambda t: (_ for _ in ()).throw(AssertionError("should not ask")))
    con = db.connect(":memory:")
    sure = [Job(title="محاسب", source="wazafne", post_url="u", confidence=1.0)]
    assert llm.improve(con, "مطلوب محاسب " * 10, sure, source="wazafne", post_url="u", posted_at=None,
                       default_kind="formal", links=[], country="sy", sector=None) is sure


def test_llm_schema_builds_for_structured_output():
    from app import llm
    schema = llm.LlmPost.model_json_schema()
    assert "jobs" in schema["properties"] and "is_job_ad" in schema["required"]


def test_new_source_is_not_stale_on_its_first_quiet_run():
    con = db.connect(":memory:")
    db.record_health(con, "HIAST_Syria", "telegram", 0)
    assert db.health_report(con)[0]["status"] == "ok"


# ---------------------------------------------------------------- phase 2: anonymous counters
def test_events_are_counted_without_storing_people():
    client = TestClient(api.app)
    ua = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130", "X-Forwarded-For": "198.51.100.200"}
    assert client.post("/api/e", json={"e": "view", "k": "board"}, headers=ua).status_code == 200
    client.post("/api/e", json={"e": "apply", "k": "whatsapp", "s": "syrianjobs"}, headers=ua)
    client.post("/api/e", json={"e": "apply", "k": "site", "s": "nope"}, headers=ua)        # unknown source: apply only
    client.post("/api/e", json={"e": "view", "k": "<script>"}, headers=ua)                  # rejected key
    client.post("/api/e", json={"e": "view", "k": "board"}, headers={"User-Agent": "Googlebot/2.1"})  # bots ignored
    stats = client.get("/api/admin/stats", headers={"X-Admin-Token": "test-token"}).json()
    t = stats["totals"]
    assert t["view"]["board"] == 1 and "<script>" not in t["view"]
    assert t["apply"] == {"whatsapp": 1, "site": 1} and t["apply_src"] == {"syrianjobs": 1}
    assert stats["source_labels"]["syrianjobs"]
    cols = {r[1] for r in api.con.execute("PRAGMA table_info(events_daily)")}
    assert cols == {"day", "event", "key", "n"}  # no ip, no user agent, no id
    assert client.get("/api/admin/stats").status_code == 403


# ---------------------------------------------------------------- phase 2: sources & partners
@pytest.fixture
def quick_job():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    jid, _ = db.upsert(api.con, Job(title="عامل مستودع للمصادر", source="syrianjobs", post_url="https://t.me/syrianjobs2/990010",
                                    posted_at=now, cities=["طرطوس"], education="none", kind="quick", phones=["0933555111"]))
    api.con.commit()
    api._stats_cache.clear()
    yield jid
    db.delete_jobs(api.con, [jid])
    api.con.commit()


def test_sources_page_api_credits_each_source(quick_job):
    client = TestClient(api.app)
    by = {s["source"]: s for s in client.get("/api/sources").json()["sources"]}
    assert by["syrianjobs"]["kind"] == "telegram" and by["syrianjobs"]["url"] == "https://t.me/syrianjobs2"
    assert by["syrianjobs"]["open"] >= 1 and by["syrianjobs"]["recent"] >= 1
    assert "employer" not in by


def test_partners_report_needs_token_and_counts_clicks(quick_job):
    client = TestClient(api.app)
    assert client.get("/api/admin/partners").status_code == 403
    ua = {"User-Agent": "Mozilla/5.0 Chrome/130", "X-Forwarded-For": "198.51.100.201"}
    client.post("/api/e", json={"e": "source", "k": "syrianjobs"}, headers=ua)
    client.post("/api/e", json={"e": "apply", "k": "whatsapp", "s": "syrianjobs"}, headers=ua)
    rows = client.get("/api/admin/partners", headers={"X-Admin-Token": "test-token"}).json()["sources"]
    sj = next(r for r in rows if r["source"] == "syrianjobs")
    assert sj["visits_sent"] >= 1 and sj["apply_clicks"] >= 1


# ---------------------------------------------------------------- phase 3: backup & restore
def test_backup_restores_people_data_into_a_rebuilt_database(tmp_path):
    from app import backup
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    src = db.connect(":memory:")
    # a collected ad that an admin rejected, and one that got a report
    bad, _ = db.upsert(src, Job(title="ربح من البيت", source="syrianjobs", post_url="https://t.me/s/1", posted_at=now, cities=["دمشق"]))
    ok, _ = db.upsert(src, Job(title="سائق توصيل", source="syrianjobs", post_url="https://t.me/s/2", posted_at=now, cities=["حمص"]))
    src.execute("UPDATE jobs SET status='rejected' WHERE id=?", (bad,))
    src.execute("UPDATE jobs SET reports=1 WHERE id=?", (ok,))
    src.execute("INSERT INTO reports VALUES (?,?,?)", (ok, "scam", now))
    # an employer post with its management code, a bot user who was sent the ok ad, and a counter
    emp, _ = db.upsert(src, Job(title="خياطة درزة", source="employer", post_url="x", posted_at=now, employer="معمل النور",
                                cities=["حلب"], phones=["0944111222"]), status="live")
    src.execute("INSERT INTO employer_tokens (token, job_id, verified, created_at) VALUES ('tok-123456789012', ?, 1, ?)", (emp, now))
    src.execute("INSERT INTO bot_users (chat_id, education, cities, active, created) VALUES (42, 'none', '|حمص|', 1, ?)", (now,))
    src.execute("INSERT INTO bot_sent VALUES (42, ?, ?)", (ok, now))
    db.count_event(src, "apply", "whatsapp")
    src.commit()
    path = tmp_path / "b.json.gz"
    backup.write_file(backup.export_data(src), path)

    # the server comes back from the seed: same collected ads, different ids, no people data
    dst = db.connect(":memory:")
    db.upsert(dst, Job(title="محاسب", source="wazafne", post_url="https://t.me/w/9", posted_at=now, cities=["دمشق"]))
    db.upsert(dst, Job(title="سائق توصيل", source="syrianjobs", post_url="https://t.me/s/2", posted_at=now, cities=["حمص"]))
    db.upsert(dst, Job(title="ربح من البيت", source="syrianjobs", post_url="https://t.me/s/1", posted_at=now, cities=["دمشق"]))
    dst.commit()
    stats = backup.import_data(dst, backup.read_file(path))
    assert stats["employer_jobs"] == 1 and stats["tokens"] == 1 and stats["bot_users"] == 1
    st = {r["title"]: r for r in dst.execute("SELECT title, status, reports, id FROM jobs")}
    assert st["ربح من البيت"]["status"] == "rejected" and st["سائق توصيل"]["reports"] == 1
    tok = dst.execute("SELECT job_id FROM employer_tokens WHERE token='tok-123456789012'").fetchone()
    assert tok["job_id"] == st["خياطة درزة"]["id"]
    assert dst.execute("SELECT job_id FROM bot_sent WHERE chat_id=42").fetchone()["job_id"] == st["سائق توصيل"]["id"]
    assert db.event_stats(dst)["totals"]["apply"]["whatsapp"] == 1
    # running it again changes nothing
    again = backup.import_data(dst, backup.read_file(path))
    assert dst.execute("SELECT COUNT(*) FROM jobs WHERE source='employer'").fetchone()[0] == 1 and again["reports"] == 0


def test_admin_backup_download_needs_token():
    import gzip as _gz
    import json as _json
    client = TestClient(api.app)
    assert client.get("/api/admin/backup").status_code == 403
    r = client.get("/api/admin/backup", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert _json.loads(_gz.decompress(r.content))["version"] == 1
