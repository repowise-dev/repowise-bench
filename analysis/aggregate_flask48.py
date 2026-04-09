#!/usr/bin/env python3
"""Aggregate the flask48 SWE-QA benchmark results.

Reads the canonical results JSONL produced by ``run_experiment.py`` against
``configs/swe_qa_flask48.yaml`` and prints:

  * a per-task table (cost, wall, score, turns) for each of the 48 tasks
  * aggregate metrics (mean, median, trimmed mean) for cost, wall, turns,
    tool calls, files read, and judge score
  * per-task win counts (cost, wall, score)
  * the best and worst task per metric
  * totals across the full benchmark

The script reads ``estimated_cost_usd`` directly from each row. The harness
populates this field from the agent runtime's per-model billing roll-up, so
it already includes any subagent dispatches the parent session triggered.
No re-pricing or token-based recomputation is performed here.

Usage:

    python analysis/aggregate_flask48.py [--results PATH]

By default the script reads ``results/swe_qa_flask48/swe_qa.jsonl`` relative
to the repository root.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = REPO_ROOT / "results" / "swe_qa_flask48" / "swe_qa.jsonl"

C0 = "C0_bare"
C2 = "C2_full"


def judge_score(row: dict) -> float | None:
    """Mean of all numeric judge dimensions on a row, or None if absent."""
    js = row.get("judge_scores") or {}
    nums = [v for v in js.values() if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def load_paired(path: Path) -> dict[tuple[str, str], dict]:
    """Load the JSONL into a {(task_id, condition): row} dict.

    Raises a clear error if the file is missing — the most common cause of
    that is having run only one of the two arms.
    """
    if not path.exists():
        raise SystemExit(
            f"Results file not found: {path}\n"
            f"Run `python harness/run_experiment.py --config configs/swe_qa_flask48.yaml` first."
        )
    out: dict[tuple[str, str], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out[(r["task_id"], r["condition"])] = r
    return out


def trimmed_mean(values: list[float], k: int = 2) -> float:
    """Mean after dropping the k largest and k smallest values."""
    if len(values) <= 2 * k:
        return mean(values)
    return mean(sorted(values)[k:-k])


def metric_block(
    name: str,
    c0: list[float],
    c2: list[float],
    higher_better: bool,
    fmt: str = "{:.4f}",
) -> None:
    """Print mean & median for a single metric, tagging which arm is better."""
    m0, m2 = mean(c0), mean(c2)
    d0, d2 = median(c0), median(c2)
    pct_m = (m2 - m0) / m0 * 100 if m0 else 0
    pct_med = (d2 - d0) / d0 * 100 if d0 else 0
    favors_m = (m2 > m0) if higher_better else (m2 < m0)
    favors_med = (d2 > d0) if higher_better else (d2 < d0)
    tag = lambda f, p: "C2 better" if f else ("tied" if abs(p) < 1 else "C0 better")
    print(
        f"  {name:14s}  mean    C0 {fmt.format(m0)}  C2 {fmt.format(m2)}"
        f"  Δ {pct_m:+6.1f}%  [{tag(favors_m, pct_m)}]"
    )
    print(
        f"  {' ':14s}  median  C0 {fmt.format(d0)}  C2 {fmt.format(d2)}"
        f"  Δ {pct_med:+6.1f}%  [{tag(favors_med, pct_med)}]"
    )


def best_worst(
    name: str,
    paired: list[str],
    rows: dict,
    get_value,
    higher_better: bool,
    fmt: str = "{:.4f}",
) -> None:
    """Print the best and worst per-task delta for one metric."""
    triples = [(t, get_value(rows[(t, C0)]), get_value(rows[(t, C2)])) for t in paired]
    if higher_better:
        deltas = [(t, b - a) for t, a, b in triples]
    else:
        deltas = [(t, a - b) for t, a, b in triples]
    deltas.sort(key=lambda x: x[1], reverse=True)
    best_task, _ = deltas[0]
    worst_task, _ = deltas[-1]
    bt = next(x for x in triples if x[0] == best_task)
    wt = next(x for x in triples if x[0] == worst_task)
    print(f"  {name:14s}  best  {best_task}  C0 {fmt.format(bt[1])} → C2 {fmt.format(bt[2])}")
    print(f"  {' ':14s}  worst {worst_task}  C0 {fmt.format(wt[1])} → C2 {fmt.format(wt[2])}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--results",
        type=Path,
        default=DEFAULT_RESULTS,
        help=f"Path to swe_qa.jsonl (default: {DEFAULT_RESULTS.relative_to(REPO_ROOT)})",
    )
    args = ap.parse_args()

    rows = load_paired(args.results)
    print(f"Loaded {len(rows)} rows from {args.results.relative_to(REPO_ROOT)}")

    task_ids = sorted({t for (t, _) in rows.keys()})
    paired = [
        t
        for t in task_ids
        if (t, C0) in rows
        and (t, C2) in rows
        and judge_score(rows[(t, C0)]) is not None
        and judge_score(rows[(t, C2)]) is not None
        and rows[(t, C0)].get("estimated_cost_usd") is not None
        and rows[(t, C2)].get("estimated_cost_usd") is not None
    ]
    n = len(paired)
    print(f"Paired & scored: {n}/48\n")

    print("=" * 88)
    print("PER-TASK RESULTS")
    print("=" * 88)
    print(
        f"  {'task':12s} | {'C0_$':>8} {'C2_$':>8} {'Δ%':>7} | "
        f"{'C0_w':>5} {'C2_w':>5} | {'C0_s':>5} {'C2_s':>5} | "
        f"{'C0_t':>4} {'C2_t':>4}"
    )
    print("  " + "-" * 86)
    for t in paired:
        c0 = rows[(t, C0)]
        c2 = rows[(t, C2)]
        a = c0["estimated_cost_usd"]
        b = c2["estimated_cost_usd"]
        delta_pct = (b - a) / a * 100 if a else 0
        print(
            f"  {t:12s} | ${a:>7.4f} ${b:>7.4f} {delta_pct:>+6.0f}% | "
            f"{c0.get('wall_clock_seconds', 0):>5.0f} {c2.get('wall_clock_seconds', 0):>5.0f} | "
            f"{judge_score(c0):>5.2f} {judge_score(c2):>5.2f} | "
            f"{c0.get('num_turns', 0):>4} {c2.get('num_turns', 0):>4}"
        )

    # Metric vectors
    c0_cost = [rows[(t, C0)]["estimated_cost_usd"] for t in paired]
    c2_cost = [rows[(t, C2)]["estimated_cost_usd"] for t in paired]
    c0_wall = [rows[(t, C0)].get("wall_clock_seconds", 0) for t in paired]
    c2_wall = [rows[(t, C2)].get("wall_clock_seconds", 0) for t in paired]
    c0_score = [judge_score(rows[(t, C0)]) for t in paired]
    c2_score = [judge_score(rows[(t, C2)]) for t in paired]
    c0_turns = [rows[(t, C0)].get("num_turns", 0) for t in paired]
    c2_turns = [rows[(t, C2)].get("num_turns", 0) for t in paired]
    c0_tools = [rows[(t, C0)].get("num_tool_calls", 0) for t in paired]
    c2_tools = [rows[(t, C2)].get("num_tool_calls", 0) for t in paired]
    c0_files = [len(rows[(t, C0)].get("files_explored", []) or []) for t in paired]
    c2_files = [len(rows[(t, C2)].get("files_explored", []) or []) for t in paired]

    print()
    print("=" * 88)
    print(f"AGGREGATE — C2 vs C0 (n={n})")
    print("=" * 88)
    print(f"  C2 cheaper on cost: {sum(1 for a, b in zip(c0_cost, c2_cost) if b < a)}/{n}")
    print(f"  C2 faster on wall:  {sum(1 for a, b in zip(c0_wall, c2_wall) if b < a)}/{n}")
    print(f"  C2 ≥ C0 on score:   {sum(1 for a, b in zip(c0_score, c2_score) if b >= a)}/{n}")
    print()
    print("METRIC TABLE — lower is better unless noted")
    print("-" * 88)
    metric_block("cost ($)", c0_cost, c2_cost, higher_better=False, fmt="${:.4f}")
    metric_block("wall (s)", c0_wall, c2_wall, higher_better=False, fmt="{:>5.1f}s")
    metric_block("turns", c0_turns, c2_turns, higher_better=False, fmt="{:>5.1f}")
    metric_block("tool_calls", c0_tools, c2_tools, higher_better=False, fmt="{:>5.1f}")
    metric_block("files_read", c0_files, c2_files, higher_better=False, fmt="{:>5.1f}")
    metric_block("score", c0_score, c2_score, higher_better=True, fmt="{:.2f}")

    deltas = [b - a for a, b in zip(c0_cost, c2_cost)]
    print()
    print(
        f"  cost trim20  Δ ${trimmed_mean(deltas):+.4f}/task "
        f"({trimmed_mean(deltas) / mean(c0_cost) * 100:+5.1f}%)"
    )

    print()
    print("BEST / WORST TASKS BY METRIC")
    print("-" * 88)
    best_worst("cost ($)", paired, rows, lambda r: r["estimated_cost_usd"], higher_better=False, fmt="${:.4f}")
    best_worst("wall (s)", paired, rows, lambda r: r.get("wall_clock_seconds", 0), higher_better=False, fmt="{:>5.1f}s")
    best_worst("score", paired, rows, judge_score, higher_better=True, fmt="{:.2f}")

    total_c0 = sum(c0_cost)
    total_c2 = sum(c2_cost)
    print()
    print("=" * 88)
    print("TOTALS")
    print("=" * 88)
    print(
        f"  cost  C0 ${total_c0:.3f}  C2 ${total_c2:.3f}  "
        f"Δ ${total_c2 - total_c0:+.3f}  ({(total_c2 - total_c0) / total_c0 * 100:+.1f}%)"
    )
    print(
        f"  wall  C0 {sum(c0_wall) / 60:.1f} min  C2 {sum(c2_wall) / 60:.1f} min  "
        f"Δ {(sum(c2_wall) - sum(c0_wall)) / 60:+.1f} min"
    )


if __name__ == "__main__":
    main()
