"""Find and score new public Telegram job channels before adding them to data/channels.json.

    python tools/discover_channels.py --snowball                 # candidates from what our channels forward/mention
    python tools/discover_channels.py chan1 chan2 @chan3         # score specific channels
    python tools/discover_channels.py --snowball --out report.json

For each candidate it reads the public web preview (t.me/s/<name>, up to 3 pages, ≥1.2 s between requests) and
measures: is it public and alive, posts in the last 30 days, how many posts are real job ads (same extractor and
spam filter as the site), Syria vs Turkey, and how many of its jobs we don't already have. Nothing is added
automatically: the report suggests channels.json entries for the ones that pass.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402

from app import config, db  # noqa: E402
from app.collect import PoliteClient  # noqa: E402
from app.extract import extract  # noqa: E402
from app.sources.telegram import parse_page  # noqa: E402

_NAME = re.compile(r"(?:t\.me/(?:s/)?|telegram\.me/|(?<![\w.])@)([A-Za-z][A-Za-z0-9_]{4,31})\b")
_SKIP = {"joinchat", "share", "addstickers", "proxy", "iv", "gmail", "hotmail", "yahoo", "outlook", "icloud", "contact"}

# Thresholds for «worth adding»
MIN_POSTS_30D = 8          # active
MIN_JOB_YIELD = 0.35       # at least a third of posts are job ads
MIN_NEW_JOBS = 5           # brings jobs we don't have yet
MAX_SPAM_SHARE = 0.20
MAX_QUIET_DAYS = 10
MIN_LOCATED = 0.4          # share of jobs naming a Syrian/Turkish city (unless the channel itself says Syria/Turkey)
_TARGET_WORDS = re.compile(r"سوري|سورية|syria|دمشق|حلب|حمص|حماه|حماة|اللاذقي|طرطوس|ادلب|إدلب|تركيا|turkey|türkiye|turkiye|"
                           r"اسطنبول|إسطنبول|istanbul|عنتاب|gaziantep|مرسين|mersin|بورصة|bursa|انطاكيا|hatay", re.I)
_OTHER_PLACES = re.compile(r"الخليج|السعودية|الرياض|جدة|دبي|الإمارات|الامارات|قطر|الكويت|مصر|القاهرة|الأردن|عمّان|لبنان|بيروت|العراق|"
                           r"gulf|saudi|dubai|uae|qatar|kuwait|egypt|cairo|jordan|lebanon|iraq", re.I)


def clean_name(n: str) -> str | None:
    n = n.strip().lstrip("@").split("/")[-1].split("?")[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", n) or n.lower() in _SKIP or n.lower().endswith("bot"):
        return None
    return n


def snowball(client, known: set[str], pages: int = 3) -> collections.Counter:
    """Channels that our channels forward from or link to, counted by how many of ours point at them."""
    found: collections.Counter = collections.Counter()
    for ch in sorted(config.CHANNELS):
        url = f"https://t.me/s/{ch}"
        for _ in range(pages):
            try:
                html = client.get(url).text
            except Exception:
                break
            soup = BeautifulSoup(html, "html.parser")
            names = set()
            for a in soup.select("a.tgme_widget_message_forwarded_from_name[href], .tgme_widget_message_text a[href]"):
                if m := _NAME.search(a["href"]):
                    names.add(m.group(1))
            for t in soup.select(".tgme_widget_message_text"):
                names.update(m.group(1) for m in _NAME.finditer(t.get_text(" ")))
            for n in names:
                if (c := clean_name(n)) and c.lower() not in known:
                    found[c] += 1
            older = soup.select_one("a.tme_messages_more[data-before]")
            if not older:
                break
            url = f"https://t.me/s/{ch}?before={older['data-before']}"
    return found


def channel_info(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one(".tgme_channel_info_header_title")
    desc = soup.select_one(".tgme_channel_info_description")
    subs = None
    for c in soup.select(".tgme_channel_info_counter"):
        typ = c.select_one(".counter_type")
        if typ and "subscriber" in typ.get_text():
            subs = c.select_one(".counter_value").get_text(strip=True)
    return {"title": title.get_text(" ", strip=True) if title else None,
            "description": desc.get_text(" ", strip=True)[:200] if desc else "", "subscribers": subs}


def evaluate(name: str, client, existing_fps: set[str], pages: int = 3) -> dict:
    out = {"channel": name, "ok": False}
    since = datetime.now(timezone.utc) - timedelta(days=30)
    posts, url, info = [], f"https://t.me/s/{name}", None
    for _ in range(pages):
        try:
            r = client.get(url)
        except Exception as e:
            out["error"] = f"{type(e).__name__}"
            return out
        if r.status_code != 200 or "tgme_channel_info" not in r.text and not posts:
            out["error"] = "not a public channel (or web preview off)"
            return out
        info = info or channel_info(r.text)
        page = parse_page(r.text, name)
        if not page:
            break
        posts.extend(page)
        oldest = min(p.id for p in page)
        if any(p.date and p.date < since.isoformat() for p in page):
            break
        url = f"https://t.me/s/{name}?before={oldest}"
    out.update(info or {})
    recent = [p for p in posts if p.date and p.date >= since.isoformat() and p.text]
    out["posts_30d"] = len(recent)
    last = max((p.date for p in posts if p.date), default=None)
    out["last_post"] = last[:10] if last else None
    out["quiet_days"] = round((datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 86400, 1) if last else None
    job_posts = spam_posts = jobs_total = new_jobs = quick = located = elsewhere = 0
    countries, cities = collections.Counter(), collections.Counter()
    samples = []
    for p in recent:
        try:
            jobs = extract(p.text, f"cand_{name}", p.url, p.date, "formal", p.links)
        except Exception:
            continue
        if any(j.spam for j in jobs):
            spam_posts += 1
        good = [j for j in jobs if not j.spam]
        if not good:
            continue
        job_posts += 1
        for j in good:
            jobs_total += 1
            countries[j.country] += 1
            cities.update(j.cities)
            located += bool([c for c in j.cities if not c.startswith("عدة")])
            elsewhere += bool(_OTHER_PLACES.search(p.text))
            quick += bool(j.phones and not j.deadline)
            if db.fingerprint(j) not in existing_fps:
                new_jobs += 1
            if len(samples) < 4:
                samples.append(f"{j.title} · {'، '.join(j.cities) or '—'}")
    n = max(1, len(recent))
    about = f"{out.get('title') or ''} {out.get('description') or ''}"
    out["located_share"] = round(located / max(1, jobs_total), 2)
    out["elsewhere_share"] = round(elsewhere / max(1, jobs_total), 2)
    out["channel_says_target"] = bool(_TARGET_WORDS.search(about)) and not _OTHER_PLACES.search(about)
    out.update(job_posts=job_posts, jobs_30d=jobs_total, new_jobs_30d=new_jobs, job_yield=round(job_posts / n, 2),
               spam_share=round(spam_posts / n, 2), countries=dict(countries), top_cities=[c for c, _ in cities.most_common(4)],
               quick_share=round(quick / max(1, jobs_total), 2), samples=samples)
    reasons = []
    if out["posts_30d"] < MIN_POSTS_30D:
        reasons.append("قليلة النشر")
    if out["job_yield"] < MIN_JOB_YIELD:
        reasons.append("أغلب منشوراتها مو إعلانات شغل")
    if new_jobs < MIN_NEW_JOBS:
        reasons.append("ما بتزيد إعلانات جديدة كفاية")
    if out["spam_share"] > MAX_SPAM_SHARE:
        reasons.append("فيها إعلانات مشبوهة كتير")
    if out["quiet_days"] is None or out["quiet_days"] > MAX_QUIET_DAYS:
        reasons.append("ساكتة من فترة")
    if jobs_total and not out["channel_says_target"] and out["located_share"] < MIN_LOCATED:
        reasons.append("ما واضح إنه الشغل بسوريا أو تركيا")
    if out["elsewhere_share"] > 0.3:
        reasons.append("كتير من إعلاناتها لبلاد تانية (خليج، مصر…)")
    out["ok"] = not reasons
    out["why_not"] = reasons
    return out


def suggest_entry(r: dict) -> dict:
    tr = r["countries"].get("tr", 0) > r["countries"].get("sy", 0)
    return {"source": re.sub(r"[^a-z0-9_]", "", r["channel"].lower())[:24],
            "label": (r.get("title") or r["channel"])[:60],
            "kind": "quick" if r.get("quick_share", 0) >= 0.5 else "formal",
            "official": False, "country": "tr" if tr else "sy"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("channels", nargs="*", help="channel usernames or t.me links")
    ap.add_argument("--snowball", action="store_true", help="also take channels our channels forward/mention")
    ap.add_argument("--min-mentions", type=int, default=1)
    ap.add_argument("--limit", type=int, default=60, help="score at most this many candidates")
    ap.add_argument("--out", default="channel_report.json")
    args = ap.parse_args()

    known = {c.lower() for c in config.CHANNELS}
    con = db.connect()
    existing_fps = {r[0] for r in con.execute("SELECT fp FROM jobs")}
    client = PoliteClient(follow_redirects=True)
    cands: collections.Counter = collections.Counter({c: 99 for a in args.channels if (c := clean_name(a))})
    if args.snowball:
        print("snowball: reading our channels for forwards and mentions…", file=sys.stderr)
        cands.update({k: v for k, v in snowball(client, known).items() if v >= args.min_mentions})
    cands = collections.Counter({k: v for k, v in cands.items() if k.lower() not in known})
    results = []
    for name, mentions in cands.most_common(args.limit):
        r = evaluate(name, client, existing_fps)
        r["mentions"] = mentions if mentions != 99 else None
        results.append(r)
        flag = "✓" if r["ok"] else "·"
        print(f"{flag} {name:<28} posts30={r.get('posts_30d', '-'):<4} yield={r.get('job_yield', '-'):<5} "
              f"new={r.get('new_jobs_30d', '-'):<4} {r.get('countries', '')} {r.get('error') or '، '.join(r.get('why_not', []))}",
              file=sys.stderr)
    winners = [r for r in results if r["ok"]]
    report = {"generated_at": db.now(), "scored": len(results), "recommended": len(winners),
              "suggested_channels": {r["channel"]: suggest_entry(r) for r in winners}, "results": results}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(winners)} of {len(results)} pass. Report: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
