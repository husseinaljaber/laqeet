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
