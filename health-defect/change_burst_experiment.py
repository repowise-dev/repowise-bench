#!/usr/bin/env python3
"""change_burst_experiment.py — temporal change *bursts* through the gate.

RESEARCH ARTIFACT (bench-only). Phase-3 Part A. Nagappan et al. (ISSRE'10,
"Change Bursts as Defect Predictors") found that *consecutive* changes clustered
in a short window were the single highest-power defect feature on Windows Vista —
and crucially it is the **temporal clustering**, not the raw churn volume, so it
is distinct from the shipped ``churn_risk`` biomarker (which counts changes, not
their burstiness). Pure git, cheap, leakage-free (computed strictly on commits
up to T0; defects are labelled (T0, HEAD]).

Definition (per file, parameterised by gap-days ``G`` and burst-size ``B``):
  * Collect the file's change timestamps in a window of ``--window-days`` before
    T0 (commits touching the file under ``source_root`` / ``extensions``, no
    merges, tests + index-excludes dropped — same universe as the file join).
  * A **burst** is a maximal run of successive changes whose consecutive gaps are
    all <= ``G`` days. ``size`` = number of changes in the run.
  * ``n_bursts``  = number of runs with ``size >= B`` (Nagappan's "number of
    consecutive changes" feature).
  * ``max_burst`` = size of the largest run (independent of B).
  * ``burst_frac`` = fraction of the file's window changes that fall in a
    qualifying (>= B) burst.

We sweep G in {3,7,14} days and B in {2,3}, **pick the ``n_bursts`` variant by
within-NLOC-band lift** (per the plan, not overall AUC), then run the winning
parameterisation's three columns through the full §3 gate and the drop-one
replacement lens (does a burst column earn a slot by *substituting* for a weak
biomarker, not just by adding?).

A file with no window changes is **absent** (None), never zero — absent != zero
is enforced by ``candidate_eval``'s computable universe.

Run (absolute venv python from the R&D worktree — ``../../.venv`` resolves wrong
from a worktree; point dirs at the MAIN bench checkout)::

    $env:PYTHONIOENCODING="utf-8"
    C:\\Users\\ragha\\Desktop\\repowise\\.venv\\Scripts\\python.exe change_burst_experiment.py \\
        --results-dir C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\results \\
        --repos-dir   C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\repos
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path

import numpy as np

import candidate_eval as ce
from lib.defect_counter import _git, resolve_t0_sha
from lib.filters import is_test_file, normalize_path

_HERE = Path(__file__).resolve().parent

DAY = 86400.0
GAPS = [3, 7, 14]            # G — max gap (days) between successive changes in a burst
SIZES = [2, 3]              # B — minimum changes for a run to count as a burst
DEFAULT_WINDOW_DAYS = 365


def _make_exclude_matcher(patterns: list[str]):
    if not patterns:
        return lambda _p: False
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return lambda p: spec.match_file(p)


def since_arg(t0_date: str, window_days: int) -> str:
    """``--since=<absolute date>`` anchored ``window_days`` before T0 — NOT the
    relative ``N days ago`` (which is relative to the run date, not T0, so it is
    neither reproducible nor T0-anchored)."""
    if not window_days:
        return "--max-count=100000"
    d = _dt.date.fromisoformat(t0_date) - _dt.timedelta(days=window_days)
    return f"--since={d.isoformat()}"


def file_change_times(
    repo_dir: str, t0_sha: str, *, source_root: str, extensions: tuple[str, ...],
    is_excluded, since: str,
) -> dict[str, list[float]]:
    """{file_path: [commit_unix_ts, ...]} for source files, in the window before
    T0. Single ``git log`` pass with ``--name-only``; merges excluded."""
    # Anchor the window's *end* at T0 by walking history from t0_sha backwards.
    out = _git(
        ["log", t0_sha, "--no-merges", "--no-renames", since,
         "--pretty=format:\x01%ct", "--name-only"],
        cwd=repo_dir,
    )
    times: dict[str, list[float]] = {}
    cur_ts: float | None = None
    for raw in out.split("\n"):
        if not raw:
            continue
        if raw[0] == "\x01":
            cur_ts = float(raw[1:])
            continue
        if cur_ts is None:
            continue
        f = normalize_path(raw)
        if not f or not f.startswith(source_root):
            continue
        if not any(f.endswith(e) for e in extensions):
            continue
        if is_test_file(f) or is_excluded(f):
            continue
        times.setdefault(f, []).append(cur_ts)
    return times


def burst_features(ts: list[float], gap_days: int, min_size: int) -> dict[str, float]:
    """Burst features for one file's change timestamps."""
    if not ts:
        return {"n_bursts": 0.0, "max_burst": 0.0, "burst_frac": 0.0, "n_changes": 0.0}
    s = sorted(ts)
    gap = gap_days * DAY
    runs: list[int] = []
    run = 1
    for prev, nxt in zip(s, s[1:]):
        if (nxt - prev) <= gap:
            run += 1
        else:
            runs.append(run)
            run = 1
    runs.append(run)
    qualifying = [r for r in runs if r >= min_size]
    return {
        "n_bursts": float(len(qualifying)),
        "max_burst": float(max(runs)),
        "burst_frac": float(sum(qualifying) / len(s)) if qualifying else 0.0,
        "n_changes": float(len(s)),
    }


