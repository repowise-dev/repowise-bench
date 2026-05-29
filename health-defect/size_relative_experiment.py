#!/usr/bin/env python3
"""EXPERIMENT (not shipped) — would size-relative scoring fix the small-file bands?

``error_analysis.py`` showed the shipped score's within-NLOC-band AUC collapses
on small/medium files (Q2 = 29-68 LOC inverts to ~0.49) because the score is
partly a size proxy: it ranks big complex files well and is near-blind to small
ones. This script tests, over the cached benchmark (all repos, NO re-index),
whether re-shaping the score to be **size-relative** — "is this file riskier than
its size predicts?" rather than "is this file big and complex?" — recovers the
small bands, and what it costs the large bands and the headline metrics.

This is a research probe for a possible future size-aware scoring track. It is
deliberately NOT a shippable change: the runtime per-finding ``health_impact``
attribution needs a linear, deterministic deduction (see the plan's §4); a
size-band z-score / residual is a non-linear, corpus-relative transform that
breaks that contract. We only want to know if the *signal* is there.

Risk variants compared (all derived from the shipped 10 - health_score):
  * shipped       — the product score as-is (baseline).
  * size_partial  — residual of risk regressed on log1p(NLOC), pooled over the
                    corpus: a single global "subtract the size effect" term.
  * size_partial_repo — same, but the size regression is fit per repo (a stronger
                    correction that a global runtime term could not do).
  * band_zscore   — risk z-scored within its NLOC quartile (pure size-stratified).
  * band_rank     — within-NLOC-quartile percentile rank of risk.

Reported per variant: corpus pooled AUC, mean per-repo AUC, mean Popt, mean
Precision@20%LOC, and per-NLOC-band AUC; plus bootstrap 95% CIs on the corpus
ΔAUC and per-repo-mean ΔPopt vs shipped.

Run (venv python):
    ../../.venv/Scripts/python.exe size_relative_experiment.py [--label keyword]
        [--repos clap,pydantic,hono,gin]   # subset; default = all config repos
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import yaml

import error_analysis as ea  # loaders / banding
from lib.stats import effort_aware_at_loc, popt, roc_auc  # type: ignore

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"


def _shim(joined, risk):
    return [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]


def _auc(joined, risk):
    s = _shim(joined, risk)
    if len({1 if d["defect_count"] > 0 else 0 for d in s}) < 2:
        return None
    return roc_auc(s)["auc"]


def _popt(joined, risk):
    return (popt(_shim(joined, risk)) or {}).get("popt")


def _prec20(joined, risk):
    return effort_aware_at_loc(_shim(joined, risk), 0.20)["precision"]


def size_partial(risk, nloc):
    """Residual of risk on log1p(NLOC) — global continuous size correction."""
    x = np.log1p(np.asarray(nloc, float))
    y = np.asarray(risk, float)
    if np.std(x) < 1e-9:
        return list(y)
    b, a = np.polyfit(x, y, 1)
    return list(y - (a + b * x))


def band_transform(rows_risk, nloc, cuts, mode):
    """z-score or percentile-rank of risk within each NLOC quartile."""
    bands = [ea.band_of(int(n), cuts) for n in nloc]
    out = [0.0] * len(rows_risk)
    for b in set(bands):
        idx = [i for i, bb in enumerate(bands) if bb == b]
        vals = np.array([rows_risk[i] for i in idx], float)
        if mode == "zscore":
            mu, sd = vals.mean(), vals.std()
            adj = (vals - mu) / sd if sd > 1e-9 else np.zeros_like(vals)
        else:  # rank → [0,1] percentile
            order = vals.argsort()
            ranks = np.empty_like(order, float)
            ranks[order] = np.arange(len(vals))
            adj = ranks / max(len(vals) - 1, 1)
        for k, i in enumerate(idx):
            out[i] = float(adj[k])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--repos", default="", help="comma list; default = all config repos")
    ap.add_argument("--out", type=Path, default=RESULTS / "size_relative_experiment.json")
    args = ap.parse_args()

    langs, roots = ea.load_config_langs(HERE / "config.yaml")
    if args.repos:
        keep = set(args.repos.split(","))
        langs = {k: v for k, v in langs.items() if k in keep}
    rows = ea.build_rows(RESULTS, langs, roots, label=args.label)
    cuts = ea.nloc_quartiles(rows)
    band_labels = [f"Q1 (<= {cuts[0]:.0f})", f"Q2 (<= {cuts[1]:.0f})",
                   f"Q3 (<= {cuts[2]:.0f})", f"Q4 (> {cuts[2]:.0f})"]

    # Group rows by repo (per-repo metrics + per-repo size correction).
    by_repo: dict[str, list] = {}
    for r in rows:
        by_repo.setdefault(r["repo"], []).append(r)

    def variant_risk(name):
        """Return {repo: [risk,...]} for a variant."""
        out = {}
        # global size_partial needs corpus-pooled fit
        if name == "size_partial":
            allrisk = size_partial([r["risk"] for r in rows], [r["nloc"] for r in rows])
            pos = 0
            for repo, rs in by_repo.items():
                out[repo] = allrisk[pos:pos + len(rs)]
                pos += len(rs)
            return out
        for repo, rs in by_repo.items():
            risk = [r["risk"] for r in rs]
            nloc = [r["nloc"] for r in rs]
            if name == "shipped":
                out[repo] = risk
            elif name == "size_partial_repo":
                out[repo] = size_partial(risk, nloc)
            elif name == "band_zscore":
                out[repo] = band_transform(risk, nloc, cuts, "zscore")
            elif name == "band_rank":
                out[repo] = band_transform(risk, nloc, cuts, "rank")
        return out

    variants = ["shipped", "size_partial", "size_partial_repo", "band_zscore", "band_rank"]

    def corpus_auc(rbr, band=None):
        s = []
        for repo, rs in by_repo.items():
            for d, rk in zip(rs, rbr[repo]):
                if band is None or ea.band_of(d["nloc"], cuts) == band:
                    s.append({"defect_count": d["defect_count"], "health_score": 10.0 - rk})
        if len({1 if d["defect_count"] > 0 else 0 for d in s}) < 2:
            return None
        return roc_auc(s)["auc"]

    print(f"\n=== size-relative scoring experiment "
          f"({len(by_repo)} repos, {len(rows)} files, "
          f"{sum(r['y'] for r in rows)} pos, label={args.label}) ===")
    print(f"NLOC quartile cuts: {[round(c) for c in cuts]}\n")
    print(f"{'variant':18s} {'corpAUC':>8s} {'mAUC':>6s} {'mPopt':>6s} {'mP@20':>6s}  "
          f"{'Q1':>5s} {'Q2':>5s} {'Q3':>5s} {'Q4':>5s}")

    results = {}
    risk_cache = {}
    for v in variants:
        rbr = variant_risk(v)
        risk_cache[v] = rbr
        per_auc, per_popt, per_p20 = [], [], []
        for repo, rs in by_repo.items():
            j = [{"defect_count": d["defect_count"], "nloc": d["nloc"]} for d in rs]
            npos = sum(1 for d in rs if d["y"])
            if 0 < npos < len(rs):
                per_auc.append(_auc(j, rbr[repo]))
                per_popt.append(_popt(j, rbr[repo]))
                per_p20.append(_prec20(j, rbr[repo]))
        m = lambda xs: round(float(np.mean([x for x in xs if x is not None])), 4) if xs else None  # noqa: E731
        corp = corpus_auc(rbr)
        bands = {b: corpus_auc(rbr, b) for b in band_labels}
        results[v] = {"corpus_auc": corp, "mean_auc": m(per_auc), "mean_popt": m(per_popt),
                      "mean_prec20": m(per_p20), "band_auc": bands}
        bn = [bands[b] for b in band_labels]
        print(f"{v:18s} {corp:8.3f} {m(per_auc):>6} {m(per_popt):>6} {m(per_p20):>6}  " +
              " ".join(f"{(x if x is not None else float('nan')):5.3f}" for x in bn))

    # Bootstrap CIs vs shipped: corpus ΔAUC + per-repo-mean ΔPopt.
    print("\n--- bootstrap 95% CI vs shipped (resample files within repo) ---")
    rng = random.Random(7)
    base = risk_cache["shipped"]
    for v in variants:
        if v == "shipped":
            continue
        treat = risk_cache[v]
        dauc, dpopt = [], []
        for _ in range(400):
            shim_a, shim_b = [], []
            popt_a, popt_b = [], []
            for repo, rs in by_repo.items():
                n = len(rs)
                idx = [rng.randrange(n) for _ in range(n)]
                ja = [{"defect_count": rs[i]["defect_count"], "nloc": rs[i]["nloc"]} for i in idx]
                ra = [base[repo][i] for i in idx]
                rb = [treat[repo][i] for i in idx]
                shim_a += _shim(ja, ra)
                shim_b += _shim(ja, rb)
                pa, pb = _popt(ja, ra), _popt(ja, rb)
                if pa is not None and pb is not None:
                    popt_a.append(pa)
                    popt_b.append(pb)
            try:
                dauc.append(roc_auc(shim_b)["auc"] - roc_auc(shim_a)["auc"])
            except Exception:
                pass
            if popt_a:
                dpopt.append(float(np.mean(popt_b)) - float(np.mean(popt_a)))
        def ci(xs):  # noqa: E306
            xs = sorted(xs)
            return (round(float(np.mean(xs)), 4), round(xs[int(0.025 * len(xs))], 4),
                    round(xs[int(0.975 * len(xs))], 4)) if xs else (None, None, None)
        ca, cp = ci(dauc), ci(dpopt)
        results[v]["delta_corpus_auc_ci"] = ca
        results[v]["delta_popt_ci"] = cp
        print(f"  {v:18s} Δcorpus AUC {ca[0]:+.4f} [{ca[1]:+.4f},{ca[2]:+.4f}]   "
              f"Δmean Popt {cp[0]:+.4f} [{cp[1]:+.4f},{cp[2]:+.4f}]")

    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
