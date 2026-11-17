"""The collector: read the four channels, turn posts into jobs, store them. Run every 15–30 minutes.

    python -m app.collect                 # read new posts from Telegram (and new ministry PDFs)
    python -m app.collect --offline       # rebuild from saved posts in data/raw and PDFs in data/pdfs
    python -m app.collect --reprocess     # re-run extraction on every stored post (after an extractor fix)
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx

from . import config, db, direct_apply as da
from . import taxonomy as tx
from .classify import classify as computer_subfield
from .download import download_pdf, resolve_drive_id
from .extract import extract
from .models import Job
from .sources import moad_announcement
from .sources.moad_pdf import Card, fix_lost_tails, parse_pdf
from .sources.reliefweb import fetch_reliefweb_jobs
from .sources.turkey_portals import fetch_all_tr_portal_jobs
from .sources.web_portals import fetch_all_web_portal_jobs
from .sources.telegram import USER_AGENT, Post, parse_page

log = logging.getLogger("collect")


class PoliteClient(httpx.Client):
    """An httpx client that waits at least REQUEST_GAP seconds between requests."""

    def __init__(self, **kw):
        super().__init__(headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(15.0, connect=6.0), **kw)
        self._last = 0.0

    def send(self, request, **kw):
        wait = config.REQUEST_GAP - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            return super().send(request, **kw)
        finally:
            self._last = time.monotonic()


# ------------------------------------------------------------------ ministry
_PDF_NAME = re.compile(r"hiring[\w-]+")


def card_to_job(card: Card, ann: moad_announcement.Announcement) -> Job:
    edu = card.education or tx.MOAD_GRADE_EDU.get(card.grade or "")
    cities = card.governorates or tx.cities_from_text(ann.governorates_text)
    job = Job(
        title=fix_lost_tails(card.title), source="moad", post_url=ann.post_url, posted_at=ann.posted_at, employer=ann.entity,
        cities=cities, education=edu, education_from="grade" if card.grade else ("stated" if edu else "unknown"),
        grade=card.grade, specialty=" / ".join(x for x in (card.general, card.precise) if x) or None,
        experience=card.experience, age_min=card.age_min, age_max=card.age_max, deadline=ann.deadline,
        kind="formal", apply_url=ann.apply_url, details_url=ann.hiring_url, official=True,
        country="sy", sector="gov", apply_types=tx.infer_apply_types(ann.apply_url, [], False, None),
        tasks=card.tasks[:6], skills=card.skills[:6],
    )
    job.field = tx.field_from_text(card.title, f"{card.general} {card.precise}")
    sub = computer_subfield(card.title, f"{card.general} {card.precise}")
    if sub or job.field == "computer":
        job.field, job.subfield = "computer", sub or "DT"
    job.confidence = 0.9 if card.grade and cities else 0.6
    return job


def portal_job(ann: moad_announcement.Announcement) -> Job:
    """The newer announcements link to the ministry's jobs portal or a direct gov form instead of a PDF."""
    apply_target = ann.apply_url or ann.hiring_url
    details_target = ann.hiring_url or ann.apply_url
    return Job(title=f"شواغر متعددة لدى {ann.entity}", source="moad", post_url=ann.post_url, posted_at=ann.posted_at,
               employer=ann.entity, cities=tx.cities_from_text(ann.governorates_text or ann.text), deadline=ann.deadline,
               kind="formal", apply_url=apply_target, details_url=details_target, official=True,
               country="sy", sector="gov", apply_types=tx.infer_apply_types(apply_target, [], False, None), confidence=0.7)


