"""Finding 7: resource_construction_in_loop -- sqlite3.connect per iteration.

Pattern: opening a database connection (or any expensive resource) inside the
loop body, once per item, instead of opening it once and reusing it.
Real instance: repowise app.py.
Fix: construct the resource once outside the loop, reuse it inside.

This is a *constant-overhead* finding: the win is the per-connect cost times N,
not an algorithmic O(n^2)->O(n) collapse. We report it honestly as such.

We use stdlib sqlite3 against a real on-disk db so connect() does real work.

1 run per config. Reports wall-clock + speedup.
"""
import os
import sqlite3
import tempfile
import time


def build_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, val TEXT)")
    con.executemany(
        "INSERT INTO item (id, val) VALUES (?, ?)",
        [(i, f"value-{i}") for i in range(1_000)],
    )
    con.commit()
    con.close()


def connect_each(path, n):  # the pattern as written: connect per iteration
    out = []
    for i in range(n):
        con = sqlite3.connect(path)
        cur = con.execute("SELECT val FROM item WHERE id = ?", (i % 1_000,))
        out.append(cur.fetchone())
        con.close()
    return out


def connect_once(path, n):  # the fix: open once, reuse
    con = sqlite3.connect(path)
    out = []
    for i in range(n):
        cur = con.execute("SELECT val FROM item WHERE id = ?", (i % 1_000,))
        out.append(cur.fetchone())
    con.close()
    return out


def bench(fn, path, n):
    t = time.perf_counter()
    fn(path, n)
    return (time.perf_counter() - t) * 1000.0  # ms


if __name__ == "__main__":
    print("Finding 7: resource_construction_in_loop (connect-per-iter -> connect once)")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "bench.db")
    build_db(db)
    try:
        for n in (1_000, 5_000, 20_000):
            b = bench(connect_each, db, n)
            a = bench(connect_once, db, n)
            print(f"  N={n:>6}  before={b:9.2f}ms  after={a:8.3f}ms  speedup={b / max(a, 1e-6):8.1f}x")
    finally:
        os.remove(db)
        os.rmdir(tmp)
