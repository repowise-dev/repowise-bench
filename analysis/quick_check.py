#!/usr/bin/env python3
"""
Quick signal check after running SWE-QA.
Run this BEFORE spending money on SWE-bench.

Usage:
    python analysis/quick_check.py results/swe_qa/
"""

import json
import sys
from pathlib import Path
from collections import defaultdict


def load_results(results_dir: str) -> list:
    """Load all results from JSONL files."""
    records = []
    for f in Path(results_dir).glob("*.jsonl"):
        with open(f) as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def analyze(results_dir: str):
    records = load_results(results_dir)

    if not records:
        print("❌ No results found. Run experiments first.")
        return

    print(f"Loaded {len(records)} results\n")

    # Group by condition
    by_condition = defaultdict(list)
    for r in records:
        by_condition[r["condition"]].append(r)

    # === JUDGE SCORES BY CONDITION ===
    print("=" * 60)
    print("JUDGE SCORES BY CONDITION (1-10 scale)")
    print("=" * 60)

    condition_avgs = {}
    for cond in sorted(by_condition.keys()):
        cond_records = by_condition[cond]
        # Collect judge scores
        all_scores = defaultdict(list)
        for r in cond_records:
            scores = r.get("judge_scores", {})
            if "error" in scores:
                continue
            for dim, val in scores.items():
                if isinstance(val, (int, float)):
                    all_scores[dim].append(val)

        if not all_scores:
            print(f"\n  {cond}: No valid judge scores")
            continue

        print(f"\n  {cond} ({len(cond_records)} tasks):")
        overall = []
        for dim in ["correctness", "completeness", "relevance", "clarity", "reasoning"]:
            vals = all_scores.get(dim, [])
            if vals:
                avg = sum(vals) / len(vals)
                overall.extend(vals)
                print(f"    {dim:15s}: {avg:.1f}")
        if overall:
            avg_overall = sum(overall) / len(overall)
            condition_avgs[cond] = avg_overall
            print(f"    {'OVERALL':15s}: {avg_overall:.1f}")

    # === KEY COMPARISON ===
    print("\n" + "=" * 60)
    print("KEY COMPARISON: C0 (bare) vs C2 (full Repowise)")
    print("=" * 60)

    c0 = condition_avgs.get("C0_bare")
    c2 = condition_avgs.get("C2_full")

    if c0 and c2:
        diff = c2 - c0
        pct = (diff / c0) * 100
        print(f"  C0 (bare):  {c0:.2f}")
        print(f"  C2 (full):  {c2:.2f}")
        print(f"  Δ:          {diff:+.2f} ({pct:+.1f}%)")

        if pct > 10:
            print("\n  ✅ STRONG SIGNAL — proceed to SWE-bench")
        elif pct > 5:
            print("\n  ⚠️  MODERATE SIGNAL — proceed with caution, consider more SWE-QA tasks first")
        else:
            print("\n  ❌ WEAK SIGNAL — investigate before spending on SWE-bench")
            print("     Check: Are certain question types showing stronger effects?")
    else:
        print("  ⚠️  Need both C0_bare and C2_full results for comparison")

    # === TOKEN EFFICIENCY ===
    print("\n" + "=" * 60)
    print("TOKEN EFFICIENCY BY CONDITION")
    print("=" * 60)

    for cond in sorted(by_condition.keys()):
        cond_records = by_condition[cond]
        tokens = [r.get("total_tokens", 0) for r in cond_records if r.get("total_tokens", 0) > 0]
        tool_calls = [r.get("num_tool_calls", 0) for r in cond_records]
        costs = [r.get("estimated_cost_usd", 0) for r in cond_records]

        if tokens:
            print(f"\n  {cond}:")
            print(f"    Avg tokens:     {sum(tokens)/len(tokens):,.0f}")
            print(f"    Avg tool calls: {sum(tool_calls)/len(tool_calls):.1f}")
            print(f"    Avg cost:       ${sum(costs)/len(costs):.3f}")
            print(f"    Total cost:     ${sum(costs):.2f}")

    # === BREAKDOWN BY QUESTION TYPE ===
    print("\n" + "=" * 60)
    print("BREAKDOWN BY QUESTION TYPE (C0 vs C2)")
    print("=" * 60)

    for qtype in ["What", "Why", "Where", "How"]:
        c0_scores = []
        c2_scores = []
        for r in records:
            if r.get("question_type") != qtype:
                continue
            scores = r.get("judge_scores", {})
            if "error" in scores:
                continue
            overall = [v for k, v in scores.items() if isinstance(v, (int, float))]
            if not overall:
                continue
            avg = sum(overall) / len(overall)
            if r["condition"] == "C0_bare":
                c0_scores.append(avg)
            elif r["condition"] == "C2_full":
                c2_scores.append(avg)

        if c0_scores and c2_scores:
            c0_avg = sum(c0_scores) / len(c0_scores)
            c2_avg = sum(c2_scores) / len(c2_scores)
            diff = c2_avg - c0_avg
            print(f"  {qtype:8s}: C0={c0_avg:.1f}  C2={c2_avg:.1f}  Δ={diff:+.1f}")

    # === ERRORS ===
    errors = [r for r in records if r.get("error")]
    if errors:
        print(f"\n⚠️  {len(errors)} tasks had errors:")
        error_types = defaultdict(int)
        for r in errors:
            error_types[r["error"][:50]] += 1
        for err, count in sorted(error_types.items(), key=lambda x: -x[1]):
            print(f"    {count}× {err}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analysis/quick_check.py results/swe_qa/")
        sys.exit(1)
    analyze(sys.argv[1])
