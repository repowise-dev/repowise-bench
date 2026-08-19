"""Read a sample of one arm's contradicted edges against the source.

`contradicted` is the bucket that sets precision, so it is the bucket that has
to be believed. In the Go cell it was believable because a second method, the
540-row hand audit, landed within a point of it. A new oracle in a new language
has no such corroboration until someone reads the rows, and if the oracle is
missing a class of real call then every arm's precision is understated by
whatever share of that class it emits.

Prints, per drawn edge, the caller's declaration line, the callee's declaration
line, and the caller's body, so a person can look for the call and decide who is
right. It asserts nothing.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "lib"))
sys.path.insert(0, str(BENCH / "graph" / "arms"))

import arms as arms_lib  # noqa: E402

import compare  # noqa: E402


def body(repo: Path, rel: str, line: int, span: int) -> list[str]:
    p = repo / rel
    if not p.exists():
        return ["<file missing>"]
    text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    lo, hi = max(0, line - 1), min(len(text), line - 1 + span)
    return [f"{i + 1:>6}  {text[i]}" for i in range(lo, hi)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--arm", default="repowise")
    ap.add_argument("--bucket", default="contradicted", choices=["contradicted", "unjudged", "missed"])
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--span", type=int, default=14)
    ap.add_argument("--seed", type=int, default=20260819)
    args = ap.parse_args()

    header, oracle_all, reachable = compare.load_oracle(Path(args.oracle))
    analysed = set(header["analysed_files"])
    oracle = compare.in_scope(oracle_all, analysed)
    repo = Path(args.repo).resolve()
    name = args.repo_name or repo.name

    arm = arms_lib.get_arm(args.arm)
    art = arm.build(repo, repo_name=name, fresh=True)
    try:
        keys = compare.in_scope(compare.EXTRACT[args.arm](art), analysed)
    finally:
        arm.close(art)

    if args.bucket == "missed":
        pool = oracle - keys
    else:
        outside = keys - oracle
        contradicted = {k for k in outside if (k[0], k[1]) in reachable}
        pool = contradicted if args.bucket == "contradicted" else outside - contradicted

    print(f"{args.arm} {args.bucket}: {len(pool)} edges, drawing {args.n}\n")
    for i, k in enumerate(sorted(random.Random(args.seed).sample(sorted(pool), min(args.n, len(pool)))), 1):
        cf, cl, tf, tl = k
        print(f"--- {i}. {cf}:{cl}  ->  {tf}:{tl}")
        print("    CALLEE:")
        for s in body(repo, tf, tl, 2):
            print(f"    {s}")
        print("    CALLER:")
        for s in body(repo, cf, cl, args.span):
            print(f"    {s}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
