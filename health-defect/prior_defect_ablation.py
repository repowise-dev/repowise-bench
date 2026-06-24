#!/usr/bin/env python3
"""Gate F - prior_defect ablation WITH numbers (R&R panel 2026-06-24, R1/R3/R4).

Re-run the headline (cross-project mean + pooled AUC, partial Spearman vs NLOC)
and the within-band (NLOC-quartile) wall with the ``prior_defect`` biomarker
DROPPED from the calibrated score, then report the removed-feature numbers
side-by-side with the shipped score.

The shipped file score is an additive roster: ``health = max(1, 10 - sum|impact|)``
over the scoring biomarkers (verified exact on the 21-repo canonical corpus:
2826 files, 0 mismatch, max abs error 0.009). Dropping ``prior_defect`` removes
its per-file impact P, raising health by P (re-clamped to [1, 10]):
``health' = max(1, 10 - (S - P))``. We reconstruct S and P from the cached
per-finding ``health_impact`` in ``health_scores.json`` and re-run the existing
headline / within-band machinery on the ablated scores. ``prior_defect`` already
ships at neutral unit weight, so this is the calibrated-model half of the gate-F
argument (the by-construction half is STATE_OF_HEALTH.md:90,198).

Cache-only, deterministic. Seed 12345, n_boot 2000, keyword label, canonical
21-repo corpus (config.yaml minus the cockroach large-repo demonstration), fixed
NLOC cuts 22/48/108 (Sec 4.1).

Usage (venv python):
    PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe prior_defect_ablation.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml

import statistical_rigor as sr
from error_analysis import SCORING_BIOMARKERS, auc, band_of

EXCLUDE = {"cockroach"}          # large-repo demonstration, not in the corpus
SEED = 12345
N_BOOT = 2000
LABEL = "keyword"
CUTS = [22.0, 48.0, 108.0]       # canonical fixed quartile cuts (Sec 4.1)
BANDS = [f"Q1 (<= {CUTS[0]:.0f})", f"Q2 (<= {CUTS[1]:.0f})",
         f"Q3 (<= {CUTS[2]:.0f})", f"Q4 (> {CUTS[2]:.0f})"]

_RESULTS = sr._RESULTS


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def reconstruct_impacts(repo: str) -> tuple[dict[str, float], dict[str, float]]:
    """Return (S, P): total scoring impact and prior_defect impact per file."""
    h = json.loads((_RESULTS / f"health_defect_{repo}" / "health_scores.json").read_text())
    S: dict[str, float] = defaultdict(float)
    P: dict[str, float] = defaultdict(float)
    for x in h.get("findings", []):
        bt = x.get("biomarker_type")
        if bt not in SCORING_BIOMARKERS:
            continue
        fp = _norm(x.get("file_path", ""))
        imp = abs(float(x.get("health_impact") or 0.0))
        S[fp] += imp
        if bt == "prior_defect":
            P[fp] += imp
    return S, P


def build_corpora(label: str):
    """Build the shipped and prior_defect-ablated corpora (joined-style rows)."""
    cfg = yaml.safe_load((sr._BENCH / "config.yaml").read_text())
    repos = [r["name"] for r in cfg["repos"] if r["name"] not in EXCLUDE]
    ship: dict[str, list[dict]] = {}
    abl: dict[str, list[dict]] = {}
    recon_err = 0.0
    n_pd = 0
    for repo in repos:
        joined = sr.load_repo(repo, label)
        if joined is None:
            raise SystemExit(f"missing cache for {repo}")
        S, P = reconstruct_impacts(repo)
        ship_rows, abl_rows = [], []
        for d in joined:
            fp = _norm(d["file_path"])
            s, p = S.get(fp, 0.0), P.get(fp, 0.0)
            recon = max(1.0, 10.0 - s)
            recon_err = max(recon_err, abs(recon - float(d["health_score"])))
            abl_health = max(1.0, 10.0 - (s - p))
            ship_rows.append(d)
            abl_rows.append({**d, "health_score": abl_health})
            if p > 0:
                n_pd += 1
        ship[repo] = ship_rows
        abl[repo] = abl_rows
    return ship, abl, repos, recon_err, n_pd


def headline(corpus: dict[str, list[dict]]) -> dict:
    auc_mean = sr.cluster_bootstrap_mean(corpus, sr.auc_metric, n_boot=N_BOOT, seed=SEED)
    auc_pool = sr.pooled_ci(corpus, sr.auc_metric, n_boot=N_BOOT, seed=SEED)
    pr_mean = sr.cluster_bootstrap_mean(corpus, sr.partial_rho_metric, n_boot=N_BOOT, seed=SEED)
    pr_pool = sr.pooled_ci(corpus, sr.partial_rho_metric, n_boot=N_BOOT, seed=SEED)
    return {
        "auc_cross_project_mean": {k: auc_mean[k] for k in ("point", "lo", "hi", "n")},
        "auc_pooled": {k: auc_pool[k] for k in ("point", "lo", "hi", "n")},
        "partial_rho_cross_project_mean": {k: pr_mean[k] for k in ("point", "lo", "hi", "n")},
        "partial_rho_pooled": {k: pr_pool[k] for k in ("point", "lo", "hi", "n")},
    }


def within_band(corpus: dict[str, list[dict]]) -> dict:
    rows = []
    for repo, rs in corpus.items():
        for d in rs:
            rows.append({
                "repo": repo,
                "nloc": int(d.get("nloc", 0) or 0),
                "risk": 10.0 - float(d["health_score"]),
                "y": 1 if int(d.get("defect_count", 0) or 0) > 0 else 0,
            })
    for r in rows:
        r["band"] = band_of(r["nloc"], CUTS)
    pooled = auc([r["y"] for r in rows], [r["risk"] for r in rows])
    out = {"pooled_auc": pooled, "bands": {}}
    for b in BANDS:
        members = [r for r in rows if r["band"] == b]
        a = auc([r["y"] for r in members], [r["risk"] for r in members])
        out["bands"][b] = {
            "n": len(members),
            "positives": sum(r["y"] for r in members),
            "within_auc": a,
        }
    return out


def _f(d: dict) -> str:
    if d.get("point") is None:
        return "n/a"
    if d.get("lo") is None:
        return f"{d['point']:.4f}"
    return f"{d['point']:.4f} [{d['lo']:.4f}, {d['hi']:.4f}]"


def main() -> None:
    ship, abl, repos, recon_err, n_pd = build_corpora(LABEL)
    n_files = sum(len(v) for v in ship.values())
    print(f"Gate F prior_defect ablation | {len(repos)} repos | {n_files} files | "
          f"label={LABEL} | seed={SEED} n_boot={N_BOOT}")
    print(f"reconstruction max abs error vs shipped health_score: {recon_err:.4f} "
          f"(<=0.01 => additive floored model exact)")
    print(f"files carrying a prior_defect finding: {n_pd}\n")

    res = {"corpus_repos": len(repos), "n_files": n_files, "label": LABEL,
           "seed": SEED, "n_boot": N_BOOT, "cuts": CUTS,
           "reconstruction_max_abs_err": recon_err, "files_with_prior_defect": n_pd,
           "shipped": {}, "ablated": {}}

    for name, corpus in (("shipped", ship), ("ablated", abl)):
        h = headline(corpus)
        wb = within_band(corpus)
        res[name]["headline"] = h
        res[name]["within_band"] = wb
        print(f"--- {name} ---")
        print(f"  AUC cross-project mean : {_f(h['auc_cross_project_mean'])}")
        print(f"  AUC pooled             : {_f(h['auc_pooled'])}")
        print(f"  partial-rho mean       : {_f(h['partial_rho_cross_project_mean'])}")
        print(f"  partial-rho pooled     : {_f(h['partial_rho_pooled'])}")
        print(f"  within-band (pooled {wb['pooled_auc']:.4f}):")
        for b in BANDS:
            x = wb["bands"][b]
            wa = x["within_auc"]
            print(f"    {b:14s} n={x['n']:4d} pos={x['positives']:3d} "
                  f"within-AUC={wa:.4f}" if wa is not None else f"    {b} n/a")
        print()

    op = _RESULTS / "prior_defect_ablation.json"
    op.write_text(json.dumps(res, indent=2))
    print(f"Wrote {op}")


if __name__ == "__main__":
    main()
