"""The picture WhatsApp and Telegram show when someone shares a job link (1200×630, Open Graph size).

Drawn with Pillow. Arabic needs text shaping (libraqm + FriBiDi); where that isn't available the function returns None
and the page falls back to the plain logo, so a missing library never breaks sharing.
"""
from __future__ import annotations

import io
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from . import taxonomy as tx
from .arabic import has_arabic

W, H = 1200, 630
_FONTS = Path(__file__).parent / "assets" / "fonts"
_BG = (11, 28, 22)
_PANEL = (18, 44, 35)
_MINT = (45, 184, 138)
_WHITE = (245, 250, 247)
_MUTED = (170, 196, 186)
_CACHE: OrderedDict[tuple, bytes] = OrderedDict()
_CACHE_MAX = 300


@lru_cache(maxsize=1)
def available() -> bool:
    try:
        from PIL import features
        return bool(features.check("raqm")) and (_FONTS / "IBMPlexSansArabic-700.ttf").exists()
    except Exception:
        return False


@lru_cache(maxsize=16)
def _font(name: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(_FONTS / name), size, layout_engine=ImageFont.Layout.RAQM)


def _width(draw, text: str, font) -> float:
    return draw.textlength(text, font=font, direction="rtl", language="ar")


def _wrap(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if _width(draw, trial, font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and _width(draw, last + "…", font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


_MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول", "تشرين الأول",
           "تشرين الثاني", "كانون الأول"]


def _date_ar(iso: str) -> str:
    """2026-12-01 → «1 كانون الأول» (digits in a right-to-left line would otherwise come out reversed)."""
    try:
        y, m, dd = (int(x) for x in iso[:10].split("-"))
        return f"{dd} {_MONTHS[m - 1]}"
    except (ValueError, IndexError):
        return ""


def _rtext(draw, xy_right: tuple[int, int], text: str, font, fill):
    """Right-aligned; Arabic lines are laid out right-to-left, English ones left-to-right (so «…» lands at the end)."""
    rtl = has_arabic(text)
    draw.text(xy_right, text, font=font, fill=fill, direction="rtl" if rtl else "ltr",
              language="ar" if rtl else "en", anchor="ra")


def _pill(draw, right: int, top: int, text: str, font, fill, bg) -> int:
    """A rounded label ending at `right`; returns where the next one should end."""
    w = int(_width(draw, text, font)) + 44
    draw.rounded_rectangle((right - w, top, right, top + 58), radius=29, fill=bg)
    _rtext(draw, (right - 22, top + 10), text, font, fill)
    return right - w - 14


def render(job: dict, site_host: str = "") -> bytes | None:
    """PNG bytes for this job, or None if Arabic shaping isn't available on this machine."""
    if not available():
        return None
    key = (job.get("id"), job.get("title"), tuple(job.get("cities") or []), job.get("education"), job.get("deadline"),
           job.get("employer"), site_host)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=36, fill=_PANEL)
    # brand
    d.rounded_rectangle((W - 150, 78, W - 86, 142), radius=18, fill=_MINT)
    d.rounded_rectangle((W - 135, 100, W - 101, 128), radius=5, outline=_BG, width=4)
    d.rectangle((W - 126, 93, W - 110, 101), outline=_BG, width=4)
    _rtext(d, (W - 168, 70), "لقيت", _font("Alexandria-800.ttf", 54), _MINT)
    sector = {"gov": "جهة حكومية", "ngo": "منظمة"}.get(job.get("sector") or "", "")
    if job.get("official"):
        sector = "وظيفة رسمية"
    if sector:
        d.text((86, 86), sector, font=_font("IBMPlexSansArabic-700.ttf", 30), fill=_MUTED, direction="rtl",
               language="ar", anchor="la")
    # title
    title_font = _font("IBMPlexSansArabic-700.ttf", 66)
    y = 182
    for line in _wrap(d, (job.get("title") or "").strip(), title_font, W - 190, 2):
        _rtext(d, (W - 86, y), line, title_font, _WHITE)
        y += 90
    emp = (job.get("employer") or "").strip()
    if emp:
        sub_font = _font("IBMPlexSansArabic-400.ttf", 38)
        _rtext(d, (W - 86, y + 6), _wrap(d, emp, sub_font, W - 190, 1)[0], sub_font, _MUTED)
        y += 64
    # facts
    pill_font = _font("IBMPlexSansArabic-700.ttf", 30)
    right, top = W - 86, min(max(y + 22, 380), 436)
    cities = job.get("cities") or []
    facts = []
    if cities:
        facts.append("📍 " + "، ".join(cities[:2]))
    facts.append(tx.EDU_LABEL.get(job.get("education") or "", "الشهادة غير مذكورة"))
    if set(job.get("apply_types") or []) & {"whatsapp"}:
        facts.append("تقديم عالواتساب")
    if job.get("deadline") and (when := _date_ar(str(job["deadline"]))):
        facts.append(f"آخر موعد {when}")
    for f in facts:
        f = f.replace("📍 ", "")
        w = int(_width(d, f, pill_font)) + 44
        if right - w < 86:
            break
        right = _pill(d, right, top, f, pill_font, _WHITE, (30, 66, 53))
    # footer
    foot = _font("IBMPlexSansArabic-700.ttf", 32)
    _rtext(d, (W - 86, H - 104), "قدّم مجاناً على لقيت", foot, _MINT)
    if site_host:
        d.text((86, H - 100), site_host, font=_font("IBMPlexSansArabic-400.ttf", 28), fill=_MUTED, anchor="la")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    _CACHE[key] = data
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return data
