"""Read a free-text job post from a Telegram channel and turn it into one or more Jobs.

The shapes seen in the real channels:
  * a list post: a header ("تعلن جهة كذا عن توفر شواغر … وفق الاختصاصات التالية:"), optional grade sections
    ("🔸 الفئة الأولى"), then one job title per bullet line (tawzeef_platform, many wazafne175 posts);
  * a single post with a title line ("المسمى الوظيفي: …", "تعلن شركة … عن حاجتها إلى …", "مطلوب: …") and details
    below, often with requirement bullets that are NOT job titles (wazafne175);
  * a quick post: "#المدينة / *مطلوب: / شباب للعمل في مطعم … / رقم واتس" (syrianjobs2).
Rules first. When ANTHROPIC_API_KEY is set, llm.extract_llm can do the same job (see llm.py).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import config
from . import direct_apply as da
from . import taxonomy as tx
from .arabic import normalize, soft_fold
from .classify import classify as computer_subfield, is_computer_degree
from .models import Job
from .spam import is_job_post, is_spam, is_tr_citizenship_only

F = soft_fold

# A bullet that marks a list item. Decorated "info" markers (🔹 ✔️ ⚠️ 📍 …) are NOT here on purpose:
# they start notes like «🔹 إعلان عبر بوابة التوظيف» or «✔️ يتم تقديم الطلب…». 🔹/🟧 count only inside a list block.
_ITEM_BULLET = re.compile(r"^\s*(?:[▪▫◾◽■□•●◦‣⁃·\-–—*]+️?|\d{1,2}\s*[-.)]|[٠-٩]{1,2}\s*[-.)]|o\s+(?=[ء-ي]))\s*")
_SOFT_BULLET = re.compile(r"^\s*(?:🔹|🔸|🔷|🔶|🟧|🟦|🟩|🔺|🔻|✅|☑️|✔️|➖|👈|📌|⭐|🌟|💠)️?\s*")
_ORDINAL = re.compile(F(r"^\s*(اولا|ثانيا|ثالثا|رابعا|خامسا|سادسا|سابعا|ثامنا|تاسعا|عاشرا)\s*[:\-–]\s*"))
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿←-⇿■-◿]")

# The line that opens a list of job titles.
_LIST_HEADER = re.compile(F(
    r"(الاختصاصات|الاختصاص|المسميات|المسمي[اة]ت|الشواغر|الوظائف|المناصب|الشاغر|التخصصات|المجالات)\s*(الوظيفيه\s*)?(التاليه|الاتيه|التالي|الاتي)"
    r"|وفق (الاختصاصات|المسميات)|ضمن (الاختصاصات|المسميات)|من الاختصاصات التاليه|(شواغر|شاغر)\s*(وظيفيه)?\s*:$|يلي:$|التاليين|"
    r"^\*?\s*(مطلوب|مطلوبه|مطلوبين|يلزمنا)\s*:?\s*$|"
    r"following\s+(?:positions|vacancies|roles|opportunities)\s*:?"), re.I)
_BARE_NEED = re.compile(F(r"^\*?\s*(مطلوب|مطلوبه|مطلوبين|يلزمنا)\s*:?\s*$"))
# Lines after these are requirements / tasks / perks, not titles.
_SECTION_STOP = re.compile(F(
    r"^(الشروط|شروط|المتطلبات|متطلبات|المؤهلات|مؤهلات|المهام|مهام|المميزات|مميزات|المسؤوليات|طبيعه العمل|"
    r"تفاصيل العمل|مزايا العمل|البرامج المطلوبه|اوقات العمل|اوقات الدوام|ساعات العمل|ايام العمل|طريقه التقديم|معلومات التواصل|"
    r"نبذه|عن المنتجات|المنتجات|تفاصيل المنتج|المهارات|مهارات|الوصف|ملاحظه|ملاحظات|يشترط|يفضل|نقدم|نوفر|الامتيازات|المزايا|"
    r"key requirements|requirements|responsibilities|qualifications|benefits|how to apply|what you will do|"
    r"core stack|tech stack|preferred|what makes you|about you|nice to have|المستندات المطلوبه|يتضمن الطلب)"), re.I)
# Lines that end a list block (deadline, apply instructions, links, contact).
_LIST_END = re.compile(F(
    r"يتم قبول|اخر موعد|للتقديم|التقديم|للتواصل|تواصل|يرجى|https?://|مكان العمل|الدوام|الراتب|ترسل|يرسل|ارسال|"
    r"للاستفسار|موقع العمل|نظام العمل|نهايه الاعلان|ينتهي|الرواتب"))
_NOISE_LINE = re.compile(F(
    r"تابعونا|تابعنا|للمزيد من فرص|وبامكانكم متابعه|انضم (الي|ل)?\s*(قناتنا|القناه|مجموعتنا)|انضموا|whatsapp\.com/channel|t\.me/|facebook|instagram|شاركها|"
    r"شاركوا رابط القناه|لاعلاناتكم|لنشر ما لديكم من فرص|راسلونا دايركت|على مجموعتنا ع تلغرام|مجموعتنا على التلغرام|"
    r"في حال لم يكن لديك سيره|لانشاء سيره|سيره ذاتيه احترافيه|اعلان عبر بوابه التوظيف|wa\.me/message"), re.I)
_GRADE_LINE = re.compile(F(r"^\s*(?:ال)?فئه\s*(الاولي|الاول|الثانيه|الثالثه|الرابعه|الخامسه)\s*:?\s*"))
_GRADE_WORD = {"الاولي": "1", "الاول": "1", "الثانيه": "2", "الثالثه": "3", "الرابعه": "4", "الخامسه": "5"}

_NEED_LINE = re.compile(r"(?:^|\n)[^\w\n]{0,6}\*?\s*(?:(?:\d+|[٠-٩]+)[.)\-–]\s*)?(?:(?:ال)?مطلوب(?:ة|ون|ين)?|يلزمنا|نبحث\s+عن|تبحث\s+عن|يبحث\s+عن|نطلب|يطلب)\s*[:：]?\s*([^\n]*)")
_HAJA_RX = re.compile(r"(?:حاجت(?:ها|ه)|بحاجة|رغبت(?:ها|ه))\s*(?:إلى|الى|ل|لـ|في|بـ?)?\s*(?:رفد\s+(?:كوادرها|فريقها)\s+ب(?:منصب\s+)?|تعيين|توظيف|استقطاب|ل?التعاقد\s+مع|لتعاقد\s+مع|ضم|شغل\s+وظيفة|ملء\s+الشاغر\s+التالي)?\s*[:：(]?\s*([^\n)]+)")
_TITLE_PATTERNS = [
    re.compile(r"(?:(?:المسم[ىي]|المنصب|الشاغر)\s*الوظيف[يى]|المنصب)\s*[:：]\s*([^\n]+)"),
    re.compile(r"إعلان\s+توظيف\s*[|–\-:]\s*([^\n]+)"),
    re.compile(r"(?:إعلان|اعلان)\s+(?:شاغر|توظيف|فرص[ةه]\s+عمل)\s*[:：\-–|]\s*([^\n]+)"),
    re.compile(r"(?:^|\n)\s*(?:إعادة|اعادة)\s+(?:إعلان|اعلان)\s*[-–:|]\s*([^\n]+)"),
    re.compile(r"لشغل\s+وظيف[ةه]\s*[:：]\s*\n\s*([^\n]+)"),
    re.compile(r"فتح\s+باب\s+التقديم\s+لشاغر\s+([^\n]+)"),
    re.compile(r"(?:Position(?:\s*Title)?|Job\s*Title|Vacancy(?:\s*Title)?)\s*[:：]\s*([^\n]+)", re.I),
    re.compile(r"(?:توفر|توافر)\s+شاغر(?:\s+وظيف[يى])?\s*(?:[:：]\s*[\"«']?|[\"«])(?!\s*بمسمى)([^\n\"»']+)"),
    re.compile(r"فرص[ةه]\s+عمل\s+لدى\s+[^\n:：]+[:：]\s*(?:\n\s*)+([^\n]+)"),
    re.compile(r"\bبمسمى\s+([^\n،,.]+)"),
    re.compile(r"شاغر[^\n]{0,60}[:：]\s*\n([^\n]+)"),
    _HAJA_RX,
    re.compile(r"(?:^|\n)\s*\*?\s*(?:(?:\d+|[٠-٩]+)[.)\-–]\s*)?(?:(?:ال)?مطلوب(?:ة|ون|ين)?|يلزمنا|نبحث\s+عن|تبحث\s+عن|يبحث\s+عن|نطلب|يطلب)\s*[:：]?\s*([^\n]*)"),
    re.compile(r"(?:التعاقد\s+مع|بالتعاقد\s+مع)\s*:?\s*([^\n]+)"),
    re.compile(r"(?:فرصة\s+عمل|فرصة|شاغر)\s*(?:[–\-:|]\s*)([^\n]+)"),
    re.compile(r"(?:we\s+are\s+hiring|we['’]?re\s+hiring|\bhiring)\s*[:：\-–|]\s*([^\n]+)", re.I),
    re.compile(r"\b(?:is hiring|looking for)\s+(?:an?\s+)?([^\n]+)", re.I),
    re.compile(r"\bبصف[ةه]\s+([^\n،,.]+)"),
    re.compile(r"\bلوظيف[ةه]\s+([^\n،,.]+)"),
    re.compile(r"رفد\s+(?:كادرها|كوادرها|فريقها|فريق عملها)\s+بـ?\s*\*?([^*\n،,.]+)"),
    re.compile(r"فرصة\s+عمل(?:\s+\S+){1,2}\s*[–\-:|]\s*([^\n|]+)"),
    re.compile(r"\b(?:يطلب|تطلب)\s*[:：]\s*([^\n]+)"),
    re.compile(r"(?:^|\n)[^\w\n]{0,6}(?:شركة|محل|مطعم|مركز|معمل|صيدلية|مكتب|عيادة|مؤسسة|جمعية)[^\n]{2,60}\s+(?:يطلب|تطلب)\s+([^\n]+)"),
]
# Where a title line stops being a title.
_TITLE_CUT = re.compile(
    r"\s*(?:(?<![ء-ي])\.(?![ء-ي])|[،,؛!؟]|\s[–\-|]\s|\bدوام\b|\bالدوام\b|\bالعمر\b|\bعمر\b|\bمن الساعة\b|\bالراتب\b|\bبراتب\b|\bللتواصل\b|\bيفضل\b|"
    r"\bبدون خبر[ةه]\b|\bبخبرة\b|\bخبرة\b|\bالخبرة\b|\bشرط\b|\bيشترط\b|\bمع شهادة\b|\bللتقديم\b|\bفي (?:مدينة|محافظة|منطقة)\b|\bبمنطقة\b|\bضمن\b|"
    r"\bفي محافظ|\bلدى\b|\bلديها\b|\bلديه\b|\bوفق\b|\bحسب\b|\bالعنوان\b|\bالمنطقة\b|\bالسكن\b|\bمو مهم\b|\bيكون\b|\bتكون\b|"
    r"\bذو\b|\bذات\b|\bللانضمام\b|\bيجيد\b|\bتجيد\b|\bلا يتجاوز\b|\bذات مظهر\b|\bالشغل\b|\bفاضي\b|\bصباحي\b|\bمسائي\b|\bسكن مؤمن\b|\bالسكن مؤمن\b)")
_TITLE_TRAIL = re.compile(r"(?:^|\s+)(?:فورا|فوراً|بشكل فوري|بشكل عاجل|عاجل|للعمل|بدون|في|ب|ضمن|من|ل|و|او|أو|على|عند|مع)\s*$")
_EMPLOYER = [
    re.compile(r"(?:^|\n)[^\w\n]{0,6}(?:الجهة(?:\s+المعلنة|\s+الموظِّ?فة)?|المعلن)\s*[:：]\s*([^\n]+)"),
    re.compile(r"(?:تُ?علِ?ن|تعلن|يُ?علِ?ن|يعلن|ترغب|يرغب|ترحّب|ترحب)\s+(?:عن\s+)?((?:ال)?(?:شركة|منظمة|جمعية|مؤسسة|مشفى|مستشفى|مجموعة|مركز|"
               r"معهد|جامعة|مدارس|مدرسة|بوابة|منصة|هيئة|وزارة|مديرية|محافظة|صندوق|الصندوق|الهلال|مصرف|بنك|وحدة|الرهبانية|"
               r"عائلة|فريق|مكتب|مطعم|مجمع|اتحاد|نقابة|مؤسسه|شركه)[^\n،,.:]{2,70}?)[،,]?\s+(?:عن\s|بالتنسيق|بحاجتها|حاجتها|حاجته|بالتعاقد|ب?رفد|الشواغر|ترحّب|بكل)"),
    re.compile(r"(?:لدى|الجهة\s+الموظِّ?فة\s*:)\s*((?:ال)?(?:شركة|منظمة|جمعية|مؤسسة|مشفى|مستشفى|مجموعة|مركز|مصرف|بنك|مطعم|فندق|معهد|جامعة|صندوق|الصندوق|وزارة|مديرية|محافظة|هيئة|أمانة|مجلس)\s+[^\n،,.:]{2,50}?)(?:\s+في\s|\s*$|\s*[\n،,.])"),
]
_MONTHS = {"كانون الثاني": 1, "شباط": 2, "آذار": 3, "اذار": 3, "نيسان": 4, "أيار": 5, "ايار": 5, "حزيران": 6, "تموز": 7, "آب": 8, "اب": 8,
           "أيلول": 9, "ايلول": 9, "تشرين الأول": 10, "تشرين الاول": 10, "تشرين الثاني": 11, "كانون الأول": 12, "كانون الاول": 12,
           "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
           "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12}
_DATE = re.compile(r"(\d{1,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,4})")
_DATE_WORDS = re.compile(r"(\d{1,2})\s+(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")(?:\s+(\d{4}))?")
_DATE_2 = re.compile(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})")
_DEADLINE_HINT = re.compile(F(
    r"تاريخ انتهاء|انتهاء التقديم|نهايه التقديم|موعد انتهاء|اخر موعد|الموعد النهائي|موعد نهائي|"
    r"اخر يوم|حتي تاريخ|حتي يوم|حتي|لغايه تاريخ|لغايه|ينتهي التقديم|ينتهي في|ينتهي بتاريخ|ينتهي يوم|ينتهي|"
    r"نهايه الاعلان|deadline|تاريخ الانتهاء|قبل تاريخ|قبل يوم|لاستقبال الطلبات|لاستلام الطلبات|"
    r"لتقديم الطلبات|تلقي الطلبات|اقصاه تاريخ|اقصاه|اخر مهله|مهله التقديم|مده التقديم|"
    r"متاح حتى|متاح لغايه|تاريخ الاغلاق|اغلاق الاستماره|اغلاق الشاغر|اغلاق التقديم|يقفل باب التقديم|يغلق التقديم|"
    r"son başvuru(?:\s*tarihi)?|⏰"
))
_CLOSED_RX = re.compile(F(
    r"تم (?:اغلاق|قفل|ملء|تعبئه) (?:الشاغر|الطلب|الاستماره|باب التقديم|التسجيل)|"
    r"الشاغر مغلق|التقديم مغلق|الطلب مغلق|تم التوظيف|نعتذر عن استقبال|"
    r"اسماء المقبولين|نتائج المسابقه|انتهت (?:فتره|مرحله|مده|مهله) التقديم|توقف التقديم"
))
_WEEKDAYS = {
    "الاثنين": 0, "الاتنين": 0, "اثنين": 0, "اتنين": 0,
    "الثلاثاء": 1, "التلاتاء": 1, "تلاتاء": 1, "ثلاثاء": 1,
    "الاربعاء": 2, "اربعاء": 2,
    "الخميس": 3, "خميس": 3,
    "الجمعه": 4, "جمعه": 4,
    "السبت": 5, "سبت": 5,
    "الاحد": 6, "احد": 6,
}
_WEEKDAY_RX = re.compile(r"(?:يوم\s+)?(" + "|".join(_WEEKDAYS.keys()) + r")")
_NOT_JOB = re.compile(F(r"مطلوب (غرفه|بيت|شقه|منزل)|(?:شقه|منزل|بيت|محل|عقار|اراض[يى]|سياره|حساب|صفحه|قناه) (?:للايجار|للبيع)|^\W*(?:تنويه|للايجار|للبيع)|(?:اعلان|صدور) نتائج الاختبار|اسماؤهم|تدعو .{0,30}(المتقدمين|الاشخاص)|خدمه جديده علي|"
                        r"(?:مطلوب|نبحث عن|باب|فرص[هة]?|حمله|استماره|اعلان|استقطاب) (?:ال)?(?:متطوعين|متطوع|تطوع)|عمل تطوعي|فرص تطوعيه|منصتها التطوعيه|فرصه تطوع|\bvolunteer(?:s|ing)?\b|"
                        r"تدريب مجاني|دوره تدريبيه|#تدريب_|#فرصه_تدريب|فرصه تدريب عملي|شهاده دوليه|منحه دراسيه|(?:اعلان|طرح|دعوه|تمديد|ملف) (?:مناقصه|استدراج عروض)|(?:مناقصه|استدراج عروض) (?:رقم|عامه|بالظرف)|ورشه عمل حول|"
                        r"ندوه تفاعليه|لحضور ندوه|ببجي|فري فاير|جواكر|كلاش اوف كلانس|كلاش رويال|براول ستارز|لوردز موبايل|فرصه استثماريه|تخيل فكرتك تصير|لنشر ما لديكم من فرص|"
                        r"طلب عروض|دعوه لتقديم عروض|عطاءات|منصه تسوق الكتروني|عرض منتجاتهم|للانضمام (?:الى|الي) المنصه|"
                        r"تعلن عن بدء التسجيل في|سجل الان في|الدوره (التاصيليه|التدريبيه)|رسوم (رمزيه|الدوره|الاشتراك)|الرسوم رمزيه|للانضمام (الى|الي) (الدوره|المجموعه)"))
_SOFT_NOT_JOB = re.compile(F(r"\b(?:للايجار|للبيع|متطوعين|متطوع|تطوع|مناقصه|نتائج الاختبار)\b|اكاديميه .{0,20}للتدريب"))
_EXPLICIT_VACANCY_HDR = re.compile(F(r"وظيفه شاغره|شاغر وظيفي|فرصه عمل|اعلان توظيف|اعلان شاغر|المسمي الوظيفي|المنصب الوظيفي|مطلوب للعمل|مطلوب موظف|مطلوب مندوب|مطلوب شغيل|تعلن .{0,60}عن (?:حاجتها|توفر شاغر|توافر شاغر|رغبتها في)"))
_OFFICIAL = re.compile(F(r"وزاره|مديريه|(?:^|\n)\s*محافظه (دمشق|حلب|حمص|حماه|اللاذقيه|طرطوس|ادلب|درعا|السويداء|القنيطره|دير الزور|الحسكه|الرقه|ريف دمشق)|"
                         r"الهيئه العامه|هيئه عامه|المؤسسه العامه|الشركه العامه|الصندوق السيادي|مجلس مدينه|الامانه العامه"))
_ABROAD = re.compile(F(r"تركيا|\bturkey\b|türkiye|اسطنبول|استانبول|\bistanbul\b|غازي عنتاب|\bgaziantep\b|مرسين|\bmersin\b|"
                           r"اورفا|\burfa\b|هاتاي|انطاكيا|\bhatay\b|قونيا|\bkonya\b|انقره|\bankara\b|اضنه|\badana\b|ازمير|\bizmir\b|"
                           r"في (لبنان|الاردن|العراق|الخليج|السعوديه|الامارات|قطر|المانيا|مصر|الكويت|البحرين|عمان|الرياض|جده|دبي|ابوظبي|القاهره)"), re.I)
_TR_PHONE_IN_TEXT = re.compile(r"(?:\+?90|0090|wa\.me/90)[\s-]?5\d{2}")
_MOAD_LINK = re.compile(r"bit\.ly/hiring|moad\.gov\.sy/recruitment")
_GENERIC_PERSON = re.compile(F(r"^(شب|شاب|شباب|صبيه|صبايا|بنت|بنات|بنتين|انسه|سيده|امراه|مقيم|مقيمه|موظف|موظفه|موظفين|موظفات|عامل|عامله|عمال|عاملات|"
                               r"شخص|اشخاص|كادر|فريق|شباب وصبايا|شاب او صبيه|امراه او صبيه|صبيتين|شابين|شابان|اكتر من صبيه|انسه|انسات)$"))


def _strip_emoji(s: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI.sub(" ", s)).strip()


def _clean_parens(s: str) -> str:
    """Fix dangling/unclosed parentheses and spacing inside brackets without breaking valid tags."""
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s*\(\s*$", "", s)
    if s.startswith("(") and s.count("(") > s.count(")"):
        s = s[1:].strip()
    while s.count("(") > s.count(")"):
        idx = s.rfind("(")
        if idx < 0:
            break
        tail = s[idx + 1:].strip(" -–:،,.|")
        if not tail or (len(s) >= 75 and " " not in tail and len(tail) <= 3):
            s = s[:idx].strip(" -–:،,.|")
        else:
            s = s[:idx] + "(" + tail + ")"
    return re.sub(r"\s+", " ", s).strip()


def normalize_title_display(s: str, keep_matlob: bool = False) -> str:
    """Normalize a display job title cleanly: strip emojis, announcements, leading single count, doubled shifts, dangling '('."""
    s = _strip_emoji(normalize(s))
    s = re.sub(r"^\s*(?:\d{1,2}|[٠-٩]{1,2})\s*[-.)]\s*", "", s)  # «11- مدير عام»
    # Strip single leading vacancy count («1 سائق آليات» -> «سائق آليات»), preserving phrases like «4 شواغر توظيف» for filtering
    s = re.sub(
        r"^\s*(?:[1-9]|1[0-9]|[١-٩])\s+(?=[ء-يA-Za-z])(?!سنوات|اشهر|أشهر|ايام|أيام|عام|سنة|ساعة|ساعات|دولار|ليرة|شواغر|وظائف)",
        "",
        s,
    )
    s = re.sub(r"^\s*o\s+(?=[ء-ي])", "", s)
    for _ in range(3):
        if keep_matlob:
            s = re.sub(
                r"^\s*(?:(?:إعادة|اعادة)\s+(?:إعلان|اعلان)|(?:إعلان|اعلان)\s+(?:توظيف|شاغر(?:\s+وظيف[يى])?)|(?:المسم[ىي]|المنصب|الشاغر)\s*الوظيف[يى]|المنصب(?=\s*[:：])|فرص[ةه]\s*عمل|نحن\s*نوظ[ّ]?ف|Hiring|Position(?:\s*Title)?|Job\s*Title|Vacancy(?:\s*Title)?)\s*[:：\-–|/|]*\s*",
                "",
                s,
                flags=re.I,
            )
        else:
            s = re.sub(
                r"^\s*(?:(?:المسم[ىي]|المنصب|الشاغر)\s*الوظيف[يى]|المنصب(?=\s*[:：])|(?:إعادة|اعادة)\s+(?:إعلان|اعلان)|(?:إعلان|اعلان)\s+(?:توظيف|شاغر(?:\s+وظيف[يى])?)|فرص[ةه]\s*عمل|نحن\s*نوظ[ّ]?ف|المسمى|مطلوب(?:ة|ون|ين)?|يلزمنا|Hiring|Position(?:\s*Title)?|Job\s*Title|Vacancy(?:\s*Title)?)\s*[:：\-–|/|]*\s*",
                "",
                s,
                flags=re.I,
            )
    s = re.sub(r"^\s*(?:أولا|اولا|ثانيا|ثالثا|رابعا|خامسا|سادسا)ً?\s*[:\-–]\s*", "", s)
    s = re.sub(r"\s*[-–]\s*غير محدد\s*$", "", s)
    s = re.sub(r"[#@]\S+", " ", s)
    s = re.sub(r"\*+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–:،,.|/")
    # Remove doubled trailing shift chips when the shift/mode already appears earlier in the title
    s = re.sub(
        r"(\([^)]*(?:دوام|عن\s*ب[ُ]?عد)[^)]*\))\s+(?:دوام\s*(?:جزئي|جزئى|كامل|حضوري)|عن\s*ب[ُ]?عد|عمل\s*حر)\s*$",
        r"\1",
        s,
    )
    s = re.sub(r"([–\-|]\s*عن\s*ب[ُ]?عد\b.+?)\s+عن\s*ب[ُ]?عد\s*$", r"\1", s)
    for _ in range(3):
        s = _TITLE_TRAIL.sub("", s).strip(" -–:،,.")
    if len(s) > 94:
        cut = s[:94]
        if not s[94:95].isspace() and " " in cut[40:]:
            cut = cut.rsplit(" ", 1)[0]
        s = cut.strip(" -–:،,.")
    s = _clean_parens(s)
    return s.strip(" -–:،,.")


def _clean_title(s: str) -> str:
    return normalize_title_display(s, keep_matlob=False)[:80].strip()


def _cut_title(s: str) -> str:
    """The title part of a longer line: 'محاسب للعمل في شركة دوام كامل' -> 'محاسب للعمل في شركة'."""
    s = _strip_emoji(s).strip(" *:")
    m = _TITLE_CUT.search(s, 3)
    if m:
        s = s[:m.start()]
    return _clean_title(s)


def parse_deadline(text: str, posted_at: str | None = None) -> str | None:
    folded = F(text)
    if _CLOSED_RX.search(folded):
        return "2000-01-01"  # explicitly closed/filled

    post_date = date.today()
    if posted_at:
        try:
            post_date = datetime.fromisoformat(posted_at.replace("Z", "+00:00")).date()
        except Exception:
            pass

    found_dates = []

    for m in _DEADLINE_HINT.finditer(folded):
        w_start = max(0, m.start() - 25)
        w_end = min(len(folded), m.end() + 90)
        window = folded[w_start:w_end]
        raw_window = text[w_start:w_end]

        for dm in _DATE.finditer(raw_window):
            prefix = raw_window[max(0, dm.start() - 25):dm.start()]
            if re.search(F(r"(?:نهايه|مده|فتره)\s+(?:العقد|المشروع)"), F(prefix)):
                continue
            a, b, c = dm.groups()
            try:
                y, mo, day = (int(a), int(b), int(c)) if len(a) == 4 else (int(c), int(b), int(a))
                if y < 100: y += 2000
                dt = date(y, mo, day)
                if 2024 <= dt.year <= 2030:
                    found_dates.append(dt)
            except ValueError:
                pass

        for wm in _DATE_WORDS.finditer(raw_window):
            prefix = raw_window[max(0, wm.start() - 25):wm.start()]
            if re.search(F(r"(?:نهايه|مده|فتره)\s+(?:العقد|المشروع)"), F(prefix)):
                continue
            try:
                day = int(wm.group(1))
                mo = _MONTHS[wm.group(2)]
                y = int(wm.group(3)) if wm.group(3) else post_date.year
                dt = date(y, mo, day)
                if 2024 <= dt.year <= 2030:
                    found_dates.append(dt)
            except ValueError:
                pass

        if not found_dates:
            for dm in _DATE_2.finditer(raw_window):
                prefix = raw_window[max(0, dm.start() - 25):dm.start()]
                if re.search(F(r"(?:نهايه|مده|فتره)\s+(?:العقد|المشروع)"), F(prefix)):
                    continue
                try:
                    a, b = int(dm.group(1)), int(dm.group(2))
                    if 1 <= a <= 31 and 1 <= b <= 12:
                        dt = date(post_date.year, b, a)
                        found_dates.append(dt)
                except ValueError:
                    pass

        if not found_dates:
            for wkm in _WEEKDAY_RX.finditer(window):
                target_wd = _WEEKDAYS[wkm.group(1)]
                diff = target_wd - post_date.weekday()
                found_dates.append(post_date + timedelta(days=diff))

    if found_dates:
        return max(found_dates).isoformat()
    return None


_PORTAL_EMPLOYER = re.compile(F(r"^(?:منصه|بوابه|خدمات|قناه)\s+التوظيف|وظفني|فرص عمل في سوريا"))


def _employer(text: str) -> str | None:
    for rx in _EMPLOYER:
        if m := rx.search(text):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" :،,")
            name = re.sub(r"\s+عن$", "", name)
            if re.fullmatch(r"ال\S+", name):  # «المديرية», «الشركة»: no name given
                continue
            name = re.sub(r"\s+(?:فرع|في)\s+\S+$", "", name) if len(name) > 40 else name
            name = _strip_emoji(name)
            if _PORTAL_EMPLOYER.search(F(name)):
                continue
            return name
    return None


def _clean_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or _NOISE_LINE.search(F(s)) or not re.search(r"\w", s):
            continue
        if re.fullmatch(r"(#\S+\s*)+", s):  # a line of hashtags only
            continue
        out.append(s)
    return out


def _bullet_body(line: str, in_list: bool) -> str | None:
    """The text after a list bullet, or None if the line isn't a list item."""
    for rx in (_ITEM_BULLET, _ORDINAL) + ((_SOFT_BULLET,) if in_list else ()):
        m = rx.match(line if rx is not _ORDINAL else F(line))
        if m and m.end() > 0:
            if rx is _ORDINAL:
                # map the folded match length back onto the original line
                body = re.sub(r"^\s*\S+\s*[:\-–]\s*", "", line, count=1)
            else:
                body = line[m.end():]
            body = body.strip()
            return body or None
    return None


