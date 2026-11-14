"""Fast, Arabic-aware FTS5 search, ranking, synonym expansion, autocomplete, and typo suggestions."""
from __future__ import annotations

from datetime import date
import difflib
import json
import re
import sqlite3
from collections import Counter

from .arabic import search_fold

_SECTOR_WORDS = {
    "gov": "حكومي وزارة",
    "ngo": "منظمة منظمات انساني",
    "private": "خاص",
}

_URL_RE = re.compile(r"https?://\S+", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9_+.]+|[أ-يءآإؤئ]+")
_ARABIC_WORD_RE = re.compile(r"^[أ-ي]+$")

# Common Syrian Arabic / English job equivalence groups (expanded symmetrically at query time)
_SYNONYM_GROUPS: list[list[str]] = [
    ["مبرمج", "برمجة", "مطور", "تطوير", "developer", "programmer", "software", "swe"],
    ["فرونت", "واجهات امامية", "واجهات ويب", "frontend", "front-end", "react", "vue", "angular", "next"],
    ["باك", "backend", "laravel", "php", "django", "python", "node", "dotnet"],
    ["it", "تقنية المعلومات", "معلوماتية", "دعم فني", "helpdesk", "sysadmin"],
    ["ai", "ذكاء اصطناعي", "تعلم الالة", "machine learning"],
    ["شبكات", "network", "mikrotik", "cisco", "ccna", "اتصالات"],
    ["محاسب", "محاسبة", "مالية", "مدقق", "accountant", "accounting", "finance"],
    ["مندوب", "مبيعات", "تسويق", "مروج", "sales", "marketing"],
    ["ديجيتال", "سوشيال ميديا", "صانع محتوى", "تسويق الكتروني", "digital marketing", "social media", "seo"],
    ["مصمم", "تصميم", "غرافيك", "جرافيك", "graphic", "designer", "photoshop", "illustrator", "مونتاج"],
    ["ui", "ux", "تجربة المستخدم", "واجهة المستخدم"],
    ["دليفري", "ديليفري", "توصيل", "موصل", "موزع", "delivery", "courier"],
    ["سائق", "شوفير", "سائقين", "driver"],
    ["مدير", "مدراء", "مديرين", "ادارة", "اداري", "manager", "director"],
    ["منسق", "مشرف", "coordinator", "supervisor", "officer"],
    ["طبيب", "اطباء", "دكتور", "doctor", "physician"],
    ["طب", "الطب", "طبيب", "اطباء", "دكتور", "طبية"],
    ["صيدلي", "صيدلاني", "صيدلة", "pharmacist", "pharmacy"],
    ["ممرض", "ممرضة", "تمريض", "nurse", "nursing", "قابلة"],
    ["مهندس", "مهندسين", "مهندسة", "هندسة", "engineer", "engineering"],
    ["معماري", "عمارة", "architect", "architecture"],
    ["مدني", "انشائي", "civil"],
    ["كهرباء", "كهربائي", "طاقة شمسية", "electrical"],
    ["ميكانيك", "ميكانيكي", "mechanical"],
    ["معلم", "مدرس", "استاذ", "تدريس", "محاضر", "teacher", "instructor", "trainer"],
    ["انجليزي", "انكليزي", "english"],
    ["مترجم", "ترجمة", "translator", "translation"],
    ["موارد بشرية", "شؤون موظفين", "توظيف", "hr", "human resources"],
    ["سكرتير", "سكرتيرة", "سكرتاريا", "استقبال", "receptionist"],
    ["خدمة عملاء", "دعم عملاء", "كول سنتر", "call center", "customer service"],
    ["محامي", "محاماة", "قانون", "حقوق", "lawyer", "legal"],
    ["لوجستي", "امداد", "مشتريات", "مخازن", "مستودع", "logistics", "procurement", "supply chain"],
    ["تغليف", "تعبئة", "انتاج", "تصنيع", "معمل", "مصنع"],
    ["صيانة", "فني", "حرفي", "تمديدات"],
    ["طباخ", "شيف", "مطبخ", "مطعم", "chef", "cook"],
    ["حارس", "حراسة", "امن", "security"],
    ["عن بعد", "اونلاين", "اون لاين", "من المنزل", "remote", "freelance"],
    ["منظمة", "منظمات", "اغاثة", "انساني", "ngo", "humanitarian", "meal"],
    ["سوريا", "سورية", "السوري", "syria"],
]

