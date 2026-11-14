"""Measure the rule extractor on hand-checked real posts: python tools/evaluate.py"""
import json, re, sys
sys.path.insert(0, ".")
from app.extract import extract
from app.arabic import soft_fold
from tests.real.gold import SYRIANJOBS, WAZAFNE, TAWZEEF_EDU, SYRIANJOBS_HELDOUT, WAZAFNE_HELDOUT

def norm(s): return re.sub(r"\s*/\s*ة|\s+", " ", soft_fold(s)).replace("/", " ").strip()

def run(ch, src, kind):
    return {p["id"]: extract(p["text"], src, f"https://t.me/{ch}/{p['id']}", p["date"], kind, p["links"])
            for p in json.load(open(f"data/raw/{ch}.json"))}

def score(name, gold, out):
    post_ok = cnt_ok = 0; t_hit = t_all = e_hit = e_all = 0; misses = []
    for pid, g in gold.items():
        jobs = out.get(pid, [])
        post_ok += (bool(g) == bool(jobs)); cnt_ok += (len(g) == len(jobs))
        titles = [norm(j.title) for j in jobs]; used = set()
        for key, edu in g:
            t_all += 1
            idx = next((i for i, t in enumerate(titles) if i not in used and norm(key) in t), None)
            if idx is None:
                misses.append((pid, key, [j.title for j in jobs][:3])); continue
            used.add(idx); t_hit += 1
            if edu != "*":
                e_all += 1; e_hit += (jobs[idx].education in edu.split("|"))
    n = len(gold)
    print(f"{name:12s} posts={n}  job/not-job {post_ok/n:.0%}  exact job count {cnt_ok/n:.0%}  titles found {t_hit}/{t_all} = {t_hit/max(t_all,1):.0%}  "
          f"education right {e_hit}/{e_all} = {e_hit/max(e_all,1):.0%}")
    return misses

if __name__ == "__main__":
    sj, wz = run("syrianjobs2", "syrianjobs", "quick"), run("wazafne175", "wazafne", "formal")
    m1 = score("syrianjobs", SYRIANJOBS, sj)
    m2 = score("wazafne", WAZAFNE, wz)
    print("held-out (labelled after tuning):")
    m1 += score("syrianjobs*", SYRIANJOBS_HELDOUT, sj)
    m2 += score("wazafne*", WAZAFNE_HELDOUT, wz)
    out = run("tawzeef_platform", "tawzeef", "formal")
    posts = [p for p in json.load(open("data/raw/tawzeef_platform.json")) if p["text"].strip()][:50]
    gold_t = {p["id"]: p["text"].count("▪") for p in posts}
    gold_t[348] = 3  # its other ▪️ lines list salaries and papers, not jobs
    ok = sum(len(out[i]) == n for i, n in gold_t.items()); nj = sum(min(len(out[i]), n) for i, n in gold_t.items())
    e_hit = e_all = 0
    for pid, d in TAWZEEF_EDU.items():
        for key, edu in d.items():
            j = next((j for j in out[pid] if norm(key) in norm(j.title)), None)
            e_all += 1; e_hit += bool(j and j.education == edu)
    print(f"{'tawzeef':12s} posts=50  exact job count {ok/50:.0%}  jobs found {nj}/{sum(gold_t.values())} = {nj/sum(gold_t.values()):.0%}  education right {e_hit}/{e_all} = {e_hit/e_all:.0%}")
    if "-v" in sys.argv:
        for m in m1 + m2: print("  miss", m)