_NUMBERED_ROLE = re.compile(
    r"^\s*(?:(?:🔹|🔸|🔷|🔶|📌|⭐|🌟|💠|▪️|▪|◾|◽|•|\*)\s*)?(\d{1,2}|[٠-٩]{1,2})\s*(?:\ufe0f?\u20e3\s*|[-.)]\s+)(.+)"
)
_REQ_ITEM_START = re.compile(
    F(r"^(?:ان يكون|مظهر لائق|العمر|يفضل|خبره|شهاده|امتلاك|الحصول علي شهاده|ارسال سيره|ايام العمل|ساعات العمل|الراتب|المزايا|التفرغ|الالتزام|الدقه|اجاده|معرفه ب|مستوي جيد|حديث تخرج)")
)
_DIGIT_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _extract_numbered_outline(lines: list[str], start: int) -> list[tuple[str, str | None]]:
    """Extract only top-level numbered roles (1-, 2-, 1️⃣) when a post nests requirement sub-bullets under each role."""
    numbered: list[tuple[str, str | None]] = []
    saw_stop = False
    prev_num = 0
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        plain = _strip_emoji(line)
        if _SECTION_STOP.match(F(plain)):
            saw_stop = True
            continue
        if _GRADE_LINE.match(F(plain)):
            continue
        m = _NUMBERED_ROLE.match(line)
        if m:
            raw_num = int(m.group(1).translate(_DIGIT_TRANS))
            role_raw = m.group(2).strip()
            if role_raw.endswith((":", "：")):
                # Category heading like «1️⃣ الهندسة الميكانيكية:» rather than a single numbered role
                return []
            if saw_stop and raw_num <= prev_num:
                # Requirements/tasks list restarted numbering from 1 under a stop section
                break
            saw_stop = False
            prev_num = raw_num
            if _SECTION_STOP.match(F(role_raw)) or _LIST_END.search(F(role_raw)):
                break
            role_clean = _clean_title(role_raw)
            if 2 <= len(role_clean) <= 80 and _good_title(role_clean) and not _REQ_ITEM_START.match(F(role_clean)):
                numbered.append((role_clean, None))
            continue
        if numbered and _LIST_END.search(F(plain)) and not _bullet_body(line, in_list=True):
            break
    return numbered if len(numbered) >= 2 else []


