#!/usr/bin/env python3
"""Experiment: would LEARNING the per-category caps beat the static §3.1 caps?

The health score is  10 - Σ_c min(cap_c, raw_c)  where raw_c is the file's total
weighted deduction in category c. The caps are currently hand-set (§3.1). This
asks: if we instead FIT the 6 caps to maximize defect prediction, how much do we
gain — and does it generalize (leave-one-repo-out) or just overfit?

Two comparisons, both using the CURRENT biomarker weights (incl prior_defect=1.0)
so this isolates the *caps* question:
  * static caps  → pooled AUC + mean per-repo Popt (one number; no fitting).
  * learned caps → (a) in-sample ceiling (fit on all, optimistic) and
                   (b) honest LOO (fit caps on 12 repos, score the held-out one,
                   pool the out-of-fold predictions). (b) is the real verdict.

Optimizer: differential evolution maximizing pooled train AUC (rank metric →
gradient-free). Reported for keyword labels (szz available via --label).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import differential_evolution
from sklearn.metrics import roc_auc_score

import os as _os
BENCH = Path(__file__).resolve().parents[1]
RES = BENCH.parent / "results"
sys.path.insert(0, str(BENCH))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(BENCH.parents[1])))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / p
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

from lib.stats import ALL_BIOMARKERS, popt  # noqa: E402

from repowise.core.analysis.health import scoring  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402

REAL = set(ALL_BIOMARKERS)
SEV = {"low": Severity.LOW, "medium": Severity.MEDIUM, "high": Severity.HIGH,
       "critical": Severity.CRITICAL}
CATS = ["organizational", "structural_complexity", "test_coverage",
        "size_and_complexity", "duplication", "test_quality"]
STATIC = [scoring.CATEGORY_CAPS[c] for c in CATS]


def norm(p):
    return p.replace("\\", "/").strip("/")


def build(label):
    """Return per-file (raw[6], y, repo). raw[c] = total weighted deduction in c."""
    rows, ys, groups = [], [], []
    for name in [r["name"] for r in yaml.safe_load((BENCH / "config.yaml").read_text())["repos"]]:
        d = RES / f"health_defect_{name}"
        if not (d / "health_scores.json").exists():
            continue
        h = json.load(open(d / "health_scores.json"))
        j = json.load(open(d / "joined_data.json"))
        cnt = {}
        lp = d / f"defect_counts_{label}.json"
        if lp.exists():
            cnt = {norm(k): v for k, v in json.load(open(lp)).items()}
        by_file = {}
        for f in h["findings"]:
            bt = f["biomarker_type"]
            if bt not in REAL:
                continue
            cat = scoring.biomarker_category(bt)
            ci = CATS.index(cat) if cat in CATS else None
            if ci is None:
                continue
            ded = scoring.severity_deduction(
                SEV.get(str(f.get("severity")).lower(), Severity.MEDIUM)
            ) * scoring.biomarker_weight(bt)
            by_file.setdefault(norm(f["file_path"]), [0.0] * len(CATS))[ci] += ded
        npos = sum(1 for x in j if int(cnt.get(norm(x["file_path"]), 0) or 0) > 0)
        if npos == 0 or npos == len(j):
            continue
        for x in j:
            fp = norm(x["file_path"])
            rows.append(by_file.get(fp, [0.0] * len(CATS)))
            ys.append(1 if int(cnt.get(fp, 0) or 0) > 0 else 0)
            groups.append(name)
    return np.array(rows), np.array(ys), np.array(groups)


def score_risk(raw, caps):
    """risk = total capped deduction (higher = riskier = lower health)."""
    caps = np.asarray(caps)
    return np.minimum(raw, caps).sum(axis=1)


def pooled_auc(raw, y, caps):
    return roc_auc_score(y, score_risk(raw, caps))


def mean_repo_popt(raw, y, groups, caps):
    risk = score_risk(raw, caps)
    pts = []
    for g in np.unique(groups):
        m = groups == g
        if len(set(y[m])) < 2:
            continue
        joined = [{"health_score": 10.0 - r, "defect_count": int(t), "nloc": 1}
                  for r, t in zip(risk[m], y[m])]
        # nloc unknown here → Popt needs real nloc; load from risk proxy is wrong.
        p = (popt(joined) or {}).get("popt")
        if p is not None:
            pts.append(p)
    return float(np.mean(pts)) if pts else float("nan")


def fit_caps(raw, y, *, seed=0):
    bounds = [(0.0, 6.0)] * len(CATS)

    def obj(caps):
        try:
            return -pooled_auc(raw, y, caps)
        except Exception:
            return 0.0

    res = differential_evolution(obj, bounds, seed=seed, maxiter=40, popsize=12,
                                 tol=1e-4, polish=True, disp=False)
    return res.x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    args = ap.parse_args()
    raw, y, groups = build(args.label)
    print(f"label={args.label}  files={len(y)}  positives={int(y.sum())}  "
          f"repos={len(set(groups))}")
    print(f"categories: {CATS}")
    print(f"static caps: {STATIC}\n")

    # 1. Static caps — the shipped baseline.
    s_auc = pooled_auc(raw, y, STATIC)
    print(f"STATIC caps        pooled AUC = {s_auc:.4f}")

    # 2. Learned, in-sample (optimistic ceiling).
    learned = fit_caps(raw, y)
    l_auc = pooled_auc(raw, y, learned)
    print(f"LEARNED in-sample  pooled AUC = {l_auc:.4f}   caps="
          f"{[round(c, 2) for c in learned]}")

    # 3. Learned, leave-one-repo-out (honest generalization).
    oof_y, oof_p = [], []
    for g in np.unique(groups):
        tr = groups != g
        te = groups == g
        caps_g = fit_caps(raw[tr], y[tr], seed=hash(g) % 1000)
        oof_y.extend(int(v) for v in y[te])
        oof_p.extend(float(v) for v in score_risk(raw[te], caps_g))
    loo_auc = roc_auc_score(oof_y, oof_p) if len(set(oof_y)) > 1 else float("nan")
    print(f"LEARNED LOO (oof)  pooled AUC = {loo_auc:.4f}   <-- honest verdict")
    print(f"\nΔ in-sample = {l_auc - s_auc:+.4f}   Δ LOO = {loo_auc - s_auc:+.4f}")
    print("(if Δ LOO ≈ 0 or negative, learning caps overfits and the static "
          "§3.1 caps are already as good as cap-tuning gets on this corpus.)")


if __name__ == "__main__":
    main()
