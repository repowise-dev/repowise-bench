"""Prove `session_arms.tree_for` separates cells, in BOTH directions.

The defect this exists to prevent is not hypothetical and it is silent. Before
the fix, `tree_for` was:

    TREES_ROOT / TREE_ALIAS.get(arm, f"se-{arm}-rich")

The cell was HARDCODED into the tree name. `se-c0-bare-r1-rich` and
`se-c0-short-r{1..6}-rich` are already on disk from cell A, so a cell-B arm
called `c0-bare-r1` would have resolved to cell A's rich tree, `prepare` would
have skipped the `worktree add` because the path already existed, and the run
would have measured Textualize/rich while every row it wrote said hermes-agent.
Nothing downstream would have contradicted it. That is dead detector #13 with
the numbers already in the table.

BOTH DIRECTIONS, because a one-directional check here proves nothing:

  DIRECTION 1  every cell-A arm still resolves to the exact path on disk today.
               A "fix" that renamed cell A's trees would pass a cell-B-only
               check and orphan six paid-for runs.
  DIRECTION 2  no cell-B arm resolves to any path that exists on disk, and no
               cell-A tree and cell-B tree collide for any arm name.

Run:  python scripts/payload/prove_tree_slug.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import session_arms as sa  # noqa: E402

CELL_A = {"tree_slug": "rich"}          # explicit
CELL_A_LEGACY: dict = {}                # cell A's real yaml carries NO tree_slug
CELL_B = {"tree_slug": "hermes"}

# What cell A actually has on disk, listed rather than derived, so the check is
# against reality and not against the same expression it is testing.
ON_DISK_CELL_A = {
    "c0-bare": "se-c0bare-rich",
    "c0-bare-r1": "se-c0-bare-r1-rich",
    "c0-bare-r2": "se-c0-bare-r2-rich",
    "c0-bare-r3": "se-c0-bare-r3-rich",
    "c0-short-r1": "se-c0-short-r1-rich",
    "c0-short-r6": "se-c0-short-r6-rich",
    "rw-full": "se-rw-full-rich",
    "rw-mcp": "se-rw-mcp-rich",
    "rw-block": "se-rw-block-rich",
    "rw-hooks": "se-rw-hooks-rich",
    "codegraph": "se-codegraph-rich",
    "rw-pre-r1": "se-rw-pre-r1-rich",
    "rw-post-r1": "se-rw-post-r1-rich",
}

fails: list[str] = []
n = 0


def check(name: str, ok: bool, detail: object) -> None:
    global n
    n += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        fails.append(name)


# --- DIRECTION 1: cell A is unmoved ---------------------------------------
for arm, expected in ON_DISK_CELL_A.items():
    got = sa.tree_for(CELL_A_LEGACY, arm)
    check(f"D1 legacy-cellA {arm}", got.name == expected,
          f"{got.name} (expect {expected}, exists={got.is_dir()})")
    check(f"D1 explicit-cellA {arm}",
          sa.tree_for(CELL_A, arm).name == expected,
          sa.tree_for(CELL_A, arm).name)

# The strongest form of direction 1: the paths must EXIST. A rename would be
# caught here even if the expectation table above were wrong in the same way.
missing = [a for a in ON_DISK_CELL_A if not sa.tree_for(CELL_A_LEGACY, a).is_dir()]
check("D1 every cell-A tree still present on disk", not missing, missing or "all 13")

# --- DIRECTION 2: cell B cannot land on cell A ----------------------------
collisions = []
reused = []
for arm in sorted(sa.ARM_SHAPE):
    a = sa.tree_for(CELL_A_LEGACY, arm)
    b = sa.tree_for(CELL_B, arm)
    if a == b:
        collisions.append(arm)
    if b.is_dir():
        reused.append(f"{arm} -> {b.name}")

check("D2 no arm maps cell A and cell B to the same tree", not collisions,
      collisions or f"all {len(sa.ARM_SHAPE)} arms distinct")
check("D2 no cell-B tree already exists on disk", not reused,
      reused or f"all {len(sa.ARM_SHAPE)} cell-B trees are new")

# The specific arms this smoke runs, named rather than left to the sweep.
for arm, expected in (("c0-bare", "se-c0bare-hermes"), ("rw-full", "se-rw-full-hermes")):
    got = sa.tree_for(CELL_B, arm)
    check(f"D2 smoke arm {arm}", got.name == expected, got.name)

# --- The negative control for the checker itself --------------------------
# If `tree_for` ignored the slug, direction 2 would be vacuous. Prove the slug
# is load-bearing by showing a third slug moves the path again.
check("control: a third slug moves the path",
      sa.tree_for({"tree_slug": "zzz"}, "rw-full").name == "se-rw-full-zzz",
      sa.tree_for({"tree_slug": "zzz"}, "rw-full").name)

print(f"\n{n - len(fails)}/{n} checks passed")
sys.exit(1 if fails else 0)