def _list_items(lines: list[str]) -> tuple[list[tuple[str, str | None]], str]:
    """Job titles listed under a list header, each with the grade section it sits in. Also returns the header line."""
    start = next((i for i, l in enumerate(lines) if _LIST_HEADER.search(F(l))), None)
    if start is None:  # no header line, but grade sections («🔸 الفئة الأولى») open the list
        g = next((i for i, l in enumerate(lines) if (m := _GRADE_LINE.match(F(_strip_emoji(l)))) and len(F(_strip_emoji(l))) - m.end() < 3), None)
        if g is None:
            return [], ""
        start = g - 1
    header = lines[start] if start >= 0 else ""
    if numbered := _extract_numbered_outline(lines, start):
        return numbered, header
    bullets_only = bool(_BARE_NEED.match(F(header)))  # «*مطلوب:» then «-شيف ساخن» «-شيف مازا»
    items: list[tuple[str, str | None]] = []
    grade = None
    i = start + 1
    while i < len(lines):
        line = lines[i]
        fl = F(_strip_emoji(line))
        g = _GRADE_LINE.match(fl)
        if g and len(fl) - g.end() < 3:  # a section header: «🔸 الفئة الأولى»
            grade = _GRADE_WORD[g.group(1)]
            i += 1
            continue
        body = _bullet_body(line, in_list=True)
        bulleted = body is not None
        if body is None and bullets_only:
            break
        if body is None:
            plain = _strip_emoji(line)
            if _SECTION_STOP.match(F(plain)):
                break
            if i + 1 < len(lines) and _bullet_body(lines[i + 1], in_list=True) is not None and (
                lines[i].lstrip().startswith(("🔸", "🔶", "🔷", "🟦", "🟧", "🟪", "📁", "📂"))
                or plain.endswith(":")
                or re.search(F(r"^(?:مجال|قسم|قطاع|تخصصات)\s"), F(plain))
            ):
                i += 1
                continue
            # Unbulleted short lines right after the header can still be titles («سكرتاريا»).
            if (not items or not _LIST_END.search(F(plain))) and 2 < len(plain) <= 45 and not re.search(r"[:\d]|https?://", plain) \
                    and not _LIST_END.search(F(plain)) and not _SECTION_STOP.match(F(plain)):
                body = plain
            elif re.match(r"^(الرمز الوظيفي|رمز|https?://|عدد)", plain):  # per-item details («الرمز الوظيفي: 231531», link)
                i += 1
                continue
            else:
                break
        hard_end = re.search(F(r"https?://|يتم قبول|اخر موعد|للتواصل|للتقديم"), F(body))
        if _SECTION_STOP.match(F(body)) or hard_end or len(body) > 90:
            break
        if not bulleted and _LIST_END.search(F(body)):
            break
        item_grade = grade
        g2 = _GRADE_LINE.match(F(body))
        if g2:  # «الفئة الرابعة: عامل نظافة»
            item_grade = _GRADE_WORD[g2.group(1)]
            body = re.sub(r"^\s*(?:ال)?فئ[ةه]\s*\S+\s*[:\-–]?\s*", "", body)
        body = _clean_title(body)
        if 2 <= len(body) <= 80 and _good_title(body) and not _REQ_ITEM_START.match(F(body)):
            items.append((body, item_grade))
        i += 1
    return items, header


