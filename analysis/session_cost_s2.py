"""Analyse the step-2 arms: transport, payload and discovery.

Reads `results/bakeoff_2026_08/session-cost-eval/sessions_s2.jsonl` and emits the
tables the RESULT section needs. Deliberately reports BOTH currencies (billed
tokens and dollars) for every arm, because run 1 found them disagreeing in sign:
cache creation is billed at a premium over cache read, so an arm can be up on
tokens and flat on dollars.

T01 handling follows run 1 section 5c: it is reported separately rather than
dropped, because excluding it silently would hide a trajectory difference.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
DEFAULT = (BENCH / "results" / "bakeoff_2026_08" / "session-cost-eval"
           / "sessions_s2.jsonl")

NUM = ("input_tokens", "output_tokens", "cache_read_tokens",
       "cache_creation_tokens", "tool_calls", "mcp_calls", "cli_calls",
       "num_turns", "hook_events", "hook_injections", "cost_usd",
       "agent_wall_seconds")

BASE = "s2-c0bare"


def load(path: Path) -> dict:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    by_arm = defaultdict(list)
    for r in rows:
        # The runner appends a per-ARM summary row alongside the per-TASK rows.
        # Summing both double counts every arm's whole session, which would look
        # like a plausible number rather than an error.
        if "task_id" not in r:
            continue
        by_arm[r["arm"]].append(r)
    return by_arm


def totals(rows: list, exclude_t01: bool) -> dict:
    sel = [r for r in rows if not (exclude_t01 and r["task_id"] == "T01")]
    out = {k: 0.0 for k in NUM}
    for r in sel:
        for k in NUM:
            out[k] += float(r.get(k) or 0)
    out["billed"] = (out["input_tokens"] + out["output_tokens"]
                     + out["cache_read_tokens"] + out["cache_creation_tokens"])
    out["tasks"] = len(sel)
    # Oracles: only rows that HAVE one. A task with no oracle is not a pass and
    # not a fail, and counting it either way inflates or deflates completion.
    graded = [r for r in sel if isinstance(r.get("oracle"), dict)
              and r["oracle"].get("passed") is not None]
    out["oracles_passed"] = sum(1 for r in graded if r["oracle"]["passed"])
    out["oracles_graded"] = len(graded)
    return out


def pct(new: float, old: float) -> str:
    if not old:
        return "n/a"
    return f"{(new - old) / old * 100:+.1f}%"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    by_arm = load(path)
    if not by_arm:
        print("no rows", file=sys.stderr)
        return 1

    order = [a for a in ("s2-c0bare", "s2-cli-full", "s2-mcp", "s2-cli-trim",
                         "s2-cli-unenf", "s2-mcp-unenf") if a in by_arm]

    for exclude in (True, False):
        label = "10 comparable tasks (T01 excluded)" if exclude else "all tasks"
        t = {a: totals(by_arm[a], exclude) for a in order}
        base = t.get(BASE)
        print(f"\n=== {label} ===")
        head = (f"{'arm':<14}{'tasks':>6}{'billed':>12}{'vs bare':>9}"
                f"{'cost':>9}{'vs bare':>9}{'output':>8}{'cachecr':>10}"
                f"{'turns':>7}{'tools':>7}{'mcp':>5}{'cli':>5}{'inj':>5}"
                f"{'oracles':>9}")
        print(head)
        print("-" * len(head))
        for a in order:
            v = t[a]
            print(f"{a:<14}{v['tasks']:>6}{v['billed']:>12,.0f}"
                  f"{pct(v['billed'], base['billed']) if base else 'n/a':>9}"
                  f"{v['cost_usd']:>9.2f}"
                  f"{pct(v['cost_usd'], base['cost_usd']) if base else 'n/a':>9}"
                  f"{v['output_tokens']:>8,.0f}{v['cache_creation_tokens']:>10,.0f}"
                  f"{v['num_turns']:>7.0f}{v['tool_calls']:>7.0f}"
                  f"{v['mcp_calls']:>5.0f}{v['cli_calls']:>5.0f}"
                  f"{v['hook_injections']:>5.0f}"
                  f"{str(int(v['oracles_passed'])) + '/' + str(int(v['oracles_graded'])):>9}")

    # The three pre-registered contrasts, computed rather than eyeballed.
    t = {a: totals(by_arm[a], True) for a in order}
    print("\n=== pre-registered contrasts (10 comparable tasks) ===")
    pairs = [
        ("S1 transport, payload fixed", "s2-cli-full", "s2-mcp"),
        ("S2 payload, transport fixed", "s2-cli-full", "s2-cli-trim"),
        ("S4 discovery, unenforced", "s2-cli-unenf", "s2-mcp-unenf"),
    ]
    for label, a, b in pairs:
        if a not in t or b not in t:
            print(f"{label:<30} {a} vs {b}: INCOMPLETE")
            continue
        va, vb = t[a], t[b]
        if va["tasks"] != vb["tasks"]:
            # Totals over different task counts are not a contrast, they are a
            # count difference wearing a percentage. On a part-finished run this
            # prints as a large, plausible, entirely fake effect.
            print(f"{label:<30} {a} vs {b}: NOT COMPARABLE "
                  f"({va['tasks']} vs {vb['tasks']} tasks) — arm incomplete")
            continue
        print(f"{label:<30} {a} vs {b}")
        print(f"{'':>30}   billed {pct(vb['billed'], va['billed'])}, "
              f"cost {pct(vb['cost_usd'], va['cost_usd'])}, "
              f"tool calls {va['mcp_calls'] + va['cli_calls']:.0f} vs "
              f"{vb['mcp_calls'] + vb['cli_calls']:.0f}")

    # Per-task adoption, because a per-arm total hides an arm that used the tool
    # once on task 1 and never again — which is exactly how run 1's enforcement
    # defect presented.
    print("\n=== adoption per task (mcp+cli calls) ===")
    tids = sorted({r["task_id"] for rows in by_arm.values() for r in rows})
    print(f"{'arm':<14}" + "".join(f"{t_:>5}" for t_ in tids))
    for a in order:
        m = {r["task_id"]: (r.get("mcp_calls") or 0) + (r.get("cli_calls") or 0)
             for r in by_arm[a]}
        print(f"{a:<14}" + "".join(f"{m.get(t_, '-'):>5}" for t_ in tids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
