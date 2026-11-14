"""Recognise a vacancy announcement in a post and pull out its fields."""
from __future__ import annotations

import re
from dataclasses import dataclass

_IS_VACANCY = re.compile(r"شواغر|شاغر")
_ENTITY = re.compile(r"(?:تُ?عل[ِ]?ن|تعلن)\s+وزارة التنمية الإدارية[،,]?\s*(?:بالتنسيق مع\s*)?(.+?)\s+عن\s+(?:توفر|توافر|حاجتها)", re.S)
_DEADLINE = re.compile(r"آخر موعد[^\d]{0,60}?(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})")
_HIRING = re.compile(r"https?://bit\.ly/hiring[\w-]+|https?://moad\.gov\.sy/recruitment/jobs\?group=\d+")
_APPLY = re.compile(r"https?://bit\.ly/applying[\w-]+")
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
    """Return an Announcement if the post announces vacancies with a details link, else None."""
    text = post.text
    links = " ".join(post.links) + " " + text
    hiring = _HIRING.search(links)
    if not _IS_VACANCY.search(text) or not hiring:
        return None
    entity = ""
    if m := _ENTITY.search(text):
        entity = re.sub(r"\s+", " ", m.group(1)).strip(" ،,")
    deadline = None
    if m := _DEADLINE.search(text):
        d, mo, y = m.groups()
        deadline = f"{y}-{int(mo):02d}-{int(d):02d}"
    apply = _APPLY.search(links)
    govs = _GOVS.search(text)
    return Announcement(
        post_id=post.id, channel=post.channel, posted_at=post.date, entity=entity or "جهة عامة",
        deadline=deadline, hiring_url=hiring.group(0).rstrip("-"), apply_url=apply.group(0).rstrip("-") if apply else None,
        governorates_text=govs.group(1).strip() if govs else "", text=text,
    )
