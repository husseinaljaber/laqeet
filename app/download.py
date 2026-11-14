"""Follow a bit.ly link to the Google Drive file it points at and download the PDF."""
from __future__ import annotations

import re
from pathlib import Path

import httpx

_DRIVE_ID = re.compile(r"/d/([\w-]{20,})|[?&]id=([\w-]{20,})")


def resolve_drive_id(short_url: str, client: httpx.Client) -> str | None:
    url = short_url
    for _ in range(5):  # follow redirects by hand so we can read the Drive id from the Location header
        if m := _DRIVE_ID.search(url):
            return m.group(1) or m.group(2)
        r = client.get(url, follow_redirects=False)
        if r.status_code not in (301, 302, 303, 307, 308):
            return None
        url = r.headers["location"]
    return None


def download_pdf(drive_id: str, dest: Path, client: httpx.Client) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://drive.usercontent.google.com/download?id={drive_id}&export=download&confirm=t"
    r = client.get(url, follow_redirects=True)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"Drive file {drive_id} did not return a PDF (got {r.headers.get('content-type')})")
    dest.write_bytes(r.content)
    return dest
