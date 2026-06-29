#!/usr/bin/env python3
"""
Stage A failure mining for the SWE-bench validation (offline, free).

Ranks SWE-bench Verified instances by how often strong frontier systems FAILED
them, using the public github.com/swe-bench/experiments leaderboard dumps. The
hard tail (failed by most/all systems) is the candidate pool for the paired
C0/C1 run — these are where a localization edge could actually show, and where a
random sample would saturate and hide any delta.

This is a PRIOR, not a verdict: those systems use other models/scaffolds, so the
shortlist must still be confirmed with our own C0 (Stage B) and triaged for
localization-vs-reasoning failure mode (Stage C).

Usage:
  # 1. shallow + sparse clone the experiments dump (only the verified results):
  #    git clone --depth 1 --filter=blob:none --sparse \
  #        https://github.com/swe-bench/experiments
  #    cd experiments && git sparse-checkout set evaluation/verified
  #
  # 2. rank:
  python scripts/mine_swebench_failures.py \
      --experiments <path-to>/experiments \
      --repos django/django sympy/sympy sphinx-doc/sphinx \
      --top 40 --out results_failure_ranking.json
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parents[1]
_TASKS = _BENCH_ROOT / "data" / "swe_bench" / "tasks.json"


def load_verified_index() -> dict:
    """instance_id -> {repo, difficulty} for the local Verified subset."""
    with open(_TASKS, encoding="utf-8") as f:
        tasks = json.load(f)
    return {t["instance_id"]: {"repo": t["repo"],
                               "difficulty": t.get("difficulty", "")}
            for t in tasks}


def _read_resolved_ids(results_json: Path) -> set:
    """Extract resolved instance ids from one system's results.json.

    The dumps vary across submissions; handle the common shapes:
      {"resolved": [...]} | {"resolved_ids": [...]} |
      {"resolved_instances": [...]} | a bare list of ids.
    """
    try:
        data = json.loads(results_json.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(data, list):
        return set(data)
    for key in ("resolved", "resolved_ids", "resolved_instances"):
        if isinstance(data.get(key), list):
            return set(data[key])
    return set()


def discover_systems(experiments: Path) -> list:
    """All system result dirs under evaluation/verified/<system>/."""
    base = experiments / "evaluation" / "verified"
    if not base.exists():
        raise SystemExit(f"Not found: {base} (did you sparse-checkout "
                         f"evaluation/verified?)")
    systems = []
    for sysdir in sorted(base.iterdir()):
        if not sysdir.is_dir():
            continue
        rj = sysdir / "results" / "results.json"
        if rj.exists():
            systems.append((sysdir.name, rj))
    return systems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", required=True,
                    help="path to a clone of swe-bench/experiments")
    ap.add_argument("--repos", nargs="*", default=None,
                    help="filter to these org/name repos (default: all)")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-systems", type=int, default=5,
                    help="require at least this many systems to have reported")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    verified = load_verified_index()
    systems = discover_systems(Path(args.experiments))
    print(f"Found {len(systems)} systems with verified results")

    resolved_count = defaultdict(int)
    reported_count = defaultdict(int)
    all_ids = set(verified)
    for name, rj in systems:
        resolved = _read_resolved_ids(rj)
        for iid in all_ids:
            reported_count[iid] += 1  # every system is scored on the full set
        for iid in resolved & all_ids:
            resolved_count[iid] += 1

    n_systems = len(systems)
    rows = []
    for iid, meta in verified.items():
        if args.repos and meta["repo"] not in set(args.repos):
            continue
        if n_systems < args.min_systems:
            continue
        res = resolved_count.get(iid, 0)
        fail_frac = 1.0 - (res / n_systems) if n_systems else 0.0
        rows.append({
            "instance_id": iid,
            "repo": meta["repo"],
            "difficulty": meta["difficulty"],
            "systems_resolved": res,
            "systems_total": n_systems,
            "fail_fraction": round(fail_frac, 4),
        })

    rows.sort(key=lambda r: (-r["fail_fraction"], r["instance_id"]))
    top = rows[:args.top]

    print(f"\nHardest {len(top)} instances "
          f"(repos={args.repos or 'all'}, {n_systems} systems):\n")
    for r in top:
        print(f"  fail={r['fail_fraction']:.2f} "
              f"({r['systems_total'] - r['systems_resolved']}/{r['systems_total']} failed) "
              f"{r['instance_id']:<28} {r['difficulty']}")

    if args.out:
        Path(args.out).write_text(json.dumps(top, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