def ministry_jobs(post: Post, client: httpx.Client | None, con) -> list[Job]:
    ann = moad_announcement.parse(post)
    if not ann:
        return []
    name = _PDF_NAME.search(ann.hiring_url or "")
    if "moad.gov.sy" in (ann.hiring_url or "") or not name:
        if ann.apply_url or ann.hiring_url:
            return [portal_job(ann)]
        return []
    path = config.PDF_DIR / f"{name.group(0)}.pdf"
    if not path.exists():
        if client is None:
            return []
        try:
            drive = resolve_drive_id(ann.hiring_url, client)
            if not drive:
                raise ValueError("no Drive id behind " + ann.hiring_url)
            download_pdf(drive, path, client)
        except Exception as e:  # a broken link must not stop the run
            log.warning("PDF %s: %s", ann.hiring_url, e)
            con.execute("INSERT OR REPLACE INTO pdfs VALUES (?,?,?,?)", (name.group(0), f"error: {e}", 0, db.now()))
            return []
    cache = config.PDF_DIR / f"{name.group(0)}.cards.json"
    if cache.exists():
        cards = [Card(**c) for c in json.loads(cache.read_text())]
    else:
        cards = parse_pdf(str(path))
        cache.write_text(json.dumps([c.to_dict() for c in cards], ensure_ascii=False))
    con.execute("INSERT OR REPLACE INTO pdfs VALUES (?,?,?,?)", (name.group(0), "ok", len(cards), db.now()))
    return [card_to_job(c, ann) for c in cards if c.title]


# ------------------------------------------------------------------ channels
def post_jobs(post: Post, client, con) -> list[Job]:
    ch = config.CHANNELS.get(post.channel)
    if not ch:
        return []
    if ch["source"] == "moad":
        return ministry_jobs(post, client, con)
    jobs = extract(post.text, ch["source"], post.url, post.date, ch["kind"], post.links,
                   country=ch.get("country"), sector=ch.get("sector"))
    for j in jobs:
        if len(j.title.split()) > 9 or len(j.title) > 70:
            j.confidence = min(j.confidence, 0.4)  # reads like a sentence: a person checks it before it shows
    return [j for j in jobs if not j.spam]


def store_post(
    con,
    post: Post,
    client,
    skip_expired: bool = True,
    resolve_client: httpx.Client | None = None,
    resolve_cache: dict | None = None,
) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).date().isoformat()
    today = date.today().isoformat()
    if post.date and post.date[:10] < since:
        return 0
    jobs = post_jobs(post, client, con)
    if skip_expired:
        jobs = [j for j in jobs if db.expires_on(j) >= today]
    # Strict direct-apply gate: unwrap wrapper portals/shorteners and keep ONLY jobs with a verified form, site, email, or phone
    resolver = resolve_client if resolve_client is not None else client
    jobs = [j for j in jobs if da.resolve_job_apply(j, client=resolver, cache=resolve_cache)]
    con.execute("INSERT OR REPLACE INTO posts (channel, id, date, text, links, jobs) VALUES (?,?,?,?,?,?)",
                (post.channel, post.id, post.date, post.text, json.dumps(post.links), len(jobs)))
    new = 0
    for job in jobs:
        _, is_new = db.upsert(con, job)
        new += is_new
    return new


def read_channel(channel: str, client: httpx.Client, stop_id: int, since: str, max_pages: int = 20):
    """Posts newer than stop_id (or posted after `since`), newest first."""
    before = None
    for _ in range(max_pages):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        page = parse_page(client.get(url).text, channel)
        if not page:
            return
        for post in sorted(page, key=lambda p: -p.id):
            if post.id <= stop_id or (post.date and post.date[:10] < since):
                return
            yield post
        oldest = min(p.id for p in page)
        if before is not None and oldest >= before:
            return
        before = oldest


