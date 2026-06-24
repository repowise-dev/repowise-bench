#!/usr/bin/env python3
"""Gate G5 - refit-resampling bootstrap TOST (R&R panel, R2/R4).

The shipped bootstrap TOST (bootstrap_tost.py, results/bootstrap_tost.json)
resamples evaluation units over the FIXED leave-one-repo-out OOF predictions
(candidate_eval.py:236-263): it propagates ranking-sample variance but NOT
model-refit variance. Omitting refit variance narrows the equivalence intervals,
which makes declaring equivalence easier rather than harder, so it cannot be used
as a conservative bound (corrected Sec 4.2 paragraph).

This extends the bootstrap to ALSO resample the model fit. Per replicate it
(1) resamples the corpus repositories with replacement, (2) re-runs the full
LORO-OOF procedure on that resample - refitting the calibrated base model and the
base+candidate model on every fold - and (3) recomputes the pooled OOF AUC delta.
The resulting interval includes refit variance, so the TOST it yields is the honest
(non-anti-conservative) version.

A held-out repository's (possibly duplicated) rows are predicted by a model trained
on the OTHER distinct repositories in the resample, preserving the leakage-free
LORO structure; duplicated repositories simply up-weight, which is the cluster
bootstrap's intended effect.

Columns: only review_coverage's raw candidate column is cached
(results/review_coverage_columns.json); the three firmly powered nulls
(centrality, change bursts, error-handling) need their candidate columns
regenerated from the experiment scripts, which are not in this worktree, so their
refit TOST is deferred to the R&R window. We run review_coverage here - one of the
two boundary-sensitive verdicts the gate flags - and, as a pipeline check, also
reproduce its fixed-prediction TOST to confirm it matches the cached card.

Cache-only, deterministic. Seed 12345.

Usage (shared venv; needs error_analysis on the path):
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=../../repowise-bench/health-defect \
    ../../.venv/Scripts/python.exe refit_bootstrap_tost.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

# error_analysis lives in the canonical bench worktree; allow it on the path.
_CANON = Path(__file__).resolve().parent.parent.parent / "repowise-bench" / "health-defect"
if _CANON.exists():
    sys.path.insert(0, str(_CANON))

from candidate_eval import (  # noqa: E402
    base_matrix, _fit_logit, load_corpus,
)

_HERE = Path(__file__).resolve().parent
_RESULTS = Path(__file__).resolve().parent.parent.parent / "repowise-bench" / "results"
SESOIS = [0.005, 0.01, 0.02]
SEED = 12345
N_BOOT = 1000


def _pooled_auc(y: np.ndarray, score: np.ndarray) -> float:
    if len(set(int(v) for v in y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, score))


def loro_oof(X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Leave-one-repo-out OOF P(defect), aligned to row order. A repo's rows are
    predicted by a model trained on all rows whose group differs from it."""
    oof = np.full(len(y), np.nan)
    for g in set(groups.tolist()):
        te = groups == g
        tr = ~te
        if len(set(int(v) for v in y[tr])) < 2:
            continue
        scaler, clf = _fit_logit(X[tr], y[tr])
        oof[te] = clf.predict_proba(scaler.transform(X[te]))[:, 1]
    return oof


def tost(boot: np.ndarray, delta_point: float) -> dict:
    out = {"delta_point": round(float(delta_point), 5), "n_boot": int(len(boot)),
           "ci90": [round(float(np.percentile(boot, 5)), 5),
                    round(float(np.percentile(boot, 95)), 5)],
           "ci95": [round(float(np.percentile(boot, 2.5)), 5),
                    round(float(np.percentile(boot, 97.5)), 5)]}
    for D in SESOIS:
        p_lo = float(np.mean(boot <= -D))
        p_hi = float(np.mean(boot >= D))
        p = max(p_lo, p_hi)
        within = out["ci90"][0] >= -D and out["ci90"][1] <= D
        out[f"tost@{D}"] = {"p": round(p, 4), "equivalent": bool(within and p < 0.05)}
    return out


def build(column: dict, results_dir: Path, config_path: Path, label="keyword"):
    rows = load_corpus(results_dir, config_path, label)

    def cval(r):
        return (column.get(r["repo"]) or {}).get(r["file_path"])

    universe = [r for r in rows if cval(r) is not None]
    y = np.array([r["y"] for r in universe], dtype=int)
    groups = np.array([r["repo"] for r in universe])
    cand = np.array([float(cval(r)) for r in universe], dtype=float)
    Xb, _ = base_matrix(universe)
    Xc = np.column_stack([Xb, cand])
    return universe, y, groups, Xb, Xc


