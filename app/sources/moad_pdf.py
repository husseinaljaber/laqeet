"""Read the ministry's vacancy-card PDFs («بطاقات وصف الشواغر») into structured cards.

What the real files look like (checked on 12 PDFs, Oct–Nov 2026):
  * every card is a drawn table: a dark header row of labels (المسمى الوظيفي، محافظة العمل، الفئة الوظيفية،
    المستوى التعليمي، الاختصاص العام المفضل، الاختصاص الدقيق المفضل، سنوات الخبرة، العمر المفضل), one row of
    values, then a «المهام الرئيسية» row and a «المهارات المفضلة» row;
  * plain text extraction scrambles the order, so we read the table geometry instead (PyMuPDF find_tables) and
    put each cell's characters back together ourselves;
  * the font's lam-alef ligatures (لا لأ لإ لآ لى) come out as two characters in the wrong order, e.g. «إعالمي»
    for «إعلامي» and «األوىل» for «الأولى». The first of the two has zero width, which is how we spot and fix them.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .. import taxonomy as tx
from ..arabic import soft_fold

_LIG_FIRST = set("اأإآى")
_LABELS = {
    "title": "المسمى الوظيفي", "governorates": "محافظة العمل", "grade": "الفئة الوظيفية", "level": "المستوى التعليمي",
    "general": "الاختصاص العام", "precise": "الاختصاص الدقيق", "experience": "سنوات الخبرة", "age": "العمر المفضل",
}
_GRADE_RX = [(re.compile(r"اول|اولي|الاو"), "1"), (re.compile(r"ثان"), "2"), (re.compile(r"ثالث"), "3"), (re.compile(r"رابع"), "4"),
             (re.compile(r"خامس"), "5")]
_LEAD = re.compile(soft_fold(r"^(مدير|معاون مدير|رئيس (دائره|شعبه|قسم|مركز|وحده|مكتب))"))


@dataclass
class Card:
    title: str
    governorates: list[str] = field(default_factory=list)
    grade: str | None = None          # "1".."5"
    level_text: str = ""              # «دكتوراه - ماجستير - إجازة جامعية»
    education: str | None = None      # lowest level the card accepts (taxonomy code)
    general: str = ""                 # general specialty
    precise: str = ""                 # precise specialty
    experience: int | None = None
    age_min: int | None = None
    age_max: int | None = None
    tasks: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    notes: str = ""                   # «ملاحظات للمتقدم»
    leadership: bool = False
    page: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _is_mark(c: str) -> bool:
    return "\u064B" <= c <= "\u0652" or c == "\u0670"


def _zero(c) -> bool:
    return c["bbox"][2] - c["bbox"][0] < 0.05


def _rehome_orphans(lines) -> None:
    """Move a zero-width final letter that the PDF wrote at the start of another line back after the letter it
    sits on: «باحث ف» + «ي الكيمياء» → «باحث في» + «الكيمياء»."""
    for li, line in enumerate(lines):
        if not line or not _zero(line[0]) or not line[0]["c"].strip() or _is_mark(line[0]["c"]):
            continue
        if len(line) > 1 and line[1]["c"].strip():
            continue
        o = line[0]
        x, y = o["bbox"][0], o["bbox"][1]
        for lj, other in enumerate(lines):
            if lj == li:
                continue
            hit = next((k for k, c in enumerate(other) if c["c"].strip() and not _zero(c)
                        and c["bbox"][0] - 0.5 <= x <= c["bbox"][2] + 0.5 and abs(c["bbox"][1] - y) < 8), None)
            if hit is not None:
                other.insert(hit + 1, o)
                del line[0]
                break


# Words whose last letter the PDF font drops (a two-letter glyph reported as one letter).
_LOST_TAIL = [(re.compile(r"\bالفن(?=\s|$)"), "الفني"), (re.compile(r"\bالعاملي(?=\s|$)"), "العاملين"),
              # glyphs the font maps to the wrong letters (يي→بر، ين→ير، بي→نر)
              (re.compile(r"\bطنر\b"), "طبي"), (re.compile(r"المعايبر"), "المعايير"), (re.compile(r"\bتحسير\b"), "تحسين"),
              (re.compile(r"\bالمخبر ية\b"), "المخبرية"), (re.compile(r"أخصائ (.*?) ي\b"), r"أخصائي \1"),
              (re.compile(r"\bتقن\b"), "تقني"), (re.compile(r"\bأخصان ?ي\b|\bأخصاني\b|\bأخصانٍ"), "أخصائي"),
              (re.compile(r"\bممُ?دخل\b"), "مدخل"), (re.compile(r"\bثان(\s*\(.*\))\s*ي$"), r"ثاني\1"),
              (re.compile(r"\(\s+"), "("), (re.compile(r"(?<=[^\s(])\("), " ("), (re.compile(r"\bو (?=\w)"), "و")]


_CITY_ALT = "|".join(re.escape(c) for c in tx.CITIES + ["حماه", "اللاذقيه", "ادلب", "الحسكه", "الرقه", "القنيطره"])
# «(جرار زراع)يدرعا» → «(جرار زراعي)»: the final ي of the word before «)» lands after it, glued to the governorate.
_TAIL_IN_PAREN = re.compile(r"(\S)\)\s*ي(?:\s*(?:" + _CITY_ALT + r"))?\s*$")
_CITY_AFTER_PAREN = re.compile(r"\)\s*(?:" + _CITY_ALT + r")\s*$")
_LOST_YA_BEFORE_PAREN = re.compile(r"\b(فن|مهن)\s*\(")


def fix_lost_tails(s: str) -> str:
    for rx, rep in _LOST_TAIL:
        s = rx.sub(rep, s)
    s = _TAIL_IN_PAREN.sub(r"\1ي)", s)
    s = _CITY_AFTER_PAREN.sub(")", s)
    s = _LOST_YA_BEFORE_PAREN.sub(r"\1ي (", s)
    return s


def _chars(page) -> list[tuple[str, float, float, int, float]]:
    """Every character on the page as (char, x-centre, y-centre, reading order), with ligatures put right.

    A ligature glyph (لا، لأ، لى، مى …) is emitted as two characters in the wrong order, and the first one has
    zero width. Swapping such a pair restores the word: «إعالمي» → «إعلامي», «األوىل» → «الأولى».
    """
    out: list[tuple[str, float, float, int]] = []
    seq = 0
    lines = [[c for span in line["spans"] for c in span["chars"]]
             for block in page.get_text("rawdict")["blocks"] for line in block.get("lines", [])]
    _rehome_orphans(lines)
    for chars in lines:
        if True:
            i = 0
            while i < len(chars):
                c = chars[i]
                prev = chars[i - 1] if i else None
                # Fake spaces inside words: a zero-width one («الفن ي» → «الفني»), or one that sits at the same raised
                # height as the zero-width glyph before it («الإشر اف» → «الإشراف»).
                fake_space = c["c"] == " " and i + 1 < len(chars) and chars[i + 1]["c"].strip() and (
                    c["bbox"][2] - c["bbox"][0] < 0.05
                    or (prev and prev["bbox"][2] - prev["bbox"][0] < 0.05 and prev["c"].strip()
                        and abs(c["bbox"][1] - prev["bbox"][1]) < 1 and abs(c["bbox"][1] - chars[0]["bbox"][1]) > 2))
                if _is_mark(c["c"]) or fake_space:
                    i += 1  # harakat, and the fake space some stacked glyphs (ـشر) leave behind
                    continue
                nxt = chars[i + 1] if i + 1 < len(chars) else None
                if nxt and c["bbox"][2] - c["bbox"][0] < 0.05 and c["c"].strip() and nxt["c"].strip() and not _is_mark(nxt["c"]):
                    pair = [nxt, c]
                    i += 2
                else:
                    pair = [c]
                    i += 1
                for ch in pair:
                    b = ch["bbox"]
                    out.append((ch["c"], (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, seq, b[2] - b[0]))
                    seq += 1
            out.append((" ", 0, 0, seq, 0))  # line break marker (no position: never inside a cell)
            seq += 1
    return out


def fix_text(s: str) -> str:
    """Repair ligature order in text that has already lost its geometry (best effort, used as a fallback)."""
    s = re.sub(r"ىل", "لى", s)
    s = re.sub(r"(?<=\w)([أإآ])ل", r"ل\1", s)
    return s


def _words(chars) -> list[tuple[str, float, float, int]]:
    """Group the character stream into words placed at the median of their letters.

    A few final-form glyphs (ـر، ـي، ـن) report positions far from the rest of the word, so placing whole words
    instead of single letters keeps «الإشراف» in one cell instead of «الإش اف» here and a stray «ر» elsewhere.
    """
    words, cur = [], []

    def flush():
        if cur:
            solid = [c for c in cur if c[4] > 0.05] or cur
            xs = sorted(c[1] for c in solid)
            ys = sorted(c[2] for c in solid)
            words.append(("".join(c[0] for c in cur), xs[len(xs) // 2], ys[len(ys) // 2], cur[0][3]))
            cur.clear()

    for c in chars:
        if not c[0].strip():
            flush()
        else:
            # zero-width glyphs (parts of ligatures, final ـي) report unreliable positions: never split on them
            if cur and c[4] > 0.05 and cur[-1][4] > 0.05 and (abs(c[1] - cur[-1][1]) > 25 or abs(c[2] - cur[-1][2]) > 8):
                flush()  # letters glued across a cell border («التخصصات5»)
            cur.append(c)
    flush()
    return words


def _in(bbox, x, y) -> bool:
    return bbox[0] - 0.5 <= x <= bbox[2] + 0.5 and bbox[1] - 0.5 <= y <= bbox[3] + 0.5


def _cell_lines(words, bbox) -> list[str]:
    """The text lines inside one cell, top to bottom, words in reading order."""
    rows: dict[float, list[tuple[int, str]]] = {}
    for w, x, y, seq in words:
        if _in(bbox, x, y):
            key = next((k for k in rows if abs(k - y) < 4), y)
            rows.setdefault(key, []).append((-x, w))  # right to left, as the reader sees it
    return [" ".join(w for _, w in sorted(rows[y])) for y in sorted(rows)]


def _cell_text(words, bbox) -> str:
    return fix_lost_tails(" ".join(_cell_lines(words, bbox)).strip())


_BROKEN = re.compile(r"[\u1400-\u1DFF\u1680\u173b\uE000-\uF8FF]")


def _ocr(page, bbox, psm: int = 6) -> str | None:
    """Read one cell with Tesseract (Arabic) when its font lost the letters. None if Tesseract isn't installed."""
    import shutil
    import subprocess

    if not shutil.which("tesseract"):
        return None
    import pymupdf

    pix = page.get_pixmap(clip=pymupdf.Rect(*bbox), dpi=300)
    try:
        out = subprocess.run(["tesseract", "stdin", "stdout", "-l", "ara", "--psm", str(psm)], input=pix.tobytes("png"),
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = out.stdout.decode("utf-8", "ignore")
    return re.sub(r"\s+", " ", text).strip() or None


class _Reader:
    """Cell text for one page, falling back to OCR for cells drawn with a font that lost its letters."""

    def __init__(self, page, words):
        self.page, self.words = page, words
        self.ocr_used = 0
        # A page drawn with a broken font loses letters even in cells that show no odd glyphs: read all of it by OCR.
        self.broken = sum(bool(_BROKEN.search(w)) for w, *_ in words) > 15

    def text(self, bbox, psm: int = 6) -> str:
        t = _cell_text(self.words, bbox)
        if (self.broken or _BROKEN.search(t)) and t.strip() and (o := _ocr(self.page, bbox, psm)):
            self.ocr_used += 1
            return fix_lost_tails(o)
        return _BROKEN.sub("", t)

    def lines(self, bbox) -> list[str]:
        ls = _cell_lines(self.words, bbox)
        if (self.broken or any(_BROKEN.search(l) for l in ls)) and ls and (o := _ocr(self.page, bbox)):
            self.ocr_used += 1
            return [x.strip() for x in re.split(r"(?<=[.؛])\s+", o) if x.strip()]
        return [_BROKEN.sub("", l) for l in ls]


def _grid(page):
    """Vertical and horizontal ruling lines of the card tables."""
    vert, horiz = [], []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width < 1.5 and r.height > 15:
            vert.append((r.x0, r.y0, r.y1))
        elif r.height < 1.5 and r.width > 300:
            horiz.append(r.y0)
        elif d.get("items"):
            for it in d["items"]:  # lines drawn as paths
                if it[0] == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.x - p2.x) < 1 and abs(p1.y - p2.y) > 15:
                        vert.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))
                    elif abs(p1.y - p2.y) < 1 and abs(p1.x - p2.x) > 300:
                        horiz.append(p1.y)
    horiz = sorted({round(y, 0) for y in horiz})
    return vert, horiz


