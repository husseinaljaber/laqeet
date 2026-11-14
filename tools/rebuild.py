"""Rebuild the jobs from the stored posts after an extractor change (keeps the posts, rebuilds everything else).

    python tools/rebuild.py
"""
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import collect, config, db  # noqa: E402

src = str(config.DB_PATH)
old_path = src + ".old"
if os.path.exists(src):
    # Flush WAL into the main DB file before moving, otherwise recent writes in -wal are lost!
    tmp = sqlite3.connect(src)
    tmp.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    tmp.close()
shutil.move(src, old_path)
for ext in ("-wal", "-shm"):
    if os.path.exists(src + ext):
        os.remove(src + ext)
old = sqlite3.connect(old_path)
con = db.connect()
con.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?)", old.execute("SELECT channel,id,date,text,links,jobs FROM posts"))
con.executemany("INSERT INTO meta VALUES (?,?)", old.execute("SELECT key,value FROM meta"))
for table in ("bot_users", "bot_sent", "reports"):
    rows = old.execute(f"SELECT * FROM {table}").fetchall()
    if rows:
        con.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
# employer posts have no channel post behind them: carry them over as they are
cols = [r[1] for r in old.execute("PRAGMA table_info(jobs)")]
for row in old.execute("SELECT * FROM jobs WHERE source='employer'"):
    con.execute(f"INSERT OR IGNORE INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", row)
for row in old.execute("SELECT s.* FROM job_sources s JOIN jobs j ON j.id=s.job_id WHERE j.source='employer'"):
    con.execute("INSERT OR IGNORE INTO job_sources VALUES (?,?,?,?)", row)
con.commit()
old.close()
print("jobs from posts:", collect.reprocess())
os.remove(old_path)
