"""Print what the extractor reads from saved real posts: python tools/dump.py <channel> [start] [n]"""
import json, sys
sys.path.insert(0, ".")
from app.extract import extract
SRC = {"syrianjobs2": "syrianjobs", "wazafne175": "wazafne", "tawzeef_platform": "tawzeef", "SyMOAD": "moad"}
ch = sys.argv[1]; start = int(sys.argv[2]) if len(sys.argv) > 2 else 0; n = int(sys.argv[3]) if len(sys.argv) > 3 else 50
posts = [p for p in json.load(open(f"data/raw/{ch}.json")) if p["text"].strip()][start:start + n]
for p in posts:
    jobs = extract(p["text"], SRC[ch], f"https://t.me/{ch}/{p['id']}", p["date"], "quick" if ch == "syrianjobs2" else "formal", p["links"])
    head = p["text"].replace("\n", " ⏎ ")[:160]
    print(f"\n## {p['id']}  | {head}")
    for j in jobs:
        print(f"   - {j.title} | edu={j.education}/{j.education_from} | {','.join(j.cities)} | f={j.field} | g={j.gender} | exp={j.experience} | dl={j.deadline} | {j.kind}{' | SPAM' if j.spam else ''}{' | OFF' if j.official else ''} | emp={j.employer}")
