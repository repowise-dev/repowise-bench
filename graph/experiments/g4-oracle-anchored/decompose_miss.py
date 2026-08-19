"""Where one arm's oracle recall goes, decomposed into buckets that are not disjoint.

A single "recall is 51%" number tells you nothing about whether the remaining
49% is one tractable gap or four intractable ones. This splits the missed set by
two properties the oracle records directly: whether the call is a dynamic
dispatch, and whether either endpoint is a `func` literal.

The buckets deliberately overlap and are reported overlapping. An earlier
reading of the same data quoted "dispatch is 79% of the miss" by adding two
buckets that share most of their members.

The caller-is-a-literal column is kept even though the key no longer places a
caller there. Under the outermost-enclosing key such an edge is matchable, so
the column reads as work available rather than as a structural miss, and its
size is the honest measure of what re-keying bought.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "lib"))
sys.path.insert(0, str(BENCH / "graph" / "arms"))

import arms as arms_lib  # noqa: E402
import compare  # noqa: E402


def is_literal(name: str) -> bool:
    """An SSA closure is spelled `Parent$1`, and only the last segment counts."""
    return "$" in name.rsplit(".", 1)[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--arm", default="repowise")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    header: dict = {}
    records: dict[compare.Key, dict] = {}
    with Path(args.oracle).open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("_header"):
                header = o
                continue
            if o.get("_reachable") or not o["caller_decl_file"] or not o["callee_file"]:
                continue
            records[(o["caller_decl_file"], o["caller_decl_line"],
                     o["callee_file"], o["callee_line"])] = o

    analysed = set(header["analysed_files"])
    oracle = compare.in_scope(set(records), analysed)

    repo = Path(args.repo).resolve()
    arm = arms_lib.get_arm(args.arm)
    art = arm.build(repo, repo_name=args.repo_name or repo.name, fresh=True)
    try:
        ours = compare.in_scope(compare.EXTRACT[args.arm](art), analysed)
        version = art.version
    finally:
        arm.close(art)

    missed = oracle - ours
    n = len(missed)
    counts = {"dispatch": 0, "closure_callee": 0, "closure_caller": 0,
              "dispatch_only": 0, "closure_only": 0, "both": 0, "neither": 0,
              "static_closure_callee": 0}
    for k in missed:
        o = records[k]
        dyn = bool(o["dynamic"])
        clo = is_literal(o["callee_func"]) or is_literal(o["caller_func"])
        counts["dispatch"] += dyn
        counts["closure_callee"] += is_literal(o["callee_func"])
        counts["closure_caller"] += is_literal(o["caller_func"])
        counts["both"] += dyn and clo
        counts["dispatch_only"] += dyn and not clo
        counts["closure_only"] += clo and not dyn
        counts["neither"] += not dyn and not clo
        # The bucket that actually sizes "symbolise func literals": a static
        # call whose target is a literal is unlocked by that change alone.
        # One that is also a dynamic dispatch needs the dispatch ceiling
        # cleared as well, and would not be recovered by symbolising anything.
        counts["static_closure_callee"] += is_literal(o["callee_func"]) and not dyn

    payload = {
        "arm": args.arm, "version": version, "repo": args.repo_name or repo.name,
        "oracle_edges": len(oracle), "matched": len(oracle & ours), "missed": n,
        "buckets": counts,
    }
    print(f"{args.arm} v{version} on {payload['repo']}: "
          f"{n} missed of {len(oracle)}\n")
    print("| bucket | share of the miss |")
    print("|---|---|")
    for label, key in (("dynamic dispatch only", "dispatch_only"),
                       ("dispatch **and** a closure endpoint", "both"),
                       ("closure endpoint only", "closure_only"),
                       ("neither", "neither")):
        c = counts[key]
        print(f"| {label} | {c:,} ({c / n:.1%}) |")
    print(f"\nclosure callee anywhere in the miss: {counts['closure_callee']:,}")
    print(f"closure caller anywhere in the miss:  {counts['closure_caller']:,} "
          f"(matchable under the current key)")
    print(f"static call to a closure callee:      {counts['static_closure_callee']:,} "
          f"(what symbolising a func literal would unlock on its own)")
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
