#!/usr/bin/env python3
"""Re-score the cached T0 benchmark with the CURRENT scoring weights — no re-index.

Biomarker findings don't depend on the weight multipliers (only on gates), so
after a weight recalibration we can recompute each file's health score from the
cached findings via the real ``scoring.score_file`` and re-run the benchmark
analysis. This validates shipped weights in seconds instead of a ~35-min
re-index. Prints before (cached correlation.json) → after (recomputed) per repo
plus a corpus roll-up.

Run with the venv python and repowise on PYTHONPATH:
    PYTHONPATH=packages/core/src .venv/Scripts/python.exe local-stash/rescore_benchmark.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import os as _os
BENCH = Path(__file__).resolve().parent   # health-defect/
RESULTS = BENCH.parent / "results"        # bench-agent/results/
sys.path.insert(0, str(BENCH))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(BENCH.parents[1])))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / p
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

import yaml  # noqa: E402

from lib.stats import ALL_BIOMARKERS, analyze_all  # noqa: E402
from run_benchmark import join_and_filter  # noqa: E402

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402
from repowise.core.analysis.health.scoring import score_file  # noqa: E402

_REAL = set(ALL_BIOMARKERS)  # exclude governance biomarkers from the score pass
_SEV = {
    "low": Severity.LOW, "medium": Severity.MEDIUM,
    "high": Severity.HIGH, "critical": Severity.CRITICAL,
}


def _sev(s) -> Severity:
    return _SEV.get(str(s).strip().lower(), Severity.MEDIUM)


def rescore(health: dict) -> dict:
    """Return a copy of *health* with each metric's score recomputed from its
    findings under the current scoring weights."""
    by_file: dict[str, list[BiomarkerResult]] = {}
    for f in health.get("findings", []):
        bt = f.get("biomarker_type")
        if bt not in _REAL:
            continue
        by_file.setdefault(f["file_path"], []).append(
            BiomarkerResult(
                biomarker_type=bt, severity=_sev(f.get("severity")),
                function_name=None, line_start=None, line_end=None,
                details={}, reason="",
            )
        )
    new_metrics = []
    for m in health.get("metrics", []):
        results = by_file.get(m["file_path"], [])
        score = score_file(results)[0] if results else 10.0
        nm = dict(m)
        nm["score"] = score
        new_metrics.append(nm)
    return {**health, "metrics": new_metrics}


def main() -> None:
    cfg = yaml.safe_load((BENCH / "config.yaml").read_text())
    defaults = cfg["defaults"]
    rows = []
    pooled_before, pooled_after = [], []
    for r in cfg["repos"]:
        name = r["name"]
        d = RESULTS / f"health_defect_{name}"
        hp, dp = d / "health_scores.json", d / "defect_counts.json"
        cp = d / "correlation.json"
        if not (hp.exists() and dp.exists()):
            continue
        health = json.loads(hp.read_text())
        defects = json.loads(dp.read_text())
        before = json.loads(cp.read_text()) if cp.exists() else None

        rescored = rescore(health)
        joined = join_and_filter(
            rescored, defects,
            min_nloc=defaults["min_nloc"],
            exclude_tests=defaults["exclude_test_files"],
            exclude_patterns=list(r.get("exclude") or []),
        )
        corr = analyze_all(joined, rescored.get("findings", []), defaults)

        b_auc = before["roc_auc"]["auc"] if before else float("nan")
        b_popt = (before.get("popt") or {}).get("popt") if before else None
        b_rho = before["spearman"]["rho"] if before else float("nan")
        b_prho = before["partial_spearman_nloc"] if before else float("nan")
        a_auc = corr["roc_auc"]["auc"]
        a_popt = (corr.get("popt") or {}).get("popt")
        a_rho = corr["spearman"]["rho"]
        a_prho = corr["partial_spearman_nloc"]
        rows.append((name, len(joined), b_rho, a_rho, b_auc, a_auc, b_popt, a_popt, b_prho, a_prho))

    print(f"{'repo':12s} {'n':>4s}  {'rho(b→a)':>16s}  {'partialρ(b→a)':>16s}  {'AUC(b→a)':>16s}  {'Popt(b→a)':>16s}")
    for name, n, br, ar, ba, aa, bp, ap, bpr, apr in rows:
        bp_s = f"{bp:.3f}" if bp is not None else " n/a"
        ap_s = f"{ap:.3f}" if ap is not None else " n/a"
        print(f"{name:12s} {n:>4d}  {br:>7.3f}→{ar:>7.3f}  {bpr:>7.3f}→{apr:>7.3f}  "
              f"{ba:>7.3f}→{aa:>7.3f}  {bp_s:>7s}→{ap_s:>7s}")

    # Macro-average across repos (unweighted) for a quick headline.
    def _avg(idx):
        vals = [r[idx] for r in rows if r[idx] is not None and r[idx] == r[idx]]
        return sum(vals) / len(vals) if vals else float("nan")
    print("-" * 96)
    print(f"{'MEAN':12s} {'':>4s}  {_avg(2):>7.3f}→{_avg(3):>7.3f}  {_avg(8):>7.3f}→{_avg(9):>7.3f}  "
          f"{_avg(4):>7.3f}→{_avg(5):>7.3f}  {_avg(6):>7.3f}→{_avg(7):>7.3f}")


if __name__ == "__main__":
    main()
