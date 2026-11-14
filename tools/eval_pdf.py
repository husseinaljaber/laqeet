"""Compare the PDF reader with the 260 hand-checked ministry cards (tech jobs) for the PDFs we have locally.

python tools/eval_pdf.py            # uses data/cards_cache.json, re-parses PDFs that are not in it
"""
import glob, json, os, sys
sys.path.insert(0, ".")
from app.arabic import soft_fold
from app.sources.moad_pdf import parse_pdf

CACHE = "data/cards_cache.json"


def tokens(s):
    return set(w for w in soft_fold(s).replace("/ه", "").replace("(", " ").replace(")", " ").split() if len(w) > 1)


def load_cards():
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    for f in sorted(glob.glob("data/pdfs/*.pdf")):
        k = os.path.basename(f)[:-4]
        if k not in cache:
            cache[k] = [c.to_dict() for c in parse_pdf(f)]
            json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
    return cache


if __name__ == "__main__":
    cards = load_cards()
    print(f"{sum(map(len, cards.values()))} cards from {len(cards)} PDFs")
    seed = json.load(open("../tech-jobs-sy/data/seed_jobs.json"))
    n = found = grade_ok = exp_ok = exp_n = gov_ok = 0
    for s in seed:
        key = (s.get("cards_url") or "").rsplit("/", 1)[-1]
        if key not in cards:
            continue
        n += 1
        best, score = None, 0
        for c in cards[key]:
            a, b = tokens(s["title"]), tokens(c["title"])
            sc = len(a & b) / max(len(a | b), 1)
            if sc > score:
                best, score = c, sc
        if not best or score < 0.5:
            print("  not found:", key, s["title"], "| best:", best and best["title"]); continue
        found += 1
        want = "1" if s["level"] in ("1", "L") else s["level"]
        grade_ok += best["grade"] == want
        if best["grade"] != want:
            print("  grade:", s["title"], want, best["grade"])
        if s.get("experience") is not None:
            exp_n += 1; exp_ok += best["experience"] == s["experience"]
            if best["experience"] != s["experience"]:
                print("  exp:", s["title"], s["experience"], best["experience"])
        g = s.get("governorate") or ""
        gov_ok += (g in best["governorates"]) or g.startswith("عدة") and len(best["governorates"]) > 1 or g == "جميع المحافظات"
    print(f"hand-checked cards in these PDFs: {n}; found {found} ({found/n:.0%}); grade right {grade_ok}/{found}; "
          f"experience right {exp_ok}/{exp_n}; governorate right {gov_ok}/{found}")
