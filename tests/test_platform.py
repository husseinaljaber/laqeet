"""Store, API, bot: the pieces after extraction."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import api, bot, db
from app.models import Job

client = TestClient(api.app)
NOW = datetime.now(timezone.utc)


def _job(title, **kw):
    base = dict(source="wazafne", post_url=f"https://t.me/x/{abs(hash(title)) % 10**6}", posted_at=NOW.isoformat(timespec="seconds"))
    base.update(kw)
    return Job(title=title, **base)


def setup_module():
    for j in [
        _job("محاسب", employer="شركة الأمل", cities=["دمشق"], education="bachelor", field="business", experience=2),
        _job("كاشير", cities=["حلب"], education="none", kind="quick", source="syrianjobs", phones=["0933123456"], whatsapp=True),
        _job("مهندس شبكات", employer="وزارة الاتصالات", cities=["دمشق"], education="bachelor", field="computer", subfield="NET",
             official=True, source="moad", deadline=(NOW + timedelta(days=5)).date().isoformat()),
        _job("فني تمريض", cities=["حمص"], education="institute", field="health"),
    ]:
        db.upsert(api.con, j)
    api.con.commit()


def test_duplicate_from_another_channel_is_merged():
    j = _job("محاسب", employer="شركة الأمل", cities=["دمشق"], education="bachelor", source="tawzeef", post_url="https://t.me/y/1")
    jid, new = db.upsert(api.con, j)
    assert not new
    assert len(client.get(f"/api/jobs/{jid}").json()["sources"]) == 2


def test_education_filter_and_lower_toggle():
    titles = lambda **p: [j["title"] for j in client.get("/api/jobs", params=p).json()["jobs"]]
    assert set(titles(edu="bachelor")) == {"محاسب", "مهندس شبكات"}
    assert "فني تمريض" in titles(edu="bachelor", lower=True)
    assert titles(edu="none", kind="quick", lower=True) == ["كاشير"]
    assert titles(edu="bachelor", city="حلب") == []
    assert titles(edu="bachelor", field="computer", sub="NET") == ["مهندس شبكات"]
    # Explicit edu + search query must NOT dump unrelated 'none'/'institute' roles unless lower=True
    assert "فني تمريض" not in titles(edu="bachelor", q="فني")
    # Experience bucket filter
    assert "محاسب" in titles(exp="low")
    assert "فني تمريض" in titles(exp="unk")
    assert "فني تمريض" not in titles(exp="low")


def test_report_hides_after_three():
    jid = client.get("/api/jobs", params={"q": "تمريض"}).json()["jobs"][0]["id"]
    # Same IP pressing three times only counts once.
    for _ in range(3):
        assert client.post(f"/api/jobs/{jid}/report", json={"reason": "scam"}).status_code == 200
    assert client.get("/api/jobs", params={"q": "تمريض"}).json()["total"] == 1
    # Three distinct visitors hide it.
    for i in range(1, 4):
        assert client.post(f"/api/jobs/{jid}/report", json={"reason": "scam"},
                           headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code == 200
    assert client.get("/api/jobs", params={"q": "تمريض"}).json()["total"] == 0
    # an admin can put it back
    h = {"X-Admin-Token": "test-token"}
    assert any(j["id"] == jid for j in client.get("/api/admin/queue", headers=h).json()["jobs"])
    client.post(f"/api/admin/jobs/{jid}", json={"action": "approve"}, headers=h)
    assert client.get("/api/jobs", params={"q": "تمريض"}).json()["total"] == 1


def test_employer_post_waits_for_review():
    bad = client.post("/api/employer/jobs", json={"title": "محاسب", "employer": "شركة", "phone": "0944111222", "deadline": "not-a-date"})
    assert bad.status_code == 400
    r = client.post("/api/employer/jobs", json={"title": "عامل حبكة", "employer": "معمل النور", "city": "حلب", "phone": "0944111222",
                                                "kind": "quick", "education": "none"})
    assert r.status_code == 200
    assert client.get("/api/jobs", params={"q": "حبكة"}).json()["total"] == 0
    assert client.get("/api/admin/queue").status_code == 403
    client.post(f"/api/admin/jobs/{r.json()['id']}", json={"action": "approve"}, headers={"X-Admin-Token": "test-token"})
    assert client.get("/api/jobs", params={"q": "حبكة"}).json()["total"] == 1


def test_syria_only_excludes_turkey():
    meta = client.get("/api/meta").json()
    assert "cities_tr" not in meta
    assert [c["code"] for c in meta["countries"]] == ["sy"]
    assert meta["update_minutes"] == 20
    assert client.get("/api/jobs", params={"country": "tr"}).json()["total"] == 0
    sy_titles = [x["title"] for x in client.get("/api/jobs", params={"country": "sy"}).json()["jobs"]]
    assert "محاسب" in sy_titles


def test_share_page_and_scam_check():
    jid = client.get("/api/jobs", params={"q": "محاسب"}).json()["jobs"][0]["id"]
    page = client.get(f"/j/{jid}")
    assert page.status_code == 200 and 'property="og:title"' in page.text and "محاسب" in page.text

    bad = client.post("/api/scam-check", json={"text": "مطلوب من البيت بربح يومي 50$ على بينانس دفع رسوم تسجيل عبر شام كاش"}).json()
    assert bad["verdict"] == "danger" and bad["reasons"]
    ok = client.post("/api/scam-check", json={"text": "تعلن شركة الأمل في دمشق عن حاجتها لمحاسب دوام كامل"}).json()
    assert ok["verdict"] == "safe"


def test_sectors_apply_types_dedupe_and_expiry_rules():
    from app import collect
    from app.sources.telegram import Post

    ngo_job = _job("مسؤول حماية الطفل", employer="منظمة الأمل الإنسانية", cities=["حلب"], education="bachelor",
                   field="ngo", apply_url="https://docs.google.com/forms/d/e/testform123/viewform",
                   post_url="https://t.me/wazafne175/999001")
    jid1, new1 = db.upsert(api.con, ngo_job)
    # Reposted in another channel WITHOUT employer name, same form URL -> must merge!
    repost = _job("مسؤول حماية الطفل", employer=None, cities=["حلب"], education="bachelor",
                  field="ngo", apply_url="https://docs.google.com/forms/d/e/testform123/viewform?usp=sf_link",
                  source="tawzeef", post_url="https://t.me/tawzeef_platform/999001")
    jid2, new2 = db.upsert(api.con, repost)
    api.con.commit()
    assert new1 and not new2 and jid1 == jid2

    ngo_res = client.get("/api/jobs", params={"country": "sy", "sector": "ngo", "apply": "form"}).json()
    assert any(j["id"] == jid1 for j in ngo_res["jobs"])
    gov_res = client.get("/api/jobs", params={"country": "sy", "sector": "gov"}).json()
    assert all(j["official"] for j in gov_res["jobs"])

    # Test smart Arabic morphological search
    acc_al = client.get("/api/jobs", params={"country": "sy", "q": "المحاسب"}).json()
    acc_raw = client.get("/api/jobs", params={"country": "sy", "q": "محاسب"}).json()
    assert acc_al["total"] > 0 and acc_al["total"] == acc_raw["total"]

    eng_al = client.get("/api/jobs", params={"country": "sy", "q": "المهندس"}).json()
    eng_raw = client.get("/api/jobs", params={"country": "sy", "q": "مهندس"}).json()
    assert eng_al["total"] > 0 and eng_al["total"] == eng_raw["total"]

    # Test edu_group (degree vs nodegree)
    deg = client.get("/api/jobs", params={"country": "sy", "edu_group": "degree"}).json()
    assert "محاسب" in [x["title"] for x in deg["jobs"]]
    nodeg = client.get("/api/jobs", params={"country": "sy", "edu_group": "nodegree"}).json()
    assert "كاشير" in [x["title"] for x in nodeg["jobs"]]

    # Expired post must NOT be ingested by store_post
    past = (NOW - timedelta(days=5)).date().strftime("%d/%m/%Y")
    old_post = Post(channel="wazafne175", id=999002, date=NOW.isoformat(timespec="seconds"),
                    text=f"تعلن شركة اختبار عن حاجتها لمهندس\nآخر موعد للتقديم: {past}\nللتواصل 0933999888", links=[])
    assert collect.store_post(api.con, old_post, None) == 0


def test_stats_and_pulse():
    exp_job = _job("أرشيف منتهي", employer="شركة قديمة", cities=["حمص"], deadline=(NOW - timedelta(days=2)).date().isoformat(),
                   post_url="https://t.me/wazafne175/999003")
    db.upsert(api.con, exp_job, status="expired")
    api.con.commit()
    s = client.get("/api/stats").json()
    assert s["open"] >= 4 and s["official"] == 1 and s["six_months"] > s["open"]
    assert client.get("/api/jobs").json()["total"] == s["open"]
    p = client.get("/api/pulse").json()
    assert p["total"] >= 4 and p["fields"]


class FakeTelegram(bot.Telegram):
    def __init__(self):
        self.sent = []

    def call(self, method, **params):
        self.sent.append((method, params))
        return {}


def _press(chat, data):
    return {"callback_query": {"id": "1", "data": data, "message": {"chat": {"id": chat}, "message_id": 5}}}


def test_bot_onboarding_and_alerts_once():
    tg, chat, con = FakeTelegram(), 777, api.con
    bot.handle(con, tg, {"message": {"chat": {"id": chat}, "text": "/start"}})
    assert "شو شهادتك" in tg.sent[-1][1]["text"]
    bot.handle(con, tg, _press(chat, "edu:bachelor"))
    bot.handle(con, tg, _press(chat, "field:computer"))
    bot.handle(con, tg, _press(chat, "city:دمشق"))
    bot.handle(con, tg, _press(chat, "done"))
    con.execute("UPDATE bot_users SET created='2000-01-01' WHERE chat_id=?", (chat,))
    tg.sent.clear()
    assert bot.notify(con, tg) == 1
    assert "مهندس شبكات" in tg.sent[0][1]["text"]
    assert bot.notify(con, tg) == 0  # never twice
    bot.handle(con, tg, {"message": {"chat": {"id": chat}, "text": "/stop"}})
    assert con.execute("SELECT active FROM bot_users WHERE chat_id=?", (chat,)).fetchone()[0] == 0


def test_direct_apply_enforcement_and_unwrapping():
    from app import collect, direct_apply as da
    from app.sources.telegram import Post
    import httpx

    # Aggregators, social links, and Drive folders without contact are rejected
    for bad_url in (
        "https://tawzeefportal.com/companies/34",
        "https://job.sy/jobs/999",
        "https://hanwazafak.com/job/1",
        "https://baaeed.com/jobs/1",
        "https://facebook.com/jobs/123",
        "https://drive.google.com/drive/folders/abc123",
    ):
        j = _job("مهندس مدني", apply_url=bad_url)
        assert not da.resolve_job_apply(j, client=None)

    # Wrapper portal HTML unwraps Google Forms / career email / wa.me Syrian phone cleanly, ignoring site footer noise
    html_body = """
    <html><body>
      <header><a href="mailto:info@sarc-sy.org">info</a></header>
      <main>
        <p>للتقديم يرجى تعبئة الاستمارة:</p>
        <a href="https://docs.google.com/forms/d/e/1FAIpQLSdTestForm/viewform?utm_source=fb">اضغط للتقديم</a>
        <a href="https://wa.me/963944555666">واتساب</a>
      </main>
      <footer><p>hr@wrong-footer-ignored</p></footer>
    </body></html>
    """
    info = da.extract_apply_from_html(html_body, "https://worklink.sy/jobs/123")
    assert info["apply_url"] == "https://docs.google.com/forms/d/e/1FAIpQLSdTestForm/viewform"
    assert "0944555666" in info["phones"]
    assert info["email"] is None

    # Live-mocked wrapper resolution through resolve_job_apply
    def handler(req: httpx.Request) -> httpx.Response:
        if "worklink.sy" in str(req.url):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html_body)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as mock_client:
        wj = _job("منسق لوجستي", apply_url="https://worklink.sy/jobs/123", details_url="https://worklink.sy/jobs/123")
        assert da.resolve_job_apply(wj, client=mock_client, cache={})
        assert wj.apply_url == "https://docs.google.com/forms/d/e/1FAIpQLSdTestForm/viewform"
        assert wj.details_url == "https://worklink.sy/jobs/123"
        assert "0944555666" in wj.phones

    # Even official / moad posts with NO apply_url, phone, or email must not be saved by store_post
    no_way = Post(channel="SyMOAD", id=999009, date=NOW.isoformat(timespec="seconds"),
                  text="تعلن وزارة الصحة عن شاغر مهندس معماري في دمشق قريبا", links=[])
    assert collect.store_post(api.con, no_way, None) == 0


def test_strong_fts_search_boundaries_ranking_suggest_and_typo():
    extra = [
        _job("مطلوب أخصائي تجميل", raw="شركة عناية بالبشرة تطلب مسوقة منتجات طبيعية براتب ممتاز", phones=["0933100200"], post_url="https://t.me/x/701"),
        _job("طبيب عام", employer="مشفى الشفاء", cities=["حلب"], education="bachelor", field="health", email="hr@shifa.sy", post_url="https://t.me/x/702"),
        _job("مطور واجهات React", employer="شركة تقنية", cities=["دمشق"], raw="IT software engineer frontend developer", email="dev@tech.sy", post_url="https://t.me/x/703"),
        _job("مدير مبيعات", employer="مؤسسة الشام", cities=["حمص"], email="sales@sham.sy", post_url="https://t.me/x/704"),
        _job("سائق توصيل طلبات", employer="مطعم البيك", cities=["دمشق"], education="none", kind="quick", phones=["0955112233"], post_url="https://t.me/x/705"),
        _job("مساعد إداري", employer="شركة النور", cities=["دمشق"], posted_at=(NOW - timedelta(hours=1)).isoformat(timespec="seconds"),
             raw="التعاون مع قسم المحاسبة في المكتب", email="adm@nour.sy", post_url="https://t.me/x/706"),
    ]
    for j in extra:
        db.upsert(api.con, j)
    api.con.commit()

    # 1. Legacy JSON leakage bug fixed: q=false must return 0
    assert client.get("/api/jobs", params={"q": "false"}).json()["total"] == 0

    # 2. Short Arabic word boundary: q=طب must NOT match 'مطلوب' or 'طبيعية'
    med_res = client.get("/api/jobs", params={"q": "طب"}).json()
    med_titles = [j["title"] for j in med_res["jobs"]]
    assert "طبيب عام" in med_titles
    assert "مطلوب أخصائي تجميل" not in med_titles

    # 3. ASCII word boundary: q=IT must match standalone IT token only
    it_res = client.get("/api/jobs", params={"q": "IT"}).json()
    assert [j["title"] for j in it_res["jobs"]] == ["مطور واجهات React"]

    # 4. Broken plural & Syrian synonyms
    assert any(j["title"] == "مدير مبيعات" for j in client.get("/api/jobs", params={"q": "مدراء"}).json()["jobs"])
    assert any(j["title"] == "سائق توصيل طلبات" for j in client.get("/api/jobs", params={"q": "دليفري"}).json()["jobs"])
    assert any(j["title"] == "مطور واجهات React" for j in client.get("/api/jobs", params={"q": "فرونت"}).json()["jobs"])

    # 5. Relevance ranking: Title match ('محاسب') ranks ahead of body-only mention ('مساعد إداري')
    acc_res = client.get("/api/jobs", params={"q": "محاسبة"}).json()
    acc_titles = [j["title"] for j in acc_res["jobs"]]
    assert acc_titles.index("محاسب") < acc_titles.index("مساعد إداري")
    assert acc_res["terms"]

    # 6. Autocomplete suggestions & typo did-you-mean & demand excludes 'other'
    sug = client.get("/api/suggest", params={"q": "مهن"}).json()["suggestions"]
    assert any("مهندس شبكات" in s["text"] for s in sug)
    typo = client.get("/api/jobs", params={"q": "مهندص"}).json()
    assert typo["total"] == 0 and typo["suggestion"] and "مهندس" in typo["suggestion"]
    demand_fields = [d["field"] for d in client.get("/api/stats").json()["demand"]]
    assert "other" not in demand_fields


def test_employer_email_only_and_bot_syria_only():
    ok = client.post("/api/employer/jobs", json={"title": "مصمم غرافيك", "employer": "استوديو الشام", "email": "jobs@sham.sy"})
    assert ok.status_code == 200
    bad = client.post("/api/employer/jobs", json={"title": "محاسب", "employer": "شركة", "apply_url": "https://tawzeefportal.com/companies/1"})
    assert bad.status_code == 400

    assert bot.matches({"education": "bachelor", "field": "", "cities": ""}, {"title": "مندوب", "education": None, "cities": []})
    msg = bot.message({"id": 1, "title": "فني صيانة", "phones": ["0531112233", "0933445566"], "email": "hr@test.sy", "sources": []})
    assert "963933445566" in msg and "wa.me/90" not in msg and "mailto:hr@test.sy" in msg


def test_reliefweb_parser_extracts_inner_apply_link():
    from app.sources.reliefweb import parse_reliefweb_rss

    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><item>
      <title>Project Officer - Terre des hommes</title>
      <link>https://reliefweb.int/job/999/project-officer</link>
      <description><![CDATA[<p>Apply at <a href="https://jobs.tdh.org/vacancies/123?utm_source=rw">TDH Portal</a> or email jobs@tdh.org in Damascus.</p>]]></description>
    </item></channel></rss>"""
    jobs = parse_reliefweb_rss(xml)
    assert len(jobs) == 1
    assert jobs[0].apply_url == "https://jobs.tdh.org/vacancies/123"
    assert jobs[0].details_url == "https://reliefweb.int/job/999/project-officer"
    assert jobs[0].email == "jobs@tdh.org"


