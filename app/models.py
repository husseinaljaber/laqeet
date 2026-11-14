"""The one shape every job takes, whatever source it came from."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field


@dataclass
class Job:
    title: str
    source: str                      # moad | tawzeef | wazafne | syrianjobs | employer
    post_url: str                    # where the ad was published (always shown, so people can check it)
    posted_at: str | None = None     # ISO timestamp
    employer: str | None = None
    cities: list[str] = dc_field(default_factory=list)
    education: str | None = None     # taxonomy.EDUCATION code; None = the ad doesn't say
    education_from: str = "unknown"  # stated | grade | title | source | unknown: how sure we are
    grade: str | None = None         # government grade 1..5 when the ad gives one
    field: str = "other"             # taxonomy.FIELDS code
    subfield: str | None = None      # for computer jobs: SWE, CYBER, AI, DATA, NET, DT, SUPPORT, EDU
    specialty: str | None = None     # degree subjects named in the ad
    experience: int | None = None    # years
    age_min: int | None = None
    age_max: int | None = None
    gender: str | None = None        # male | female | None
    shift: str | None = None
    salary: str | None = None
    deadline: str | None = None      # YYYY-MM-DD
    kind: str = "formal"             # formal (apply by deadline) | quick (call/WhatsApp, fills fast)
    apply_url: str | None = None
    phones: list[str] = dc_field(default_factory=list)
    whatsapp: bool = False
    email: str | None = None
    details_url: str | None = None   # job description file, e.g. the ministry's PDF
    official: bool = False           # published by a government body
    spam: bool = False
    tasks: list[str] = dc_field(default_factory=list)    # «المهام الرئيسية» (ministry cards)
    skills: list[str] = dc_field(default_factory=list)   # «المهارات المفضلة»
    tags: list[str] = dc_field(default_factory=list)     # extra chips (e.g. Turkey work-permit/language/remote)
    country: str = "sy"              # sy | tr
    sector: str = "private"          # gov | ngo | private
    apply_types: list[str] = dc_field(default_factory=list)  # whatsapp | form | site | email | call
    confidence: float = 1.0          # 0..1, low ones go to the review queue
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
