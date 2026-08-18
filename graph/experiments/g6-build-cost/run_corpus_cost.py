"""G6 across the whole corpus: what every arm costs to build every repository.

`run.py` beside this file takes one repository and two arms. That was right when
G6 was six repositories and a question about whether we were slow; it is not
enough to publish. Build time existed for 6 repositories and **peak RSS for the
same 6**, which is one sixth of the corpus and cannot carry a memory claim.

This driver runs the same measurement over every repository `corpus.lock`
selects, on every registered arm, and it exists as a separate file rather than a
flag on `run.py` because the two answer different questions: `run.py` is the
single-cell instrument you reach for when one number looks wrong, and this is
the sweep.

    python graph/experiments/g6-build-cost/run_corpus_cost.py --runs 3
    python graph/experiments/g6-build-cost/run_corpus_cost.py --repos gitleaks --runs 1
    python graph/experiments/g6-build-cost/run_corpus_cost.py --dry-run

## Four things that would silently ruin this run, and what stops each

**1. Our arm must be the subprocess arm.** In process our peak is recorded as
`None` on purpose -- there is no child to attach a job object to, so the number
would be this interpreter's peak rather than the build's. `repowise-subprocess`
is therefore in `DEFAULT_ARMS` and `repowise` is not, and passing the in-process
arm is refused rather than quietly producing an empty memory column.

**2. Nothing is restored from the artifact cache.** Every build goes through
`arm.build(fresh=True)`, never `build_cached`. A restored artifact carries the
cost of the build that filled the cache, on some other day, and the existing
35-repository run is stamped `publishable: false` for exactly that reason. The
resume in this script is at the level of a **finished cell**, not a build: a
crash at repository 30 re-runs one cell, and every build inside a cell it does
run is real.

**3. Every cell gets a discarded warmup.** gitleaks measured 6.92s cold against
1.90s warm -- a 3.6x spread that lands entirely on whichever arm happens to run
first. `--no-warmup` exists for development and stamps the run unpublishable.

**4. One run per config is honest for memory and not for time.** Peak RSS is
stable, so a single build measures it. Wall clock is not: methodology puts
single-run timings at roughly two points of swing, which is the same order as
the differences between arms on small repositories. So `--runs` defaults to 3
and every published time is a **median**, `min`/`max` carried beside it so a
reader can see the spread rather than trust the middle.

## Seriality, which is a property of the measurement and not of the hardware

One build at a time, always. Peak RSS measured while another build runs is not
that build's peak, and free memory on the measurement machine is 3.4-4.6 GB
against a competitor that peaks near 1.75 GB. Nothing here is concurrent and
nothing here should be made concurrent.

## What is written, and when

`results/graph/g6-corpus/<date>-<commit>/`:

* `cells/<repo>__<arm>.json` -- **written the moment the cell finishes**, so a
  crash costs one cell rather than the night. This is also the resume unit.
* `result.json` -- the aggregate, rewritten after every cell so the run is
  readable while it is still going.

A failed arm is recorded as a failed cell and the sweep continues. An arm that
cannot build one repository is a finding about that arm; it is not a reason to
lose the other 34.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

import arms as arms_lib  # noqa: E402
import corpus as corpus_lib  # noqa: E402
import provenance  # noqa: E402

SCHEMA = "graph-cost/1"

# `repowise-subprocess`, never `repowise`: see the docstring, point 1.
DEFAULT_ARMS = [
    "repowise-subprocess",
    "codegraph",
    "graphify",
    "code-review-graph",
    "codebase-memory-mcp",
]

# The in-process arm reports `peak_rss_mb=None` by construction. Accepting it
# here would produce a memory table with our own row empty, which is the exact
# hole this sweep exists to fill.
_NO_MEMORY_ARMS = {"repowise"}


def cell_path(out_dir: Path, repo: str, arm: str) -> Path:
    return out_dir / "cells" / f"{repo}__{arm}.json"


def summarise(runs: list[dict]) -> dict:
    """Median for time, and for memory the median of whatever was reported.

    Median rather than mean on both: one slow run from a background process, or
    one build that happened to land beside a browser tab, should not move a
    published number. `min`/`max` travel with it so the spread is visible.
    """
    times = [r["seconds"] for r in runs if r.get("seconds") is not None]
    peaks = [r["peak_rss_mb"] for r in runs if r.get("peak_rss_mb")]
    out: dict = {
        "n_runs": len(runs),
        "median_seconds": round(statistics.median(times), 3) if times else None,
        "min_seconds": round(min(times), 3) if times else None,
        "max_seconds": round(max(times), 3) if times else None,
        "median_peak_rss_mb": round(statistics.median(peaks), 1) if peaks else None,
        "min_peak_rss_mb": round(min(peaks), 1) if peaks else None,
        "max_peak_rss_mb": round(max(peaks), 1) if peaks else None,
        "index_size_mb": next(
            (r["index_size_mb"] for r in runs if r.get("index_size_mb") is not None), None
        ),
    }
    # The spread the methodology warns about, made explicit rather than left for
    # a reader to compute. A cell wider than this is worth looking at before it
    # is quoted.
    if out["median_seconds"] and out["min_seconds"] is not None:
        span = out["max_seconds"] - out["min_seconds"]
        out["time_spread_pct"] = round(100.0 * span / out["median_seconds"], 1)
    return out


def measure_cell(arm, repo_path: Path, repo_name: str, runs: int, warmup: bool) -> dict:
    """One (repository, arm) cell: a discarded warmup, then *runs* timed builds.

    Always `fresh=True`. A cache hit is not a build and must never reach a cost
    table -- see the docstring, point 2.
    """
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if warmup:
        art = arm.build(repo_path, repo_name=repo_name, fresh=True)
        arm.close(art)

    samples: list[dict] = []
    version = None
    for _ in range(runs):
        art = arm.build(repo_path, repo_name=repo_name, fresh=True)
        try:
            version = art.version
            samples.append(
                {
                    "seconds": round(art.seconds, 3) if art.seconds is not None else None,
                    "peak_rss_mb": (
                        round(art.peak_rss_mb, 1) if art.peak_rss_mb is not None else None
                    ),
                    "index_size_mb": (
                        round(art.index_size_mb, 2) if art.index_size_mb is not None else None
                    ),
                }
            )
        finally:
            arm.close(art)

    return {
        "arm": arm.name,
        "version": version,
        "repo": repo_name,
        "started_at": started,
        "warmup_discarded": warmup,
        "runs": samples,
        **summarise(samples),
    }


def aggregate(out_dir: Path, header: dict) -> dict:
    """Rebuild `result.json` from whatever cells exist on disk right now.

    Reads the cells rather than holding state in memory, so the aggregate a
    resumed run writes is identical to the one an uninterrupted run would have.
    """
    doc = {**header, "repos": {}}
    for f in sorted((out_dir / "cells").glob("*.json")):
        try:
            cell = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        repo = cell.get("repo") or f.stem.split("__")[0]
        slot = doc["repos"].setdefault(
            repo,
            {
                "language": cell.get("_language"),
                "kind": cell.get("_kind"),
                "files_at_pin": cell.get("_files"),
                "pin": cell.get("_pin"),
                "arms": {},
            },
        )
        slot["arms"][cell["arm"]] = {k: v for k, v in cell.items() if not k.startswith("_")}
    doc["counts"] = {
        "repos": len(doc["repos"]),
        "cells": sum(len(r["arms"]) for r in doc["repos"].values()),
        "failed_cells": sum(
            1 for r in doc["repos"].values() for c in r["arms"].values() if "error" in c
        ),
    }
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    ap.add_argument("--repos", default="all")
    ap.add_argument("--kinds", default="")
    ap.add_argument("--languages", default="")
    ap.add_argument("--lock", default=str(corpus_lib.LOCK))
    ap.add_argument("--max-files", type=int, default=corpus_lib.DEFAULT_MAX_FILES)
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--runs", type=int, default=3, help="timed builds per cell; median published")
    ap.add_argument("--no-warmup", action="store_true", help="dev only; not publishable")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-root", default=str(BENCH / "results/graph/g6-corpus"))
    ap.add_argument("--out-dir", default=None, help="resume into this exact directory")
    args = ap.parse_args()

    arm_names = arms_lib.arm_names() if args.arms == "all" else [
        a.strip() for a in args.arms.split(",") if a.strip()
    ]
    bad = sorted(set(arm_names) & _NO_MEMORY_ARMS)
    if bad:
        raise SystemExit(
            f"refusing to measure {bad}: the in-process arm records peak_rss_mb=None by "
            "construction, so it would produce a memory column with our own row empty. "
            "Use repowise-subprocess."
        )

    selection = corpus_lib.select(
        lock=args.lock, repos=args.repos, kinds=args.kinds,
        languages=args.languages, max_files=args.max_files,
    )
    for line in selection.describe():
        print(line)
    if args.dry_run:
        for r in selection.rows:
            print(f"  {r['name']:26s} {r['language'] or '?':12s} {r['files']:6d} files")
        print(f"\n{len(selection.rows)} repos x {len(arm_names)} arms = "
              f"{len(selection.rows) * len(arm_names)} cells, "
              f"{(args.runs + (0 if args.no_warmup else 1))} builds each")
        return 0

    # Gate on the tree our ingestion is actually imported from, which is the
    # measurement worktree and not this checkout. Gating on the bench repo would
    # pass while measuring someone else's half-finished change.
    import repowise.core

    measured_tree = Path(repowise.core.__file__).resolve()
    for parent in measured_tree.parents:
        if (parent / ".git").exists():
            measured_tree = parent
            break
    else:
        raise SystemExit(
            f"no git checkout above {repowise.core.__file__}; refusing to run a "
            "measurement whose source tree cannot be identified"
        )

    publishable = provenance.require_clean(measured_tree, allow_dirty=args.allow_dirty)
    reasons: list[str] = []
    if not publishable:
        reasons.append("--allow-dirty: the measured tree has uncommitted changes")
    if args.no_warmup:
        publishable = False
        reasons.append("--no-warmup: the first build on a repository is cold, and gitleaks "
                       "measured 6.92s cold against 1.90s warm")
    if args.runs < 3:
        reasons.append(
            f"--runs {args.runs}: peak RSS is stable enough to publish from a single build, "
            "but wall clock is not -- single-run timings swing about two points, so the "
            "seconds column from this run is indicative and the memory column is the result"
        )

    header = {
        "schema": SCHEMA,
        "experiments": ["g6-build-cost"],
        "provenance": provenance.stamp(
            "g6-corpus-cost",
            repowise_repo=measured_tree,
            bench_repo=BENCH,
            publishable=publishable,
            extra={
                "warmup": not args.no_warmup,
                "runs_per_cell": args.runs,
                "arms_requested": arm_names,
                "measured_tree": str(measured_tree),
                "cost_from_cache": False,
                "caveats": reasons,
                "corpus": {
                    "lock": str(args.lock),
                    "selected": selection.names(),
                    "skipped_oversize": [r["name"] for r in selection.oversize],
                    "kept_oversize": [r["name"] for r in selection.kept_oversize],
                    "max_files": args.max_files,
                },
            },
        ),
    }

    commit = (header["provenance"]["repowise"]["head_short"] or "nocommit")[:8]
    out_dir = (
        Path(args.out_dir) if args.out_dir
        else Path(args.out_root) / f"{date.today().isoformat()}-{commit}"
    )
    (out_dir / "cells").mkdir(parents=True, exist_ok=True)
    print(f"\nwriting cells to {out_dir / 'cells'}")

    test_repos = Path(args.test_repos).resolve()
    total = len(selection.rows) * len(arm_names)
    done = 0
    t0 = time.time()

    for entry in selection.rows:
        repo_name = entry["name"]
        repo_path = test_repos / repo_name
        if not repo_path.is_dir():
            print(f"  !! no checkout at {repo_path}, skipping", file=sys.stderr, flush=True)
            done += len(arm_names)
            continue
        print(f"\n=== {repo_name} ({entry.get('language')}/{entry.get('kind')}, "
              f"{entry['files']} files) ===", flush=True)

        for arm_name in arm_names:
            done += 1
            path = cell_path(out_dir, repo_name, arm_name)
            if path.is_file():
                print(f"  [{done}/{total}] {arm_name:22s} cached cell, skipping", flush=True)
                continue
            print(f"  [{done}/{total}] {arm_name:22s} ", end="", flush=True)
            try:
                arm = arms_lib.get_arm(arm_name)
                cell = measure_cell(
                    arm, repo_path, repo_name, args.runs, warmup=not args.no_warmup
                )
                peak = cell["median_peak_rss_mb"]
                print(
                    f"{cell['median_seconds']}s "
                    f"[{cell['min_seconds']}, {cell['max_seconds']}] "
                    f"peak {peak if peak is not None else '-'} MB",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - a failed arm is a recorded result
                cell = {
                    "arm": arm_name,
                    "repo": repo_name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-3000:],
                }
                print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)

            cell.update({
                "_language": entry.get("language"),
                "_kind": entry.get("kind"),
                "_files": entry.get("files"),
                "_pin": entry.get("pin"),
            })
            # Written before anything else happens, so a crash on the next cell
            # cannot take this one with it.
            path.write_text(json.dumps(cell, indent=2), encoding="utf-8")
            (out_dir / "result.json").write_text(
                json.dumps(aggregate(out_dir, header), indent=2), encoding="utf-8"
            )

    doc = aggregate(out_dir, header)
    (out_dir / "result.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"\n{doc['counts']['cells']} cells over {doc['counts']['repos']} repos, "
          f"{doc['counts']['failed_cells']} failed, {(time.time() - t0) / 60:.1f} min")
    print(f"wrote {out_dir / 'result.json'}")
    if not publishable:
        print("STAMPED NOT PUBLISHABLE")
    for r in reasons:
        print(f"  - {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