def test_pulse_excludes_other_and_no_cache_headers():
    for i in range(4):
        db.upsert(api.con, _job(f"موظف عام {i}", field="other", email=f"other{i}@co.sy", post_url=f"https://t.me/x/80{i}"))
    api.con.commit()

    p_resp = client.get("/api/pulse")
    assert p_resp.status_code == 200
    assert "no-store" in p_resp.headers.get("cache-control", "")
    p = p_resp.json()
    assert p["fields"]
    assert "other" not in [f["code"] for f in p["fields"]]

    for asset in ("/", "/sw.js", "/app.js", "/app.css"):
        r = client.get(asset)
        assert r.status_code == 200
        assert "no-cache" in r.headers.get("cache-control", "")


def test_expanded_channel_extraction_and_arabic_digits():
    from app import extract

    damas_post = (
        "شركة في دمشق - المزة تطلب محاسب بدوام كامل\n"
        "للتواصل واتساب: ٠٩٣٣١١٢٢٣٣\n"
        "لإعلاناتكم على مجموعتنا ع تلغرام @damasjobsforads"
    )
    dj = extract.extract(damas_post, "damasjob", "https://t.me/damasjob/1", NOW.isoformat(timespec="seconds"), "quick")
    assert len(dj) == 1
    assert "محاسب" in dj[0].title
    assert "0933112233" in dj[0].phones

    nws_post = (
        "المنصب الوظيفي: مدير حالة\n"
        "مكان العمل: إدلب - الدانا\n"
        "للاطلاع على الوصف الوظيفي والتقديم: https://forms.gle/abc123Test"
    )
    nj = extract.extract(nws_post, "nwsjobs", "https://t.me/nwsjobs/1", NOW.isoformat(timespec="seconds"), "formal")
    assert len(nj) == 1
    assert nj[0].title == "مدير حالة"
    assert nj[0].apply_url == "https://forms.gle/abc123Test"

    multi = (
        "تعلن منظمة الإغاثة عن الشواغر التالية في حلب:\n"
        "طبيب عام\n"
        "https://forms.gle/docForm111\n"
        "مهندس مدني\n"
        "https://forms.gle/engForm222"
    )
    mj = extract.extract(multi, "wazafne", "https://t.me/wazafne175/2", NOW.isoformat(timespec="seconds"), "formal")
    assert len(mj) == 2
    assert {j.apply_url for j in mj} == {"https://forms.gle/docForm111", "https://forms.gle/engForm222"}


