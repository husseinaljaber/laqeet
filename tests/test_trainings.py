"""«تدريبات ومنح»: which posts count as trainings, what we read from them, and the API."""
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import api, db, trainings

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

FREE_COURSE = """#تدريب_مجاني
#تدريب_أونلاين
📣 هل تبحث عن فرصة لتعلم قراءة التحاليل المخبرية؟
🌟 الآن مع أكاديمية شام الدولية للتدريب والتأهيل
نقدم لكم تدريب مميز بعنوان:
🧪 كورس قراءة التحاليل المخبرية
💻 نظام التدريب: أونلاين عبر برنامج WhatsApp
✔ التدريب مجاني
للتسجيل: https://forms.gle/abcdEFGH123"""

VOLUNTEER = """#حمص
تُعلن وزارة التنمية الإدارية عبر منصتها التطوعية بُناة
عن إتاحة فرصة تطوعية جديدة في محافظة حمص بعنوان :
« المشاركة التنظيمية في فعاليات التحرير »،
حيث تُمكّن الشباب من توظيف مهاراتهم.
يمكن للراغبين بالتطوع التسجيل عبر الرابط:
https://forms.gle/9h6JmpTdBFd6Ux6i8"""

GRANT = """تعلن منظمة «أوكسفام» في سوريا عن فتح باب التسجيل في برنامج التدريب والمنح لدعم المشاريع الصغيرة في منطقة السفيرة.
📍 مكان التسجيل: مجلس مدينة السفيرة، حلب.
آخر موعد للتسجيل: 30/12/2026"""

PAID_DIPLOMA = """#دبلوم_إدارة_الموارد_البشرية
#تدريب_أونلاين
🔥 دبلوم إدارة الموارد البشرية HR
رسوم الدورة 50 دولار فقط"""

JOB_WITH_DIPLOMA = """الشاغر الوظيفي: موظفة ضيافة و اتيكيت
المؤهلات: معهد أو دبلوم في الفنادق / السياحة
#تدريب"""

ADMISSION = """تُعلن الجامعة الافتراضية السورية عن بدء التسجيل الإلكتروني للطلاب الجدد على مفاضلة الفصل الدراسي خريف 2026
في جميع برامج الإجازات ودبلوم التأهيل التربوي."""

TENDER = """#تدريب
إعلان مناقصة لشراء أجهزة حاسوب لمركز التدريب المهني"""


def test_kinds_and_rejections():
    assert trainings.classify(FREE_COURSE) == "course"
    assert trainings.classify(VOLUNTEER) == "volunteer"
    assert trainings.classify(GRANT) == "course"  # «برنامج التدريب والمنح»
    for not_training in (JOB_WITH_DIPLOMA, ADMISSION, TENDER, "صباح الخير"):
        assert trainings.classify(not_training) is None, not_training[:30]


def test_what_we_read():
    t = trainings.parse(FREE_COURSE, "wazafne", "https://t.me/w/1", NOW)
    assert t["title"] == "كورس قراءة التحاليل المخبرية" and t["cost"] == "free" and t["online"] == 1
    assert t["provider"].startswith("أكاديمية شام") and t["apply_url"] == "https://forms.gle/abcdEFGH123"
    v = trainings.parse(VOLUNTEER, "sy_jobb", "https://t.me/s/2", NOW)
    assert v["title"] == "المشاركة التنظيمية في فعاليات التحرير" and v["cities"] == ["حمص"] and v["cost"] == "free"
    g = trainings.parse(GRANT, "jobsportal1", "https://t.me/j/3", NOW)
    assert g["provider"] == "منظمة أوكسفام" and g["deadline"] == "2026-12-30" and "حلب" in g["cities"]
    assert g["title"].startswith("برنامج التدريب والمنح")
    assert trainings.parse(PAID_DIPLOMA, "wazafne", "https://t.me/w/4", NOW)["cost"] == "paid"


def test_expiry_without_deadline():
    old = (datetime.now(timezone.utc) - timedelta(days=trainings.TRAINING_DAYS + 2)).isoformat(timespec="seconds")
    t = trainings.parse(FREE_COURSE, "wazafne", "https://t.me/w/5", old)
    assert t["expires_on"] < date.today().isoformat()


def test_store_dedupes_and_api_filters():
    client = TestClient(api.app)
    trainings.ensure_schema(api.con)
    api.con.execute("DELETE FROM trainings")
    assert trainings.maybe_store_post(api.con, "wazafne175", FREE_COURSE, "https://t.me/wazafne175/1", NOW)
    assert not trainings.maybe_store_post(api.con, "KhotwaInsideSyria", FREE_COURSE, "https://t.me/k/2", NOW)  # same course
    assert trainings.maybe_store_post(api.con, "wazafne175", VOLUNTEER, "https://t.me/wazafne175/3", NOW)
    assert trainings.maybe_store_post(api.con, "wazafne175", PAID_DIPLOMA, "https://t.me/wazafne175/4", NOW)
    api.con.commit()
    d = client.get("/api/trainings").json()
    assert d["total"] == 3 and d["counts"]["volunteer"] == 1 and d["counts"]["course"] == 2
    assert [i["title"] for i in client.get("/api/trainings", params={"free": True}).json()["items"]].count("دبلوم إدارة الموارد البشرية HR") == 0
    assert client.get("/api/trainings", params={"q": "التحاليل"}).json()["total"] == 1
    assert client.get("/api/trainings", params={"kind": "volunteer", "city": "حمص"}).json()["total"] == 1
    api.con.execute("DELETE FROM trainings")
    api.con.commit()


def test_trainings_survive_the_seed_round_trip(tmp_path, monkeypatch):
    import sqlite3
    import sys
    from app import config as cfg, seed
    from app.models import Job
    src = db.connect(":memory:")
    trainings.ensure_schema(src)
    db.upsert(src, Job(title="محاسب", source="wazafne", post_url="u1", posted_at=NOW, cities=["دمشق"]))
    trainings.maybe_store_post(src, "wazafne175", VOLUNTEER, "https://t.me/wazafne175/9", NOW)
    src.commit()
    (tmp_path / "data").mkdir()
    seed.export_seed(src, tmp_path / "data" / "seed_jobs.jsonl.gz")
    # a fresh server: empty database that boots from the seed (the bootstrap normally skips itself under pytest)
    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    monkeypatch.delitem(sys.modules, "pytest")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    dst.executescript(db.SCHEMA)
    db._migrate(dst)
    db._bootstrap_seed_if_empty(dst, tmp_path / "data" / "fresh.db")
    assert dst.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert dst.execute("SELECT title FROM trainings").fetchone()[0] == "المشاركة التنظيمية في فعاليات التحرير"