def build_repo_times(
    repo: str, cfg: dict, repos_dir: Path, cache_path: Path, *,
    window_days: int, rebuild: bool,
) -> dict[str, list[float]]:
    if cache_path.exists() and not rebuild:
        d = json.loads(cache_path.read_text())
        if d.get("_meta", {}).get("window_days") == window_days:
            return {k: v for k, v in d.items() if k != "_meta"}
    repo_dir = (repos_dir / repo).resolve()
    nested = repo_dir / repo
    if nested.exists() and (nested / ".git").exists():
        repo_dir = nested
    if not repo_dir.exists():
        raise FileNotFoundError(f"clone missing: {repo_dir}")
    repo_dir = str(repo_dir)
    source_root = cfg["source_root"]
    extensions = tuple(cfg.get("extensions", [".py"]))
    is_excluded = _make_exclude_matcher(list(cfg.get("exclude") or []))
    t0_sha = resolve_t0_sha(repo_dir, cfg["t0_date"])
    t = time.time()
    times = file_change_times(
        repo_dir, t0_sha, source_root=source_root, extensions=extensions,
        is_excluded=is_excluded, since=since_arg(cfg["t0_date"], window_days),
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_meta": {"t0_sha": t0_sha, "window_days": window_days,
                         "n_files": len(times), "build_seconds": round(time.time() - t, 1)},
               **times}
    cache_path.write_text(json.dumps(payload))
    return times