def test_web_portals_ida_and_jobsyria_mocked():
    import httpx
    from app.sources import web_portals as wp

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "ida-org.com/wp-json" in u:
            return httpx.Response(200, json=[{
                "link": "https://ida-org.com/jobs/nurse-1",
                "date_gmt": NOW.isoformat(),
                "title": {"rendered": "Nurse"},
                "content": {"rendered": '<p>Apply: <a href="https://forms.gle/idaNurseForm1">Form</a></p>'},
            }])
        if "api.jobsyria.org/api/jobs" in u:
            return httpx.Response(200, json={
                "data": {
                    "data": [{
                        "id": 1,
                        "status": "open",
                        "title": "IT Assistant",
                        "cities_names": "دمشق",
                        "client": {"name": "IMC"},
                        "published_at": NOW.strftime("%Y-%m-%d %H:%M"),
                        "deadline": (NOW + timedelta(days=5)).strftime("%Y-%m-%d 16:00"),
                        "permanent_link": "https://www.jobsyria.org/jobs/details/abc",
                        "application_link": '<p><a href="https://enketo.ona.io/x/wbDvm2LO">Apply</a></p>',
                    }]
                }
            })
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        ida_jobs = wp.scrape_ida(c)
        assert len(ida_jobs) == 1 and ida_jobs[0].apply_url == "https://forms.gle/idaNurseForm1"
        js_jobs = wp.scrape_jobsyria(c)
        assert len(js_jobs) == 1 and js_jobs[0].apply_url == "https://enketo.ona.io/x/wbDvm2LO"
        assert "دمشق" in js_jobs[0].cities


