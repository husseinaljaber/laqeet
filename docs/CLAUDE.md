# Project brief: لقيت (Laqeet), a job platform for Syria

You are taking over this project from an earlier chat with Claude. Everything you need is in this file and in the folders next to it. Read the whole file before you start.

## The owner

- **Hussein Aljaber** (GitHub: `husseinaljaber`, email: husinaljber98@gmail.com). Syrian, lives in Istanbul. Computer Engineering graduate (FSMVU), MSc student in AI and Data Engineering. Knows Python, SQL/PostgreSQL, Flutter/Dart, Power BI.
- **Talk to him in Levantine (Syrian) Arabic.** Keep replies short and practical. He writes quickly and sometimes uses voice dictation, so read past typos.
- He is not competing with anyone. The goal is to help Syrians find work faster.

## The product

One website (and later a mobile app) that gathers job ads from four public sources and shows each person the jobs that fit **their education level**:

| Education level (code) | Arabic | Typical jobs |
|---|---|---|
| `phd`, `master` | دكتوراه، ماجستير | research, leadership, university |
| `bachelor` | جامعة | engineers, doctors, accountants, NGO officers, government grade 1 |
| `institute` | معهد | technicians, nurses, data entry, government grade 2 |
| `secondary` | بكالوريا | clerks, cashiers, sales, government grade 3 |
| `basic` | تعليم أساسي | government grade 4, workers |
| `none` | بدون شهادة | restaurants, cafés, clothing and dairy shops, factories, sewing/knitting machines (ماكينة حبكة), guards, delivery, cleaning, government grade 5 |

**Name:** the suggested name is **«لقيت» (Laqeet)**, as in «لقيت شغل» ("I found a job"). It's short, Levantine, and easy to say. Other options: «شُغلة» (Shughla) and «بلّش» (Ballesh). A web search found no Syrian job platform using these names, but nobody has checked domain availability or trademarks yet. Keep the name in one config value so it is easy to change.

**He also wants a mobile app later.** Build the site as an installable PWA first (manifest + service worker). Later, wrap it for the Play Store (Trusted Web Activity or Capacitor), or build a Flutter app on the same API, since he knows Flutter.

## Sources (all public, no official APIs exist)

Read each channel through its public web preview: `https://t.me/s/<channel>`. Page backwards with `?before=<post id>`. No Telegram account or bot is needed for reading. Posts are `div.tgme_widget_message[data-post]`, text in `.tgme_widget_message_text`, time in `time[datetime]`. Code for this already exists in `tech-jobs-sy/app/telegram.py`.

1. **Ministry of Administrative Development (وزارة التنمية الإدارية), the official government source:** https://t.me/s/SyMOAD
   - Each vacancy post has a details link `https://bit.ly/hiringYYYYMMDD` and an apply link `https://bit.ly/applyingYYYYMMDD` (same suffix), a deadline («آخر موعد لاستقبال طلبات التوظيف: dd/mm/yyyy»), and the governorates.
   - `bit.ly/hiring…` redirects to a **Google Drive PDF** of job cards («بطاقات وصف»). Each card is a table: title, governorate(s), grade («الفئة الأولى…الخامسة»), qualification, general and precise specialty, years of experience, preferred age, then «المهام الرئيسية» and «المهارات المفضلة».
   - **The PDF text comes out messy:** often in visual (reversed) order, with presentation-form glyphs and dropped letters. `tech-jobs-sy/app/arabic.py` and `cards.py` handle this, but **they have not been tested on real PDFs yet** (the earlier sandbox could not reach Telegram or Drive). Test them on real files first; `python -m app.inspect_pdf file.pdf --text` helps.
   - **Grade maps to education:** grade 1 = bachelor, 2 = institute, 3 = secondary, 4 = basic, 5 = none (reads and writes). Leadership posts (مدير، رئيس دائرة/شعبة/قسم) are grade 1.
   - Since late September 2026 some posts link to the new government jobs portal instead of a PDF: `https://moad.gov.sy/recruitment/jobs?group=<id>` (all jobs: `https://moad.gov.sy/recruitment/jobs`; mobile app: `https://bit.ly/moad_app`). The portal is a JavaScript app: it asks you to pick governorates first, then loads data from a backend. **Open it in a browser, watch DevTools → Network → Fetch/XHR, find the JSON endpoint, and write an adapter.** This is probably the best source of structured government data.
   - Official site: https://moad.gov.sy. SANA (https://sana.sy) re-publishes the ministry's announcements and is useful for checking them.
