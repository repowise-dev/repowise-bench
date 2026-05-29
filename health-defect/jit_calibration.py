#!/usr/bin/env python3
"""Offline calibration for the shipped just-in-time change-risk model.

``jit_defect_prototype.py`` proved the paradigm on one repo at a time (clap AUC
0.819 vs churn 0.769; pydantic 0.781 vs 0.733). This script turns that into
**shippable constants**: it pools the corpus, fits one interpretable logistic
over the genuinely *runtime-available* change features, evaluates generalization
(per-repo time-split + leave-one-repo-out, vs the churn-only baseline, with
bootstrap CIs), and dumps the standardization + coefficients the product bakes
into ``analysis/change_risk/model.py``.

Key difference from the prototype: the runtime scorer sees a *live* diff, so
``age_days`` (a label-availability artifact — older commits have had more time to
be fixed) is NOT a feature. We keep the prototype's right-censoring guard (drop
the most recent ``--gap-days`` from training/eval) but the model itself is fit on
``[la, ld, nf, nd, ns, entropy, exp]`` only — every one of which a pre-merge diff
has in hand.

Labels: AG-SZZ bug-inducing commits (same as the prototype). Offline only — the
runtime never blames.

Run (venv python), from health-defect/:
    ../../.venv/Scripts/python.exe jit_calibration.py \
        --repos clap,pydantic,fd,gin,fastify --window 1500 --gap-days 120
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from jit_defect_prototype import bug_inducing_set, commit_features  # type: ignore
from lib.defect_counter import find_fix_commits  # type: ignore
from lib.szz import _git  # type: ignore

HERE = Path(__file__).resolve().parent
REPOS = (HERE.parent / "repos").resolve()

# Genuinely runtime-available features (NO age_days — see module docstring).
COLS = ["la", "ld", "nf", "nd", "ns", "entropy", "exp"]
LOG1P = {"la", "ld", "nf", "nd", "ns", "exp"}  # heavy-tailed counts


def load_repo_cfg() -> dict[str, dict]:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    return {r["name"]: r for r in cfg.get("repos", [])}


def build_repo(name: str, rc: dict, window: int, max_fixes: int, gap_days: float):
    repo = str((REPOS / name).resolve())
    exts = tuple(rc.get("extensions", [".py"]))
    sroot = rc.get("source_root", "")
    roots = _git(["rev-list", "--max-parents=0", "HEAD"], repo).strip().split("\n")
    root = roots[-1] if roots and roots[-1] else "HEAD"
    fixes = find_fix_commits(repo, root, "HEAD", strategy="keyword")
    fix_shas = {s for s, _ in fixes}
    inducing = bug_inducing_set(repo, fixes, sroot, exts, fix_shas, max_fixes)
    feats = commit_features(repo, window, sroot, exts)
    for r in feats:
        r["y"] = 1 if r["sha"] in inducing else 0
    now = max((r["ct"] for r in feats), default=0)
    cutoff = now - gap_days * 86400.0
    eval_rows = [r for r in feats if r["ct"] <= cutoff]
    return eval_rows, len(fixes), len(inducing)


def matrix(rows):
    X = np.array([[r[c] for c in COLS] for r in rows], float)
    for j, c in enumerate(COLS):
        if c in LOG1P:
            X[:, j] = np.log1p(X[:, j])
    y = np.array([r["y"] for r in rows], int)
    return X, y


def time_split_auc(rows, train_frac=0.7):
    """Per-repo time-ordered AUC for the model vs churn-only baseline."""
    if len(rows) < 40:
        return None
    X, y = matrix(rows)
    k = int(len(rows) * train_frac)
    if len(set(y[:k])) < 2 or len(set(y[k:])) < 2:
        return None
    sc = StandardScaler().fit(X[:k])
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(
        sc.transform(X[:k]), y[:k]
    )
    p = clf.predict_proba(sc.transform(X[k:]))[:, 1]
    churn = [r["churn"] for r in rows[k:]]
    return {
        "model_auc": float(roc_auc_score(y[k:], p)),
        "churn_auc": float(roc_auc_score(y[k:], churn)),
        "n_test": len(rows) - k,
        "pos_test": int(sum(y[k:])),
    }


def loo_auc(rows_by_repo):
    """Leave-one-repo-out pooled OOF AUC (model vs churn)."""
    repos = list(rows_by_repo)
    oy, op, oc = [], [], []
    for held in repos:
        tr = [r for rp in repos if rp != held for r in rows_by_repo[rp]]
        te = rows_by_repo[held]
        Xtr, ytr = matrix(tr)
        Xte, yte = matrix(te)
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        sc = StandardScaler().fit(Xtr)
        clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(
            sc.transform(Xtr), ytr
        )
        op.extend(float(v) for v in clf.predict_proba(sc.transform(Xte))[:, 1])
        oc.extend(float(r["churn"]) for r in te)
        oy.extend(int(v) for v in yte)
    if len(set(oy)) < 2:
        return None
    model = roc_auc_score(oy, op)
    churn = roc_auc_score(oy, oc)
    # bootstrap CI of the model-minus-churn AUC gap.
    rng = random.Random(7)
    diffs = []
    n = len(oy)
    ay, ap, ac = np.array(oy), np.array(op), np.array(oc)
    for _ in range(500):
        idx = [rng.randrange(n) for _ in range(n)]
        yy = ay[idx]
        if len(set(yy.tolist())) < 2:
            continue
        diffs.append(roc_auc_score(yy, ap[idx]) - roc_auc_score(yy, ac[idx]))
    diffs.sort()
    ci = [round(float(np.mean(diffs)), 4),
          round(diffs[int(0.025 * len(diffs))], 4),
          round(diffs[int(0.975 * len(diffs))], 4)] if diffs else [None, None, None]
    return {"model_oof_auc": round(model, 4), "churn_oof_auc": round(churn, 4),
            "delta_vs_churn_ci": ci, "n": len(oy), "pos": int(sum(oy))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default="clap,pydantic,fd,gin,fastify")
    ap.add_argument("--window", type=int, default=1500)
    ap.add_argument("--max-fixes", type=int, default=400)
    ap.add_argument("--gap-days", type=float, default=120.0)
    ap.add_argument("--out", type=Path, default=HERE.parent / "results" / "jit_calibration.json")
    args = ap.parse_args()

    rc = load_repo_cfg()
    names = [n.strip() for n in args.repos.split(",") if n.strip()]
    rows_by_repo, per_repo = {}, {}
    for name in names:
        if name not in rc:
            print(f"  ! unknown repo {name}; skipping")
            continue
        rows, nfix, nind = build_repo(name, rc[name], args.window, args.max_fixes, args.gap_days)
        npos = sum(r["y"] for r in rows)
        print(f"  {name:10s} eval_rows={len(rows):5d} pos={npos:4d} "
              f"fixes={nfix} inducing={nind}")
        if npos >= 10:
            rows_by_repo[name] = rows
            per_repo[name] = time_split_auc(rows)

    print("\n=== per-repo time-split (model vs churn) ===")
    for name, r in per_repo.items():
        if r:
            print(f"  {name:10s} model {r['model_auc']:.3f}  churn {r['churn_auc']:.3f}  "
                  f"(+{r['model_auc'] - r['churn_auc']:+.3f})  n_test={r['n_test']} pos={r['pos_test']}")

    loo = loo_auc(rows_by_repo)
    print("\n=== leave-one-repo-out pooled ===")
    if loo:
        print(f"  model OOF AUC {loo['model_oof_auc']}  churn {loo['churn_oof_auc']}  "
              f"Δ {loo['delta_vs_churn_ci'][0]:+.4f} "
              f"[{loo['delta_vs_churn_ci'][1]:+.4f},{loo['delta_vs_churn_ci'][2]:+.4f}]  "
              f"(n={loo['n']}, pos={loo['pos']})")

    # Final shippable fit: pooled, standardized, on ALL censored commits.
    all_rows = [r for rows in rows_by_repo.values() for r in rows]
    X, y = matrix(all_rows)
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(
        scaler.transform(X), y
    )
    constants = {
        "features": COLS,
        "log1p": [c in LOG1P for c in COLS],
        "mean": [round(float(v), 6) for v in scaler.mean_],
        "std": [round(float(v), 6) for v in scaler.scale_],
        "coef": [round(float(v), 6) for v in clf.coef_[0]],
        "intercept": round(float(clf.intercept_[0]), 6),
        "n_train": len(all_rows),
        "n_positive": int(sum(y)),
        "repos": list(rows_by_repo),
        "gap_days": args.gap_days,
        "window": args.window,
        "loo": loo,
        "per_repo_time_split": per_repo,
    }
    print("\n=== shippable constants (pooled fit) ===")
    for c, m, s, w in zip(COLS, constants["mean"], constants["std"], constants["coef"]):
        print(f"  {c:10s} mean={m:10.3f} std={s:10.3f} coef={w:+.4f}")
    print(f"  intercept={constants['intercept']:+.4f}  n={len(all_rows)} pos={int(sum(y))}")
    args.out.write_text(json.dumps(constants, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
