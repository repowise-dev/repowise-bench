#!/usr/bin/env python3
"""
Health Score vs. Defect Prediction Benchmark

Proves that Repowise's deterministic health scores predict real-world defects.
Methodology: score files at T0, count bug-fixing commits T0→T1, correlate.

Usage:
    python run_benchmark.py                                    # all repos
    python run_benchmark.py --repo fastapi                     # one repo
    python run_benchmark.py --repo fastapi --skip-health       # reuse scores
    python run_benchmark.py --clone                            # clone missing repos first
    python run_benchmark.py --repos-dir /path/to/repos         # custom repo location
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from lib.baselines import all_baselines, attach_baseline_features
from lib.charts import generate_all_charts
from lib.defect_counter import (
    _attribute,
    find_fix_commits,
    resolve_t0_sha,
)
from lib.filters import normalize_path, should_include
from lib.health_runner import run_health, run_health_at_commit
from lib.issue_links import confirmed_fixes, owner_repo_from_url
from lib.stats import analyze_all, auc_metric, bootstrap_ci, popt_metric
from lib.szz import compute_szz, label_repo, labels_for_variant

_LABEL_STRATEGIES = ("keyword", "szz", "issue", "szz+issue")

_BENCH_DIR = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_repo_dir(repo_config: dict, repos_dir: Path) -> Path:
    base = repos_dir / repo_config["name"]
    nested = base / repo_config["name"]
    if nested.exists() and (nested / ".git").exists():
        return nested
    return base


def clone_repo(repo_config: dict, repos_dir: Path) -> Path:
    repo_dir = resolve_repo_dir(repo_config, repos_dir)
    if repo_dir.exists():
        return repo_dir
    url = repo_config.get("repo_url")
    if not url:
        raise ValueError(f"No repo_url for {repo_config['name']} and directory does not exist")
    print(f"  Cloning {url} -> {repo_dir} ...")
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth=5000", url, str(repo_dir)],
        check=True,
    )
    return repo_dir


def _make_exclude_matcher(patterns: list[str]):
    """Build a predicate that matches the repo's index-time exclude patterns.

    `repowise health` re-walks the working tree and does NOT honor the `-x`
    patterns passed to `init` (health_cmd builds a FileTraverser with no
    excludes), so excluded dirs (docs/website/examples/monorepo-siblings)
    reappear in the scored metrics. We replicate the exclusion here so the
    evaluated universe matches the intended source set + the defect source_root.
    """
    if not patterns:
        return lambda _p: False
    import pathspec
    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return lambda p: spec.match_file(p)


def join_and_filter(
    health_data: dict,
    defect_counts: dict[str, int],
    *,
    min_nloc: int = 10,
    exclude_tests: bool = True,
    exclude_patterns: list[str] | None = None,
) -> list[dict]:
    is_excluded = _make_exclude_matcher(exclude_patterns or [])
    joined = []
    for metric in health_data.get("metrics", []):
        fp = normalize_path(metric["file_path"])
        nloc = metric.get("nloc", 0)

        if is_excluded(fp):
            continue
        if exclude_tests and not should_include(fp, nloc, min_nloc=min_nloc):
            continue

        joined.append({
            "file_path": fp,
            "health_score": metric["score"],
            "max_ccn": metric.get("max_ccn", 0),
            "max_nesting": metric.get("max_nesting", 0),
            "nloc": nloc,
            "has_test_file": metric.get("has_test_file", False),
            # Continuous coverage signal (None when no coverage artifact was
            # ingested — absent, NOT zero, so calibration can distinguish them).
            "line_coverage_pct": metric.get("line_coverage_pct"),
            "branch_coverage_pct": metric.get("branch_coverage_pct"),
            "duplication_pct": metric.get("duplication_pct"),
            "defect_count": defect_counts.get(fp, 0),
        })

    return joined


def _resolve_coverage_path(repo_config: dict, out_dir: Path) -> str | None:
    """Locate a coverage artifact for this repo, if one was collected.

    Priority: an explicit ``coverage:`` path in config.yaml, else the cached
    normalized artifact ``results/<repo>/coverage_t0.json`` written by
    ``local-stash/collect_coverage.py``. Returns ``None`` when neither exists —
    the run then proceeds coverage-blind (has_test_file fallback), exactly as
    before Phase 7.
    """
    explicit = repo_config.get("coverage")
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = (_BENCH_DIR / explicit).resolve()
        return str(p) if p.exists() else None
    cached = out_dir / "coverage_t0.json"
    return str(cached) if cached.exists() else None


def compute_all_labels(
    repo_dir: str,
    t0_sha: str,
    repo_config: dict,
    out_dir: Path,
) -> tuple[dict[str, dict[str, int]], dict]:
    """Compute every available defect-label strategy for one repo.

    Returns ``(labels_by_strategy, meta)`` where each label maps
    ``file_path -> defect_count``:

      * ``keyword``   — fix-touch attribution of all fix commits (Phase-5 label).
      * ``szz``       — AG-SZZ bug-inducing attribution of all fix commits.
      * ``szz_b``     — B-SZZ (reported in the comparison, not a primary).
      * ``issue``     — fix-touch of the bug-issue-confirmed fix subset.
      * ``szz+issue`` — AG-SZZ over the confirmed fix subset (highest precision).

    The ``issue``/``szz+issue`` strategies are present only when ``gh`` resolved
    the repo's issues; otherwise callers degrade to ``szz``/``keyword``.
    """
    strategy = repo_config["defect_strategy"]
    source_root = repo_config["source_root"]
    extensions = tuple(repo_config.get("extensions", [".py"]))
    emoji = repo_config.get("gitmoji_bug", "\U0001F41B")
    prefix = repo_config.get("bug_prefix", "Fixed #")
    include = repo_config.get("bug_keywords")
    exclude = repo_config.get("exclude_keywords")

    fixes = find_fix_commits(
        repo_dir, t0_sha, "HEAD", strategy=strategy,
        emoji=emoji, prefix=prefix, include=include, exclude=exclude,
    )
    fix_sha_set = {s for s, _ in fixes}
    fix_shas = [s for s, _ in fixes]

    labels: dict[str, dict[str, int]] = {
        "keyword": _attribute(repo_dir, fix_shas, source_root, extensions),
    }

    szz_res = label_repo(
        repo_dir, t0_sha, fixes,
        source_root=source_root, extensions=extensions,
        cache_path=out_dir / "szz_labels.json", fix_sha_set=fix_sha_set,
    )
    labels["szz"] = labels_for_variant(szz_res, "ag")
    labels["szz_b"] = labels_for_variant(szz_res, "b")

    issue_meta: dict = {"available": False, "reason": "no github repo_url"}
    owner_repo = owner_repo_from_url(repo_config.get("repo_url", ""))
    if owner_repo:
        owner, repo = owner_repo
        confirmed, issue_meta = confirmed_fixes(
            fixes, owner, repo, out_dir / "issues",
            bug_labels=repo_config.get("bug_labels"),
        )
        if issue_meta.get("available"):
            labels["issue"] = _attribute(
                repo_dir, [s for s, _ in confirmed], source_root, extensions
            )
            szz_issue = compute_szz(
                repo_dir, t0_sha, confirmed,
                source_root=source_root, extensions=extensions,
                fix_sha_set=fix_sha_set,
            )
            labels["szz+issue"] = labels_for_variant(szz_issue, "ag")

    meta = {
        "n_fixes": len(fixes),
        "szz_stats": szz_res["stats"],
        "issue": issue_meta,
    }
    return labels, meta


def _resolve_active_strategy(requested: str, labels: dict[str, dict]) -> str:
    """Fall back gracefully when the requested strategy is unavailable
    (e.g. ``szz+issue`` requested but ``gh`` could not resolve issues)."""
    if requested in labels:
        return requested
    fallback = "szz" if requested in ("issue", "szz+issue") else "keyword"
    print(f"  !! label-strategy '{requested}' unavailable → falling back to '{fallback}'")
    return fallback


def build_label_comparison(
    health_data: dict,
    labels: dict[str, dict[str, int]],
    config: dict,
    exclude_patterns: list[str],
) -> dict:
    """Side-by-side headline metrics (AUC/Popt with bootstrap 95% CI) for every
    label strategy, over the identical filtered universe — so the label upgrade
    is auditable, not asserted."""
    out: dict[str, dict] = {}
    for name, counts in labels.items():
        joined = join_and_filter(
            health_data, counts,
            min_nloc=config["defaults"]["min_nloc"],
            exclude_tests=config["defaults"]["exclude_test_files"],
            exclude_patterns=exclude_patterns,
        )
        n_pos = sum(1 for d in joined if d["defect_count"] > 0)
        out[name] = {
            "n_files": len(joined),
            "n_positives": n_pos,
            "auc": bootstrap_ci(joined, auc_metric),
            "popt": bootstrap_ci(joined, popt_metric),
        }
    return out


def print_summary(repo_name: str, correlation: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  Results: {repo_name}")
    print(f"{'='*60}")

    desc = correlation["descriptive"]
    print(f"\n  Files analyzed: {desc['n_files']}")
    print(f"  Files with defects: {desc['n_with_defects']}")
    print(f"  Zero-defect files: {desc['n_zero_defects']} ({desc['pct_zero_defects']:.0f}%)")
    print(f"  Total bug-fix touches: {desc['defect_count']['total']}")

    sp = correlation["spearman"]
    print(f"\n  Spearman ρ: {sp['rho']:.4f}  (p={sp['p_value']:.4f})")

    ps = correlation["partial_spearman_nloc"]
    print(f"  Partial Spearman (ctrl NLOC): {ps:.4f}")

    dr = correlation["density_ratio"]
    print(f"\n  Defect density ratio (raw): {dr['raw_ratio']:.1f}x")
    print(f"  Defect density ratio (per KLOC): {dr['nloc_normalized_ratio']:.1f}x")
    print(f"    Low-health group: {dr['low_group']['count']} files, {dr['low_group']['defects_per_kloc']:.2f} defects/KLOC")
    print(f"    High-health group: {dr['high_group']['count']} files, {dr['high_group']['defects_per_kloc']:.2f} defects/KLOC")

    roc = correlation["roc_auc"]
    print(f"\n  ROC AUC: {roc['auc']:.3f}")

    pak = correlation["precision_at_k"]
    print(f"  Precision@{pak['k']}: {pak['precision']:.0%} ({pak['true_positives']}/{pak['k']} had bugs)")

    eff = correlation.get("effort_at_20pct_loc")
    if eff:
        print(f"  Effort@20%LOC: precision {eff['precision']:.0%}, "
              f"recall(defects) {eff['recall_defects']:.0%}, "
              f"recall(files) {eff['recall_files']:.0%} "
              f"({eff['files_inspected']} files / {eff['loc_inspected']:.0f} LOC)")
    pt = correlation.get("popt") or {}
    if pt.get("popt") is not None:
        print(f"  Popt: {pt['popt']:.3f}  (0.5≈random, 1.0=optimal)")

    kw = correlation["kruskal_wallis"]
    if kw.get("h_stat") is not None:
        print(f"\n  Kruskal-Wallis H: {kw['h_stat']:.2f}  (p={kw['p_value']:.4f})")
    print(f"  Group sizes — Red: {kw['group_sizes']['red']}, Yellow: {kw['group_sizes']['yellow']}, Green: {kw['group_sizes']['green']}")

    print(f"\n  Top biomarker predictors:")
    for b in correlation["per_biomarker"][:5]:
        delta = b.get("cliffs_delta")
        p = b.get("p_value")
        if delta is not None:
            sig = "*" if p and p < 0.05 else ""
            print(f"    {b['biomarker']:25s}  δ={delta:+.3f}  p={p:.4f}{sig}  (n={b['files_with']})")
        else:
            print(f"    {b['biomarker']:25s}  (insufficient data, n={b['files_with']})")

    print(f"\n{'='*60}")

    buckets = correlation["density_by_bucket"]
    print(f"\n  Defects by health bucket:")
    print(f"  {'Bucket':<12} {'Files':>6} {'Defects':>8} {'Mean':>8} {'Per KLOC':>10}")
    for b in buckets:
        print(f"  {b['bucket']:<12} {b['file_count']:>6} {b['total_defects']:>8} {b['mean_defects']:>8.2f} {b['defects_per_kloc']:>10.2f}")

    comp = correlation.get("label_comparison")
    if comp:
        print(f"\n  Label-strategy comparison (active='{correlation.get('label_strategy')}'):")
        print(f"  {'strategy':<12} {'pos':>4} {'AUC [95% CI]':>26} {'Popt':>8}")
        for name in ("keyword", "szz_b", "szz", "issue", "szz+issue"):
            c = comp.get(name)
            if not c:
                continue
            a = c["auc"]
            ci = (f"{a['point']:.3f} [{a['lo']:.3f},{a['hi']:.3f}]"
                  if a.get("lo") is not None else f"{a['point']:.3f}")
            pt = c["popt"].get("point")
            pt_s = f"{pt:.3f}" if pt is not None else "  n/a"
            print(f"  {name:<12} {c['n_positives']:>4} {ci:>26} {pt_s:>8}")

    bl = correlation.get("baselines")
    if bl:
        print(f"\n  Baselines vs health (active label, AUC / Popt):")
        for name in ("health", "prior_defects", "churn_only", "loc_only", "random"):
            b = bl.get(name)
            if not b:
                continue
            pt = b.get("popt")
            pt_s = f"{pt:.3f}" if pt is not None else "n/a"
            print(f"    {name:<14} AUC {b['auc']:.3f}   Popt {pt_s}")

    print()


def run_one_repo(
    repo_config: dict,
    config: dict,
    repos_dir: Path,
    results_dir: Path,
    skip_health: bool,
    do_clone: bool,
    score_at: str = "t0",
    score_label_strategy: str = "szz+issue",
) -> None:
    name = repo_config["name"]
    source_root = repo_config["source_root"]

    print(f"\n{'='*60}")
    print(f"  Benchmarking: {name}")
    print(f"{'='*60}")

    repo_dir = resolve_repo_dir(repo_config, repos_dir)
    if do_clone and not repo_dir.exists():
        clone_repo(repo_config, repos_dir)
    if not repo_dir.exists():
        print(f"  SKIP: {repo_dir} does not exist (use --clone to auto-clone)")
        return

    out_dir = results_dir / f"health_defect_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # T0 is resolved first: in t0 mode it is also the commit we *score* at, so
    # the health measurement strictly precedes the defect-labeling window.
    t0_sha = resolve_t0_sha(str(repo_dir), repo_config["t0_date"])

    # Gitignore-style patterns to skip docs/website/example trees while
    # indexing — cuts index time and keeps the scored universe on source.
    # NOTE: do NOT exclude test dirs here; has_test_file / untested_hotspot
    # need test files present to pair source<->test.
    exclude_patterns = list(repo_config.get("exclude") or [])

    # Coverage artifact (normalized repowise-coverage-v1 JSON keyed by repo-rel
    # path). When present it feeds untested_hotspot/coverage_gap real line
    # coverage; when absent the run is coverage-blind (Phase-5 behavior).
    coverage_path = _resolve_coverage_path(repo_config, out_dir)
    if coverage_path:
        print(f"  Coverage artifact: {coverage_path}")

    # Phase 1: Health scores
    health_path = out_dir / "health_scores.json"
    if skip_health and health_path.exists():
        print("  Using existing health scores...")
        health_data = json.loads(health_path.read_text())
    elif score_at == "t0":
        print(f"  Scoring at T0 {t0_sha[:12]} ({repo_config['t0_date']}) "
              f"[worktree + index{', excludes=' + str(exclude_patterns) if exclude_patterns else ''}]"
              f"{', +coverage' if coverage_path else ''}...")
        health_data = run_health_at_commit(
            str(repo_dir), t0_sha, exclude_patterns=exclude_patterns,
            coverage_path=coverage_path,
        )
        health_data["_scored_at"] = {"mode": "t0", "sha": t0_sha}
        health_path.write_text(json.dumps(health_data, indent=2))
        print(f"  -> {len(health_data.get('metrics', []))} files scored at T0")
    else:
        print("  Running repowise health (at HEAD)...")
        health_data = run_health(str(repo_dir), coverage_path=coverage_path)
        health_data["_scored_at"] = {"mode": "head"}
        health_path.write_text(json.dumps(health_data, indent=2))
        print(f"  -> {len(health_data.get('metrics', []))} files scored")

    # Phase 2: Defect labeling — every available strategy (keyword / SZZ /
    # issue / szz+issue). The chosen one drives the joined dataset + calibration;
    # the rest populate an auditable side-by-side comparison.
    print("  Labeling defects (keyword / SZZ / issue)...")
    print(f"  T0 commit: {t0_sha[:12]} ({repo_config['t0_date']})")
    labels, label_meta = compute_all_labels(str(repo_dir), t0_sha, repo_config, out_dir)
    print(f"  -> {label_meta['n_fixes']} fix commits; "
          f"SZZ(AG) {label_meta['szz_stats']['n_defective_files_ag']} defective files; "
          f"issue-linkage {'on' if label_meta['issue'].get('available') else 'off'}"
          + (f" ({label_meta['issue'].get('n_confirmed', 0)} confirmed fixes)"
             if label_meta['issue'].get('available') else ""))

    active = _resolve_active_strategy(score_label_strategy, labels)
    defect_counts = labels[active]
    (out_dir / "defect_counts.json").write_text(json.dumps(defect_counts, indent=2))
    for name, counts in labels.items():
        (out_dir / f"defect_counts_{name.replace('+', '_')}.json").write_text(
            json.dumps(counts, indent=2)
        )
    total_bugs = sum(defect_counts.values())
    print(f"  -> active label '{active}': {len(defect_counts)} files, "
          f"{total_bugs} total touches")

    # Phase 3: Join, filter, analyze (on the active label)
    print("  Running statistical analysis...")
    joined = join_and_filter(
        health_data, defect_counts,
        min_nloc=config["defaults"]["min_nloc"],
        exclude_tests=config["defaults"]["exclude_test_files"],
        exclude_patterns=exclude_patterns,
    )
    print(f"  -> {len(joined)} files after filtering")

    findings = health_data.get("findings", [])
    correlation = analyze_all(joined, findings, config["defaults"])
    correlation["_findings"] = findings
    correlation["label_strategy"] = active
    correlation["label_meta"] = label_meta

    # Label-quality comparison + trivial baselines (the result is only
    # interesting if health beats LOC / churn / prior-defects).
    print("  Comparing label strategies + baselines...")
    correlation["label_comparison"] = build_label_comparison(
        health_data, labels, config, exclude_patterns
    )
    attach_baseline_features(
        joined, str(repo_dir), t0_sha, repo_config["t0_date"],
        source_root=source_root,
        extensions=tuple(repo_config.get("extensions", [".py"])),
        strategy=repo_config["defect_strategy"],
        emoji=repo_config.get("gitmoji_bug", "\U0001F41B"),
        prefix=repo_config.get("bug_prefix", "Fixed #"),
        include=repo_config.get("bug_keywords"),
        exclude=repo_config.get("exclude_keywords"),
    )
    correlation["baselines"] = all_baselines(joined)

    serializable = {k: v for k, v in correlation.items() if k != "_findings"}
    (out_dir / "correlation.json").write_text(json.dumps(serializable, indent=2))

    # Phase 4: Charts
    print("  Generating charts...")
    charts_dir = out_dir / "charts"
    generate_all_charts(joined, correlation, charts_dir)
    print(f"  -> Charts saved to {charts_dir}")

    # Phase 5: Summary
    print_summary(name, correlation)

    # Phase 6: Save joined data
    (out_dir / "joined_data.json").write_text(json.dumps(joined, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Health Score vs. Defect Prediction Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--repo", help="Run only this repo (by name in config)")
    parser.add_argument("--skip-health", action="store_true", help="Reuse existing health scores")
    parser.add_argument("--clone", action="store_true", help="Auto-clone missing repos")
    parser.add_argument(
        "--score-at", choices=["t0", "head"], default="t0",
        help="Score files at the T0 commit (default, fixes the HEAD-vs-T0 "
             "leakage of §7.2) or at HEAD (legacy/comparison).",
    )
    parser.add_argument(
        "--label-strategy", choices=list(_LABEL_STRATEGIES), default="szz+issue",
        help="Defect label that drives the joined dataset + calibration: "
             "keyword (fix-touch), szz (AG-SZZ bug-inducing), issue "
             "(bug-issue-confirmed fix-touch), or szz+issue (AG-SZZ over the "
             "confirmed subset, default). All strategies are always computed for "
             "the side-by-side comparison; this picks the primary.",
    )
    parser.add_argument("--config", type=Path, default=_BENCH_DIR / "config.yaml",
                        help="Path to config.yaml (default: ./config.yaml)")
    parser.add_argument("--repos-dir", type=Path, default=None,
                        help="Directory containing cloned repos (default: ../repos)")
    parser.add_argument("--results-dir", type=Path, default=None,
                        help="Directory for output (default: ../results)")
    args = parser.parse_args()

    config = load_config(args.config)

    repos_dir = args.repos_dir
    if repos_dir is None:
        candidate = _BENCH_DIR.parent / "repos"
        repos_dir = candidate if candidate.exists() else _BENCH_DIR / "repos"
    repos_dir = repos_dir.resolve()

    results_dir = args.results_dir
    if results_dir is None:
        candidate = _BENCH_DIR.parent / "results"
        results_dir = candidate if candidate.exists() else _BENCH_DIR / "results"
    results_dir = results_dir.resolve()

    print(f"  Repos dir:   {repos_dir}")
    print(f"  Results dir: {results_dir}")

    for repo_config in config["repos"]:
        if args.repo and repo_config["name"] != args.repo:
            continue
        try:
            run_one_repo(
                repo_config, config, repos_dir, results_dir,
                skip_health=args.skip_health, do_clone=args.clone,
                score_at=args.score_at, score_label_strategy=args.label_strategy,
            )
        except Exception as exc:  # noqa: BLE001 — one bad repo must not abort the batch
            import traceback
            print(f"\n  !! {repo_config['name']} FAILED: {exc}")
            traceback.print_exc()
            continue


if __name__ == "__main__":
    main()
