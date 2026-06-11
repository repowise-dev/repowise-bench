#!/usr/bin/env python3
"""Does blending the health score with prior-defect history lift Popt?

Phase-6 finding: on effort-aware Popt the cheap process-history baselines
(prior-defects, churn) out-rank the health score, even though health wins on AUC.
The obvious hypothesis: combine them. This experiment fits a leave-one-repo-out
logistic over the file-level joined data and reports pooled out-of-fold AUC +
Popt (effort = NLOC) for each signal alone and blended, so we can see whether
health + prior-defects beats *both* parents — and specifically whether it lifts
Popt past the prior-defects baseline.

Single-feature specs use the same LOO-logistic path; for one monotonic feature
the logistic is a monotone map, so its ranking (hence AUC/Popt) equals the raw
signal's — the comparison is apples-to-apples.

NOT a runtime dependency. Reads repowise-bench file-level results.

    .venv/Scripts/python.exe local-stash/blend_prior_defects.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

# Risk features. health_risk = (10 - health_score) so higher = worse, aligning
# every signal "up = riskier". Counts are log1p'd (heavy-tailed). NLOC is left
# OUT of the blends on purpose: it is the effort axis, and feeding it in just
# pushes the ranking toward read-the-biggest-first, which is what tanks Popt.
def _row_features(r: dict) -> dict[str, float]:
    return {
        "health_risk": 10.0 - float(r.get("health_score", 10.0)),
        "log_prior": float(np.log1p(max(int(r.get("prior_defect_count", 0) or 0), 0))),
        "log_churn": float(np.log1p(max(int(r.get("commit_count_90d", 0) or 0), 0))),
        "log_nloc": float(np.log1p(max(int(r.get("nloc", 0) or 0), 0))),
    }


SPECS = {
    "health_only": ["health_risk"],
    "prior_only": ["log_prior"],
    "churn_only": ["log_churn"],
    "health+prior": ["health_risk", "log_prior"],
    "health+churn": ["health_risk", "log_churn"],
    "health+prior+churn": ["health_risk", "log_prior", "log_churn"],
}


def _trap(cd, ce):
    f = getattr(np, "trapezoid", None) or np.trapz
    return f(cd, ce)


def popt(y, scores, effort) -> float:
    def curve(order):
        ce = np.cumsum(effort[order]) / effort.sum()
        cd = np.cumsum(y[order]) / max(y.sum(), 1)
        return _trap(np.concatenate([[0.0], cd]), np.concatenate([[0.0], ce]))
    opt = curve(np.lexsort((effort, -y)))
    worst = curve(np.lexsort((-effort, y)))
    model = curve(np.argsort(-scores, kind="stable"))
    return float((model - worst) / (opt - worst)) if opt > worst else float("nan")


def oof(X, y, groups, C=0.5):
    logo = LeaveOneGroupOut()
    pred = np.full(len(y), np.nan)
    fold_aucs = []
    for tr, te in logo.split(X, y, groups):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
        clf.fit(sc.transform(X[tr]), y[tr])
        p = clf.predict_proba(sc.transform(X[te]))[:, 1]
        pred[te] = p
        if len(set(y[te])) > 1:
            fold_aucs.append(roc_auc_score(y[te], p))
    return pred, (float(np.mean(fold_aucs)) if fold_aucs else float("nan"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path,
                    default=Path(__file__).resolve().parents[2] / "results")
    ap.add_argument("--config", type=Path,
                    default=Path(__file__).resolve().parents[1] / "config.yaml")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "blend_prior_defects_output.json")
    args = ap.parse_args()

    allowed = {r["name"] for r in yaml.safe_load(args.config.read_text())["repos"]}
    rows, groups, y, eff = [], [], [], []
    repos = []
    for d in sorted(args.results_dir.glob("health_defect_*")):
        name = d.name.replace("health_defect_", "")
        if name not in allowed:
            continue
        p = d / "joined_data.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        if not data:
            continue
        repos.append(name)
        for r in data:
            rows.append(_row_features(r))
            groups.append(name)
            y.append(1 if int(r.get("defect_count", 0) or 0) > 0 else 0)
            eff.append(max(int(r.get("nloc", 0) or 0), 1))

    groups = np.asarray(groups)
    y = np.asarray(y, int)
    eff = np.asarray(eff, float)
    print(f"Corpus: {len(repos)} repos | {len(y)} files | {int(y.sum())} positives "
          f"({y.sum()/len(y):.1%})\n")

    feat_names = ["health_risk", "log_prior", "log_churn", "log_nloc"]
    feat_idx = {n: i for i, n in enumerate(feat_names)}
    Xall = np.asarray([[r[n] for n in feat_names] for r in rows], float)

    def mean_per_repo_popt(pred):
        """Popt computed WITHIN each repo (on its held-out OOF preds) then
        averaged — matches BENCHMARK_REPORT's per-repo Popt; pooling instead lets
        the biggest repos dominate one shared effort curve."""
        vals = []
        for r in repos:
            gm = (groups == r) & np.isfinite(pred)
            if gm.sum() < 2 or len(set(y[gm])) < 2:
                continue
            vals.append(popt(y[gm], pred[gm], eff[gm]))
        return float(np.mean(vals)) if vals else float("nan"), len(vals)

    results = {}
    print(f"  {'model':22s} {'pooled AUC':>11s} {'mean-fold AUC':>14s} "
          f"{'mean Popt':>10s} {'pooled Popt':>12s}")
    for spec, feats in SPECS.items():
        cols = [feat_idx[f] for f in feats]
        X = Xall[:, cols]
        pred, mean_fold = oof(X, y, groups)
        m = np.isfinite(pred)
        auc = float(roc_auc_score(y[m], pred[m])) if len(set(y[m])) > 1 else float("nan")
        mean_pt, n_pt = mean_per_repo_popt(pred)
        pooled_pt = popt(y[m], pred[m], eff[m])
        results[spec] = {"pooled_oof_auc": round(auc, 4),
                         "mean_fold_auc": round(mean_fold, 4),
                         "mean_per_repo_popt": round(mean_pt, 4),
                         "n_repos_popt": n_pt,
                         "pooled_popt": round(pooled_pt, 4)}
        print(f"  {spec:22s} {auc:>11.4f} {mean_fold:>14.4f} "
              f"{mean_pt:>10.4f} {pooled_pt:>12.4f}")

    # Headline: does the blend beat BOTH parents on the per-repo Popt?
    h, pr, blend = results["health_only"], results["prior_only"], results["health+prior"]
    print(f"\n  health+prior vs parents (mean per-repo Popt): "
          f"blend {blend['mean_per_repo_popt']:.3f}  vs health {h['mean_per_repo_popt']:.3f}  "
          f"vs prior {pr['mean_per_repo_popt']:.3f}")
    print(f"  health+prior vs parents (pooled AUC):  "
          f"blend {blend['pooled_oof_auc']:.3f}  vs health {h['pooled_oof_auc']:.3f}  "
          f"vs prior {pr['pooled_oof_auc']:.3f}")

    args.out.write_text(json.dumps(
        {"corpus": repos, "n_files": int(len(y)), "n_positives": int(y.sum()),
         "results": results}, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