def collect(offline: bool = False, quick: bool = False) -> dict:
    con = db.connect()
    since = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).date().isoformat()
    stats = {}
    client = None if offline else PoliteClient()
    resolve_client = (
        None
        if offline
        else httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0), verify=False, follow_redirects=True)
    )
    resolve_cache: dict = {}
    max_pages = 4 if quick else 20
    try:
        for channel in config.CHANNELS:
            new = posts = 0
            if offline:
                path = config.ROOT / "data" / "raw" / f"{channel}.json"
                feed = [Post(**p) for p in json.loads(path.read_text())] if path.exists() else []
            else:
                stop = int(db.get_meta(con, f"last:{channel}", 0))
                try:
                    feed = list(read_channel(channel, client, stop, since, max_pages=max_pages))
                except httpx.HTTPError as e:
                    log.warning("%s: %s", channel, e)
                    feed = []
            for post in sorted(feed, key=lambda p: p.id):  # oldest first, so first_seen follows posting order
                posts += 1
                new += store_post(con, post, client, resolve_client=resolve_client, resolve_cache=resolve_cache)
                db.set_meta(con, f"last:{channel}", max(post.id, int(db.get_meta(con, f"last:{channel}", 0))))
                con.commit()
            stats[channel] = {"posts": posts, "new_jobs": new}
            log.info("%s: %d posts, %d new jobs", channel, posts, new)
        if not offline:
            today = date.today().isoformat()
            for key, fn in (
                ("reliefweb", fetch_reliefweb_jobs),
                ("web_portals", lambda c, cache=None: fetch_all_web_portal_jobs(c, cache=cache, quick=quick)),
                ("turkey_portals", lambda c, cache=None: fetch_all_tr_portal_jobs(c, cache=cache, max_pages=2 if quick else 6, quick=quick)),
            ):
                try:
                    batch = fn(resolve_client, cache=resolve_cache)
                    # Resolve external links BEFORE opening the SQLite write transaction so concurrent web requests never block.
                    ready = [
                        j
                        for j in batch
                        if db.expires_on(j) >= today and da.resolve_job_apply(j, client=resolve_client, cache=resolve_cache)
                    ]
                    new_count = 0
                    for j in ready:
                        _, is_new = db.upsert(con, j)
                        new_count += is_new
                    con.commit()
                    stats[key] = {"posts": len(batch), "new_jobs": new_count}
                    log.info("%s: %d jobs, %d new jobs", key, len(batch), new_count)
                except Exception as e:
                    log.warning("%s collect failed: %s", key, e)
            try:
                stats["live_audit"] = verify_live_jobs(con, cache=resolve_cache, max_checks=150 if quick else 350)
            except Exception as e:
                log.warning("live_audit failed: %s", e)
        pruned = db.prune_old(con, config.HISTORY_DAYS)
        if pruned:
            log.info("pruned %d jobs older than %d days", pruned, config.HISTORY_DAYS)
        db.set_meta(con, "updated_at", db.now())
        con.commit()
    finally:
        if resolve_client:
            resolve_client.close()
        if client:
            client.close()
        con.close()
        gc.collect()
    return stats


