#!/usr/bin/env python3
"""Offline defect-calibration of code-health biomarker weights.

RESEARCH ARTIFACT — NOT a runtime dependency. Nothing under ``packages/``
imports this. It reads the benchmark's per-repo ``joined_data.json`` +
``health_scores.json`` (produced by ``repowise-bench/health-defect`` with
``--score-at t0``), fits an L2-regularized logistic regression of
"file received a bug-fix in (T0, T1]" on the per-file biomarker hits — with an
explicit NLOC column so each biomarker's coefficient is its lift *beyond* file
size — validates cross-project (leave-one-repo-out), then maps the fitted
coefficients to per-biomarker weight multipliers for ``scoring.py``.

The runtime stays pure-deterministic / zero-LLM: only the learned constants are
copied into ``_BIOMARKER_WEIGHT_MULTIPLIER`` by hand after reviewing this
script's output.

Usage (venv python — has numpy/scipy/scikit-learn):
    .venv/Scripts/python.exe local-stash/calibrate_health_weights.py \
        --results-dir repowise-bench/results \
        [--out local-stash/calibration_output.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

# The biomarker roster (kept in sync with scoring._BIOMARKER_CATEGORY and the
# bench lib/stats.ALL_BIOMARKERS). Governance biomarkers are written by an
# additive pass and don't affect file score, so they're excluded from calibration.
# error_handling is also deliberately excluded: it ships as a bounded
# maintainability flag (own 0.5-capped category), not a fitted predictor —
# do NOT add it here when syncing the roster.
BIOMARKERS = [
    "brain_method", "low_cohesion", "god_class", "nested_complexity",
    "complex_method", "bumpy_road", "complex_conditional", "large_method",
    "primitive_obsession", "dry_violation", "untested_hotspot", "coverage_gap",
    "developer_congestion", "knowledge_loss", "hidden_coupling", "function_hotspot",
    "code_age_volatility", "ownership_risk", "churn_risk", "change_entropy",
    "co_change_scatter", "prior_defect",
    "large_assertion_block", "duplicated_assertion_block",
]

# Severity → weight used to turn N findings of varying severity into one scalar
# per (file, biomarker). Mirrors the *ordering* of scoring._SEVERITY_DEDUCTION
# without baking in its exact magnitudes (we want the raw signal, not today's
# tuned deductions).
_SEV_WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0,
               "1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0}


def _sev_weight(sev) -> float:
    return _SEV_WEIGHT.get(str(sev).strip().lower(), 1.0)


# Continuous magnitude per biomarker: the finding-`details` key carrying the
# underlying metric the binary "fired / didn't" encoding throws away (CCN=30 ≠
# CCN=9). When a biomarker has no clean scalar magnitude (or none is emitted) it
# falls back to the severity-weighted hit. Values are log1p-compressed (heavy
# tailed counts) before standardization.
_CONTINUOUS_KEY: dict[str, str] = {
    "brain_method": "ccn",
    "bumpy_road": "bumps",
    "change_entropy": "change_entropy",
    "churn_risk": "relative_churn",
    "co_change_scatter": "scatter",
    "complex_conditional": "operator_count",
    "complex_method": "cognitive",
    "dry_violation": "duplication_pct",
    "function_hotspot": "modification_count",
    "god_class": "method_count",
    "large_assertion_block": "assertion_count",
    "large_method": "nloc",
    "low_cohesion": "lcom4",
    "nested_complexity": "max_nesting",
    "ownership_risk": "minor_contributors",
    "primitive_obsession": "param_count",
    "prior_defect": "prior_defect_count",
}


def _continuous_value(details: dict, key: str) -> float:
    try:
        v = float(details.get(key))
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def load_repo(result_dir: Path) -> tuple[list[dict], list[dict]] | None:
    joined_p = result_dir / "joined_data.json"
    health_p = result_dir / "health_scores.json"
    if not joined_p.exists() or not health_p.exists():
        return None
    joined = json.loads(joined_p.read_text())
    health = json.loads(health_p.read_text())
    findings = health.get("findings", [])
    return joined, findings


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def build_matrix(repos: dict[str, tuple[list[dict], list[dict]]], *, continuous: bool = False):
    """Return (X, y, groups, feature_names).

    ``continuous=False`` (default, the shipped fit): one severity-weighted-hit
    column per biomarker + an NLOC control.

    ``continuous=True`` (the Phase-7 Part-C ablation): each biomarker column
    holds log1p of its underlying magnitude (max over the file's findings) where
    a magnitude key exists (``_CONTINUOUS_KEY``), else the severity-weighted hit.
    Adds global continuous columns the binary encoding throws away — max_ccn,
    max_nesting, and (where a coverage artifact was ingested) the uncovered
    fraction with a coverage-known mask so no-data files don't read as 0% cov.
    """
    feature_names = [*BIOMARKERS, "nloc_log"]
    if continuous:
        feature_names += ["max_ccn_log", "max_nesting", "uncovered_frac", "coverage_known"]
    X_rows: list[list[float]] = []
    y: list[int] = []
    groups: list[str] = []

    for repo_name, (joined, findings) in repos.items():
        hits: dict[str, dict[str, float]] = {}
        mags: dict[str, dict[str, float]] = {}
        for f in findings:
            bt = f.get("biomarker_type")
            if bt not in BIOMARKERS:
                continue
            fp = _norm(f.get("file_path", ""))
            hits.setdefault(fp, {}).setdefault(bt, 0.0)
            hits[fp][bt] += _sev_weight(f.get("severity"))
            key = _CONTINUOUS_KEY.get(bt)
            if key:
                v = _continuous_value(f.get("details") or {}, key)
                cur = mags.setdefault(fp, {}).get(bt, 0.0)
                mags[fp][bt] = max(cur, v)

        for d in joined:
            fp = _norm(d["file_path"])
            if continuous:
                row = []
                for bt in BIOMARKERS:
                    key = _CONTINUOUS_KEY.get(bt)
                    if key and fp in hits and bt in hits[fp]:
                        row.append(float(np.log1p(mags.get(fp, {}).get(bt, 0.0))))
                    else:
                        row.append(hits.get(fp, {}).get(bt, 0.0))
            else:
                row = [hits.get(fp, {}).get(bt, 0.0) for bt in BIOMARKERS]
            row.append(float(np.log1p(max(d.get("nloc", 0), 0))))
            if continuous:
                row.append(float(np.log1p(max(d.get("max_ccn", 0) or 0, 0))))
                row.append(float(d.get("max_nesting", 0) or 0))
                cov = d.get("line_coverage_pct")
                if cov is None:
                    row += [0.0, 0.0]  # uncovered_frac masked off, coverage_known=0
                else:
                    row += [max(0.0, (100.0 - float(cov)) / 100.0), 1.0]
            X_rows.append(row)
            y.append(1 if d.get("defect_count", 0) > 0 else 0)
            groups.append(repo_name)

    return (
        np.asarray(X_rows, dtype=float),
        np.asarray(y, dtype=int),
        np.asarray(groups),
        feature_names,
    )


def cross_project_auc(X, y, groups, C: float) -> tuple[float, float, list[dict]]:
    """Leave-one-repo-out CV. Returns (pooled_oof_auc, mean_fold_auc, per_fold).

    The **pooled out-of-fold AUC** — one AUC over every held-out prediction
    concatenated — is the headline: it is robust to fold-size imbalance (a
    14-file repo and a 600-file repo contribute proportionally), unlike the
    mean of per-repo AUCs which a couple of tiny near-single-class repos can
    drag around. We report both for transparency.
    """
    logo = LeaveOneGroupOut()
    per_fold = []
    aucs = []
    oof_y: list[int] = []
    oof_p: list[float] = []
    for train_idx, test_idx in logo.split(X, y, groups):
        held = groups[test_idx][0]
        scaler = StandardScaler().fit(X[train_idx])
        clf = LogisticRegression(
            C=C, class_weight="balanced", max_iter=5000,
        ).fit(scaler.transform(X[train_idx]), y[train_idx])
        proba = clf.predict_proba(scaler.transform(X[test_idx]))[:, 1]
        oof_y.extend(int(v) for v in y[test_idx])
        oof_p.extend(float(v) for v in proba)
        if len(set(y[test_idx])) < 2:
            per_fold.append({"held_out": held, "auc": None,
                             "n_test": int(len(test_idx)), "n_pos": int(y[test_idx].sum()),
                             "note": "single-class test fold (excluded from mean)"})
            continue
        auc = roc_auc_score(y[test_idx], proba)
        aucs.append(auc)
        per_fold.append({"held_out": held, "auc": round(float(auc), 4),
                         "n_test": int(len(test_idx)), "n_pos": int(y[test_idx].sum())})
    pooled = float(roc_auc_score(oof_y, oof_p)) if len(set(oof_y)) > 1 else float("nan")
    mean_auc = float(np.mean(aucs)) if aucs else float("nan")
    return pooled, mean_auc, per_fold


def fit_final(X, y, feature_names, C: float):
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(
        penalty="l2", C=C, class_weight="balanced", max_iter=5000,
    ).fit(scaler.transform(X), y)
    coefs = clf.coef_[0]
    return {name: float(c) for name, c in zip(feature_names, coefs)}


# --- Balanced shipping policy (chosen 2026-05-29) -------------------------
# The raw coef→multiplier mapping zeros ~14 biomarkers, but several of those
# never fired in the corpus because the benchmark CANNOT measure them (no
# coverage ingested; test-only smells excluded from the source universe; gates
# unmet). Zeroing those would disable signal the benchmark was simply blind to.
# So:
#   * KEEP_PRIOR — benchmark-blind / coverage-dependent / too-rare to trust:
#     left at their existing hand-tuned weight.
#   * FLOOR_WEAK — fired widely but weak/negative at T0: demoted to 0.5 (kept as
#     a mild maintainability/parity signal, not disabled).
#   * everything else with a positive coef → boosted into [1.0, 1.8] ∝ coef.
_KEEP_PRIOR = {
    "untested_hotspot",          # benchmark ingests no coverage → has_test_file fallback only
    "coverage_gap",              # 0 firings (needs coverage data)
    "code_age_volatility",       # 0 firings (gate unmet at T0)
    "large_assertion_block",     # test-files only → excluded from source universe
    "duplicated_assertion_block",
    "churn_risk",                # fired in only 2 repos → unreliable cross-project
    "hidden_coupling",           # fired in only 2 repos
    "knowledge_loss",            # confirmed weak-negative; already low (0.4) from Phase 1
}
_FLOOR_WEAK = {
    "dry_violation",             # coef -0.36, fires 14 repos — strongly non-predictive at T0
    "low_cohesion",              # parity/maintainability signal, weak defect predictor
    "primitive_obsession",
    "bumpy_road",
    "brain_method",
    "developer_congestion",      # the old HEAD-leakage hero; weak/negative under T0
}
_FLOOR_VALUE = 0.5
# Current shipped priors (scoring._BIOMARKER_WEIGHT_MULTIPLIER), so KEEP_PRIOR
# biomarkers are emitted unchanged. Unknown → 1.0 default.
_CURRENT_PRIORS = {
    "developer_congestion": 1.5, "untested_hotspot": 1.3, "function_hotspot": 1.2,
    "code_age_volatility": 1.1, "hidden_coupling": 1.0, "ownership_risk": 1.3,
    "churn_risk": 1.2, "change_entropy": 1.1, "co_change_scatter": 1.0,
    "knowledge_loss": 0.4,
}


def balanced_multipliers(coefs: dict[str, float], *, boost_cap: float = 1.8) -> dict[str, float]:
    """The 'balanced' shipping policy (see comment block above)."""
    bio = {k: v for k, v in coefs.items() if k in BIOMARKERS}
    max_pos = max((c for c in bio.values() if c > 0), default=1.0)
    out: dict[str, float] = {}
    for name, c in bio.items():
        if name in _KEEP_PRIOR:
            out[name] = _CURRENT_PRIORS.get(name, 1.0)
        elif name in _FLOOR_WEAK or c <= 0:
            out[name] = _FLOOR_VALUE
        else:  # positive, measured → boost into [1.0, boost_cap] ∝ coef
            out[name] = round(1.0 + (boost_cap - 1.0) * (c / max_pos), 2)
    return out


def coefs_to_multipliers(coefs: dict[str, float], *, max_mult: float = 2.0,
                         min_mult: float = 0.0) -> dict[str, float]:
    """Map standardized logistic coefficients → weight multipliers.

    Sign-corrected: a biomarker whose presence *raises* defect odds (positive
    coefficient, controlling for NLOC) earns weight proportional to its
    coefficient; non-predictive / wrong-direction biomarkers (coef <= 0) floor
    to ``min_mult``. Scaled so the strongest predictor maps to ``max_mult`` and
    the rest land proportionally — keeping the deduction math inside [1, 10].
    NLOC is a control only and is never shipped as a weight.
    """
    bio_coefs = {k: v for k, v in coefs.items() if k in BIOMARKERS}
    pos = [c for c in bio_coefs.values() if c > 0]
    scale = max(pos) if pos else 1.0
    out = {}
    for name, c in bio_coefs.items():
        if c <= 0:
            out[name] = min_mult
        else:
            out[name] = round(min(max_mult, max_mult * c / scale), 3)
    return out


def main() -> None:
    _BENCH = Path(__file__).resolve().parent  # health-defect/
    _RESULTS = _BENCH.parent / "results"      # bench-agent/results/
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path,
                    default=_RESULTS)
    ap.add_argument("--config", type=Path,
                    default=_BENCH / "config.yaml",
                    help="Restrict to the repo names in this benchmark config "
                         "(avoids loading stale HEAD-era / dropped result dirs).")
    ap.add_argument("--out", type=Path,
                    default=_RESULTS / "calibration" / "calibration_output.json")
    ap.add_argument("--C", type=float, default=0.5, help="Inverse L2 strength")
    ap.add_argument("--features", choices=["binary", "continuous", "ablation"],
                    default="binary",
                    help="binary = severity-weighted hits (shipped fit); "
                         "continuous = log magnitudes + max_ccn/nesting + coverage; "
                         "ablation = fit both and report the pooled-OOF-AUC delta.")
    ap.add_argument("--label", default="joined",
                    help="Label strategy to calibrate on. 'joined' uses "
                         "joined_data.json's defect_count as-is; a strategy name "
                         "(keyword/szz/szz_b/issue/szz_issue) swaps in that repo's "
                         "defect_counts_<label>.json over the SAME file universe — "
                         "for apples-to-apples label-quality comparison.")
    args = ap.parse_args()

    # Only load repos in the current benchmark config — stale result dirs
    # (django/fastapi at HEAD = leakage; starlette = 0 defects) must not enter.
    allowed = None
    if args.config and args.config.exists():
        import yaml
        cfg = yaml.safe_load(args.config.read_text())
        allowed = {r["name"] for r in cfg.get("repos", [])}

    repos: dict[str, tuple[list[dict], list[dict]]] = {}
    skipped = []
    for d in sorted(args.results_dir.glob("health_defect_*")):
        name = d.name.replace("health_defect_", "")
        if allowed is not None and name not in allowed:
            skipped.append(name)
            continue
        loaded = load_repo(d)
        if loaded is None:
            continue
        if args.label != "joined":
            label_p = d / f"defect_counts_{args.label}.json"
            if not label_p.exists():
                print(f"  (skip {name}: no {label_p.name})")
                continue
            counts = {_norm(k): v for k, v in json.loads(label_p.read_text()).items()}
            joined, findings = loaded
            for row in joined:
                row["defect_count"] = counts.get(_norm(row["file_path"]), 0)
            loaded = (joined, findings)
        repos[name] = loaded
    if skipped:
        print(f"Skipped {len(skipped)} non-config result dirs: {', '.join(sorted(skipped))}")

    if not repos:
        print(f"No repo results under {args.results_dir}", file=sys.stderr)
        sys.exit(1)

    # Ablation: fit binary vs continuous over the IDENTICAL universe and report
    # the pooled-OOF-AUC delta (the Phase-7 Part-C question).
    if args.features == "ablation":
        print("\n=== Binary vs continuous feature ablation (pooled OOF AUC) ===")
        rows = []
        for mode in ("binary", "continuous"):
            Xa, ya, ga, _ = build_matrix(repos, continuous=(mode == "continuous"))
            best_m = max(
                ((C, cross_project_auc(Xa, ya, ga, C)[0]) for C in (0.1, 0.25, 0.5, 1.0, 2.0)),
                key=lambda t: t[1],
            )
            rows.append((mode, best_m[0], best_m[1]))
            print(f"  {mode:11s} best C={best_m[0]:<4} pooled OOF AUC = {best_m[1]:.4f}")
        delta = rows[1][2] - rows[0][2]
        print(f"  continuous − binary ΔAUC = {delta:+.4f}")
        out = {"ablation": {m: {"C": c, "pooled_oof_auc": a} for m, c, a in rows},
               "delta_auc": delta,
               "n_files": int(len(ya)), "n_positives": int(ya.sum())}
        args.out.write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.out}")
        return

    X, y, groups, feature_names = build_matrix(repos, continuous=(args.features == "continuous"))
    n, n_pos = len(y), int(y.sum())
    print(f"Corpus: {len(repos)} repos | {n} files | {n_pos} positives "
          f"({n_pos / n:.1%}) | {len(feature_names)} features [{args.features}]")
    for r in repos:
        gm = groups == r
        print(f"  {r:14s} files={int(gm.sum()):5d}  positives={int(y[gm].sum()):4d}")

    # Pick C by pooled out-of-fold AUC over a small grid.
    best = None
    for C in (0.1, 0.25, 0.5, 1.0, 2.0):
        pooled, mean_fold, _ = cross_project_auc(X, y, groups, C)
        print(f"  C={C:<5}  pooled OOF AUC = {pooled:.4f}   (mean-fold {mean_fold:.4f})")
        if best is None or pooled > best[1]:
            best = (C, pooled)
    C = best[0]
    pooled_auc, mean_auc, per_fold = cross_project_auc(X, y, groups, C)
    print(f"\nChosen C={C}  pooled OOF AUC = {pooled_auc:.4f}  (mean-fold {mean_auc:.4f})")

    coefs = fit_final(X, y, feature_names, C)
    mults = coefs_to_multipliers(coefs)
    shipped = balanced_multipliers(coefs)

    print("\nFitted coefficients (standardized; +ve => raises defect odds beyond size):")
    print(f"  {'biomarker':26s} {'coef':>8s} {'raw':>6s} {'SHIPPED(balanced)':>18s}")
    for name in BIOMARKERS:
        print(f"  {name:26s} {coefs[name]:+.4f} {mults.get(name):>6} {shipped.get(name):>18}")
    print(f"  {'nloc_log':26s} {coefs['nloc_log']:+.4f}  (control, not shipped)")
    for name in feature_names:
        if name not in BIOMARKERS and name != "nloc_log":
            print(f"  {name:26s} {coefs[name]:+.4f}  (continuous control, not shipped)")

    out = {
        "corpus": {r: {"files": int((groups == r).sum()),
                       "positives": int(y[groups == r].sum())} for r in repos},
        "n_files": n, "n_positives": n_pos,
        "chosen_C": C,
        "pooled_oof_auc": pooled_auc,
        "cross_project_mean_fold_auc": mean_auc,
        "per_fold": per_fold,
        "coefficients": coefs,
        "raw_multipliers": mults,
        "shipped_multipliers_balanced": shipped,
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
