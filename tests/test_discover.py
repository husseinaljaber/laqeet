"""The channel discovery tool: names, scoring rules and the suggested channels.json entry."""
from datetime import datetime, timedelta, timezone

from tools import discover_channels as dc


def _page(title, desc, posts):
    msgs = "".join(
        f'<div class="tgme_widget_message" data-post="cand/{i}"><div class="tgme_widget_message_text">{t}</div>'
        f'<a class="tgme_widget_message_date"><time datetime="{d}">x</time></a></div>'
        for i, (t, d) in enumerate(posts, start=100)
    )
    return (f'<html><body><div class="tgme_channel_info"><div class="tgme_channel_info_header_title">{title}</div>'
            f'<div class="tgme_channel_info_description">{desc}</div>'
            f'<div class="tgme_channel_info_counter"><span class="counter_value">12K</span>'
            f'<span class="counter_type">subscribers</span></div></div>{msgs}</body></html>')


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def get(self, url):
        self.calls += 1
        return FakeResp(self.pages.pop(0) if self.pages else "<html></html>")


def _recent(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def test_clean_name():
    assert dc.clean_name("@shofeforas") == "shofeforas"
    assert dc.clean_name("https://t.me/s/syrjob?before=5") == "syrjob"
    assert dc.clean_name("joinchat") is None and dc.clean_name("SomeHelperBot") is None and dc.clean_name("ab") is None


def test_good_syrian_channel_passes_and_gets_an_entry():
    ads = [(f"مطلوب عامل مستودع رقم {i} في دمشق براتب جيد، للتواصل واتس 09441112{i:02d}", _recent(i % 9 + 1)) for i in range(12)]
    client = FakeClient([_page("فرص شغل دمشق", "وظائف يومية في سوريا", ads)])
    r = dc.evaluate("damas_work", client, existing_fps=set(), pages=1)
    assert r["ok"], r["why_not"]
    assert r["posts_30d"] == 12 and r["job_yield"] > 0.9 and r["new_jobs_30d"] >= 5 and r["subscribers"] == "12K"
    e = dc.suggest_entry(r)
    assert e["source"] == "damas_work" and e["country"] == "sy" and e["kind"] == "quick"


def test_gulf_channel_is_rejected():
    ads = [(f"مطلوب محاسب للعمل في الرياض السعودية براتب ممتاز رقم {i}", _recent(2)) for i in range(12)]
    client = FakeClient([_page("سافر واشتغل", "وظائف الخليج", ads)])
    r = dc.evaluate("gulfjobs", client, existing_fps=set(), pages=1)
    assert not r["ok"]


def test_dead_or_private_channel():
    r = dc.evaluate("nochan", FakeClient([FakeResp("<html>no</html>", 302).text]), existing_fps=set(), pages=1)
    assert not r["ok"] and r.get("error")
