import json, sys, time, httpx
sys.path.insert(0, "/home/user/laqeet-src/laqeet")
from app.sources.telegram import parse_page
ch, pages = sys.argv[1], int(sys.argv[2])
c = httpx.Client(headers={"User-Agent": "laqeet/0.1"}, timeout=30, follow_redirects=True)
out, before = {}, None
for _ in range(pages):
    url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
    page = parse_page(c.get(url).text, ch)
    if not page: break
    for p in page: out[p.id] = dict(channel=ch, id=p.id, date=p.date, text=p.text, links=p.links)
    o = min(p.id for p in page)
    if before and o >= before: break
    before = o; time.sleep(1.2)
json.dump(sorted(out.values(), key=lambda x: -x["id"]), open(f"/home/user/laqeet-src/laqeet/data/raw/{ch}.json", "w"), ensure_ascii=False, indent=1)
print(ch, len(out), min(out), max(out), min(v['date'] for v in out.values()))
