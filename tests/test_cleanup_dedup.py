"""Regression tests for extraction cleanup, anti-leak HTML parsing, Turkish/Syria normalization, and safe dedup."""
from app import arabic, db, direct_apply as da, extract as ex, spam, taxonomy as tx
from app.models import Job


def _tr_job(title: str, **kw) -> Job:
    base = dict(
        source="jobsintr",
        post_url=f"https://t.me/jobsintr/{abs(hash((title, kw.get('source', 'jobsintr')))) % 100000}",
        posted_at="2026-11-10T10:00:00+00:00",
        country="tr",
        cities=["إسطنبول"],
    )
    base.update(kw)
    return Job(title=title, **base)


def test_turkish_sat_math_networking_cross_source_dedup(tmp_path):
    con = db.connect(tmp_path / "tr_dedup.db")

    # Districts & proclitics resolve cleanly
    assert tx.tr_cities_from_text("لمعهد تعليمي في بهتشه شهير") == ["إسطنبول"]
    assert tx.tr_cities_from_text("العمل بإسطنبول قرب tahtakale") == ["إسطنبول"]

    # Doubled shift badge removed; unclosed parens balanced cleanly before/at 95 chars
    doubled = ex.normalize_title_display(
        "مطلوب مدرس SAT Math وAP Statistics وAP Calculus (دوام جزئي) دوام جزئى",
        keep_matlob=True,
    )
    assert doubled == "مطلوب مدرس SAT Math وAP Statistics وAP Calculus (دوام جزئي)"
    truncated = ex.normalize_title_display(
        "مطلوب موظفة كول سنتر للعمل في عيادة أسنان متخصصة في تجميل وزراعة الأسنان والسياحة العلاجية (دوام",
        keep_matlob=True,
    )
    assert truncated.count("(") == truncated.count(")")
    assert len(truncated) <= 95

    # Cross-source SAT Math duplicates collapse into 1 canonical row keeping contacts & sources
    id1, new1 = db.upsert(con, _tr_job(
        "مدرس SAT Math وAP Statistics وAP Calculus",
        source="jobsintr",
        post_url="https://t.me/jobsintr/5001",
        apply_url="https://jobsintr.net/jobs/satmath1",
        cities=["عدة ولايات في تركيا"],
    ))
    id2, new2 = db.upsert(con, _tr_job(
        doubled,
        source="jobsintr_web",
        post_url="https://jobsintr.net/jobs/satmath1",
        apply_url="https://jobsintr.net/jobs/satmath1",
        phones=["05343806011"],
    ))
    id3, new3 = db.upsert(con, _tr_job(
        "مطلوب مدرس SAT Math وAP Statistics وAP Calculus (دوام جزئي)",
        source="salamjobs",
        post_url="https://salamjobs.com/jobs/satmath-1",
        cities=tx.tr_cities_from_text("لمعهد في بهتشه شهير"),
        phones=["05343806011"],
    ))
    assert new1 is True
    assert new2 is False and id2 == id1
    assert new3 is False and id3 == id1
    srcs = {r[0] for r in con.execute("SELECT source FROM job_sources WHERE job_id=?", (id1,)).fetchall()}
    assert srcs == {"jobsintr", "jobsintr_web", "salamjobs"}

    # Networking Fundamentals across Telegram + portal merges cleanly into 1 row
    nid1, n1 = db.upsert(con, _tr_job(
        "مدرب متخصص في أساسيات الشبكات (Networking Fundamentals)",
        source="jobsintr",
        post_url="https://t.me/jobsintr/5002",
        cities=["عدة ولايات في تركيا"],
    ))
    nid2, n2 = db.upsert(con, _tr_job(
        "مطلوب مدرب متخصص في أساسيات الشبكات (Networking Fundamentals)",
        source="jobsintr_web",
        post_url="https://jobsintr.net/jobs/net1",
        apply_url="https://jobsintr.net/jobs/net1",
        phones=["05499009923"],
    ))
    assert n1 is True and n2 is False and nid2 == nid1


def test_html_apply_extractor_blocks_sidebar_and_portal_junk():
    salam_html = """
    <html><body>
      <header><a href="/account/applicants">Applicants</a></header>
      <main>
        <div class="job-details">
          <p>مطلوب خياط مساطر في إسطنبول، للتواصل واتساب: 0905306336978</p>
        </div>
      </main>
      <aside class="sidebar">
        <a href="https://jobsintr.net/jobs/k4lfath">وظيفة أخرى في الموقع الشقيق</a>
        <a href="https://ab-ilan.com/ngocareers/">دليل المنظمات</a>
        <a href="https://worklink.sy/recruiting-agencies">وكالات التوظيف</a>
      </aside>
    </body></html>
    """
    info = da.extract_apply_from_html(salam_html, "https://salamjobs.com/jobs/16442")
    assert info["apply_url"] is None
    assert "0905306336978" not in info["phones"]  # Syrian tx.phones rejects 13-digit TR numbers; tx.phones_tr extracts it

    assert tx.phones_tr("التواصل واتساب: 0905306336978") == ["05306336978"]
    assert da.is_disallowed_offline_url("https://ab-ilan.com/ngocareers/")
    assert da.is_disallowed_offline_url("https://worklink.sy/recruiting-agencies")
    assert da.is_disallowed_offline_url("https://inspira.un.org/psp/PUNA1J/?cmd=login&languageCd=ENG")


def test_title_filters_citizenship_and_city_false_positives():
    # Turkish characters preserved in normalization
    assert "İstanbul" in arabic.normalize("İstanbul Kalkınma Ajansı")
    assert spam.is_tr_citizenship_only("İstanbul Kalkınma Ajansı Personel Alımı Duyurusu")
    assert spam.is_tr_citizenship_only("الشروط: • حاصلة على الجنسية التركية وإتقان اللغة التركية")

    # Visual adjective ("بصري") must never false-trigger Daraa ("بصرى")
    assert tx.cities_from_text("مطلوب مصمم محتوى بصري وسرد بصري في إسطنبول") == []
    assert "درعا" in tx.cities_from_text("فرصة عمل في مدينة بصرى الشام - درعا")

    # Irregular / non-job titles rejected
    bad_samples = [
        "Organization: Deutsche Gesellschaft für Internationale Zusammenarbeit (GIZ) GmbH",
        "Closes: أكتوبر 6, 2026",
        "Create and manage content calendars",
        "كوادر",
        "راتب شهري عالي",
        "Location: Istanbul, Türkiye",
        "Son Başvuru Tarihi: 24/11/2026",
        "امتلاك مهارات التواصل والإقناع",
        "المعرف الوظيفي (LatTel001) محافظة اللاذقية",
        "كيف تحدث AI Agents ثورة في قيادة المشاريع التقنية",
        "كبر فريقنا وكن واحدا من عائلة MR",
        "مميزة بانتظارك",
        "لتوظيف في دمشق",
        "لمركز تجميل",
        "for three positions within its Water",
    ]
    for bad in bad_samples:
        assert not ex._good_title(bad), f"Expected rejection for {bad!r}"

    # Legitimate English & Arabic titles preserved
    good_samples = [
        "Support Services Team Leader",
        "Female Medical Consultant required Istanbul",
        "Medical Consultant with experience in dentistry",
        "A reservations officer is required - within a tourism company",
        "مشفى طبي في منطقة Beşyol في اسطنبول بحاجة الى مترجمة ادارية",
    ]
    for good in good_samples:
        assert ex._good_title(good), f"Unexpected rejection for {good!r}"