def fixed_prediction_boot(Xb, Xc, y, groups, n_boot, seed):
    """Reproduce the shipped fixed-prediction bootstrap (validation check)."""
    oof_b = loro_oof(Xb, y, groups)
    oof_c = loro_oof(Xc, y, groups)
    delta_point = _pooled_auc(y, oof_c) - _pooled_auc(y, oof_b)
    repos = sorted(set(groups.tolist()))
    idx_by = {g: np.where(groups == g)[0] for g in repos}
    rng = random.Random(seed)
    boot = []
    for _ in range(n_boot):
        chosen = [repos[rng.randrange(len(repos))] for _ in repos]
        parts = []
        for g in chosen:
            ridx = idx_by[g]
            parts.append(ridx[np.random.default_rng(rng.randrange(1 << 30)).integers(0, len(ridx), len(ridx))])
        idx = np.concatenate(parts)
        yb = y[idx]
        if len(set(int(v) for v in yb)) < 2:
            continue
        ab = _pooled_auc(yb, oof_b[idx])
        ac = _pooled_auc(yb, oof_c[idx])
        if ab == ab and ac == ac:
            boot.append(ac - ab)
    return delta_point, np.asarray(boot)


def refit_boot(Xb, Xc, y, groups, n_boot, seed):
    """Refit-resampling bootstrap: resample repos, re-run LORO-OOF, recompute delta."""
    repos = sorted(set(groups.tolist()))
    idx_by = {g: np.where(groups == g)[0] for g in repos}
    delta_point = _pooled_auc(y, loro_oof(Xc, y, groups)) - _pooled_auc(y, loro_oof(Xb, y, groups))
    rng = random.Random(seed)
    boot = []
    for _ in range(n_boot):
        chosen = [repos[rng.randrange(len(repos))] for _ in repos]
        idx = np.concatenate([idx_by[g] for g in chosen])
        gb = groups[idx]
        yb = y[idx]
        if len(set(int(v) for v in yb)) < 2 or len(set(gb.tolist())) < 3:
            continue
        ob = loro_oof(Xb[idx], yb, gb)
        oc = loro_oof(Xc[idx], yb, gb)
        m = ~np.isnan(ob) & ~np.isnan(oc)
        if m.sum() < 10 or len(set(int(v) for v in yb[m])) < 2:
            continue
        ab = _pooled_auc(yb[m], ob[m])
        ac = _pooled_auc(yb[m], oc[m])
        if ab == ab and ac == ac:
            boot.append(ac - ab)
    return delta_point, np.asarray(boot)


def main() -> None:
    col = json.loads((_RESULTS / "review_coverage_columns.json").read_text())
    column = col["by_feat"]["reviewed_fraction"]
    config = _CANON / "config.yaml"
    universe, y, groups, Xb, Xc = build(column, _RESULTS.parent / "results", config)
    print(f"Gate G5 refit-resampling bootstrap TOST | signal=review coverage "
          f"(reviewed_fraction) | {len(universe)} files | {int(y.sum())} positives | "
          f"{len(set(groups.tolist()))} repos | seed {SEED} | n_boot {N_BOOT}\n")

    dp_fix, boot_fix = fixed_prediction_boot(Xb, Xc, y, groups, N_BOOT, SEED)
    r_fix = tost(boot_fix, dp_fix)
    print("--- fixed-prediction bootstrap (validation vs cached card p@0.02=0.068, "
          "delta -0.0031) ---")
    print(f"  delta={r_fix['delta_point']:+.4f}  90% CI [{r_fix['ci90'][0]:+.4f}, "
          f"{r_fix['ci90'][1]:+.4f}]")
    for D in SESOIS:
        t = r_fix[f"tost@{D}"]
        print(f"  TOST@{D}: p={t['p']:.4f}  equivalent={t['equivalent']}")

    dp_ref, boot_ref = refit_boot(Xb, Xc, y, groups, N_BOOT, SEED)
    r_ref = tost(boot_ref, dp_ref)
    print("\n--- refit-resampling bootstrap (includes model-refit variance) ---")
    print(f"  delta={r_ref['delta_point']:+.4f}  90% CI [{r_ref['ci90'][0]:+.4f}, "
          f"{r_ref['ci90'][1]:+.4f}]  (n_eff={r_ref['n_boot']})")
    for D in SESOIS:
        t = r_ref[f"tost@{D}"]
        print(f"  TOST@{D}: p={t['p']:.4f}  equivalent={t['equivalent']}")

    widen = (r_ref["ci90"][1] - r_ref["ci90"][0]) / (r_fix["ci90"][1] - r_fix["ci90"][0])
    print(f"\n90% CI width refit/fixed ratio: {widen:.2f}x")

    out = {
        "signal": "review coverage (reviewed_fraction)",
        "n_files": len(universe), "n_positives": int(y.sum()),
        "n_repos": len(set(groups.tolist())), "seed": SEED, "n_boot": N_BOOT,
        "sesois": SESOIS,
        "fixed_prediction": r_fix,
        "refit_resampling": r_ref,
        "ci90_width_ratio_refit_over_fixed": round(float(widen), 3),
        "note": ("Only review_coverage's raw candidate column is cached; the three "
                 "firmly powered nulls need their columns regenerated from the "
                 "experiment scripts (absent from this worktree), so their refit TOST "
                 "is deferred to the R&R window."),
    }
    op = _RESULTS / "refit_bootstrap_tost.json"
    op.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {op}")


if __name__ == "__main__":
    main()
