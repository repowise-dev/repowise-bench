#!/usr/bin/env python3
"""EXPERIMENT — can an INTERPRETABLE coverage term capture the coverage gradient?

``coverage_gradient_experiment.py`` showed the continuous uncovered fraction is
worth +0.066 pooled AUC as a calibration *feature*. But a logistic feature is not
shippable: the runtime score is a sum of per-finding deductions (each must carry
an attributable ``health_impact`` — plan §4). The question this probe answers: if
we add coverage as a **monotonic, per-file, attributable deduction** — "N health
points off, scaling with the uncovered fraction" — how much of that +0.066 does
the *shipped* score recover? Unlike size-relative scoring, this stays linear and
explainable, so a positive result here is genuinely shippable (as a coverage
biomarker whose severity scales with uncovered %).

Method (cache only, no re-index): over the covered-7 subset (coverage re-attached
from coverage_t0.json), take the shipped risk = 10 - health_score and add
``penalty = min(cap, w * uncovered_frac)`` for files with KNOWN coverage (absent
≠ uncovered → no penalty). Sweep w/cap; report corpus + per-repo AUC and Popt vs
baseline with bootstrap CIs. The penalty is exactly what a runtime coverage
deduction would do to the ranking.

Run (venv python):
    ../../.venv/Scripts/python.exe coverage_scoring_experiment.py
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np

import error_analysis as ea
from lib.stats import popt, roc_auc  # type: ignore

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"


def inject_coverage(repo, rows):
    cov_p = RESULTS / f"health_defect_{repo}" / "coverage_t0.json"
    if not cov_p.exists():
        return 0
    cov = {ea._norm(k): v for k, v in json.loads(cov_p.read_text()).get("files", {}).items()}
    n = 0
    for r in rows:
        c = cov.get(ea._norm(r["file_path"]))
        if c and c.get("line_coverage_pct") is not None:
            r["line_coverage_pct"] = float(c["line_coverage_pct"])
            n += 1
    return n


def _shim(rows, risk):
    return [{"defect_count": d["defect_count"], "nloc": d["nloc"], "health_score": 10.0 - r}
            for d, r in zip(rows, risk)]


def _auc(rows, risk):
    s = _shim(rows, risk)
    if len({1 if d["defect_count"] > 0 else 0 for d in s}) < 2:
        return None
    return roc_auc(s)["auc"]


def _popt(rows, risk):
    return (popt(_shim(rows, risk)) or {}).get("popt")


def penalized_risk(rows, w, cap):
    out = []
    for r in rows:
        base = r["risk"]
        cov = r.get("line_coverage_pct")
        pen = 0.0
        if cov is not None:
            pen = min(cap, w * max(0.0, (100.0 - cov) / 100.0))
        out.append(min(10.0, base + pen))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--out", type=Path, default=RESULTS / "coverage_scoring_experiment.json")
    args = ap.parse_args()

    langs, roots = ea.load_config_langs(HERE / "config.yaml")
    by_repo = {}
    for repo, lang in langs.items():
        rows = ea.build_rows(RESULTS, {repo: lang}, roots, label=args.label)
        if rows and inject_coverage(repo, rows) > 0:
            by_repo[repo] = rows

    nfiles = sum(len(r) for r in by_repo.values())
    npos = sum(rr["y"] for rows in by_repo.values() for rr in rows)
    print(f"\n=== interpretable coverage-penalty scoring (label={args.label}) ===")
    print(f"covered subset: {len(by_repo)} repos, {nfiles} files, {npos} positives\n")

    def corpus_auc(risk_by_repo):
        s = []
        for repo, rows in by_repo.items():
            s += _shim(rows, risk_by_repo[repo])
        return roc_auc(s)["auc"]

    m = lambda xs: round(float(np.mean([x for x in xs if x is not None])), 4) if xs else None  # noqa: E731

    def evaluate(w, cap):
        rbr = {repo: penalized_risk(rows, w, cap) for repo, rows in by_repo.items()}
        per_auc, per_popt = [], []
        for repo, rows in by_repo.items():
            if 0 < sum(r["y"] for r in rows) < len(rows):
                per_auc.append(_auc(rows, rbr[repo]))
                per_popt.append(_popt(rows, rbr[repo]))
        return rbr, corpus_auc(rbr), m(per_auc), m(per_popt)

    base_rbr, base_corp, base_mauc, base_mpopt = evaluate(0.0, 0.0)
    print(f"{'setting':18s} {'corpAUC':>8s} {'mAUC':>6s} {'mPopt':>6s}")
    print(f"{'baseline (w=0)':18s} {base_corp:8.3f} {base_mauc:>6} {base_mpopt:>6}")

    results = {"label": args.label, "subset_repos": list(by_repo),
               "n_files": nfiles, "n_positives": npos,
               "baseline": {"corpus_auc": base_corp, "mean_auc": base_mauc, "mean_popt": base_mpopt},
               "sweep": {}}
    grid = [(w, cap) for cap in (1.5, 2.0, 3.0) for w in (1.0, 2.0, 3.0, 4.0)]
    best = None
    for w, cap in grid:
        rbr, corp, mauc, mpopt = evaluate(w, cap)
        tag = f"w={w} cap={cap}"
        print(f"{tag:18s} {corp:8.3f} {mauc:>6} {mpopt:>6}")
        results["sweep"][tag] = {"corpus_auc": corp, "mean_auc": mauc, "mean_popt": mpopt}
        if best is None or corp > best[1]:
            best = ((w, cap), corp, rbr)

    # Bootstrap CI of corpus ΔAUC + per-repo-mean ΔPopt for the best setting.
    (bw, bcap), _, best_rbr = best
    rng = random.Random(11)
    dauc, dpopt = [], []
    for _ in range(500):
        sa, sb, pa, pb = [], [], [], []
        for repo, rows in by_repo.items():
            n = len(rows)
            idx = [rng.randrange(n) for _ in range(n)]
            sub = [rows[i] for i in idx]
            ra = [base_rbr[repo][i] for i in idx]
            rb = [best_rbr[repo][i] for i in idx]
            sa += _shim(sub, ra)
            sb += _shim(sub, rb)
            va, vb = _popt(sub, ra), _popt(sub, rb)
            if va is not None and vb is not None:
                pa.append(va)
                pb.append(vb)
        try:
            dauc.append(roc_auc(sb)["auc"] - roc_auc(sa)["auc"])
        except Exception:
            pass
        if pa:
            dpopt.append(float(np.mean(pb)) - float(np.mean(pa)))

    def ci(xs):
        xs = sorted(xs)
        return [round(float(np.mean(xs)), 4), round(xs[int(0.025 * len(xs))], 4),
                round(xs[int(0.975 * len(xs))], 4)] if xs else [None, None, None]

    ca, cp = ci(dauc), ci(dpopt)
    print(f"\nbest interpretable penalty: w={bw} cap={bcap}")
    print(f"  Δcorpus AUC {ca[0]:+.4f} [{ca[1]:+.4f},{ca[2]:+.4f}]")
    print(f"  Δmean Popt  {cp[0]:+.4f} [{cp[1]:+.4f},{cp[2]:+.4f}]")
    print(f"  (continuous-feature ceiling for reference: +0.066 AUC)")
    results["best_setting"] = {"w": bw, "cap": bcap,
                               "delta_corpus_auc_ci": ca, "delta_popt_ci": cp,
                               "feature_ceiling_auc": 0.066}
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
