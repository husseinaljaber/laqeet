"""Export the live SQLite database into data/seed_jobs.jsonl.gz (the file Render boots from).

    python -m app.seed
"""
from __future__ import annotations

import gzip
import json

from . import config, db

SEED_PATH = config.ROOT / "data" / "seed_jobs.jsonl.gz"


def export_seed(con=None, path=SEED_PATH) -> int:
    """Write jobs, FTS rows, sources, dedupe keys and meta in the tagged JSONL format `db` bootstraps from."""
    own = con is None
    con = con or db.connect()
    try:
        db.expire_past(con)
        db.rebuild_fts(con)
        con.commit()
        cur = con.execute("SELECT * FROM jobs ORDER BY id")
        cols = [d[0] for d in cur.description]
        jobs = cur.fetchall()
        tables = (
            ("f", "SELECT job_id, title_text, employer_text, specs_text, body_text, aux_text FROM jobs_fts ORDER BY job_id"),
            ("s", "SELECT job_id, source, post_url, posted_at FROM job_sources ORDER BY job_id"),
            ("k", "SELECT k, job_id FROM job_keys"),
            ("m", "SELECT key, value FROM meta"),
        )
        tmp = path.with_suffix(".gz.tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as gz:
            gz.write(json.dumps({"jobs_cols": cols, "fts": True}, ensure_ascii=False) + "\n")
            data_i = cols.index("data")
            for r in jobs:
                row = list(r)
                if r["status"] == "rejected":
                    # keep the row so a rejected ad is not re-added on the next collect, but drop its text and contacts
                    d = json.loads(row[data_i])
                    d.update(raw="", phones=[], email=None, apply_url=None, tasks=[], skills=[])
                    row[data_i] = json.dumps(d, ensure_ascii=False)
                gz.write("j\t" + json.dumps(row, ensure_ascii=False) + "\n")
            for tag, sql in tables:
                for r in con.execute(sql):
                    gz.write(f"{tag}\t" + json.dumps(list(r), ensure_ascii=False) + "\n")
        tmp.replace(path)
        return len(jobs)
    finally:
        if own:
            con.close()


if __name__ == "__main__":
    n = export_seed()
    print(f"seed exported: {n} jobs -> {SEED_PATH} ({SEED_PATH.stat().st_size} bytes)")
