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

    assert config.QUICK_DAYS <= 14
    assert config.FORMAL_DAYS <= 21

    con = db.connect(tmp_path / "ttl.db")
    old_26d = (datetime.now(timezone.utc) - timedelta(days=26)).isoformat(timespec="seconds")
    j_old = Job(
        title="رئيس دائرة الخطط والمتابعة",
        source="sy_jobb",
        post_url="https://t.me/sy_jobb/99",
        posted_at=old_26d,
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
