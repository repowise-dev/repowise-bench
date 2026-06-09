#!/usr/bin/env python3
"""agent_characterization.py — how agent code behaves vs human code, within-repo.

Consumes the per-commit label records (`agent_defect_labels.py`) and reports,
per repo and per authorship group (human / T1 / T2 / T3 / per-agent):

  * fix share                  (share of commits that are keyword fixes)
  * self-fix share of fixes    (fix touching a file the same identity touched
                                within 48 h — agent fix-spam)
  * median self-fix latency    (hours from own previous touch to the fix)
  * revert rate                (commit later reverted; eligibility: >=30 d old)
  * test discipline            (source-touching commit also touches tests)
  * followed-by-fix-within-90d (a later NON-self-fix, non-revert fix touches
                                >=1 of the commit's files; eligibility: >=90 d
                                before HEAD) — the blame-free defect proxy
  * median files touched       (size proxy; plus the 90-d proxy by size band)

Pooling follows the bench hygiene: rates are computed within repo; the pooled
contrast per tier is the mean within-repo delta (group − human) with a
cluster bootstrap (resample repos) 95% CI. Saturation-exhibit repos are
reported separately, never pooled into headlines. A repo contributes to a
contrast only when both cells have >= MIN_N commits.

Run::

    .venv/Scripts/python.exe health-defect/agent_characterization.py \
        --labels-dir <data>/agent-repos/_labels --out <report dir>
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MIN_N = 30
ELIG_FIX_DAYS = 90
ELIG_REVERT_DAYS = 30
SATURATION = {"github/gh-aw", "koala73/worldmonitor",
              "windmill-labs/windmill", "PrimeIntellect-ai/verifiers",
              "fern-api/fern", "basicmachines-co/basic-memory",
              "binaricat/Netcatty"}
SIZE_BANDS = [("1", 1, 1), ("2-4", 2, 4), ("5+", 5, 10 ** 9)]


def group_of(c: dict) -> str:
    if not c["agent"]:
        return "human"
    return f"t{c['tier']}"


def repo_metrics(data: dict) -> dict:
    commits = [c for c in data["commits"] if c["n_files"] > 0]
    if not commits:
        return {}
    head_ts = max(c["ts"] for c in commits)

    # per-file timelines for the 90-d proxy and self-fix latency
    fix_times: dict[str, list[int]] = defaultdict(list)
    for c in commits:
        if c["is_fix"] and not c["self_fix"] and not c["is_revert"] \
                and not c["was_reverted"]:
            for f in c["files"]:
                fix_times[f].append(c["ts"])
    for v in fix_times.values():
        v.sort()

    touch_hist: dict[str, list[tuple[int, str]]] = defaultdict(list)

    groups: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for c in commits:
        g = group_of(c)
        ident = c["agent"] or c["email"]
        for gg in (g, f"agent:{c['agent']}" if c["agent"] else None):
            if not gg:
                continue
            m = groups[gg]
            m["n"].append(1)
            m["is_fix"].append(c["is_fix"])
            m["n_files"].append(c["n_files"])
            m["tests"].append(1 if c["test_files"] > 0 else 0)
            if c["is_fix"]:
                m["self_fix"].append(1 if c["self_fix"] else 0)
            if head_ts - c["ts"] >= ELIG_REVERT_DAYS * 86400:
                m["reverted"].append(1 if c["was_reverted"] else 0)
            if head_ts - c["ts"] >= ELIG_FIX_DAYS * 86400:
                later = 0
                for f in c["files"]:
                    times = fix_times.get(f)
                    if times:
                        import bisect
                        i = bisect.bisect_right(times, c["ts"])
                        if i < len(times) and times[i] <= c["ts"] + ELIG_FIX_DAYS * 86400:
                            later = 1
                            break
                m["fix90"].append(later)
                for label, lo, hi in SIZE_BANDS:
                    if lo <= c["n_files"] <= hi:
                        m[f"fix90_{label}"].append(later)
            # self-fix latency: nearest own prior touch among touched files
            if c["self_fix"]:
                best = None
                for f in c["files"]:
                    for ts_prev, id_prev in reversed(touch_hist[f]):
                        if ts_prev >= c["ts"]:
                            continue
                        if id_prev == ident:
                            d = c["ts"] - ts_prev
                            best = d if best is None else min(best, d)
                        break
                if best is not None:
                    m["self_fix_latency_h"].append(best / 3600)
        for f in c["files"]:
            touch_hist[f].append((c["ts"], c["agent"] or c["email"]))

    out = {}
    for g, m in groups.items():
        n = len(m["n"])
        if n == 0:
            continue
        def rate(key):
            v = m.get(key, [])
            return (round(sum(v) / len(v), 4), len(v)) if v else (None, 0)
        row = {"n": n}
        for key, name in (("is_fix", "fix_share"), ("self_fix", "self_fix_share"),
                          ("reverted", "revert_rate"), ("tests", "test_share"),
                          ("fix90", "fix90")):
            row[name], row[f"{name}_n"] = rate(key)
        for label, _, _ in SIZE_BANDS:
            row[f"fix90_{label}"], row[f"fix90_{label}_n"] = rate(f"fix90_{label}")
        row["median_files"] = statistics.median(m["n_files"])
        lat = m.get("self_fix_latency_h", [])
        row["self_fix_latency_h_median"] = round(statistics.median(lat), 1) if lat else None
        out[g] = row
    return out


def pooled_deltas(per_repo: dict[str, dict], metric: str, group: str,
                  *, rng: random.Random, n_boot: int = 2000) -> dict | None:
    """Mean within-repo (group − human) delta with cluster-bootstrap CI."""
    deltas = []
    for repo, gm in per_repo.items():
        g, h = gm.get(group), gm.get("human")
        if not g or not h:
            continue
        if (g.get(f"{metric}_n") or 0) < MIN_N or (h.get(f"{metric}_n") or 0) < MIN_N:
            continue
        if g.get(metric) is None or h.get(metric) is None:
            continue
        deltas.append((repo, g[metric] - h[metric], g[metric], h[metric]))
    if len(deltas) < 3:
        return None
    vals = [d[1] for d in deltas]
    boots = []
    for _ in range(n_boot):
        sample = [rng.choice(vals) for _ in vals]
        boots.append(sum(sample) / len(sample))
    boots.sort()
    return {"mean_delta": round(sum(vals) / len(vals), 4),
            "ci95": [round(boots[int(0.025 * n_boot)], 4),
                     round(boots[int(0.975 * n_boot)], 4)],
            "n_repos": len(deltas),
            "per_repo": [{"repo": r, "delta": round(d, 4),
                          "group": g, "human": h} for r, d, g, h in deltas]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20260604)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    per_repo_main: dict[str, dict] = {}
    per_repo_sat: dict[str, dict] = {}
    for path in sorted(args.labels_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        repo = data["summary"]["repo"]
        metrics = repo_metrics(data)
        (per_repo_sat if repo in SATURATION else per_repo_main)[repo] = metrics

    metrics = ["fix_share", "self_fix_share", "revert_rate", "test_share",
               "fix90", "fix90_1", "fix90_2-4", "fix90_5+"]
    contrasts = {}
    for pool_name, pool in (("main", per_repo_main), ("saturation", per_repo_sat)):
        contrasts[pool_name] = {}
        for group in ("t1", "t2", "t3"):
            contrasts[pool_name][group] = {
                m: pooled_deltas(pool, m, group, rng=rng) for m in metrics}

    result = {"generated": datetime.now(timezone.utc).isoformat(),
              "min_n": MIN_N, "seed": args.seed,
              "per_repo_main": per_repo_main, "per_repo_saturation": per_repo_sat,
              "contrasts": contrasts}
    (args.out_dir / "characterization.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")

    # markdown report
    lines = ["# Agent-vs-human characterization (within-repo, window 2025-06 → HEAD)",
             f"\nGenerated {result['generated']} · MIN_N={MIN_N} per cell · "
             "delta = group rate − human rate in the SAME repo · cluster-bootstrap "
             "95% CI over repos · saturation-exhibit repos pooled separately.\n"]
    for pool_name in ("main", "saturation"):
        lines.append(f"\n## Pool: {pool_name}\n")
        lines.append("| metric | tier | mean Δ | 95% CI | n repos |")
        lines.append("|---|---|--:|---|--:|")
        for group in ("t1", "t2", "t3"):
            for m in metrics:
                c = contrasts[pool_name][group][m]
                if not c:
                    continue
                lo, hi = c["ci95"]
                star = " **\\***" if lo > 0 or hi < 0 else ""
                lines.append(f"| {m} | {group} | {c['mean_delta']:+.3f}{star} | "
                             f"[{lo:+.3f}, {hi:+.3f}] | {c['n_repos']} |")
    lines.append("\n\\* CI excludes 0.\n")
    lines.append("\n## Per-repo group rates (main pool)\n")
    lines.append("| repo | group | n | fix | self-fix | latency h | revert | tests | fix90 | med files |")
    lines.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for pool in (per_repo_main, per_repo_sat):
        for repo, gm in sorted(pool.items()):
            for g in ("human", "t1", "t2", "t3"):
                r = gm.get(g)
                if not r or r["n"] < MIN_N:
                    continue
                def fmt(x):
                    return f"{x:.3f}" if isinstance(x, float) else ("—" if x is None else x)
                lines.append(f"| {repo} | {g} | {r['n']} | {fmt(r['fix_share'])} | "
                             f"{fmt(r['self_fix_share'])} | {fmt(r['self_fix_latency_h_median'])} | "
                             f"{fmt(r['revert_rate'])} | {fmt(r['test_share'])} | "
                             f"{fmt(r['fix90'])} | {r['median_files']} |")
    (args.out_dir / "CHARACTERIZATION.md").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    print(f"wrote {args.out_dir / 'CHARACTERIZATION.md'}")


if __name__ == "__main__":
    main()
