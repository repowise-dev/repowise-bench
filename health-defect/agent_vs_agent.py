#!/usr/bin/env python3
"""agent_vs_agent.py — within-repo contrasts between agents.

Same repo, same review process, same window — different agent. For every repo
where two agents both have >= MIN_N commits, report the pairwise delta on the
characterization metrics with a per-commit bootstrap 95% CI (resample commits
within each agent cell). Repos are never pooled across agents here — the
agent mix per repo is confounded with task mix; this is a per-repo exhibit
with replication counted across repos.

Metrics mirror agent_characterization.py: fix share, self-fix share of fixes,
test discipline, followed-by-fix-within-90d (blame-free defect proxy,
eligibility >=90 d before HEAD), median files touched.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_vs_agent.py \
        --labels-dir <data>/agent-repos/_labels --out-dir <data>/agent-repos/_characterization
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

MIN_N = 30
ELIG_FIX_DAYS = 90
N_BOOT = 2000
SATURATION = {"github/gh-aw", "koala73/worldmonitor",
              "windmill-labs/windmill", "PrimeIntellect-ai/verifiers",
              "fern-api/fern", "basicmachines-co/basic-memory",
              "binaricat/Netcatty"}
METRICS = ("fix_share", "self_fix_share", "test_share", "fix90")


def per_commit_records(data: dict) -> dict[str, list[dict]]:
    """agent name -> per-commit metric records (mirrors agent_characterization)."""
    commits = [c for c in data["commits"] if c["n_files"] > 0]
    if not commits:
        return {}
    head_ts = max(c["ts"] for c in commits)

    fix_times: dict[str, list[int]] = defaultdict(list)
    for c in commits:
        if c["is_fix"] and not c["self_fix"] and not c["is_revert"] \
                and not c["was_reverted"]:
            for f in c["files"]:
                fix_times[f].append(c["ts"])
    for v in fix_times.values():
        v.sort()

    by_agent: dict[str, list[dict]] = defaultdict(list)
    for c in commits:
        if not c["agent"]:
            continue
        rec = {"fix_share": int(c["is_fix"]),
               "test_share": int(c["test_files"] > 0),
               "self_fix_share": int(c["self_fix"]) if c["is_fix"] else None,
               "n_files": c["n_files"], "fix90": None}
        if head_ts - c["ts"] >= ELIG_FIX_DAYS * 86400:
            later = 0
            for f in c["files"]:
                times = fix_times.get(f)
                if times:
                    i = bisect.bisect_right(times, c["ts"])
                    if i < len(times) and times[i] <= c["ts"] + ELIG_FIX_DAYS * 86400:
                        later = 1
                        break
            rec["fix90"] = later
        by_agent[c["agent"]].append(rec)
    return by_agent


def rate(recs: list[dict], metric: str) -> tuple[float | None, int]:
    vals = [r[metric] for r in recs if r[metric] is not None]
    return (sum(vals) / len(vals) if vals else None), len(vals)


def boot_delta(a: list[dict], b: list[dict], metric: str,
               rng: random.Random) -> dict | None:
    ra, na = rate(a, metric)
    rb, nb = rate(b, metric)
    if ra is None or rb is None or na < MIN_N or nb < MIN_N:
        return None
    boots = []
    for _ in range(N_BOOT):
        sa = [rng.choice(a) for _ in range(len(a))]
        sb = [rng.choice(b) for _ in range(len(b))]
        da, _ = rate(sa, metric)
        db, _ = rate(sb, metric)
        if da is not None and db is not None:
            boots.append(da - db)
    boots.sort()
    return {"delta": round(ra - rb, 4), "a": round(ra, 4), "b": round(rb, 4),
            "n_a": na, "n_b": nb,
            "ci95": [round(boots[int(0.025 * len(boots))], 4),
                     round(boots[int(0.975 * len(boots))], 4)]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20260604)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    results = []
    for path in sorted(args.labels_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        repo = data["summary"]["repo"]
        by_agent = per_commit_records(data)
        big = {a: r for a, r in by_agent.items() if len(r) >= MIN_N}
        for a, b in combinations(sorted(big), 2):
            pair = {"repo": repo, "saturation": repo in SATURATION,
                    "agent_a": a, "agent_b": b, "metrics": {}}
            for m in METRICS:
                d = boot_delta(big[a], big[b], m, rng)
                if d:
                    pair["metrics"][m] = d
            pair["median_files"] = {
                a: statistics.median(r["n_files"] for r in big[a]),
                b: statistics.median(r["n_files"] for r in big[b])}
            if pair["metrics"]:
                results.append(pair)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "agent_vs_agent.json").write_text(
        json.dumps({"generated": datetime.now(timezone.utc).isoformat(),
                    "min_n": MIN_N, "pairs": results}, indent=1),
        encoding="utf-8")

    lines = ["# Agent-vs-agent, within-repo (same project, same review process)",
             f"\nGenerated {datetime.now(timezone.utc).isoformat()} · "
             f"MIN_N={MIN_N} per cell · Δ = agent A − agent B · per-commit "
             "bootstrap 95% CI · saturation repos flagged, shown separately.\n",
             "| repo | A vs B | metric | A | B | Δ | 95% CI |",
             "|---|---|---|--:|--:|--:|---|"]
    for sat in (False, True):
        for p in results:
            if p["saturation"] is not sat:
                continue
            tag = " (saturation)" if sat else ""
            for m, d in p["metrics"].items():
                lo, hi = d["ci95"]
                star = " **\\***" if lo > 0 or hi < 0 else ""
                lines.append(
                    f"| {p['repo']}{tag} | {p['agent_a']} (n={d['n_a']}) vs "
                    f"{p['agent_b']} (n={d['n_b']}) | {m} | {d['a']:.3f} | "
                    f"{d['b']:.3f} | {d['delta']:+.3f}{star} | [{lo:+.3f}, {hi:+.3f}] |")
    lines.append("\n\\* CI excludes 0.\n")
    (args.out_dir / "AGENT_VS_AGENT.md").write_text("\n".join(lines) + "\n",
                                                    encoding="utf-8")
    print(f"wrote {args.out_dir / 'AGENT_VS_AGENT.md'}")


if __name__ == "__main__":
    main()