def test_generic_vs_cross_month_and_same_contact_dedup(tmp_path):
    con = db.connect(tmp_path / "dedup_rules.db")

    # Generic 1-word title in Damascus with distinct phones stays 2 separate jobs
    gid1, g1 = db.upsert(con, Job(
        title="محاسب", source="syrianjobs", post_url="https://t.me/s/1",
        posted_at="2026-11-10T10:00:00+00:00", cities=["دمشق"], country="sy", phones=["0933111222"],
    ))
    gid2, g2 = db.upsert(con, Job(
        title="محاسب", source="wazafne", post_url="https://t.me/w/2",
        posted_at="2026-11-10T11:00:00+00:00", cities=["دمشق"], country="sy", phones=["0944333444"],
    ))
    assert g1 is True and g2 is True and gid1 != gid2

    # Cross-month specific title duplicates (Oct 29 + Nov 2) merge into 1 job
    cm1, c_new1 = db.upsert(con, _tr_job(
        "منسق عمليات ومحاسبة تشغيلية",
        source="jobsintr", post_url="https://t.me/jobsintr/901",
        posted_at="2026-10-29T10:00:00+00:00", phones=["05321110099"],
    ))
    cm2, c_new2 = db.upsert(con, _tr_job(
        "مطلوب موظف منسق عمليات ومحاسبة تشغيلية",
        source="salamjobs", post_url="https://salamjobs.com/jobs/ops-1",
        posted_at="2026-11-02T10:00:00+00:00", phones=["05321110099"],
    ))
    assert c_new1 is True and c_new2 is False and cm2 == cm1

    # Same phone + subset role title merges ('كيميائي أو مهندس أغذية' <= 'مطلوب كيميائي أو مهندس أغذية بخبرة...')
    sc1, s_new1 = db.upsert(con, _tr_job(
        "كيميائي أو مهندس أغذية",
        source="jobsintr", post_url="https://t.me/jobsintr/902", phones=["05515115052"],
    ))
    sc2, s_new2 = db.upsert(con, _tr_job(
        "مطلوب كيميائي أو مهندس أغذية بخبرة في الزيوت النباتية",
        source="salamjobs", post_url="https://salamjobs.com/jobs/chem-1", phones=["05515115052"],
    ))
    assert s_new1 is True and s_new2 is False and sc2 == sc1

    # Same phone + distinct roles OR distinct neighborhoods must NOT merge
    d1, d_new1 = db.upsert(con, _tr_job(
        "مطلوب مدرس SAT Math وAP Calculus",
        source="salamjobs", post_url="https://salamjobs.com/jobs/sat-a", phones=["05343806011"],
    ))
    d2, d_new2 = db.upsert(con, _tr_job(
        "مطلوب مدرس لغة انكليزية دوام جزئي",
        source="salamjobs", post_url="https://salamjobs.com/jobs/eng-b", phones=["05343806011"],
    ))
    assert d_new1 is True and d_new2 is True and d1 != d2

    ph1, p_new1 = db.upsert(con, Job(
        title="صيدلانية للعمل في صيدلية في الطلياني", source="syrdrugs",
        post_url="https://t.me/syrdrugs/1", posted_at="2026-11-10T10:00:00+00:00",
        cities=["دمشق"], country="sy", phones=["0933999888"],
    ))
    ph2, p_new2 = db.upsert(con, Job(
        title="صيدلاني للعمل في صيدلية بالمزة", source="syrdrugs",
        post_url="https://t.me/syrdrugs/2", posted_at="2026-11-10T12:00:00+00:00",
        cities=["دمشق"], country="sy", phones=["0933999888"],
    ))
    assert p_new1 is True and p_new2 is True and ph1 != ph2


def test_worklink_closed_banner_and_strict_direct_apply_only():
    from datetime import date, timedelta
    import httpx
    from app.sources import web_portals as wp

    today_s = date.today().isoformat()
    future_s = (date.today() + timedelta(days=15)).isoformat()
    closed_worklink_html = f"""
    <html><head><title>رئيس دائرة الخطط والمتابعة</title></head><body>
      <div class="alert"><span>تم الانتهاء من التقديم على هذه الفرصة وهي لم تعد متاحة الآن</span></div>
      <main>
        <script type="application/ld+json">{{"@type":"JobPosting","title":"رئيس دائرة الخطط والمتابعة",
          "description":"دمشق","datePosted":"{today_s}","validThrough":"{future_s}",
          "hiringOrganization":{{"name":"وزارة التنمية الإدارية"}},
          "jobLocation":[{{"address":{{"addressCountry":"SY","addressLocality":"دمشق"}}}}]}}</script>
        <a href="https://apply.hiring.moad.gov.sy/single/98c785a437eb7769a5d5855d237c81fd">تقديم</a>
      </main>
    </body></html>
    """
    info = da.extract_apply_from_html(closed_worklink_html, "https://worklink.sy/jobs/demo")
    assert info["closed"] is True
    assert wp._parse_worklink_detail("https://worklink.sy/jobs/demo", closed_worklink_html, today_s) is None

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "worklink.sy" in u:
            return httpx.Response(200, headers={"content-type": "text/html"}, text=closed_worklink_html)
        if "reliefweb.int" in u or "jobinsyria.com" in u:
            return httpx.Response(202, headers={"content-type": "text/html"}, text="waf")
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        j_wl = Job(title="رئيس دائرة الخطط والمتابعة", source="sy_jobb", post_url="https://t.me/sy_jobb/1",
                   apply_url="https://worklink.sy/jobs/demo")
        assert da.resolve_job_apply(j_wl, client=c, cache={}) is False

        # WAF 202 / failed fetch on wrapper must never leave wrapper URL as apply_url
        j_rw = Job(title="MEAL Officer", source="reliefweb", post_url="https://reliefweb.int/job/1",
                   apply_url="https://reliefweb.int/job/1")
        assert da.resolve_job_apply(j_rw, client=c, cache={}) is False
        j_rw_email = Job(title="MEAL Officer", source="wazafne", post_url="https://t.me/w/1",
                         apply_url="https://reliefweb.int/job/1", email="hr@ngo.org")
        assert da.resolve_job_apply(j_rw_email, client=c, cache={}) is True
        assert j_rw_email.apply_url is None

    # Offline strict gate blocks wrappers, shorteners, aggregators, and Google Maps pins
    for bad in (
        "https://worklink.sy/jobs/1",
        "https://jobinsyria.com/job/1",
        "https://reliefweb.int/job/1",
        "https://unjobs.org/vacancies/1",
        "https://jobsintr.net/jobs/fvsales",
        "https://bit.ly/abc123",
        "https://shorturl.at/xyz",
        "https://maps.app.goo.gl/abc",
        "https://spc.sy/",
    ):
        j_bad = Job(title="مهندس", source="wazafne", post_url="https://t.me/w/2", apply_url=bad)
        assert da.resolve_job_apply(j_bad, client=None) is False


def test_tightened_ttl_and_no_expired_zombie_resurrection(tmp_path):
    from datetime import date, datetime, timedelta, timezone
    from app import config

    assert config.QUICK_DAYS <= 21
    assert config.FORMAL_DAYS <= 30

    con = db.connect(tmp_path / "ttl.db")
    old_35d = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat(timespec="seconds")
    j_old = Job(
        title="رئيس دائرة الخطط والمتابعة",
        source="sy_jobb",
        post_url="https://t.me/sy_jobb/99",
        posted_at=old_35d,
        cities=["دمشق"],
        kind="formal",
        apply_url="https://apply.hiring.moad.gov.sy/single/test123",
    )
    assert db.expires_on(j_old) < date.today().isoformat()
    jid_old, _ = db.upsert(con, j_old)
    assert db.expire_past(con) >= 1
    assert con.execute("SELECT status FROM jobs WHERE id=?", (jid_old,)).fetchone()[0] == "expired"

    # Expired official deadline cannot be resurrected by an undated duplicate mirror
    past_dl = (date.today() - timedelta(days=5)).isoformat()
    j_exp = Job(
        title="أخصائي مشاريع تنموية في دمشق",
        source="moad",
        post_url="https://t.me/SyMOAD/1",
        posted_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds"),
        cities=["دمشق"],
        deadline=past_dl,
        apply_url="https://apply.hiring.moad.gov.sy/single/exp123",
    )
    jid_exp, _ = db.upsert(con, j_exp, status="expired")
    j_mirror = Job(
        title="أخصائي مشاريع تنموية في دمشق",
        source="sy_jobb",
        post_url="https://t.me/sy_jobb/2",
        posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cities=["دمشق"],
        apply_url="https://apply.hiring.moad.gov.sy/single/exp123",
    )
    jid_dup, is_new = db.upsert(con, j_mirror)
    assert is_new is False and jid_dup == jid_exp
    assert con.execute("SELECT status FROM jobs WHERE id=?", (jid_exp,)).fetchone()[0] == "expired"