def verify_live_jobs(con, cache: dict | None = None, max_checks: int = 120) -> dict:
    """Periodically re-check live job URLs to expire closed postings and unwrap any lingering wrappers."""
    cache = cache if cache is not None else {}
    now_dt = datetime.now(timezone.utc)
    today_str = now_dt.date().isoformat()
    stale_cutoff = (now_dt - timedelta(hours=24)).isoformat(timespec="seconds")

    rows = con.execute(
        "SELECT id, country, data, posted_at, expires_on, deadline FROM jobs WHERE status='live'"
    ).fetchall()
    priority: list[tuple[int, str, dict]] = []
    regular: list[tuple[int, str, dict]] = []
    for r in rows:
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        u = d.get("apply_url") or ""
        if not u or u.startswith("https://wa.me/") or u.startswith("http://wa.me/"):
            continue
        if da.is_shortener(u) or da.is_wrapper_portal(u) or da.is_disallowed_offline_url(u) or not da.is_direct_form_or_ats(u):
            priority.append((r["id"], r["country"] or "sy", d))
        else:
            ver_at = d.get("verified_at") or ""
            if not ver_at or ver_at < stale_cutoff:
                regular.append((r["id"], r["country"] or "sy", d))

    targets = (priority + regular)[:max_checks]
    if not targets:
        return {"checked": 0, "expired": 0, "rejected": 0, "unwrapped": 0}

    tl = threading.local()
    lock = threading.Lock()
    clients: list[httpx.Client] = []

    def _check_one(item: tuple[int, str, dict]) -> tuple[int, str, dict, dict]:
        jid, country, d = item
        u = d.get("apply_url") or ""
        c = getattr(tl, "client", None)
        if c is None:
            c = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=10.0, verify=False, follow_redirects=True)
            tl.client = c
            with lock:
                clients.append(c)
        res = da._resolve_url_live(u, c, {}, country=country)
        return jid, country, d, res

    checked_results: list[tuple[int, str, dict, dict]] = []
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            checked_results = list(pool.map(_check_one, targets))
    finally:
        for c in clients:
            c.close()

    expired = rejected = unwrapped = 0
    ver_iso = now_dt.isoformat(timespec="seconds")
    for jid, country, d, res in checked_results:
        orig_u = d.get("apply_url") or ""
        if res.get("closed") or (res.get("dead") and not d.get("email") and not d.get("phones")):
            d["apply_url"] = None
            d["verified_at"] = ver_iso
            con.execute(
                "UPDATE jobs SET status='expired', expires_on=min(expires_on, ?), data=?, updated_at=? WHERE id=?",
                (today_str, json.dumps(d, ensure_ascii=False), db.now(), jid),
            )
            expired += 1
            continue

        new_u = orig_u
        changed = False
        if res.get("dead") or res.get("status") in (404, 410):
            new_u = None
            changed = True
        elif res.get("status") == 200:
            if res.get("email") and not d.get("email"):
                d["email"] = res["email"]
                changed = True
            if res.get("phones"):
                merged_p = sorted({p for p in (d.get("phones") or []) + res["phones"] if p})
                if merged_p != (d.get("phones") or []):
                    d["phones"] = merged_p
                    changed = True
            cand = res.get("apply_url")
            if cand and not da.is_strict_portal_wrapper(cand):
                if cand != orig_u:
                    unwrapped += 1
                    changed = True
                new_u = cand
            elif (res.get("has_internal_form") and not da.is_strict_portal_wrapper(res["final_url"])) or da.is_direct_form_or_ats(res["final_url"]):
                new_u = res["final_url"]
                if new_u != orig_u:
                    changed = True
            else:
                new_u = None
                changed = True
        elif da.is_shortener(orig_u) or da.is_strict_portal_wrapper(orig_u):
            new_u = None
            changed = True

        if new_u and (da.is_disallowed_offline_url(new_u) or da.is_shortener(new_u) or da.is_strict_portal_wrapper(new_u)):
            new_u = None
            changed = True

        d["apply_url"] = new_u
        d["verified_at"] = ver_iso
        if not (d.get("apply_url") or d.get("phones") or d.get("email")):
            con.execute(
                "UPDATE jobs SET status='rejected', data=?, updated_at=? WHERE id=?",
                (json.dumps(d, ensure_ascii=False), db.now(), jid),
            )
            rejected += 1
            continue

        new_types = tx.infer_apply_types(d.get("apply_url"), d.get("phones") or [], bool(d.get("whatsapp")), d.get("email"))
        d["apply_types"] = new_types
        if changed:
            con.execute(
                "UPDATE jobs SET apply_types=?, data=?, updated_at=? WHERE id=?",
                ("|" + "|".join(new_types) + "|" if new_types else "", json.dumps(d, ensure_ascii=False), db.now(), jid),
            )
        else:
            con.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), jid))
    con.commit()
    return {"checked": len(targets), "expired": expired, "rejected": rejected, "unwrapped": unwrapped}


def reprocess() -> int:
    """Re-run extraction on stored posts (keeps ids of jobs that still exist)."""
    con = db.connect()
    n = 0
    for row in con.execute("SELECT * FROM posts ORDER BY date").fetchall():
        post = Post(channel=row["channel"], id=row["id"], date=row["date"], text=row["text"], links=json.loads(row["links"]))
        n += store_post(con, post, None, skip_expired=False)
    con.commit()
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--reprocess", action="store_true")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    print(reprocess() if a.reprocess else collect(offline=a.offline, quick=a.quick))
