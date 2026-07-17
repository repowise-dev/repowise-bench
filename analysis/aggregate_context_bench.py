#!/usr/bin/env python3
"""Aggregate the multi-tool context benchmark (Track 1 style runs).

Unlike the two-condition flask48 aggregator, this handles N conditions,
repeat runs, and question categories:

  * repeats live in separate results files (one per n); per (task, condition)
    the MEDIAN across repeats is taken before any aggregation
  * rows whose attach-guard fired (server mounted, zero successful calls)
    are genuine salience behavior once mounting is pre-flighted, so they are
    NOT silently dropped: each condition reports an adoption_rate plus two
    metric views — "all" rows and "adopted" rows only. Charts must say
    which view they plot.
  * rows with errors (timeouts, max-turns exhaustion) are counted per
    condition, not silently dropped
  * every aggregate is also sliced by question category (adopted view)

Emits a human-readable table and a machine-readable JSON for chart
generation.

Usage:
    python analysis/aggregate_context_bench.py results/context_bench/track1_flask/swe_qa.jsonl \
        [more.jsonl ...] --out results/context_bench/track1_flask/aggregate.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

METRICS = ("num_tool_calls", "files_read", "input_tokens", "output_tokens",
           "cache_read_tokens", "total_tokens", "wall_clock_seconds",
           "estimated_cost_usd", "judge_score", "num_turns")


def judge_score(row: dict) -> float | None:
    js = row.get("judge_scores") or {}
    nums = [v for v in js.values() if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def row_metrics(row: dict) -> dict:
    return {
        "num_tool_calls": row.get("num_tool_calls", 0),
        "files_read": len(row.get("files_explored") or []),
        "input_tokens": row.get("input_tokens", 0),
        "output_tokens": row.get("output_tokens", 0),
        "cache_read_tokens": row.get("cache_read_tokens", 0),
        "total_tokens": row.get("total_tokens", 0),
        "wall_clock_seconds": row.get("wall_clock_seconds", 0.0),
        "estimated_cost_usd": row.get("estimated_cost_usd", 0.0),
        "judge_score": judge_score(row),
        "num_turns": row.get("num_turns", 0),
    }


def load_rows(paths: list) -> list:
    rows = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            rows.append(json.loads(line))
    return rows


def aggregate(rows: list) -> dict:
    # Bucket by (task, condition); collapse repeats to per-metric medians.
    buckets = defaultdict(list)
    not_adopted = defaultdict(int)  # condition -> rows with zero server calls
    mounted = defaultdict(int)      # condition -> rows where a server was mounted
    errors = defaultdict(lambda: defaultdict(int))  # condition -> error kind
    categories = {}
    for row in rows:
        cond = row["condition"]
        if row.get("error"):
            kind = ("max_turns" if "max_turns" in str(row["error"])
                    else "timeout" if row.get("timed_out") else "other")
            errors[cond][kind] += 1
            continue
        if row.get("attach_guard_fired") is not None:
            mounted[cond] += 1
            if row["attach_guard_fired"]:
                not_adopted[cond] += 1
        metrics = row_metrics(row)
        metrics["adopted"] = (row.get("attach_guard_fired") is not True)
        buckets[(row["task_id"], cond)].append(metrics)
        categories[row["task_id"]] = row.get("category", "")

    collapsed = {}
    for key, repeats in buckets.items():
        merged = {}
        for m in METRICS:
            vals = [r[m] for r in repeats if r[m] is not None]
            merged[m] = median(vals) if vals else None
        merged["n_repeats"] = len(repeats)
        # A collapsed task counts as adopted when any repeat adopted.
        merged["adopted"] = any(r["adopted"] for r in repeats)
        collapsed[key] = merged

    def summarize(items: list) -> dict:
        out = {}
        for m in METRICS:
            vals = [it[m] for it in items if it[m] is not None]
            out[m] = ({"mean": round(mean(vals), 3), "median": round(median(vals), 3),
                       "min": round(min(vals), 3), "max": round(max(vals), 3),
                       "n": len(vals)} if vals else None)
        return out

    by_condition = defaultdict(list)
    by_cond_cat = defaultdict(list)
    for (task_id, cond), merged in collapsed.items():
        by_condition[cond].append(merged)
        by_cond_cat[(cond, categories.get(task_id, ""))].append(merged)

    def adopted_only(items: list) -> list:
        return [it for it in items if it["adopted"]]

    return {
        "conditions": {cond: summarize(items)
                       for cond, items in sorted(by_condition.items())},
        "conditions_adopted": {cond: summarize(adopted_only(items))
                               for cond, items in sorted(by_condition.items())},
        "adoption_rate": {cond: round(1 - not_adopted[cond] / mounted[cond], 4)
                          for cond in sorted(mounted) if mounted[cond]},
        "by_category": {f"{cond}/{cat}": summarize(adopted_only(items))
                        for (cond, cat), items in sorted(by_cond_cat.items())},
        "errors": {c: dict(k) for c, k in errors.items()},
        "tasks_per_condition": {cond: len(items)
                                for cond, items in sorted(by_condition.items())},
    }


def print_table(agg: dict) -> None:
    cols = ("num_tool_calls", "files_read", "total_tokens",
            "wall_clock_seconds", "judge_score", "estimated_cost_usd")
    header = f"{'condition':16}" + "".join(f"{c:>18}" for c in cols) + f"{'n':>5}"
    print(header)
    print("-" * len(header))
    for cond, s in agg["conditions"].items():
        cells = ""
        for c in cols:
            v = s.get(c)
            cells += f"{v['median'] if v else '-':>18}"
        n = s.get("num_tool_calls")
        print(f"{cond:16}{cells}{(n or {}).get('n', 0):>5}")
    if agg["adoption_rate"]:
        print(f"\nadoption rate (mounted arms): {agg['adoption_rate']}")
    if agg["errors"]:
        print(f"errored runs: {agg['errors']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", nargs="+", help="results JSONL file(s), one per repeat")
    ap.add_argument("--out", type=Path, default=None, help="write aggregate JSON here")
    args = ap.parse_args()
    agg = aggregate(load_rows(args.results))
    print_table(agg)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(agg, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
