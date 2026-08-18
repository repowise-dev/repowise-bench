"""G2, our side: the same "fair coverage" sweep as run_peer.py, off our graph.

`run_peer.py` reads a frozen `.codegraph/codegraph.db` and does not touch the
repo under test. We have no such artifact — our graph exists only in memory
for the life of one process — so this script builds it: walk, parse, resolve,
`GraphBuilder.build()`, all in-process, via `graph/lib/ours.py::build_graph`.
Nothing under `--repo` is written to.

Reports the same three free variables run_peer.py reports, so the two JSON
outputs can be joined on `{scope}__{edge_set}__{direction}`:

  1. denominator scope   primary_language (files in --languages) | all_files
  2. edge set            any_dependency | calls_or_refs | calls_only
  3. direction           either | incoming | outgoing

Run:

    python graph/experiments/g2-cross-file-coverage/run_ours.py \
        --repo ../test-repos/gitleaks --name gitleaks --languages go \
        --out results/graph/g2/ours_gitleaks.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import ours  # noqa: E402

# Same three readings as the peer script, on our closed edge vocabulary. See
# graph/lib/ours.py's module docstring for why our DEPENDENCY_KINDS is wider
# than the peer's: we do not collapse extends/implements/type_use/etc. into
# one "references" bucket.
EDGE_SETS = {
    "any_dependency": ours.DEPENDENCY_KINDS,
    "calls_or_refs": frozenset({"calls", "references"}),
    "calls_only": frozenset({"calls"}),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="path to the repo checkout to build a graph for")
    ap.add_argument("--name", required=True, help="repo name, as it will appear in the report")
    ap.add_argument(
        "--languages",
        action="append",
        default=None,
        help="primary language(s) of the repo (repeatable). Restricts the "
        "primary_language denominator; all_files is unrestricted.",
    )
    ap.add_argument("--out", help="write JSON here (stdout table always printed)")
    args = ap.parse_args()

    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"no repo at {repo_path}", file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    built = ours.build_graph(repo_path)
    build_wall = time.perf_counter() - t0

    resolved = ours.resolved_call_edges(built)

    row: dict = {
        "language_pin": args.languages,
        "index": {
            "repowise_build": True,  # built fresh this run, not read from disk
            "files_walked": built.parsed_count,
            "nodes": built.graph.number_of_nodes(),
            "edges": built.graph.number_of_edges(),
            "calls_raw": len(built.resolved_calls),
            "calls_distinct": len(resolved),
            "timings_sec": {
                "walk": round(built.timings.walk, 3),
                "parse": round(built.timings.parse, 3),
                "build": round(built.timings.build, 3),
                "total": round(built.timings.total, 3),
                "script_wall": round(build_wall, 3),
            },
        },
        "file_languages": ours.language_histogram(built),
        "edge_type_histogram": ours.edge_type_histogram(built),
        "coverage": {},
    }

    for scope, langs in (("primary_language", args.languages), ("all_files", None)):
        denom = ours.symbol_bearing_files(built, langs)
        for name, kinds in EDGE_SETS.items():
            for direction in ("either", "incoming", "outgoing"):
                covered = (
                    ours.files_with_cross_file_dependents(built, kinds, langs, direction) & denom
                )
                row["coverage"][f"{scope}__{name}__{direction}"] = {
                    "covered": len(covered),
                    "symbol_bearing": len(denom),
                    "rate": round(len(covered) / len(denom), 4) if denom else None,
                }

    report = {"metric": "cross_file_coverage", "arm": "repowise", "repos": {args.name: row}}

    _print_table(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


def _print_table(report: dict) -> None:
    """Same three-column view as run_peer.py's `_print_table`, for a diff-by-eye
    against its output. `either / any_dependency` reproduces CodeGraph's
    published reading; `incoming / calls_only` is the reading that describes
    whether the call graph actually connected the file.
    """
    columns = [
        ("any_dependency", "either", "either (theirs)"),
        ("any_dependency", "incoming", "incoming"),
        ("calls_only", "incoming", "incoming calls"),
    ]
    print("\n== primary-language denominator ==")
    print(
        f"{'repo':10s} {'lang':11s} {'sym files':>9s} "
        + " ".join(f"{label:>17s}" for _, _, label in columns)
    )
    for repo, row in report["repos"].items():
        cov = row["coverage"]
        keys = [f"primary_language__{e}__{d}" for e, d, _ in columns]
        cells = " ".join(
            f"{cov[k]['rate']:>17.3f}" if cov[k]["rate"] is not None else f"{'-':>17s}"
            for k in keys
        )
        lang_label = ",".join(row["language_pin"]) if row["language_pin"] else "-"
        print(f"{repo:10s} {lang_label:11s} " f"{cov[keys[0]]['symbol_bearing']:>9d} {cells}")
    print("\nFull sweep of every (denominator, edge set, direction) is in --out.")


if __name__ == "__main__":
    raise SystemExit(main())
