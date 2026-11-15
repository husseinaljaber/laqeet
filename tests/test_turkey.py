"""Unit tests for Turkey job ingestion, filtering, normalization, and direct-apply gates."""
from app import bot, direct_apply as da, spam, taxonomy as tx
from app.extract import extract
from app.models import Job


def test_tr_cities_and_hashtag_filtering():
    assert tx.tr_cities_from_text("عمل في باشاك شهير - اسطنبول") == ["إسطنبول"]
    assert tx.tr_cities_from_text("مصنع في المنطقة الصناعية باشبينار غازي عنتاب") == ["غازي عنتاب"]
    assert tx.tr_cities_from_text("مكتب الريحانية هاتاي") == ["هاتاي (أنطاكيا)"]
    noisy = "مطلوب محاسب في مرسين\n#اسطنبول #مرسين #انقرة #بورصة #ازمير #قونيا"
    assert tx.tr_cities_from_text(noisy) == ["مرسين"]


def test_tr_phones_and_placeholder_filtering():
    txt = "للتواصل +90 534 380 60 11 أو واتساب 0538-584-44-84 أو تجريبي +90 555 123 4567"
    assert tx.phones_tr(txt) == ["05343806011", "05385844484"]
    assert tx.phones(txt) == []  # Syrian extractor does not pick up Turkish numbers
    assert da.extract_phone_from_wa("https://api.whatsapp.com/send/?phone=905385844484", country="tr") == ["05385844484"]
    assert da.extract_phone_from_wa("https://wa.me/905551234567", country="tr") == []


def test_tr_salary_extraction():
    assert "50,000 - 60,000 TL" in (tx.salary("الراتب التقريبي 50,000.00 - 60,000.00 TL") or "")
    assert "28,000 ليرة تركية" in (tx.salary("معامل أغذية راتب 28,000 ليرة تركية") or "")
    assert "1,000 - 1,500 $" in (tx.salary("1,000 - 1,500 $ دوام كامل") or "")
    assert tx.salary("الدولار الامريكي 50.3716 الليرة السورية 0.4129") is None
    assert tx.salary("راتب ثابت بعد تجربة 14 يوم) للتواصل") is None


def test_tr_citizenship_scam_and_nonjob_filters():
    assert spam.is_tr_citizenship_only("شروط التقديم: - أنثى وتحمل الجنسية التركية. تجيد الإنجليزية")
    assert spam.is_tr_citizenship_only("مطلوب محاسب يحمل الجنسية التركية للعمل في شيشلي")
    assert spam.is_tr_citizenship_only("Kamu İhale Kurumu Uzman Yardımcısı Alımı (KPSS)")
    assert spam.is_tr_citizenship_only("Hazine ve Maliye Bakanlığı Stajyer Alımı")

    # Non-citizen accepted or mere preference should NOT be blocked
    assert not spam.is_tr_citizenship_only("مطلوب موظف مبيعات في غازي عنتاب يقبل الكيملك أو الإقامة")
    assert not spam.is_tr_citizenship_only("مطلوب محاسب في إسطنبول ويفضل من يحمل الجنسية التركية")

    # Forex / XAUUSD / tenders / weather / courses
    assert spam.is_spam("فرصة مبيعات فوركس وتداول الذهب XAUUSD بعمولة عالية")
    assert not spam.is_job_post("دعوة لتقديم عروض أسعار ITB ضيافة مشروع عزم")
    assert not spam.is_job_post("حالة الطقس المتوقعة في مدينة غازي عنتاب")
    assert not spam.is_job_post("TÜBİTAK Bursları eğitimlerine kayıtlar başladı")