def _link_items_with_urls(lines: list[str]) -> list[tuple[str, str | None]]:
    """Posts that list each job as a title line followed by its own link (احسان، شفق، الأطباء المستقلين)."""
    items = []
    for a, b in zip(lines, lines[1:]):
        b_clean = _strip_emoji(b)
        if re.match(r"https?://", b_clean) and not re.match(r"https?://", _strip_emoji(a)) and len(a) <= 70 and not _LIST_END.search(F(a)) \
                and not _ANNOUNCE.search(F(a)) and not re.search(r"[:：]$", a):
            t = _clean_title(_bullet_body(a, True) or a)
            if len(t) >= 3 and _good_title(t):
                u = tx.urls(b_clean)
                clean_u = da.strip_tracking(u[0]) if u and _is_valid_apply_url(u[0]) else None
                if clean_u:
                    items.append((t, clean_u))
    return items


def _link_items(lines: list[str]) -> list[tuple[str, str | None]]:
    """Return `(title, None)` pairs compatible with `(title, grade)` item lists."""
    return [(t, None) for t, _ in _link_items_with_urls(lines)]


def _bullet_block(lines: list[str]) -> list[tuple[str, str | None]]:
    """A run of short bullet lines that name jobs, not requirements («🔹 مدير تقنية المعلومات» × 10)."""
    best, run, prev, skipping = [], [], "", False
    for line in lines + [""]:
        body = _bullet_body(line, in_list=True) if line else None
        if body is not None and (skipping or (not run and _SECTION_STOP.match(F(_strip_emoji(prev))))):
            skipping = True  # bullets under «المهام:» / «الشروط:» are duties and requirements, not titles
            continue
        skipping = False
        if body is not None:
            run.append(body)
            continue
        if run and line and re.match(r"^[^:：]{2,25}[:：]", _strip_emoji(line)) and not _SECTION_STOP.match(F(_strip_emoji(line))):
            continue  # «عدد الشواغر: 20» under an item
        if run:
            ok = [
                b for b in run
                if len(b) <= 80 and ":" not in b and not _REQ_WORDS.search(F(b))
                and not _REQ_ITEM_START.match(F(b)) and _good_title(_clean_title(b))
            ]
            if len(ok) >= 2 and len(ok) >= 0.8 * len(run) and len(ok) > len(best):
                best = ok
        run, prev = [], line
    return [(_clean_title(b), None) for b in best]


def _pin_items(lines: list[str]) -> list[tuple[str, str | None]]:
    """«📌 ميسر تدريب مهني» followed by «📍 مكان العمل: …» for each job."""
    out = []
    for a, b in zip(lines, lines[1:]):
        if a.lstrip().startswith("📌") and re.match(r"\s*📍|\s*مكان العمل", b):
            t = _clean_title(a)
            if 2 <= len(t) <= 70:
                out.append((t, None))
    return out


def _ordinal_items(lines: list[str]) -> list[tuple[str, str | None]]:
    """«أولاً: محاسبة … ثانياً: مندوب مبيعات …» with long descriptions in between."""
    out = []
    for l in lines:
        m = _ORDINAL.match(F(_strip_emoji(l)))
        if m:
            t = _cut_title(re.sub(r"^\s*\S+\s*[:\-–]\s*", "", _strip_emoji(l), count=1))
            if 2 <= len(t) <= 60 and not _SECTION_STOP.match(F(t)) and not re.match(r"^(?:أن|ان)\s+(?:يكون|تكون)\b", t):
                out.append((t, None))
    return out


_SY_TITLE_CITY_SUFFIX = re.compile(
    r"\s+(?:في\s+(?:مدينة\s+|محافظة\s+|منطقة\s+)?|بـ?(?:مدينة\s+|محافظة\s+)?)?"
    r"(?:ريف\s+دمشق|دمشق|حلب|حمص|حماة|حماه|إدلب|ادلب|اللاذقية|اللاذقيه|طرطوس|درعا|السويداء|القنيطرة|القنيطره|دير\s*الزور|الحسكة|الحسكه|الرقة|الرقه)"
    r"(?:\s*[-–]\s*[^\n]{2,25}|\s+(?:مواقع\s+المشاريع|السوق\s+الحرة|المقر\s+الرئيسي))?\s*$",
    re.I,
)
_TR_TITLE_CITY_SUFFIX = re.compile(
    r"\s+(?:في\s+(?:ولاية\s+|مدينة\s+)?)?(?:إسطنبول|اسطنبول|غازي\s*عنتاب|مرسين|أنقرة|انقرة|بورصة|بورصه|أنطاكيا|هاتاي|أضنة|اضنة|إزمير|ازمير|قونيا|شانلي\s*أورفا|قيصري|كليس|طرابزون|أنطاليا|انطاليا|istanbul|ankara|gaziantep|mersin|bursa|izmir)\s*$",
    re.I,
)


def _split_item_city(item: str, country: str = "sy") -> tuple[str, list[str]]:
    """Split city suffixes or extract city mentions from a role headline without losing SRD/specialty info."""
    def _cities_fn(s: str) -> list[str]:
        fn = tx.tr_cities_from_text if country == "tr" else tx.cities_from_text
        gen = "عدة ولايات في تركيا" if country == "tr" else "عدة محافظات"
        return [c for c in fn(s) if c != gen]

    is_srd = bool(re.search(r"\bSRD\s*[-–]?\s*\d+", item, re.I))

    # 1. Comma-delimited suffix: preserve full title context while locking trailing city ('...، حلب')
    if m_comma := re.match(r"^(.+?)\s*[،,]\s*([^،,]{2,35})$", item):
        tail_cs = _cities_fn(m_comma.group(2))
        if tail_cs and len(m_comma.group(1).strip()) >= 4:
            return item.strip(), tail_cs

    sfx_rx = _TR_TITLE_CITY_SUFFIX if country == "tr" else _SY_TITLE_CITY_SUFFIX

    # 2. Delimiter-separated suffix ('Program Officer / Damascus', 'أمين صندوق – دمشق - السوق الحرة', 'مروجة - طرطوس')
    for rx in (
        re.compile(r"^(.+?)(?:\s+/\s+|\s*[-–|]\s*)(.+)$"),
        re.compile(r"^(.+)(?:\s+/\s+|\s*[-–|]\s*)([^-–|/]+)$"),
    ):
        if m := rx.match(item):
            head, tail = m.group(1).strip(" -–/|،,"), m.group(2).strip()
            tail_cs = _cities_fn(tail)
            if tail_cs and len(tail) <= 45 and len(head) >= 4 and _good_title(head):
                if is_srd:
                    return item.strip(), tail_cs
                if m_sub := sfx_rx.search(head):
                    head2 = head[:m_sub.start()].strip(" -–/|،,")
                    extra_cs = _cities_fn(head[max(0, m_sub.start() - 25):m_sub.end()])
                    if extra_cs and len(head2) >= 4 and _good_title(head2) and not _NOT_TITLE.search(head2.strip("-•* ")):
                        merged_cs = list(dict.fromkeys(extra_cs + tail_cs))
                        return head2, merged_cs
                return head, tail_cs

    # 3. Space-separated trailing city ('مهندس ميكانيك دراسات دمشق', 'سائق بوب كات (تركس صغير) دمشق مواقع المشاريع')
    if m := sfx_rx.search(item):
        head = item[:m.start()].strip(" -–/|،,")
        ctx = item[max(0, m.start() - 25):m.end()]
        tail_cs = _cities_fn(ctx)
        if tail_cs and len(head) >= 4 and _good_title(head) and not _NOT_TITLE.search(head.strip("-•* ")):
            return head, tail_cs

    # 4. Inline city outside parentheses (do not let parenthetical experience override real city)
    outside = re.sub(r"\([^)]*\)", " ", item)
    out_cs = _cities_fn(outside)
    if out_cs:
        return item.strip(), out_cs

    return item, []


