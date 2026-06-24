#!/usr/bin/env python3
"""candidate_eval.py — the promotion-gate scorecard as reusable code.

RESEARCH ARTIFACT (bench-only). Given a single per-file *candidate column*
(``{repo: {file_path: value}}``) and a feature name, it produces the full
five-part promotion scorecard the code-health frontier work gates every signal
on, and emits it as JSON + a paste-ready markdown block.

This is the contract every later phase calls: a column in, a scorecard out.
Keep it generic — no candidate-specific logic lives here.

The five parts (decision rule at the bottom):

1. **Lift beyond size.** The candidate (standardized) is added to the existing
   L2-logistic *calibration* — every scoring biomarker as a severity-weighted
   hit column **plus an explicit log-NLOC control** (the
   ``calibrate_health_weights.py`` design). We report its standardized
   coefficient and a cluster-bootstrap 95% CI (resample *repos*). A pure size
   proxy collapses here because NLOC is already in the model.
2. **OOF AUC delta.** Leave-one-repo-out pooled out-of-fold AUC of the
   calibrated model **vs** the calibrated model **+ candidate**, evaluated on the
   identical (computable) file universe. Bootstrap 95% CI on the delta by
   resampling repos then files within repo over the *fixed* OOF predictions.
   This is the headline.
3. **Within-NLOC-band AUC.** Univariate AUC of the candidate (oriented by its
   fitted sign) *inside* each NLOC quartile — the ≈0.49 wall the shipped score
   hits. The acid test: a candidate can lift overall AUC by re-encoding size and
   still be worthless within band. The shipped risk's within-band AUC is printed
   alongside as the reference wall.
4. **Redundancy.** Spearman of the candidate against the existing process
   columns (``change_entropy``, ``co_change_scatter``, ``churn_risk``,
   ``prior_defect``, ``ownership_risk``), plus the coefficient shift those
   columns take when the candidate joins the model (did it just shuffle weight?).
5. **Coverage / cost.** Fraction of corpus files where the column is computable
   (absent ≠ zero — never imputed) and a caller-supplied cost note.

Decision rule (recorded, not enforced): PROMOTE iff the coef CI excludes 0 and
is positive **and** the OOF AUC delta CI lower bound ≥ 0 with a positive point
estimate **and** it is not redundant (max |Spearman| < ``REDUNDANCY_RHO``).
Otherwise PARK (clean null / size proxy / redundant). FLOOR is a human call for
AUC-neutral-but-expected signals.

Usage as a library (the normal path — see ``centrality_experiment.py``)::

    from candidate_eval import evaluate_candidate, scorecard_markdown
    card = evaluate_candidate(column, "betweenness",
                              results_dir=RESULTS, config_path=CONFIG)
    print(scorecard_markdown(card))

Usage standalone (column cached as JSON ``{repo:{file:value}}``)::

    ../../.venv/Scripts/python.exe candidate_eval.py \
        --column my_column.json --name my_feature \
        [--results-dir ../results] [--label keyword] [--out card.json]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Callable

import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

import error_analysis as ea  # loaders / banding / tie-aware AUC

# The scoring biomarker roster (kept in sync with the calibration + lib.stats).
# Governance/additive biomarkers are excluded — same set the shipped fit uses.
BIOMARKERS = [
    "brain_method", "low_cohesion", "god_class", "nested_complexity",
    "complex_method", "bumpy_road", "complex_conditional", "large_method",
    "primitive_obsession", "dry_violation", "untested_hotspot", "coverage_gap",
    "developer_congestion", "knowledge_loss", "hidden_coupling", "function_hotspot",
    "code_age_volatility", "ownership_risk", "churn_risk", "change_entropy",
    "co_change_scatter", "prior_defect",
    "large_assertion_block", "duplicated_assertion_block",
]

# Process columns the candidate must prove itself distinct from (redundancy).
PROCESS_COLS = [
    "change_entropy", "co_change_scatter", "churn_risk", "prior_defect",
    "ownership_risk",
]

# L2 inverse strength — fixed to the shipped calibration default so the gate is
# deterministic and the candidate cannot win by re-tuning regularization.
C_FIXED = 0.5
# Redundancy threshold: |Spearman| at/above this vs any one process column ⇒
# "fold into X", not a new biomarker.
REDUNDANCY_RHO = 0.70

_HERE = Path(__file__).resolve().parent


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


# --------------------------------------------------------------------------
# Corpus assembly
# --------------------------------------------------------------------------
def load_corpus(results_dir: Path, config_path: Path, label: str) -> list[dict]:
    """Flat per-file rows for the whole corpus via ``error_analysis.build_rows``.

    Each row carries repo / file_path (normalized) / nloc / defect_count / y /
    ``biomarkers`` (severity-weighted hit per scoring biomarker) — exactly the
    substrate the calibration fits on. ``label`` selects the defect ground truth
    (``defect_counts_<label>.json``; default ``keyword``).
    """
    langs, roots = ea.load_config_langs(config_path)
    rows = ea.build_rows(results_dir, langs, roots, label=label)
    for r in rows:
        r["file_path"] = _norm(r["file_path"])
    return rows


def _hit(row: dict, bt: str) -> float:
    return float((row.get("biomarkers") or {}).get(bt, 0.0))


def base_matrix(rows: list[dict]) -> tuple[np.ndarray, list[str]]:
    """Baseline design: one severity-weighted-hit column per biomarker + a
    log1p(NLOC) control column. Returns (X, feature_names)."""
    feats = [*BIOMARKERS, "nloc_log"]
    X = np.array(
        [[_hit(r, bt) for bt in BIOMARKERS] + [float(np.log1p(max(r["nloc"], 0)))]
         for r in rows],
        dtype=float,
    )
    return X, feats


# --------------------------------------------------------------------------
# Model fitting helpers
# --------------------------------------------------------------------------
def _fit_logit(X: np.ndarray, y: np.ndarray) -> tuple[StandardScaler, LogisticRegression]:
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(
        penalty="l2", C=C_FIXED, class_weight="balanced", max_iter=5000,
    ).fit(scaler.transform(X), y)
    return scaler, clf


def _oof_predictions(
    X: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> np.ndarray:
    """Leave-one-repo-out out-of-fold P(defect). Aligned to row order."""
    oof = np.full(len(y), np.nan)
    logo = LeaveOneGroupOut()
    for tr, te in logo.split(X, y, groups):
        scaler, clf = _fit_logit(X[tr], y[tr])
        oof[te] = clf.predict_proba(scaler.transform(X[te]))[:, 1]
    return oof


def _pooled_auc(y: np.ndarray, score: np.ndarray) -> float:
    if len(set(int(v) for v in y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, score))


# --------------------------------------------------------------------------
# The scorecard
# --------------------------------------------------------------------------
def evaluate_candidate(
    column: dict[str, dict[str, float]],
    feature_name: str,
    *,
    results_dir: Path,
    config_path: Path,
    label: str = "keyword",
    n_boot_auc: int = 1000,
    n_boot_coef: int = 400,
    seed: int = 12345,
    cost_note: str | None = None,
    corpus_rows: list[dict] | None = None,
) -> dict:
    """Run a candidate column through the full §3 gate. Returns the scorecard
    dict. ``corpus_rows`` may be passed to avoid reloading for repeated calls."""
    rows = corpus_rows if corpus_rows is not None else load_corpus(results_dir, config_path, label)
    n_total = len(rows)

    # --- restrict to the computable universe (absent ≠ zero) ----------------
    def cand_value(r: dict):
        return (column.get(r["repo"]) or {}).get(r["file_path"])

    universe = [r for r in rows if cand_value(r) is not None]
    n_cov = len(universe)
    coverage = n_cov / n_total if n_total else 0.0

    y = np.array([r["y"] for r in universe], dtype=int)
    groups = np.array([r["repo"] for r in universe])
    cand = np.array([float(cand_value(r)) for r in universe], dtype=float)
    Xb, base_feats = base_matrix(universe)
    Xc = np.column_stack([Xb, cand])
    cand_feats = [*base_feats, feature_name]
    n_pos = int(y.sum())

    insufficient = n_pos < 3 or (n_cov - n_pos) < 3 or len(set(groups)) < 3
    rng = random.Random(seed)

    # --- (1) lift beyond size: candidate coef + cluster-bootstrap CI --------
    full_scaler, full_clf = _fit_logit(Xc, y)
    full_coefs = dict(zip(cand_feats, full_clf.coef_[0]))
    cand_coef = float(full_coefs[feature_name])
    cand_sign = 1.0 if cand_coef >= 0 else -1.0

    base_full_scaler, base_full_clf = _fit_logit(Xb, y)
    base_full_coefs = dict(zip(base_feats, base_full_clf.coef_[0]))

    repo_list = sorted(set(groups))
    idx_by_repo = {g: np.where(groups == g)[0] for g in repo_list}

    coef_boot: list[float] = []
    if not insufficient:
        for _ in range(n_boot_coef):
            chosen = [repo_list[rng.randrange(len(repo_list))] for _ in repo_list]
            idx = np.concatenate([idx_by_repo[g] for g in chosen])
            yb = y[idx]
            if len(set(int(v) for v in yb)) < 2:
                continue
            try:
                _, clf = _fit_logit(Xc[idx], yb)
                coef_boot.append(float(clf.coef_[0][-1]))
            except Exception:  # noqa: BLE001 — degenerate resample
                continue
    coef_ci = _percentile_ci(coef_boot)
    coef_excludes_zero = bool(coef_ci and ((coef_ci[0] > 0) or (coef_ci[1] < 0)))

    # --- (2) OOF AUC delta + bootstrap CI (fixed OOF predictions) -----------
    if insufficient:
        oof_base = oof_cand = np.full(len(y), np.nan)
        auc_base = auc_cand = float("nan")
    else:
        oof_base = _oof_predictions(Xb, y, groups)
        oof_cand = _oof_predictions(Xc, y, groups)
        auc_base = _pooled_auc(y, oof_base)
        auc_cand = _pooled_auc(y, oof_cand)
    delta_point = (auc_cand - auc_base) if (auc_cand == auc_cand and auc_base == auc_base) else float("nan")

    delta_boot: list[float] = []
    if not insufficient:
        for _ in range(n_boot_auc):
            chosen = [repo_list[rng.randrange(len(repo_list))] for _ in repo_list]
            parts = []
            for g in chosen:
                ridx = idx_by_repo[g]
                parts.append(ridx[np.random.default_rng(rng.randrange(1 << 30)).integers(0, len(ridx), len(ridx))])
            idx = np.concatenate(parts)
            yb = y[idx]
            if len(set(int(v) for v in yb)) < 2:
                continue
            ab = _pooled_auc(yb, oof_base[idx])
            ac = _pooled_auc(yb, oof_cand[idx])
            if ab == ab and ac == ac:
                delta_boot.append(ac - ab)
    delta_ci = _percentile_ci(delta_boot)
    delta_ci_ge_zero = bool(delta_ci and delta_ci[0] >= 0 and delta_point > 0)

    # --- (3) within-NLOC-band AUC (candidate oriented by sign) --------------
    cuts = ea.nloc_quartiles(universe)
    band_labels = [f"Q1 (<= {cuts[0]:.0f})", f"Q2 (<= {cuts[1]:.0f})",
                   f"Q3 (<= {cuts[2]:.0f})", f"Q4 (> {cuts[2]:.0f})"]
    oriented = (cand_sign * cand).tolist()
    shipped_risk = [r["risk"] for r in universe]
    band_auc: dict[str, dict] = {}
    for bl in band_labels:
        members = [i for i, r in enumerate(universe) if ea.band_of(r["nloc"], cuts) == bl]
        yy = [int(y[i]) for i in members]
        a_cand = ea.auc(yy, [oriented[i] for i in members])
        a_ship = ea.auc(yy, [shipped_risk[i] for i in members])
        band_auc[bl] = {
            "n": len(members), "pos": int(sum(yy)),
            "candidate_auc": round(a_cand, 4) if a_cand is not None else None,
            "shipped_auc": round(a_ship, 4) if a_ship is not None else None,
        }
    valid_bands = [b["candidate_auc"] for b in band_auc.values() if b["candidate_auc"] is not None]
    within_band_mean = round(float(np.mean(valid_bands)), 4) if valid_bands else None

    # --- (4) redundancy: Spearman vs process cols + coef shift --------------
    redundancy = {}
    for pc in PROCESS_COLS:
        col = np.array([_hit(r, pc) for r in universe], dtype=float)
        if np.std(col) < 1e-12 or np.std(cand) < 1e-12:
            rho = None
        else:
            rho = float(spearmanr(cand, col).statistic)
        redundancy[pc] = {
            "spearman": round(rho, 4) if rho is not None else None,
            "coef_base": round(float(base_full_coefs.get(pc, 0.0)), 4),
            "coef_with_candidate": round(float(full_coefs.get(pc, 0.0)), 4),
        }
    rhos = [abs(v["spearman"]) for v in redundancy.values() if v["spearman"] is not None]
    max_abs_rho = round(max(rhos), 4) if rhos else None
    is_redundant = bool(max_abs_rho is not None and max_abs_rho >= REDUNDANCY_RHO)

    # --- (5) coverage / cost ------------------------------------------------
    coverage_block = {
        "computable_files": n_cov, "corpus_files": n_total,
        "coverage_fraction": round(coverage, 4), "absent_files": n_total - n_cov,
        "cost_note": cost_note,
    }

    # --- verdict ------------------------------------------------------------
    # §3 formal rule: coef CI excludes 0 (positive) AND OOF delta CI ≥ 0 positive
    # AND not redundant. §5 adds the non-negotiable acid test: the candidate must
    # carry signal *within* an NLOC band (mean within-band AUC > 0.5), else it is
    # a size proxy regardless of overall AUC.
    within_band_positive = bool(within_band_mean is not None and within_band_mean > 0.50)
    coef_pos = bool(coef_excludes_zero and coef_ci and coef_ci[0] > 0)
    if insufficient:
        verdict = "INSUFFICIENT"
    elif coef_pos and delta_ci_ge_zero and not is_redundant and within_band_positive:
        verdict = "PROMOTE"
    else:
        verdict = "PARK"

    return {
        "feature_name": feature_name,
        "label": label,
        "verdict": verdict,
        "n_files": n_cov, "n_positives": n_pos, "n_repos": len(repo_list),
        "lift_beyond_size": {
            "candidate_coef": round(cand_coef, 4),
            "coef_ci95": [round(coef_ci[0], 4), round(coef_ci[1], 4)] if coef_ci else None,
            "excludes_zero": coef_excludes_zero,
            "n_boot": len(coef_boot),
            "C": C_FIXED,
        },
        "oof_auc_delta": {
            "auc_base": round(auc_base, 4) if auc_base == auc_base else None,
            "auc_candidate": round(auc_cand, 4) if auc_cand == auc_cand else None,
            "delta": round(delta_point, 4) if delta_point == delta_point else None,
            "delta_ci95": [round(delta_ci[0], 4), round(delta_ci[1], 4)] if delta_ci else None,
            "ci_lower_ge_zero_and_positive": delta_ci_ge_zero,
            "n_boot": len(delta_boot),
            "delta_boot": [round(float(d), 6) for d in delta_boot],
        },
        "within_band_auc": {
            "nloc_quartile_cuts": [round(c) for c in cuts],
            "bands": band_auc,
            "candidate_band_mean": within_band_mean,
        },
        "redundancy": {
            "by_process_col": redundancy,
            "max_abs_spearman": max_abs_rho,
            "is_redundant": is_redundant,
            "threshold": REDUNDANCY_RHO,
        },
        "coverage_cost": coverage_block,
        "gate_components": {
            "coef_excludes_zero_positive": coef_pos,
            "oof_delta_ci_ge_zero_positive": delta_ci_ge_zero,
            "not_redundant": not is_redundant,
            "within_band_positive": within_band_positive,
        },
    }


def _percentile_ci(samples: list[float], ci: float = 0.95) -> tuple[float, float] | None:
    if len(samples) < 20:
        return None
    s = sorted(samples)
    a = (1.0 - ci) / 2.0
    lo = s[int(a * len(s))]
    hi = s[min(len(s) - 1, int((1.0 - a) * len(s)))]
    return (lo, hi)


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------
def scorecard_markdown(card: dict) -> str:
    lb = card["lift_beyond_size"]
    da = card["oof_auc_delta"]
    wb = card["within_band_auc"]
    rd = card["redundancy"]
    cc = card["coverage_cost"]
    coef_ci = lb["coef_ci95"]
    d_ci = da["delta_ci95"]
    lines = []
    lines.append(f"#### `{card['feature_name']}` — **{card['verdict']}**")
    lines.append("")
    lines.append(f"_n={card['n_files']} files · {card['n_positives']} positives · "
                 f"{card['n_repos']} repos · label={card['label']}_")
    lines.append("")
    lines.append("| Gate | Value | Pass |")
    lines.append("|---|---|:--:|")
    lines.append(
        f"| (1) coef beyond size | {lb['candidate_coef']:+.3f} "
        f"CI[{coef_ci[0]:+.3f}, {coef_ci[1]:+.3f}]" if coef_ci else
        f"| (1) coef beyond size | {lb['candidate_coef']:+.3f} CI[n/a]"
    )
    lines[-1] += f" | {'✓' if card['gate_components']['coef_excludes_zero_positive'] else '✗'} |"
    lines.append(
        f"| (2) OOF AUC Δ | {da['auc_base']}→{da['auc_candidate']} "
        f"Δ{_signed(da['delta'])} CI[{_signed(d_ci[0])}, {_signed(d_ci[1])}]" if d_ci else
        f"| (2) OOF AUC Δ | Δ{da['delta']} CI[n/a]"
    )
    lines[-1] += f" | {'✓' if card['gate_components']['oof_delta_ci_ge_zero_positive'] else '✗'} |"
    bands = wb["bands"]
    band_str = " ".join(
        f"{k.split()[0]}={v['candidate_auc']}" for k, v in bands.items()
    )
    lines.append(f"| (3) within-band AUC | mean={wb['candidate_band_mean']} ({band_str}) | "
                 f"{'✓' if card['gate_components'].get('within_band_positive') else '✗'} |")
    lines.append(f"| (4) redundancy | max|ρ|={rd['max_abs_spearman']} | "
                 f"{'✓' if card['gate_components']['not_redundant'] else '✗'} |")
    lines.append(f"| (5) coverage | {cc['coverage_fraction']:.0%} "
                 f"({cc['computable_files']}/{cc['corpus_files']}) | — |")
    lines.append("")
    # within-band shipped reference
    ship_str = " ".join(
        f"{k.split()[0]}={v['shipped_auc']}" for k, v in bands.items()
    )
    lines.append(f"_within-band shipped-risk reference (the wall): {ship_str}_")
    lines.append("")
    # redundancy detail
    lines.append("_redundancy Spearman vs process cols: " +
                 ", ".join(f"{pc}={v['spearman']}" for pc, v in rd["by_process_col"].items()) + "_")
    if cc.get("cost_note"):
        lines.append("")
        lines.append(f"_cost: {cc['cost_note']}_")
    return "\n".join(lines)


def _signed(v) -> str:
    return f"{v:+.4f}" if isinstance(v, (int, float)) else str(v)


# --------------------------------------------------------------------------
# CLI (standalone column file)
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--column", type=Path, required=True,
                    help="JSON file: {repo: {file_path: value}}")
    ap.add_argument("--name", required=True, help="feature name")
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--cost-note", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    column = json.loads(args.column.read_text())
    card = evaluate_candidate(
        column, args.name, results_dir=args.results_dir, config_path=args.config,
        label=args.label, cost_note=args.cost_note,
    )
    print(scorecard_markdown(card))
    if args.out:
        args.out.write_text(json.dumps(card, indent=2))
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