def test_extract_tr_and_mixed_routing():
    # Gelbasla format
    g = extract(
        "💼 Sevgi ve Kardeşlik Vakfı (SEVKAR): Yaygın Eğitim Sorumlusu (NFE Officer)\n📍 İstanbul ⏰ ❗ 13.11.2026\nhttps://ab-ilan.com/is-ilani/test-1/",
        "gelbaslacom", "https://t.me/gelbaslacom/100", posted_at="2026-11-10T10:00:00+00:00",
        links=["https://ab-ilan.com/is-ilani/test-1/"], country="tr", sector="ngo",
    )
    assert len(g) == 1
    assert g[0].country == "tr" and g[0].cities == ["إسطنبول"]
    assert g[0].deadline == "2026-11-13" and "SEVKAR" in (g[0].employer or "")

    # Mixed channel routing: Syria stays sy, Turkey routes tr
    sy = extract(
        "مطلوب ميسر حماية\nالمكان: حلب - سوريا\nللتواصل 0933445566",
        "takaful", "https://t.me/takafulalsham/50", posted_at="2026-11-10T10:00:00+00:00", country="mixed",
    )
    assert len(sy) == 1 and sy[0].country == "sy" and sy[0].cities == ["حلب"]

    tr = extract(
        "تعلن الشركة في غازي عنتاب باشبينار عن حاجتها إلى محاسب برنامج الأمين براتب 30,000 ليرة تركية\nواتساب +90 532 444 55 66",
        "gaziantepis", "https://t.me/gaziantepis/51", posted_at="2026-11-10T10:00:00+00:00", country="tr",
    )
    assert len(tr) == 1 and tr[0].country == "tr" and tr[0].cities == ["غازي عنتاب"]
    assert tr[0].phones == ["05324445566"] and "30,000 ليرة تركية" in (tr[0].salary or "")

    # Bare URL-only post is skipped
    assert extract("https://www.adwhit.com/ar/مطلوب-موظف-49635", "adwhit_jobs", "https://t.me/adwhit_jobs/10", country="tr") == []


def test_tr_direct_apply_isolates_similar_cards_and_blocks_login_walls():
    html = """
    <main>
      <article class="min-w-0">
        <h1>مدرس رياضيات</h1>
        <div class="job-description">دوام جزئي <a href="tel:+905343806011">اتصل</a></div>
        <ul class="similar"><li><article class="sj-card"><a href="tel:+905537757474">noise</a></article></li></ul>
      </article>
    </main>
    """
    info = da.extract_apply_from_html(html, "https://salamjobs.com/jobs/1", country="tr")
    assert info["phones"] == ["05343806011"]

    wall_job = Job(title="محاسب", source="salamjobs", post_url="https://x", apply_url="https://mahereen.com/jobs/turkey/1", country="tr")
    assert da.resolve_job_apply(wall_job, client=None) is False

    msg = bot.message({"id": 2, "country": "tr", "title": "محاسب", "phones": ["05343806011"], "sources": []})
    assert "wa.me/905343806011" in msg


def test_turkey_portals_parsers():
    from app.sources import turkey_portals as tp

    _, ok1 = tp._parse_relative_ar_date("منذ 5 أيام")
    _, ok2 = tp._parse_relative_ar_date("منذ شهرين")
    _, old = tp._parse_relative_ar_date("منذ 8 أشهر")
    _, yr = tp._parse_relative_ar_date("منذ سنة")
    assert ok1 and ok2 and not old and not yr

    assert tp._clean_tr_title("مطلوب سكرتيرة في شيشلي - فرص عمل في تركيا") == "مطلوب سكرتيرة في شيشلي"

    job = tp._build_tr_job(
        title="مطلوب مدراء مطاعم لشركة في اسطنبول - أدويت",
        source="adwhit_web",
        post_url="https://www.adwhit.com/ar/test-1",
        posted_at="2026-11-14T13:00:00+00:00",
        body_text="نبحث عن مدير فرع\nالراتب: 55,000 TL\nالشروط المطلوبة:\n- خبرة 5 سنوات\nالمهام:\n- إدارة الفريق",
        city_hint="Başakşehir İstanbul",
        phones=["05385844484"],
        email="hr@example-tr.org",
    )
    assert job is not None
    assert job.country == "tr" and job.cities == ["إسطنبول"]
    assert "55,000 TL" in (job.salary or "")
    assert "خبرة 5 سنوات" in job.skills and "إدارة الفريق" in job.tasks
    assert "email" in job.apply_types and "whatsapp" in job.apply_types
