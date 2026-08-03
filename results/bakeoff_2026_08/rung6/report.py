"""Rung 6 (Layer B) report: proof of life first, then the numbers.

The order of the two tables below is the whole point and is not cosmetic.

Rung 8's headline was that we came last, and it was publishable because every
arm had been proven alive first: 70 of 70 cells ok, `embedder_live` on 70 of 70
repowise rows, the grader self-test discriminating. Without that block, a table
of Coverage numbers is indistinguishable from a table of harness bugs — and the
arm that gets silently zeroed is never ours, because ours is the only output
format we already know.

So: **proof of life is printed first, and a cell that fails it is excluded from
the numbers and counted in the exclusions line, never dropped silently.**

Usage:
    python results/bakeoff_2026_08/rung6/report.py \
        --results results/bakeoff_2026_08/rung6/layerb_pilot_django
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load(results_dir: Path) -> list[dict]:
    rows = []
    for f in sorted(results_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # A resumed run appends, so the same (task, condition) can appear twice.
    # Keep the LAST occurrence: a re-run exists because the earlier one failed.
    latest: dict = {}
    for r in rows:
        latest[(r.get("task_id"), r.get("condition"))] = r
    return list(latest.values())


def judge_mean(r: dict):
    js = r.get("judge_scores") or {}
    vals = [v for k, v in js.items() if isinstance(v, (int, float))]
    return statistics.mean(vals) if vals else None


def proof_of_life(rows: list[dict]) -> dict:
    by_arm: dict = defaultdict(lambda: {
        "cells": 0, "ok": 0, "errors": defaultdict(int),
        "served": set(), "served_count": set(),
        "cells_calling_mcp": 0, "mcp_calls": 0, "mcp_isError": 0,
        "hook_injected": 0, "index_dims": set(), "token_sources": set(),
        "models": set(), "judges": set(), "prompt_styles": set(),
    })
    for r in rows:
        a = by_arm[r.get("arm") or r.get("condition")]
        a["cells"] += 1
        if r.get("error"):
            a["errors"][str(r["error"])[:60]] += 1
        else:
            a["ok"] += 1
        for t in r.get("served_tools") or []:
            a["served"].add(t)
        if r.get("served_count") is not None:
            a["served_count"].add(r["served_count"])
        issued = r.get("mcp_tools_issued") or []
        if issued:
            a["cells_calling_mcp"] += 1
        elif r.get("arm_exercised") is False:
            a["not_exercised"] = a.get("not_exercised", 0) + 1
        a["mcp_calls"] += len(issued)
        a["mcp_isError"] += int(r.get("mcp_isError_count") or 0)
        if r.get("hook_injections"):
            a["hook_injected"] += 1
        ev = r.get("index_evidence") or {}
        if ev.get("index_vector_dim") is not None:
            a["index_dims"].add(ev["index_vector_dim"])
        if r.get("token_source"):
            a["token_sources"].add(r["token_source"])
        for m in r.get("models_used") or []:
            a["models"].add(m)
        if r.get("judge_model"):
            a["judges"].add(r["judge_model"])
        if r.get("prompt_style"):
            a["prompt_styles"].add(r["prompt_style"])
    return by_arm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    rows = load(Path(a.results))
    if not rows:
        print("no rows")
        return 1
    pol = proof_of_life(rows)

    print("=" * 92)
    print("PROOF OF LIFE — read this before any number below it")
    print("=" * 92)
    print(f"{'arm':20s} {'cells':>5s} {'ok':>4s} {'served':>7s} "
          f"{'usedMCP':>8s} {'calls':>6s} {'isErr':>6s} {'hookInj':>8s} {'vecdim':>7s}")
    for arm, s in sorted(pol.items()):
        served = ",".join(str(x) for x in sorted(s["served_count"])) or "-"
        dims = ",".join(str(x) for x in sorted(s["index_dims"])) or "-"
        print(f"{arm:20s} {s['cells']:5d} {s['ok']:4d} {served:>7s} "
              f"{s['cells_calling_mcp']:8d} {s['mcp_calls']:6d} "
              f"{s['mcp_isError']:6d} {s['hook_injected']:8d} {dims:>7s}")
        for err, n in sorted(s["errors"].items(), key=lambda kv: -kv[1]):
            print(f"    error x{n}: {err}")
        if s["hook_injected"]:
            print(f"    !! {s['hook_injected']} cell(s) received INJECTED hook "
                  f"context — not run in the pinned environment (D16)")
        if s["mcp_isError"]:
            print(f"    !! {s['mcp_isError']} isError responses from this arm's server")
        if s.get("not_exercised"):
            print(f"    !! {s['not_exercised']} cell(s) NOT EXERCISED — the arm's "
                  f"server was alive and the agent never called it. Those cells "
                  f"measure a bare agent and are excluded from the table below.")

    print()
    print("  token source(s):", {k: sorted(v["token_sources"]) for k, v in pol.items()})
    print("  judge model(s) :", sorted({j for v in pol.values() for j in v["judges"]}))
    print("  prompt style(s):", sorted({p for v in pol.values() for p in v["prompt_styles"]}))
    print("  agent model(s) :", sorted({m for v in pol.values() for m in v["models"]}))

    # ---- numbers, over the cells that passed ----------------------------
    # `arm_exercised is False` is excluded for the same reason an errored cell
    # is: it is not a measurement of the arm. It is excluded LOUDLY, on the line
    # above, because a quietly-dropped cell and a cell that never existed look
    # the same in a mean.
    scored = [r for r in rows
              if not r.get("error") and r.get("arm_exercised") is not False]
    excluded = len(rows) - len(scored)

    by_arm: dict = defaultdict(list)
    for r in scored:
        by_arm[r.get("arm") or r.get("condition")].append(r)

    print()
    print("=" * 92)
    print(f"RESULT — {len(scored)} scored cells, {excluded} excluded (see errors above)")
    print("=" * 92)
    print(f"{'arm':20s} {'n':>3s} {'judge':>6s} {'tools':>6s} {'files':>6s} "
          f"{'turns':>6s} {'in_tok':>9s} {'out_tok':>8s} {'cache_r':>10s} "
          f"{'$/task':>8s} {'secs':>6s}")

    def m(rs, key, default=0):
        vals = [r.get(key) or default for r in rs]
        return statistics.mean(vals) if vals else 0.0

    table = {}
    for arm, rs in sorted(by_arm.items()):
        judges = [j for j in (judge_mean(r) for r in rs) if j is not None]
        row = {
            "n": len(rs),
            "judge": statistics.mean(judges) if judges else None,
            "judged_n": len(judges),
            "tool_calls": m(rs, "num_tool_calls"),
            "files_read": statistics.mean([len(r.get("files_explored") or []) for r in rs]),
            "turns": m(rs, "num_turns"),
            "input_tokens": m(rs, "input_tokens"),
            "output_tokens": m(rs, "output_tokens"),
            "cache_read": m(rs, "cache_read_tokens"),
            "cost": m(rs, "estimated_cost_usd"),
            "wall": m(rs, "wall_clock_seconds"),
        }
        table[arm] = row
        jtxt = f"{row['judge']:.2f}" if row["judge"] is not None else "-"
        print(f"{arm:20s} {row['n']:3d} {jtxt:>6s} "
              f"{row['tool_calls']:6.1f} {row['files_read']:6.1f} {row['turns']:6.1f} "
              f"{row['input_tokens']:9.0f} {row['output_tokens']:8.0f} "
              f"{row['cache_read']:10.0f} {row['cost']:8.4f} {row['wall']:6.0f}")

    total = sum(r.get("estimated_cost_usd") or 0.0 for r in rows)
    print(f"\n  total agent spend on these rows: ${total:.2f}")

    if a.out:
        Path(a.out).write_text(json.dumps({
            "proof_of_life": {
                k: {kk: (sorted(vv) if isinstance(vv, set)
                         else dict(vv) if isinstance(vv, defaultdict) else vv)
                    for kk, vv in v.items()}
                for k, v in pol.items()
            },
            "table": table,
            "scored_cells": len(scored),
            "excluded_cells": excluded,
            "total_cost_usd": total,
        }, indent=2, default=str), encoding="utf-8")
        print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
