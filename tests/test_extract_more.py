"""Regressions found while checking the full 6-month collection."""
from app.extract import extract

DUTIES_UNDER_HEADER = 'براتب 250$ نبحث عن :\n#مساعدة_ادارية 🧕🏻 / أنثى\n\nالمهام والمسؤوليات:🔻\n- تنفيذ الأعمال الإدارية والمكتبية اليومية.\n- تنظيم المواعيد والاجتماعات وتنسيق جدول الأعمال.\n- إعداد الخطابات والمراسلات والتقارير الإدارية.\n- حفظ الملفات والمستندات ورقياً وإلكترونياً.\n- إدخال البيانات وتحديث قواعد المعلومات والسجلات\n- إجراء استبيانات تقييم مدى رضا العملاء عن الخدمات المقدمة\n- التواصل الهاتفي مع الزبائن والاستماع للاراء والملاحظات وتدوينها بدقة\n\nشروط الوظيفة:👇\n✔️  القدرة العالية على التواصل الهاتفي واللباقة في الحديث.\n✔️الالتزام والتفرغ للعمل\n✔️ خبرة سابقة مفضلة في المجال\n✔️اتقان العمل على الكومبيوتر وبرامج الأوفيس (شرط أساسي )\n✔️اللغة الانكليزية بمستوى جيد\n\nتفاصيل وطبيعة العمل:\nمكان العمل: دمشق - البرامكة\nالدوام من 11 حتى 7\nالراتب: 250$\n\nطريقة التقديم:\nإذا كنتِ ترين في نفسك الكفاءة وتتمتعين بمهارات التواصل المطلوبة، يرجى إرسال السيرة الذاتية (CV)  0900000000\n\n\nللمزيد من فرص العمل والدورات التدريبية والمناقصات تابعونا على تلغرام :\nhttps://t.me/wazafne175\nوبإمكانكم متابعة قناتنا على واتساب :\nhttps://whatsapp.com/channel/0029VaBZ63W1yT294EIjjP0d'

COURSE_AD = '📢 الدورة التأصيلية لتعليم علم فن التجويد\n\nهل ترغب في تعلم قراءة القرآن الكريم بطريقة صحيحة وجميلة❓❓\n\nإقرأ وإرتقي معنا وتقرب الى الله بتعلم كلامه وصحح قراءتك ✅\n\n⭕ دورة علم فن التجويد التأصيلية متاحة للجميع...\n🔹في جميع محافظات السورية 🏙️\n🔹ولجميع الأعمار والأجناس 👨\u200d👩\u200d👧\u200d👦\n🔹اونلاين عن بُعد عبر الإنترنت 💻\n🔹من بيتك وفي وقت فراغك 🌐\n🔹مدة الدورة شهر كامل مكثف 📆\n🔹من الصفر حتى الإحتراف 👑\n🔹ومن التأصيل حتى التحصيل 🎓\n🔹 متابعة وأسئلة ومناقشات ✨\n🔹وتحصل على شهادة إتمام الدورة 📜\n🔹 بعد اختبار الكتروني متميز 📝\n\n💵 الرسوم رمزية جداً ؛\n\n💳 1 $ أو 150 ليرة سورية جديد\n\n⏳سارع بالانضمام لدورة الأولى من نوعها\n\n💎 ولا تفوت الفرصة 💎\n\nللإنضمام الى المجموعة ⬇️\nhttps://chat.whatsapp.com/Jk1Sg6p40PdA3HMLwc2qvU?s=cl&p=a&mlu=4&ilr=4&iam=2\n\n📩 لتسجيل والإستفسار\n0900000000'


def test_duty_bullets_are_not_titles():
    jobs = extract(DUTIES_UNDER_HEADER, "wazafne", "https://t.me/wazafne175/70107")
    assert [j.title for j in jobs] == ["مساعدة ادارية / أنثى"]
    assert jobs[0].gender == "female" and jobs[0].cities == ["دمشق"]


def test_paid_course_is_not_a_job():
    assert extract(COURSE_AD, "wazafne", "https://t.me/wazafne175/70068") == []