def _xs_at(vert, y) -> list[float]:
    return sorted({round(x, 1) for x, y0, y1 in vert if y0 - 1 <= y <= y1 + 1})


_KEYS = [("title", r"المس.{0,3}الوظيف|المسمي"), ("governorates", r"محافظ"), ("grade", r"الفئ"), ("level", r"المستو|التعليم"),
         ("precise", r"الدقيق"), ("general", r"العام"), ("experience", r"الخبر"), ("age", r"العمر")]


def _key(label: str) -> str | None:
    f = soft_fold(label).replace(" ", "")
    for k, pat in _KEYS:
        if re.search(pat.replace(" ", ""), f):
            return k
    return None


def _fill(card: Card, key: str, value: str) -> None:
    v = value.strip()
    if key == "title":
        card.title = v
    elif key == "governorates":
        card.governorates = tx.cities_from_text(v)
    elif key == "grade":
        f = soft_fold(v)
        card.grade = next((g for rx, g in _GRADE_RX if rx.search(f)), None)
    elif key == "level":
        card.level_text = v
    elif key == "general":
        card.general = "" if v in ("-", "_", "–") else v
    elif key == "precise":
        card.precise = "" if v in ("-", "_", "–") else v
    elif key == "experience":
        m = re.search(r"\d{1,2}", v)
        card.experience = int(m.group(0)) if m else (0 if re.search(soft_fold("لا يشترط|بدون"), soft_fold(v)) else None)
    elif key == "age":
        nums = sorted(int(n) for n in re.findall(r"\d{2}", v))
        if len(nums) >= 2 and 15 <= nums[0] < nums[-1] <= 70:
            card.age_min, card.age_max = nums[0], nums[-1]


