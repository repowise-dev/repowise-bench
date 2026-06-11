#!/usr/bin/env python3
"""Offline function/symbol-level defect calibration (Phase 8 research artifact).

NOT a runtime dependency. Reads the bench's per-repo ``function_joined.json``
(built by ``repowise-bench/health-defect/build_function_dataset.py``) and fits an
interpretable L2-logistic of "function received a bug-inducing-commit's lines at
T0" on the walker's per-function structural features + per-function process
signals (blame-derived). Reports leave-one-repo-out pooled out-of-fold AUC +
Popt with a bootstrap CI, the standardized coefficients, and a structural-only
vs +process ablation — then puts the function-level number next to the shipped
file-level result so the granularity question (does function-level help?) is
answerable with evidence.

Usage (venv python — numpy/scipy/scikit-learn):
    .venv/Scripts/python.exe local-stash/calibrate_function_level.py \
        --results-dir repowise-bench/results
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

# Per-function features. Counts are heavy-tailed → log1p before standardizing.
_STRUCTURAL = ["ccn", "cognitive", "max_nesting", "nloc", "param_count",
               "n_conditions", "bumps"]
_PROCESS = ["mod_count", "recent_mods", "age_days"]
_LOG_FEATURES = {"ccn", "cognitive", "nloc", "n_conditions", "bumps",
                 "mod_count", "recent_mods", "param_count"}


def _feat(row: dict, name: str) -> float:
    if name == "age_days":
        v = row.get("age_days")
        return float(v) if v is not None else 0.0
    v = float(row.get(name, 0) or 0)
    if v < 0:
        v = 0.0
    return float(np.log1p(v)) if name in _LOG_FEATURES else v


def build_matrix(repos: dict[str, list[dict]], features: list[str]):
    X, y, groups, effort = [], [], [], []
    for repo, rows in repos.items():
        for r in rows:
            X.append([_feat(r, f) for f in features])
            y.append(int(r.get("label", 0)))
            groups.append(repo)
            effort.append(max(int(r.get("nloc", 0) or 0), 1))
    return (np.asarray(X, float), np.asarray(y, int),
            np.asarray(groups), np.asarray(effort, float))


def oof_predictions(X, y, groups, C: float):
    """Leave-one-repo-out out-of-fold probabilities, index-aligned to X."""
    logo = LeaveOneGroupOut()
    oof = np.full(len(y), np.nan)
    per_fold = []
    for tr, te in logo.split(X, y, groups):
        held = groups[te][0]
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
        clf.fit(sc.transform(X[tr]), y[tr])
        p = clf.predict_proba(sc.transform(X[te]))[:, 1]
        oof[te] = p
        if len(set(y[te])) > 1:
            per_fold.append({"held_out": held, "auc": round(float(roc_auc_score(y[te], p)), 4),
                             "n": int(len(te)), "n_pos": int(y[te].sum())})
        else:
            per_fold.append({"held_out": held, "auc": None, "n": int(len(te)),
                             "n_pos": int(y[te].sum())})
    return oof, per_fold


def popt(y, scores, effort) -> float:
    """Mende–Koschke effort-aware Popt: area between the model and worst curves,
    normalized by optimal-vs-worst. 0.5≈random ordering, 1.0=optimal."""
    def curve(order):
        ce = np.cumsum(effort[order]) / effort.sum()
        cd = np.cumsum(y[order]) / max(y.sum(), 1)
        ce = np.concatenate([[0.0], ce]); cd = np.concatenate([[0.0], cd])
        _trap = getattr(np, "trapezoid", None) or np.trapz
        return _trap(cd, ce)
    opt = curve(np.lexsort((effort, -y)))            # bugs first, cheap first
    worst = curve(np.lexsort((-effort, y)))          # clean+expensive first
    model = curve(np.argsort(-scores, kind="stable"))
    return float((model - worst) / (opt - worst)) if opt > worst else float("nan")


def precision_at_loc(y, scores, effort, frac=0.20) -> float:
    order = np.argsort(-scores, kind="stable")
    budget = effort.sum() * frac
    spent = tp = k = 0
    for i in order:
        if spent + effort[i] > budget and k > 0:
            break
        spent += effort[i]; k += 1
        tp += int(y[i] == 1)
    return tp / k if k else 0.0


def best_C(X, y, groups):
    best = None
    for C in (0.05, 0.1, 0.25, 0.5, 1.0):
        oof, _ = oof_predictions(X, y, groups, C)
        m = np.isfinite(oof)
        auc = roc_auc_score(y[m], oof[m]) if len(set(y[m])) > 1 else float("nan")
        if best is None or auc > best[1]:
            best = (C, auc)
    return best[0]


def bootstrap_repo_ci(repos, features, C, n_boot=500, seed=12345):
    """Resample repos (cluster bootstrap) → pooled-OOF-AUC 95% CI."""
    rng = np.random.default_rng(seed)
    names = list(repos)
    aucs = []
    for _ in range(n_boot):
        pick = rng.choice(len(names), len(names), replace=True)
        sub = {f"{names[i]}#{j}": repos[names[i]] for j, i in enumerate(pick)}
        X, y, g, _e = build_matrix(sub, features)
        if len(set(y)) < 2:
            continue
        oof, _ = oof_predictions(X, y, g, C)
        m = np.isfinite(oof)
        if len(set(y[m])) > 1:
            aucs.append(roc_auc_score(y[m], oof[m]))
    if not aucs:
        return None
    return {"lo": round(float(np.percentile(aucs, 2.5)), 4),
            "hi": round(float(np.percentile(aucs, 97.5)), 4),
            "n_boot": len(aucs)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path,
                    default=Path(__file__).resolve().parents[1] / "repowise-bench" / "results")
    ap.add_argument("--config", type=Path,
                    default=Path(__file__).resolve().parents[1] / "repowise-bench"
                            / "health-defect" / "config.yaml")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "function_calibration_output.json")
    args = ap.parse_args()

    allowed = {r["name"] for r in yaml.safe_load(args.config.read_text())["repos"]}
    repos: dict[str, list[dict]] = {}
    for d in sorted(args.results_dir.glob("health_defect_*")):
        name = d.name.replace("health_defect_", "")
        if name not in allowed:
            continue
        p = d / "function_joined.json"
        if not p.exists():
            continue
        rows = json.loads(p.read_text())
        if rows:
            repos[name] = rows
    if not repos:
        raise SystemExit("No function_joined.json found — run build_function_dataset.py first")

    full = _STRUCTURAL + _PROCESS
    print(f"Corpus: {len(repos)} repos")
    tot = pos = 0
    for r, rows in repos.items():
        np_ = sum(x["label"] for x in rows)
        print(f"  {r:12s} functions={len(rows):5d}  positive={np_:4d} ({np_/len(rows):.1%})")
        tot += len(rows); pos += np_
    print(f"  TOTAL functions={tot} positive={pos} ({pos/tot:.1%})\n")

    results = {}
    for label, feats in [("structural_only", _STRUCTURAL), ("structural+process", full)]:
        X, y, g, eff = build_matrix(repos, feats)
        C = best_C(X, y, g)
        oof, per_fold = oof_predictions(X, y, g, C)
        m = np.isfinite(oof)
        pooled = float(roc_auc_score(y[m], oof[m]))
        mean_fold = float(np.mean([f["auc"] for f in per_fold if f["auc"] is not None]))
        pt = popt(y[m], oof[m], eff[m])
        p20 = precision_at_loc(y[m], oof[m], eff[m])
        ci = bootstrap_repo_ci(repos, feats, C)
        # standardized final coefficients
        sc = StandardScaler().fit(X)
        clf = LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
        clf.fit(sc.transform(X), y)
        coefs = {f: round(float(c), 4) for f, c in zip(feats, clf.coef_[0])}
        results[label] = {
            "C": C, "pooled_oof_auc": round(pooled, 4),
            "mean_fold_auc": round(mean_fold, 4),
            "pooled_oof_auc_ci95": ci,
            "popt": round(pt, 4), "precision_at_20pct_loc": round(p20, 4),
            "coefficients": coefs, "per_fold": per_fold,
        }
        print(f"=== {label} (C={C}) ===")
        print(f"  pooled OOF AUC = {pooled:.4f}  [{ci['lo'] if ci else '?'},"
              f"{ci['hi'] if ci else '?'}]   mean-fold {mean_fold:.4f}")
        print(f"  Popt = {pt:.4f}   Precision@20%LOC = {p20:.4f}")
        print("  coefficients (standardized; +ve raises defect odds):")
        for f, c in sorted(coefs.items(), key=lambda kv: -abs(kv[1])):
            print(f"    {f:14s} {c:+.4f}")
        print()

    # Sensitivity: axios is the function-level analogue of the file-level valibot
    # exclusion — a 126-function micro-lib whose 104 fixes saturate its tiny
    # surface (63% of functions positive), a degenerate near-single-class label
    # that can't be discriminated and drags the pooled OOF. Report the corpus
    # with it removed so the granularity question isn't decided by one outlier.
    sensitivity = {}
    for drop in (["axios"], ["axios", "chi"]):
        sub = {k: v for k, v in repos.items() if k not in drop}
        block = {}
        for label, feats in [("structural_only", _STRUCTURAL), ("structural+process", full)]:
            X, y, g, eff = build_matrix(sub, feats)
            C = best_C(X, y, g)
            oof, _ = oof_predictions(X, y, g, C)
            mm = np.isfinite(oof)
            block[label] = {
                "C": C,
                "pooled_oof_auc": round(float(roc_auc_score(y[mm], oof[mm])), 4),
                "popt": round(popt(y[mm], oof[mm], eff[mm]), 4),
                "n_functions": int(len(y)), "n_positive": int(y.sum()),
            }
        sensitivity[",".join(f"-{d}" for d in drop)] = block
        print(f"=== sensitivity (drop {drop}) ===")
        for label, b in block.items():
            print(f"  {label:18s} AUC={b['pooled_oof_auc']:.4f} Popt={b['popt']:.4f} "
                  f"(n={b['n_functions']}, pos={b['n_positive']})")

    args.out.write_text(json.dumps({"corpus": {r: len(v) for r, v in repos.items()},
                                    "n_functions": tot, "n_positive": pos,
                                    "file_level_reference": {
                                        "binary_pooled_oof_auc": 0.699,
                                        "continuous_pooled_oof_auc": 0.744,
                                        "n_files": 830, "n_positives": 216},
                                    "results": results,
                                    "sensitivity": sensitivity}, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
