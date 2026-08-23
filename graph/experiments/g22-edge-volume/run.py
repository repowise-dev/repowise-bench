"""G22: true-edge volume, both folds, both arms, one build.

The comparison this exists to make was not computable before. Each arm was
reported on whichever fold it happened to compute, so a raw edge lead could not
be turned into a statement about content: a tool that resolves one call site per
caller-callee pair and a tool that resolves twenty look identical under a pair
fold and 20x apart under a site fold.

Two facts settled before this was written, both by measurement rather than
assumption:

* Both arms' `call_edges` already fold to the same thing -- distinct
  `(caller_file, line, callee_identity)` -- so the site fold was never the
  asymmetry it was believed to be.
* The peer's `edges` table is genuinely per call site, not pre-folded to pairs.
  Across eighteen indexes the site count runs 1.14x to 4.17x the pair count; a
  pre-folded table would read 1.00x throughout.

So the remaining work was to emit BOTH folds off ONE build, which is what this
does. It reports volume. It says nothing about whether an edge is right, and a
volume lead is not a quality claim -- G1 and G8 are where that is decided.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "lib"))

import arms as arms_lib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--arms", default="repowise,codebase-memory-mcp,codegraph")
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    lock = json.loads((BENCH / "graph/corpus/corpus.lock").read_text(encoding="utf-8"))
    spec = next(r for r in lock["repos"] if r["name"] == args.repo)
    repo_path = Path(args.test_repos) / args.repo

    out = {"repo": args.repo, "language": spec["language"], "pin": spec["pin"], "arms": {}}
    for name in args.arms.split(","):
        arm = arms_lib.get_arm(name)
        cached = name != "repowise" and hasattr(arm, "cache_payload")
        art = (arms_lib.build_cached(arm, repo_path, repo_name=args.repo, pin=spec["pin"])
               if cached else arm.build(repo_path, repo_name=args.repo, fresh=True))
        try:
            sites = arm.call_edges(art)
            pairs = arm.call_pairs(art)
            out["arms"][name] = {
                "sites": len(sites),
                "pairs": len(pairs),
                "sites_per_pair": round(len(sites) / len(pairs), 3) if pairs else None,
            }
        finally:
            arm.close(art)

    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"# G22 edge volume - {args.repo} ({spec['language']}) @ {spec['pin'][:8]}")
    print(f"\n| arm | call sites | (caller, callee) pairs | sites per pair |")
    print("|---|---:|---:|---:|")
    for name, v in out["arms"].items():
        print(f"| {name} | {v['sites']} | {v['pairs']} | {v['sites_per_pair']} |")
    print("\n# Volume only. Whether an edge is right is G1 and G8, not this table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