def _boxes(page):
    """Filled rectangles: the dark header bands and the right-hand label cells."""
    heads, right = [], []
    for d in page.get_drawings():
        r, fill = d["rect"], d.get("fill")
        if not fill or d["type"] not in ("f", "fs"):
            continue
        dark = sum(fill) < 0.8 and fill[1] > fill[0]  # the ministry's dark green
        if dark and r.width > 500 and 20 < r.height < 60:
            heads.append((r.x0, r.y0, r.x1, r.y1))
        elif r.width < 260 and r.x0 > page.rect.width * 0.75 and r.height > 15:
            right.append((r.x0, r.y0, r.x1, r.y1))
    return sorted(heads, key=lambda b: b[1]), sorted(right, key=lambda b: b[1])


# Column borders of the ministry's card template (from the files that do draw ruling lines), as fractions of
# the header width, right to left: العمر | الخبرة | الدقيق | العام | المستوى | الفئة | المحافظة | المسمى
# Files drawn without ruling lines use a slightly different layout (measured from the label positions).
_TEMPLATE = [19.0, 100.0, 210.0, 415.0, 625.0, 785.0, 901.0, 1046.0, 1210.2]


def _template_columns(head) -> list[tuple[float, float]]:
    x0, _, x1, _ = head
    a, b = _TEMPLATE[0], _TEMPLATE[-1]
    xs = [x0 + (t - a) / (b - a) * (x1 - x0) for t in _TEMPLATE]
    return list(zip(xs, xs[1:]))