def test_cross_source_tr_duplicates_and_composite_job_keys(tmp_path):
    """Cross-source TR blurbs/headlines dedup even when a contact phone holds multiple distinct roles."""
    from datetime import datetime, timezone

    con = db.connect(tmp_path / "cross_tr.db")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Clinic phone 05339643455 posts two DIFFERENT roles first, then a mirror of role #2 arrives with a truncated blurb title.
    r1 = Job(
        title="موظفين مبيعات عقارية (Real Estate Sales Executive)",
        source="jobsintr",
        post_url="https://t.me/jobsintr/10",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05339643455"],
        whatsapp=True,
    )
    r2 = Job(
        title="سيدة متفرغة للعمل في التنظيف والضيافة",
        source="jobsintr",
        post_url="https://t.me/jobsintr/11",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05339643455"],
        whatsapp=True,
        raw="📢 مطلوب سيدة متفرغة للعمل في التنظيف والضيافة\nتعلن عيادة أسنان راقية عن توفر فرصة عمل",
    )
    r2_mirror = Job(
        title="تعلن عيادة أسنان راقية في إسطنبول عن توفر فرصة عمل لسيدة للانضمام إلى فريقنا، للعمل في مجال",
        source="jobsintr_web",
        post_url="https://jobsintr.net/jobs/clean1",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05339643455"],
        whatsapp=True,
        raw="📢 مطلوب سيدة متفرغة للعمل في التنظيف والضيافة\nتعلن عيادة أسنان راقية في إسطنبول عن توفر فرصة عمل لسيدة للانضمام إلى فريقنا",
    )
    id1, new1 = db.upsert(con, r1)
    id2, new2 = db.upsert(con, r2)
    id2m, new2m = db.upsert(con, r2_mirror)
    assert new1 and new2 and id1 != id2
    assert not new2m and id2m == id2
    # Crisp title is preserved instead of overwritten by truncated blurb
    assert con.execute("SELECT title FROM jobs WHERE id=?", (id2,)).fetchone()[0] == "سيدة متفرغة للعمل في التنظيف والضيافة"

    # 2. Stem normalization merges single-token role equivalence ("محاسب/ة" vs "محاسب / محاسبة")
    a1 = Job(title="محاسب/ة", source="jobsintr", post_url="https://t.me/jobsintr/20", posted_at=now_iso, country="tr", cities=["إسطنبول"], email="info@tolin-co.com")
    a2 = Job(title="محاسب / محاسبة", source="jobsintr_web", post_url="https://jobsintr.net/jobs/acc1", posted_at=now_iso, country="tr", cities=["إسطنبول"], email="info@tolin-co.com")
    aid1, _ = db.upsert(con, a1)
    aid2, a_new = db.upsert(con, a2)
    assert not a_new and aid2 == aid1

    # 3. Call Center + Dental Medical Consultant sharing clinic phone & template header stay separate
    c1 = Job(
        title="English-speaking Call Center Agent",
        source="jobsintr",
        post_url="https://t.me/jobsintr/30",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05521247615"],
        raw="ESTE PRO Clinic is hiring an English-speaking Call Center Agent\nLocation: Beylikduzu",
    )
    c2 = Job(
        title="Medical Consultant with experience in dentistry",
        source="jobsintr_web",
        post_url="https://jobsintr.net/jobs/med1",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05521247615"],
        raw="ESTE PRO Clinic is hiring an English-speaking Call Center Agent\nLocation: Beylikduzu",
    )
    cid1, _ = db.upsert(con, c1)
    cid2, c_new = db.upsert(con, c2)
    assert c_new and cid2 != cid1

    # 4. Syndicate/multi-role separation: syrdrugs single-word role, ahcl engineers, bahri managers stay distinct
    s1 = Job(title="صيدلانية", source="syrdrugs", post_url="https://t.me/syrdrugs/1", posted_at=now_iso, country="sy", cities=["دمشق"], phones=["0934415484"])
    s2 = Job(title="صيدلاني/ة للعمل في صيدلية في ركن الدين", source="syrdrugs", post_url="https://t.me/syrdrugs/2", posted_at=now_iso, country="sy", cities=["دمشق"], phones=["0934415484"])
    e1 = Job(title="مهندس تقنية معلومات", source="wazafne", post_url="https://t.me/w/101", posted_at=now_iso, country="sy", cities=["دمشق"], email="admin@ahcl.sy")
    e2 = Job(title="مهندس كهرباء", source="wazafne", post_url="https://t.me/w/102", posted_at=now_iso, country="sy", cities=["دمشق"], email="admin@ahcl.sy")
    sid1, _ = db.upsert(con, s1)
    sid2, s_new = db.upsert(con, s2)
    eid1, _ = db.upsert(con, e1)
    eid2, e_new = db.upsert(con, e2)
    assert s_new and sid1 != sid2
    assert e_new and eid1 != eid2
    con.close()


def test_laqeet_whatsapp_branding_and_source_scrubbing(tmp_path):
    """External source strings and third-party ?text= params are scrubbed; WhatsApp uses منصة لقيت."""
    from datetime import datetime, timezone
    from urllib.parse import unquote
    from fastapi.testclient import TestClient
    from app import api, direct_apply as da
    from app.sources import turkey_portals as tp

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. direct_apply strips third-party ?text= on wa.me links
    jw = Job(
        title="مسؤول دعم تقني",
        source="jobinsyria",
        post_url="https://jobinsyria.com/job/99",
        posted_at=now_iso,
        apply_url="https://wa.me/963932158719?text=Job+In+Syria+test",
    )
    assert da.resolve_job_apply(jw, client=None) is True
    assert jw.apply_url == "https://wa.me/963932158719"
    assert "0932158719" in jw.phones

    # 2. turkey_portals._build_tr_job drops duplicate wrapper details_url == post_url
    trj = tp._build_tr_job(
        title="مطلوب محاسب في إسطنبول",
        source="salamjobs",
        post_url="https://salamjobs.com/job/123",
        posted_at=now_iso,
        body_text="مطلوب محاسب للعمل بدوام كامل 05519685915",
        details_url="https://salamjobs.com/job/123",
    )
    assert trj is not None and trj.details_url is None

    # 3. DB row_to_dict and /j/{id} scrub source tails & use منصة لقيت in WhatsApp link
    tmp_db = db.connect(tmp_path / "scrub.db")
    old_con = api.con
    api.con = tmp_db
    api._stats_cache.clear()
    try:
        j = Job(
            title="موظف دعم تقني",
            source="jobinsyria",
            post_url="https://jobinsyria.com/job/77",
            posted_at=now_iso,
            country="sy",
            cities=["دمشق"],
            employer="جهة توظيف عبر Job In Syria",
            phones=["0932158719"],
            whatsapp=True,
            tags=["شواغر محلية", "Job In Syria"],
            raw="موظف دعم تقني · 💎💎 المصدر: فريلانس سوريا",
        )
        jid, _ = db.upsert(tmp_db, j)
        row = tmp_db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        dto = db.row_to_dict(tmp_db, row)
        assert not dto.get("employer")
        assert "المصدر:" not in (dto.get("summary") or "")
        assert "Job In Syria" not in (dto.get("tags") or [])

        c = TestClient(api.app)
        share_html = c.get(f"/j/{jid}").text
        assert "https://wa.me/963932158719?text=" in share_html
        decoded = unquote(share_html)
        assert "عبر منصة لقيت" in decoded
        assert "فريلانس سوريا" not in decoded
    finally:
        api._stats_cache.clear()
        api.con = old_con
        tmp_db.close()