_BROKEN_PLURALS: dict[str, list[str]] = {
    "مدراء": ["مدير"],
    "مديرين": ["مدير"],
    "مدير": ["مدراء", "مديرين", "ادارة"],
    "اطباء": ["طبيب", "دكتور"],
    "طبيب": ["اطباء", "دكتور"],
    "مهندسين": ["مهندس", "هندسة"],
    "مهندس": ["مهندسين", "مهندسة", "هندسة"],
    "محاسبين": ["محاسب", "محاسبة"],
    "محاسب": ["محاسبين", "محاسبة"],
    "معلمين": ["معلم", "مدرس"],
    "معلم": ["معلمين", "مدرسين", "مدرس"],
    "مدرسين": ["مدرس", "معلم"],
    "مدرس": ["مدرسين", "معلم"],
    "عمال": ["عامل"],
    "عامل": ["عمال"],
    "سائقين": ["سائق", "شوفير"],
    "سائق": ["سائقين", "شوفير"],
    "مصممين": ["مصمم", "تصميم"],
    "مصمم": ["مصممين", "تصميم"],
    "مبرمجين": ["مبرمج", "برمجة", "مطور"],
    "مبرمج": ["مبرمجين", "برمجة", "مطور"],
    "ممرضين": ["ممرض", "ممرضة", "تمريض"],
    "ممرضة": ["ممرض", "ممرضين", "تمريض"],
    "صيادلة": ["صيدلي", "صيدلاني", "صيدلة"],
    "صيدلي": ["صيادلة", "صيدلاني", "صيدلة"],
    "محامين": ["محامي", "محاماة"],
    "مندوبين": ["مندوب", "مبيعات"],
    "مندوب": ["مندوبين", "مبيعات"],
    "منسقين": ["منسق"],
    "منظمات": ["منظمة", "ngo"],
    "شواغر": ["شاغر", "وظيفة"],
    "وظائف": ["وظيفة", "شاغر"],
}

# Build fast lookup maps
_SYN_MAP: dict[str, set[str]] = {}
for _grp in _SYNONYM_GROUPS:
    folded_grp = [search_fold(w) for w in _grp if search_fold(w)]
    for item in folded_grp:
        bucket = _SYN_MAP.setdefault(item, set())
        for other in folded_grp:
            if other != item:
                bucket.add(other)


def tokenize(text: str) -> list[str]:
    """Fold and extract clean alphanumeric / Arabic tokens."""
    f = search_fold(text)
    return _TOKEN_RE.findall(f) if f else []


def _strip_ar_prefixes(tok: str) -> list[str]:
    """Return candidate stems for an Arabic token (stripping ال / وال / بال / لل and regular endings)."""
    out: list[str] = []
    if not _ARABIC_WORD_RE.match(tok):
        return out
    t = tok
    if len(t) >= 5 and t[:3] in ("وال", "بال", "كال", "فال"):
        t = t[3:]
        out.append(t)
    elif len(t) >= 4 and t.startswith("ال"):
        t = t[2:]
        out.append(t)
    elif len(t) >= 4 and t.startswith("لل"):
        t = t[2:]
        out.append(t)
    # Regular plural / feminine suffixes for 5+ letter words
    base = t
    if len(base) >= 5 and base.endswith(("ين", "ون", "ات")):
        out.append(base[:-2])
    elif len(base) >= 4 and base.endswith(("ه", "ة", "ي")) and not base.endswith("يه"):
        out.append(base[:-1])
    return [x for x in dict.fromkeys(out) if len(x) >= 3]


def _augment_tokens(text: str) -> str:
    """Produce space-joined normalized stems & plural roots for auxiliary FTS indexing."""
    toks = tokenize(text)
    extra: list[str] = []
    seen: set[str] = set()
    for t in toks:
        for st in _strip_ar_prefixes(t):
            if st not in seen:
                seen.add(st)
                extra.append(st)
        for root in _BROKEN_PLURALS.get(t, ()):
            rf = search_fold(root)
            if rf and rf not in seen:
                seen.add(rf)
                extra.append(rf)
    return " ".join(extra)


def _strip_urls(text: str) -> str:
    return _URL_RE.sub(" ", text or "")