def _label_columns(words, head) -> list[tuple[float, float]]:
    """Column borders from the label positions, for files drawn without ruling lines."""
    x0, y0, x1, y1 = head
    ws = sorted((x, w) for w, x, y, _ in words if x0 <= x <= x1 and y0 <= y <= y1)
    clusters: list[list[float]] = []
    for x, _ in ws:
        if clusters and x - clusters[-1][-1] < 30:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    centers = [(c[0] + c[-1]) / 2 for c in clusters]
    edges = [x0] + [(a + b) / 2 for a, b in zip(centers, centers[1:])] + [x1]
    return list(zip(edges, edges[1:]))


_ROW_LABEL = re.compile(soft_fold(r"المهام|المهارات|ملاحظات"))


def _rows_from_labels(words, lab_x0, lab_x1, top, bottom) -> list[float]:
    """Row borders for files without ruling lines: each row's text is centred on its label in the right column."""
    labels = sorted({round(y) for w, x, y, _ in words if lab_x0 <= x <= lab_x1 and top < y < bottom and _ROW_LABEL.search(soft_fold(w))})
    labels = [y for k, y in enumerate(labels) if k == 0 or y - labels[k - 1] > 10]
    if not labels:
        return []
    body = sorted({round(y) for w, x, y, _ in words if x < lab_x0 and top < y < bottom})
    title_ys = [y for w, x, y, _ in words if lab_x0 <= x <= lab_x1 and top < y < labels[0] - 6]
    anchors = [sum(title_ys) / len(title_ys) if title_ys else top + 20] + labels
    borders = []
    for a, b in zip(anchors, anchors[1:]):
        # The border is the widest gap between text lines lying between the two anchors.
        between = [y for y in body if a < y < b]
        pts = [a] + between + [b]
        gaps = [(pts[k + 1] - pts[k], (pts[k + 1] + pts[k]) / 2) for k in range(len(pts) - 1)]
        borders.append(max(gaps)[1] if gaps else (a + b) / 2)
    return borders


