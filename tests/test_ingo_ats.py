"""Offline mocked unit tests for Phase 3 INGO/UN ATS connectors, portals, and direct-apply gates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, direct_apply as da
from app.sources import ingo_ats, web_portals


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_direct_apply_new_ats_and_shorteners():
    assert da.is_shortener("https://syr.now/e73u7c")
    assert da.is_shortener("https://acr.ps/hBy2sDH")
    assert da.is_shortener("https://molh.am/JucTvK")
    assert da.is_shortener("https://sl1nk.com/abc123")
    assert da.is_strict_portal_wrapper("https://jobsyria.net/وظائف/محاسب/حماة/12")
    assert da.is_disallowed_offline_url("https://career.ihsanrd.org") is False

    for u in [
        "https://web.inform.unicef.org/x/akmcdLkN",
        "https://cloud.deirezzor.gov.sy/apps/formvox/public/9818/abc",
        "https://erp.nasaemkhair.org/Apps/ApplyJob/0d125e88",
        "https://apps.muzungo.com/jobs/apply/44702c99",
        "https://war-child-alliance.jobs.personio.de/job/2696186",
        "https://sy.medglobal.org/jobs/apply/data-entry-operator-homs-1418",
        "https://sy.medglobal.org/ar/jobs/apply/data-entry-operator-lattakia-1419",
        "https://rise.yallago.net/careers/5f6614d4-1372-459d-916d-296aa3e6b29b",
        "https://jobs.hro.ong/Jobs/Apply",
        "https://jobs.oxfam.org.uk/jobs/vacancy/supply-officer/25267/description/",
        "https://careers.powerholding-intl.com/job/Syria-Site-Engineer/1357486657/",
        "https://cv.inoportal.com/DunyaDoktorlari/Basvuru/531/2",
    ]:
        assert da.is_direct_form_or_ats(u), f"Expected direct ATS/form: {u}"

    assert not da._good_email("procurement@hope-revival.ngo")
    assert not da._good_email("tenders@org.org")
    assert da._good_email("hr@example-ngo.org")


def test_workday_board_routing_and_30_plus_days():
    seen_payloads = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode())
        seen_payloads.append(body)
        kw = body.get("searchText")
        assert body.get("limit") == 20
        if "folkehjelp" in str(req.url) and kw == "Syria":
            return httpx.Response(200, json={
                "total": 2,
                "jobPostings": [
                    {
                        "title": "Medic",
                        "externalPath": "/job/Syria-Homs/Medic_JR101579",
                        "locationsText": "Syria, Homs",
                        "postedOn": "Posted 2 Days Ago",
                    },
                    {
                        "title": "Programme Assistant",
                        "externalPath": "/job/Syria-Damascus/Programme-Assistant_JR101489",
                        "locationsText": "Syria, Damascus",
                        "postedOn": "Posted 30+ Days Ago",
                    },
                    {
                        "title": "Finance Coordinator",
                        "externalPath": "/job/Jordan-Amman/Finance_JR999",
                        "locationsText": "Jordan, Amman",
                        "postedOn": "Posted Today",
                    },
                ],
            })
        if "worldvision" in str(req.url):
            # WVI returns Syria jobs even under Turkiye search; must never misclassify Syria as Turkey!
            return httpx.Response(200, json={
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Finance Officer",
                        "externalPath": "/job/Dana-Office-Syria/Finance-Officer_JR50651",
                        "locationsText": "Dana Office, Syria",
                        "postedOn": "Posted 30+ Days Ago",
                    },
                ],
            })
        if "unhcr" in str(req.url) and kw == "Turkiye":
            return httpx.Response(200, json={
                "total": 2,
                "jobPostings": [
                    {
                        "title": "Senior Legal Associate",
                        "externalPath": "/job/Ankara-Turkiye/Senior-Legal-Associate_JR2669056-1",
                        "locationsText": "Ankara, Turkiye",
                        "postedOn": "Posted 8 Days Ago",
                    },
                    {
                        "title": "Field Coordinator (Only for Turkish Citizens)",
                        "externalPath": "/job/Istanbul-Turkiye/Field_JR1",
                        "locationsText": "Istanbul, Turkiye",
                        "postedOn": "Posted 1 Day Ago",
                    },
                ],
            })
        return httpx.Response(200, json={"total": 0, "jobPostings": []})

    with _mock_client(handler) as cli:
        npa = ingo_ats.scrape_workday_board(
            cli,
            source="npa",
            employer="NPA",
            host="folkehjelp.wd103.myworkdayjobs.com",
            tenant="folkehjelp",
            site="NPA_Career",
        )
        assert [j.title for j in npa] == ["Medic", "Programme Assistant"]
        assert npa[0].cities == ["حمص"]
        assert npa[1].cities == ["دمشق"]
        assert all(j.country == "sy" for j in npa)

        wvi = ingo_ats.scrape_workday_board(
            cli,
            source="wvi",
            employer="WVI",
            host="worldvision.wd1.myworkdayjobs.com",
            tenant="worldvision",
            site="WorldVisionInternational",
        )
        assert len(wvi) == 1
        assert wvi[0].country == "sy"

        unhcr = ingo_ats.scrape_workday_board(
            cli,
            source="unhcr",
            employer="UNHCR",
            host="unhcr.wd3.myworkdayjobs.com",
            tenant="unhcr",
            site="External",
        )
        assert [j.title for j in unhcr] == ["Senior Legal Associate"]
        assert unhcr[0].country == "tr"
        assert unhcr[0].cities == ["أنقرة"]


def test_oracle_hcm_strict_location_gate():
    def handler(req: httpx.Request) -> httpx.Response:
        if "Syria" in str(req.url):
            return httpx.Response(200, json={
                "items": [{
                    "TotalJobsCount": 2,
                    "requisitionList": [
                        {
                            "Id": "22134",
                            "Title": "Early Warning System Expert",
                            "PrimaryLocation": "Damascus, Syrian Arab Republic",
                            "secondaryLocations": [],
                            "PostedDate": "2026-11-20",
                        },
                        {
                            "Id": "22143",
                            "Title": "Protection Specialist",
                            "PrimaryLocation": "Ramallah, Palestine, State of",
                            "secondaryLocations": [],
                            "PostedDate": "2026-11-18",
                        },
                    ],
                }]
            })
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 0, "requisitionList": []}]})

    with _mock_client(handler) as cli:
        jobs = ingo_ats.scrape_oracle_hcm_board(
            cli,
            source="nrc",
            employer="NRC",
            host="ekum.fa.em2.oraclecloud.com",
            site="CX_2019",
        )
    assert len(jobs) == 1
    assert jobs[0].cities == ["دمشق"]
    assert jobs[0].apply_url == "https://ekum.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2019/job/22134"


def test_un_careers_filters_internal_intern_and_geneva():
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode())
        if body["filterConfig"]["keyword"] == "Syria":
            return httpx.Response(200, json={
                "data": {
                    "list": [
                        {
                            "jobId": "288233",
                            "jobTitle": "Partnership & Resource Mobilization Officer",
                            "dutyStation": [{"description": "FO Syria"}],
                            "startDate": "2026-11-15T00:00:00.000Z",
                            "endDate": "2026-12-03T04:59:59.000Z",
                        },
                        {
                            "jobId": "286919",
                            "jobTitle": "Senior Officer (For Internal Candidates Only – Syria)",
                            "dutyStation": [{"description": "FO Syria"}],
                            "startDate": "2026-11-15T00:00:00.000Z",
                            "endDate": "2026-12-01T04:59:59.000Z",
                        },
                        {
                            "jobId": "288425",
                            "jobTitle": "Legal Affairs Internship",
                            "dutyStation": [{"description": "GENEVA"}],
                            "startDate": "2026-11-15T00:00:00.000Z",
                            "endDate": "2026-12-01T04:59:59.000Z",
                        },
                    ]
                }
            })
        return httpx.Response(200, json={"data": {"list": []}})

    with _mock_client(handler) as cli:
        jobs = ingo_ats.scrape_un_careers(cli)
    assert len(jobs) == 1
    assert jobs[0].source == "un_careers"
    assert jobs[0].apply_url == "https://careers.un.org/jobSearchDescription/288233?language=en"


def test_bamboohr_workable_personio_greenhouse():
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "bamboohr.com" in u:
            return httpx.Response(200, json={
                "result": [
                    {"id": "531", "jobOpeningName": "Enumerator - Aleppo", "location": {"city": "Idleb", "state": "Idleb"}},
                    {"id": "999", "jobOpeningName": "Finance Manager - Yemen", "location": {"city": "Aden", "state": "YE"}},
                ]
            })
        if "workable.com" in u:
            return httpx.Response(200, json={
                "results": [
                    {"shortcode": "88788758D7", "title": "Recruitment Administrator", "location": {"countryCode": "GB", "city": "Manchester"}, "published": "2026-11-20T00:00:00.000Z"},
                    {"shortcode": "0189894210", "title": "Community Liaison Manager - Syria", "location": {"countryCode": "SY", "city": "Raqqa"}, "published": "2026-11-18T00:00:00.000Z"},
                ]
            })
        if "personio.de" in u:
            xml = """<workzag-jobs>
              <position><id>2696186</id><name>Head of Programme</name><office>Syria - Damascus office</office><createdAt>2026-11-10T09:00:00+00:00</createdAt></position>
              <position><id>1111111</id><name>Developer</name><office>Amsterdam</office><createdAt>2026-11-10T09:00:00+00:00</createdAt></position>
            </workzag-jobs>"""
            return httpx.Response(200, text=xml, headers={"Content-Type": "text/xml"})
        if "greenhouse.io" in u:
            return httpx.Response(200, json={
                "jobs": [
                    {
                        "title": "WASH Officer",
                        "absolute_url": "https://job-boards.greenhouse.io/goalsyria/jobs/8401456",
                        "location": {"name": "Idlib, Syria"},
                        "updated_at": "2026-11-15T10:00:00-00:00",
                        "content": "<p>Apply now for Idlib field work.</p>",
                    }
                ]
            })
        return httpx.Response(404)

    with _mock_client(handler) as cli:
        ri = ingo_ats.scrape_relief_international(cli)
        mag = ingo_ats.scrape_mag_international(cli)
        wc = ingo_ats.scrape_warchild(cli)
        goal = ingo_ats.scrape_goal_syria(cli)

    assert len(ri) == 1 and ri[0].apply_url == "https://ri.bamboohr.com/careers/531"
    assert len(mag) == 1 and mag[0].cities == ["الرقة"]
    assert len(wc) == 1 and wc[0].cities == ["دمشق"]
    assert len(goal) == 1 and goal[0].cities == ["إدلب"]


def test_inoportal_ihsan_medglobal_and_wp_ngo():
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("/DunyaDoktorlari/ilanlar"):
            return httpx.Response(200, text="""
              <a href="/DunyaDoktorlari/IlanDetay/531/2">HR Specialist</a>
              <a href="/DunyaDoktorlari/IlanDetay/525/2">IT Officer</a>
              <a href="/DunyaDoktorlari/IlanDetay/529/2">TC Citizen Specialist</a>
            """)
        if "/IlanDetay/531/2" in u:
            return httpx.Response(200, text="""
              <h1>HR Specialist - DÜNYA DOKTORLARI</h1>
              <p>Location: Damascus, Syria</p>
              <a href="/DunyaDoktorlari/Basvuru/531/2">Apply</a>
            """)
        if "/IlanDetay/525/2" in u:
            return httpx.Response(200, text="""
              <h1>IT Officer - DÜNYA DOKTORLARI</h1>
              <p>Location: İstanbul, Türkiye</p>
              <a href="/DunyaDoktorlari/Basvuru/525/2">Apply</a>
            """)
        if "/IlanDetay/529/2" in u:
            return httpx.Response(200, text="""
              <h1>Field Officer - DÜNYA DOKTORLARI</h1>
              <p>Location: İstanbul, Türkiye. T.C. vatandaşı olma şartı aranmaktadır.</p>
              <a href="/DunyaDoktorlari/Basvuru/529/2">Apply</a>
            """)
        if "ihsanrd.org/jm-ajax" in u:
            return httpx.Response(200, json={
                "html": """<ul class="job_listings">
                  <li class="job_listing"><a href="https://career.ihsanrd.org/job/nurse-1/"><h3>ممرضة – السفيرة -2611012</h3><div class="location">السفيرة</div><span class="job-type">Full Time</span><span class="date">Posted 3 days ago | Closes: November 30, 2026</span></a></li>
                  <li class="job_listing"><a href="https://career.ihsanrd.org/job/old-1/"><h3>منسق قديم</h3><div class="location">حلب</div><span class="job-type">Full Time</span><span class="date">Posted 1 month ago | Closed: November 1, 2026</span></a></li>
                  <li class="job_listing"><a href="https://career.ihsanrd.org/job/vol-1/"><h3>فرصة تطوع</h3><div class="location">دمشق</div><span class="job-type">Volunteer</span><span class="date">Posted 2 days ago</span></a></li>
                </ul>"""
            })
        if u == "https://sy.medglobal.org/en/jobs":
            return httpx.Response(200, text='<a href="/jobs/data-entry-operator-homs-1418">Data Entry Operator-Homs</a>')
        if "/jobs/data-entry-operator-homs-1418" in u:
            return httpx.Response(200, text='<h1>Data Entry Operator-Homs -- MedGlobal SY</h1><a href="/jobs/apply/data-entry-operator-homs-1418">Apply Now!</a>')
        if "ahf.ngo/wp-json" in u:
            return httpx.Response(200, json=[
                {
                    "date_gmt": "2026-11-18T10:00:00",
                    "link": "https://ahf.ngo/2026/11/18/data/",
                    "title": {"rendered": "مدخل بيانات"},
                    "content": {"rendered": '<p>حمص - للتقديم: <a href="https://sham.creativetaskflow.com/cv.aspx?id=JR/26/000219">رابط التقديم</a></p>'},
                },
                {
                    "date_gmt": "2026-11-18T10:00:00",
                    "link": "https://ahf.ngo/2026/11/18/tender/",
                    "title": {"rendered": "إعلان مناقصة : تأهيل مدارس"},
                    "content": {"rendered": '<p>للتواصل: <a href="mailto:procurement@hope-revival.ngo">mail</a></p>'},
                },
            ])
        return httpx.Response(404)

    with _mock_client(handler) as cli:
        dd = ingo_ats.scrape_inoportal_ngo(cli, "DunyaDoktorlari", "dunya_doktorlari", "MDM-T")
        assert len(dd) == 2
        by_c = {j.country: j for j in dd}
        assert by_c["sy"].cities == ["دمشق"]
        assert by_c["sy"].apply_url == "https://cv.inoportal.com/DunyaDoktorlari/Basvuru/531/2"
        assert by_c["tr"].cities == ["إسطنبول"]

        ihsan = ingo_ats.scrape_ihsanrd(cli)
        assert len(ihsan) == 1
        assert ihsan[0].cities == ["حلب"]
        assert ihsan[0].deadline == "2026-11-30"

        mg = ingo_ats.scrape_medglobal_sy(cli)
        assert len(mg) == 1
        assert mg[0].apply_url == "https://sy.medglobal.org/jobs/apply/data-entry-operator-homs-1418"
        assert mg[0].cities == ["حمص"]

        ahf = ingo_ats.scrape_wp_ngo_vacancies(cli, base_url="https://ahf.ngo", source="ahf", employer="AHF", default_cities=["دمشق"])
        assert len(ahf) == 1
        assert ahf[0].apply_url == "https://sham.creativetaskflow.com/cv.aspx?id=JR/26/000219"


def test_powerholding_and_jobsyria_net_portals():
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "powerholding-intl.com" in u:
            return httpx.Response(200, text="""
              <table><tbody>
                <tr class="data-row">
                  <td><a class="jobTitle-link" href="/job/Syria-Site-Engineer/1357486657/">Site Engineer</a>
                      <a class="jobTitle-link" href="/job/Syria-Site-Engineer/1357486657/">Site Engineer</a></td>
                  <td><span class="jobLocation">Syria, SY</span></td>
                  <td><span class="jobDate">20 Nov 2026</span></td>
                </tr>
                <tr class="data-row">
                  <td><a class="jobTitle-link" href="/job/Qatar-Driver/999/">Driver</a></td>
                  <td><span class="jobLocation">Doha, QA</span></td>
                  <td><span class="jobDate">20 Nov 2026</span></td>
                </tr>
              </tbody></table>
            """)
        if "jobsyria.net" in u and "page=1" in u:
            return httpx.Response(200, text="""
              <article class="work-card">
                <button onclick="openShareModal('https://jobsyria.net/وظائف/اكثر-من-اربع-شواغر/حماة/916')"></button>
                <h2>اكثر من اربع شواغر</h2>
                <span>21 hours ago</span>
                <p class="work-description-text">
                  <span class="work-description-preview">شركة بحاجة...</span>
                  <span class="work-description-full hidden">شركة متخصصة بتنقية المياه بحاجة إلى مهندس كهرباء للتواصل عبر الرقم التالي 0997227502</span>
                </p>
                <p class="new-style-data">الجهة المعنية:<b> شركة تنقية المياه</b></p>
                <p class="new-style-data">المحافظة:<b> حماة</b></p>
                <a href="tel:0997227502">0997227502</a>
              </article>
              <article class="work-card">
                <button onclick="openShareModal('https://jobsyria.net/وظائف/محاسب/حمص/917')"></button>
                <h2>مطلوب محاسب</h2>
                <span>2 days ago</span>
                <p class="work-description-text">
                  <span class="work-description-full hidden">مطلوب محاسب للعمل في شركة تجارية، للتقديم على الموقع:</span>
                </p>
                <p class="new-style-data">المحافظة:<b> حمص</b></p>
                <a href="https://jobsyria.net/وظائف/محاسب/حمص/917">التفاصيل</a>
              </article>
            """)
        return httpx.Response(200, text="<html></html>")

    with _mock_client(handler) as cli:
        ph = web_portals.scrape_powerholding_sy(cli, max_pages=1)
        assert len(ph) == 1
        assert ph[0].apply_url == "https://careers.powerholding-intl.com/job/Syria-Site-Engineer/1357486657/"
        assert ph[0].source not in config.OFFICIAL_NGO_SOURCES

        js = web_portals.scrape_jobsyria_net(cli, max_pages=1)
        assert len(js) == 1
        assert js[0].phones == ["0997227502"]
        assert js[0].cities == ["حماة"]
        assert js[0].apply_url is None
        assert "jobsyria_net" not in config.OFFICIAL_NGO_SOURCES
        assert "jobsyria_net" not in config.OFFICIAL_GOV_SOURCES


def test_phase3_cleanup_extract_sy_routing_and_h2_fallback():
    from app import extract as ex

    post_ar = (
        "🟣 المسمى الوظيفي\n"
        "مختص تسويق عبر وسائل التواصل الاجتماعي – Social Media Specialist\n\n"
        "🟣 الجهة\n"
        "شركة ديفيد اب الهندسية – دمشق، المزة\n"
        "للتقديم: hr@devedup-arch.com\n"
    )
    js_ar = ex.extract(post_ar, "hr_partner", "https://t.me/recruitment_vacancies/7515", "2026-11-20T10:00:00+00:00", "formal", [], country="sy", sector="private")
    assert len(js_ar) == 1
    assert js_ar[0].title != "الجهة"
    assert "مختص تسويق" in js_ar[0].title

    post_en = (
        "🟣 Job Title\n"
        "Photographer\n\n"
        "🟣 Company\n"
        "YallaGo – Homs, Syria\n"
        "Apply: https://rise.yallago.net/careers/3910c117-0e29-4249-9a23-43c2adbbee31\n"
    )
    js_en = ex.extract(
        post_en,
        "hr_partner",
        "https://t.me/recruitment_vacancies/7521",
        "2026-11-20T10:00:00+00:00",
        "formal",
        ["https://rise.yallago.net/careers/3910c117-0e29-4249-9a23-43c2adbbee31"],
        country="sy",
        sector="private",
    )
    assert len(js_en) == 1
    assert js_en[0].title == "Photographer"
    assert js_en[0].cities == ["حمص"]

    assert ingo_ats._detect_sy_or_tr("HR Administrative Manager\nDamascus\nMedecins du Monde Turkey (MDM-T)") == ("sy", ["دمشق"])

    def h2_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="""
          <article class="work-card">
            <button onclick="openShareModal('https://jobsyria.net/وظائف/سائقين/دمشق/883')"></button>
            <h2>سائقين</h2>
            <span>1 day ago</span>
            <p class="work-description-text">
              <span class="work-description-full hidden">مطلوب لشركة بسكوت