# Lines that are greetings or duties («إعداد التقارير المالية…»), never job titles.
_NOT_TITLE = re.compile(r"^(?:ال)?سلام عليكم|^مرحبا|^من (?:بيتك|البيت|المنزل)|"
                        r"^(?:تحديد|إعداد|اعداد|متابعة|تنفيذ|القيام|المساهمة|ضمان|تقديم|التواصل|التنسيق|المشاركة|مراجعة|"
                        r"كتابة|بناء|دعم|إدارة|ادارة)\s+\S+\s+\S+|"
                        r"^\(|^(?:مطلوب[ةه]?|بهار|نارة|فريق|صبية|شاب|فرصة|كوادر)$|^عرض خاص|^الزملاء|^للجنسي?ين|^اكيد|^أكيد|^كفاءات|^الهندسية$|^تلفزيون|^قناة|^إذاعة|"
                        r"^الموعد|^صدور|نتائج|^لتقديم|قم بالدخول|^نواصل|بترحب|^حرصا|^نسبة|^تسجيل و|^دعوة|^من جميع المتقدمين|ممن تنطبق|"
                        r"^يوجد|^الهلال الأحمر|^مشفى(?![^\n]*(?:بحاج[ةه]|مطلوب|تطلب|يلزم|شاغر|توظيف))|^مستشفى(?![^\n]*(?:بحاج[ةه]|مطلوب|تطلب|يلزم|شاغر|توظيف))|^رابط|^الطبية$|^الشركات|^(?:ما\s+(?:هو|هي)|النجاح\s+هو|التوظيف\s+هو)|"
                        r"^في\s|^ضمن\s|^اشتراك|^باقة|^www\.|^فرص[ةه]?$|^فرصتك|^فرصة تعاقد|(?:^|\s)لتوريد\b|توريد (?:خدمات|مواد|وقود)|مناقصة|عروض أسعار|"
                        r"^اليوم والتاريخ|^التوقيت|^الوثائق المطلوبة|^يرجى التواجد|^أجرى|^تفاصيل وموعد|^موعد (?:الاختبار|المقابلة|الامتحان)|^الكسل الوظيفي|^تشكل نقاط|^تصريح|^المساعدات الإنسانية|^الرواتب الشهرية|^المدينة الصناعية|^سانا$|"
                        r"^وظيف[ةه](?:\s+جديد[ةه])?$|^الشواغر\s+(?:الوظيفي[ةه]|المتاح[ةه])$|^الرمز\s+الوظيف[يى]|^المميزات\b|^نبذ[ةه]\b|^رواتب\s+عالي[ةه]|^بيئ[ةه]\s+عمل|^اجازات\s+سنوي[ةه]|^تأمين\s+صح[يى]|"
                        r"^العدد\s*[\d٠-٩]+$|^وصف\s+مختصر\s+ل|^نموذج\s+الأعمال\s+السابقة|"
                        r"^(?:المهام|المسؤوليات|الشروط|المهارات|المؤهلات|المتطلبات|ال[إا]ختصاصات|التعاقد\s+مع\s+الشركات|الرواتب)\b|"
                        r"^(?:للاطلاع\s+على|هل\s+أنت\s+مهتم|رفد\s+ك[وا]+در|ك[وا]+در(?:\s+(?:وطني[ةه]|مؤهل[ةه]|للعمل))?$|شواغر\s+(?:توظيف|وظيفي[ةه])|\d+\s*شواغر|"
                        r"نوع\s+الدوام|الدوام\b|دوام\s+(?:كامل|جزئي)|الراتب(?:\s*[:：]|$)|راتب\s+(?:شهري\s+عالي|ثابت|مجز)|"
                        r"خبر[ةه]\s+(?:في|لا\s*تقل)|العمر\b|السن\b|فرع\s+(?:إدلب|ادلب|دمشق|حلب|حمص|حماة|الحريقة)|شهاد[ةه]\s+معهد)|"
                        r"^(?:(?:أن|ان)\s+يكون\s+(?:سوريا|المتقدم)|وطن$|عربي$|للعم$|حدا$)|"
                        r"^(?:مكان\s+العمل|الموقع|المعرف\s+الوظيف[يى])\s*(?:[:：(]|$)|^الوظيفتان\s+بدوام\s+كامل|"
                        r"^(?:امتلاك\s+مهار|اللباق[ةه]\s+وحسن|القدرة\s+على|[إا]جادة\s+(?:ال|لغ)|[إا]تقان\s+(?:ال|عمل)|حسن\s+(?:التعامل|المظهر)|الالتزام\s+ب)|"
                        r"^(?:لتوظيف\s+في\b|لمركز\s+|مميز[ةه]\s+بانتظارك|كبر\s+فريقنا|كيف\s+تحدث\b)|"
                        r"^(?:مدة\s+ال[إا]علان\b|فتر[ةه]\s+التقديم\b|التفاصيل\s+والشروط\s+كامل[ةه]?$|الشاغر\s*:|وصف\s+الشواغر|[آا]خر\s+موعد\s+تقديم\b|أوقات\s+الدوام\s*:|الصفات\s*:|الفئ[ةه]\s+المستهدف[ةه]\s*:|التبعي[ةه]\s+ال[إا]داري[ةه]\b|جهة\s+العمل\s*:)|"
                        r"^(?:السير[ةه]\s+الذاتي[ةه]|رسالة\s+تعريفي[ةه]|يرجى\s+[إا]رسال|[أا]رسل\s+السير|[أا]رفق\s+\d|لا\s+نستقبل\s+اتصالات|يجب\s+استخدام\s+نموذج|شو\s+عم\s+تستنى|هل\s+تحلم\s+بالعمل|نحو\s+تجرب[ةه]\s+مصرفي[ةه])|"
                        r"^(?:العمل\s+(?:من\s+البيت|حضوري|عن\s+بعد|24\s+ساعة)|وظيف[ةه]\s+عن\s+بعد|لا\s+تشترط\s+شهاد[ةه]|متاح\s+للشباب\s+فقط|خبر[ةه]\s+(?:جيد[ةه]|عملي[ةه]|قوي[ةه]|سابق[ةه]|مثبت[ةه]|متقدم[ةه]|كافي[ةه]|مميز[ةه]|ممتاز[ةه]|ميداني[ةه])|امتلاك\s+(?:رخص[ةه]|خبر[ةه]|حس)|حديث\s+تخرج|معرف[ةه]\s+ب(?:مبادئ|معايير)|مستو[ىي]\s+جيد\s+باللغ[ةه]|مونتاج\s+و[إا]نتاج|تصميم\s+وتنفيذ\s+Reels|تخطيط\s+و(?:متابع[ةه]|تنفيذ)|الرغب[ةه]\s+في\s+التعلم|تتاح\s+الفرص[ةه]\s+لحديثي|فرص[ةه]\s+تعاون\s+مهن[يى]|تبحث\s+شرك[ةه]\s+.{1,40}\s+عن\s+كوادر|[إا]لمام\s+جيد|[إا]جادة\s+(?:استخدام|برامج|جيد[ةه])|الأفضلي[ةه]\s+لمن|الدق[ةه]\s+(?:وتحمل|والاهتمام)|الجدي[ةه]\s+والالتزام|الالتزام\s+(?:وتحمل|والدق[ةه])|العمل\s+ضمن\s+نقط[ةه]|تنظيم\s+الملفات|استقبال\s+المكالمات|ال[إا]شراف\s+على\s+ال[أا]عمال|أن\s+يتراوح\s+العمر|أن\s+يكون\s+نشيط|حاصل/?[ةه]?\s+على\s+شهاد[ةه]|الحصول\s+على\s+شهاد[ةه]|شهاد[ةه]\s+(?:في|جامعي[ةه]|بكالوريوس|[إا]جاز[ةه])|التفرغ\s+(?:التام|الكامل)|مهار(?:ات|ة)\s+(?:التواصل|عالي[ةه]|قوي[ةه]|قيادي[ةه]|تنظيمي[ةه])|متفرغ/?[ةه]?\s+للعمل|خريجون\s+جدد\s+مرحب|المطلوب\s+أصحاب\s+الخبر[ةه])|"
                        r"^(?:تليغرام|تيليجرام|تلغرام|تليجرام|واتساب|واتس\s*اب|فيسبوك|انستغرام|انستقرام|لينكد[إا]ن|الموقع\s+الرسمي|شغلني|تكاتف\s+للوظائف|بواب[ةه]\s+الوظائف|متقدم$|كوادر\s+جديد[ةه]|مظهر\s+لائق|نحن\s+نوسع\s+فريق|توسيع\s+فريق\s+عملها|يفضل\s+وجود|عم\s+نبحث\s+عن\s+موظفين|موظفين\s+من\s+أصحاب|أصحاب\s+المحلات|التجار\s+وأصحاب|العلامات\s+التجاري[ةه]|المتسوقون|الأطباء$|طب\s+الفم\s+والأسنان$|الشواغر\s+الوظيفي[ةه]\s+المطلوب[ةه]|الوظائف\s+المتاح[ةه]$|تأمين\s+النقل|تدريب\s+وتطوير|مجال\s+للتطور|قسم\s+(?:المبيعات|الصيانة|الخدمات|الموارد|المحاسبة|المالية)$|^الخدمات$|^(?:ميكانيك|تصميم\s+وإنتاج|تصميم\s+ميكانيكي|كهرباء|طاقة\s+كهربائية|أتمتة\s+وتحكم\s+آلي|أنظمة\s+التحكم\s+الصناعية)$|أيام\s+العمل\s*:|ساعات\s+العمل\s*:|الراتب\s+والمزايا\s*:|3D\s*Max$|Corona\s*/\s*V-Ray$|HKN\s+Syria$|اترك\s+تعليق|إذا\s+كنت\s+بتتمتع|اذا\s+كنت\s+تتمتع|التزام\s+وقدر[ةه]\s+على\s+العمل|نتمنى\s+التوفيق|الفريق\s+ال[إا]داري\s+والفني$|موظف[ةه]?\s+للانضمام\s+[إا]ل[ىي]\s+فريق|كوادر\s+نسائي[ةه]\s+متخصص[ةه]|كفاء[ةه]\s+متميز[ةه]\s+لشغل\s+وظيف[ةه]|انضم\s+[إا]ل[ىي]\s+|جميع\s+تفاصيل\s+الوظيف[ةه]|شهاد[ةه]\s+معتمد[ةه]\s+عند\s+[إا]تمام\s+التدريب|^الجه[ةه]\s+\S+$|يتم\s+استبعاد\s+الطلبات|سيتم\s+النظر\s+فقط|يمكن\s+(?:للمهتمين|تقديم)|يرجى\s+تقديم|للمهتمين\s+بالانضمام|منص[ةه]\s+خطو[ةه]|تدعوكم\s+|قطاع\s+بيع\s+التجزئ[ةه]$|الشواغر\s+الوظيفي[ةه]\s+المتاح[ةه]$|الشرك[ةه]\s+ومجالها$|صاحبات$|يتوفر\s+لدينا\s+شاغر$|بالتنسيق\s+والتعاون|يهدف\s+هذا\s+التدريب|"
                        r"إلى\s+من\s+يهمه|الى\s+من\s+يهمه|ادعمولنا|ادعمونا|شاب\s+عندو?|صبي[ةه]\s+عندها?|(?:الالتزام|الانضباط)\s+و)|"
                        r"^(?:وظيف[يى]|مطعم|كافيه|محل|شرك[ةه]|مؤسس[ةه])$|"
                        r"(?:^|\s)(?:يطلب|تطلب|تعلن\s+عن\s+وظائف\s+شاغر[ةه])\s*$|"
                        r"(?i:^(?:organization|closes|closing\s*date|deadline|duty\s*station|location|son\s+başvuru\s+tarihi)\s*[:：]|"
                        r"^(?:current\s+salary|expected\s+salary|notice\s+period|competitive\s+salary|internet\s+allowance|job\s+opportunity|university\s+graduates|age\s+up\s+to|very\s+good\s+english|available\s+to\s+join|stable\s+and\s+reliable|comfortable\s+in\s+a\s+target|creative\s+thinking|opportunities\s+for\s+professional|performance-based\s+bonuses|stay\s+updated|plan\s+and\s+coordinate|track\s+growth|present\s+product|handle\s+inquiries|follow\s+up\s+on\s+leads|achieve\s+monthly|building\s+and\s+maintaining|mobile\s+shooting|reel\s+editing)\b|"
                        r"^(?:for\s+)?(?:two|three|four|five|several|\d+)\s+positions?\b|"
                        r"^excellente\s+maîtrise\b)")