def parse_page(page, page_no: int = 0) -> list[Card]:
    words = _words(_chars(page))
    rd = _Reader(page, words)
    vert, _ = _grid(page)
    heads, right = _boxes(page)
    cards: list[Card] = []
    for n, head in enumerate(heads):
        hx0, top, hx1, bottom = head
        next_top = heads[n + 1][1] if n + 1 < len(heads) else page.rect.height
        xs = _xs_at(vert, (top + bottom) / 2)
        cols = list(zip(xs, xs[1:])) if len(xs) >= 6 else _template_columns(head)
        labels = [_key(rd.text((x0, top, x1, bottom))) for x0, x1 in cols]
        if len(xs) < 6 and "title" not in labels:  # unruled file: the template's column order is known
            labels = ["age", "experience", "precise", "general", "level", "grade", "governorates", "title"]
        if "title" not in labels:
            continue
        lab_x0 = cols[-1][0]  # the title column is the rightmost one; row labels sit in the same column below
        _, horiz = _grid(page)
        ys = sorted({round(y) for y in horiz if bottom + 8 < y < next_top - 2})
        ys = [y for k, y in enumerate(ys) if k == 0 or y - ys[k - 1] > 6]
        if not ys:
            ys = _rows_from_labels(words, lab_x0, hx1, bottom, next_top)
        if not ys:
            continue
        vtop, vbottom = bottom, ys[0]
        card = Card(title="", page=page_no)
        vxs = _xs_at(vert, (vtop + vbottom) / 2)
        vcols = list(zip(vxs, vxs[1:])) if len(vxs) == len(xs) and len(xs) >= 6 else cols
        for (x0, x1), key in zip(vcols, labels):
            if key:
                # Short number cells read better as a single line of text.
                _fill(card, key, rd.text((x0, vtop, x1, vbottom), 7 if key in ("experience", "age", "grade") else 6))
        if not card.grade:  # OCR sometimes pushes «الفئة الأولى» into the next column
            _fill(card, "grade", card.level_text + " " + card.governorates_text if hasattr(card, "governorates_text") else card.level_text)
        # Rows below the values: a label on the right (ملاحظات / المهام الرئيسية / المهارات المفضلة), text to its left.
        current = None
        for rtop, rbottom in zip(ys, ys[1:] + [next_top]):
            label = soft_fold(rd.text((lab_x0, rtop, hx1 + 2, rbottom)))
            if soft_fold("المهام") in label:
                current = "tasks"
            elif soft_fold("المهارات") in label:
                current = "skills"
            elif soft_fold("ملاحظات") in label:
                current = "notes"
            elif label:
                current = None
            if not current:
                continue
            lines = [t.strip(" .") for t in rd.lines((hx0, rtop, lab_x0, rbottom)) if len(t.strip(" .")) > 3]
            if current == "notes":
                card.notes = (card.notes + " " + " ".join(lines)).strip()
            else:
                getattr(card, current).extend(lines)
        if not card.title:
            continue
        card.leadership = bool(_LEAD.match(soft_fold(card.title)))
        card.education = tx.education_from_text(card.level_text) or tx.MOAD_GRADE_EDU.get(card.grade or "")
        cards.append(card)
    return cards


def parse_pdf(path: str) -> list[Card]:
    import pymupdf

    cards: list[Card] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            cards += parse_page(page, i + 1)
    return cards
