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

from lib.charts import generate_all_charts
from lib.defect_counter import (
    count_defects_gitmoji,
    count_defects_keyword,
    count_defects_prefix,
    resolve_t0_sha,
)
from lib.filters import normalize_path, should_include
from lib.health_runner import run_health
from lib.stats import analyze_all

_BENCH_DIR = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_repo_dir(repo_config: dict, repos_dir: Path) -> Path:
    return repos_dir / repo_config["name"]


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


def join_and_filter(
    health_data: dict,
    defect_counts: dict[str, int],
    *,
    min_nloc: int = 10,
    exclude_tests: bool = True,
) -> list[dict]:
    joined = []
    for metric in health_data.get("metrics", []):
        fp = normalize_path(metric["file_path"])
        nloc = metric.get("nloc", 0)

        if exclude_tests and not should_include(fp, nloc, min_nloc=min_nloc):
            continue

        joined.append({
            "file_path": fp,
            "health_score": metric["score"],
            "max_ccn": metric.get("max_ccn", 0),
            "max_nesting": metric.get("max_nesting", 0),
            "nloc": nloc,
            "has_test_file": metric.get("has_test_file", False),
            "defect_count": defect_counts.get(fp, 0),
        })

    return joined


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

    print()


def run_one_repo(
    repo_config: dict,
    config: dict,
    repos_dir: Path,
    results_dir: Path,
    skip_health: bool,
    do_clone: bool,
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

    # Phase 1: Health scores
    health_path = out_dir / "health_scores.json"
    if skip_health and health_path.exists():
        print("  Using existing health scores...")
        health_data = json.loads(health_path.read_text())
    else:
        print("  Running repowise health...")
        health_data = run_health(str(repo_dir))
        health_path.write_text(json.dumps(health_data, indent=2))
        print(f"  -> {len(health_data.get('metrics', []))} files scored")

    # Phase 2: Defect counting
    print("  Counting bug-fixing commits...")
    t0_sha = resolve_t0_sha(str(repo_dir), repo_config["t0_date"])
    print(f"  T0 commit: {t0_sha[:12]} ({repo_config['t0_date']})")

    strategy = repo_config["defect_strategy"]
    if strategy == "gitmoji":
        defect_counts = count_defects_gitmoji(
            str(repo_dir), t0_sha, "HEAD",
            source_root=source_root,
            emoji=repo_config.get("gitmoji_bug", "\U0001F41B"),
        )
    elif strategy == "prefix":
        defect_counts = count_defects_prefix(
            str(repo_dir), t0_sha, "HEAD",
            source_root=source_root,
            prefix=repo_config.get("bug_prefix", "Fixed #"),
        )
    else:
        defect_counts = count_defects_keyword(
            str(repo_dir), t0_sha, "HEAD",
            source_root=source_root,
            include=repo_config.get("bug_keywords"),
            exclude=repo_config.get("exclude_keywords"),
        )

    defects_path = out_dir / "defect_counts.json"
    defects_path.write_text(json.dumps(defect_counts, indent=2))
    total_bugs = sum(defect_counts.values())
    print(f"  -> {len(defect_counts)} files with bugs, {total_bugs} total bug-fix touches")

    # Phase 3: Join, filter, analyze
    print("  Running statistical analysis...")
    joined = join_and_filter(
        health_data, defect_counts,
        min_nloc=config["defaults"]["min_nloc"],
        exclude_tests=config["defaults"]["exclude_test_files"],
    )
    print(f"  -> {len(joined)} files after filtering")

    findings = health_data.get("findings", [])
    correlation = analyze_all(joined, findings, config["defaults"])
    correlation["_findings"] = findings

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
        run_one_repo(
            repo_config, config, repos_dir, results_dir,
            skip_health=args.skip_health, do_clone=args.clone,
        )


if __name__ == "__main__":
    main()
