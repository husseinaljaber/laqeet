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
