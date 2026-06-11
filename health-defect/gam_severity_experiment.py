#!/usr/bin/env python3
"""gam_severity_experiment.py — monotone severity SHAPE functions vs the flat
per-tier ``_SEVERITY_DEDUCTION`` lookup (the Phase-5 Part-B ship-iff probe).

RESEARCH ARTIFACT (bench-only). The shipped file health score deducts a flat
amount per severity tier — CCN=30 deducts the same as CCN=9. This asks whether
replacing that flat tier with a *learned monotone shape* on each biomarker's
**continuous magnitude** (max_ccn, max_nesting, change_entropy value,
duplication_pct, prior_defect_count, …) improves cross-project defect ranking.

The shape we fit is **isotonic regression** (monotone by construction, direction
auto-detected per biomarker) — the nonparametric equivalent of an EBM/pyGAM term
with a monotonicity constraint, and exactly additive + per-feature, so the
runtime per-finding ``health_impact`` attribution contract survives unchanged.

Three designs, identical leave-one-repo-out pooled OOF AUC harness (so the only
thing that moves is the encoding of each biomarker column):

* **base**   — one severity-weighted-hit column per biomarker + log-NLOC
               (the shipped calibration design; reproduces ≈0.746).
* **maglin** — replace each mapped biomarker's column with log1p(max magnitude)
               (a *linear* read of the continuous magnitude; isolates "does the
               magnitude carry more than the tier?" before any shape).
* **mono**   — replace each mapped biomarker's column with an isotonic monotone
               SHAPE of its magnitude, fit on the training fold only (the GAM).

Headline = pooled LOO OOF AUC delta **mono − base**, bootstrap 95% CI
(resample repos → files over fixed OOF predictions). Within-NLOC-band pooled-OOF
AUC reported for base vs mono (the acid test: the shape must not WORSEN the
within-band wall). Both keyword + szz labels.

SHIP IFF delta ≥ +0.04 (point) with CI lower bound ≥ 0 AND within-band not
worsened. Else PARK with the numbers — a clean ablation.

Run (from the bench R&D worktree, ABSOLUTE venv + UTF-8 preamble; dirs → MAIN
bench checkout):
    $env:PYTHONIOENCODING="utf-8"
    C:\\Users\\ragha\\Desktop\\repowise\\.venv\\Scripts\\python.exe \
        gam_severity_experiment.py \
        --results-dir C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\results \
        --out C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\results\\gam_severity_scorecard.json
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

import error_analysis as ea
from candidate_eval import BIOMARKERS, C_FIXED, _percentile_ci, load_corpus

_HERE = Path(__file__).resolve().parent

# Per-biomarker continuous magnitude: the details key whose value scales with
# severity. Direction is auto-detected by the isotonic fit (increasing='auto'),
# so inverse signals (bus_factor, line_coverage_pct — lower is worse) need no
# special-casing. Biomarkers absent here keep their severity-weighted-hit column
# (no continuous magnitude to shape — coverage_gap/hidden_coupling/
# code_age_volatility don't expose one in findings details).
MAGNITUDE_KEY: dict[str, str] = {
    "brain_method": "ccn",
    "bumpy_road": "bumps",
    "change_entropy": "change_entropy",
    "churn_risk": "relative_churn",
    "co_change_scatter": "scatter",
    "complex_conditional": "operator_count",
    "complex_method": "ccn",
    "developer_congestion": "contributor_count",
    "dry_violation": "duplication_pct",
    "duplicated_assertion_block": "assertion_lines",
    "function_hotspot": "modification_count",
    "god_class": "method_count",
    "knowledge_loss": "bus_factor",          # decreasing
    "large_assertion_block": "assertion_count",
    "large_method": "nloc",
    "low_cohesion": "lcom4",
    "nested_complexity": "max_nesting",
    "ownership_risk": "minor_contributors",
    "primitive_obsession": "param_count",
    "prior_defect": "prior_defect_count",
    "untested_hotspot": "line_coverage_pct",  # decreasing
}

_SEV_WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0,
               "1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0}


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def _sev_weight(sev) -> float:
    return _SEV_WEIGHT.get(str(sev).strip().lower(), 1.0)


def load_magnitudes(results_dir: Path, langs: dict[str, str]) -> dict[tuple[str, str], dict[str, float]]:
    """``{(repo, file_path): {biomarker: max magnitude}}`` from cached findings.

    Per file, the MAX magnitude across that biomarker's findings (a file's worst
    instance drives its tier today, so the max is the faithful continuous analog
    of the discrete severity it would otherwise get). Absent ⇒ the biomarker did
    not fire ⇒ left out (encoded as 0 downstream, exactly as the severity-
    weighted-hit column treats a non-firing biomarker)."""
    out: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for repo in langs:
        hp = results_dir / f"health_defect_{repo}" / "health_scores.json"
        if not hp.exists():
            continue
        health = json.loads(hp.read_text(encoding="utf-8"))
        agg: dict[tuple[str, str], float] = {}
        for f in health.get("findings", []):
            bt = f.get("biomarker_type")
            key = MAGNITUDE_KEY.get(bt)
            if key is None:
                continue
            details = f.get("details") or {}
            val = details.get(key)
            if val is None:
                continue
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            fp = _norm(f.get("file_path", ""))
            k = (repo, fp, bt)
            agg[k] = max(agg.get(k, v), v) if k in agg else v
        for (r, fp, bt), v in agg.items():
            out[(r, fp)][bt] = v
    return out


def _sevhit(row: dict, bt: str) -> float:
    return float((row.get("biomarkers") or {}).get(bt, 0.0))


def build_columns(rows, magnitudes):
    """Per design, build the per-biomarker column dict {design: {bt: np.array}}.

    Returns (sevhit, mag) where each is {bt: array over rows}. ``mag`` holds the
    raw magnitude (0 where the biomarker didn't fire / has no magnitude key).
    """
    n = len(rows)
    sevhit = {bt: np.array([_sevhit(r, bt) for r in rows], dtype=float) for bt in BIOMARKERS}
    mag = {}
    for bt in BIOMARKERS:
        if bt not in MAGNITUDE_KEY:
            continue
        col = np.zeros(n, dtype=float)
        for i, r in enumerate(rows):
            mv = magnitudes.get((r["repo"], r["file_path"]), {})
            if bt in mv:
                col[i] = mv[bt]
        mag[bt] = col
    return sevhit, mag


def design_matrix(rows, sevhit, mag, kind, *, iso_models=None, fit_iso=False):
    """Assemble the design matrix for one of {base, maglin, mono}.

    For ``mono``, ``iso_models`` is a dict {bt: IsotonicRegression}; when
    ``fit_iso`` is True they are fit on the passed rows (training fold) against
    ``y`` (read from rows) and stored, else applied (test fold).
    """
    nloc_log = np.array([float(np.log1p(max(r["nloc"], 0))) for r in rows])
    cols = []
    feats = []
    for bt in BIOMARKERS:
        if kind == "base" or bt not in MAGNITUDE_KEY:
            cols.append(sevhit[bt])
            feats.append(bt + "_sevhit")
            continue
        m = mag[bt]
        if kind == "maglin":
            cols.append(np.log1p(np.maximum(m, 0.0)))
            feats.append(bt + "_logmag")
        elif kind == "mono":
            if fit_iso:
                y = np.array([r["y"] for r in rows], dtype=int)
                iso = IsotonicRegression(increasing="auto", out_of_bounds="clip")
                # Degenerate guard: needs ≥2 distinct magnitudes + both classes.
                if len(np.unique(m)) < 2 or len(set(y)) < 2:
                    iso = None
                else:
                    iso.fit(m, y)
                iso_models[bt] = iso
                shaped = iso.predict(m) if iso is not None else m
            else:
                iso = (iso_models or {}).get(bt)
                shaped = iso.predict(m) if iso is not None else m
            cols.append(np.asarray(shaped, dtype=float))
            feats.append(bt + "_mono")
    cols.append(nloc_log)
    feats.append("nloc_log")
    return np.column_stack(cols), feats


def oof_predictions(rows, sevhit, mag, kind):
    """Leave-one-repo-out OOF P(defect) for one design. Isotonic shapes (mono)
    are fit on the training fold only — no leakage."""
    y = np.array([r["y"] for r in rows], dtype=int)
    groups = np.array([r["repo"] for r in rows])
    oof = np.full(len(y), np.nan)
    logo = LeaveOneGroupOut()
    for tr, te in logo.split(np.zeros(len(y)), y, groups):
        tr_rows = [rows[i] for i in tr]
        te_rows = [rows[i] for i in te]
        tr_sev = {bt: sevhit[bt][tr] for bt in sevhit}
        te_sev = {bt: sevhit[bt][te] for bt in sevhit}
        tr_mag = {bt: mag[bt][tr] for bt in mag}
        te_mag = {bt: mag[bt][te] for bt in mag}
        iso_models: dict = {}
        Xtr, _ = design_matrix(tr_rows, tr_sev, tr_mag, kind,
                               iso_models=iso_models, fit_iso=True)
        Xte, _ = design_matrix(te_rows, te_sev, te_mag, kind,
                               iso_models=iso_models, fit_iso=False)
        scaler = StandardScaler().fit(Xtr)
        clf = LogisticRegression(penalty="l2", C=C_FIXED, class_weight="balanced",
                                 max_iter=5000).fit(scaler.transform(Xtr), y[tr])
        oof[te] = clf.predict_proba(scaler.transform(Xte))[:, 1]
    return oof


def pooled_auc(y, score) -> float:
    if len(set(int(v) for v in y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, score))


def within_band_oof_auc(rows, oof):
    cuts = ea.nloc_quartiles(rows)
    labels = [f"Q1 (<= {cuts[0]:.0f})", f"Q2 (<= {cuts[1]:.0f})",
              f"Q3 (<= {cuts[2]:.0f})", f"Q4 (> {cuts[2]:.0f})"]
    y = np.array([r["y"] for r in rows])
    out = {}
    for bl in labels:
        idx = [i for i, r in enumerate(rows) if ea.band_of(r["nloc"], cuts) == bl]
        yy = y[idx]
        a = pooled_auc(yy, oof[idx]) if len(idx) else float("nan")
        out[bl] = {"n": len(idx), "pos": int(yy.sum()), "auc": None if a != a else round(a, 4)}
    vals = [v["auc"] for v in out.values() if v["auc"] is not None]
    return out, (round(float(np.mean(vals)), 4) if vals else None)


def boot_delta_ci(rows, oof_a, oof_b, *, n_boot=1000, seed=12345):
    """Bootstrap CI on pooled-AUC delta (b - a) over fixed OOF preds:
    resample repos → files within repo (matches candidate_eval)."""
    y = np.array([r["y"] for r in rows], dtype=int)
    groups = np.array([r["repo"] for r in rows])
    repos = sorted(set(groups))
    idx_by_repo = {g: np.where(groups == g)[0] for g in repos}
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_boot):
        chosen = [repos[rng.randrange(len(repos))] for _ in repos]
        parts = []
        for g in chosen:
            ridx = idx_by_repo[g]
            gen = np.random.default_rng(rng.randrange(1 << 30))
            parts.append(ridx[gen.integers(0, len(ridx), len(ridx))])
        idx = np.concatenate(parts)
        yb = y[idx]
        if len(set(int(v) for v in yb)) < 2:
            continue
        aa = pooled_auc(yb, oof_a[idx])
        ab = pooled_auc(yb, oof_b[idx])
        if aa == aa and ab == ab:
            deltas.append(ab - aa)
    return _percentile_ci(deltas), len(deltas)


def run_label(rows, magnitudes, label):
    sevhit, mag = build_columns(rows, magnitudes)
    y = [r["y"] for r in rows]
    oof = {k: oof_predictions(rows, sevhit, mag, k) for k in ("base", "maglin", "mono")}
    aucs = {k: round(pooled_auc(np.array(y), v), 4) for k, v in oof.items()}
    band = {k: within_band_oof_auc(rows, v) for k, v in oof.items()}
    out = {
        "label": label,
        "n_files": len(rows),
        "n_positives": int(sum(y)),
        "n_repos": len(set(r["repo"] for r in rows)),
        "n_magnitude_biomarkers": len([b for b in BIOMARKERS if b in MAGNITUDE_KEY]),
        "pooled_oof_auc": aucs,
        "within_band": {k: {"bands": band[k][0], "mean": band[k][1]} for k in band},
    }
    for cand in ("maglin", "mono"):
        ci, nb = boot_delta_ci(rows, oof["base"], oof[cand])
        delta = round(aucs[cand] - aucs["base"], 4)
        ships = bool(delta >= 0.04 and ci and ci[0] >= 0
                     and (band[cand][1] or 0) >= (band["base"][1] or 0))
        out[f"delta_{cand}_vs_base"] = {
            "delta": delta,
            "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            "n_boot": nb,
            "within_band_mean_base": band["base"][1],
            "within_band_mean_cand": band[cand][1],
            "within_band_not_worsened": bool((band[cand][1] or 0) >= (band["base"][1] or 0)),
            "ships_at_0.04_bar": ships,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--out", type=Path, default=_HERE.parent / "results" / "gam_severity_scorecard.json")
    args = ap.parse_args()

    langs, _roots = ea.load_config_langs(args.config)
    magnitudes = load_magnitudes(args.results_dir, langs)
    print(f"loaded magnitudes for {len(magnitudes)} files")

    results = {}
    for label in ("keyword", "szz"):
        rows = load_corpus(args.results_dir, args.config, label)
        print(f"\n=== label={label}: {len(rows)} files, {sum(r['y'] for r in rows)} positives ===")
        res = run_label(rows, magnitudes, label)
        results[label] = res
        print(f"  pooled OOF AUC: base={res['pooled_oof_auc']['base']} "
              f"maglin={res['pooled_oof_auc']['maglin']} mono={res['pooled_oof_auc']['mono']}")
        d = res["delta_mono_vs_base"]
        print(f"  mono-base Δ={d['delta']} CI={d['ci95']} "
              f"within-band base={d['within_band_mean_base']} mono={d['within_band_mean_cand']} "
              f"SHIP={d['ships_at_0.04_bar']}")

    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
