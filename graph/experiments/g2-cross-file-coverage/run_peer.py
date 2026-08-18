"""G2, peer side: recompute CodeGraph's own "fair coverage" from its own index.

CodeGraph publishes a per-language coverage table in its README and defines the
metric as "the share of symbol-bearing source files that have at least one
resolved cross-file dependent". That sentence has at least three free variables,
and the published number cannot be reproduced without pinning them:

  1. Which node kinds count as "symbol-bearing"? (`import` nodes are 48% of
     gitleaks' node table, and counting them changes the denominator.)
  2. Which edge kinds count as a "dependent"? Calls only, or every edge kind
     including `imports` and `contains`?
  3. Is the denominator restricted to the repo's primary language, or does it
     include the yaml/xml/kotlin files the indexer also walked?

So this script does not report one number. It reports the metric under each
setting of (2) and (3), which turns "we cannot reproduce their table" into
"their table corresponds to this specific reading, and here is the spread".

Read-only. Never writes to a peer index. Run:

    python graph/experiments/g2-cross-file-coverage/run_peer.py \
        --test-repos ../test-repos --out results/g2/peer_coverage.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import peer_codegraph as peer  # noqa: E402

# The three readings of "dependent", widest to narrowest.
EDGE_SETS = {
    "any_dependency": peer.DEPENDENCY_KINDS,
    "calls_or_refs": frozenset({"calls", "references", "instantiates"}),
    "calls_only": frozenset({"calls"}),
}

# Repos whose frozen index the published head-to-head reconciles against, with
# the language the repo is actually written in. The language pin matters: the
# caffeine index carries kotlin and python callers too.
CORPUS = {
    "caffeine": ["java"],
    "zod": ["typescript", "tsx"],
    "Ocelot": ["csharp"],
    "celery": ["python"],
    "gitleaks": ["go"],
    "dub": ["typescript", "tsx"],
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test-repos", required=True, help="directory holding the repo checkouts")
    ap.add_argument("--out", help="write JSON here (stdout table always printed)")
    args = ap.parse_args()

    root = Path(args.test_repos).resolve()
    report: dict = {"metric": "cross_file_coverage", "arm": "codegraph", "repos": {}}

    for repo, languages in CORPUS.items():
        db = root / repo / ".codegraph" / "codegraph.db"
        if not db.is_file():
            print(f"{repo:10s}  no index at {db}", file=sys.stderr)
            continue

        conn = peer.connect(db)
        try:
            st = peer.stats(conn, repo, str(db))
            row: dict = {
                "language_pin": languages,
                "index": {
                    "codegraph_version": st.codegraph_version,
                    "extraction_version": st.extraction_version,
                    "files": st.files,
                    "nodes": st.nodes,
                    "edges": st.edges,
                    "calls_raw": st.calls_raw,
                    "calls_distinct": st.calls_distinct,
                    "unresolved_calls": st.unresolved_calls,
                },
                "file_languages": peer.language_histogram(conn),
                "coverage": {},
            }

            for scope, langs in (("primary_language", languages), ("all_files", None)):
                denom = peer.symbol_bearing_files(conn, langs)
                for name, kinds in EDGE_SETS.items():
                    for direction in ("either", "incoming", "outgoing"):
                        covered = (
                            peer.files_with_cross_file_dependents(conn, kinds, langs, direction)
                            & denom
                        )
                        row["coverage"][f"{scope}__{name}__{direction}"] = {
                            "covered": len(covered),
                            "symbol_bearing": len(denom),
                            "rate": round(len(covered) / len(denom), 4) if denom else None,
                        }
            report["repos"][repo] = row
        finally:
            conn.close()

    _print_table(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


def _print_table(report: dict) -> None:
    """The three readings that matter, on the primary-language denominator.

    `either / any_dependency` is the reading that reproduces CodeGraph's
    published table. `incoming / calls_only` is the reading that describes
    whether the call graph actually connected the file. The gap between them is
    the point of this experiment, so both are always printed together.
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
        print(
            f"{repo:10s} {row['language_pin'][0]:11s} "
            f"{cov[keys[0]]['symbol_bearing']:>9d} {cells}"
        )
    print("\nFull sweep of every (denominator, edge set, direction) is in --out.")


if __name__ == "__main__":
    raise SystemExit(main())
