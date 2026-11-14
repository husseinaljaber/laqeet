"""Read posts from a public Telegram channel through its web preview (t.me/s/<channel>).

No Telegram account or bot token is needed: the preview page is public HTML.
Each request returns about 20 posts; `?before=<id>` pages backwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import httpx
from bs4 import BeautifulSoup

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
