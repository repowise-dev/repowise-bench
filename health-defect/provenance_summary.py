#!/usr/bin/env python3
"""provenance_summary.py — corpus-wide provenance summary tables.

From the provenance-walk outputs, emits one markdown report:
  * per repo: commits, agent share (overall + window), tier split, top agents,
    agent-era onset (first month with >=5 agent commits)
  * per repo: agent share by quarter (the "agent fraction over time" series)

Run (venv python)::

    .venv/Scripts/python.exe health-defect/provenance_summary.py \
        --provenance-dir <data>/agent-repos/_provenance --out <report.md>
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

WINDOW_START = "2025-06-01"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    lines = ["# Agent provenance — corpus summary",
             f"\nGenerated: {datetime.now(timezone.utc).isoformat()}",
             f"\nWindow for 'recent share': {WINDOW_START} → HEAD. Merge commits excluded.",
             "\n## Per-repo overview\n",
             "| Repo | Cohort | Commits | Agent | Agent % | recent % | T1 | T2 | T3 | Top agents | Onset |",
             "|---|---|--:|--:|--:|--:|--:|--:|--:|---|---|"]
    quarter_rows: dict[str, dict[str, tuple[int, int]]] = {}
    for path in sorted(args.provenance_dir.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        rows = [r for r in d["rows"] if not r["is_merge"]]
        n = len(rows)
        agents = [r for r in rows if r["agent"]]
        recent = [r for r in rows if (r["date"] or "") >= WINDOW_START]
        recent_agent = [r for r in recent if r["agent"]]
        tiers = defaultdict(int)
        by_agent = defaultdict(int)
        for r in agents:
            tiers[r["autonomy_tier"]] += 1
            by_agent[r["agent"]] += 1
        top = ", ".join(f"{a}:{c}" for a, c in
                        sorted(by_agent.items(), key=lambda kv: -kv[1])[:3])
        monthly = defaultdict(lambda: [0, 0])  # month -> [agent, total]
        for r in rows:
            m = (r["date"] or "")[:7]
            if m:
                monthly[m][1] += 1
                if r["agent"]:
                    monthly[m][0] += 1
        onset = next((m for m, (a, _) in sorted(monthly.items()) if a >= 5), "—")
        q: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for m, (a, t) in monthly.items():
            qk = f"{m[:4]}Q{(int(m[5:7]) - 1) // 3 + 1}"
            q[qk][0] += a
            q[qk][1] += t
        quarter_rows[d["repo"]] = {k: (v[0], v[1]) for k, v in q.items()}
        lines.append(
            f"| {d['repo']} | {d['cohort']} | {n} | {len(agents)} | "
            f"{len(agents) / max(n, 1):.1%} | "
            f"{len(recent_agent) / max(len(recent), 1):.1%} | "
            f"{tiers[1]} | {tiers[2]} | {tiers[3]} | {top or '—'} | {onset} |")

    lines += ["\n## Agent share by quarter (agent/total commits)\n"]
    all_q = sorted({qk for qs in quarter_rows.values() for qk in qs})
    keep_q = [qk for qk in all_q if qk >= "2024Q1"]
    lines.append("| Repo | " + " | ".join(keep_q) + " |")
    lines.append("|---|" + "--:|" * len(keep_q))
    for repo, qs in sorted(quarter_rows.items()):
        cells = []
        for qk in keep_q:
            a, t = qs.get(qk, (0, 0))
            cells.append(f"{a / t:.0%}" if t else "—")
        lines.append(f"| {repo} | " + " | ".join(cells) + " |")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