def column_for(
    times_by_repo: dict[str, dict[str, list[float]]], feature: str,
    gap_days: int, min_size: int,
) -> dict[str, dict[str, float]]:
    col: dict[str, dict[str, float]] = {}
    for repo, files in times_by_repo.items():
        col[repo] = {f: burst_features(ts, gap_days, min_size)[feature]
                     for f, ts in files.items()}
    return col


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_HERE.parent / "repos")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--repo", default="", help="comma list; default = all config repos")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    import yaml
    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)
    out_path = args.out or (args.results_dir / "change_burst_scorecards.json")

    # --- Step 1: per-repo change timestamps (cached) ------------------------
    print(f"=== Change-burst features: {len(repos)} repos, window={args.window_days}d ===")
    times_by_repo: dict[str, dict[str, list[float]]] = {}
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo}: not in config)")
            continue
        cache = args.results_dir / f"health_defect_{repo}" / "change_times.json"
        try:
            times = build_repo_times(repo, cfg, args.repos_dir, cache,
                                     window_days=args.window_days, rebuild=args.rebuild)
        except Exception as exc:  # noqa: BLE001
            print(f"  (skip {repo}: {exc})")
            continue
        times_by_repo[repo] = times
        n_changes = sum(len(v) for v in times.values())
        print(f"  {repo:12s} files={len(times):4d} changes={n_changes:>6d}")

    rows = ce.load_corpus(args.results_dir, args.config, args.label)
    cost = f"single git-log pass/repo, window={args.window_days}d (pure git, ~<1s/repo)"

    # --- Step 2: G/B sweep on n_bursts, pick by within-band mean ------------
    print(f"\n=== G/B sweep on n_bursts (pick by within-band mean; label={args.label}) ===")
    print(f"{'G(gap)':>7s} {'B(size)':>8s} {'within-band':>12s} {'OOFΔ':>9s} {'max|ρ|':>7s} {'cov':>5s}")
    sweep = {}
    best = None
    for g in GAPS:
        for b in SIZES:
            col = column_for(times_by_repo, "n_bursts", g, b)
            card = ce.evaluate_candidate(
                col, f"n_bursts_G{g}_B{b}", results_dir=args.results_dir,
                config_path=args.config, label=args.label, corpus_rows=rows,
                cost_note=cost, n_boot_auc=300, n_boot_coef=150,
            )
            sweep[f"G{g}_B{b}"] = card
            wb = card["within_band_auc"]["candidate_band_mean"]
            da = card["oof_auc_delta"]["delta"]
            rho = card["redundancy"]["max_abs_spearman"]
            cov = card["coverage_cost"]["coverage_fraction"]
            print(f"{g:>7d} {b:>8d} {str(wb):>12s} {str(da):>9s} {str(rho):>7s} {cov:>5.0%}")
            if wb is not None and (best is None or wb > best[1]):
                best = ((g, b), wb)

    if best is None:
        print("No usable sweep result.")
        return
    (bg, bb), bwb = best
    print(f"\n>>> winner by within-band mean: G={bg} B={bb} (within-band {bwb})")

    # --- Step 3: full gate on the winning parameterisation's columns --------
    print(f"\n=== Full gate on winning burst columns (G={bg} B={bb}, label={args.label}) ===")
    cards = {}
    md_blocks = []
    for feat in ("n_bursts", "max_burst", "burst_frac"):
        col = column_for(times_by_repo, feat, bg, bb)
        card = ce.evaluate_candidate(
            col, f"{feat}_G{bg}_B{bb}", results_dir=args.results_dir,
            config_path=args.config, label=args.label, corpus_rows=rows, cost_note=cost,
        )
        cards[feat] = card
        md = ce.scorecard_markdown(card)
        md_blocks.append(md)
        print("\n" + md)

    # --- Step 4: also score under SZZ for the winning n_bursts --------------
    print(f"\n=== Cross-label check: n_bursts G={bg} B={bb} under SZZ ===")
    rows_szz = ce.load_corpus(args.results_dir, args.config, "szz")
    col = column_for(times_by_repo, "n_bursts", bg, bb)
    card_szz = ce.evaluate_candidate(
        col, f"n_bursts_G{bg}_B{bb}", results_dir=args.results_dir,
        config_path=args.config, label="szz", corpus_rows=rows_szz, cost_note=cost,
    )
    cards["n_bursts_szz"] = card_szz
    md_blocks.append(ce.scorecard_markdown(card_szz))
    print("\n" + ce.scorecard_markdown(card_szz))

    payload = {"winner": {"gap_days": bg, "min_size": bb, "within_band_mean": bwb},
               "sweep": sweep, "cards": cards, "cost_note": cost,
               "window_days": args.window_days}
    out_path.write_text(json.dumps(payload, indent=2))
    out_path.with_suffix(".md").write_text("\n\n".join(md_blocks))
    print(f"\nWrote {out_path}")
    print(f"Wrote {out_path.with_suffix('.md')}")


if __name__ == "__main__":
    sys.exit(main())