سائقين على سيارات توزيع ضمن دمشق، التواصل 0964956514</span>
            </p>
            <p class="new-style-data">المحافظة:<b> دمشق</b></p>
            <a href="tel:0964956514">0964956514</a>
          </article>
        """)

    with _mock_client(h2_handler) as cli:
        cleaned = web_portals.scrape_jobsyria_net(cli, max_pages=1)
        assert len(cleaned) == 1
        assert cleaned[0].title == "سائقين"
        assert cleaned[0].cities == ["دمشق"]

    channels = config.load_channels()
    assert "job_in_syria" not in channels
    assert "recruitment_vacancies" in channels
    assert "tawzif2" in channels


def test_expansion_pack_ingo_and_syria_corporates_universities():
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "neareast.bamboohr.com" in u:
            return httpx.Response(200, json={
                "result": [
                    {"id": "565", "jobOpeningName": "Loan Officer", "departmentLabel": "Syria سوريا", "location": {"city": "Hawi Al Hawa حاوي الهوى", "state": "Al-Raqqa الرقة"}},
                    {"id": "563", "jobOpeningName": "Senior MEL Manager", "departmentLabel": "Iraq", "location": {"city": "Baghdad", "state": "Iraq"}},
                ]
            })
        if "solidarites.org" in u and "job-detail" not in u:
            return httpx.Response(200, text="""
              <a href="/en/since-1980/join-us/jobs/job-detail/?id=117988">SYRIA - Field Coordinator - (M/F) - Aleppo</a>
              <a href="/en/since-1980/join-us/jobs/job-detail/?id=117000">MALI - Field Coordinator</a>
            """)
        if "solidarites.org" in u and "job-detail" in u:
            return httpx.Response(200, text="""
              <h1>SYRIA - Field Coordinator - (M/F) - Aleppo</h1>
              <a href="https://www.solidarites.org/en/since-1980/join-us/jobs/submit-your-application/?jobId=PA3-117988">Apply</a>
            """)
        if "sarderp.baseilowcloud.com" in u:
            return httpx.Response(200, text="""
              <article class="card">
                <h2>Child Friendly Space Team Leader</h2>
                <div class="meta"><span>Protection</span><span>Closes Nov. 29, 2026</span></div>
                <p class="summary">SARD is looking for an IACC Team Leader in Azaz, Aleppo.</p>
                <div class="actions">
                  <a href="/careers/child-friendly-space-team-leader/Hb5l/">View details</a>
                  <a href="/careers/child-friendly-space-team-leader/Hb5l/apply/">Apply now</a>
                </div>
              </article>
            """)
        if "ipv-sy.org/wp-json" in u:
            return httpx.Response(200, json=[
                {
                    "date_gmt": "2026-11-19T10:00:00",
                    "link": "https://ipv-sy.org/job-archive/guard-1/",
                    "title": {"rendered": "Guard-حارس"},
                    "content": {"rendered": "<p>مكان العمل: دير الزور - أبو حمام</p>"},
                }
            ])
        if "ihh.hrpeak.com" in u:
            return httpx.Response(200, text="""
              <table>
                <tr><td><a href="/y7dyb.job">National Fsal Sector Co-Coordinator</a></td><td>Damascus / Syria</td><td>17.11.2026</td></tr>
              </table>
            """)
        if "careers-page.com/platform-human-capital-development" in u:
            if "/job/" in u:
                return httpx.Response(200, text="<h1>مندوب علمي</h1><p>العمل في حلب ودمشق</p>")
            if "page=2" in u:
                return httpx.Response(200, text="<html></html>")
            return httpx.Response(200, text="""
              <template v-else-if="jobs && jobs.length == 0">
                <li class="media">
                  <div class="media-body">
                    <a href="/platform-human-capital-development/job/4R74Y64V">
                      <h5>مندوب علمي/ Scientific Representative</h5>
                    </a>
                  </div>
                </li>
              </template>
            """)
        if "careers.beeorder.com" in u:
            return httpx.Response(200, json={
                "html": """
                  <article class="job-card">
                    <p class="job-card__dept">امتياز المطاعم</p>
                    <h2>Regional Sales Officer - Latakia & Tartous</h2>
                    <span>اللاذقية</span><span>دوام كامل</span>
                    <a href="https://careers.beeorder.com/jobs/92">Apply</a>
                  </article>
                """
            })
        if "hknsyria.com" in u:
            return httpx.Response(200, text="""
              <a href="/Careers/Details/126">Field Services Team Lead | HKN Syria | Al-Hasakah, Syrian Arab Republic | Full-time | Closes 29 Nov 2026</a>
            """)
        if "elffa.com/careers" in u:
            if u.endswith("/careers"):
                return httpx.Response(200, text='<a href="/careers/flutter-developer">View Role</a>')
            return httpx.Response(200, text="<h1>Flutter Developer</h1><p>Build mobile apps in Damascus.</p>")
        if "onecash-latakia" in u:
            return httpx.Response(200, text="""
              <a href="/careers/apply/acc-1/">محاسب متابعة وتدقيق | فرع الزراعة | دوام جزئي | آخر موعد 2026-11-28 | قدّم الآن</a>
            """)
        if "hpu.edu.sy" in u:
            if "2026-2027" in u and "دكتوراه" not in u and "d8%af%d9%83" not in u.lower():
                return httpx.Response(200, text='<a href="https://hpu.edu.sy/ar/اعلان-توظيف-دكتوراه-طبي-2026-2027/">قدّم الآن</a>')
            return httpx.Response(200, text='<h1>إعلان توظيف لحملة شهادة الدكتوراه اختصاصات طبية 2026-2027</h1><form class="wpcf7-form"><input name="n"/></form>')
        if "au.edu.sy" in u:
            return httpx.Response(200, text="""
              <a href="https://au.edu.sy/job/1-tlb-tkdm-llaaml-dmn-alhyy-altaalymy">الهيئة التعليمية</a>
              <a href="https://au.edu.sy/job/2-tlb-tkdm-llaaml-dmn-alkadr-aladary">الكادر الإداري</a>
            """)
        return httpx.Response(404)

    with _mock_client(handler) as cli:
        nef = ingo_ats.scrape_neareast_foundation(cli)
        assert len(nef) == 1 and nef[0].cities == ["الرقة"]

        sol = ingo_ats.scrape_solidarites(cli)
        assert len(sol) == 1 and sol[0].cities == ["حلب"]
        assert "submit-your-application" in sol[0].apply_url

        sard = ingo_ats.scrape_sard(cli)
        assert len(sard) == 1 and sard[0].cities == ["حلب"]
        assert sard[0].deadline == "2026-11-29"
        assert sard[0].apply_url.endswith("/apply/")

        ipv = ingo_ats.scrape_ipv_sy(cli)
        assert len(ipv) == 1 and ipv[0].cities == ["دير الزور"]

        ihh = ingo_ats.scrape_ihh_hrpeak(cli)
        assert len(ihh) == 1 and ihh[0].country == "sy" and ihh[0].cities == ["دمشق"]

        hcd = web_portals.scrape_careers_page_hcd(cli, max_pages=2)
        assert len(hcd) == 1 and "حلب" in hcd[0].cities

        bee = web_portals.scrape_beeorder_sy(cli)
        assert len(bee) == 1 and "اللاذقية" in bee[0].cities and "طرطوس" in bee[0].cities

        hkn = web_portals.scrape_hknsyria(cli)
        assert len(hkn) == 1 and hkn[0].cities == ["الحسكة"] and hkn[0].deadline == "2026-11-29"

        elffa = web_portals.scrape_elffa_sy(cli)
        assert len(elffa) == 1 and elffa[0].field == "computer"

        oc = web_portals.scrape_onecash_sy(cli)
        assert len(oc) == 1 and oc[0].cities == ["اللاذقية"]

        unis = web_portals.scrape_syrian_private_universities(cli)
        assert len(unis) == 3
        uni_cities = {c for j in unis for c in j.cities}
        assert "حمص" in uni_cities and "طرطوس" in uni_cities
        for src in ("beeorder", "hknsyria", "elffa", "onecash", "hcd_syria", "hpu_uni", "au_uni"):
            assert src not in config.OFFICIAL_GOV_SOURCES
            assert src not in config.OFFICIAL_NGO_SOURCES


def test_pitch_generator_and_tracker_ui():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    index_html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    app_css = (root / "frontend" / "app.css").read_text(encoding="utf-8")
    api_py = (root / "app" / "api.py").read_text(encoding="utf-8")

    assert 'id="savedOnly"' in index_html
    assert 'id="appliedOnly"' in index_html
    assert 'id="expiringSavedBanner"' in index_html
    assert "?v=38" in index_html

    for fn in (
        "function toggleApplied(",
        "function mailtoHref(",
        "function isExpiringWithin48h(",
        "function renderExpiringSavedBanner(",
        "function buildCardTrackBadges(",
        "function stripUnicodeJunk(",
        "function cleanCardSummary(",
        "function cardBadges(",
        "CARD_EDU_LABEL",
        "CARD_FIELD_LABEL",
        "tpl-subject-input",
        "card-det-btn",
        "sheet-external-specs",
    ):
        assert fn in app_js

    for cls in (
        ".expiring-saved-banner",
        ".tracker-expiring-pill",
        ".tpl-subject-row",
        ".card-footer",
        ".card-submeta",
        ".card-cta-main",
        ".card-applied-btn",
        ".trunc-txt",
        ".card-det-btn",
        ".sheet-external-specs",
    ):
        assert cls in app_css

    assert 'id="pitchSubjectText"' in api_py
    assert 'id="pitchBodyText"' in api_py
    assert "?subject=" in api_py


def test_clean_titles_summaries_and_no_unicode_surrogate_leaks():
    from app import db, extract as ex

    assert ex.rewrite_freelance_title(
        "مزود خدمة مستقل لتقديم خدمات تخصصية في أمراض الأطفال",
        "دعوة للتعاقد مع مزود خدمة مستقل (طبيب أطفال)\nتعلن مؤسسة SDI...",
    ) == "طبيب أطفال — مزود خدمة مستقل"

    js_sana = ex.extract(
        "وزارة التنمية الإدارية تعلن عن توفر شواغر وظيفية لصالح وزارة الإدارة المحلية والبيئة في دمشق.\n- التفاصيل حول الشواغر المتاحة متوفرة ضمن الرابط الآتي\nhttps://example.gov.sy/jobs",
        "sana",
        "https://t.me/sana_gov/1",
        "2026-11-22T10:00:00+00:00",
    )
    assert all("الرابط الآتي" not in j.title and "التفاصيل حول" not in j.title for j in js_sana)

    d = {
        "title": "الشواغر المطلوبة والشروط",
        "employer": "شركة الترك للصناعات (ALTURK) عبر",
        "cities": ["طرطوس"],
        "country": "sy",
        "phones": ["0933232080"],
        "raw": (
            "إعلان توظيف — فرص عمل في طرطوس\n"
            "\u200bتعلن شركة الترك للصناعات عبر فرع طرطوس عن حاجتها:\n"
            "\u200bالشواغر المطلوبة والشروط:\n"
            "\u200bسكرتيرة:\nإجادة استخدام الحاسوب.\n\n"
            "\u200bصانع مخرطة:\nالخبرة مطلوبة.\n\n"
            "\u200bمعلومات التواصل والموقع:\nطرطوس\n0933232080"
        ),
    }
    db._enrich_from_raw(d)
    assert d["title"] == "سكرتيرة / صانع مخرطة"
    assert d["employer"] == "شركة الترك للصناعات (ALTURK)"
    assert "معلومات التواصل" not in (d["summary"] or "")
    assert "\u200b" not in (d["summary"] or "") and "\ufffd" not in (d["summary"] or "")


def test_bugfixes_city_hours_title_moad_and_specs_drawer():
    from app import db, extract as ex, taxonomy as tx
    from app.sources import moad_pdf

    assert tx.cities_from_text("محاسبة لدى مركز جملة بقدسيا") == ["دمشق"]
    assert tx.shift("أوقات الدوام:\nمن الساعة 9 صباحاً حتى الساعة 6 مساءً.") == "من 9 لـ 6"

    hs = ex.extract(
        (
            "💼 فرصة عمل في المجال الإداري | 📍 اللاذقية – شركة M.ONE Fashion Wear\n"
            "🎙️ تعلن شركة M.ONE Fashion Wear في اللاذقية عن حاجتها إلى صبية للعمل في المعمل الرئيسي للشركة.\n"
            "👩🏻‍💼 المسمى الوظيفي: موظفة إدارية (متابعة الورشات).\n"
            "⏰ أوقات الدوام:\nمن الساعة 9 صباحاً حتى الساعة 6 مساءً.\n"
            "📲 0930707221"
        ),
        "higher_steps",
        "https://t.me/higher_steps/15152",
        "2026-11-22T06:48:18+00:00",
    )
    assert len(hs) == 1
    assert hs[0].title == "موظفة إدارية (متابعة الورشات)"
    assert db._clean_employer_val(hs[0].employer) == "شركة M.ONE Fashion Wear"
    assert hs[0].shift == "من 9 لـ 6"
    assert hs[0].cities == ["اللاذقية"]

    sj = ex.extract(
        "شاغر معلم نجار المينو للعمل في ورشة ب المزينة المنامة مؤمنة للأستفسار اكثر الاتصال على في حمص – للتقديم والتواصل المباشر عبر الرقم 0957700555",
        "syrianjobs2",
        "https://t.me/syrianjobs2/9457",
        "2026-11-22T06:00:00+00:00",
    )
    assert len(sj) == 1
    assert "المنامة" not in sj[0].title and "استفسار" not in sj[0].title and "أستفسار" not in sj[0].title
    assert sj[0].cities == ["حمص"]

    assert moad_pdf.fix_lost_tails("مساعد دعم لوجست (أرشيف إعلامي)") == "مساعد دعم لوجستي (أرشيف إعلامي)"

    moad_row = {
        "title": "مساعد دعم لوجست (أرشيف إعلامي)",
        "employer": "وزارة الإعلام",
        "cities": ["دمشق"],
        "country": "sy",
        "official": True,
        "education": "secondary",
        "summary": "مساعد دعم لوجستي ( أرشيف إعلامي ) · الجهة: وزارة الإعلام",
        "apply_url": "https://apply.hiring.moad.gov.sy/single/test123",
    }
    db._enrich_from_raw(moad_row)
    assert moad_row["title"] == "مساعد دعم لوجستي (أرشيف إعلامي)"
    assert "الجهة:" not in (moad_row["summary"] or "")

    orphan_shift_row = {
        "title": "صبية للعمل في المعمل الرئيسي للشركة",
        "employer": None,
        "cities": [],
        "country": "sy",
        "summary": "المسمى الوظيفي: موظفة إدارية (متابعة الورشات). · أوقات الدوام",
        "raw": (
            "🎙️ تعلن شركة M.ONE Fashion Wear في اللاذقية عن حاجتها إلى صبية للعمل في المعمل الرئيسي للشركة.\n"
            "👩🏻‍💼 المسمى الوظيفي: موظفة إدارية (متابعة الورشات).\n"
            "⏰ أوقات الدوام:\nمن الساعة 9 صباحاً حتى الساعة 6 مساءً."
        ),
    }
    db._enrich_from_raw(orphan_shift_row)
    assert orphan_shift_row["title"] == "موظفة إدارية (متابعة الورشات)"
    assert orphan_shift_row["employer"] == "شركة M.ONE Fashion Wear"
    assert orphan_shift_row["shift"] == "من 9 لـ 6"
    assert orphan_shift_row["cities"] == ["اللاذقية"]

    assert ex.strip_person_role_prefix("شاب للعمل معلم صاج") == "معلم صاج"
    assert ex.strip_person_role_prefix("شاب للعمل في مطعم") == "شاب للعمل في مطعم"
    saj = ex.extract(
        "مطلوب شاب للعمل معلم صاج براتب ممتاز في طرطوس\nللتواصل 0933111222",
        "syrianjobs2",
        "https://t.me/syrianjobs2/9999",
        "2026-11-22T09:00:00+00:00",
    )
    assert len(saj) == 1 and saj[0].title == "معلم صاج"

    skills_card = {
        "title": "فني صيانة كهربائية",
        "employer": "شركة برولاين فارما للصناعات الدوائية والتجميلية",
        "cities": ["ريف دمشق"],
        "education": "institute",
        "email": "hr@prolinepharma.com",
        "skills": [
            "خريج معهد تقني أو مهني في الاختصاص المطلوب.",
            "خبرة عملية لا تقل عن 5 سنوات في مجال الاختصاص.",
            "القدرة على تشخيص الأعطال وتنفيذ أعمال الصيانة الوقائية والتصحيحية.",
        ],
        "tasks": [],
        "summary": "فرصة عمل لدى شركة برولاين فارما للصناعات الدوائية والتجميلية (ريف دمشق) · المؤهل: معهد تقاني / متوسط",
    }
    db._enrich_from_raw(skills_card)
    assert "المؤهل:" not in (skills_card["summary"] or "")
    assert "خبرة عملية لا تقل عن 5 سنوات" in (skills_card["summary"] or "")





