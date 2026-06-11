#!/usr/bin/env python3
"""Validate the SHIPPED coverage_gradient biomarker reproduces the measured win.

Unlike coverage_scoring_experiment.py (which applies an abstract penalty), this
drives the *actual product code path*: for each covered file it builds a
FileContext, runs ``CoverageGradientDetector.detect`` + ``score_file``, and adds
the resulting (category-capped, weighted) deduction to the cached shipped risk.
If the product reproduces the experiment's +0.043 corpus AUC within its CI, the
biomarker as implemented ships the measured lift — not just the prototype math.

Run from the bench's health-defect dir so error_analysis/lib import:
    cd repowise-bench/health-defect
    ../../.venv/Scripts/python.exe ../../local-stash/validate_coverage_gradient.py
"""
from __future__ import annotations

import json
import os as _os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(Path(__file__).resolve().parents[3])))
for _pkg in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / _pkg
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

import numpy as np

import error_analysis as ea  # type: ignore
from lib.stats import popt, roc_auc  # type: ignore

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.coverage_gradient import (
    CoverageGradientDetector,
)
from repowise.core.analysis.health.scoring import score_file

HERE = Path(__file__).resolve().parents[1]
RESULTS = HERE.parent / "results"
_DET = CoverageGradientDetector()


def inject_coverage(repo: str, rows: list[dict]) -> int:
    cov_p = RESULTS / f"health_defect_{repo}" / "coverage_t0.json"
    if not cov_p.exists():
        return 0
    cov = {ea._norm(k): v for k, v in json.loads(cov_p.read_text()).get("files", {}).items()}
    n = 0
    for r in rows:
        c = cov.get(ea._norm(r["file_path"]))
        if c and c.get("line_coverage_pct") is not None:
            r["line_coverage_pct"] = float(c["line_coverage_pct"])
            n += 1
    return n


def product_deduction(row: dict) -> float:
    """The deduction the SHIPPED biomarker + scorer apply for this file."""
    cov = row.get("line_coverage_pct")
    if cov is None:
        return 0.0
    ctx = FileContext(
        file_path=row["file_path"],
        language="python",
        nloc=int(row.get("nloc", 0)),
        has_test_file=False,
        module=None,
        line_coverage_pct=cov,
    )
    findings = _DET.detect(ctx)
    if not findings:
        return 0.0
    _, deductions = score_file(findings)
    return sum(deductions)


def _shim(rows, risk):
    return [{"defect_count": d["defect_count"], "nloc": d["nloc"], "health_score": 10.0 - r}
            for d, r in zip(rows, risk)]


def main() -> None:
    langs, roots = ea.load_config_langs(HERE / "config.yaml")
    by_repo = {}
    for repo, lang in langs.items():
        rows = ea.build_rows(RESULTS, {repo: lang}, roots, label="keyword")
        if rows and inject_coverage(repo, rows) > 0:
            by_repo[repo] = rows

    base_rbr, prod_rbr = {}, {}
    for repo, rows in by_repo.items():
        base_rbr[repo] = [r["risk"] for r in rows]
        prod_rbr[repo] = [min(10.0, r["risk"] + product_deduction(r)) for r in rows]

    def corpus_auc(rbr):
        s = []
        for repo, rows in by_repo.items():
            s += _shim(rows, rbr[repo])
        return roc_auc(s)["auc"]

    nfiles = sum(len(r) for r in by_repo.values())
    npos = sum(rr["y"] for rows in by_repo.values() for rr in rows)
    base_corp, prod_corp = corpus_auc(base_rbr), corpus_auc(prod_rbr)
    print(f"\n=== PRODUCT coverage_gradient validation (covered-{len(by_repo)}) ===")
    print(f"files={nfiles} positives={npos}")
    print(f"baseline corpus AUC = {base_corp:.4f}")
    print(f"product  corpus AUC = {prod_corp:.4f}")
    print(f"Δ corpus AUC        = {prod_corp - base_corp:+.4f}")

    # bootstrap CI of corpus ΔAUC + per-repo-mean ΔPopt (mirrors the experiment).
    rng = random.Random(11)
    dauc, dpopt = [], []
    for _ in range(500):
        sa, sb, pa, pb = [], [], [], []
        for repo, rows in by_repo.items():
            n = len(rows)
            idx = [rng.randrange(n) for _ in range(n)]
            sub = [rows[i] for i in idx]
            ra = [base_rbr[repo][i] for i in idx]
            rb = [prod_rbr[repo][i] for i in idx]
            sa += _shim(sub, ra)
            sb += _shim(sub, rb)
            va = (popt(_shim(sub, ra)) or {}).get("popt")
            vb = (popt(_shim(sub, rb)) or {}).get("popt")
            if va is not None and vb is not None:
                pa.append(va)
                pb.append(vb)
        try:
            dauc.append(roc_auc(sb)["auc"] - roc_auc(sa)["auc"])
        except Exception:
            pass
        if pa:
            dpopt.append(float(np.mean(pb)) - float(np.mean(pa)))

    def ci(xs):
        xs = sorted(xs)
        return [round(float(np.mean(xs)), 4), round(xs[int(0.025 * len(xs))], 4),
                round(xs[int(0.975 * len(xs))], 4)] if xs else [None, None, None]

    ca, cp = ci(dauc), ci(dpopt)
    print(f"\nbootstrap (500x):")
    print(f"  Δcorpus AUC {ca[0]:+.4f} [{ca[1]:+.4f},{ca[2]:+.4f}]")
    print(f"  Δmean Popt  {cp[0]:+.4f} [{cp[1]:+.4f},{cp[2]:+.4f}]")
    print(f"  (experiment target: +0.0428 [+0.0231,+0.0614])")


if __name__ == "__main__":
    main()