def test_cloudflare_email_footer_ignore_and_new_portals():
    import httpx
    from app import direct_apply as da
    from app.models import Job
    from app.sources import reliefweb as rw
    from app.sources import web_portals as wp

    # 1. Cloudflare XOR email decode + ignoring portal footer emails
    key = 0x2A
    target = "hr@acme-sy.org"
    encoded = f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in target)
    html_cf = (
        f'<a href="/cdn-cgi/l/email-protection#{encoded}">تقديم</a>'
        '<a href="mailto:contact@jobinsyria.com">support</a>'
    )
    extracted = da.extract_apply_from_html(html_cf, "https://worklink.sy/jobs/demo")
    assert extracted["email"] == target

    # 2. Login-walled aggregators and footer emails blocked cleanly
    for wall in ("https://www.jobs.sy/offer/100", "https://syrianjobs.net/ar/jobs/x", "https://phoenijobs.sy/ar/jobs/x", "https://lmo.sy/job/x"):
        j_wall = Job(title="Job", source="test", post_url=wall, apply_url=wall, email="contact@jobinsyria.com")
        assert da.resolve_job_apply(j_wall) is False

    # 3. WorkLink JSON-LD parser keeps Syria + validThrough and rejects Gulf relocation
    today_s = NOW.date().isoformat()
    future_s = (NOW + timedelta(days=10)).date().isoformat()
    wl_sy = (
        f'<script type="application/ld+json">{{"@type":"JobPosting","title":"محاسب",'
        f'"description":"محاسب في دمشق","datePosted":"{today_s}","validThrough":"{future_s}",'
        f'"hiringOrganization":{{"name":"شركة النور"}},'
        f'"jobLocation":[{{"address":{{"addressCountry":"SY","addressLocality":"دمشق"}}}}]}}</script>'
        f'<a href="/cdn-cgi/l/email-protection#{encoded}">تقديم</a>'
    )
    j_wl = wp._parse_worklink_detail("https://worklink.sy/jobs/noor-1", wl_sy, today_s)
    assert j_wl is not None and j_wl.email == target and j_wl.employer == "شركة النور" and "دمشق" in j_wl.cities

    wl_qatar = (
        f'<script type="application/ld+json">{{"@type":"JobPosting","title":"Sales",'
        f'"description":"Actual Job Location: Qatar Candidate Location: Syria","validThrough":"{future_s}",'
        f'"jobLocation":[{{"address":{{"addressCountry":"SY","addressLocality":"دمشق"}}}}]}}</script>'
        f'<a href="/cdn-cgi/l/email-protection#{encoded}">تقديم</a>'
    )
    assert wp._parse_worklink_detail("https://worklink.sy/jobs/qatar-1", wl_qatar, today_s) is None

    # 4. ReliefWeb HTML parser + UN Syria scraper + JobInSyria pagination
    rw_html = f"""
    <article class="rw-river-article--job">
      <p class="rw-entity-country-slug"><a href="/jobs">Syria</a></p>
      <h3 class="rw-river-article__title"><a href="/job/4239000/wash-officer-aleppo">WASH Officer - Aleppo</a></h3>
      <dd class="rw-entity-meta__tag-value--source">UNICEF</dd>
      <dd class="rw-entity-meta__tag-value--posted"><time datetime="{NOW.isoformat()}">Today</time></dd>
      <dd class="rw-entity-meta__tag-value--closing-date"><time datetime="{future_s}T00:00:00+00:00">{future_s}</time></dd>
    </article>
    <article class="rw-river-article--job">
      <p class="rw-entity-country-slug"><a href="/jobs">Jordan + 2 more</a></p>
      <h3 class="rw-river-article__title"><a href="/job/4239001/regional-jordan">Regional Advisor</a></h3>
    </article>
    """
    rw_jobs = rw.parse_reliefweb_html(rw_html)
    assert len(rw_jobs) == 1 and rw_jobs[0].employer == "UNICEF" and "حلب" in rw_jobs[0].cities

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "syria.un.org/ar/jobs" in u:
            return httpx.Response(200, text=(
                '<a href="https://jobs.unicef.org/en-us/job/596853">Health Officer, Deir-ez-Zor</a>'
                '<a href="https://careers.un.org/lbw/home.aspx?viewtype=CYJA">Profile</a>'
            ))
        if "syria.un.org/en/jobs" in u:
            return httpx.Response(200, text="")
        if "jobinsyria.com/wp-json/wp/v2/job_listing" in u:
            if "&page=1" in u:
                return httpx.Response(200, json=[{
                    "link": "https://jobinsyria.com/job/j1",
                    "date_gmt": NOW.isoformat(),
                    "title": {"rendered": "محاسب — شركة الأمل، دمشق"},
                    "content": {"rendered": '<p><a href="https://wa.me/963933112233">واتساب</a></p>'},
                    "metas": {"_job_expiry_date": future_s, "_job_location": {"1": "دمشق"}},
                }])
            if "&page=2" in u:
                return httpx.Response(200, json=[{
                    "link": "https://jobinsyria.com/job/j2",
                    "date_gmt": NOW.isoformat(),
                    "title": {"rendered": "مراسل — جمعية جذور، حلب"},
                    "content": {"rendered": '<p><a href="https://forms.gle/jisForm2">Form</a></p>'},
                    "metas": {"_job_expiry_date": future_s, "_job_location": {"1": "حلب"}},
                }])
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        un_jobs = wp.scrape_syria_un(c)
        assert len(un_jobs) == 1 and un_jobs[0].apply_url == "https://jobs.unicef.org/en-us/job/596853"
        jis_jobs = wp.scrape_jobinsyria(c)
        assert len(jis_jobs) == 2
        assert jis_jobs[0].employer == "شركة الأمل" and "0933112233" in jis_jobs[0].phones
        assert jis_jobs[1].employer == "جمعية جذور" and jis_jobs[1].apply_url == "https://forms.gle/jisForm2"