def build_fts_fields(job: dict) -> tuple[str, str, str, str, str]:
    """Extract clean, normalized search columns from a Job dict (never raw JSON keys)."""
    title = search_fold(job.get("title") or "")
    employer = search_fold(job.get("employer") or "")

    spec_parts = [
        " ".join(job.get("cities") or []),
        job.get("specialty") or "",
        job.get("subfield") or "",
        _SECTOR_WORDS.get(job.get("sector") or "", ""),
        "عن بعد" if "عن بعد" in (job.get("tags") or []) else "",
        " ".join(job.get("tags") or []),
        "ساعات سريعة تطوع" if job.get("kind") == "quick" else "",
    ]
    specs = search_fold(" ".join(p for p in spec_parts if p))

    body_parts = [
        " ".join(job.get("tasks") or []),
        " ".join(job.get("skills") or []),
        job.get("salary") or "",
        job.get("shift") or "",
        _strip_urls(job.get("raw") or ""),
    ]
    body = search_fold(" ".join(p for p in body_parts if p))
    aux = _augment_tokens(" ".join((title, employer, specs, body)))
    return title, employer, specs, body, aux


def _is_short_or_ascii(term: str) -> bool:
    """True for ASCII tokens (e.g. 'it', 'ai', 'react') and short <=3-char Arabic words (e.g. 'طب')."""
    if re.search(r"[a-z0-9]", term):
        return True
    return len(term) <= 3


def _term_pattern(term: str) -> re.Pattern:
    """Build a strict boundary regex for matching a single folded term or phrase."""
    esc = re.escape(term)
    if _is_short_or_ascii(term):
        # Never allow 'طب' inside 'مطلوب' or 'it' inside 'humanitarian'
        return re.compile(rf"(?<![a-z0-9أ-ي]){esc}(?![a-z0-9أ-ي])", re.I)
    # 4+ letter Arabic word: allow leading 'ال'/'وال'/etc. and regular suffixes, but require boundary on stem start
    if " " not in term and _ARABIC_WORD_RE.match(term):
        stem = term[2:] if len(term) >= 5 and term.startswith("ال") else term
        return re.compile(rf"(?<![أ-ي])(?:وال|بال|كال|فال|ال|لل|و)?{re.escape(stem)}(?:ة|ه|ين|ون|ات|ي|ية)?(?![أ-ي])")
    return re.compile(rf"(?<![a-z0-9أ-ي]){esc}(?![a-z0-9أ-ي])", re.I)


def expand_query_groups(q: str) -> list[dict]:
    """Turn raw user query into ordered token groups with exact + synonym variants."""
    fq = search_fold(q)
    if not fq:
        return []
    raw_tokens = tokenize(fq)
    # Filter lone 1-char noise unless meaningful like 'c' (rare)
    tokens = [t for t in raw_tokens if len(t) >= 2]
    if not tokens:
        tokens = raw_tokens
    groups: list[dict] = []

    # Check whole-phrase synonyms too (e.g. "عن بعد", "موارد بشرية", "خدمة عملاء")
    whole_syns = _SYN_MAP.get(fq, set())

    for tok in tokens:
        variants: list[str] = [tok]
        for st in _strip_ar_prefixes(tok):
            if st not in variants:
                variants.append(st)
        for bp in _BROKEN_PLURALS.get(tok, ()):
            bf = search_fold(bp)
            if bf and bf not in variants:
                variants.append(bf)
        # Word synonyms (skip attaching giant synonym lists if user typed 3+ words? Keeping clean helps recall!)
        for syn in sorted(_SYN_MAP.get(tok, ())):
            if syn not in variants:
                variants.append(syn)
        if len(tokens) == 1:
            for syn in sorted(whole_syns):
                if syn not in variants:
                    variants.append(syn)

        patterns = [(v, _term_pattern(v), v == tok) for v in variants]
        groups.append({"primary": tok, "variants": variants, "patterns": patterns})

    # Add phrase-level group override note if multi-word whole phrase exists in _SYN_MAP
    return groups


def _fts_quote(term: str) -> str:
    clean = term.replace('"', "").strip()
    if not clean:
        return ""
    if " " in clean:
        return f'"{clean}"'
    # Prefix wildcard ONLY for 4+ char single Arabic words without digits/punctuation
    if _ARABIC_WORD_RE.match(clean) and len(clean) >= 4:
        return f'"{clean}"*'
    return f'"{clean}"'


def build_fts_query(groups: list[dict]) -> str:
    """Compile query groups into a safe SQLite FTS5 MATCH expression."""
    clauses: list[str] = []
    for g in groups:
        parts = [_fts_quote(v) for v in g["variants"] if _fts_quote(v)]
        if not parts:
            continue
        clauses.append("(" + " OR ".join(dict.fromkeys(parts)) + ")")
    return " AND ".join(clauses)