_LATIN_SENTENCE = re.compile(
    r"\b(years?|experience|minimum|required|must|apply|send|knowledge|proficiency|familiarity|"
    r"career move|foundation|understanding|passionate|campaigns|to review|responsibilities|"
    r"click here|for more|job description|job summary|create and|ensure consistent|align all|"
    r"use the app|strong communication|bachelor'?s? degree|master'?s? degree|working conditions?|"
    r"ability to|proven experience|commitment to the role)\b|"
    r"\b(?:yönetecek|liderlik\s+edecek|operasyonlarını|kalitesini)\b|"
    r"\b(?:roas|kpis?|self-?motivated|objection-handling|closing skills|brand identity|content calendars?|content plans?|track opportunit|customer interactions|strong alignment|kpi reporting|growth in engagement)\b",
    re.I,
)
_STRICT_LATIN_NON_JOB = re.compile(
    r"\b(?:bachelor'?s?\s+degree|master'?s?\s+degree|working\s+conditions?|create\s+and\b|"
    r"ensure\s+consistent|align\s+all|strong\s+communication|ability\s+to|"
    r"commitment\s+to\s+the\s+role|responsibilities|click\s+here|for\s+more|"
    r"job\s+description|job\s+summary)\b",
    re.I,
)
_EN_IMPERATIVE_START = re.compile(
    r"^(?:create|ensure|align|develop|manage|coordinate|maintain|prepare|monitor|assist|support|"
    r"perform|collaborate|participate|provide|conduct|review|update|use|record|"
    r"strong|clear|improved|growth|organized|proactive|excellent|proven)\s+",
    re.I,
)
_EN_ROLE_NOUN = re.compile(
    r"\b(?:manager|officer|coordinator|assistant|specialist|engineer|developer|analyst|designer|"
    r"lead|leader|head|director|supervisor|teacher|trainer|consultant|counselor|intern|educator|technician|nurse|"
    r"pediatrician|agronomist|medic|paramedic|midwife|enumerator|steward|"
    r"accountant|driver|translator|interpreter|representative|advisor|worker|chef|"
    r"expert|therapist|pharmacist|chemist|doctor|surgeon|editor|writer|producer|marketer|"
    r"recruiter|auditor|cashier|barista|cook|cleaner|guard|operator|mechanic|electrician|"
    r"carpenter|tailor)\b",
    re.I,
)


def _good_title(t: str) -> bool:
    """Reject phone numbers, links, greetings, duty lines, place names and non-job announcements."""
    if len(t) < 3 or re.fullmatch(r"[\d\s+()-]+", t) or re.match(r"https?://|www\.", t) or _NOT_TITLE.search(t.strip("-•* ")) \
            or _GENERIC_PERSON.match(F(t)) or _only_places(t) or t in tx.CITIES or t == "عدة محافظات" or re.search(r"\.(com|net|org)\b", t):
        return False
    if not re.search(r"[ء-ي]", t):
        if _STRICT_LATIN_NON_JOB.search(t):
            return False
        if _LATIN_SENTENCE.search(t) and not _EN_ROLE_NOUN.search(t):
            return False  # «Minimum 2 years of experience in medical tourism» / «Create and manage content calendars»
        if _EN_IMPERATIVE_START.match(t) and not _EN_ROLE_NOUN.search(t):
            return False
    return bool(re.search(r"[ء-ي]", t)) or len(t.split()) >= 2 or bool(_EN_ROLE_NOUN.fullmatch(t.strip()))


_STRONG = re.compile(
    r"(?:المسم[ىي]|المنصب|الشاغر)\s*الوظيف|المنصب\s*[:：]|بمسمى|بصف[ةه]\s|(?:إعلان|اعلان|شاغر)\s+(?:شاغر|وظيف[يى])|"
    r"حاجت(?:ها|ه)\s*(?:إلى|الى|ل)|(?:^|\n|[|–\-])[^\w\n]{0,6}\*?\s*(?:مطلوب|يلزمنا)\b[^\n:]{3,}|"
    r"we['’]?re\s+hiring|is\s+hiring|looking\s+for\s+(?:an?\s+)?|\b(?:position|job\s*title|vacancy(?:\s*title)?)\s*[:：]",
    re.I,
)


def _all_needs(text: str) -> list[str]:
    """Posts that glue several one-job announcements together («تعلن … بمنصب X» … «تعلن … بمنصب Y»)."""
    rx = _HAJA_RX
    out = []
    for m in rx.finditer(text):
        t = _cut_title(m.group(1))
        if len(t) >= 3 and _good_title(t) and not re.search(r"التالي|الآتي|الاتي", t) and t not in out:
            out.append(t)
    return out


def _single_title(text: str, lines: list[str]) -> tuple[str, str]:
    """(title, the line it came from) for a one-job post."""
    head = "\n".join(text.split("\n")[:2])
    order = _TITLE_PATTERNS[:5] + ([_NEED_LINE] if _NEED_LINE.search(head) else []) + _TITLE_PATTERNS[5:] + [_NEED_LINE]
    for rx in order:
        for m in rx.finditer(text):
            raw = m.group(1).strip()
            src = raw
            if not _strip_emoji(raw).strip(" *:"):  # «*مطلوب:» with the details on the next line
                after = text[m.end():].lstrip("\n").split("\n", 1)[0]
                raw = src = after
            # «حاجتها إلى موظف/ موظفة مبيعات للعمل بدوام كامل»
            if re.match(r"\s*https?://", raw) or re.match(r"\s*(?:منظمة|شركة|مؤسسة|جمعية|لدى|في)\s", _strip_emoji(raw)):
                continue
            raw = re.sub(r"\s(?:ذو|ذات|ذوي)\s+(?:منظر|مظهر)\s+لائق", "", raw)
            raw = re.sub(
                r"^\s*(?:شاب|شباب|صبي[ةه]|صبايا|آنس[ةه]|انسه|سيد[ةه]|شخص|موظف[ةه]?|عامل[ةه]?)\s+"
                r"(?:لديه[ا]?|يمتلك|تمتلك|ذو|ذات)\s+(?:خبر[ةه](?:\s+\S+)?\s+)?(?:في\s+مجال|بمجال|في)\s+",
                "",
                raw,
            )
            title = _cut_title(raw)
            if re.search(r"التالي|الآتي|الاتي|ما يلي", title):
                continue
            words = F(title).split()
            if words and _GENERIC_PERSON.match(words[0]) and len(words) > 3 and not re.search(r"للعمل|تعمل|تشتغل|يشتغل|بمحل|بمعمل|بمطعم|في", raw):
                continue  # «شخص يجمع بين الحس الإبداعي…»
            if _GENERIC_PERSON.match(F(title)) and re.search(r"للعمل|تعمل|تشتغل|يشتغل|للانضمام|لكادر", raw):
                # «شباب للعمل في مطعم» → keep the workplace: it is what the job is.
                title = _clean_title(re.split(r"\s(?:دوام|الدوام|العمر|من الساعة|للتواصل|يفضل|براتب|الراتب|لدينا)\b|[.،,]", raw)[0])[:70]
            if _good_title(title) and not _NOISE_LINE.search(F(title)):
                return title, src
    for l in lines:
        if _SECTION_STOP.match(F(_strip_emoji(_bullet_body(l, True) or l))):
            break  # what follows «المهام:» / «الشروط:» is duties and requirements
        t = _cut_title(l)
        if _good_title(t) and not l.startswith("#") and not _LIST_END.search(F(t)) and not _ANNOUNCE.search(F(t)) \
                and not re.match(r"^[^\w\n:：]{0,4}[^:：\n]{2,55}[:：]", l) and not _NOT_FALLBACK.search(F(t)) and not _only_places(t):
            return t, l
    return "", ""