def test_phase2_employer_self_manage_verified_badge_and_trust_metrics():
    # Submit a Turkey employer job and receive a self-service management token
    r = client.post("/api/employer/jobs", json={
        "country": "tr",
        "title": "مصمم غرافيك إسطنبول",
        "employer": "استوديو الفجر للتصميم",
        "city": "إسطنبول",
        "phone": "05341112233",
        "whatsapp": True,
        "education": "bachelor",
        "kind": "formal",
        "sector": "private",
        "company_url": "https://fajrstudio.example.com",
        "details": "تصميم هوية بصرية ومحتوى سوشال ميديا",
    })
    assert r.status_code == 200
    data = r.json()
    jid = data["id"]
    token = data["manage_token"]
    assert token and len(token) >= 16 and data["status"] == "review"

    # Unknown token returns 404
    assert client.get("/api/employer/manage/invalid-short").status_code == 404
    assert client.get("/api/employer/manage/unknown_token_123456").status_code == 404

    # Status check before approval shows 'review'
    st_before = client.get(f"/api/employer/manage/{token}").json()
    assert st_before["status"] == "review" and st_before["public_url"] is None and st_before["country"] == "tr"

    # Admin approves -> job goes live with verified_employer=True & trust_tier='verified_employer'
    h = {"X-Admin-Token": "test-token"}
    assert client.post(f"/api/admin/jobs/{jid}", json={"action": "approve"}, headers=h).status_code == 200
    live_job = client.get(f"/api/jobs/{jid}").json()
    assert live_job["verified_employer"] is True
    assert live_job["trust_tier"] == "verified_employer"
    assert "صاحب عمل موثّق على لقيت" in client.get(f"/j/{jid}").text

    st_live = client.get(f"/api/employer/manage/{token}").json()
    assert st_live["status"] == "live" and st_live["verified"] is True and st_live["public_url"] == f"/j/{jid}"

    # /api/stats returns live trust telemetry
    st_stats = client.get("/api/stats").json()
    assert "trust" in st_stats
    trust = st_stats["trust"]
    assert trust["open_total"] >= 1
    assert trust["verified_employers"] >= 1
    assert trust["scanned_total"] >= trust["open_total"]
    assert isinstance(trust["duplicates_merged"], int) and trust["duplicates_merged"] >= 0

    # Employer immediately closes/marks filled via self-service token
    cl = client.post(f"/api/employer/manage/{token}/close")
    assert cl.status_code == 200 and cl.json()["status"] == "expired"
    assert client.get("/api/jobs", params={"country": "tr", "q": "غرافيك"}).json()["total"] == 0
    st_closed = client.get(f"/api/employer/manage/{token}").json()
    assert st_closed["status"] == "expired" and st_closed["closed_at"] is not None