2. **وظفني || SYR:** https://t.me/s/wazafne175 (~34K subscribers). Re-posts ministry, NGO, and company jobs, plus trainings and tenders (skip those). Formats vary: some posts have full details, some only a title and a Google Form link, and many share a footer pointing to the channel and WhatsApp.
3. **منصة التوظيف (Tawzeef Platform):** https://t.me/s/tawzeef_platform (~8K subscribers). List posts: a header «تُعلن [جهة] عن توفر شواغر … وفق الاختصاصات التالية:», then one title per «▪️» line (sometimes «title - city»), then «يتم قبول طلبات التوظيف حتى تاريخ : yyyy/mm/dd», then an apply link `https://tawzeefportal.com/companies/<id>` (e.g. 57, 34, 166, 23, 17, 12, 94, 276, 279, 93). The channel also sells a CV-writing service through WhatsApp; ignore those posts. Its website is https://tawzeefportal.com.
4. **فرص عمل في سوريا:** https://t.me/s/syrianjobs2 (~36K subscribers). Quick, no-degree jobs: a city hashtag (#دمشق), «مطلوب …», a few details (shift, hours, age, gender), and a phone number, often «واتس فقط». There is almost never a salary or a deadline. **Some posts are scams or ads** (Binance sign-up bonuses, subscription deals) and must be filtered out.

Other Syrian job channels and sites to consider later: https://t.me/s/JobsvacanciesinSyriaandTurkey, https://syriaworks.com, Dubarah (an older Syrian platform), Bayt.com.

**Ethics (he agreed to this approach):**
- Always show the source channel and link the original post.
- Send people to the original apply link.
- Contact the channel admins and offer credit and traffic. They may even send a structured feed.
- Rate-limit requests (≥1 s apart).
- Check the ministry site's terms before going public.
- Phone numbers in ads were published by advertisers for applicants, so a WhatsApp button is fine. Don't collect anything else about them.

## Product requirements (agreed)

### Phase 1: collection engine + new website
- **One source adapter per channel**, all producing the same `Job` shape (see `laqeet/app/models.py`): title, employer, cities, education + how we know it (stated / from title / from grade), field, computer subfield, experience, age range, gender, shift and hours, salary, deadline, kind (`formal` vs `quick`), apply url / phones / WhatsApp / email, details url, official flag, spam flag, source and post url.
- **Extraction:** rule-based by default (`laqeet/app/extract.py`, `taxonomy.py`). It must also work with an optional LLM extractor when `ANTHROPIC_API_KEY` is set: send the post text, get back JSON matching `Job`, and use a small cheap model. Low-confidence results go to a review queue. He hasn't yet decided whether to pay for the API, so the rule-based path must work on its own.
- **Spam and scam filter** (`laqeet/app/spam.py`), plus a «بلّغ عن إعلان» report button. Reported posts get auto-hidden after N reports.
- **Dedupe:** the same job shows up in several channels (e.g. وظفني re-posts the ministry). Merge into one card that lists every source. Fingerprint on normalized title + city + employer + deadline or post week.
- **Expiry:** formal jobs expire at their deadline (or 30 days after posting if none). Quick jobs expire 10 days after posting.
- **Show the last ~6 months**, with updates every 15–30 minutes.
- **Website (Arabic, RTL, mobile-first, light on data for slow Syrian internet):**
  - **First screen asks «شو شهادتك؟»** (the levels above), then specialty for جامعة/معهد, then city. Saved in localStorage.
  - **Results:** jobs the person qualifies for, with a toggle for jobs that need less.
  - **Two modes:** «شغل رسمي» (deadline, requirements, CV) and «شغل سريع» (city first, «جديد اليوم», a big WhatsApp button).
  - **«بتنطبق عليك؟» on every card:** green / amber / red from their education, age and experience.
  - **Experience colors on the card edge:** green ≤2 years, amber 3, red 5+, grey unknown.
  - **Official badge** for government sources.
- **Computer-engineering view:** jobs whose degree is computer / informatics / IT / AI engineering / CS, with subfields (cyber, AI, data, software, networks, digital transformation, support, teaching). This already exists in `tech-jobs-sy` and becomes a specialty inside «جامعة».

### Phase 2: Telegram alert bot
- Onboarding with buttons: education → specialty → city (or several).
- Sends each new matching job once (keep a sent log), with /stop and /settings.
- Run it from the same scheduler as the scraper.

### Phase 3
- **Employers post directly:** a form, then a review queue, then «منشور مباشرة» on approval. The admin page is protected by a token.
- **CV builder:** a profile turned into a clean Arabic CV page that can be printed or saved as PDF. When opened from a job, it highlights the job's requirements.
- **«نبض السوق» (market pulse):** what's most in demand by city × education × field, and how it changes month to month.

## Tech choices (suggested; change if you have a good reason)
- **Backend:** Python 3.12, FastAPI, httpx, BeautifulSoup, PyMuPDF.
- **Database:** SQLite now, with a clear path to Postgres.
- **Frontend:** vanilla JS + CSS (no build step), PWA.
- **Deploy:** Docker Compose (api + scheduler/bot) on a small VPS (~5–10 USD a month) with Cloudflare in front. A static fallback also exists: GitHub Actions runs the scraper and publishes `jobs.json` to GitHub Pages (see `tech-jobs-sy/.github/workflows/update.yml`).
- **Custom domain:** GitHub Pages takes a CNAME to `husseinaljaber.github.io` (or A records 185.199.108–111.153). With a VPS, point the domain at Cloudflare.
- **GitHub:** account `husseinaljaber`. Ask him to create the repo, or create it yourself with his permission. **Never make anything public, push, or deploy without asking him first.**