# First lines that name the employer or the place, not the job («لجنة الانقاذ الدولية», «إدلب _ سرمدا»).
_NOT_FALLBACK = re.compile(F(r"^(لجنه|منظمه|جمعيه|مؤسسه|الاونروا|برنامج|شركه|الهيئه|المجلس|unicef|unhcr|wfp)\b|الصوره|ضمن الشروط|"
                             r"المحافظ(?:ات|ه)\s*المطلوب|محافظه العمل|بطاقه الوصف|رابط التقديم|^كفاءات|^نبحث عن كفاءات"), re.I)


def _only_places(t: str) -> bool:
    """A line that is just city or town names («إدلب _ سرمدا», «دارة عزة»)."""
    rest = F(t)
    for city in tx.CITIES + tx.TR_CITIES + [
        "سرمدا", "اعزاز", "الباب", "عفرين", "منبج",
        "دارة عزة", "دارةعزة", "قباسين", "اخترين", "أخترين", "صوران", "جنديرس", "تل رفعت",
        "مارع", "الأتارب", "الاتارب", "الدانا", "سلقين", "حارم", "جرابلس", "الطبقة",
        "القامشلي", "قامشلو", "رأس العين", "دوما", "حرستا", "داريا", "جرمانا", "صحنايا",
        "قدسيا", "جبلة", "بانياس", "سلمية", "مصياف",
        "لبنان", "بيروت", "سوريا", "سورية", "لرقة", "ريف", "الاردن", "عمان", "العراق", "اربيل", "تركيا", "turkiye", "turkey",
    ]:
        rest = rest.replace(F(city), " ")
    return not re.sub(r"[\W_\d]+", "", rest)


_ANNOUNCE = re.compile(F(r"^(اعلان|إعلان|فرصه عمل|فرص عمل|فرصتكم|وظيفه(?: جديده)?|#)|\bتعلن|\bيعلن|\bنعلن|\bشواغر|\bترحب|^يسر|hiring|join|vacancy announcement"), re.I)
_REQ_WORDS = re.compile(F(r"خبره|شهاده|اجاده|القدره|الالتزام|يفضل|مهارات|معرفه|العمر|الجنس|التفرغ|امتلاك|اتقان|حسن|الدقه|سكان|متفرغ|"
                          r"رخصه|اسلوب|الاهتمام|حاصل|متفرغ|العمل ضمن|تحمل|بيئه|راتب|رواتب|تامين (صحي|مواصلات|سكن)|مواصلات|فرصه|عدد|الفئه المستهدفه"))


def _fill_details(job: Job, text: str, title_text: str, single: bool, grade: str | None, header: str = "") -> Job:
    if grade:
        job.education, job.education_from = tx.MOAD_GRADE_EDU[grade], "grade"
        job.grade = grade
    else:
        stated = tx.education_from_text(title_text) or (tx.education_from_text(text) if single else None)
        if stated:
            job.education, job.education_from = stated, "stated"
        elif guess := tx.education_from_title(job.title):
            job.education, job.education_from = guess, "title"
    # Context = the opening lines only: footers and perks («تأمين صحي») mislead.
    job.field = tx.field_from_text(job.title, "\n".join(text.split("\n")[:3]) if single else header)
    if job.field == "computer" or (single and is_computer_degree(text)) or is_computer_degree(title_text):
        sub = computer_subfield(job.title)
        if job.field == "computer" or sub:
            job.field = "computer"
            job.subfield = sub or "DT"
    if single:
        job.experience = tx.experience_years(text)
        job.age_min, job.age_max = tx.age_range(text)
        job.gender = tx.gender(title_text) or tx.gender(job.title) or tx.gender(text)
        job.shift = tx.shift(text)
        job.salary = tx.salary(text)
    else:
        job.gender = tx.gender(title_text) or tx.gender(header)
        job.experience = tx.experience_years(title_text)
    return job


_SOURCE_DEFAULT_EMPLOYER = {
    "moad": "وزارة التنمية الإدارية",
    "moi": "وزارة الداخلية السورية",
    "mohe": "وزارة التعليم العالي والبحث العلمي",
    "moe": "وزارة التربية والتعليم السورية",
    "moh": "وزارة الصحة السورية",
    "moeai": "وزارة الاقتصاد والصناعة السورية",
    "mof": "وزارة المالية السورية",
    "mocait": "وزارة الاتصالات وتقانة المعلومات",
    "moe_energy": "وزارة الطاقة / الكهرباء والموارد المائية",
    "moa": "وزارة الزراعة والإصلاح الزراعي",
    "mopwah": "وزارة الأشغال العامة والإسكان",
    "damascusgovrnorat": "محافظة دمشق",
    "sana": "الجهات العامة الرسمية (سانا)",
    "hiast": "المعهد العالي للعلوم التطبيقية والتكنولوجيا (HIAST)",
    "damascus_univ": "جامعة دمشق",
    "svu": "الجامعة الافتراضية السورية",
    "syredcrescent": "الهلال الأحمر العربي السوري (SARC)",
    "sarc": "الهلال الأحمر العربي السوري (SARC)",
    "org_vio": "منظمة بنفسج الإنسانية",
    "violet": "جمعية بنفسج للإغاثة والتنمية",
    "takafulalsham": "منظمة تكافل الشام",
    "takaful": "منظمة تكافل الشام الخيرية",
    "nwsjobs": "منظمة بهار والعمل الإنساني",
    "sema": "الرابطة الطبية للمغتربين السوريين (SEMA)",
    "ataa": "جمعية عطاء للإغاثة الإنسانية",
    "ida": "رابطة الأطباء المستقلين (IDA)",
    "srd": "منظمة سوريا للإغاثة والتنمية (SRD)",
    "sened": "منظمة سند (Sened)",
    "bahar": "منظمة بهار (Bahar)",
    "reliefweb": "منظمة دولية / وكالة أممية",
    "worklink": "",
    "ngosquare": "",
    "unjobnet_sy": "",
    "syria_jobs": "",
    "ssf_gov": "الصندوق السيادي السوري",
    "gaziantepis": "شركات ومصانع غازي عنتاب",
    "gelbaslacom": "منظمات ومؤسسات في تركيا",
}


def _is_valid_apply_url(u: str | None) -> bool:
    if not u or not u.startswith(("http://", "https://")):
        return False
    if da.is_disallowed_offline_url(u):
        return False
    m = re.match(r"https?://([^/?#]+)", u)
    if not m:
        return False
    host = m.group(1).lower().split(":")[0]
    if "." not in host or len(host) < 4:
        return False
    tld = host.split(".")[-1]
    return len(tld) >= 2 and tld.isalpha()


