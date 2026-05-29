#!/usr/bin/env python3
"""EXPERIMENT — how much defect signal is in the continuous coverage gradient?

The two binary coverage biomarkers (untested_hotspot / coverage_gap) barely fire
on these 91-98%-covered repos, so the file score is nearly blind to coverage.
But coverage is one of the strongest defect predictors in the literature. This
probe measures the marginal AUC of feeding the **continuous uncovered fraction**
(1 - line_coverage_pct/100) into the calibration, over the repos for which a
T0-anchored coverage report was acquired (``coverage_t0.json``).

Why a separate script: the latest benchmark re-index re-scored WITHOUT
``--coverage``, so the cached ``joined_data.json`` rows carry no coverage. The
coverage_t0.json artifacts still exist, and coverage is a per-file attribute
*join*, not a re-walk — so we can re-attach it and measure the gradient with NO
re-index. (This reproduces the Phase-7 coverage result on the current cache.)

It compares a continuous feature matrix WITH vs WITHOUT the coverage columns
(LOO pooled out-of-fold AUC) and reports the uncovered-fraction coefficient.
Coverage-aware continuous scoring is non-linear / out of scope for the runtime
(plan §4) — this only quantifies whether the signal is worth a future
interpretable (e.g. monotonic) coverage term.

Run (venv python):
    ../../.venv/Scripts/python.exe coverage_gradient_experiment.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

import error_analysis as ea

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"

BIOMARKERS = sorted(ea.SCORING_BIOMARKERS)
# continuous magnitude key per biomarker (mirrors calibrate_health_weights)
_CONT = {
    "brain_method": "ccn", "bumpy_road": "bumps", "change_entropy": "change_entropy",
    "churn_risk": "relative_churn", "co_change_scatter": "scatter",
    "complex_conditional": "operator_count", "complex_method": "cognitive",
    "dry_violation": "duplication_pct", "function_hotspot": "modification_count",
    "god_class": "method_count", "large_assertion_block": "assertion_count",
    "large_method": "nloc", "low_cohesion": "lcom4", "nested_complexity": "max_nesting",
    "ownership_risk": "minor_contributors", "primitive_obsession": "param_count",
    "prior_defect": "prior_defect_count",
}


def inject_coverage(repo: str, rows: list[dict]) -> int:
    cov_p = RESULTS / f"health_defect_{repo}" / "coverage_t0.json"
    if not cov_p.exists():
        return 0
    cov = json.loads(cov_p.read_text()).get("files", {})
    cov = {ea._norm(k): v for k, v in cov.items()}
    n = 0
    for r in rows:
        c = cov.get(ea._norm(r["file_path"]))
        if c and c.get("line_coverage_pct") is not None:
            r["line_coverage_pct"] = float(c["line_coverage_pct"])
            n += 1
    return n


def magnitudes(repo: str) -> dict[str, dict[str, float]]:
    """file -> {biomarker -> max continuous magnitude} from cached findings."""
    h = json.loads((RESULTS / f"health_defect_{repo}" / "health_scores.json").read_text())
    out: dict[str, dict[str, float]] = {}
    for f in h.get("findings", []):
        bt = f.get("biomarker_type")
        key = _CONT.get(bt)
        if not key:
            continue
        try:
            v = float((f.get("details") or {}).get(key))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        fp = ea._norm(f.get("file_path", ""))
        out.setdefault(fp, {})[bt] = max(out.setdefault(fp, {}).get(bt, 0.0), v)
    return out


def build(rows_by_repo, mags_by_repo, *, with_cov: bool):
    X, y, g = [], [], []
    names = [*BIOMARKERS, "nloc_log", "max_ccn_log", "max_nesting"]
    if with_cov:
        names += ["uncovered_frac", "coverage_known"]
    for repo, rows in rows_by_repo.items():
        mg = mags_by_repo[repo]
        for r in rows:
            fp = ea._norm(r["file_path"])
            row = []
            for bm in BIOMARKERS:
                if bm in _CONT and fp in mg and bm in mg[fp]:
                    row.append(float(np.log1p(mg[fp][bm])))
                else:
                    row.append(float(r["biomarkers"].get(bm, 0.0)))
            row.append(float(np.log1p(max(r["nloc"], 0))))
            row.append(0.0)  # max_ccn_log unavailable in build_rows → 0 (constant, harmless)
            row.append(0.0)  # max_nesting likewise
            if with_cov:
                cov = r.get("line_coverage_pct")
                if cov is None:
                    row += [0.0, 0.0]
                else:
                    row += [max(0.0, (100.0 - float(cov)) / 100.0), 1.0]
            X.append(row)
            y.append(r["y"])
            g.append(repo)
    return np.asarray(X, float), np.asarray(y, int), np.asarray(g), names


def oof_auc(X, y, g, C=0.5):
    logo = LeaveOneGroupOut()
    oy, op = [], []
    for tr, te in logo.split(X, y, g):
        if len(set(y[tr])) < 2:
            continue
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=5000).fit(sc.transform(X[tr]), y[tr])
        oy.extend(int(v) for v in y[te])
        op.extend(float(v) for v in clf.predict_proba(sc.transform(X[te]))[:, 1])
    return roc_auc_score(oy, op) if len(set(oy)) > 1 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--out", type=Path, default=RESULTS / "coverage_gradient_experiment.json")
    args = ap.parse_args()

    langs, roots = ea.load_config_langs(HERE / "config.yaml")
    rows_by_repo, mags_by_repo, covered = {}, {}, []
    for repo, lang in langs.items():
        rows = ea.build_rows(RESULTS, {repo: lang}, roots, label=args.label)
        if not rows:
            continue
        n = inject_coverage(repo, rows)
        if n == 0:
            continue  # only repos with a coverage artifact enter this probe
        covered.append((repo, n, len(rows)))
        rows_by_repo[repo] = rows
        mags_by_repo[repo] = magnitudes(repo)

    print(f"\n=== continuous coverage-gradient probe (label={args.label}) ===")
    print("covered repos (files with coverage / total):")
    for repo, n, tot in covered:
        print(f"  {repo:12s} {n:4d}/{tot:4d}")
    nfiles = sum(len(r) for r in rows_by_repo.values())
    npos = sum(rr["y"] for rows in rows_by_repo.values() for rr in rows)
    print(f"  subset: {len(rows_by_repo)} repos, {nfiles} files, {npos} positives")

    best = {}
    for with_cov in (False, True):
        Xx, yy, gg, names = build(rows_by_repo, mags_by_repo, with_cov=with_cov)
        a = max((oof_auc(Xx, yy, gg, C) or 0) for C in (0.1, 0.25, 0.5, 1.0))
        best[with_cov] = a
        # uncovered_frac coefficient at C=0.5 (final fit on all)
        coef = None
        if with_cov:
            sc = StandardScaler().fit(Xx)
            clf = LogisticRegression(C=0.5, class_weight="balanced", max_iter=5000).fit(sc.transform(Xx), yy)
            coef = float(clf.coef_[0][names.index("uncovered_frac")])
        tag = "WITH coverage" if with_cov else "WITHOUT coverage"
        extra = f"  uncovered_frac coef={coef:+.3f}" if coef is not None else ""
        print(f"  continuous {tag:16s} pooled OOF AUC = {a:.4f}{extra}")

    delta = best[True] - best[False]
    print(f"\n  coverage marginal ΔAUC = {delta:+.4f}")
    out = {"label": args.label, "covered_repos": [c[0] for c in covered],
           "n_files": nfiles, "n_positives": npos,
           "oof_auc_without_coverage": best[False], "oof_auc_with_coverage": best[True],
           "coverage_marginal_delta_auc": delta}
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
