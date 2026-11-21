"""Settings in one place. Everything can be overridden with an environment variable of the same name."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SITE_NAME = os.getenv("SITE_NAME", "لقيت")          # change the platform's name here only
SITE_NAME_EN = os.getenv("SITE_NAME_EN", "Laqeet")
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "laqeet.db"))
PDF_DIR = Path(os.getenv("PDF_DIR", ROOT / "data" / "pdfs"))
CHANNELS_PATH = Path(os.getenv("CHANNELS_PATH", ROOT / "data" / "channels.json"))
FRONTEND_DIR = ROOT / "frontend"

# Public Telegram channels we read, loaded from data/channels.json so scaling to 300-400 sources needs no code edits.
_DEFAULT_CHANNELS = {
    "SyMOAD": {"source": "moad", "label": "وزارة التنمية الإدارية", "kind": "formal", "official": True, "country": "sy", "sector": "gov"},
    "wazafne175": {"source": "wazafne", "label": "وظفني || SYR", "kind": "formal", "official": False, "country": "sy"},
    "tawzeef_platform": {"source": "tawzeef", "label": "منصة التوظيف", "kind": "formal", "official": False, "country": "sy"},
    "syrianjobs2": {"source": "syrianjobs", "label": "فرص عمل في سوريا", "kind": "quick", "official": False, "country": "sy", "sector": "private"},
}


def load_channels() -> dict[str, dict]:
    if CHANNELS_PATH.exists():
        try:
            data = json.loads(CHANNELS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return _DEFAULT_CHANNELS


CHANNELS = load_channels()
SOURCE_LABEL = {v["source"]: v["label"] for v in CHANNELS.values()} | {
    "employer": "صاحب العمل مباشرة",
    "reliefweb": "منصة ريليف ويب (منظمات وأمم متحدة)",
    "sema": "الرابطة الطبية للمغتربين (SEMA)",
    "ataa": "جمعية عطاء للإغاثة الإنسانية",
    "ida": "رابطة الأطباء المستقلين (IDA)",
    "worklink": "منصة ورك لينك (WorkLink Syria)",
    "ngosquare": "منصة ساحة المنظمات (NGO Square)",
    "sarc": "الهلال الأحمر العربي السوري (SARC)",
    "jobsyria": "منصة JobSyria",
    "nsjobs_web": "منصة NS Jobs",
    "jobinsyria": "منصة Job In Syria",
    "srd": "منظمة سوريا للإغاثة والتنمية (SRD)",
    "sened": "منظمة سند (Sened)",
    "bahar": "منظمة بهار (Bahar)",
    "ngossyria": "بوابة منظمات سوريا (NGOs Syria)",
    "un_syria": "بوابة الأمم المتحدة في سوريا (UN Syria)",
    "unjobnet_sy": "بوابة UNjobnet (وظائف الأمم المتحدة)",
    "unjobnet_tr": "بوابة UNjobnet تركيا (منظمات وأمم متحدة)",
    "impactpool_sy": "منظمات دولية وأمم متحدة (Impactpool)",
    "impactpool_tr": "منظمات دولية وأمم متحدة في تركيا (Impactpool)",
    "shafak": "منظمة شفق الإنسانية (Shafak)",
    "drc": "المجلس الدنماركي للاجئين (DRC)",
    "acted": "منظمة أكتيد الدولية (ACTED)",
    "wfp": "برنامج الأغذية العالمي (WFP)",
    "syria_jobs": "موقع وظائف سوريا (Syria Jobs)",
    "ssf_gov": "الصندوق السيادي السوري (بوابة التوظيف الرسمية)",
    "jobsintr_web": "موقع فرص عمل في تركيا",
    "adwhit_web": "موقع أدويت للوظائف",
    "salamjobs": "منصة سلام جوبز",
    "wazifturk": "منصة وظيفتورك",
    "abilan_tr": "بوابة وظائف المنظمات في تركيا (AB-İlan)",
    "powerholding": "مجموعة باور إنترناشيونال القابضة (Power Holding)",
    "jobsyria_net": "شبكة وظائف سوريا (JobSyria.net)",
    "ihsanrd": "منظمة إحسان للإغاثة والتنمية (IhsanRD)",
    "medglobal": "منظمة ميدغلوبال (MedGlobal Syria)",
    "ahf": "مؤسسة الأمين للمساندة الإنسانية (AHF)",
    "hro": "منظمة إحياء الأمل (Hope Revival)",
    "salam_sy": "منظمة سلام سوريا (Salam)",
    "ghiras": "منظمة غراس الخير الإنسانية",
    "un_careers": "الأمم المتحدة (UN Careers)",
    "npa": "المساعدات الشعبية النرويجية (NPA)",
    "wvi": "منظمة الرؤية العالمية (World Vision)",
    "unhcr": "المفوضية السامية لشؤون اللاجئين (UNHCR)",
    "nrc": "المجلس النرويجي للاجئين (NRC)",
    "undp": "برنامج الأمم المتحدة الإنمائي (UNDP)",
    "relief_intl": "منظمة الإغاثة الدولية (Relief International)",
    "mag_intl": "المجموعة الاستشارية للألغام (MAG)",
    "warchild": "منظمة وور تشايلد (War Child)",
    "goal_syria": "منظمة غول الدولية (GOAL Syria)",
    "dunya_doktorlari": "أطباء العالم (Dünya Doktorları / MDM-T)",
    "hayata_destek": "جمعية دعم الحياة (Hayata Destek)",
    "httpOrganizationalExamTemplates": "بنك نماذج امتحانات المنظمات والوظائف",
}

OFFICIAL_GOV_SOURCES = frozenset({
    "moad", "SyMOAD", "moi", "mohe", "moe", "moh", "moeai", "mof", "mocait",
    "moe_energy", "moa", "mopwah", "damascusgovrnorat", "sana", "hiast",
    "damascus_univ", "svu", "ssf_gov",
})
OFFICIAL_NGO_SOURCES = frozenset({
    "sarc", "syredcrescent", "sema", "ataa", "ida", "srd", "sened", "bahar",
    "shafak", "drc", "acted", "wfp", "violet", "org_vio", "takaful",
    "takafulalsham", "un_syria", "reliefweb", "unjobnet_sy", "unjobnet_tr",
    "impactpool_sy", "impactpool_tr", "ihsanrd", "medglobal", "ahf", "hro",
    "salam_sy", "ghiras", "un_careers", "npa", "wvi", "unhcr", "nrc", "undp",
    "relief_intl", "mag_intl", "warchild", "goal_syria", "dunya_doktorlari",
    "hayata_destek",
})

HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", 183))      # show the last ~6 months
QUICK_DAYS = int(os.getenv("QUICK_DAYS", 21))           # quick jobs expire 21 days after posting
FORMAL_DAYS = int(os.getenv("FORMAL_DAYS", 30))         # formal jobs without a deadline: 30 days
UPDATE_MINUTES = int(os.getenv("UPDATE_MINUTES", 20))   # collector interval (every 20 minutes)
REQUEST_GAP = float(os.getenv("REQUEST_GAP", 1.2))      # seconds between requests to one host (≥1 s, be polite)
REPORTS_TO_HIDE = int(os.getenv("REPORTS_TO_HIDE", 3))  # «بلّغ عن إعلان»: hide after this many reports
REVIEW_BELOW = float(os.getenv("REVIEW_BELOW", 0.5))    # confidence under this goes to the review queue

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")              # protects /admin; empty = admin disabled
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # optional LLM extractor
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")
