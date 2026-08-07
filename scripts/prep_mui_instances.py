"""Stage one mui checkout per instance, each at its own `base_commit`.

Mirrors `prep_go_tasks.py` deliberately. The value of a second language comes
from it being measured by the SAME code path as the first, so the layout is the
one the harness already understands and no harness change is needed:

    <repos_dir>/cbmui-<short>/material-ui

`swe_qa_runner.resolve_repo_path` maps a task's `repo: "org/name"` to
`<repos_dir>/org/name`, and `arms.arm_tree` names an arm's worktree
`lb-<arm>-<parent>-<name>`. Putting the INSTANCE ID in the parent segment is
what gives every instance its own arm tree, its own index and its own resume
key. So `lb-repowise-cbmui-2bb4ea7a-material-ui`, exactly as Go produced
`lb-repowise-go-cbgo-03f04397-cli`.

Checkouts are git WORKTREES off the one clone at `repos/mui/material-ui`, not
clones. mui has 24,715 reachable commits and the staged tree's `.git` is a
96-byte pointer, so fifteen instances share one object store instead of
duplicating history fifteen times.

THE SEALED 30 ARE NEVER STAGED. The split is read from `configs/mui_split.json`
and the intersection is asserted empty before anything is written.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))

CLONE = BENCH / "repos" / "mui" / "material-ui"
SPLIT = BENCH / "configs" / "mui_split.json"
PARQUET = BENCH / "data" / "contextbench" / "contextbench_verified.parquet"
SRC_ROOT = Path(r"C:\Users\ragha\Desktop\bakeoff\_cbmui_src")
TASKS = BENCH / "data" / "cb_mui" / "swe_qa" / "tasks.json"


def short(instance_id: str) -> str:
    """Last underscore-delimited segment, which the draw guarantees unique."""
    return instance_id.rsplit("__", 1)[-1]


def stage(short_id: str, base_commit: str) -> Path:
    dest = SRC_ROOT / f"cbmui-{short_id}" / "material-ui"
    if dest.exists():
        head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if head == base_commit:
            return dest
        subprocess.run(["git", "-C", str(CLONE), "worktree", "remove",
                        "--force", str(dest)], capture_output=True, text=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(CLONE), "worktree", "prune"],
                   capture_output=True, text=True)
    p = subprocess.run(["git", "-C", str(CLONE), "worktree", "add", "--detach",
                        str(dest), base_commit], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"staging {short_id} failed: {p.stderr[:400]}")
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if head != base_commit:
        raise SystemExit(f"{short_id}: staged HEAD {head} != {base_commit}")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="stage only the 2 smoke instances, not all 15")
    args = ap.parse_args()

    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    want = split["smoke"] if args.smoke else split["dev"]
    sealed = set(split["sealed"])
    if set(want) & sealed:
        print("FAIL: the staging set intersects the sealed 30")
        return 1
    print(f"staging {len(want)} instances, sealed {len(sealed)} untouched")

    df = pd.read_parquet(PARQUET).set_index("instance_id")
    tasks = []
    for iid in want:
        row = df.loc[iid]
        sid = short(iid)
        tree = stage(sid, row["base_commit"])
        gold = sorted({s["file"] for s in json.loads(row["gold_context"])})
        tasks.append({
            "id": f"cbmui_{sid}",
            "instance_id": iid,
            "repo": f"cbmui-{sid}/material-ui",
            "upstream_repo": row["repo"],
            "base_commit": row["base_commit"],
            "split_name": "contextbench_mui_dev",
            "problem_statement": row["problem_statement"],
            "gold_files": gold,
            "gold_spans": row["gold_context"],
            "staged_tree": str(tree),
        })
        print(f"  {sid} @ {row['base_commit'][:10]} gold={len(gold):>3} -> {tree}")

    TASKS.parent.mkdir(parents=True, exist_ok=True)
    TASKS.write_text(json.dumps(tasks, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {len(tasks)} tasks -> {TASKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