def _extract_job_zones(row_or_job: dict) -> tuple[str, str, str, str, str]:
    """Return folded (title, employer, specs, tasks_skills, body) from either a Job dict or DB row."""
    if "data" in row_or_job and isinstance(row_or_job["data"], str):
        j = json.loads(row_or_job["data"])
    else:
        j = row_or_job
    title = search_fold(j.get("title") or "")
    employer = search_fold(j.get("employer") or "")
    specs = search_fold(" ".join(
        p for p in [
            " ".join(j.get("cities") or []),
            j.get("specialty") or "",
            j.get("subfield") or "",
            _SECTOR_WORDS.get(j.get("sector") or "", ""),
            " ".join(j.get("tags") or []),
        ] if p
    ))
    tasks_skills = search_fold(" ".join(
        p for p in [
            " ".join(j.get("tasks") or []),
            " ".join(j.get("skills") or []),
            j.get("salary") or "",
            j.get("shift") or "",
        ] if p
    ))
    body = search_fold(_strip_urls(j.get("raw") or ""))
    return title, employer, specs, tasks_skills, body


def score_job_match(q_folded: str, groups: list[dict], zones: tuple[str, str, str, str, str], bm25_bonus: float = 0.0) -> float | None:
    """Strictly verify every query group matches clean zones and compute relevance score."""
    title, employer, specs, tasks_skills, body = zones
    # Support multi-word phrase synonyms as an alternate full match (e.g. "موارد بشرية" -> "hr")
    phrase_syns = _SYN_MAP.get(q_folded, set()) if len(groups) > 1 else set()
    if phrase_syns:
        all_zones_joined = f"{title} {employer} {specs} {tasks_skills} {body}"
        for ps in phrase_syns:
            pat = _term_pattern(ps)
            if pat.search(all_zones_joined):
                # Re-score cleanly via this phrase synonym
                sc = 15.0 + bm25_bonus
                if pat.search(title):
                    sc += 90.0
                elif pat.search(employer):
                    sc += 45.0
                elif pat.search(specs):
                    sc += 30.0
                elif pat.search(tasks_skills):
                    sc += 20.0
                return sc

    total = 0.0
    if q_folded and len(groups) >= 1:
        full_pat = _term_pattern(q_folded)
        if full_pat.search(title):
            total += 120.0
        elif full_pat.search(employer):
            total += 55.0

    for g in groups:
        best_group = 0.0
        for _, pat, is_exact in g["patterns"]:
            mult = 1.0 if is_exact else 0.72
            hit = 0.0
            if pat.search(title):
                hit = max(hit, 40.0 * mult)
            if pat.search(employer):
                hit = max(hit, 22.0 * mult)
            if pat.search(specs):
                hit = max(hit, 16.0 * mult)
            if pat.search(tasks_skills):
                hit = max(hit, 10.0 * mult)
            if hit == 0.0 and pat.search(body):
                hit = 4.0 * mult
            if hit > best_group:
                best_group = hit
        if best_group == 0.0:
            return None
        total += best_group

    return total + bm25_bonus


def search_and_rank(
    con: sqlite3.Connection,
    candidate_rows: list,
    q: str,
    sort: str = "relevance",
) -> tuple[list, list[str]]:
    """Filter candidate DB rows using FTS5 + strict boundary verification and rank by relevance or date.

    Returns (ranked_rows, highlight_terms).
    """
    fq = search_fold(q)
    groups = expand_query_groups(q)
    if not fq or not groups:
        return candidate_rows, []

    # Collect highlight terms cleanly (primary words + immediate non-noise variants)
    hl_terms: list[str] = []
    for g in groups:
        for v in g["variants"][:6]:
            if len(v) >= 2 and v not in hl_terms:
                hl_terms.append(v)

    # Fast FTS5 pre-filter when jobs_fts exists
    fts_scores: dict[str, float] = {}
    row_by_id = {r["id"]: r for r in candidate_rows}
    fts_ok = False
    try:
        fts_expr = build_fts_query(groups)
        # Also include phrase synonym OR in FTS5 when multi-word synonym exists
        if len(groups) > 1 and fq in _SYN_MAP:
            extra = [_fts_quote(s) for s in _SYN_MAP[fq] if _fts_quote(s)]
            if extra:
                fts_expr = f"({fts_expr}) OR (" + " OR ".join(extra) + ")"
        if fts_expr:
            sql = (
                "SELECT job_id, -bm25(jobs_fts, 10.0, 5.0, 3.0, 1.0, 0.8) AS rank "
                "FROM jobs_fts WHERE jobs_fts MATCH ? LIMIT 2000"
            )
            for rid, rnk in con.execute(sql, (fts_expr,)).fetchall():
                if rid in row_by_id:
                    fts_scores[rid] = max(0.0, float(rnk or 0.0))
            fts_ok = True
    except sqlite3.OperationalError:
        fts_ok = False

    pool = [row_by_id[rid] for rid in fts_scores] if fts_ok else candidate_rows

    scored: list[tuple[float, int, str, sqlite3.Row]] = []
    for r in pool:
        zones = _extract_job_zones(dict(r))
        sc = score_job_match(fq, groups, zones, bm25_bonus=min(fts_scores.get(r["id"], 0.0), 15.0))
        if sc is None:
            continue
        official = int(r["official"] or 0) if "official" in r.keys() else 0
        posted = str(r["posted_at"] or "")
        scored.append((sc, official, posted, r))

    if sort == "recent":
        scored.sort(key=lambda x: (x[2], x[0]), reverse=True)
    else:
        scored.sort(key=lambda x: (round(x[0], 2), x[1], x[2]), reverse=True)
    return [x[3] for x in scored], hl_terms


