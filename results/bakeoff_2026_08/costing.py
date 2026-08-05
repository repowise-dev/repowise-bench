"""Price rungs 6, 7 and 9 against the spend ceiling, from measured data only.

Raghav asked for this explicitly (PLAN.md, scheduling note 2026-08-01): a written
cost estimate before anyone commits to rung 9, which is the one rung that can
breach the ceiling.

Every per-task dollar figure here is MEASURED, read out of
`results/swe_qa_*/swe_qa.jsonl` rather than modelled. Those rows carry
`estimated_cost_usd`, which `swe_qa_runner.py:1471` sets from the Claude Code
CLI's own `total_cost_usd`. Two consequences worth stating before any number is
quoted:

1. **`total_cost_usd` is agent-only. The judge is not in it.** `judge_answer` is
   a separate call made after the metrics object is populated, and nothing adds
   its cost back. So judge spend is additive to every figure below and is
   estimated separately.

2. **It is API-list pricing.** If runs are executed against a subscription
   rather than metered API billing, out-of-pocket differs from this. The
   ceiling is read here as metered API spend, which is the conservative reading.

Run: .venv/Scripts/python.exe results/bakeoff_2026_08/costing.py
"""

from __future__ import annotations

import glob
import json
import statistics as st
from collections import defaultdict

# Raised from $200 to $250 by Raghav on 2026-08-01, after this script showed
# the recommended ladder landing at $192-197 against $200: $3 of headroom is
# not a plan, since one re-run breaches it. The extra is margin, not scope.
CEILING = 250.00

# Judge prompt sized from the real transcripts on disk: rubric ~700 chars plus
# question plus reference answer plus agent answer, median 5,639 chars, so
# ~1,400 input tokens at 4 chars/token. Output is ~30 tokens of rubric JSON plus
# an unmeasured reasoning allowance. Judge unit price is left as a dial because
# it is the one input here that is not measured; the conclusion is insensitive
# to it, which the script prints.
JUDGE_INPUT_TOKENS = 1400
JUDGE_OUTPUT_TOKENS = 400  # 30 of JSON, the rest reasoning headroom


def measured() -> dict:
    """Per-task agent cost by condition, from every swe_qa run on disk."""
    rows = defaultdict(list)
    for p in sorted(glob.glob("results/swe_qa_*/swe_qa.jsonl")):
        run = p.replace("\\", "/").split("/")[-2]
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("error") or not r.get("estimated_cost_usd"):
                continue
            rows[(run, r.get("condition"))].append(r)
    return rows


