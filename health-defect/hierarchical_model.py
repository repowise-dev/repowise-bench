#!/usr/bin/env python3
"""Hierarchical & size-stratified calibration analysis (Phase-9 Part B).

A flat logistic regression conflates two questions: "is *this repo* buggy?" and
"is *this file* risky?". This script separates them with a mixed-effects logistic
(repo / language random intercepts) and quantifies how much variance lives at
the repo and language level — a reportable result in itself, and the honest
answer to "do the weights generalize across repos/languages?".

It also tests the size confound that ``error_analysis.py`` exposed (the score's
within-NLOC-band AUC collapses to ~0.49 on the 29-68 LOC band): does
**within-size-band standardization** of the biomarker features beat a single
global log-NLOC control? If band-relative features rank better — especially in
the small-file bands — that is the evidence for a size-stratified gate.

Read-only over the cached benchmark artifacts; no re-index. Fixed-effect
coefficients are the generalizable, shippable signal; random effects are
diagnostic only (never shipped — the runtime has no repo identity).

Usage (venv python — needs numpy/pandas/statsmodels/scikit-learn):
    ../../.venv/Scripts/python.exe hierarchical_model.py \
        [--results-dir ../results] [--config config.yaml] [--label keyword] \
        [--out ../results/hierarchical_model.json]
"""
from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

import error_analysis as ea  # reuse loaders / AUC / banding

# Same calibration roster + severity weighting as calibrate_health_weights.py.
BIOMARKERS = [
    "brain_method", "low_cohesion", "god_class", "nested_complexity",
    "complex_method", "bumpy_road", "complex_conditional", "large_method",
    "primitive_obsession", "dry_violation", "untested_hotspot", "coverage_gap",
    "developer_congestion", "knowledge_loss", "hidden_coupling", "function_hotspot",
    "code_age_volatility", "ownership_risk", "churn_risk", "change_entropy",
    "co_change_scatter", "prior_defect",
    "large_assertion_block", "duplicated_assertion_block",
]


def build_matrix(rows):
    """X = severity-weighted hit per biomarker + log-NLOC control; y, repo, lang."""
    X, y, repo, lang, nloc = [], [], [], [], []
    for r in rows:
        feat = [r["biomarkers"].get(bm, 0.0) for bm in BIOMARKERS]
        feat.append(float(np.log1p(max(r["nloc"], 0))))
        X.append(feat)
        y.append(r["y"])
        repo.append(r["repo"])
        lang.append(r["language"])
        nloc.append(r["nloc"])
    return (np.asarray(X, float), np.asarray(y, int),
            np.asarray(repo), np.asarray(lang), np.asarray(nloc),
            [*BIOMARKERS, "nloc_log"])


def pooled_oof_auc(X, y, groups, C=0.5, band=None, bands=None):
    """Leave-one-repo-out pooled out-of-fold AUC. If ``band`` given, AUC is taken
    only over held-out files in that NLOC band (size-regime generalization)."""
    logo = LeaveOneGroupOut()
    oy, op, ob = [], [], []
    for tr, te in logo.split(X, y, groups):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
        clf.fit(sc.transform(X[tr]), y[tr])
        p = clf.predict_proba(sc.transform(X[te]))[:, 1]
        oy.extend(int(v) for v in y[te])
        op.extend(float(v) for v in p)
        if bands is not None:
            ob.extend(bands[te])
    if band is not None and bands is not None:
        oy2 = [a for a, b in zip(oy, ob) if b == band]
        op2 = [a for a, b in zip(op, ob) if b == band]
        if len(set(oy2)) < 2:
            return None
        return roc_auc_score(oy2, op2)
    return roc_auc_score(oy, op) if len(set(oy)) > 1 else None


def band_standardize(X, nloc, cuts, feature_names):
    """Standardize every feature *within* its file's NLOC quartile (z-score per
    band), dropping the global nloc_log control. A biomarker hit then means
    'unusually high *for a file this size*', neutralizing the 'big files fire
    more biomarkers' confound the global control only partially removes."""
    bands = np.array([ea.band_of(int(n), cuts) for n in nloc])
    Xb = X[:, :-1].copy()  # drop nloc_log (last col)
    for b in set(bands):
        idx = bands == b
        for j in range(Xb.shape[1]):
            col = Xb[idx, j]
            mu, sd = col.mean(), col.std()
            Xb[idx, j] = (col - mu) / sd if sd > 1e-9 else 0.0
    return Xb, feature_names[:-1], bands


