#!/usr/bin/env python3
"""EXPERIMENT — apples-to-apples: does the SHIPPED score gain AUC if each
biomarker contributes a CONTINUOUS magnitude deduction instead of the 4-bin
severity ladder, holding weights + category caps + deduction range fixed?

Motivation. The continuous-FEATURE ablation (`coverage_gradient_experiment.py`
style: a logistic on log-magnitude columns) reaches pooled OOF AUC 0.744 vs 0.693
for binary hits (+0.051). But that +0.051 is logistic-vs-logistic — it is NOT the
shipped score, which already sits at ~0.737 because its 4-level severity ladder
(low/medium/high/critical -> 0.3/0.7/1.2/2.0) is itself a coarse magnitude
encoding plus calibrated weights + caps. The honest question an investor diligence
team would ask: if you relax the linear/4-bin constraint INSIDE the product scorer,
how much does the *product* AUC actually move? This script answers it directly.

Method (cache only, no re-index, deterministic):
  1. Reimplement the shipped file scorer from cached findings:
       score = max(1.0, 10 - sum_categories min(cap, sum_findings weight[bm]*ded))
     - BINARY arm: ded = severity_deduction[severity]  (reproduces shipped score).
     - CONTINUOUS arm: for the 17 magnitude biomarkers, ded scales with the
       finding's own magnitude (CCN, nesting, entropy, scatter, ...), mapped
       monotonically into the SAME [0.3, 2.0] range via the corpus ECDF of that
       biomarker's firing magnitudes; non-magnitude biomarkers keep the discrete
       severity deduction. Weights and category caps are identical to the product.
     Pinning the range to [0.3, 2.0] isolates SHAPE (continuous vs 4-bin), not
     deduction inflation — the only thing that changes is within-biomarker gradient.
  2. Score every file both ways; compute per-repo ROC AUC, the cross-project mean
     (the 0.737 headline estimator) and the pooled AUC, for: cached health_score
     (reference), reimplemented-binary (fidelity check), and continuous.
  3. Repo-cluster bootstrap (resample repos, then files) of the paired
     continuous - cached AUC delta, two-sided p.

Run (venv python):
    ../../.venv/Scripts/python.exe continuous_scoring_experiment.py
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

import error_analysis as ea
from lib.stats import popt, roc_auc  # type: ignore

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"

# --- shipped scoring constants (mirror analysis/health/scoring.py) ---
SEV_DED = {"low": 0.3, "medium": 0.7, "high": 1.2, "critical": 2.0}
D_MIN, D_MAX = 0.3, 2.0  # continuous deductions pinned to the severity range
CAPS = {
    "organizational": 3.5, "structural_complexity": 2.5, "test_coverage": 2.0,
    "test_coverage_gradient": 2.0, "size_and_complexity": 1.5, "duplication": 1.0,
    "test_quality": 0.5, "error_handling": 0.5,
}
WEIGHT = {
    "co_change_scatter": 1.8, "change_entropy": 1.51, "ownership_risk": 1.38,
    "nested_complexity": 1.34, "complex_conditional": 1.33, "large_method": 1.25,
    "complex_method": 1.21, "function_hotspot": 1.16, "god_class": 1.13,
    "prior_defect": 1.0, "untested_hotspot": 1.3, "churn_risk": 1.2,
    "code_age_volatility": 1.1, "developer_congestion": 0.5, "low_cohesion": 0.5,
    "brain_method": 0.5, "bumpy_road": 0.5, "primitive_obsession": 0.5,
    "dry_violation": 0.5, "knowledge_loss": 0.4,
}
CATEGORY = {
    "brain_method": "structural_complexity", "low_cohesion": "structural_complexity",
    "god_class": "structural_complexity", "nested_complexity": "structural_complexity",
    "bumpy_road": "structural_complexity", "complex_conditional": "structural_complexity",
    "complex_method": "size_and_complexity", "large_method": "size_and_complexity",
    "primitive_obsession": "size_and_complexity", "dry_violation": "duplication",
    "untested_hotspot": "test_coverage", "coverage_gap": "test_coverage",
    "developer_congestion": "organizational", "knowledge_loss": "organizational",
    "hidden_coupling": "organizational", "function_hotspot": "organizational",
    "code_age_volatility": "organizational", "ownership_risk": "organizational",
    "churn_risk": "organizational", "change_entropy": "organizational",
    "co_change_scatter": "organizational", "prior_defect": "organizational",
    "large_assertion_block": "test_quality", "duplicated_assertion_block": "test_quality",
}
# magnitude key per biomarker (mirrors coverage_gradient_experiment._CONT)
CONT_KEY = {
    "brain_method": "ccn", "bumpy_road": "bumps", "change_entropy": "change_entropy",
    "churn_risk": "relative_churn", "co_change_scatter": "scatter",
    "complex_conditional": "operator_count", "complex_method": "cognitive",
    "dry_violation": "duplication_pct", "function_hotspot": "modification_count",
    "god_class": "method_count", "large_assertion_block": "assertion_count",
    "large_method": "nloc", "low_cohesion": "lcom4", "nested_complexity": "max_nesting",
    "ownership_risk": "minor_contributors", "primitive_obsession": "param_count",
    "prior_defect": "prior_defect_count",
}


def _w(bm: str) -> float:
    return WEIGHT.get(bm, 1.0)


def load_findings(repo: str) -> list[dict]:
    p = RESULTS / f"health_defect_{repo}" / "health_scores.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("findings", [])


def build_ecdf(findings_by_repo: dict[str, list[dict]]) -> dict[str, list[float]]:
    """Sorted firing-magnitude list per magnitude biomarker (corpus-wide)."""
    pool: dict[str, list[float]] = defaultdict(list)
    for findings in findings_by_repo.values():
        for f in findings:
            bm = f.get("biomarker_type")
            key = CONT_KEY.get(bm)
            if not key:
                continue
            try:
                v = float((f.get("details") or {}).get(key))
            except (TypeError, ValueError):
                continue
            if v > 0:
                pool[bm].append(v)
    return {bm: sorted(vs) for bm, vs in pool.items()}


def ecdf_norm(sorted_vals: list[float], v: float) -> float:
    """Fraction of corpus firings <= v, in [0,1] (midrank for ties-free)."""
    if not sorted_vals:
        return 0.5
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= v:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_vals)


def score_file(findings: list[dict], ecdf: dict[str, list[float]], *, continuous: bool) -> float:
    cat: dict[str, float] = defaultdict(float)
    for f in findings:
        bm = f.get("biomarker_type")
        c = CATEGORY.get(bm)
        if c is None:
            continue  # governance / coverage_gradient / error_handling not in cache roster
        sev = str(f.get("severity", "low")).strip().lower()
        base = SEV_DED.get(sev, 0.3)
        if continuous and bm in CONT_KEY:
            key = CONT_KEY[bm]
            try:
                mag = float((f.get("details") or {}).get(key))
            except (TypeError, ValueError):
                mag = None
            if mag is not None and mag > 0:
                base = D_MIN + (D_MAX - D_MIN) * ecdf_norm(ecdf.get(bm, []), mag)
        cat[c] += _w(bm) * base
    total = sum(min(CAPS.get(c, 1.0), d) for c, d in cat.items())
    return max(1.0, 10.0 - total)


def per_repo_auc(corpus: dict[str, list[dict]], key: str) -> dict[str, float]:
    out = {}
    for repo, rows in corpus.items():
        joined = [{"health_score": r[key], "defect_count": r["defect_count"], "nloc": r["nloc"]} for r in rows]
        y = [1 if d["defect_count"] > 0 else 0 for d in joined]
        if 0 < sum(y) < len(y):
            out[repo] = roc_auc(joined)["auc"]
    return out


def pooled_auc(corpus: dict[str, list[dict]], key: str) -> float:
    joined = [{"health_score": r[key], "defect_count": r["defect_count"], "nloc": r["nloc"]}
              for rows in corpus.values() for r in rows]
    return roc_auc(joined)["auc"]


def pooled_popt(corpus: dict[str, list[dict]], key: str) -> float | None:
    joined = [{"health_score": r[key], "defect_count": r["defect_count"], "nloc": r["nloc"]}
              for rows in corpus.values() for r in rows]
    return (popt(joined) or {}).get("popt")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", type=Path, default=RESULTS / "continuous_scoring_experiment.json")
    args = ap.parse_args()

    langs, roots = ea.load_config_langs(HERE / "config.yaml")
    findings_by_repo = {repo: load_findings(repo) for repo in langs}
    ecdf = build_ecdf(findings_by_repo)

    corpus: dict[str, list[dict]] = {}
    for repo, lang in langs.items():
        rows = ea.build_rows(RESULTS, {repo: lang}, roots, label=args.label)
        if not rows:
            continue
        # index findings by normalized file path
        f_by_file: dict[str, list[dict]] = defaultdict(list)
        for f in findings_by_repo.get(repo, []):
            f_by_file[ea._norm(f.get("file_path", ""))].append(f)
        out_rows = []
        for r in rows:
            ff = f_by_file.get(ea._norm(r["file_path"]), [])
            out_rows.append({
                "file_path": r["file_path"], "nloc": r["nloc"], "defect_count": r["defect_count"],
                "cached": float(r["health_score"]),
                "bin": score_file(ff, ecdf, continuous=False),
                "cont": score_file(ff, ecdf, continuous=True),
            })
        corpus[repo] = out_rows

    n_files = sum(len(v) for v in corpus.values())
    n_pos = sum(1 for v in corpus.values() for r in v if r["defect_count"] > 0)
    print(f"\n=== continuous vs 4-bin product scoring (label={args.label}) ===")
    print(f"corpus: {len(corpus)} repos, {n_files} files, {n_pos} positives\n")

    def summarize(key: str) -> tuple[float, float, float | None]:
        pr = per_repo_auc(corpus, key)
        cpm = float(np.mean(list(pr.values()))) if pr else float("nan")
        return cpm, pooled_auc(corpus, key), pooled_popt(corpus, key)

    print(f"{'scorer':28s} {'x-proj mean AUC':>15s} {'pooled AUC':>11s} {'pooled Popt':>12s}")
    rep = {}
    for key, name in (("cached", "cached health_score (ref)"),
                      ("bin", "reimpl BINARY (4-bin sev)"),
                      ("cont", "reimpl CONTINUOUS (magn.)")):
        cpm, pool, pp = summarize(key)
        rep[key] = {"x_proj_mean_auc": cpm, "pooled_auc": pool, "pooled_popt": pp}
        pps = f"{pp:.4f}" if pp is not None else "  n/a"
        print(f"{name:28s} {cpm:15.4f} {pool:11.4f} {pps:>12s}")

    # Fidelity: reimplemented binary vs cached (should be ~equal).
    fid = rep["bin"]["x_proj_mean_auc"] - rep["cached"]["x_proj_mean_auc"]
    print(f"\nfidelity check (reimpl BINARY - cached x-proj mean AUC): {fid:+.4f}"
          f"   {'OK' if abs(fid) < 0.01 else 'CHECK'}")

    # Paired repo-cluster bootstrap of the CONTINUOUS - CACHED delta (x-proj mean AUC).
    names = list(corpus)
    rng = random.Random(args.seed)
    deltas_cpm, deltas_pool = [], []
    for _ in range(args.n_boot):
        drawn = [names[rng.randrange(len(names))] for _ in range(len(names))]
        boot: dict[str, list[dict]] = {}
        for i, rp in enumerate(drawn):
            rows = corpus[rp]
            idx = [rng.randrange(len(rows)) for _ in range(len(rows))]
            boot[f"{rp}#{i}"] = [rows[j] for j in idx]
        prc = per_repo_auc(boot, "cont")
        prk = per_repo_auc(boot, "cached")
        common = set(prc) & set(prk)
        if common:
            deltas_cpm.append(float(np.mean([prc[r] for r in common]))
                              - float(np.mean([prk[r] for r in common])))
        try:
            deltas_pool.append(pooled_auc(boot, "cont") - pooled_auc(boot, "cached"))
        except Exception:
            pass

    def ci(xs: list[float]) -> dict:
        xs = sorted(xs)
        if not xs:
            return {"mean": None, "lo": None, "hi": None, "p_two_sided": None}
        mean = float(np.mean(xs))
        lo = xs[int(0.025 * len(xs))]
        hi = xs[min(len(xs) - 1, int(0.975 * len(xs)))]
        # two-sided bootstrap p that delta == 0
        frac_le0 = sum(1 for x in xs if x <= 0) / len(xs)
        p = 2 * min(frac_le0, 1 - frac_le0)
        return {"mean": round(mean, 4), "lo": round(lo, 4), "hi": round(hi, 4),
                "p_two_sided": round(p, 4)}

    c_cpm, c_pool = ci(deltas_cpm), ci(deltas_pool)
    print(f"\npaired delta (continuous - cached), repo-cluster bootstrap, {args.n_boot} reps:")
    print(f"  x-proj mean AUC  delta {c_cpm['mean']:+.4f} [{c_cpm['lo']:+.4f}, {c_cpm['hi']:+.4f}]  p={c_cpm['p_two_sided']}")
    print(f"  pooled AUC       delta {c_pool['mean']:+.4f} [{c_pool['lo']:+.4f}, {c_pool['hi']:+.4f}]  p={c_pool['p_two_sided']}")

    out = {
        "label": args.label, "n_repos": len(corpus), "n_files": n_files, "n_positives": n_pos,
        "scorers": rep, "fidelity_reimpl_binary_minus_cached_cpm_auc": round(fid, 4),
        "paired_delta_continuous_minus_cached": {"x_proj_mean_auc": c_cpm, "pooled_auc": c_pool},
        "note": "Continuous deductions pinned to the [0.3,2.0] severity range via corpus "
                "ECDF, so only within-biomarker gradient changes (not deduction scale).",
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