def main() -> int:
    rows = measured()

    # Only the claude-sonnet-5 API-model runs are a valid basis. The
    # `swe_qa_flask_local*` runs are Ollama-local (different model, different
    # economics) and must not be pooled in; the `_smoke` runs are n=2 and are
    # kept out of the average for the same reason a 2-sample mean is not one.
    def is_basis(run: str) -> bool:
        return "local" not in run and not run.endswith("_smoke")

    std, long = [], []
    for (run, cond), rs in rows.items():
        if not is_basis(run):
            continue
        costs = [r["estimated_cost_usd"] for r in rs]
        (long if "long" in run else std).extend(costs)

    print("=" * 78)
    print("MEASURED PER-TASK AGENT COST (claude-sonnet-5 configs, judge excluded)")
    print("=" * 78)
    for label, xs in (("standard (max_turns 15)", std), ("long-horizon", long)):
        if not xs:
            continue
        print(f"  {label:26s} n={len(xs):4d}  mean=${st.mean(xs):.4f}  "
              f"median=${st.median(xs):.4f}  p90=${sorted(xs)[int(.9*len(xs))]:.4f}  "
              f"max=${max(xs):.4f}")

    std_mean, std_p90 = st.mean(std), sorted(std)[int(.9 * len(std))]
    long_mean = st.mean(long) if long else std_mean * 2

    # Reasoning effort. Standing rule 2 requires both efforts, and no run on disk
    # varied it, so there is no measured multiplier. The long-horizon runs are
    # the closest proxy for "same task, more tokens spent thinking": they cost
    # 2.2x the standard runs. High effort is not the same mechanism, so half of
    # that is used as the working multiplier and the sensitivity is printed
    # rather than buried.
    effort_mult = 1.0 + (long_mean / std_mean - 1.0) / 2
    print(f"\n  long/standard ratio = {long_mean/std_mean:.2f}x")
    print(f"  high-effort multiplier used (UNMEASURED, half the long ratio) = "
          f"{effort_mult:.2f}x")

    def agent_cost(runs: int, per_task: float, both_efforts: bool) -> float:
        # Half the runs at each effort when both are required.
        return runs * per_task * ((1 + effort_mult) / 2 if both_efforts else 1.0)

    def judge_cost(calls: int, in_per_m: float, out_per_m: float) -> float:
        return calls * (JUDGE_INPUT_TOKENS / 1e6 * in_per_m
                        + JUDGE_OUTPUT_TOKENS / 1e6 * out_per_m)

    print("\n" + "=" * 78)
    print("RUNG COSTS")
    print("=" * 78)

    scenarios = [
        # name, arms, questions, reps, both_efforts, per_task basis
        ("R6 pilot, 3 arms the harness can drive today",
         3, 10, 1, False, std_p90),
        ("R6 pilot, all 7 arms (needs arm generalization first)",
         7, 10, 1, False, std_p90),
        ("R7 distill rerun, 2 arms x 30 paired tasks, both efforts",
         2, 30, 1, True, long_mean),
        ("R9 AS SPECIFIED: 8 arms x 100 q x 2 reps x 2 efforts",
         8, 100, 2, True, std_mean),
        ("R9 conservative (p90 per-task)",
         8, 100, 2, True, std_p90),
        ("R9 cut: 1 rep",
         8, 100, 1, True, std_mean),
        ("R9 cut: 1 rep, 5 arms",
         5, 100, 1, True, std_mean),
        ("R9 cut: 1 rep, 4 arms",
         4, 100, 1, True, std_mean),
    ]

    results = []
    for name, arms, q, reps, both, per_task in scenarios:
        runs = arms * q * reps * (2 if both else 1)
        a = agent_cost(runs, per_task, both)
        # Judge unit price is unknown; bracket it. The point is the bracket is
        # narrow enough not to change any decision.
        j_lo = judge_cost(runs, 0.15, 0.60)
        j_hi = judge_cost(runs, 1.25, 10.00)
        results.append((name, runs, a, j_lo, j_hi))
        print(f"\n  {name}")
        print(f"    runs={runs:5d}   agent=${a:8.2f}   judge=${j_lo:.2f}-${j_hi:.2f}"
              f"   TOTAL=${a+j_lo:8.2f}-${a+j_hi:.2f}")
        tot = a + j_hi
        verdict = "FITS" if tot <= CEILING else f"OVER by {tot/CEILING:.1f}x"
        print(f"    vs ${CEILING:.0f} ceiling alone: {verdict}")

    print("\n" + "=" * 78)
    print("LADDER TOTAL, recommended configuration")
    print("=" * 78)
    r6 = next(r for r in results if r[0].startswith("R6 pilot, 3 arms"))
    r7 = next(r for r in results if r[0].startswith("R7"))
    r9 = next(r for r in results if r[0].startswith("R9 cut: 1 rep, 4 arms"))
    lo = r6[2] + r6[3] + r7[2] + r7[3] + r9[2] + r9[3]
    hi = r6[2] + r6[4] + r7[2] + r7[4] + r9[2] + r9[4]
    print(f"  R6 (3 arms)     ${r6[2]+r6[3]:7.2f} - ${r6[2]+r6[4]:.2f}")
    print(f"  R7 (distill)    ${r7[2]+r7[3]:7.2f} - ${r7[2]+r7[4]:.2f}")
    print(f"  R9 (4 arms,1rep)${r9[2]+r9[3]:7.2f} - ${r9[2]+r9[4]:.2f}")
    print(f"  {'-'*44}")
    print(f"  TOTAL           ${lo:7.2f} - ${hi:.2f}   ceiling ${CEILING:.0f}   "
          f"{'FITS' if hi <= CEILING else 'OVER'}")
    print(f"  headroom at the pessimistic end: ${CEILING - hi:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