## Design direction (he liked this)
- **Look:** Arabic RTL. Display font *Reem Kufi*, body font *IBM Plex Sans Arabic* (Google Fonts). Deep green hero (#0D1F18) with a mint accent (#3CC596). Light and dark themes.
- **The opening hook:** a hero with a slowly drifting dot grid, a pulsing «محدّث» live dot, and a headline whose last words rotate (e.g. the field names). Below it, numbers that count up (open now / last 6 months / ≤2 years experience / employers) and a clickable bar chart of what's most in demand.
- **Motion:** cards stagger in, lift on hover, and respect `prefers-reduced-motion`.
- **The current design** is in `tech-jobs-sy/frontend/app.html`. Reuse it, but make the first screen the education picker.

## What exists now (in this folder)

- `tech-jobs-sy/`: a **working** first version for computer-engineering government jobs only. It includes the Telegram reader, ministry announcement parser, bit.ly→Drive downloader, PDF card parser, keyword classifier (96% agreement with the hand labels; tuned on the same data, so expect less on new data), SQLite store, FastAPI API, the designed frontend, the export to `jobs.json`, the GitHub Actions workflow, and tests. To try it: `pip install -r requirements.txt && pytest && python -m app.seed && python tools/build_frontend.py && uvicorn app.api:app`.
- `tech-jobs-sy/data/seed_jobs.json`: **260 hand-checked ministry tech job cards** from 88 vacancy PDFs (March to November 2026), with field, grade, experience (where readable), deadline, and links. Use it as seed data and as a test set.
- `laqeet/`: **the start of the full platform (in progress, not finished).** It has `taxonomy.py` (education levels, cities and aliases, fields, phone / age / experience / salary / gender / shift parsing), `extract.py` (rule extractor), `spam.py`, `models.py`, and `tests/fixtures/posts.py` with example posts in each channel's format (fake phone numbers).
- **Bugs seen on the last run of `laqeet/app/extract.py`. Fix these first:**
  1. List posts pick up decorated non-title lines as jobs (lines starting with 🔹 / ✔️ / ⚠️, e.g. «إعلان عبر بوابة التوظيف», «يتم تقديم طلب التوظيف…»). Only «▪️/•/-» items under the «الاختصاصات التالية / المسميات» header should count.
  2. Single-job posts with requirement bullets (وظفني, e.g. «المتطلبات: - إجازة جامعية… - خبرة 3 سنوات») are treated as lists of jobs. Bullets after «المتطلبات/الشروط/المهام» are requirements, not titles. The title is the line after «حاجتها لـ/إلى / لشغل وظيفة».
  3. Gender is taken from the whole post, so one «مروجة» makes every item female. Read gender per item.
  4. `fold()` deletes «ي», so very short patterns (e.g. «سني») match inside other words. `الإنسانية` was read as health. Use word boundaries and longer patterns.
  5. Field falls back to the whole post's context for list items («فني كهرباء» became sales). Add «كهرباء» to industry, and don't use the post context for list items.
  6. The employer regex is too loose: in «معمل الدوام من 8 الى 4», "the factory" was read as the employer.
  7. Prefix grades in titles («الفئة الرابعة: عامل نظافة») should set education from the grade and be removed from the title.
- **Research done earlier** (useful for «نبض السوق» and as a sanity check): across the ministry's Mar–Nov 2026 announcements, university-level tech roles were mostly data / analysis, networks, software, digital transformation, then cyber (heavily from one May announcement by the National Authority for IT Services), then AI. Most first-category roles ask for **2 years** of experience (age 25–45). Leadership roles ask for 3–7 years. Damascus has most of the roles.
- **Earlier private previews** (claude.ai, only Hussein can open them): the ministry tech-jobs dashboard https://claude.ai/artifact/AjGYX34EfmwG7bsg9qxQmn and the site preview https://claude.ai/artifact/C4tUNtm9n7mF7akCzu6und.

## How to work
1. Fix the extractor bugs above. Then collect **real posts** from the four channels (`python -m ...` scraping `t.me/s/...`), save a sample as test fixtures, and measure extraction quality on ~50 real posts per channel before building on it.
2. Test the ministry PDF parser on 5–10 real PDFs and fix `cards.py`.
3. Build Phase 1 end to end (adapters → DB → API → site → PWA), then Phase 2, then Phase 3. Keep tests green (`pytest`) and add tests for every parser fix.
4. After each phase, show Hussein the result: run it locally, take screenshots, and give a short summary in Levantine Arabic. Ask him before anything goes public.
5. Keep a `README.md` (in Arabic) with how to run, deploy, and connect a domain.
