"""The seven bugs from the handoff, plus regressions found on real posts."""
from app.extract import extract
from tests.fixtures import posts as P


def jobs(text, source="tawzeef", kind="formal"):
    return extract(text, source, "https://t.me/x/1", "2026-11-07T10:00:00+00:00", kind)


def test_bug1_decorated_lines_are_not_jobs():
    titles = [j.title for j in jobs(P.TAWZEEF_LIST)]
    assert titles == ["مروجة", "مندوب مبيعات", "مصمم/ة جرافيك", "فني كهرباء"]


def test_bug2_requirement_bullets_are_not_titles():
    js = jobs(P.WAZAFNE_NGO, "wazafne")
    assert len(js) == 1 and "متابعة وتقييم" in js[0].title
    assert js[0].education == "bachelor" and js[0].experience == 3 and js[0].deadline == "2026-11-20"
    js = jobs(P.WAZAFNE_COMPANY, "wazafne")
    assert len(js) == 1 and js[0].title == "موظف مشتريات" and js[0].employer == "شركة صدف للصناعات الغذائية"


def test_bug3_gender_per_item():
    g = {j.title: j.gender for j in jobs(P.TAWZEEF_LIST)}
    assert g["مروجة"] == "female" and g["مندوب مبيعات"] is None and g["مصمم/ة جرافيك"] is None


def test_bug4_short_patterns_do_not_match_inside_words():
    from app import taxonomy as tx
    assert tx.field_from_text("منسق مشاريع الإنسانية") != "health"
    assert tx.field_from_text("خبرة 3 سنوات") != "health"


def test_bug5_list_items_do_not_borrow_post_context():
    f = {j.title: j.field for j in jobs(P.TAWZEEF_LIST)}
    assert f["فني كهرباء"] == "industry" and f["مصمم/ة جرافيك"] == "media"


def test_bug6_employer_is_not_a_generic_place():
    js = jobs(P.SYRIANJOBS_FACTORY, "syrianjobs", "quick")
    assert js[0].employer is None and js[0].gender == "female" and js[0].experience == 0


def test_bug7_grade_prefix_sets_education():
    js = jobs(P.TAWZEEF_GOV)
    assert [(j.title, j.education, j.education_from) for j in js] == [
        ("عامل نظافة", "basic", "grade"), ("سائق آليات ثقيلة (نظافة)", "none", "grade")]
    assert all(j.official for j in js)


def test_quick_posts_and_spam():
    js = jobs(P.SYRIANJOBS_CAFE, "syrianjobs", "quick")
    assert js[0].kind == "quick" and js[0].cities == ["دمشق"] and js[0].whatsapp and (js[0].age_min, js[0].age_max) == (18, 25)
    assert all(j.spam for j in jobs(P.SYRIANJOBS_SPAM, "syrianjobs", "quick")) or not jobs(P.SYRIANJOBS_SPAM, "syrianjobs", "quick")
    assert jobs(P.GREETING) == []


def test_real_formats():
    grade_sections = """🔹️ إعلان عبر بوابة التوظيف
تُعلن وزارة التنمية الإدارية بالتنسيق مع وزارة الإعلام عن توفر شواغر وظيفية في عدد من المحافظات السورية
🔸️ الفئة الاولى
▪️مدير مكتب إعلامي
▪️ مسؤول منصات التواصل الاجتماعي
🔸️ الفئة الثانية
▪️ مصور
🔸️ الفئة الخامسة
▪️ مستخدم
يتم قبول طلبات التوظيف حتى تاريخ : 2026/11/15"""
    js = jobs(grade_sections)
    assert [(j.title, j.education) for j in js] == [("مدير مكتب إعلامي", "bachelor"), ("مسؤول منصات التواصل الاجتماعي", "bachelor"),
                                                   ("مصور", "institute"), ("مستخدم", "none")]
    quick = "#حماة\n\n*مطلوب:\nمعلم شاورما\n*للتواصل ولمعلومات أكثر الاتصال على الرقم:\n0930000009"
    j = jobs(quick, "syrianjobs", "quick")[0]
    assert j.title == "معلم شاورما" and j.education == "none" and j.field == "hospitality" and j.cities == ["حماة"]
    chefs = "#طرطوس\n\n*مطلوب:\n-شيف ساخن بدوام مسائي\n-شيف مازا صباحي تحضير\nللعمل في مطعم\n0930000010"
    assert len(jobs(chefs, "syrianjobs", "quick")) == 2
    repost = "تُعلن وزارة التنمية الإدارية عن توفر شواغر وظيفية.\nhttps://bit.ly/hiring20261104\n- آخر موعد لاستقبال طلبات التوظيف: 15/11/2026"
    assert jobs(repost, "wazafne") == []  # the ministry adapter reads the real cards


def test_real_posts_accuracy():
    """Floors measured on 178 hand-checked real posts (see tools/evaluate.py). Fails if a change makes extraction worse."""
    import io, contextlib, re
    from tools import evaluate
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sj, wz = evaluate.run("syrianjobs2", "syrianjobs", "quick"), evaluate.run("wazafne175", "wazafne", "formal")
        evaluate.score("s", evaluate.SYRIANJOBS, sj); evaluate.score("w", evaluate.WAZAFNE, wz)
    found = [int(x) for x in re.findall(r"titles found (\d+)/", buf.getvalue())]
    assert found[0] >= 43 and found[1] >= 82
