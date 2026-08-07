"""Build every arm's index for every staged mui instance, one at a time, timed.

Uses the harness's own `prepare_arm_tree` / `ensure_arm_index`, so what is built
here is exactly what a graded cell would have built, including the D13 gate that
refuses a repowise index whose vectors are 8-dimensional.

DIFFERENT FROM THE GO PREBUILD IN ONE RESPECT, AND IT MATTERS.
`layerb-go-contextbench/scripts/prebuild_indexes.py` ends by disclaiming its own
seconds: "these are provenance, not a published build timing (standing rule 7)".
Here the timing IS the deliverable, so the rule is satisfied rather than
disclaimed: builds run strictly sequentially in one process, and the operator
does not start this while an agent run is in flight. If that is violated the
numbers are provenance again, and RESULT.md must say so.

DISK IS MEASURED, NOT ASSUMED. An overnight run already died once at 5.7 GB
free. Every build records the resulting index size and the free space after it,
so the 13 that follow are sized from measurement.

A DISK STAMP per (arm, tree), because `ensure_arm_index` memoises in a
module-level dict only, so a second process rebuilds everything it already has.
The Go run found that out the hard way: `prebuild_indexes.py` did not prevent
inline builds, which put an E11 confound in that run's cost column. The stamp is
written only AFTER a build exits 0, never before, because `.repowise/wiki.db`
exists minutes into a build that is later killed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))

from harness import arms as arm_registry  # noqa: E402
from harness.metrics import RunMetrics  # noqa: E402
from harness.swe_qa_runner import (  # noqa: E402
    ensure_arm_index, prepare_arm_tree, resolve_repo_path,
)

INDEX_DIRS = [".repowise", ".codegraph", "graphify-out", ".code-review-graph",
              ".serena", ".cocoindex_code"]

LOCK = BENCH / ".prebuild_mui.lock"


def acquire_lock() -> None:
    """One prebuild at a time, across PROCESSES, or the timings are worthless.

    The disk stamp makes a rebuild idempotent but it is read once per instance
    at the top of the loop, so two prebuilds started minutes apart do not see
    each other and run concurrently. That happened on 2026-08-07: a launch the
    operator's harness reported as KILLED was still running, and three builds
    started afterwards were timed while it held the machine. Contention is
    arm-specific and uncorrectable (finding E1, and Layer A section B measured
    it at 1.03x to 3.31x depending on the arm), so every second measured in that
    window had to be thrown away.

    A stale lock is detected rather than trusted: if the recorded pid is gone,
    the lock is taken over. A live pid is a hard exit, because the alternative
    is silently producing numbers that cannot be published.
    """
    if LOCK.exists():
        try:
            held = json.loads(LOCK.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            held = {}
        pid = held.get("pid")
        alive = False
        if isinstance(pid, int):
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True)
            alive = str(pid) in (r.stdout or "")
        if alive:
            raise SystemExit(
                f"REFUSING TO START: another prebuild is running (pid {pid}, "
                f"started {held.get('started')}, config "
                f"{held.get('config')}).\nTwo concurrent prebuilds make every "
                f"build second in this window unpublishable. Kill it or wait, "
                f"then retry.\nLock: {LOCK}"
            )
        print(f"[lock] taking over a stale lock from dead pid {pid}")
    LOCK.write_text(json.dumps({
        "pid": os.getpid(),
        "started": datetime.now().isoformat(timespec="seconds"),
        "config": " ".join(sys.argv[1:]),
    }), encoding="utf-8")


def release_lock() -> None:
    try:
        if LOCK.exists() and json.loads(
                LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
            LOCK.unlink()
    except (OSError, json.JSONDecodeError):
        pass


def dir_size_mb(p: Path) -> float:
    if not p.exists():
        return 0.0
    total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return round(total / 1e6, 1)


def index_size_mb(tree: Path) -> dict:
    return {d: dir_size_mb(tree / d) for d in INDEX_DIRS
            if (tree / d).exists()}


def stamp_path(tree: Path, arm_name: str) -> Path:
    safe = arm_name.replace("/", "-")
    return tree / f".bench_prebuild__{safe}.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--limit", type=int, default=0)
    # Named instances, because `--limit` takes the FIRST n of tasks.json and a
    # cost smoke needs the two ENDS of the size range. cocoindex entered after
    # the other four and its build cost was unknown across a 12x span, so the
    # bracket pair (smallest, largest) is what fits a curve; the middle thirteen
    # tell you nothing you cannot interpolate.
    ap.add_argument("--instances", nargs="*", default=None,
                    help="task ids or instance_ids to build; default all")
    args = ap.parse_args()
    acquire_lock()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    data_dir = config["benchmarks"]["swe_qa"]["data_dir"].lstrip("./")
    tasks = json.loads((BENCH / data_dir / "swe_qa" / "tasks.json")
                       .read_text(encoding="utf-8"))
    if args.instances:
        want = set(args.instances)
        tasks = [t for t in tasks if t["id"] in want or t["instance_id"] in want]
        missing = want - {t["id"] for t in tasks} - {t["instance_id"] for t in tasks}
        if missing:
            print(f"unknown instances: {sorted(missing)}")
            return 2
    if args.limit:
        tasks = tasks[:args.limit]
    arm_names = config["arms"]

    out_dir = BENCH / config["paths"]["results_dir"].lstrip("./")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "prebuild.json"

    free0 = shutil.disk_usage("C:\\").free / 1e9
    print(f"{len(tasks)} instances x {len(arm_names)} arms = "
          f"{len(tasks) * len(arm_names)} builds")
    print(f"free disk before: {free0:.1f} GB\n")

    rows, t_all = [], time.time()
    for task in tasks:
        repo_name = task["repo"]
        repo_path = resolve_repo_path(repo_name, config["paths"]["repos_dir"])
        for arm_name in arm_names:
            tree = prepare_arm_tree(arm_name, repo_path, config)
            arm = arm_registry.resolve_arm(
                arm_name, tree=tree, repo_path=repo_path, repo_name=repo_name,
                arms_file=config.get("arms_file"),
                arms_dir=config.get("arms_dir"))
            sp = stamp_path(tree, arm_name)
            if sp.exists():
                row = json.loads(sp.read_text(encoding="utf-8"))
                row["skipped"] = "already built, stamp on disk"
                rows.append(row)
                print(f"  {task['id']:<22} {arm_name:<18} SKIP", flush=True)
                continue

            # AN INDEX DIR WITH NO STAMP IS PARTIAL STATE, AND TIMING IT IS A
            # RESUMED BUILD REPORTED AS A COLD ONE.
            #
            # The stamp is written only after rc=0, so a dotdir without one is
            # the residue of a build that was killed. `index_repo_at` passes
            # `--resume` (deliberately: it picks up partial pipeline
            # checkpoints), so the next build continues from that residue and
            # finishes fast. Measured 2026-08-07: `cbmui_1de1bd3c` came back at
            # 166.8 s for 8,308 files, against 330.7 s for the 6,668-file
            # instance beside it, because a killed batch had left it half built.
            #
            # This is the third face of one bug in one session: the stamp alone
            # re-times an incremental refresh, the harness cache returns 0.0 s
            # without building, and partial tree state resumes. All three report
            # a number that looks like a cold build and is faster than one.
            partial = [d for d in INDEX_DIRS if (tree / d).exists()]
            if partial:
                raise SystemExit(
                    f"REFUSING TO BUILD {task['id']} / {arm_name}: the tree "
                    f"carries {partial} but no stamp, so it is a killed "
                    f"build's residue and `--resume` would continue it.\n"
                    f"Timing that reports a resumed build as a cold one.\n"
                    f"Reset it first:\n"
                    f"  python scripts/reset_mui_arm_indexes.py --config "
                    f"{args.config} --instances {task['id']} --arms {arm_name}"
                )

            m = RunMetrics(task_id=task["id"], benchmark="swe_qa",
                           condition=arm_name, repo=repo_name)
            t0 = time.time()
            try:
                ev = dict(ensure_arm_index(arm, tree, repo_name, config, m))
            except Exception as e:  # noqa: BLE001
                ev = {"failed": f"{type(e).__name__}: {e}"}
            ev.update({
                "task_id": task["id"], "instance_id": task["instance_id"],
                "arm": arm_name, "tree": str(tree),
                "base_commit": task["base_commit"],
                "wall_seconds": round(time.time() - t0, 1),
                "index_size_mb": index_size_mb(tree),
                "free_gb_after": round(shutil.disk_usage("C:\\").free / 1e9, 1),
            })
            rows.append(ev)
            flag = "FAIL" if ev.get("failed") else "ok"
            print(f"  {task['id']:<22} {arm_name:<18} {flag:<4} "
                  f"{ev['wall_seconds']:>7.1f}s  {ev['index_size_mb']}  "
                  f"free={ev['free_gb_after']}GB"
                  + (f"  :: {ev['failed'][:120]}" if ev.get("failed") else ""),
                  flush=True)
            if not ev.get("failed"):
                sp.write_text(json.dumps(ev, indent=2, default=str),
                              encoding="utf-8")
            out.write_text(json.dumps(rows, indent=2, default=str),
                           encoding="utf-8")

    bad = [r for r in rows if r.get("failed")]
    free1 = shutil.disk_usage("C:\\").free / 1e9
    print(f"\n{len(rows)} builds in {round(time.time() - t_all, 1)}s, "
          f"failures {len(bad)}")
    print(f"disk consumed: {free0 - free1:.1f} GB  (free now {free1:.1f} GB)")
    print(f"wrote {out}")
    if bad:
        print("\nFAILED, and each is a gate item per PREREGISTRATION section 5:")
        for r in bad:
            print(f"  {r.get('task_id')} {r.get('arm')}: {r.get('failed')}")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        release_lock()
