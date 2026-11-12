"""Tables from a paired run directory: per arm, paired against bare, per tier, per task.

    python paired/analyze.py OUT [--base bare] > OUT/analysis.md

Columns follow the published table: tool calls, files opened, output tokens,
total cost, time, correct, plus the Repowise tool-use rate of every cell.
Tokens = fresh input + cache write + cache read + output (modelUsage).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path


def load(root: Path) -> list[dict]:
    grades = {}
    for vp in (root / "judge").glob("verdict_*.json"):
        for v in json.loads(vp.read_text(encoding="utf-8")).get("verdicts", []):
            if v.get("run"):
                grades[v["run"]] = v.get("grade")
    rows = []
    for mp in sorted(root.glob("runs/*/meta.json")):
        m = json.loads(mp.read_text(encoding="utf-8"))
        m["grade"] = grades.get(mp.parent.name)
        rows.append(m)
    return rows


def boot(diffs: list[float], n: int = 2000, seed: int = 7) -> tuple[float, float]:
    if len(diffs) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    ms = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


METRICS = [("cost_usd", "cost $"), ("total_tokens", "total tok"), ("out", "out tok"),
           ("tool_calls", "tool calls"), ("n_files_read", "files opened"), ("api_calls", "API calls"),
           ("wall_s", "wall s")]


def val(r: dict, k: str) -> float:
    if k == "out":
        return r["tokens"]["output"]
    return r.get(k) or 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--base", default="bare")
    a = ap.parse_args()
    rows = load(Path(a.out))
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    p = print
    p("### Per arm (means)\n")
    p("| arm | n | " + " | ".join(lbl for _, lbl in METRICS) + " | median $ | first ctx | RW tool used | RW first | correct/partial/wrong/ungraded |")
    p("|---|---" + "|---" * (len(METRICS) + 5) + "|")
    for arm, rs in sorted(by_arm.items()):
        g = [r["grade"] for r in rs]
        cells = [f"{statistics.mean(val(r, k) for r in rs):.4g}" for k, _ in METRICS]
        ctx = statistics.mean((r.get("first_ctx") or 0) for r in rs)
        p(f"| {arm} | {len(rs)} | " + " | ".join(cells) +
          f" | {statistics.median(r['cost_usd'] or 0 for r in rs):.4f} | {ctx:.0f} | {sum(r['used_rw'] for r in rs)} of {len(rs)} | {sum(r['first_tool_rw'] for r in rs)} of {len(rs)} | "
          f"{g.count('correct')}/{g.count('partial')}/{g.count('wrong')}/{g.count(None)} |")
    key = {(r["task"], r["rep"], r["arm"]): r for r in rows}
    p(f"\n### Paired against {a.base} (arm minus {a.base}; relative = sum over sum)\n")
    p("| arm | metric | pairs | mean diff | boot95 | relative | arm lower | arm higher |")
    p("|---|---|---|---|---|---|---|---|")
    for arm in sorted(by_arm):
        if arm == a.base:
            continue
        pairs = [(key[(t, rp, a.base)], r) for (t, rp, ar), r in key.items() if ar == arm and (t, rp, a.base) in key]
        if not pairs:
            continue
        for k, lbl in METRICS:
            d = [val(x, k) - val(b, k) for b, x in pairs]
            lo, hi = boot(d)
            base_sum = sum(val(b, k) for b, _ in pairs) or 1
            p(f"| {arm} | {lbl} | {len(pairs)} | {statistics.mean(d):.4g} | [{lo:.3g}, {hi:.3g}] | "
              f"{100 * sum(d) / base_sum:+.0f}% | {sum(1 for v in d if v < 0)} | {sum(1 for v in d if v > 0)} |")
        rank = {"correct": 2, "partial": 1, "wrong": 0}
        better = sum(1 for b, x in pairs if x["grade"] in rank and b["grade"] in rank and rank[x["grade"]] > rank[b["grade"]])
        worse = sum(1 for b, x in pairs if x["grade"] in rank and b["grade"] in rank and rank[x["grade"]] < rank[b["grade"]])
        p(f"| {arm} | correctness | {len(pairs)} | better on {better}, worse on {worse} | | | | |")
    p(f"\n### By tier against {a.base} (summed within tier)\n")
    p("| tier | arm | pairs | tokens | dollars | output tok | RW used | correct arm / base |")
    p("|---|---|---|---|---|---|---|---|")
    tiers = sorted({r.get("tier") or "-" for r in rows})
    for tier in tiers:
        for arm in sorted(by_arm):
            if arm == a.base:
                continue
            pairs = [(key[(t, rp, a.base)], r) for (t, rp, ar), r in key.items()
                     if ar == arm and (t, rp, a.base) in key and (r.get("tier") or "-") == tier]
            if not pairs:
                continue

            def rel(k):
                s0 = sum(val(b, k) for b, _ in pairs) or 1
                return f"{100 * (sum(val(x, k) for _, x in pairs) - s0) / s0:+.0f}%"
            p(f"| {tier} | {arm} | {len(pairs)} | {rel('total_tokens')} | {rel('cost_usd')} | {rel('out')} | "
              f"{sum(x['used_rw'] for _, x in pairs)} of {len(pairs)} | "
              f"{sum(x['grade'] == 'correct' for _, x in pairs)} / {sum(b['grade'] == 'correct' for b, _ in pairs)} |")
    p("\n### Per run\n")
    p("| task | rep | arm | $ | total tok | out tok | API calls | tools | RW calls (tools) | first RW | files opened | grade |")
    p("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["task"], r["rep"], r["arm"])):
        p(f"| {r['task']} | {r['rep']} | {r['arm']} | {r['cost_usd'] or 0:.4f} | {r['total_tokens']} | {r['tokens']['output']} | "
          f"{r['api_calls']} | {r['tool_calls']} | {r['rw_calls']} ({','.join(r['rw_tools'])}) | {r['first_tool_rw']} | "
          f"{r['n_files_read']} | {r['grade']} |")
    total = sum(r["cost_usd"] or 0 for r in rows)
    judge = sum(json.loads(v.read_text()).get("cost_usd") or 0 for v in (Path(a.out) / "judge").glob("verdict_*.json"))
    vers = {r.get("cc_version") for r in rows}
    models = {r.get("model") for r in rows}
    p(f"\nRuns ${total:.3f}, judge ${judge:.3f}. Claude Code {vers}, models {models}.")


if __name__ == "__main__":
    main()
