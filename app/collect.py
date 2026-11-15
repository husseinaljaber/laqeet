"""The collector: read the four channels, turn posts into jobs, store them. Run every 15–30 minutes.

    python -m app.collect                 # read new posts from Telegram (and new ministry PDFs)
    python -m app.collect --offline       # rebuild from saved posts in data/raw and PDFs in data/pdfs
    python -m app.collect --reprocess     # re-run extraction on every stored post (after an extractor fix)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
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
        super().__init__(headers={"User-Agent": USER_AGENT}, timeout=60, **kw)
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
    """The newer announcements link to the ministry's jobs portal instead of a PDF: one card that points there."""
    return Job(title=f"شواغر متعددة لدى {ann.entity}", source="moad", post_url=ann.post_url, posted_at=ann.posted_at,
               employer=ann.entity, cities=tx.cities_from_text(ann.governorates_text or ann.text), deadline=ann.deadline,
               kind="formal", apply_url=ann.hiring_url, details_url=ann.hiring_url, official=True,
               country="sy", sector="gov", apply_types=tx.infer_apply_types(ann.hiring_url, [], False, None), confidence=0.7)


def ministry_jobs(post: Post, client: httpx.Client | None, con) -> list[Job]:
    ann = moad_announcement.parse(post)
    if not ann:
        return []
    if "moad.gov.sy" in (ann.hiring_url or ""):
        return [portal_job(ann)]
    name = _PDF_NAME.search(ann.hiring_url or "")
    if not name:
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


def collect(offline: bool = False) -> dict:
    con = db.connect()
    since = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).date().isoformat()
    stats = {}
    client = None if offline else PoliteClient()
    resolve_client = None if offline else httpx.Client(timeout=20, verify=False, follow_redirects=True)
    resolve_cache: dict = {}
    try:
        for channel in config.CHANNELS:
            new = posts = 0
            if offline:
                path = config.ROOT / "data" / "raw" / f"{channel}.json"
                feed = [Post(**p) for p in json.loads(path.read_text())] if path.exists() else []
            else:
                stop = int(db.get_meta(con, f"last:{channel}", 0))
                try:
                    feed = list(read_channel(channel, client, stop, since))
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
                ("web_portals", fetch_all_web_portal_jobs),
                ("turkey_portals", fetch_all_tr_portal_jobs),
            ):
                try:
                    batch = fn(resolve_client, cache=resolve_cache)
                    new_count = 0
                    for j in batch:
                        if db.expires_on(j) >= today and da.resolve_job_apply(j, client=resolve_client, cache=resolve_cache):
                            _, is_new = db.upsert(con, j)
                            new_count += is_new
                    stats[key] = {"posts": len(batch), "new_jobs": new_count}
                    log.info("%s: %d jobs, %d new jobs", key, len(batch), new_count)
                except Exception as e:
                    log.warning("%s collect failed: %s", key, e)
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
    return stats


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
    a = ap.parse_args()
    print(reprocess() if a.reprocess else collect(offline=a.offline))
