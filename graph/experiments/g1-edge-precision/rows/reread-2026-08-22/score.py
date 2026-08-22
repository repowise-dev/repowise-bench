"""Score the 2026-08-22 C++ re-read and compare it with the published cell.

G1's C++ rule, reproduced rather than described: ten rows per repository are
graded, and the cross-language pooled cell is the seeded six of each ten, drawn
with `random.Random(2026).sample(range(10), 6)`, so C++ carries the same weight
as every other language instead of nearly double. Both readings are printed.

    python score.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "lib"))

from stats import wilson  # noqa: E402

REPOS = ["fmt", "Crow", "leveldb", "seastar", "aria2"]

# The published cell, measured at 13cc339a, before #1782 merged.
PUBLISHED_DEPTH = (39, 50)
PUBLISHED_POOLED = (23, 30)


def load(repo: str) -> list[dict]:
    path = HERE / f"graded-{repo}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    if len(rows) != 10:
        raise SystemExit(f"{repo}: expected 10 rows, got {len(rows)}")
    return rows


def correct(rows: list[dict]) -> int:
    return sum(1 for r in rows if r["verdict"] == "correct")


def main() -> None:
    per_repo, depth, pooled = {}, [], []
    for repo in REPOS:
        rows = load(repo)
        per_repo[repo] = rows
        depth.extend(rows)
        keep = sorted(random.Random(2026).sample(range(10), 6))
        pooled.extend(rows[i] for i in keep)

    print("| repo | re-read | published (13cc339a) |")
    print("|---|---|---|")
    was = {"fmt": 9, "Crow": 7, "leveldb": 9, "seastar": 4, "aria2": 10}
    for repo in REPOS:
        print(f"| {repo} | {correct(per_repo[repo])}/10 | {was[repo]}/10 |")

    print()
    for label, rows, before in (
        ("depth, n=50", depth, PUBLISHED_DEPTH),
        ("pooled cell, n=30", pooled, PUBLISHED_POOLED),
    ):
        k, n = correct(rows), len(rows)
        b_k, b_n = before
        print(f"{label}: {k}/{n} = {wilson(k, n).pct()}   "
              f"was {b_k}/{b_n} = {wilson(b_k, b_n).pct()}")

    # The bucket #1782 was built to remove, counted rather than asserted.
    chained = [r for r in depth
               if "chain" in (r.get("reason") or "").lower()
               or "future<" in (r.get("source_line") or "")]
    if chained:
        print(f"\nrows whose reason or source names a chained/future receiver: "
              f"{len(chained)}, of which wrong "
              f"{sum(1 for r in chained if r['verdict'] != 'correct')}")

    verdicts: dict[str, int] = {}
    for r in depth:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    print("verdicts over the 50:", verdicts)

    by_origin: dict[str, list[int]] = {}
    for r in depth:
        b = by_origin.setdefault(r["origin"], [0, 0])
        b[0] += r["verdict"] == "correct"
        b[1] += 1
    print("\n| origin | correct / n |")
    print("|---|---|")
    for origin, (k, n) in sorted(by_origin.items(), key=lambda kv: -kv[1][1]):
        print(f"| `{origin}` | {k}/{n} |")


if __name__ == "__main__":
    main()