def test_turkey_rejected_and_cleaning_regressions():
    from app.sources.moad_pdf import fix_lost_tails
    assert fix_lost_tails("سائق أليات ثقيلة (جرار زراع)يدرعا") == "سائق أليات ثقيلة (جرار زراعي)"
    assert fix_lost_tails("مساعد مهندس دعم فن (هندس)يدرعا") == "مساعد مهندس دعم فني (هندسي)"

    assert extract(
        "إعلان توظيف | كيميائي أو مهندس أغذية\nتعلن مجموعة العميد في مرسين، تركيا، عن حاجتها لمهندس\nللتواصل +90 551 511 50 52",
        "wazafne", "https://t.me/wazafne175/93625",
    ) == []

    assert extract("مطلوب مسؤولة تنظيف (أنثى) في إسطنبول - باشاك شهير\n05521247615", "wazafne", "https://t.me/wazafne175/93620") == []
    assert extract("مطلوب محاسب عن بعد أو في غازي عنتاب. يقبل كملك إسطنبول وإذن عمل", "wazafne", "https://t.me/wazafne175/93621") == []
    assert extract("مطلوب محاسب في دمشق\n0933111222", "jobsintr", "https://t.me/jobsintr/93622", country="tr") == []

    bullet_o = extract("شركة في دمشق تعلن عن شواغر في المجالات التالية:\no مهندس ذكاء صناعي عدد 6\no مهندس كيمياء عدد 3", "wazafne", "https://t.me/wazafne175/93623")
    assert len(bullet_o) == 2 and all(not j.title.startswith("o ") for j in bullet_o)

    sub_spam = extract("مطلوب موظف! اشتراك لمدة شهر بسعر 6.5$ باقة Gemini للتواصل 0933111222", "syrianjobs", "https://t.me/syrianjobs2/1", default_kind="quick")
    assert all(j.spam for j in sub_spam) or not sub_spam


CITY_SECTIONS = """بوابة التوظيف تعلن عن الشواغر التالية:
▪️حلب :
🔸 أمين/ة صندوق
https://tawzeefportal.com/jobs/cashier-7153
🔸 حارس
https://tawzeefportal.com/jobs/guard-6861

▪️إدلب :
🔸 ممرضة
https://tawzeefportal.com/jobs/nurse-7150

▪️بدون موقع :
🔸 مصمم واجهات وتجربة مستخدم

- ينتهي التقديم بتاريخ: 2026/11/29
📄للمزيد من الوظائف: https://tawzeefportal.com/jobs"""

MINISTRY_SECTIONS = """[وظائف وزارة المالية]
بوابة التوظيف تعلن عن الشواغر التالية:
▪️القنيطرة :
🔸 مراقب دخل
🔸 محاسب إدارة

▪️درعا :
🔸 مراقب دخل
🔸 محاسب إدارة

- ينتهي التقديم بتاريخ: 2026/12/07"""


def test_city_sections_give_each_job_its_own_city_and_link():
    from app.extract import extract
    jobs = {j.title: j for j in extract(CITY_SECTIONS, "tawzeefportal", "https://t.me/tawzeefportal/1", "2026-11-20T10:00:00+00:00")}
    assert set(jobs) == {"أمين/ة صندوق", "حارس", "ممرضة", "مصمم واجهات وتجربة مستخدم"}
    assert jobs["أمين/ة صندوق"].cities == ["حلب"] and jobs["ممرضة"].cities == ["إدلب"]
    assert jobs["مصمم واجهات وتجربة مستخدم"].cities == ["عدة محافظات"]
    assert jobs["حارس"].apply_url == "https://tawzeefportal.com/jobs/guard-6861"
    assert all(j.deadline == "2026-11-29" for j in jobs.values())


def test_same_title_in_many_sections_is_one_job():
    from app.extract import extract
    jobs = extract(MINISTRY_SECTIONS, "tawzeefportal", "https://t.me/tawzeefportal/2", "2026-11-20T10:00:00+00:00")
    assert sorted(j.title for j in jobs) == ["محاسب إدارة", "مراقب دخل"]
    assert all(set(j.cities) == {"القنيطرة", "درعا"} for j in jobs)


def test_tawzeefportal_job_pages_are_unwrapped_not_dropped():
    from app import direct_apply as da
    assert not da.is_aggregator_or_wall("https://tawzeefportal.com/jobs/cashier-7153")
    assert da.is_strict_portal_wrapper("https://tawzeefportal.com/jobs/cashier-7153")
    assert da.is_aggregator_or_wall("https://tawzeefportal.com/companies/166")
    assert da.is_aggregator_or_wall("https://tawzeefportal.com/jobs")
