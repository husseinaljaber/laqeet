"""Plain-language search: «محاسب بدمشق دوام جزئي» becomes keywords + filters."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import api, db, nlsearch as nls
from app.models import Job

client = TestClient(api.app)
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def chips(q):
    p = nls.parse(q)
    return p.text, {(c.kind, c.value) for c in p.chips}


@pytest.mark.parametrize("q, text, expected", [
    ("بدي شغل محاسبة بدمشق دوام جزئي بلا خبرة", "محاسبة", {("city", "دمشق"), ("shift", "part"), ("exp", "none")}),
    ("ممرضة بحلب للبنات", "ممرضة", {("city", "حلب"), ("gender", "female")}),
    ("مهندس مدني بالمنظمات", "مهندس مدني", {("sector", "ngo")}),
    ("شغل من البيت", "", {("shift", "remote")}),
    ("سائق باستنبول", "سائق", {("city", "إسطنبول"), ("country", "tr")}),
    ("معي بكالوريا بدي شغل بحمص", "", {("edu", "secondary"), ("city", "حمص")}),
    ("خبرتي 3 سنين مبرمج", "مبرمج", {("exp", "3")}),
    ("عندي خبرة سنتين", "", {("exp", "2")}),
    ("ريف دمشق كاشير", "كاشير", {("city", "ريف دمشق")}),
    ("طبيب اسنان بالمزة", "طبيب اسنان", {("city", "دمشق")}),
    ("وظائف حكومية جديدة", "", {("sector", "gov"), ("new", "1")}),
    ("بدي شغل بتركيا", "", {("country", "tr")}),
    ("خريج معهد", "", {("edu", "institute")}),
])
def test_understands_common_phrases(q, text, expected):
    assert chips(q) == (text, expected)


@pytest.mark.parametrize("q", ["محاسب", "مدرس معهد", "مدرس خاص", "Accountant", "مندوب مبيعات"])
def test_job_titles_are_left_alone(q):
    """Words that are part of a job title must not turn into filters."""
    assert chips(q) == (q, set())


def test_chip_span_is_the_text_to_remove():
    p = nls.parse("محاسب بدمشق دوام جزئي")
    spans = {c.kind: c.span for c in p.chips}
    assert spans == {"city": "بدمشق", "shift": "دوام جزئي"}
    # removing a chip's span and searching again drops exactly that filter
    again = nls.parse("محاسب بدمشق دوام جزئي".replace(spans["city"], ""))
    assert {c.kind for c in again.chips} == {"shift"}


def test_experience_buckets():
    assert nls.exp_codes("none") == "unk"
    assert nls.exp_codes("2") == "unk,low"
    assert nls.exp_codes("3") == "unk,low,mid"
    assert nls.exp_codes("7") is None
    assert nls.exp_codes(None) is None


_IDS: list[int] = []


def teardown_module():
    """The test database is shared between files: leave it as we found it."""
    for jid in _IDS:
        api.con.execute("DELETE FROM job_sources WHERE job_id=?", (jid,))
        api.con.execute("DELETE FROM jobs_fts WHERE job_id=?", (jid,))
        api.con.execute("DELETE FROM jobs WHERE id=?", (jid,))
    api.con.commit()


def setup_module():
    for title, kw in [
        ("مترجم دوام جزئي", dict(cities=["دمشق"], shift="دوام جزئي", education="bachelor")),
        ("مترجم", dict(cities=["حلب"], shift="دوام كامل", education="bachelor")),
        ("مدخل بيانات", dict(cities=["دمشق"], tags=["عن بعد"], education="secondary")),
        ("سكرتيرة", dict(cities=["دمشق"], gender="female", education="secondary")),
    ]:
        jid, _ = db.upsert(api.con, Job(title=title, source="wazafne", post_url=f"https://t.me/nl/{abs(hash(title)) % 10**6}",
                                        posted_at=NOW, **kw))
        _IDS.append(jid)
    api.con.commit()


def _search(q, **p):
    return client.get("/api/jobs", params={"q": q, "smart": 1, **p}).json()


def test_api_applies_understood_filters():
    d = _search("مترجم بدمشق دوام جزئي")
    assert [j["title"] for j in d["jobs"]] == ["مترجم دوام جزئي"]
    assert {c["label"] for c in d["understood"]} == {"دمشق", "دوام جزئي"}
    assert {j["title"] for j in _search("شغل من البيت")["jobs"]} == {"مدخل بيانات"}


def test_query_city_wins_over_the_city_filter_sent_by_the_app():
    d = _search("مترجم بحلب", city="دمشق")
    assert [j["title"] for j in d["jobs"]] == ["مترجم"]


def test_typo_is_corrected_when_nothing_matches():
    d = _search("مترحم")
    assert d["corrected"] and d["total"] >= 1


def test_plain_search_unchanged_without_smart():
    d = client.get("/api/jobs", params={"q": "مترجم بدمشق"}).json()
    assert d["understood"] == []
