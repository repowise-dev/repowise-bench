"""Build every competitor artifact across the corpus, once, into the cache.

This is the expensive half of the benchmark and the half that does not depend
on our commit at all. A CodeGraph index of `hugo` at a given pin is the same
index whatever our resolver did this week, so it is paid for once here and
every later graph session re-runs only our own column.

    python graph/experiments/prebuild_artifacts.py --arms codegraph,graphify
    python graph/experiments/prebuild_artifacts.py --kinds library,application,framework
    python graph/experiments/prebuild_artifacts.py --dry-run

Serial by construction, one arm and one repository at a time. Two heavy arms
concurrently is how this machine froze in August, and CodeGraph peaked at
1,752 MB on a 4,000-file repository against 3.4 GB of free RAM. `--max-files`
defaults to the corpus size cap for the same reason.

Our own arms are skipped: they hold nothing on disk to cache and rebuild in
seconds, so storing them would be cost with no saving. A failure is recorded
and the run continues -- an arm that cannot index a language is a G7 finding,
and stopping the sweep on the first one would hide the other twenty-nine.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

import arms as arms_lib  # noqa: E402
import artifact_cache  # noqa: E402

LOCK = GRAPH / "corpus" / "corpus.lock"
DEFAULT_ARMS = "codegraph,graphify,code-review-graph"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", default=DEFAULT_ARMS)
    ap.add_argument("--repos", default="", help="comma-separated names")
    ap.add_argument("--kinds", default="", help="library,application,framework")
    ap.add_argument("--languages", default="")
    ap.add_argument("--lock", default=str(LOCK))
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument(
        "--max-files", type=int, default=2000,
        help="skip repositories larger than this; the corpus size cap exists "
             "because graphify took 176s on dub against 5s on gitleaks",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-root", default=str(BENCH / "results/graph/prebuild"))
    args = ap.parse_args()

    doc = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    rows = [r for r in doc["repos"] if r.get("usable", True)]
    if args.repos:
        want = set(args.repos.split(","))
        rows = [r for r in rows if r["name"] in want]
    elif args.kinds:
        want = set(args.kinds.split(","))
        rows = [r for r in rows if r.get("kind") in want]
    else:
        # Without a filter this is the pinned corpus, not all 88 checkouts.
        rows = [r for r in rows if r.get("kind")]
    if args.languages:
        want = set(args.languages.split(","))
        rows = [r for r in rows if r.get("language") in want]

    # The size cap exists to stop spares costing an hour each. It must not
    # delete a per-language claim: if a (language, kind) slot has no
    # under-cap repository at all, its smallest repository is kept however
    # large, because the alternative is a G7 table that quietly has no Rust
    # framework row and no PHP framework row and looks complete.
    under = {r["name"] for r in rows if r["files"] <= args.max_files}
    covered = {
        (r["language"], r["kind"]) for r in rows if r["name"] in under
    }
    kept_oversize: list[dict] = []
    for slot in sorted({(r["language"], r["kind"]) for r in rows} - covered):
        candidates = [r for r in rows if (r["language"], r["kind"]) == slot]
        kept_oversize.append(min(candidates, key=lambda r: r["files"]))
    keep = under | {r["name"] for r in kept_oversize}

    oversize = [r for r in rows if r["name"] not in keep]
    rows = [r for r in rows if r["name"] in keep]
    if kept_oversize:
        print(
            "kept over the cap as the only repository in its (language, kind): "
            + ", ".join(f"{r['name']}({r['files']}, {r['language']}/{r['kind']})"
                        for r in sorted(kept_oversize, key=lambda r: r["files"]))
        )
    rows.sort(key=lambda r: r["files"])  # cheapest first, so a broken arm shows early

    test_repos = Path(args.test_repos).resolve()
    arm_names = args.arms.split(",")
    print(f"{len(rows)} repositories x {len(arm_names)} arms")
    if oversize:
        # Never a silent cap. A sweep that quietly dropped the big repositories
        # reads as "we covered the corpus" when it did not.
        print(f"skipped over --max-files={args.max_files}: "
              + ", ".join(f"{r['name']}({r['files']})" for r in oversize))
    if args.dry_run:
        for r in rows:
            print(f"  {r['name']:24s} {r['language'] or '?':12s} {r['files']:6d} files")
        return 0

    log: list[dict] = []
    for row in rows:
        repo_path = test_repos / row["name"]
        if not repo_path.is_dir():
            print(f"  !! no checkout at {repo_path}; run graph/corpus/fetch.py")
            log.append({"repo": row["name"], "error": "no checkout"})
            continue
        print(f"\n=== {row['name']} ({row['language']}, {row['files']} files) ===", flush=True)
        for arm_name in arm_names:
            arm = arms_lib.get_arm(arm_name)
            if not hasattr(arm, "cache_payload"):
                print(f"  {arm_name}: nothing to cache, skipped")
                continue
            hit = artifact_cache.lookup(arm_name, arm.version(), row["name"], row["pin"])
            if hit is not None:
                print(f"  {arm_name}: already cached")
                log.append({"repo": row["name"], "arm": arm_name, "status": "hit"})
                continue
            print(f"  {arm_name} building ...", end="", flush=True)
            started = time.perf_counter()
            try:
                art = arms_lib.build_cached(
                    arm, repo_path, repo_name=row["name"], pin=row["pin"], fresh=False
                )
            except Exception as exc:  # noqa: BLE001 - a failed arm is a result
                print(f" FAILED: {type(exc).__name__}: {exc}", flush=True)
                log.append({
                    "repo": row["name"], "arm": arm_name, "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                })
                continue
            try:
                counts = {
                    "files_seen": len(arm.files_seen(art)),
                    "symbol_files": len(arm.symbol_files(art)),
                    "call_edges": len(arm.call_edges(art)),
                    "cross_file_edges": len(arm.cross_file_edges(art, arms_lib.CALLS)),
                }
            finally:
                arm.close(art)
            print(f" {round(time.perf_counter() - started, 1)}s  {counts}", flush=True)
            log.append({
                "repo": row["name"], "arm": arm_name, "status": "built",
                "version": arm.version(), "pin": row["pin"], "counts": counts,
            })

    out_dir = Path(args.out_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{date.today().isoformat()}-prebuild.json"
    out.write_text(
        json.dumps(
            {
                "schema": "prebuild/1",
                "arms": arm_names,
                "max_files": args.max_files,
                "skipped_oversize": [r["name"] for r in oversize],
                "log": log,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    built = sum(1 for e in log if e.get("status") == "built")
    failed = [e for e in log if e.get("status") == "failed"]
    print(f"\nbuilt {built}, cached-already {sum(1 for e in log if e.get('status') == 'hit')}, "
          f"failed {len(failed)}")
    for e in failed:
        print(f"  FAILED {e['arm']} on {e['repo']}: {e['error'][:120]}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
