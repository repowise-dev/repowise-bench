"""Finding 6: io_in_loop / N+1 -- one query per item in a loop.

Pattern: given N ids, fetch each row with its own SELECT inside a loop.
N round-trips to the database where one would do.
Real instances: repowise scheduler.py; the canonical N+1.
Fix: a single batched query, `SELECT ... WHERE id IN (...)`.

We use stdlib sqlite3 against a real on-disk table of rows so the per-query
overhead (statement prep + round-trip) is genuine, not optimized away.

1 run per config. Reports wall-clock + speedup.
"""
import os
import sqlite3
import tempfile
import time


def build_db(path, n_rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, val TEXT)")
    con.executemany(
        "INSERT INTO item (id, val) VALUES (?, ?)",
        [(i, f"value-{i}") for i in range(n_rows)],
    )
    con.commit()
    con.close()


def n_plus_1(con, ids):  # the pattern as written: one query per id
    rows = []
    for i in ids:
        cur = con.execute("SELECT val FROM item WHERE id = ?", (i,))
        rows.append(cur.fetchone())
    return rows


def batched(con, ids):  # the fix: one query with WHERE id IN (...)
    placeholders = ",".join("?" * len(ids))
    cur = con.execute(f"SELECT id, val FROM item WHERE id IN ({placeholders})", ids)
    return cur.fetchall()


def bench(fn, con, ids):
    t = time.perf_counter()
    fn(con, ids)
    return (time.perf_counter() - t) * 1000.0  # ms


if __name__ == "__main__":
    print("Finding 6: N+1 query (per-item SELECT -> single WHERE id IN (...))")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "bench.db")
    build_db(db, 50_000)
    con = sqlite3.connect(db)
    try:
        for n in (100, 500, 2_000):
            ids = list(range(n))
            b = bench(n_plus_1, con, ids)
            a = bench(batched, con, ids)
            print(f"  N={n:>6}  before={b:9.2f}ms  after={a:8.3f}ms  speedup={b / max(a, 1e-6):8.1f}x")
    finally:
        con.close()
        os.remove(db)
        os.rmdir(tmp)