def test_syria10102_channel_and_mtproto_fallback():
    from types import SimpleNamespace
    from app import collect, config, extract as ex
    from app.sources import telegram as tg

    assert "syria10102" in config.CHANNELS
    assert config.CHANNELS["syria10102"]["country"] == "sy"
    assert "syria10102" in config.SOURCE_LABEL

    class MessageEntityTextUrl:
        def __init__(self, offset: int, length: int, url: str):
            self.offset = offset
            self.length = length
            self.url = url

    class MessageEntityUrl:
        def __init__(self, offset: int, length: int):
            self.offset = offset
            self.length = length

    # Include 4-byte emoji (2 UTF-16 code units) before plain URL to verify UTF-16 slicing
    text = "💼 مطلوب محاسب في دمشق — التقديم: اضغط هنا\n🔗 https://example.org/apply"
    link_off = text.encode("utf-16-le").index("https://example.org/apply".encode("utf-16-le")) // 2
    entities = [
        MessageEntityTextUrl(0, 5, "https://apply.example.com/form"),
        MessageEntityUrl(link_off, len("https://example.org/apply")),
    ]
    msg = SimpleNamespace(
        id=4012,
        date=NOW,
        message=text,
        entities=entities,
    )
    post = tg.message_to_post("syria10102", msg)
    assert post is not None
    assert post.url == "https://t.me/syria10102/4012"
    assert post.links == ["https://apply.example.com/form", "https://example.org/apply"]

    extracted = ex.extract(post.text, "syria10102", post.url, post.date, links=post.links, country="sy")
    assert len(extracted) == 1
    assert "محاسب" in extracted[0].title and "دمشق" in extracted[0].cities

    # Fallback triggers ONLY when web preview returns empty on page 0
    calls = []

    class FakeMTProto:
        def iter_channel(self, channel, stop_id=0, since_iso="2026-03-01", limit=200):
            calls.append((channel, stop_id))
            return [post]

    class FakeHTTP:
        def __init__(self, html: str):
            self.html = html

        def get(self, url: str):
            return SimpleNamespace(text=self.html)

    fallback_posts = list(collect.read_channel("syria10102", FakeHTTP("<html>no messages</html>"), 0, "2026-01-01", mtproto_reader=FakeMTProto()))
    assert calls == [("syria10102", 0)]
    assert [p.id for p in fallback_posts] == [4012]

    # When web preview has HTML posts up to stop_id, MTProto must NOT be invoked
    calls.clear()
    html_with_msg = (
        '<div class="tgme_widget_message" data-post="syria10102/4012">'
        '<div class="tgme_widget_message_text">مطلوب محاسب</div>'
        '<time datetime="2026-11-22T10:00:00+00:00"></time></div>'
    )
    existing_posts = list(collect.read_channel("syria10102", FakeHTTP(html_with_msg), 4012, "2026-01-01", mtproto_reader=FakeMTProto()))
    assert existing_posts == []
    assert calls == []

    # Verify syria10102 real post patterns (list with بمسميات + MOI link, footer promo protection, non-job disclaimers)
    moi_multi = ex.extract(
        "تعلن الإدارة العامة للشؤون المدنية توفر شواغر وظيفية بمسميات:\n"
        "◼️رئيس دائرة الشؤون الهندسية\n"
        "◼️مهندس دراسات\n"
        "◼️مهندس موقع\n"
        "التقديم عبر: https://ee.moi-sy.com/x/6o2YflqU",
        "syria10102",
        "https://t.me/syria10102/1007",
        "2026-11-22T12:08:14+00:00",
        links=["https://ee.moi-sy.com/x/6o2YflqU", "https://t.me/syria10102"],
        country="sy",
    )
    assert [j.title for j in moi_multi] == ["رئيس دائرة الشؤون الهندسية", "مهندس دراسات", "مهندس موقع"]
    assert all(j.official and j.employer == "الإدارة العامة للشؤون المدنية" for j in moi_multi)

    moi_course = ex.extract(
        "تعلن إدارة القوى البشرية في وزارة الداخلية عن فتح باب الانتساب للالتحاق ب دورة أفراد (ذكور)، وذلك وفق الشروط والمواصفات المطلوبة.\n"
        "للتسجيل والتقديم يرجى التقديم عبر الرابط التالي:\n"
        "https://ee.moi-sy.com/single/0f623bf7\n"
        "📍 تابعونا على:\n"
        "◼️وظائف عامة على #تلجرام\nhttps://t.me/syria10102\n#وظائف_عامة",
        "syria10102",
        "https://t.me/syria10102/1",
        "2026-11-10T10:00:00+00:00",
        links=["https://ee.moi-sy.com/single/0f623bf7", "https://t.me/syria10102"],
        country="sy",
    )
    assert len(moi_course) == 1
    assert moi_course[0].title == "دورة أفراد (ذكور)"
    assert all("تلجرام" not in j.title for j in moi_course)

    assert ex.extract(
        "نقابة المهندسين تطلق موقعاً إلكترونياً لتوظيف المهندسين.\nhttps://docs.google.com/forms/d/e/1FAIpQLSdTest/viewform",
        "syria10102",
        "https://t.me/syria10102/1005",
        "2026-11-21T13:13:13+00:00",
        country="sy",
    ) == []


