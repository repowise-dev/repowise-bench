#!/usr/bin/env python3
"""Arm-generic aggregator for the token-reduction story.

The published ``aggregate_flask48.py`` compares exactly two arms (C0 vs
C2_full) and reports cost/wall/score. This script generalises to any number of
arms and foregrounds the metric that actually drives the cost story: the
**cache-write tokens** each arm pays per task. On a coding agent those are
dominated by the always-on context — the system prompt plus every advertised
MCP tool *schema* — so the C2-minus-C0 cache-write delta is a direct,
empirical read on the "schema tax".

For each arm (baseline ``C0_bare`` first) it prints, averaged over the tasks
that arm ran:

  * cost ($) and d% vs baseline
  * cache-write tokens and d vs baseline  (the schema/context tax)
  * cache-read tokens
  * tool calls, files read  (the navigation the tools buy back)
  * judge score

Usage:
    python analysis/aggregate_savings.py --results results/<exp>/swe_qa.jsonl
    python analysis/aggregate_savings.py --results <path> --baseline C0_bare
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[1]


def judge_score(row: dict) -> float | None:
    js = row.get("judge_scores") or {}
    nums = [v for v in js.values() if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Results file not found: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _avg(rows: list[dict], get, *, default=0.0) -> float:
    vals = [get(r) for r in rows]
    vals = [v for v in vals if v is not None]
    return mean(vals) if vals else default


# Per-arm metric accessors. Each maps a result row to a scalar.
_METRICS = {
    "cost_usd": lambda r: r.get("estimated_cost_usd"),
    "cache_write": lambda r: r.get("cache_write_tokens"),
    "cache_read": lambda r: r.get("cache_read_tokens"),
    "tool_calls": lambda r: r.get("num_tool_calls"),
    "files_read": lambda r: len(r.get("files_explored", []) or []),
    "score": judge_score,
}


def arm_summary(rows: list[dict]) -> dict:
    """Mean of every metric over the (non-errored) rows for one arm."""
    ok = [r for r in rows if not r.get("error")]
    out = {"n": len(ok), "n_total": len(rows)}
    for name, get in _METRICS.items():
        out[name] = _avg(ok, get)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--baseline", default="C0_bare")
    args = ap.parse_args()

    rows = load_rows(args.results)
    by_arm: dict[str, list[dict]] = {}
    for r in rows:
        by_arm.setdefault(r["condition"], []).append(r)

    # Baseline first, then the rest in a stable order.
    arms = sorted(by_arm, key=lambda a: (a != args.baseline, a))
    base = arm_summary(by_arm.get(args.baseline, []))

    print(f"Results: {args.results}")
    print(f"Arms: {', '.join(arms)}   baseline={args.baseline}\n")

    hdr = f"{'arm':12} {'n':>3} | {'cost$':>8} {'d%':>6} | {'cache_wr':>8} {'d':>7} | {'cache_rd':>8} | {'tools':>5} {'files':>5} | {'score':>5}"
    print(hdr)
    print("-" * len(hdr))
    for arm in arms:
        s = arm_summary(by_arm[arm])
        d_cost = pct(s["cost_usd"], base["cost_usd"])
        d_cw = s["cache_write"] - base["cache_write"]
        print(
            f"{arm:12} {s['n']:>3} | "
            f"${s['cost_usd']:>7.4f} {d_cost:>+5.0f}% | "
            f"{s['cache_write']:>8.0f} {d_cw:>+7.0f} | "
            f"{s['cache_read']:>8.0f} | "
            f"{s['tool_calls']:>5.1f} {s['files_read']:>5.1f} | "
            f"{s['score']:>5.2f}"
        )

    # The headline: how much of each arm's cache-write tax is the MCP/context
    # overhead it adds on top of the bare baseline.
    print("\nSCHEMA / CONTEXT TAX  (cache-write tokens above the bare baseline)")
    print("-" * 64)
    for arm in arms:
        if arm == args.baseline:
            continue
        s = arm_summary(by_arm[arm])
        extra = s["cache_write"] - base["cache_write"]
        print(f"  {arm:12}  +{extra:>8.0f} cache-write tokens/task")
    print()


def pct(v: float, base: float) -> float:
    return (v - base) / base * 100 if base else 0.0


if __name__ == "__main__":
    main()