def fuzzy_suggestion(con: sqlite3.Connection, q: str) -> str | None:
    """When a query gets 0 results, suggest the closest real term from live jobs or synonyms."""
    fq = search_fold(q)
    tokens = tokenize(fq)
    if not tokens:
        return None

    vocab: Counter[str] = Counter()
    for syn_k in _SYN_MAP:
        if " " not in syn_k and len(syn_k) >= 3:
            vocab[syn_k] += 5

    today = date.today().isoformat()
    rows = con.execute(
        "SELECT title, json_extract(data, '$.employer') FROM jobs WHERE status = 'live' AND expires_on >= ?",
        (today,),
    ).fetchall()
    for r in rows:
        for t in tokenize(f"{r[0] or ''} {r[1] or ''}"):
            if len(t) >= 3:
                vocab[t] += 1

    if not vocab:
        return None
    candidates = [w for w, c in vocab.most_common(600)]

    fixed_tokens: list[str] = []
    changed = False
    for tok in tokens:
        if tok in vocab or len(tok) < 3:
            fixed_tokens.append(tok)
            continue
        matches = difflib.get_close_matches(tok, candidates, n=1, cutoff=0.72)
        if matches and matches[0] != tok:
            fixed_tokens.append(matches[0])
            changed = True
        else:
            fixed_tokens.append(tok)

    if not changed:
        return None
    suggestion = " ".join(fixed_tokens)
    if suggestion == fq:
        return None
    # Confirm the suggestion actually matches at least 1 live job
    all_open = con.execute("SELECT id, official, posted_at, data FROM jobs WHERE status = 'live' AND expires_on >= ?", (today,)).fetchall()
    hits, _ = search_and_rank(con, all_open, suggestion)
    return suggestion if hits else None


def suggest_queries(con: sqlite3.Connection, q: str, limit: int = 8) -> list[dict]:
    """Fast live autocomplete suggestions from active job titles, employers, and standard topics."""
    fq = search_fold(q)
    if len(fq) < 2:
        return []

    today = date.today().isoformat()
    rows = con.execute(
        "SELECT title, json_extract(data, '$.employer') FROM jobs WHERE status = 'live' AND expires_on >= ?",
        (today,),
    ).fetchall()
    title_counts: Counter[str] = Counter()
    title_display: dict[str, str] = {}
    emp_counts: Counter[str] = Counter()
    emp_display: dict[str, str] = {}

    for title, emp in rows:
        if title:
            ft = search_fold(title)
            if ft:
                title_counts[ft] += 1
                title_display.setdefault(ft, title.strip())
        if emp:
            fe = search_fold(emp)
            if fe:
                emp_counts[fe] += 1
                emp_display.setdefault(fe, emp.strip())

    scored: list[tuple[int, int, str, str]] = []
    seen_folded: set[str] = set()

    def _add(folded: str, raw: str, kind: str, cnt: int):
        if folded in seen_folded:
            return
        if folded.startswith(fq):
            pri = 3
        elif f" {fq}" in folded:
            pri = 2
        elif fq in folded:
            pri = 1
        else:
            return
        seen_folded.add(folded)
        scored.append((pri, cnt, raw, kind))

    for ft, cnt in title_counts.items():
        _add(ft, title_display[ft], "title", cnt)
    for fe, cnt in emp_counts.items():
        _add(fe, emp_display[fe], "employer", cnt)
    for grp in _SYNONYM_GROUPS:
        rep = grp[0]
        fr = search_fold(rep)
        if fr and (fr.startswith(fq) or fq in fr):
            _add(fr, rep, "topic", 2)

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [{"text": x[2], "kind": x[3], "count": x[1]} for x in scored[:limit]]
