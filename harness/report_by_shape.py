"""The per-slice report. PLAN.md Phase 3's chart, which no run has produced.

Three things this prints that `results/bakeoff_2026_08/rung6/report.py` does not,
and each of them exists because of a specific way the pooled version misleads.

**1. Adoption is separated from quality.** An arm whose server the agent never
calls produces a clean, well-scored, fully-billed cell that measures a bare
agent. `arm_exercised` already excludes those from a mean, but excluding them
silently turns "the agent would not use this tool" into "this arm has a smaller
n", which is the wrong sentence. So the adoption table is printed FIRST and an
arm with zero answered MCP calls gets no quality row at all. Pre-registered at
`configs/layerb_stratified_django.PREREGISTRATION.md` section 1, before the run.

**2. The slices are the output; the pooled row is a convenience.** The draw is
equal allocation, 3 per non-empty shape, so each slice can be compared within
itself. The population is not equally allocated (architecture-why 16, multi-hop
14, symbol-lookup 9, cross-file-impact 5, performance-why 4, history-why 0).
A pooled mean over the draw is therefore NOT an estimate of any arm's mean over
all 48, and the pooled row is printed with that sentence attached to it.

**3. Comparisons are paired against the control per slice.** An unpaired mean
over a slice mixes question difficulty into an arm difference, and at n=3 per
slice that dominates. Pairing is on questions where BOTH the arm and `c0-bare`
produced a clean, exercised, gradeable cell, and the pair count is printed
because a mean over two pairs and a mean over three are not the same claim.

Usage:
    python -m harness.report_by_shape \
        --results results/bakeoff_2026_08/rung6/layerb_stratified_django
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from harness.question_shapes import SHAPES, load as load_shapes

CONTROL = "c0-bare"


def load_rows(results_dir: Path) -> list[dict]:
    """Rows, de-duplicated to the LAST occurrence of each (task, condition).

    A resumed run appends rather than rewriting, and a re-run exists because the
    earlier attempt failed.
    """
    rows: list[dict] = []
    for f in sorted(results_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    latest: dict = {}
    for r in rows:
        latest[(r.get("task_id"), r.get("condition"))] = r
    return list(latest.values())


def judge_mean(r: dict):
    js = r.get("judge_scores") or {}
    vals = [v for k, v in js.items() if isinstance(v, (int, float))]
    return statistics.mean(vals) if vals else None


def answered_mcp(r: dict) -> bool:
    """At least one MCP call the server ANSWERED.

    Not "at least one the agent issued". Session 9 found a Codex cell with
    `error: null`, one `get_answer` issued, judge 9.0 and a full bill whose call
    had returned `user cancelled MCP tool call` in a run with no user in it.
    """
    per = r.get("mcp_per_server") or {}
    return any((v or {}).get("ok", 0) > 0 for v in per.values())


def is_mcp_arm(arm: str) -> bool:
    return arm != CONTROL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = load_rows(Path(args.results))
    if not rows:
        print("no rows")
        return 1

    shapes_doc = load_shapes()["questions"]
    shape_of = {qid: row["shape"] for qid, row in shapes_doc.items()}

    arms = sorted({r.get("arm") or r.get("condition") for r in rows})
    tasks = sorted({r.get("task_id") for r in rows})
    present_shapes = [s for s in SHAPES if any(shape_of.get(t) == s for t in tasks)]

    cell = {(r.get("task_id"), r.get("arm") or r.get("condition")): r for r in rows}

    # ---------------------------------------------------------------- adoption
    # Printed first and deliberately: an arm the agent declines to call has no
    # quality number to report, only a rate.
    print("=" * 96)
    print("ADOPTION — did the agent call the arm's server at all, and did the "
          "server answer?")
    print("=" * 96)
    print("cells with >=1 ANSWERED MCP call / cells run, per shape. The control "
          "has no server and is '-'.")
    header = f"{'arm':20s}" + "".join(f"{s[:13]:>15s}" for s in present_shapes) + f"{'total':>10s}"
    print(header)

    adoption: dict = {}
    for arm in arms:
        line = f"{arm:20s}"
        tot_used = tot_run = 0
        per_shape: dict = {}
        for s in present_shapes:
            ids = [t for t in tasks if shape_of.get(t) == s]
            run = [cell[(t, arm)] for t in ids if (t, arm) in cell]
            used = [r for r in run if answered_mcp(r)]
            per_shape[s] = {"used": len(used), "run": len(run)}
            tot_used += len(used)
            tot_run += len(run)
            line += ("-" if not is_mcp_arm(arm) else f"{len(used)}/{len(run)}").rjust(15)
        line += ("-" if not is_mcp_arm(arm) else f"{tot_used}/{tot_run}").rjust(10)
        adoption[arm] = {"per_shape": per_shape, "used": tot_used, "run": tot_run}
        print(line)

    unexercised = [a for a in arms if is_mcp_arm(a) and adoption[a]["used"] == 0]
    for arm in unexercised:
        print(f"    !! {arm}: 0 of {adoption[arm]['run']} cells. Every cell is a "
              f"bare agent wearing this arm's name. NO quality row and NO cost "
              f"row below, per the pre-registration. These cells are also NOT "
              f"pooled into {CONTROL}.")

    # ------------------------------------------------------------ proof of life
    print()
    print("=" * 96)
    print("PROOF OF LIFE")
    print("=" * 96)
    print(f"{'arm':20s} {'cells':>6s} {'ok':>4s} {'served':>7s} {'mcpCalls':>9s} "
          f"{'isErr':>6s} {'hookInj':>8s} {'judgeFail':>10s}")
    pol: dict = {}
    for arm in arms:
        rs = [r for r in rows if (r.get("arm") or r.get("condition")) == arm]
        served = sorted({r.get("served_count") for r in rs if r.get("served_count") is not None})
        ok = sum(1 for r in rs if not r.get("error"))
        calls = sum(len(r.get("mcp_tools_issued") or []) for r in rs)
        iserr = sum(int(r.get("mcp_isError_count") or 0) for r in rs)
        hook = sum(1 for r in rs if r.get("hook_injections"))
        jfail = sum(1 for r in rs if not r.get("error") and judge_mean(r) is None)
        pol[arm] = {"cells": len(rs), "ok": ok, "served": served, "mcp_calls": calls,
                    "isError": iserr, "hook_injections": hook, "judge_failures": jfail}
        print(f"{arm:20s} {len(rs):6d} {ok:4d} "
              f"{(','.join(str(x) for x in served) or '-'):>7s} {calls:9d} "
              f"{iserr:6d} {hook:8d} {jfail:10d}")
        for r in rs:
            if r.get("error"):
                print(f"    error {r.get('task_id')}: {str(r['error'])[:80]}")
        if hook:
            print(f"    !! {hook} cell(s) received INJECTED hook context (D16)")

    # ------------------------------------------------------------- per slice
    reportable = [a for a in arms if a == CONTROL or adoption[a]["used"] > 0]

    def paired(arm: str, ids: list[str]):
        """Rows for `arm` and the control on ids where BOTH are usable."""
        out = []
        for t in ids:
            a = cell.get((t, arm))
            c = cell.get((t, CONTROL))
            if not a or not c:
                continue
            if a.get("error") or c.get("error"):
                continue
            if is_mcp_arm(arm) and not answered_mcp(a):
                continue
            out.append((t, a, c))
        return out

    print()
    print("=" * 96)
    print("BY SLICE — this is the output. Each slice compared within itself.")
    print("=" * 96)

    slice_tables: dict = {}
    for s in present_shapes + ["POOLED"]:
        ids = tasks if s == "POOLED" else [t for t in tasks if shape_of.get(t) == s]
        print()
        print(f"--- {s}  (n={len(ids)} questions drawn)")
        if s == "POOLED":
            print("    CONVENIENCE ROW. Equal allocation means this is NOT an "
                  "estimate of any arm's mean over all 48 questions.")
        print(f"{'arm':20s} {'pairs':>6s} {'judge':>7s} {'dJudge':>8s} "
              f"{'$/q':>9s} {'d$%':>8s} {'files':>7s} {'tools':>7s} "
              f"{'judgeW-T-L':>12s}")
        slice_tables[s] = {}
        for arm in reportable:
            pairs = paired(arm, ids)
            if not pairs:
                print(f"{arm:20s} {0:6d}       -        -         -        -       -       -")
                continue
            aj = [(judge_mean(a), judge_mean(c)) for _, a, c in pairs]
            gradeable = [(x, y) for x, y in aj if x is not None and y is not None]
            a_cost = statistics.mean([a.get("estimated_cost_usd") or 0.0 for _, a, _ in pairs])
            c_cost = statistics.mean([c.get("estimated_cost_usd") or 0.0 for _, _, c in pairs])
            files = statistics.mean([len(a.get("files_explored") or []) for _, a, _ in pairs])
            tools = statistics.mean([a.get("num_tool_calls") or 0 for _, a, _ in pairs])
            if gradeable:
                jm = statistics.mean([x for x, _ in gradeable])
                dj = statistics.mean([x - y for x, y in gradeable])
                w = sum(1 for x, y in gradeable if x > y)
                t_ = sum(1 for x, y in gradeable if x == y)
                l = sum(1 for x, y in gradeable if x < y)
                wtl = f"{w}-{t_}-{l}"
            else:
                jm = dj = None
                wtl = "-"
            dcost = (a_cost - c_cost) / c_cost * 100 if c_cost else 0.0
            slice_tables[s][arm] = {
                "pairs": len(pairs), "gradeable_pairs": len(gradeable),
                "judge": jm, "d_judge": dj, "cost": a_cost, "control_cost": c_cost,
                "d_cost_pct": dcost, "files_read": files, "tool_calls": tools,
                "judge_wtl": wtl,
            }
            jtxt = f"{jm:.2f}" if jm is not None else "-"
            dtxt = f"{dj:+.2f}" if dj is not None else "-"
            print(f"{arm:20s} {len(pairs):6d} {jtxt:>7s} {dtxt:>8s} "
                  f"{a_cost:9.4f} {dcost:+8.1f} {files:7.1f} {tools:7.1f} "
                  f"{wtl:>12s}")

    total = sum(r.get("estimated_cost_usd") or 0.0 for r in rows)
    print()
    print(f"total agent spend on these rows: ${total:.2f}")
    print(f"cells: {len(rows)}   arms reported for quality: {reportable}")
    print(f"arms with an adoption row only: {unexercised}")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "adoption": adoption,
            "proof_of_life": pol,
            "slices": slice_tables,
            "reportable_arms": reportable,
            "adoption_only_arms": unexercised,
            "total_cost_usd": total,
            "cells": len(rows),
        }, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
