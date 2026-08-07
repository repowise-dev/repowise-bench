"""Reset per-arm indexes for named mui instances so the next build is COLD.

WHY THIS EXISTS, and why deleting the stamp alone is not enough.

`prebuild_mui_indexes.py` skips an (arm, tree) pair that carries a disk stamp, so
removing the stamp is what makes a rebuild happen at all. But every one of these
tools writes its index into a dotdir inside the tree and several of them update
INCREMENTALLY when that dotdir already exists. Removing only the stamp therefore
re-times an incremental refresh and reports it as a cold build, which is a
plausible wrong number of exactly the kind this workstream keeps paying for: it
would be FASTER than the truth, and the arm it would flatter is whichever arm
happens to support incremental best.

So this removes the arm's own index dir as well, and it removes ONLY that arm's
own dir inside that arm's own worktree (finding E3: arms never share a tree, so
there is nothing here that can touch another arm's output).

The worktree itself is left alone deliberately. `prepare_arm_tree` already
asserts its HEAD against the source checkout and recuts if it moved, so the tree
is known-good at the pinned base_commit and recutting 28,000 files to delete a
dotdir would be cost with no measurement value.

Usage:
  python scripts/reset_mui_arm_indexes.py --config configs/layera_mui_smoke.yaml \
      --instances cbmui_2bb4ea7a
  # add --dry-run to see what it would remove, which is the default posture for
  # anything that deletes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))

from harness import arms as arm_registry  # noqa: E402
from harness.swe_qa_runner import prepare_arm_tree, resolve_repo_path  # noqa: E402

# Same list prebuild_mui_indexes.py measures, so the two cannot drift apart in
# what they consider "the index".
INDEX_DIRS = [".repowise", ".codegraph", "graphify-out", ".code-review-graph",
              ".serena", ".cocoindex_code"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--instances", nargs="*", default=[],
                    help="task ids to reset; empty means ALL in the config")
    ap.add_argument("--arms", nargs="*", default=[],
                    help="arm names to reset; empty means ALL in the config")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    data_dir = config["benchmarks"]["swe_qa"]["data_dir"].lstrip("./")
    tasks = json.loads((BENCH / data_dir / "swe_qa" / "tasks.json")
                       .read_text(encoding="utf-8"))
    if args.instances:
        tasks = [t for t in tasks if t["id"] in set(args.instances)]
        if not tasks:
            print("no task matched --instances; nothing done")
            return 1
    arm_names = args.arms or config["arms"]

    verb = "would remove" if args.dry_run else "removed"
    n_stamps = n_dirs = 0
    for task in tasks:
        repo_path = resolve_repo_path(task["repo"], config["paths"]["repos_dir"])
        for arm_name in arm_names:
            tree = prepare_arm_tree(arm_name, repo_path, config)
            arm_registry.resolve_arm(
                arm_name, tree=tree, repo_path=repo_path,
                repo_name=task["repo"], arms_file=config.get("arms_file"),
                arms_dir=config.get("arms_dir"))

            safe = arm_name.replace("/", "-")
            stamp = tree / f".bench_prebuild__{safe}.json"
            if stamp.exists():
                if not args.dry_run:
                    stamp.unlink()
                n_stamps += 1
                print(f"  {verb} stamp  {task['id']:<18} {arm_name}")

            for d in INDEX_DIRS:
                p = tree / d
                if p.exists():
                    size = round(sum(f.stat().st_size for f in p.rglob('*')
                                     if f.is_file()) / 1e6, 1)
                    if not args.dry_run:
                        shutil.rmtree(p, ignore_errors=True)
                    n_dirs += 1
                    print(f"  {verb} index  {task['id']:<18} {arm_name:<18} "
                          f"{d} ({size} MB)")

    print(f"\n{verb}: {n_stamps} stamps, {n_dirs} index dirs")
    if args.dry_run:
        print("DRY RUN. Re-run without --dry-run to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