def test_direct_apply_vendor_roots_portal_accounts_and_instant_filter():
    from app import direct_apply as da

    for bad_url in (
        "https://www.hrpeak.com/",
        "https://hire.trakstar.com/",
        "http://www.lumesse.com/",
        "https://ats.in-recruiting.com/prova-gratuita",
        "https://careers.rescue.org",
        "https://intranet.medair.org/Recruitment/RecPublic/newSubscription",
    ):
        assert da.is_disallowed_offline_url(bad_url), f"expected disallowed: {bad_url}"
        assert not da.is_direct_form_or_ats(bad_url), f"expected rejected direct/ats: {bad_url}"

    for good_url in (
        "https://form.goldyol.com/",
        "https://career.alboraq-syr.com",
        "https://hr.mabcoonline.com/",
        "https://ihh.hrpeak.com/abc12345.job",
    ):
        assert not da.is_disallowed_offline_url(good_url), f"expected allowed: {good_url}"
        assert da.is_direct_form_or_ats(good_url), f"expected direct/ats: {good_url}"

    for portal_url in (
        "https://moad.gov.sy/jobs/1",
        "https://ssf.gov.sy/careers",
        "https://powerholding-intl.com/apply/1",
        "https://wd3.myworkdaysite.com/recruiting/care/CARE/job/Damascus/Officer",
        "https://ekyc.fa.em2.oraclecloud.com/hcmUI/CandidateExperience",
        "https://jobs.unicef.org/en-us/job/123",
    ):
        assert da.requires_portal_account(portal_url), f"expected portal account: {portal_url}"

    for open_url in (
        "https://docs.google.com/forms/d/e/1FAIpQLSdTest/viewform",
        "https://forms.gle/abc123",
        "https://form.goldyol.com/",
        "https://career.alboraq-syr.com",
        "https://hr.mabcoonline.com/",
        None,
        "",
    ):
        assert not da.requires_portal_account(open_url), f"unexpected portal account: {open_url}"

    j_vendor = _job(
        "مسؤول لوجستيات ميداني",
        employer="WeWorld Test",
        cities=["حلب"],
        apply_url="https://ats.in-recruiting.com/prova-gratuita",
        email="info@weworld.it",
        post_url="https://t.me/test/9900",
    )
    jid_vendor, _ = db.upsert(api.con, j_vendor)

    j_portal_only = _job(
        "منسق مشاريع ميداني",
        employer="MOAD",
        cities=["دمشق"],
        apply_url="https://moad.gov.sy/vacancy/99",
        post_url="https://t.me/test/9901",
    )
    jid_portal_only, _ = db.upsert(api.con, j_portal_only)

    j_portal_alt = _job(
        "محاسب مشاريع دولية",
        employer="NRC Portal",
        cities=["دمشق"],
        apply_url="https://ekyc.fa.em2.oraclecloud.com/job/1",
        email="jobs@nrc-alt.org",
        post_url="https://t.me/test/9902",
    )
    db.upsert(api.con, j_portal_alt)

    j_direct = _job(
        "مهندس برمجيات مباشر",
        employer="GoldYol",
        cities=["حلب"],
        apply_url="https://form.goldyol.com/",
        post_url="https://t.me/test/9903",
    )
    db.upsert(api.con, j_direct)
    db.rebuild_fts(api.con)
    api.con.commit()

    vendor_row = api.con.execute("SELECT * FROM jobs WHERE id = ?", (jid_vendor,)).fetchone()
    vendor_d = db.row_to_dict(api.con, vendor_row)
    assert vendor_d["apply_url"] is None
    assert vendor_d["apply_types"] == ["email"]
    assert vendor_d["url_requires_account"] is False
    assert vendor_d["requires_account"] is False

    portal_row = api.con.execute("SELECT * FROM jobs WHERE id = ?", (jid_portal_only,)).fetchone()
    portal_d = db.row_to_dict(api.con, portal_row)
    assert portal_d["url_requires_account"] is True
    assert portal_d["requires_account"] is True

    instant_jobs = client.get("/api/jobs", params={"instant": "true", "limit": 200}).json()["jobs"]
    instant_titles = {j["title"] for j in instant_jobs}
    assert "منسق مشاريع ميداني" not in instant_titles
    assert "محاسب مشاريع دولية" in instant_titles
    assert "مهندس برمجيات مباشر" in instant_titles
    assert "مسؤول لوجستيات ميداني" in instant_titles

    all_matching = client.get("/api/jobs", params={"q": "منسق مشاريع ميداني"}).json()["jobs"]
    assert len(all_matching) == 1
    assert all_matching[0]["url_requires_account"] is True
    assert all_matching[0]["requires_account"] is True




