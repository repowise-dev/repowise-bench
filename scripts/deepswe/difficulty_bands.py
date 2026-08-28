"""Difficulty-banded task selection for DeepSWE, calibrated to gpt-5.6-terra.

Why this exists
---------------
The 2026-08-09 smoke selected tasks by a patch-size proxy. That proxy was then
measured and does not predict difficulty: both "hard" tasks ranked 48th and 53rd
of 113 on the real column and the bare control passed both. This script replaces
it with the only real per-task difficulty data that exists publicly, and adds a
calibration step the earlier script did not have.

Difficulty source
-----------------
kimjune01/deepswe-run, results/v1.1/heatmap.json: 113 tasks x 8 frontier models,
8,484 scored attempts, up to 20 per cell. Pooled corpus solve rate 50.5%.

The calibration, and why a raw pooled rate is not enough
-------------------------------------------------------
The pooled column is 8 FRONTIER models. The run under design uses gpt-5.6-terra,
which is weaker, so pooled rate overstates what terra will solve. Worse, it does
not overstate it uniformly. akonlabs published a three-tier stratification of a
terra bare arm (17 easy / 38 moderate / 58 hard, bare 80.8 / 49.9 / 15.9 pct).
Those tier sizes partition our 113 exactly, and applying them to our pooled
ranking gives tier means of 84.1 / 65.7 / 30.6 pct. So the terra-to-frontier
ratio is 0.96, 0.76, 0.52 across the tiers: terra falls away fastest exactly
where the tasks are hardest. A single flat ratio would mis-band the hard end
badly, so this module interpolates piecewise between those three anchors.

Those anchors are external and unverified by us. They are used only to CHOOSE
tasks, never to report a result: our own bare arm is measured, not assumed.

Exclusions
----------
  * 4 tasks whose reference solutions fail their own verifiers (kimjune01 audit)
  * 11 giveaway tasks whose prompt names at least one of their own gold files,
    computed locally from instruction.md against solution.patch
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

# Reference solutions that fail their own verifiers. A benchmark defect must
# never be read as model difficulty.
DEFECTIVE_GOLD = frozenset({
    "langchain-request-coalescing",
    "narwhals-rolling-window-suite",
    "prometheus-transactional-reload-status",
    "skrub-duration-encoding",
})

# The published terra bare arm, by difficulty tier:
# (our pooled mean for that tier, their reported bare solve rate).
TERRA_ANCHORS = ((0.306, 0.159), (0.657, 0.499), (0.841, 0.808))

# Their treated-minus-bare lift per tier. Used only to rank expected discordance.
TIER_LIFT = {"hard": 0.559 - 0.159, "moderate": 0.773 - 0.499, "easy": 0.947 - 0.808}

# Tier cut points on the pooled column, from applying their 17/38/58 tier sizes.
TIER_CUTS = (0.526, 0.763)


def pooled_to_terra_bare(pooled: float) -> float:
    """Map a pooled 8-model solve rate to a predicted gpt-5.6-terra bare rate."""
    lo_x, lo_y = TERRA_ANCHORS[0]
    if pooled <= lo_x:
        return lo_y * pooled / lo_x
    for (x0, y0), (x1, y1) in zip(TERRA_ANCHORS, TERRA_ANCHORS[1:]):
        if pooled <= x1:
            return y0 + (pooled - x0) * (y1 - y0) / (x1 - x0)
    hi_x, hi_y = TERRA_ANCHORS[-1]
    return min(1.0, hi_y + (pooled - hi_x) * (1 - hi_y) / (1 - hi_x))


def tier_of(pooled: float) -> str:
    hard_cut, easy_cut = TIER_CUTS
    if pooled <= hard_cut:
        return "hard"
    return "moderate" if pooled < easy_cut else "easy"


def load_pooled(heatmap_path: Path) -> dict:
    cells = json.loads(heatmap_path.read_text(encoding="utf-8"))["cells"]
    agg: dict = {}
    for cell in cells:
        row = agg.setdefault(cell["task"], [0, 0])
        row[0] += cell["n_passed"]
        row[1] += cell["n_attempted"]
    return {task: (p, n) for task, (p, n) in agg.items() if n}


def load_task_meta(tasks_dir: Path) -> dict:
    """Language, gold-file count, and giveaway count, computed from the corpus."""
    meta: dict = {}
    for name in sorted(os.listdir(tasks_dir)):
        toml_path = tasks_dir / name / "task.toml"
        if not toml_path.is_file():
            continue
        toml = toml_path.read_text(encoding="utf-8")
        patch = (tasks_dir / name / "solution" / "solution.patch").read_text(
            encoding="utf-8", errors="replace")
        instruction = (tasks_dir / name / "instruction.md").read_text(
            encoding="utf-8", errors="replace")
        gold = {m.group(1) for m in re.finditer(r"^diff --git a/\S+ b/(\S+)", patch, re.M)}
        # A gold file counts as named on a full-path match, or on a basename
        # match long enough not to collide with an ordinary English word.
        named = sum(
            1 for path in gold
            if path in instruction
            or (len(os.path.basename(path)) > 4 and os.path.basename(path) in instruction)
        )
        language = re.search(r'^language = "(.*)"', toml, re.M)
        commit = re.search(r'^base_commit_hash = "(.*)"', toml, re.M)
        repo = re.search(r'^repository_url = "(.*)"', toml, re.M)
        meta[name] = {
            "language": language.group(1) if language else "?",
            "base_commit": commit.group(1) if commit else "?",
            "repo": repo.group(1) if repo else "?",
            "gold_files": len(gold),
            "gold_named_in_prompt": named,
            "added_lines": sum(1 for line in patch.splitlines() if line.startswith("+")),
            "instruction_words": len(instruction.split()),
        }
    return meta


def build_rows(heatmap_path: Path, tasks_dir: Path) -> list:
    pooled = load_pooled(heatmap_path)
    meta = load_task_meta(tasks_dir)
    rows = []
    for task, (passed, attempted) in pooled.items():
        rate = passed / attempted
        bare = pooled_to_terra_bare(rate)
        tier = tier_of(rate)
        treated = min(1.0, bare + TIER_LIFT[tier])
        rows.append({
            "task": task,
            "pooled_rate": rate,
            "pooled_passed": passed,
            "pooled_attempted": attempted,
            "tier": tier,
            "predicted_terra_bare": bare,
            # Expected share of rep-pairs that disagree, under their tier model.
            # A pair that agrees carries no information about the treatment.
            "expected_discordance": treated * (1 - bare) + bare * (1 - treated),
            "defective_gold": task in DEFECTIVE_GOLD,
            **meta.get(task, {}),
        })
    return rows


def eligible(rows: list) -> list:
    return [r for r in rows
            if not r["defective_gold"] and r.get("gold_named_in_prompt", 0) == 0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--heatmap", type=Path,
                    default=Path(r"C:\Users\ragha\Desktop\bakeoff\deepswe\build\heatmap.json"))
    ap.add_argument("--tasks", type=Path,
                    default=Path(r"C:\Users\ragha\Desktop\bakeoff\deep-swe\tasks"))
    ap.add_argument("--band", type=float, nargs=2, metavar=("LO", "HI"), default=(0.20, 0.60),
                    help="predicted terra bare solve rate band to select from")
    ap.add_argument("--json-out", type=Path, help="write the full table as JSON")
    args = ap.parse_args()

    rows = build_rows(args.heatmap, args.tasks)
    total_p = sum(r["pooled_passed"] for r in rows)
    total_n = sum(r["pooled_attempted"] for r in rows)
    print(f"tasks with heatmap data : {len(rows)}")
    print(f"pooled corpus solve rate: {total_p}/{total_n} = {total_p / total_n:.1%}")

    excluded_defective = [r for r in rows if r["defective_gold"]]
    excluded_giveaway = [r for r in rows if r.get("gold_named_in_prompt", 0) > 0]
    print(f"excluded, defective gold : {len(excluded_defective)}")
    print(f"excluded, prompt giveaway: {len(excluded_giveaway)}")

    lo, hi = args.band
    band = sorted((r for r in eligible(rows) if lo <= r["predicted_terra_bare"] <= hi),
                  key=lambda r: r["predicted_terra_bare"])
    pooled_lo = min(r["pooled_rate"] for r in band)
    pooled_hi = max(r["pooled_rate"] for r in band)
    print(f"\nband: predicted terra bare {lo:.0%}-{hi:.0%} "
          f"(pooled {pooled_lo:.1%}-{pooled_hi:.1%}), n={len(band)}\n")
    header = f"{'bare':>6} {'pooled':>7} {'tier':<9} {'lang':<11} {'gold':>4} {'words':>6}  task"
    print(header)
    print("-" * len(header))
    for r in band:
        print(f"{r['predicted_terra_bare']:>6.1%} {r['pooled_rate']:>7.1%} {r['tier']:<9} "
              f"{r['language']:<11} {r['gold_files']:>4} {r['instruction_words']:>6}  {r['task']}")

    if args.json_out:
        args.json_out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
