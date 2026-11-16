"""Recognise a vacancy announcement in a post and pull out its fields."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import direct_apply as da

_IS_VACANCY = re.compile(r"شواغر|شاغر")
_ENTITY = re.compile(r"(?:تُ?عل[ِ]?ن|تعلن)\s+وزارة التنمية الإدارية[،,]?\s*(?:بالتنسيق مع\s*)?(.+?)\s+عن\s+(?:توفر|توافر|حاجتها)", re.S)
_DEADLINE = re.compile(r"آخر موعد[^\d]{0,60}?(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})")
_HIRING = re.compile(
    r"(?:https?://)?bit\.ly/hiring[\w-]+|https?://moad\.gov\.sy/recruitment/jobs\?group=\d+",
    re.I,
)
_GOVS = re.compile(r"(?:المحافظات المطلوب العمل فيها|محافظة العمل|المحافظة المطلوب العمل فيها|محافظات المطلوب العمل فيها)\s*:?\s*(.+?)(?:\n|\.|-\s)")


@dataclass
class Announcement:
    post_id: int
    channel: str
    posted_at: str | None
    entity: str
    deadline: str | None  # YYYY-MM-DD
    hiring_url: str | None
    apply_url: str | None
    governorates_text: str
    text: str

    @property
    def post_url(self) -> str:
        return f"https://t.me/{self.channel}/{self.post_id}"


def parse(post) -> Announcement | None:
    """Return an Announcement if the post announces vacancies with a details or direct apply link, else None."""
    text = post.text
    if not _IS_VACANCY.search(text):
        return None
    post_links = list(getattr(post, "links", None) or [])
    cleaned_hay = da._RTL_CONTROL_RE.sub("", " ".join(post_links) + " " + text)
    hiring_m = _HIRING.search(cleaned_hay)
    hiring_url = da.clean_url(hiring_m.group(0)) if hiring_m else None

    gov_links = da.extract_gov_links(text, extra_links=post_links)
    apply_url = gov_links["best_apply_url"] or None
    if not hiring_url and gov_links["details_url"]:
        hiring_url = str(gov_links["details_url"])

    if not hiring_url and not apply_url:
        return None

    entity = ""
    if m := _ENTITY.search(text):
        entity = re.sub(r"\s+", " ", m.group(1)).strip(" ،,")
    deadline = None
    if m := _DEADLINE.search(text):
        d, mo, y = m.groups()
        deadline = f"{y}-{int(mo):02d}-{int(d):02d}"
    govs = _GOVS.search(text)
    return Announcement(
        post_id=post.id,
        channel=post.channel,
        posted_at=post.date,
        entity=entity or "جهة عامة",
        deadline=deadline,
        hiring_url=hiring_url,
        apply_url=str(apply_url) if apply_url else None,
        governorates_text=govs.group(1).strip() if govs else "",
        text=text,
    )