def fit_glmm(rows, group_key, feature_names):
    """BinomialBayesMixedGLM with a random intercept per group (repo/language).
    Returns fixed-effect coefs + the random-intercept SD (variance the flat model
    ignores). Features standardized so coefs are comparable."""
    import pandas as pd
    from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM

    X, y, repo, lang, nloc, names = build_matrix(rows)
    Xs = StandardScaler().fit_transform(X)
    data = {n.replace("nloc_log", "nlocLog"): Xs[:, i] for i, n in enumerate(names)}
    cols = list(data.keys())
    data["y"] = y
    data["grp"] = repo if group_key == "repo" else lang
    df = pd.DataFrame(data)
    formula = "y ~ " + " + ".join(cols)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = BinomialBayesMixedGLM.from_formula(
            formula, {"grp": "0 + C(grp)"}, df)
        res = model.fit_vb()
    # Fixed effects: first len(cols)+1 params (intercept + fixed slopes).
    fe = {}
    for i, name in enumerate(["Intercept", *cols]):
        fe[name] = float(res.fe_mean[i])
    # Random-effect SD: vcp_mean is log-SD of the random intercept distribution.
    re_sd = float(np.exp(res.vcp_mean[0]))
    return fe, re_sd, group_key


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=here.parent / "results")
    ap.add_argument("--config", type=Path, default=here / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--out", type=Path, default=here.parent / "results" / "hierarchical_model.json")
    ap.add_argument("--C", type=float, default=0.5)
    args = ap.parse_args()

    langs, roots = ea.load_config_langs(args.config)
    rows = ea.build_rows(args.results_dir, langs, roots, label=args.label)
    X, y, repo, lang, nloc, names = build_matrix(rows)
    cuts = ea.nloc_quartiles(rows)
    band_labels = [f"Q1 (<= {cuts[0]:.0f})", f"Q2 (<= {cuts[1]:.0f})",
                   f"Q3 (<= {cuts[2]:.0f})", f"Q4 (> {cuts[2]:.0f})"]
    bands = np.array([ea.band_of(int(n), cuts) for n in nloc])

    print(f"\n=== Hierarchical / size-stratified analysis "
          f"({len(langs)} repos, {len(y)} files, {int(y.sum())} pos, label={args.label}) ===")

    # ---- Part B.1: mixed-effects logistic (repo + language random intercepts) --
    glmm_out = {}
    for gk in ("repo", "language"):
        try:
            fe, re_sd, _ = fit_glmm(rows, gk, names)
            glmm_out[gk] = {"fixed_effects": fe, "random_intercept_sd": re_sd}
            print(f"\n--- GLMM with {gk} random intercept ---")
            print(f"  random-intercept SD = {re_sd:.3f}  "
                  f"(odds-scale spread of '{gk} baseline bugginess')")
            top = sorted(fe.items(), key=lambda kv: -abs(kv[1]))
            print("  top fixed effects (standardized log-odds):")
            for nm, c in top[:12]:
                print(f"    {nm:24s} {c:+.3f}")
        except Exception as e:  # noqa: BLE001
            print(f"  GLMM ({gk}) failed: {e}")
            glmm_out[gk] = {"error": str(e)}

    # ---- Part B.2: size-stratified vs global-NLOC-control ----------------------
    print("\n--- Size-stratified feature normalization vs global NLOC control ---")
    global_overall = pooled_oof_auc(X, y, repo, C=args.C)
    Xb, _, _ = band_standardize(X, nloc, cuts, names)
    band_overall = pooled_oof_auc(Xb, y, repo, C=args.C)
    print(f"  global-NLOC-control  pooled OOF AUC = {global_overall:.4f}")
    print(f"  within-band z-scored pooled OOF AUC = {band_overall:.4f}  "
          f"(Δ {band_overall - global_overall:+.4f})")

    print("\n  per-band pooled OOF AUC (held-out files, by model):")
    print(f"  {'band':18s} {'global-ctrl':>11s} {'band-zscore':>11s} {'Δ':>7s}")
    per_band = {}
    for b in band_labels:
        g = pooled_oof_auc(X, y, repo, C=args.C, band=b, bands=bands)
        z = pooled_oof_auc(Xb, y, repo, C=args.C, band=b, bands=bands)
        d = (z - g) if (g is not None and z is not None) else None
        per_band[b] = {"global": g, "band_zscore": z, "delta": d}
        print(f"  {b:18s} {('%.3f'%g) if g else 'n/a':>11s} "
              f"{('%.3f'%z) if z else 'n/a':>11s} "
              f"{('%+.3f'%d) if d is not None else 'n/a':>7s}")

    out = {
        "corpus": {"repos": len(langs), "files": int(len(y)), "positives": int(y.sum())},
        "label": args.label,
        "glmm": glmm_out,
        "size_stratified": {
            "global_control_oof_auc": global_overall,
            "band_zscore_oof_auc": band_overall,
            "per_band": per_band,
            "nloc_quartile_cuts": cuts,
        },
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