def _extract_tasks_and_skills(text: str) -> tuple[list[str], list[str]]:
    tasks: list[str] = []
    skills: list[str] = []
    current = None
    task_rx = re.compile(r"^(?:(?:ال)?مهام|(?:ال)?مسؤوليات|الواجبات|طبيعة العمل|responsibilities|duties|what you will do)\b", re.I)
    skill_rx = re.compile(r"^(?:(?:ال)?شروط|(?:ال)?متطلبات|(?:ال)?مؤهلات|(?:ال)?مهارات|requirements|qualifications|skills)\b", re.I)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^[\s#*•🔹▪️▫️🔻👇👉✔️✅\-]+\s*", "", line).strip(" :：")
        if task_rx.match(cleaned) and len(cleaned) <= 45:
            current = "tasks"
            continue
        elif skill_rx.match(cleaned) and len(cleaned) <= 45:
            current = "skills"
            continue
        elif re.match(r"^(?:تفاصيل|مكان العمل|الراتب|الدوام|طريقة التقديم|للتواصل|ملاحظات|المصدر|معلومات التوظيف)", cleaned):
            current = None
            continue
        if current in ("tasks", "skills"):
            m_bullet = re.match(r"^[\s*•🔹▪️▫️🔻👇👉✔️✅\-]+\s*(.+)$", line)
            item = m_bullet.group(1).strip() if m_bullet else cleaned
            if (m_bullet or (12 <= len(item) <= 150 and ":" not in item[:30])) and 5 <= len(item) <= 160:
                if current == "tasks" and len(tasks) < 8:
                    tasks.append(item)
                elif current == "skills" and len(skills) < 8:
                    skills.append(item)
    return tasks, skills


def resolve_employer(emp: str | None, src: str, official: bool, text: str, hint: str | None = None) -> str | None:
    if emp and len(emp) >= 3 and not _PORTAL_EMPLOYER.search(F(emp)):
        return emp
    if src in _SOURCE_DEFAULT_EMPLOYER:
        return _SOURCE_DEFAULT_EMPLOYER[src]
    return None


def _resolve_target_country(text: str, links: list[str] | None, country: str | None) -> str:
    """Route channel posts cleanly between Syria ('sy') and Turkey ('tr')."""
    if country == "tr":
        return "tr"
    if country != "mixed":
        return "sy"
    sy_cities = tx.cities_from_text(text)
    tr_cities = tx.tr_cities_from_text(text)
    if tr_cities and not sy_cities:
        return "tr"
    if sy_cities and not tr_cities:
        return "sy"
    mentions_sy = bool(re.search(r"\bسوريا\b|\bsyria\b|الشمال السوري|دمشق|حلب|إدلب|ادلب", text, re.I))
    mentions_tr = bool(re.search(r"\bتركيا\b|\bturkey\b|türkiye", text, re.I))
    if mentions_tr and not mentions_sy:
        return "tr"
    if sy_cities or mentions_sy:
        return "sy"
    if tx.phones_tr(" ".join([text] + (links or []))) and not tx.phones(text):
        return "tr"
    return "sy"


def extract(text: str, source: str, post_url: str, posted_at: str | None = None, default_kind: str = "formal",
            links: list[str] | None = None, country: str | None = None, sector: str | None = None) -> list[Job]:
    """All the jobs in one channel post. Returns [] for posts that are not job ads."""
    if not text or len(re.sub(r"https?://\S+|\s+", "", text)) < 15:
        return []
    target_country = _resolve_target_country(text, links, country)
    # «نبحث عن :» or «في وظيفة:» with the title as a hashtag on the same or next line («#مشرف_تعبئة_وتغليف») → one line
    text = re.sub(r"#فرص[ةه]_عمل\s*[–\-:|]\s*#(\S+)",
                  lambda m: f"فرصة عمل - {m.group(1).replace('_', ' ')}", text or "")
    text = re.sub(r"((?:نبحث|تبحث|يبحث)\s+عن|في\s+وظيف[ةه]|بمسمى|مطلوب|يلزمنا)\s*[:：]?\s*(?:(?:\n\s*)+[^\w\n#]{0,4}|\s+)#(\S+)",
                  lambda m: f"\n{m.group(1)} {m.group(2).replace('_', ' ')}", text or "")
    if target_country == "sy":
        if text and _ABROAD.search(F(text)) and not tx.cities_from_text(text):
            return []  # jobs in Turkey and elsewhere: this branch is for work inside Syria
        tr_check_hay = re.sub(r"https?://(?!(?:wa\.me|api\.whatsapp\.com))\S+", " ", " ".join([text] + (links or [])))
        if text and _TR_PHONE_IN_TEXT.search(tr_check_hay) and not (
            tx.phones(text)
            or any(re.search(r"963\s*9\d{8}", l) for l in (links or []))
            or (tx.cities_from_text(text) and (
                any(da._good_email(e) for e in tx.emails(text))
                or any(da.is_direct_form_or_ats(u) for u in (tx.urls(text) + (links or [])))
            ))
        ):
            return []  # skip ads whose only contact number is Turkish (+90)
    else:
        if is_tr_citizenship_only(text):
            return []
        if tx.cities_from_text(text) and not tx.tr_cities_from_text(text):
            return []
    if not is_job_post(text):
        return []
    spam = is_spam(text)
    if target_country == "tr" and spam:
        return []
    text = re.sub("[\u064B-\u0652\u0640]", "", text)  # harakat and tatweel: «تٌعلن» → «تعلن», «مطوّر» → «مطور»
    lines = _clean_lines(text)
    body = "\n".join(lines)
    if not body or _NOT_JOB.search(F(body)):
        return []
    if _SOFT_NOT_JOB.search(F(body)) and not _EXPLICIT_VACANCY_HDR.search(F(body)):
        return []
    city_text = "\n".join(l for l in text.splitlines() if not _NOISE_LINE.search(F(l)))
    explicit_cities = tx.extract_explicit_location_cities(city_text, target_country)
    if target_country == "tr":
        cities = explicit_cities or tx.tr_cities_from_text(city_text) or (["غازي عنتاب"] if source == "gaziantepis" else ["عدة ولايات في تركيا"])
    else:
        cities = explicit_cities or tx.cities_from_text(city_text)
    cleaned_extra_links = [da.clean_url(l) for l in (links or []) if l]
    all_links = list(dict.fromkeys(tx.urls(text) + [u for u in cleaned_extra_links if u.startswith(("http://", "https://"))]))
    gov_links = da.extract_gov_links(text, extra_links=cleaned_extra_links)
    details_url = da.strip_tracking(str(gov_links["details_url"])) if gov_links["details_url"] else None
    hiring_details_set = set(gov_links["all_details_urls"] or ())
    valid_links = [u for u in all_links if _is_valid_apply_url(u) and u not in hiring_details_set]
    valid_links.sort(key=lambda u: 0 if da.is_direct_form_or_ats(u) else (1 if da.is_shortener(u) else 2))
    best_gov_apply = str(gov_links["best_apply_url"]) if gov_links["best_apply_url"] else None
    if best_gov_apply and _is_valid_apply_url(best_gov_apply):
        apply_url = da.strip_tracking(best_gov_apply) or None
    else:
        apply_url = da.strip_tracking(valid_links[0]) if valid_links else None
    if details_url and details_url == apply_url:
        details_url = None
    phones = tx.phones_tr(body) if target_country == "tr" else tx.phones(body)
    for u in all_links:
        for p in da.extract_phone_from_wa(u, country=target_country):
            if p not in phones:
                phones.append(p)
    phones.sort()
    raw_emails = tx.emails(body)
    if raw_emails and all(re.search(r"tenders?@", e, re.I) for e in raw_emails) and not apply_url and not phones:
        return []
    emails = [e for e in raw_emails if da._good_email(e)]
    if tx.is_foreign_non_target(text, apply_url, emails[0] if emails else None, has_local_city=bool(cities and cities != ["عدة محافظات"] and cities != ["عدة ولايات في تركيا"])):
        return []
    deadline = parse_deadline(body, posted_at)
    tr_pair = re.search(r"💼\s*([^:\n]{2,70}):\s*([^\n📍⏰🔗]{4,110})", text) if target_country == "tr" else None
    if tr_pair and (
        re.search(F(r"مزايا|مميزات|شروط|متطلبات|تفاصيل|ملاحظ|ساعات|اوقات|عنوان|موقع|راتب|طبيعه|المهام"), F(tr_pair.group(1)))
        or not _good_title(_clean_title(tr_pair.group(2)))
    ):
        tr_pair = None
    employer = tr_pair.group(1).strip() if tr_pair else _employer(body)
    official = False if target_country == "tr" else (
        bool(_MOAD_LINK.search(" ".join(all_links)))
        or any(".gov.sy" in u.lower() for u in all_links)
        or bool(employer and _OFFICIAL.search(F(employer)) and "منظمات" not in F(employer))
    )
    employer = resolve_employer(employer, source, official, body, hint=sector)
    tasks, skills = _extract_tasks_and_skills(body)
    base = dict(source=source, post_url=post_url, posted_at=posted_at, employer=employer, deadline=deadline,
                apply_url=apply_url, details_url=details_url, phones=phones, whatsapp=tx.whatsapp_only(body) and bool(phones),
                email=emails[0] if emails else None, spam=spam, official=official, tasks=tasks, skills=skills, raw=text[:2500])

    items, header = _list_items(lines)
    if len(items) < 2 and len(ords := _ordinal_items(lines)) >= 2:
        items = ords
    strong = bool(_STRONG.search(body))
    if len(items) < 2 and len(needs := _all_needs(body)) >= 2:
        items = [(t, None) for t in needs]
    item_urls: list[str | None] = []
    for finder in (_link_items, _pin_items) + (() if strong else (_bullet_block,)):
        if len(items) >= 2:
            break
        if finder is _link_items:
            found_urls = _link_items_with_urls(lines)
            if len(found_urls) >= 2:
                items = [(t, None) for t, _ in found_urls]
                item_urls = [u for _, u in found_urls]
        elif len(found := finder(lines)) >= 2:
            items = found
    if items and not item_urls and (link_pairs := _link_items_with_urls(lines)):
        url_map = {F(t): u for t, u in link_pairs if u}
        if url_map:
            item_urls = [url_map.get(F(it[0])) or url_map.get(F(_split_item_city(it[0], target_country)[0])) for it in items]
    if not items and _MOAD_LINK.search(text) and not strong:
        return []  # a re-post of a ministry announcement: the ministry adapter reads the real cards
    titles: list[tuple[str, str, list[str], str | None, str | None]] = []  # (title, source text, cities, grade, item_url)
    single = len(items) < 2
    if not single:
        doctors = bool(re.search(F(r"اطباء|الاطباء"), F(header)))
        for idx, (item, grade) in enumerate(items):
            title, item_cities = _split_item_city(item, target_country)
            if doctors and not re.search(F(r"طبيب|اطباء"), F(title)):
                title = "طبيب " + title  # «أطباء من الاختصاصات التالية: الجراحة العامة»
            i_url = item_urls[idx] if idx < len(item_urls) else None
            titles.append((title, item, item_cities, grade, i_url))
        if not any(_good_title(t) and not _only_places(t) and t not in tx.CITIES and t not in tx.TR_CITIES for t, *_ in titles):
            titles.clear()
            single = True
    if single:
        if tr_pair:
            title = _clean_title(tr_pair.group(2))
            src = tr_pair.group(0)
        else:
            title, src = _single_title(body, lines)
        grade = None
        i_url = None
        g = _GRADE_LINE.match(F(title))
        if g:
            grade = _GRADE_WORD[g.group(1)]
            title = _clean_title(re.sub(r"^\s*(?:ال)?فئ[ةه]\s*\S+\s*[:\-–]?\s*", "", title))
        if not title:
            if items:
                title, grade = items[0]
                src = title
            elif link_one := _link_items_with_urls(lines):
                title, i_url = link_one[0]
                src = title
        if title:
            title, item_cities = _split_item_city(title, target_country)
            titles.append((title, src, item_cities, grade, i_url))

    jobs = []
    tags = tx.job_tags(body)
    for title, title_text, item_cities, grade, i_url in titles:
        if not _good_title(title) or _only_places(title) or title in tx.CITIES or title in tx.TR_CITIES:
            continue
        if single:
            job_cities = item_cities or explicit_cities or cities
        else:
            t_cs = [] if item_cities else (tx.tr_cities_from_text(title_text) if target_country == "tr" else tx.cities_from_text(title_text))
            if item_cities:
                job_cities = item_cities
            elif t_cs:
                job_cities = t_cs
            elif "غير محدد" in title_text:
                job_cities = ["عدة ولايات في تركيا"] if target_country == "tr" else ["عدة محافظات"]
            elif "المقر الرئيسي" in title_text and employer and "الصندوق السيادي" in employer:
                job_cities = ["دمشق"]
            else:
                job_cities = explicit_cities or cities
        per_apply = i_url or apply_url
        job_kw = {**base, "apply_url": per_apply, "phones": list(base["phones"])}
        job = Job(title=title, cities=job_cities, tags=tags, country=target_country, **job_kw)
        _fill_details(job, body, title_text, single, grade, header)
        if source == "syrianjobs" and not job.education:
            job.education, job.education_from = "none", "source"
        quick = not deadline and not (per_apply and not per_apply.startswith("https://wa.me")) and bool(phones)
        job.kind = "quick" if (default_kind == "quick" or quick) else "formal"
        job.sector = tx.infer_sector(job.official, job.field, job.employer, body, hint=sector, country=target_country, source=source)
        job.apply_types = tx.infer_apply_types(job.apply_url, job.phones, job.whatsapp, job.email)
        da.resolve_job_apply(job, client=None, strict=False)
        jobs.append(job)
    return jobs


def expires_on(job: Job, quick_days: int | None = None, formal_days: int | None = None) -> str:
    """When a job stops showing: its deadline, or a fixed time after posting when there is none."""
    if job.deadline:
        return job.deadline
    q_days = config.QUICK_DAYS if quick_days is None else quick_days
    f_days = config.FORMAL_DAYS if formal_days is None else formal_days
    start = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00")).date() if job.posted_at else date.today()
    return (start + timedelta(days=q_days if job.kind == "quick" else f_days)).isoformat()
