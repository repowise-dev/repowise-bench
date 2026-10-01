"""Precision/recall restricted to repowise edges with confidence >= 0.9.

PREREGISTRATION.md requires reporting both "all confidences" (what
`score.py`/`compare.py` already does) and the >=0.9 confidence tier. `compare.py`'s `ours_keys()` deliberately carries no
confidence filter (by design, so every arm is scored on its full output);
this is a separate, additive read of the same graph that applies one,
reusing `load_oracle`/`in_scope`/`arms_lib.get_arm` unmodified rather than
forking compare.py's bucket math a second time.

Usage:
    python confidence_tier.py --oracle work/Polly/oracle.jsonl \
        --repo path/to/Polly --repo-name Polly
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_GRAPH = Path(__file__).resolve().parents[2]  # repowise-bench/graph
_G4 = _GRAPH / "experiments" / "g4-oracle-anchored"
_BENCH_LIB = _GRAPH / "lib"
_BENCH_ARMS = _GRAPH / "arms"
for p in (_G4, _BENCH_LIB, _BENCH_ARMS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import arms as arms_lib  # noqa: E402
import compare as g4_compare  # noqa: E402
import stats  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--min-confidence", type=float, default=0.9)
    args = ap.parse_args()

    header, oracle_all, reachable = g4_compare.load_oracle(Path(args.oracle))
    analysed = set(header["analysed_files"])
    oracle = g4_compare.in_scope(oracle_all, analysed)

    repo = Path(args.repo).resolve()
    name = args.repo_name or repo.name
    arm = arms_lib.get_arm("repowise")
    art = arm.build(repo, repo_name=name, fresh=True)
    try:
        g = art.handle.graph
        keys = set()
        for u, v, d in g.edges(data=True):
            if d.get("edge_type") != "calls":
                continue
            if float(d.get("confidence", 1.0)) < args.min_confidence:
                continue
            a, b = g.nodes[u], g.nodes[v]
            if a.get("node_type") != "symbol" or b.get("node_type") != "symbol":
                continue
            if a.get("file_path") is None or b.get("file_path") is None:
                continue
            keys.add(
                (
                    arms_lib.norm_path(a["file_path"]),
                    int(a.get("start_line") or 0),
                    arms_lib.norm_path(b["file_path"]),
                    int(b.get("start_line") or 0),
                )
            )
        keys = g4_compare.in_scope(keys, analysed)
    finally:
        arm.close(art)

    matched = keys & oracle
    outside = keys - oracle
    contradicted = {k for k in outside if (k[0], k[1]) in reachable}
    denom = len(matched) + len(contradicted)
    rec = stats.wilson(len(matched), len(oracle)) if oracle else None
    prec = stats.wilson(len(matched), denom) if denom else None
    print(f"confidence >= {args.min_confidence}")
    print(f"  edges in scope   {len(keys)}")
    print(f"  matched          {len(matched)}")
    print(f"  contradicted     {len(contradicted)}")
    print(
        f"  RECALL           {round(len(matched) / len(oracle), 4) if oracle else None}  "
        f"CI[{round(rec.low, 4) if rec else None}, {round(rec.high, 4) if rec else None}]"
    )
    print(
        f"  PRECISION        {round(len(matched) / denom, 4) if denom else None}  "
        f"CI[{round(prec.low, 4) if prec else None}, {round(prec.high, 4) if prec else None}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
