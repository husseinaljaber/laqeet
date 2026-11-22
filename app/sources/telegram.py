"""Read posts from a public Telegram channel via web preview (`t.me/s/<channel>`),
with optional Telethon MTProto fallback for channels whose web preview is disabled.
"""
from __future__ import annotations

import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import timezone
from typing import Iterator

import httpx
from bs4 import BeautifulSoup

from .. import config

log = logging.getLogger(__name__)
USER_AGENT = "laqeet/0.1 (job aggregator for Syria)"


@dataclass
class Post:
    channel: str
    id: int
    date: str | None  # ISO timestamp, UTC
    text: str
    links: list[str]

    @property
    def url(self) -> str:
        return f"https://t.me/{self.channel}/{self.id}"


def _text(body) -> str:
    """Post text with its real line breaks: <br> becomes a newline, inline tags (emoji, links, bold) stay on their line."""
    for br in body.find_all("br"):
        br.replace_with("\n")
    text = body.get_text("")
    return "\n".join(l.strip() for l in text.splitlines()).strip()


def parse_page(html: str, channel: str) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        try:
            pid = int(msg["data-post"].split("/")[-1])
        except ValueError:
            continue
        body = msg.select_one(".tgme_widget_message_text")
        time = msg.select_one("time[datetime]")
        posts.append(Post(
            channel=channel,
            id=pid,
            date=time["datetime"] if time else None,
            text=_text(body) if body else "",
            links=[a["href"] for a in (body.select("a[href]") if body else [])],
        ))
    return posts


def iter_posts(channel: str, since_iso: str = "2026-03-01", client: httpx.Client | None = None,
               max_pages: int = 200) -> Iterator[Post]:
    """Yield posts from newest to oldest until `since_iso` (a date or timestamp) is passed."""
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    try:
        before = None
        for _ in range(max_pages):
            url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
            page = parse_page(client.get(url).text, channel)
            if not page:
                return
            for post in sorted(page, key=lambda p: -p.id):
                if post.date and post.date[:len(since_iso)] < since_iso:
                    return
                yield post
            oldest = min(p.id for p in page)
            if before is not None and oldest >= before:
                return
            before = oldest
    finally:
        if own:
            client.close()


def has_mtproto_config() -> bool:
    """True when Telegram API credentials + session string are configured."""
    api_id = int(os.getenv("TG_API_ID") or config.TG_API_ID or 0)
    api_hash = os.getenv("TG_API_HASH") or config.TG_API_HASH or ""
    session = os.getenv("TG_STRING_SESSION") or config.TG_STRING_SESSION or ""
    return bool(api_id and api_hash and session)


def extract_mtproto_links(text: str, entities: list | None) -> list[str]:
    """Extract embedded hyperlinks and plain URLs using Telegram UTF-16 offsets."""
    if not text or not entities:
        return []
    utf16 = text.encode("utf-16-le")
    out: list[str] = []
    for ent in entities:
        url = getattr(ent, "url", None)
        if url:
            if url not in out:
                out.append(url)
            continue
        cls_name = type(ent).__name__
        if cls_name == "MessageEntityUrl":
            off = getattr(ent, "offset", 0)
            ln = getattr(ent, "length", 0)
            if off >= 0 and ln > 0:
                seg = utf16[off * 2:(off + ln) * 2].decode("utf-16-le", errors="ignore").strip()
                if seg and seg not in out:
                    out.append(seg)
    return out


def message_to_post(channel: str, msg) -> Post | None:
    """Convert a Telethon Message object into our unified Post record."""
    text = (getattr(msg, "message", None) or getattr(msg, "raw_text", None) or "").strip()
    if not text:
        return None
    dt = getattr(msg, "date", None)
    if dt is not None and getattr(dt, "tzinfo", None) is not None:
        date_str = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    elif dt is not None:
        date_str = dt.isoformat(timespec="seconds") + "+00:00"
    else:
        date_str = None
    links = extract_mtproto_links(text, getattr(msg, "entities", None))
    return Post(channel=channel, id=int(msg.id), date=date_str, text=text, links=links)


class MTProtoReader:
    """Lazy single-session Telethon reader for channels that disable `t.me/s/` web preview."""

    def __init__(self, api_id: int | None = None, api_hash: str | None = None, session: str | None = None):
        self.api_id = int(api_id or os.getenv("TG_API_ID") or config.TG_API_ID or 0)
        self.api_hash = api_hash or os.getenv("TG_API_HASH") or config.TG_API_HASH or ""
        self.session = session or os.getenv("TG_STRING_SESSION") or config.TG_STRING_SESSION or ""
        self._client = None

    def available(self) -> bool:
        return bool(self.api_id and self.api_hash and self.session)

    def _ensure_connected(self):
        if self._client is not None:
            return self._client
        if not self.available():
            return None
        try:
            from telethon.sessions import StringSession
            from telethon.sync import TelegramClient
        except ImportError:
            log.warning("telethon not installed; cannot read MTProto-only Telegram channels")
            return None
        client = TelegramClient(StringSession(self.session), self.api_id, self.api_hash)
        client.connect()
        if not client.is_user_authorized():
            client.disconnect()
            log.warning("TG_STRING_SESSION is not authorized; skipping MTProto channels")
            return None
        self._client = client
        return self._client

    def iter_channel(self, channel: str, stop_id: int = 0, since_iso: str = "2026-03-01",
                     limit: int = 200) -> list[Post]:
        """Return newest-filtered posts > stop_id and >= since_iso via Telethon MTProto."""
        client = self._ensure_connected()
        if client is None:
            return []
        posts: list[Post] = []
        try:
            entity = client.get_entity(channel)
            kw = {"limit": limit}
            if stop_id > 0:
                kw["min_id"] = stop_id
            for msg in client.iter_messages(entity, **kw):
                post = message_to_post(channel, msg)
                if post is None:
                    continue
                if post.date and post.date[:len(since_iso)] < since_iso:
                    break
                posts.append(post)
            _time.sleep(0.8)
        except Exception as e:
            log.warning("%s MTProto read failed: %s", channel, e)
        return posts

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "MTProtoReader":
        return self

    def __exit__(self, *args) -> None:
        self.close()