def test_migrate_upgrades_legacy_job_keys_and_backfills_cp(tmp_path):
    """Opening a pre-migration DB upgrades job_keys PK and populates cp:/ce: keys."""
    import json
    import sqlite3
    from datetime import datetime, timezone

    legacy_path = tmp_path / "legacy.db"
    raw_con = sqlite3.connect(legacy_path)
    raw_con.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fp TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, source TEXT NOT NULL,
            country TEXT DEFAULT 'sy', sector TEXT DEFAULT 'private', apply_types TEXT DEFAULT '',
            education TEXT, education_from TEXT, field TEXT NOT NULL, subfield TEXT,
            kind TEXT NOT NULL, official INTEGER NOT NULL DEFAULT 0, cities TEXT NOT NULL,
            experience INTEGER, deadline TEXT, posted_at TEXT NOT NULL, expires_on TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'live', reports INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, first_seen TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE job_keys (k TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
        CREATE TABLE job_sources (job_id INTEGER NOT NULL, source TEXT NOT NULL, post_url TEXT NOT NULL, posted_at TEXT);
        CREATE TABLE posts (channel TEXT NOT NULL, id INTEGER NOT NULL, date TEXT, text TEXT, links TEXT, jobs INTEGER DEFAULT 0, PRIMARY KEY(channel, id));
        CREATE TABLE pdfs (name TEXT PRIMARY KEY, status TEXT, cards INTEGER, at TEXT);
        CREATE TABLE reports (job_id INTEGER, reason TEXT, at TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE bot_users (chat_id INTEGER PRIMARY KEY);
        CREATE TABLE bot_sent (chat_id INTEGER, job_id INTEGER, at TEXT, PRIMARY KEY(chat_id, job_id));
        CREATE VIRTUAL TABLE jobs_fts USING fts5(job_id UNINDEXED, title_text, employer_text, specs_text, body_text, aux_text, tokenize='unicode61');
        """
    )
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "title": "طباخ ماهر", "source": "jobsintr", "post_url": "https://t.me/jobsintr/99",
        "posted_at": now_iso, "country": "tr", "cities": ["إسطنبول"], "phones": ["05526444888"],
    }
    raw_con.execute(
        "INSERT INTO jobs (fp, title, source, country, sector, apply_types, field, kind, official, cities, posted_at, expires_on, status, data, first_seen, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("fp1", "طباخ ماهر", "jobsintr", "tr", "private", "", "other", "formal", 0, "|إسطنبول|", now_iso, "2027-01-01", "live", json.dumps(payload), now_iso, now_iso),
    )
    raw_con.execute("INSERT INTO jobs_fts (job_id, title_text, employer_text, specs_text, body_text, aux_text) VALUES (1,'طباخ','','','','')")
    raw_con.commit()
    raw_con.close()

    migrated = db.connect(legacy_path)
    ddl = migrated.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='job_keys'").fetchone()[0]
    assert "PRIMARY KEY (k, job_id)" in ddl
    keys = {r[0] for r in migrated.execute("SELECT k FROM job_keys WHERE job_id=1").fetchall()}
    assert any(k.startswith("cp:tr:05526444888|") for k in keys)
    migrated.close()


def test_case_insensitive_norm_title_first_and_single_url_dedup(tmp_path):
    """Mixed-case EN tokens in raw, long non-blurb titles, and same single-job post_url merge cleanly."""
    from datetime import datetime, timezone

    con = db.connect(tmp_path / "url_case.db")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Mixed-case English role tokens in raw + single Arabic keyword ("حجوزات")
    b1 = Job(
        title="موظفة حجوزات سياحية",
        source="jobsintr",
        post_url="https://t.me/jobsintr/501",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        email="dalya@isaretr.com",
        raw="مطلوب موظفة حجوزات سياحية – عن بعد Reservations & Sales Specialist",
    )
    b2 = Job(
        title="مطلوب موظفة حجوزات سياحية عن بُعد Reservations & Sales Specialist",
        source="salamjobs",
        post_url="https://salamjobs.com/jobs/res-sales-1",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        email="dalya@isaretr.com",
        raw="إعلان توظيف | موظفة حجوزات سياحية",
    )
    id_b1, new_b1 = db.upsert(con, b1)
    id_b2, new_b2 = db.upsert(con, b2)
    assert new_b1 and not new_b2 and id_b2 == id_b1

    # 2. Long >80-char title whose raw starts with company name still matches concise headline
    t1 = Job(
        title="مترجمة إدارية",
        source="jobsintr",
        post_url="https://t.me/jobsintr/502",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05315243168"],
    )
    t2 = Job(
        title="مطلوب مترجمة إدارية تجيد التركية والإنجليزية والعربية لحملة الجنسية أو الإقامة أو الكيملك",
        source="salamjobs",
        post_url="https://salamjobs.com/jobs/trans-admin-1",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05315243168"],
        raw="مشفى طبي في اسطنبول بحاجة الى مترجمة حصرا للعمل كمترجمة إدارية",
    )
    id_t1, _ = db.upsert(con, t1)
    id_t2, new_t2 = db.upsert(con, t2)
    assert not new_t2 and id_t2 == id_t1

    # 3. Single-job portal post_url merges even if title wording is slightly edited later
    w1 = Job(
        title="تعلن شركة تعمل في مجال الاستيراد عن حاجتها إلى موظفات",
        source="jobsintr_web",
        post_url="https://jobsintr.net/jobs/rltgary",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05533675257"],
    )
    w2 = Job(
        title="تعلن شركة تعمل في مجال الاستيراد عن حاجتها إلى موظفات للعمل ضمن القسم",
        source="jobsintr_web",
        post_url="https://jobsintr.net/jobs/rltgary",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05533675257"],
    )
    id_w1, _ = db.upsert(con, w1)
    id_w2, new_w2 = db.upsert(con, w2)
    assert not new_w2 and id_w2 == id_w1

    # 4. Multi-role MOAD ministry posts sharing the exact same post_url never collapse into one row
    m1 = Job(
        title="مهندس معلوماتية",
        source="moad",
        post_url="https://t.me/SyMOAD/9093",
        posted_at=now_iso,
        country="sy",
        cities=["دمشق"],
        official=True,
        apply_url="https://apply.hiring.moad.gov.sy/single/abc",
    )
    m2 = Job(
        title="محاسب رئيسي",
        source="moad",
        post_url="https://t.me/SyMOAD/9093",
        posted_at=now_iso,
        country="sy",
        cities=["دمشق"],
        official=True,
        apply_url="https://apply.hiring.moad.gov.sy/single/abc",
    )
    id_m1, _ = db.upsert(con, m1)
    id_m2, new_m2 = db.upsert(con, m2)
    assert new_m2 and id_m2 != id_m1
    con.close()


def test_syrian_gov_link_separation_and_rtl_cleanup():
    from app import collect
    from app.sources import moad_announcement
    from app.sources.telegram import Post

    # 1. Strip hidden RTL/LTR directional marks & trailing punctuation + prepend https://
    dirty = "\u200fbit.ly/applyingGov2026\u200e،"
    assert da.clean_url(dirty) == "https://bit.ly/applyingGov2026"
    found = tx.urls("رابط التقديم: \u200fbit.ly/applyingGov2026\u200e وبطاقة الشاغر https://bit.ly/hiringGov2026.")
    assert found == ["https://bit.ly/applyingGov2026", "https://bit.ly/hiringGov2026"]

    # 2. Gov link classifier separates direct forms (Nextcloud / Kobo / bit.ly/applying) from bit.ly/hiring info card
    text_gov = (
        "تعلن وزارة التنمية الإدارية بالتنسيق مع وزارة الطاقة عن توفر شواغر في دمشق وحمص\n"
        "بطاقة الوصف الوظيفي: bit.ly/hiringEnergy99\u200f\n"
        "استمارة التقديم المباشرة: https://cloud.moenergy.gov.sy/index.php/apps/forms/s/xyz987\u200f!\n"
        "بديل عبر كوبو: https://kf.kobotoolbox.org/x/abc123\u200e\n"
        "آخر موعد للتقديم 30/11/2026"
    )
    gov_res = da.extract_gov_links(text_gov)
    assert gov_res["best_apply_url"] == "https://cloud.moenergy.gov.sy/index.php/apps/forms/s/xyz987"
    assert gov_res["details_url"] == "https://bit.ly/hiringEnergy99"
    assert "https://kf.kobotoolbox.org/x/abc123" in gov_res["all_apply_urls"]
    assert da.is_direct_form_or_ats("https://cloud.moenergy.gov.sy/index.php/apps/forms/s/xyz987")
    assert da.is_direct_form_or_ats("https://kf.kobotoolbox.org/x/abc123")
    assert da.is_direct_form_or_ats("https://hr.mofaex.gov.sy/apply/12")

    # 3. moad_announcement + portal_job preserve apply_url directly without dropping scheme-less/Nextcloud forms
    post = Post(id=9901, channel="SyMOAD", date="2026-11-16T10:00:00+00:00", text=text_gov, links=[])
    ann = moad_announcement.parse(post)
    assert ann is not None
    assert ann.hiring_url == "https://bit.ly/hiringEnergy99"
    assert ann.apply_url == "https://cloud.moenergy.gov.sy/index.php/apps/forms/s/xyz987"
    pj = collect.portal_job(ann)
    assert pj.apply_url == "https://cloud.moenergy.gov.sy/index.php/apps/forms/s/xyz987"
    assert pj.details_url == "https://bit.ly/hiringEnergy99"

    # 4. General extractor separates bit.ly/applying -> apply_url, bit.ly/hiring -> details_url, supports «المنصب :»
    #    and ignores WorkLink numeric slugs like ...790565 in _TR_PHONE_IN_TEXT check
    sy_text = (
        "تعلن وزارة الصحة السورية عن شاغر في دمشق\n"
        "المنصب : منسق لوجستي\n"
        "بطاقة الشاغر: https://bit.ly/hiringHealth1\n"
        "رابط التقديم: \u200fbit.ly/applyingHealth1\u200f"
    )
    extracted = ex.extract(
        sy_text,
        source="syrian_jobb",
        post_url="https://t.me/syrian_jobb/28191",
        posted_at="2026-11-15T10:00:00+00:00",
        links=["https://worklink.sy/job/some-role-790565"],
    )
    assert len(extracted) == 1
    assert extracted[0].title == "منسق لوجستي"
    assert extracted[0].apply_url == "https://bit.ly/applyingHealth1"
    assert extracted[0].details_url == "https://bit.ly/hiringHealth1"
    assert extracted[0].official is True

    # Genuine Turkish WhatsApp number on a Syria channel is still blocked
    tr_fake = ex.extract(
        "مطلوب محاسب للعمل في شركة بدمشق للتواصل واتساب wa.me/905551234567",
        source="syrian_jobb",
        post_url="https://t.me/syrian_jobb/28192",
    )
    assert tr_fake == []

    # SSF login-walled apply route is rejected
    assert da.is_disallowed_offline_url("https://recruitment.ssf.gov.sy/Applications/Apply/95") is True

    # Governorate header lines like «المحافظات المطلوب العمل فيها: دمشق» are never extracted as job titles
    moad_repost = (
        "تعلن وزارة التنمية الإدارية بالتنسيق مع وزارة النقل عن توفر شواغر\n"
        "🔹 المحافظات المطلوب العمل فيها: دمشق\n"
        "رابط التقديم: bit.ly/applying999"
    )
    assert ex.extract(moad_repost, source="syria110", post_url="https://t.me/syria110/9999") == []


def test_unjobnet_and_syria_jobs_scrapers_mocked(monkeypatch):
    from datetime import datetime, timezone
    from app.sources import web_portals as wp

    today_rss = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    today_iso = datetime.now(timezone.utc).date().isoformat()

    class _FakeResp:
        def __init__(self, status_code: int, text: str, url: str = ""):
            self.status_code = status_code
            self.text = text
            self.url = url

    list_html = '<a href="/jobs/detail/drc-protection-officer-90611122">Protection Officer</a><a href="/jobs/detail/undp-rfp-supply-90611123">RFP-123 Supply of Solar Panels</a>'
    detail_html = f"""
    <html><head><script type="application/ld+json">
    {{
      "@type": "JobPosting",
      "title": "Protection Officer",
      "hiringOrganization": {{"name": "Danish Refugee Council"}},
      "datePosted": "{today_iso}T09:00:00+00:00",
      "validThrough": "2026-12-30T23:59:00+00:00",
      "jobLocation": {{"address": {{"addressLocality": "Aleppo", "addressCountry": "Syria"}}}},
      "description": "<p>University degree required. Apply online or email hr@drc-syria.example</p>"
    }}
    </script></head><body></body></html>
    """
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel>
        <item>
          <title>محاسب مالي</title>
          <link>https://syria-jobs.com/acc-1/</link>
          <pubDate>{today_rss}</pubDate>
          <content:encoded><![CDATA[
            <p>محاسب مالي</p>
            <p>شركة النور للتجارة</p>
            <p>الجنـس : ذكر</p>
            <p>مقر المؤسسة : دمشق</p>
            <p>للتواصل <a href="mailto:careers@alnoor-sy.example">careers@alnoor-sy.example</a> او <a href="mailto:info@syria-jobs.com">info</a> وهاتف 0946111729</p>
          ]]></content:encoded>
        </item>
        <item>
          <title>مهندس مدني يبحث عن عمل</title>
          <link>https://syria-jobs.com/seeker-1/</link>
          <pubDate>{today_rss}</pubDate>
          <content:encoded><![CDATA[
            <p>أنا مهندس مدني أبحث عن عمل في دمشق هاتف 0933112233</p>
          ]]></content:encoded>
        </item>
      </channel>
    </rss>"""

    class _FakeClient:
        def get(self, url, **kw):
            if "unjobnet.org/countries/syria?page=2" in url:
                return _FakeResp(200, "<html></html>", url)
            if "unjobnet.org/countries/syria" in url:
                return _FakeResp(200, list_html, url)
            if "90611122" in url:
                return _FakeResp(200, detail_html, url)
            if "90611123" in url:
                return _FakeResp(200, detail_html.replace("Protection Officer", "RFP-123 Supply of Solar Panels"), url)
            if "syria-jobs.com/category/" in url and "paged=2" in url:
                return _FakeResp(200, "<rss><channel></channel></rss>", url)
            if "syria-jobs.com/category/" in url:
                return _FakeResp(200, rss_xml, url)
            return _FakeResp(404, "", url)

    fc = _FakeClient()
    un_jobs = wp.scrape_unjobnet_syria(fc)
    assert len(un_jobs) == 1
    assert un_jobs[0].source == "unjobnet_sy"
    assert un_jobs[0].employer == "Danish Refugee Council"
    assert "حلب" in un_jobs[0].cities
    assert un_jobs[0].email == "hr@drc-syria.example"
    assert un_jobs[0].apply_url == "https://www.unjobnet.org/apply/90611122"

    sj_jobs = wp.scrape_syria_jobs_com(fc)
    assert len(sj_jobs) == 1
    assert sj_jobs[0].source == "syria_jobs"
    assert sj_jobs[0].employer == "شركة النور للتجارة"
    assert sj_jobs[0].email == "careers@alnoor-sy.example"  # never info@syria-jobs.com
    assert sj_jobs[0].phones == ["0946111729"]
    assert "دمشق" in sj_jobs[0].cities


def test_turkey_districts_textile_crafts_and_seeker_filter():
    from app import search

    # 1. Turkish city & Istanbul district Arabic spellings
    assert tx.tr_cities_from_text("مطلوب عمال في شيرين ايفر قرب زيتون بورنو") == ["إسطنبول"]
    assert tx.tr_cities_from_text("عمل في ايوب سلطان او قاضي كوي") == ["إسطنبول"]
    assert tx.tr_cities_from_text("ورشة مفروشات في إينيغول") == ["بورصة"]
    assert tx.tr_cities_from_text("وظيفة في خطاي") == ["هاتاي (أنطاكيا)"]

    # 2. Textile, restaurant crafts, real-estate, and medical tourism classification
    assert tx.field_from_text("مطلوب معلم درزة وأورطجي وإمبلاج وكوي") == "industry"
    assert tx.field_from_text("مطلوب قصاص وموديليست في ورشة ألبسة") == "industry"
    assert tx.field_from_text("مطلوب كابتن صالة وغرسون لمطعم") == "hospitality"
    assert tx.field_from_text("مطلوب مستشار عقاري في بيليك دوزو") == "sales"
    assert tx.field_from_text("مطلوب منسق السياحة العلاجية") == "sales"
    # Verify "كاديكوي" substring does not falsely match "كوي" as industry
    assert tx.field_from_text("مطلوب محاسب مالي في كاديكوي") == "business"

    # 3. Search synonyms include textile & restaurant terms
    groups = search.expand_query_groups("أورطجي")
    assert any("درزه" in g["variants"] or "خياطه" in g["variants"] for g in groups)

    # 4. Candidate seeker CVs are rejected while employer ads pass
    assert spam.is_job_post("أنا شاب أبحث عن عمل خياط في إسطنبول") is False
    assert spam.is_job_post("بدي شغل بمطعم في الفاتح") is False
    assert spam.is_job_post("مطلوب خياط درزة للعمل في إسطنبول واتساب 05315243168") is True


def test_turkey_reliefweb_unjobnet_and_seed_roundtrip(tmp_path):
    from datetime import datetime, timezone
    from app import seed
    from app.sources import reliefweb as rw, web_portals as wp

    today_iso = datetime.now(timezone.utc).date().isoformat()
    rss_tr = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Logistics Officer - IOM (Gaziantep)</title>
        <link>https://reliefweb.int/job/999001/logistics-officer</link>
        <description>&lt;p&gt;Duty station: Gaziantep, Türkiye. Apply at https://forms.gle/testtr1&lt;/p&gt;</description>
        <pubDate>Tue, 17 Nov 2026 08:00:00 +0000</pubDate>
      </item>
    </channel></rss>"""
    rw_jobs = rw.parse_reliefweb_rss(rss_tr, country="tr")
    assert len(rw_jobs) == 1
    assert rw_jobs[0].country == "tr"
    assert rw_jobs[0].sector == "ngo"
    assert "غازي عنتاب" in rw_jobs[0].cities
    assert rw_jobs[0].apply_url == "https://forms.gle/testtr1"

    class _FakeResp:
        def __init__(self, status_code: int, text: str, url: str = ""):
            self.status_code = status_code
            self.text = text
            self.url = url

    list_html = '<a href="/jobs/detail/iom-finance-istanbul-90788899">Finance Associate</a>'
    detail_html = f"""
    <html><head><script type="application/ld+json">
    {{
      "@type": "JobPosting",
      "title": "Finance Associate - Istanbul",
      "hiringOrganization": {{"name": "IOM"}},
      "datePosted": "{today_iso}T09:00:00+00:00",
      "validThrough": "2026-12-30T23:59:00+00:00",
      "jobLocation": {{"address": {{"addressLocality": "Istanbul", "addressCountry": "Türkiye"}}}},
      "description": "<p>Apply online via IOM portal.</p>"
    }}
    </script></head><body></body></html>
    """

    class _FakeClient:
        def get(self, url, **kw):
            if "countries/turkey?page=" in url:
                return _FakeResp(200, "<html></html>", url)
            if "countries/turkey" in url:
                return _FakeResp(200, list_html, url)
            if "90788899" in url:
                return _FakeResp(200, detail_html, url)
            return _FakeResp(404, "", url)

    un_tr = wp.scrape_unjobnet_turkey(_FakeClient())
    assert len(un_tr) == 1
    assert un_tr[0].source == "unjobnet_tr"
    assert un_tr[0].country == "tr"
    assert "إسطنبول" in un_tr[0].cities

    # Seed export roundtrip preserves job + meta (e.g. updated_at)
    src_db = tmp_path / "src.db"
    con_src = db.connect(src_db)
    db.upsert(con_src, rw_jobs[0])
    db.set_meta(con_src, "updated_at", "2026-11-18T00:00:00+00:00")
    con_src.commit()
    seed_gz = tmp_path / "seed.jsonl.gz"
    exported = seed.export_seed(con_src, path=seed_gz)
    con_src.close()
    assert exported == 1
    assert seed_gz.exists() and seed_gz.stat().st_size > 0


def test_recover_falsely_dropped_jobs_with_strict_rules(tmp_path):
    from datetime import datetime, timezone
    from app import extract as ex

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Turkey job where 💼 مزايا العمل: appears in body does not hijack title
    t_93730 = (
        "📢 مطلوب موظفين وموظفات كول سنتر أسنان 🇹🇷 (اسطنبول – باشاك شهير)\n"
        "💼 مزايا العمل: * راتب شهري عالي. * عمولات مجزية.\n"
        "📞 للتقديم عبر واتساب: https://wa.me/905497122233"
    )
    j_93730 = ex.extract(t_93730, "jobsintr", "https://t.me/jobsintr/93730", now_iso, country="tr")
    assert len(j_93730) == 1
    assert "كول سنتر" in j_93730[0].title
    assert "05497122233" in j_93730[0].phones

    # 2. Training academy hiring a real employee (not a training course ad)
    t_93734 = (
        "📢 وظيفة شاغرة | أكاديمية المسار الدولية للتدريب (MITA)\n"
        "مطلوب: مسؤول/ة إدارة صفحات التواصل الاجتماعي\n"
        "📍 إسطنبول\n"
        "للتقديم: https://forms.gle/uNixK8k5NgJzJAz9A"
    )
    j_93734 = ex.extract(t_93734, "jobsintr", "https://t.me/jobsintr/93734", now_iso, country="tr")
    assert len(j_93734) == 1
    assert "التواصل الاجتماعي" in j_93734[0].title
    assert j_93734[0].apply_url == "https://forms.gle/uNixK8k5NgJzJAz9A"

    # 3. Damascus clinic with valid email + a +90 number stays a Syria job via email
    t_14366 = (
        "فرصة عمل\n"
        "مطلوب موظفة استقبال لعيادة تجميل\n"
        "📍 دمشق – المزة - Kristal Clinic\n"
        "📧 Leen@kristalclinic.com\n"
        "📞 0905491697588"
    )
    j_14366 = ex.extract(t_14366, "higher_steps", "https://t.me/higher_steps/14366", now_iso, country="sy")
    assert len(j_14366) == 1
    assert j_14366[0].country == "sy"
    assert j_14366[0].email == "Leen@kristalclinic.com"
    assert j_14366[0].phones == []

    # 4. Generic person + experience prefix stripped cleanly
    t_8437 = "مطلوب شاب لديه خبرة جيدة في مجال تنظيف غرف السيارات في دمشق للتواصل 0933112233"
    j_8437 = ex.extract(t_8437, "syrianjobs2", "https://t.me/syrianjobs2/8437", now_iso, country="sy")
    assert len(j_8437) == 1
    assert "تنظيف غرف السيارات" in j_8437[0].title

    # 5. Paid NGO vacancy mentioning volunteers in intro passes, while volunteer call is blocked
    t_ngo = (
        "تعلن منظمة الهلال الأحمر التي تعتمد على كوادرها ومتطوعيها عن حاجتها لتعيين منسق مشاريع في دمشق\n"
        "للتقديم: hr@sarc-sy.org"
    )
    j_ngo = ex.extract(t_ngo, "wazafne175", "https://t.me/wazafne175/69542", now_iso, country="sy")
    assert len(j_ngo) == 1
    assert "منسق مشاريع" in j_ngo[0].title

    t_vol = "إعلان تطوع: مطلوب متطوعين للمشاركة في حملة النظافة في دمشق للتواصل 0933112233"
    assert ex.extract(t_vol, "wazafne175", "https://t.me/wazafne175/69999", now_iso, country="sy") == []

    # 6. Adwhit support phone 05385844484 is blocked; previously rejected jobinsyria row promotes to live on valid phone
    from app import direct_apply as da
    j_adw = Job(
        title="موظف مبيعات",
        source="adwhit_web",
        post_url="https://www.adwhit.com/job/123",
        posted_at=now_iso,
        country="tr",
        cities=["إسطنبول"],
        phones=["05385844484"],
    )
    assert da.resolve_job_apply(j_adw, client=None, strict=True) is False
    assert j_adw.phones == []
    con = db.connect(tmp_path / "promote.db")
    j_rej = Job(
        title="محاسب مالي",
        source="jobinsyria",
        post_url="https://jobinsyria.com/jobs_details/view/555",
        posted_at=now_iso,
        country="sy",
        cities=["دمشق"],
    )
    jid, _ = db.upsert(con, j_rej, status="rejected")
    assert con.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "rejected"

    j_with_phone = Job(
        title="محاسب مالي",
        source="jobinsyria",
        post_url="https://jobinsyria.com/jobs_details/view/555",
        posted_at=now_iso,
        country="sy",
        cities=["دمشق"],
        phones=["0944112233"],
    )
    jid2, is_new = db.upsert(con, j_with_phone)
    assert jid2 == jid and not is_new
    assert con.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "live"
    con.close()


def test_step2_new_direct_apply_sources_and_depth():
    from datetime import datetime, timezone
    from app.sources import web_portals as wp

    today_iso = datetime.now(timezone.utc).date().isoformat()

    class _Resp:
        def __init__(self, status_code: int, text: str = "", headers: dict | None = None, json_data=None):
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}
            self._json = json_data

        def json(self):
            return self._json

    class _FakeClient:
        def get(self, url, **kw):
            # 1. Shafak WP API (rewrites /preview -> /viewform)
            if "shafak.org/wp-json" in url:
                return _Resp(
                    200,
                    "[{}]",
                    json_data=[
                        {
                            "date_gmt": f"{today_iso}T09:00:00",
                            "link": "https://shafak.org/job-finance-dz/",
                            "title": {"rendered": "مسؤول/ة مالي/ة ميداني/ة | سوريا – دير الزور"},
                            "content": {"rendered": '<p><a href="https://docs.google.com/forms/d/1MEjht1mtJnoqbpnRxDmn/preview">Apply</a></p>'},
                        },
                        {
                            "date_gmt": f"{today_iso}T09:00:00",
                            "link": "https://shafak.org/tender-1/",
                            "title": {"rendered": "مناقصة توريد أدوية"},
                            "content": {"rendered": '<p><a href="https://forms.gle/abc">Apply</a></p>'},
                        },
                    ],
                )
            # 2. DRC jobs list + detail
            if url == "https://job.drc.ngo/jobs/":
                html = (
                    '<article class="jobList__item" data-country="Syria" data-deadline="12/20/2026 10:59:59 PM" '
                    'data-published="11/18/2026 5:44:27 PM" data-title="Finance Coordinator">'
                    '<a href="/jobs/job?id=177225">Read more</a></article>'
                    '<article class="jobList__item" data-country="Cameroon" data-deadline="12/20/2026 10:59:59 PM" '
                    'data-published="11/18/2026 5:44:27 PM" data-title="Other Job">'
                    '<a href="/jobs/job?id=999999">Read more</a></article>'
                )
                return _Resp(200, html)
            if "job.drc.ngo/jobs/job?id=177225" in url:
                return _Resp(
                    200,
                    '<html><body><h1>Finance Coordinator - Damascus</h1>'
                    '<a href="https://candidate.hr-manager.net/ApplicationInit.aspx?cid=1036&ProjectId=177225">Apply</a></body></html>',
                )
            # 3. Impactpool Turkey (keeps WFP paid job, drops UNV volunteer job)
            if "impactpool.org/search?countries%5B%5D=Turkey" in url:
                return _Resp(200, '<a href="/jobs/1248365">WFP</a><a href="/jobs/1248672">UNV</a>')
            if url.endswith("/jobs/1248365/apply"):
                return _Resp(301, headers={"location": "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings/job/Ankara-Turkiye-PM"})
            if url.endswith("/jobs/1248672/apply"):
                return _Resp(301, headers={"location": "https://app.unv.org/opportunities/1784888021274141"})
            if url.endswith("/jobs/1248365"):
                return _Resp(
                    200,
                    "<main><h1>Logistics Capacity Strengthening Project Manager</h1>"
                    "<p>WFP - World Food Programme</p><p>Ankara</p>"
                    "<p>Application deadline: December 20, 2026</p></main>",
                )
            # 4. ACTED Syria
            if "acted.org/en/get-involved/join-us/vacancies/" in url:
                return _Resp(200, '<a href="https://www.acted.org/en/jobs/syria-country-security-manager-damascus/">Job</a>')
            if "acted.org/en/jobs/syria-country-security-manager-damascus/" in url:
                return _Resp(200, "<main><h1>Syria Country Security Manager - Damascus</h1><p>Damascus, Syria</p></main>")
            return _Resp(404, "")

        def post(self, url, **kw):
            if "myworkdaysite.com/wday/cxs/wfp" in url:
                return _Resp(
                    200,
                    "{}",
                    json_data={
                        "jobPostings": [
                            {
                                "title": "Programme Officer",
                                "externalPath": "/job/Beirut-Lebanon/Programme-Officer",
                                "locationsText": "Beirut, Lebanon",
                            },
                            {
                                "title": "Logistics Project Manager",
                                "externalPath": "/job/Ankara-Turkiye/Logistics-PM",
                                "locationsText": "Ankara, Turkiye",
                            },
                        ]
                    },
                )
            return _Resp(404, "")

    cli = _FakeClient()
    sh = wp.scrape_shafak(cli)
    assert len(sh) == 1
    assert "دير الزور" in sh[0].cities
    assert sh[0].apply_url == "https://docs.google.com/forms/d/1MEjht1mtJnoqbpnRxDmn/viewform"

    drc = wp.scrape_drc(cli, country="sy")
    assert len(drc) == 1
    assert drc[0].deadline == "2026-12-20"
    assert "hr-manager.net" in (drc[0].apply_url or "")

    imp_tr = wp.scrape_impactpool_turkey(cli)
    assert len(imp_tr) == 1
    assert imp_tr[0].country == "tr"
    assert "أنقرة" in imp_tr[0].cities
    assert "myworkdaysite.com" in (imp_tr[0].apply_url or "")
    assert imp_tr[0].deadline == "2026-12-20"

    act = wp.scrape_acted(cli, country="sy")
    assert len(act) == 1
    assert act[0].apply_url == "https://www.acted.org/en/jobs/syria-country-security-manager-damascus/#form"

    # WFP strictly excludes Lebanon when scraping Syria, and includes Ankara when scraping Turkey
    assert wp.scrape_wfp(cli, country="sy") == []
    wfp_tr = wp.scrape_wfp(cli, country="tr")
    assert len(wfp_tr) == 1
    assert "أنقرة" in wfp_tr[0].cities


def test_step3_channels_requirement_title_guard_and_srd_ar():
    from datetime import datetime, timezone
    from app import config, extract as ex
    from app.sources import web_portals as wp

    channels = config.load_channels()
    for handle in ("jobs963", "jobsportal1", "ExtraJobs", "takatuf2026", "tawasolsyria"):
        assert handle in channels
        assert channels[handle]["country"] == "sy"

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today_iso = datetime.now(timezone.utc).date().isoformat()

    post_txt = (
        "تعلن شركة بحري للصناعات الدوائية عن توفر الشواغر التالية:\n"
        "مندوب علمي _ طرطوس _\n"
        "- شهادة في الصيدلة\n"
        "- التفرغ التام للعمل\n"
        "E-mail : marketing@bahripharm.com"
    )
    jobs = ex.extract(post_txt, "jobsportal1", "https://t.me/jobsportal1/16862", now_iso, "formal", country="sy")
    assert len(jobs) == 1
    assert "مندوب علمي" in jobs[0].title
    assert "طرطوس" in jobs[0].cities
    assert jobs[0].email == "marketing@bahripharm.com"

    assert ex._good_title("خبرة قوية في إدارة وتنظيم المستودعات") is False
    assert ex._good_title("أمين مستودع") is True

    class _Resp:
        def __init__(self, status_code: int, text: str = "", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data

        def json(self):
            return self._json

    calls = []

    class _FakeSrdClient:
        def get(self, url, **kw):
            calls.append(url)
            if "/ar/wp-json/" in url:
                return _Resp(
                    200,
                    "[{}]",
                    json_data=[
                        {
                            "date_gmt": f"{today_iso}T09:00:00",
                            "link": "https://srd.org.tr/ar/jobs-vacancies-ar/nurse-2422/",
                            "title": {"rendered": "ممرضة — إدلب"},
                            "content": {"rendered": '<p><a href="https://forms.gle/srdtest1">Apply</a></p>'},
                        },
                        {
                            "date_gmt": f"{today_iso}T09:00:00",
                            "link": "https://srd.org.tr/jobs-vacancies/shared-1/",
                            "title": {"rendered": "Shared EN Link"},
                            "content": {"rendered": '<p><a href="https://forms.gle/srdshared">Apply</a></p>'},
                        },
                    ],
                )
            return _Resp(
                200,
                "[{}]",
                json_data=[
                    {
                        "date_gmt": f"{today_iso}T09:00:00",
                        "link": "https://srd.org.tr/jobs-vacancies/shared-1/",
                        "title": {"rendered": "Medical Officer - Idlib"},
                        "content": {"rendered": '<p><a href="https://forms.gle/srdshared">Apply</a></p>'},
                    }
                ],
            )

    srd_jobs = wp.scrape_srd(_FakeSrdClient())
    assert len(calls) == 2
    assert len(srd_jobs) == 2
    assert {j.post_url for j in srd_jobs} == {
        "https://srd.org.tr/jobs-vacancies/shared-1/",
        "https://srd.org.tr/ar/jobs-vacancies-ar/nurse-2422/",
    }

    from app import direct_apply as da

    # 1. Tail "تليغرام\nhttps://t.me/JobsPortal1" never yields title "تليغرام"
    tg_tail_post = (
        "تعلن شركة النور عن توفر الشاغر التالي:\n"
        "محاسب مالي\n"
        "التقديم عبر البريد: hr@alnoor-co.com\n"
        "تليغرام\n"
        "https://t.me/JobsPortal1\n"
    )
    tg_jobs = ex.extract(tg_tail_post, "jobsportal1", "https://t.me/jobsportal1/16627", now_iso, "formal", country="sy")
    assert len(tg_jobs) == 1
    assert tg_jobs[0].title == "محاسب مالي"
    assert all("تليغرام" not in j.title for j in tg_jobs)

    # 2. Hierarchical numbered roles extract ONLY top-level roles, never sub-bullets
    hier_post = (
        "تعلن شركة في دمشق عن الشواغر الوظيفية التالية:\n"
        "🔹 1- موظف/ة سكرتاريا\n"
        "• إجادة برامج Microsoft Office، وخاصة Word وExcel.\n"
        "• تنظيم الملفات والأرشفة الورقية.\n"
        "🔹 2- مسؤول مشتريات\n"
        "• خبرة عملية في مجال المشتريات.\n"
        "للتواصل: hr@pharma-example.com"
    )
    hier_jobs = ex.extract(hier_post, "jobs963", "https://t.me/jobs963/12190", now_iso, "formal", country="sy")
    assert [j.title for j in hier_jobs] == ["موظف/ة سكرتاريا", "مسؤول مشتريات"]

    # 3. Section stop before "المميزات المقدمة:" stops list extraction cleanly
    perks_post = (
        "تعلن شركة الحافظ عن حاجتها لتوظيف فنيين بالاختصاصات التالية:\n"
        "• كهرباء صناعية\n"
        "• لحام\n"
        "💼 المميزات المقدمة:\n"
        "• تأمين النقل.\n"
        "• تدريب وتطوير مهني وفني.\n"
        "للتقديم واتساب: 0991444559"
    )
    perks_jobs = ex.extract(perks_post, "jobs963", "https://t.me/jobs963/12220", now_iso, "formal", country="sy")
    assert [j.title for j in perks_jobs] == ["كهرباء صناعية", "لحام"]

    # 4. English multi-role header ("following positions:") parses list items
    en_multi = (
        "HAND is currently recruiting for the following positions:\n"
        "🔹 Field Officer - Syria - Hama\n"
        "🔹 Counselor - Syria - Aleppo\n"
        "Apply: career.sy@hand.ngo"
    )
    en_jobs = ex.extract(en_multi, "jobsportal1", "https://t.me/jobsportal1/16700", now_iso, "formal", country="sy")
    assert len(en_jobs) == 2
    assert {j.title for j in en_jobs} == {"Field Officer", "Counselor"}

    # 5. Tenders and e-commerce promos return [], and tender emails are blocked
    tender_post = "طلب عروض للتعاقد مع شركة متخصصة لتنفيذ مبادرات مجتمعية\nللتواصل: fcasyriatender@kirkonulkomaanapu.fi"
    promo_post = (
        "LIMITLESS Shopping\n"
        "منصة تسوق إلكتروني تهدف إلى جمع المتاجر والتجار لعرض منتجاتهم\n"
        "الفئات المستهدفة\n"
        "• أصحاب المحلات التجارية\n"
        "• المتسوقون\n"
        "للتواصل: 0933111222"
    )
    assert ex.extract(tender_post, "jobsportal1", "https://t.me/jobsportal1/16710", now_iso, "formal", country="sy") == []
    assert ex.extract(promo_post, "extrajobs", "https://t.me/ExtraJobs/5157", now_iso, "quick", country="sy") == []
    assert da._good_email("tenders@ashticenter.org") is False

    # 6. Elementor / Astra ast-no-sidebar layout with CTA outside <article> still extracts direct Google Form URL
    elementor_html = (
        "<html><body class='rtl ast-no-sidebar elementor-default'>"
        "<div class='site-content'><div class='elementor-section'>"
        "<a href='https://forms.gle/2QeAMNLcj9e7Dgih6'>قدم الآن</a>"
        "</div></div>"
        "</body></html>"
    )
    info = da.extract_apply_from_html(elementor_html, "https://srd.org.tr/ar/jobs-vacancies-ar/test/")
    assert info["apply_url"] == "https://forms.gle/2QeAMNLcj9e7Dgih6"

    # 7. Re-announcement header extracts the real role rather than "انضم إلى..."
    reann_post = "إعادة اعلان - مدربة خياطة نسائية\nانضم إلى مؤسسة سلام للأمل!\nللتقديم: hr@salam-sy.org"
    reann_jobs = ex.extract(reann_post, "jobsportal1", "https://t.me/jobsportal1/16720", now_iso, "formal", country="sy")
    assert len(reann_jobs) == 1
    assert reann_jobs[0].title == "مدربة خياطة نسائية"


def test_sector_field_official_public_sources_and_filter_matrix(tmp_path):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from app import api, db, extract as ex, taxonomy as tx
    from app.models import Job
    from app.sources import web_portals as wp

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Sector purity
    mashro_job = ex.extract(
        "مطلوب بائعة لمحل ألبسة في مشروع دمر، دمشق\n0933123456",
        "jobinsyria",
        "https://t.me/jobinsyria/1",
        now_iso,
        "quick",
        country="sy",
    )
    assert len(mashro_job) == 1
    assert mashro_job[0].sector == "private"

    assert tx.infer_sector(False, "media", "World Vision", country="sy", source="worklink") == "ngo"
    assert tx.infer_sector(False, "business", "الهيئة العالمية للإغاثة والتنمية (انسر)", country="sy", source="jobsportal1") == "ngo"
    priv_idlib = ex.extract(
        "تعلن شركة زمرد القابضة في محافظة إدلب عن حاجتها إلى موظفة موارد بشرية\nhr@zumurrud.com",
        "wazafne",
        "https://t.me/wazafne175/99001",
        now_iso,
        "formal",
        country="sy",
    )
    assert len(priv_idlib) == 1
    assert priv_idlib[0].official is False
    assert priv_idlib[0].sector == "private"
    assert ex.extract("تدعوكم مديرية التنمية الإدارية في حلب لحضور ندوة تفاعلية حول التحول الرقمي\nhttps://forms.gle/abc123", "SyMOAD", "https://t.me/SyMOAD/9999", now_iso, "formal", country="sy") == []
    assert ex.extract("مطلوب مشغل ببجي و فري فاير و جواكر\n0933444555", "syrianjobs", "https://t.me/syrianjobs2/9999", now_iso, "quick", country="sy") == []

    # 2. Field purity + no 'management' leak in UNjobnet
    assert tx.field_from_text("كابتن صالة", "مطعم وكافيه في دمشق") == "hospitality"
    assert tx.field_from_text("معلم ساندويش فلافل", "دوام مسائي") == "hospitality"
    assert tx.field_from_text("محلل بيانات", "عمل مكتبي في دمشق") == "computer"
    assert tx.field_from_text("بائعة", "محل منظفات خلف مستشفى المجتهد، دمشق") == "retail"

    class _UnResp:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    class _FakeUnClient:
        def get(self, url, **kw):
            if "www.unjobnet.org/jobs/detail/" in url:
                return _UnResp(
                    200,
                    "<html><head><script type='application/ld+json'>"
                    '{"@type":"JobPosting","title":"Senior Coordination Fellow",'
                    '"hiringOrganization":{"name":"UNDP"},'
                    '"description":"<p>Damascus, Syria https://forms.gle/untest1</p>"}'
                    "</script></head><body><h1>Senior Coordination Fellow</h1></body></html>",
                )
            return _UnResp(
                200,
                "<html><body><a href='/jobs/detail/senior-coordination-fellow-999001'>Senior Coordination Fellow</a></body></html>",
            )

    un_jobs = wp.scrape_unjobnet_country(_FakeUnClient(), slug="syria", country="sy", source="unjobnet_sy", max_pages=1, max_details=5)
    assert len(un_jobs) == 1
    assert un_jobs[0].field != "management"
    assert un_jobs[0].field == "ngo"

    # 3. official_public source visibility + /j/{id} + /api/jobs filter matrix
    test_con = db.connect(tmp_path / "filter_matrix.db")
    orig_con = api.con
    api.con = test_con
    try:
        j_gov = Job(
            title="مهندس مدني",
            source="moad",
            post_url="https://t.me/SyMOAD/100",
            posted_at=now_iso,
            employer="وزارة الإدارة المحلية والبيئة",
            cities=["دمشق"],
            field="engineering",
            official=True,
            country="sy",
            sector="gov",
            apply_url="https://forms.gle/govform1",
            education="bachelor",
        )
        j_ngo = Job(
            title="منسق صحة مجتمعية",
            source="srd",
            post_url="https://srd.org.tr/ar/jobs-vacancies-ar/100",
            posted_at=now_iso,
            employer="منظمة سوريا للإغاثة والتنمية (SRD)",
            cities=["إدلب"],
            field="health",
            country="sy",
            sector="ngo",
            apply_url="https://forms.gle/srdform1",
            education="bachelor",
        )
        j_priv = Job(
            title="محاسب",
            source="wazafne",
            post_url="https://t.me/wazafne175/100",
            posted_at=now_iso,
            employer="شركة النور التجارية",
            cities=["حلب"],
            field="business",
            country="sy",
            sector="private",
            phones=["0933123456"],
            education="none",
        )
        j_tr = Job(
            title="خياط درزة",
            source="jobsintr",
            post_url="https://t.me/jobsintr/100",
            posted_at=now_iso,
            employer="ورشة الأناضول",
            cities=["إسطنبول"],
            field="industry",
            country="tr",
            sector="private",
            phones=["05331234567"],
            education="none",
        )
        id_gov, _ = db.upsert(test_con, j_gov)
        id_ngo, _ = db.upsert(test_con, j_ngo)
        id_priv, _ = db.upsert(test_con, j_priv)
        db.upsert(test_con, j_tr)
        test_con.commit()

        c = TestClient(api.app)
        assert c.get(f"/api/jobs/{id_gov}").json()["sources"][0]["official_public"] is True
        assert c.get(f"/api/jobs/{id_ngo}").json()["sources"][0]["official_public"] is True
        assert c.get(f"/api/jobs/{id_priv}").json()["sources"][0]["official_public"] is False

        assert "المصدر:" in c.get(f"/j/{id_gov}").text
        assert "المصدر:" in c.get(f"/j/{id_ngo}").text
        assert "المصدر:" not in c.get(f"/j/{id_priv}").text

        assert [x["id"] for x in c.get("/api/jobs", params={"country": "sy", "sector": "gov"}).json()["jobs"]] == [id_gov]
        assert [x["id"] for x in c.get("/api/jobs", params={"country": "sy", "sector": "ngo"}).json()["jobs"]] == [id_ngo]
        assert [x["id"] for x in c.get("/api/jobs", params={"country": "sy", "sector": "private"}).json()["jobs"]] == [id_priv]
        tr_res = c.get("/api/jobs", params={"country": "tr", "edu_group": "nodegree"}).json()["jobs"]
        assert len(tr_res) == 1 and tr_res[0]["country"] == "tr" and tr_res[0]["cities"] == ["إسطنبول"]
    finally:
        api.con = orig_con
        test_con.close()


def test_hashtag_line_no_redos_and_wp_job_strips_css_footer():
    import time
    from app.sources import web_portals as wp

    css_colors = "; ".join(f"color:#a{i:05d}" for i in range(500))
    sy_payload = f"{css_colors}\nمطلوب ممرضة في إدلب"
    tr_payload = f"{css_colors}\nمطلوب خياط في إسطنبول"

    t0 = time.monotonic()
    sy_cities = tx.cities_from_text(sy_payload)
    tr_cities = tx.tr_cities_from_text(tr_payload)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2
    assert sy_cities == ["إدلب"]
    assert tr_cities == ["إسطنبول"]

    html_doc = (
        f"<html><head><style>body{{{css_colors};}}</style></head>"
        "<body><footer>Gaziantep, Turkey</footer>"
        "<main><p>مطلوب ممرضة للعمل في إدلب</p>"
        '<a href="https://forms.gle/abc123XYZ">تقديم</a></main></body></html>'
    )
    t1 = time.monotonic()
    job = wp._make_wp_job(
        title="ممرضة",
        source="srd",
        link="https://srd.org.tr/ar/jobs-vacancies-ar/nurse-idlib/",
        posted_iso="2026-11-18T10:00:00+00:00",
        employer="منظمة سوريا للإغاثة والتنمية (SRD)",
        content_html=html_doc,
        default_cities=["إدلب"],
        default_field="health",
    )
    assert time.monotonic() - t1 < 0.5
    assert job is not None
    assert job.cities == ["إدلب"]
    assert job.apply_url == "https://forms.gle/abc123XYZ"
    assert "#a00000" not in (job.raw or "")
    assert "body{" not in (job.raw or "")
    assert "Gaziantep" not in (job.raw or "")


def test_zero_sector_mixing_food_security_and_aleppo_neighborhoods():
    assert tx.infer_sector(False, "ngo", None, "فرصة عمل لدى المجلس النرويجي للاجئين (NRC)", hint="private") == "ngo"
    assert tx.infer_sector(False, "business", "International Medical Corps (IMC)", "HR Officer", hint="private") == "ngo"
    assert tx.infer_sector(False, "hospitality", "جمعية الزهراء", "موظفة كافيه في جمعية الزهراء حلب", hint="private") == "private"
    assert tx.field_from_text("Food Security & Livelihood Officer") == "ngo"
    assert tx.field_from_text("Senior Data Scientist, MNO Data") == "computer"
    assert tx.cities_from_text("كافيه لوسيل في حي الفرقان") == ["حلب"]
    assert tx.infer_sector(False, "other", None, "كاريتاس سورية - المكتب التنفيذي تعلن عن شاغر", hint="private") == "ngo"
    assert ex._good_title("دارة عزة") is False
    assert ex._good_title("ادعمولنا البوست لانو في ناس عبتستفيد") is False
    assert ex._good_title("إلى من يهمه الأمر") is False
