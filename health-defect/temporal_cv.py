"""Phase 11 Part A — temporal cross-validation.

Repeats the leakage-free T0 pipeline at several distinct, rolling 6-month
windows and reports the stability of the headline metrics across them. A single
T0 could be a lucky window; three rolling windows guard against that.

For each (T0, repo) it indexes the repo *at the T0 commit* (the same detached-
worktree path the main benchmark uses) and counts keyword bug-fix defects in the
**bounded** window ``(T0, T0+window_months]`` — bounded (not ``..HEAD``) so the
earlier windows measure a comparable 6-month horizon, not "everything since".

Keyword labels + no SZZ/issue/gh — temporal CV is about *stability*, and keeping
it offline + label-simple makes a 3×N-repo sweep tractable. Each indexed T0 is
cached under ``results/temporal/<repo>__<t0>.json`` so the (expensive) sweep is
resumable.

Usage (long — background it)::

    ../../.venv/Scripts/python.exe temporal_cv.py \
        --t0 2025-05-23 --t0 2025-08-23 --t0 2025-11-23 \
        --repos pydantic,hono,zod,axios,clap,bat,gin,chi,spdlog
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from lib.defect_counter import _attribute, find_fix_commits, resolve_t0_sha
from lib.health_runner import run_health_at_commit
from lib.stats import auc_metric, effort_aware_at_loc, partial_spearman, popt_metric
from run_benchmark import join_and_filter

_BENCH = Path(__file__).resolve().parent
_RESULTS = _BENCH.parent / "results"
_REPOS = _BENCH.parent / "repos"
_CACHE = _RESULTS / "temporal"


def _shift(date: str, months: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=months * 30)).strftime(
        "%Y-%m-%d"
    )


def _metrics(joined: list[dict]) -> dict:
    pos = sum(1 for d in joined if d["defect_count"] > 0)
    if pos == 0 or len(joined) < 4:
        return {"n": len(joined), "n_pos": pos, "auc": None, "popt": None,
                "partial_rho": None, "precision20": None}
    scores = [d["health_score"] for d in joined]
    defects = [float(d["defect_count"]) for d in joined]
    nlocs = [float(d["nloc"]) for d in joined]
    eff = effort_aware_at_loc(joined, 0.20)
    return {
        "n": len(joined),
        "n_pos": pos,
        "auc": auc_metric(joined),
        "popt": popt_metric(joined),
        "partial_rho": partial_spearman(scores, defects, nlocs),
        "precision20": eff["precision"],
        "recall20": eff["recall_files"],
    }


def run_repo_at_t0(rc: dict, t0_date: str, window_months: int, timeout: int) -> dict:
    name = rc["name"]
    repo_dir = str(_REPOS / name)
    cache = _CACHE / f"{name}__{t0_date}.json"
    source_root = rc["source_root"]
    extensions = tuple(rc.get("extensions", [".py"]))
    exclude = rc.get("exclude", [])

    t0_sha = resolve_t0_sha(repo_dir, t0_date)
    t1_date = _shift(t0_date, window_months)
    t1_sha = resolve_t0_sha(repo_dir, t1_date)

    if cache.exists():
        health = json.loads(cache.read_text())
    else:
        t0 = time.time()
        health = run_health_at_commit(
            repo_dir, t0_sha, timeout=timeout, exclude_patterns=exclude
        )
        cache.write_text(json.dumps(health))
        print(f"    indexed {name}@{t0_date} in {time.time() - t0:.0f}s "
              f"({len(health.get('metrics', []))} files)")

    fixes = find_fix_commits(
        repo_dir, t0_sha, t1_sha, strategy=rc.get("defect_strategy", "keyword"),
        include=rc.get("bug_keywords"), exclude=rc.get("exclude_keywords"),
    )
    counts = _attribute(repo_dir, [s for s, _ in fixes], source_root, extensions)
    joined = join_and_filter(
        health, counts, min_nloc=10, exclude_tests=True, exclude_patterns=exclude
    )
    m = _metrics(joined)
    m.update({"repo": name, "t0": t0_date, "window_end": t1_date, "n_fixes": len(fixes)})
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", action="append", required=True,
                    help="T0 date (repeatable); each defines a rolling window.")
    ap.add_argument("--repos", required=True, help="Comma-separated repo names.")
    ap.add_argument("--window-months", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--out", type=Path, default=_RESULTS / "temporal_cv.json")
    args = ap.parse_args()

    _CACHE.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
    by_name = {r["name"]: r for r in cfg["repos"]}
    repo_names = [r.strip() for r in args.repos.split(",") if r.strip()]

    rows: list[dict] = []
    for t0 in args.t0:
        print(f"\n=== T0 {t0} (window → {_shift(t0, args.window_months)}) ===")
        for name in repo_names:
            if name not in by_name:
                print(f"    !! {name} not in config; skipping")
                continue
            try:
                m = run_repo_at_t0(by_name[name], t0, args.window_months, args.timeout)
                rows.append(m)
                a = f"{m['auc']:.3f}" if m["auc"] is not None else "n/a"
                print(f"    {name:10} n={m['n']:4} pos={m['n_pos']:3} "
                      f"fixes={m['n_fixes']:3} AUC={a}")
            except Exception as exc:
                print(f"    !! {name}@{t0} FAILED: {exc}")

    # Per-T0 cross-project means + overall stability.
    by_t0: dict[str, list[dict]] = {}
    for r in rows:
        by_t0.setdefault(r["t0"], []).append(r)

    def _mean(vals: list[float | None]) -> float | None:
        v = [x for x in vals if x is not None]
        return sum(v) / len(v) if v else None

    summary = {}
    for t0, rs in by_t0.items():
        summary[t0] = {
            "n_repos": len(rs),
            "mean_auc": _mean([r["auc"] for r in rs]),
            "mean_popt": _mean([r["popt"] for r in rs]),
            "mean_partial_rho": _mean([r["partial_rho"] for r in rs]),
            "mean_precision20": _mean([r["precision20"] for r in rs]),
            "total_pos": sum(r["n_pos"] for r in rs),
        }

    # Stability across windows: spread of the per-T0 mean AUC.
    mean_aucs = [s["mean_auc"] for s in summary.values() if s["mean_auc"] is not None]
    stability = {
        "mean_auc_across_windows": _mean(mean_aucs),
        "min_auc": min(mean_aucs) if mean_aucs else None,
        "max_auc": max(mean_aucs) if mean_aucs else None,
        "range": (max(mean_aucs) - min(mean_aucs)) if mean_aucs else None,
    }

    out = {
        "t0_windows": args.t0,
        "window_months": args.window_months,
        "repos": repo_names,
        "per_repo": rows,
        "per_t0_summary": summary,
        "stability": stability,
    }
    args.out.write_text(json.dumps(out, indent=2))

    print("\n=== Temporal CV summary (cross-project mean per window) ===")
    for t0 in args.t0:
        s = summary.get(t0)
        if not s:
            continue
        print(f"  {t0}: AUC {s['mean_auc']:.3f}  Popt {s['mean_popt']:.3f}  "
              f"partial-rho {s['mean_partial_rho']:+.3f}  "
              f"(repos={s['n_repos']}, pos={s['total_pos']})")
    if stability["range"] is not None:
        print(f"\n  AUC across windows: {stability['min_auc']:.3f}–"
              f"{stability['max_auc']:.3f} (range {stability['range']:.3f}, "
              f"mean {stability['mean_auc_across_windows']:.3f})")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
