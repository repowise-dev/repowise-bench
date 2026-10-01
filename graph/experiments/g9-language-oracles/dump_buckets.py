"""Dump contradicted/missed edge lists for hand-checking and decomposition.

Not a fork of `compare.py`'s bucket math: imports `load_oracle`, `in_scope`
and `EXTRACT` from it by path and recomputes the same three sets
(matched / contradicted / unjudged) it already computes internally, then
also derives `missed` (oracle edges in a judgeable caller that no arm edge
matched -- the complement of recall) which `compare.py` only counts, never
writes out. Run where repowise is installed, same as `score.py`, since it
needs `ours_keys()` -> `repowise.core`.

Usage:
    python dump_buckets.py --oracle work/Polly/oracle.jsonl \
        --repo path/to/Polly --repo-name Polly --out-dir work/Polly
"""

from __future__ import annotations

import argparse
import json
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    header, oracle_all, reachable = g4_compare.load_oracle(Path(args.oracle))
    analysed = set(header["analysed_files"])
    oracle = g4_compare.in_scope(oracle_all, analysed)

    repo = Path(args.repo).resolve()
    name = args.repo_name or repo.name
    arm = arms_lib.get_arm("repowise")
    art = arm.build(repo, repo_name=name, fresh=True)
    try:
        keys = g4_compare.in_scope(g4_compare.EXTRACT["repowise"](art), analysed)
        # Same walk as ours_keys() in compare.py, but also keeps each edge's
        # resolution_origin (persist.py's own field name, carried on the
        # in-memory graph edge dict) for the
        # contradicted-edge decomposition, which compare.py has no reason to
        # track since it only counts bucket sizes.
        origin_by_key: dict[tuple[str, int, str, int], str | None] = {}
        g = art.handle.graph
        for u, v, d in g.edges(data=True):
            if d.get("edge_type") != "calls":
                continue
            a, b = g.nodes[u], g.nodes[v]
            if a.get("node_type") != "symbol" or b.get("node_type") != "symbol":
                continue
            if a.get("file_path") is None or b.get("file_path") is None:
                continue
            key = (
                arms_lib.norm_path(a["file_path"]),
                int(a.get("start_line") or 0),
                arms_lib.norm_path(b["file_path"]),
                int(b.get("start_line") or 0),
            )
            origin_by_key.setdefault(key, d.get("resolution_origin"))
    finally:
        arm.close(art)

    matched = keys & oracle
    outside = keys - oracle
    contradicted = {k for k in outside if (k[0], k[1]) in reachable}
    unjudged = outside - contradicted
    oracle_judgeable = {k for k in oracle if (k[0], k[1]) in reachable}
    missed = oracle_judgeable - matched

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def write(name_, rows, *, with_origin=False):
        with (out_dir / name_).open("w", encoding="utf-8") as fh:
            for cf, cl, tf, tl in sorted(rows):
                rec = {
                    "caller_decl_file": cf,
                    "caller_decl_line": cl,
                    "callee_file": tf,
                    "callee_line": tl,
                }
                if with_origin:
                    rec["resolution_origin"] = origin_by_key.get((cf, cl, tf, tl))
                fh.write(json.dumps(rec) + "\n")

    write("contradicted.jsonl", contradicted, with_origin=True)
    write("missed.jsonl", missed)
    print(
        f"matched={len(matched)} contradicted={len(contradicted)} "
        f"unjudged={len(unjudged)} missed={len(missed)} "
        f"oracle_judgeable={len(oracle_judgeable)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
