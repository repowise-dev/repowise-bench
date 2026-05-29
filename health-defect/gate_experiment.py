#!/usr/bin/env python3
"""Evidence for Phase-9 Part-C gate fixes — measured on the SHIPPED score, from
cached findings, with NO re-index/re-walk.

``error_analysis.py`` showed the score's within-NLOC-band AUC collapses on small
files (Q2 = 29-68 LOC inverts to ~0.49), and that the biomarkers firing there
(primitive_obsession, dry_violation, low_cohesion) are anti-predictive on small
files. This script quantifies, for a candidate gate, exactly how much the shipped
health score improves — by re-aggregating each repo's cached findings through the
product's own ``scoring.score_file`` with the candidate biomarker suppressed on
files below an NLOC floor (a faithful proxy for a function-body gate: in small
files functions are small too), then scoring AUC/Popt with the benchmark's own
metrics, corpus-wide and within NLOC quartiles, with bootstrap CIs.

This is the cheap, exact validation the user asked for: a gate change only alters
*which findings fire*, and for a size-floor gate that subset is fully determined
by data already in the cache (file NLOC). No worktree re-walk is required to see
the score effect; a confirmatory benchmark re-run is only needed to capture the
small second-order interaction with category caps on the few files where the
suppressed finding was not the cap-binding one.

Run (venv python):
    ../../.venv/Scripts/python.exe gate_experiment.py [--label keyword]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "repowise-bench" / "health-defect"
RESULTS = ROOT / "repowise-bench" / "results"
sys.path.insert(0, str(BENCH))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    sys.path.insert(0, str(ROOT / p))

from lib.stats import ALL_BIOMARKERS, popt, roc_auc  # noqa: E402

from repowise.core.analysis.health import scoring  # noqa: E402
from repowise.core.analysis.health.biomarkers.base import BiomarkerResult  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402

import error_analysis as ea  # noqa: E402

_REAL = set(ALL_BIOMARKERS)
_SEV = {"low": Severity.LOW, "medium": Severity.MEDIUM,
        "high": Severity.HIGH, "critical": Severity.CRITICAL}


def _sev(s) -> Severity:
    return _SEV.get(str(s).strip().lower(), Severity.MEDIUM)


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def _score(findings: list[dict], *, suppress: str | None, file_nloc: int,
           floor: float) -> float:
    """score_file over cached findings, optionally suppressing one biomarker on a
    file whose NLOC is below ``floor``."""
    results = []
    for f in findings:
        bt = f.get("biomarker_type")
        if bt not in _REAL:
            continue
        if suppress and bt == suppress and file_nloc < floor:
            continue
        results.append(BiomarkerResult(
            biomarker_type=bt, severity=_sev(f.get("severity")),
            function_name=None, line_start=None, line_end=None, details={}, reason="",
        ))
    return scoring.score_file(results)[0] if results else 10.0


def _auc(joined, risk):
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return roc_auc(shim)["auc"]


def _popt(joined, risk):
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return (popt(shim) or {}).get("popt")


def _bootstrap_delta(joined, ra, rb, metric, *, n_boot=600, seed=4242):
    rng = random.Random(seed)
    n = len(joined)
    deltas = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        j = [joined[i] for i in idx]
        try:
            va, vb = metric(j, [ra[i] for i in idx]), metric(j, [rb[i] for i in idx])
        except Exception:
            continue
        if va is not None and vb is not None and va == va and vb == vb:
            deltas.append(vb - va)
    if not deltas:
        return None
    deltas.sort()
    return (float(np.mean(deltas)),
            deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas))])


def load(name, label):
    d = RESULTS / f"health_defect_{name}"
    hp, jp = d / "health_scores.json", d / "joined_data.json"
    if not (hp.exists() and jp.exists()):
        return None
    health, joined = json.loads(hp.read_text()), json.loads(jp.read_text())
    if not joined:
        return None
    if label != "joined":
        lp = d / f"defect_counts_{label}.json"
        if not lp.exists():
            return None
        counts = {_norm(k): v for k, v in json.loads(lp.read_text()).items()}
        for r in joined:
            r["defect_count"] = counts.get(_norm(r["file_path"]), 0)
    by_file: dict[str, list[dict]] = {}
    for f in health.get("findings", []):
        by_file.setdefault(_norm(f["file_path"]), []).append(f)
    return joined, by_file


def _m(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 4) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--out", type=Path, default=RESULTS / "gate_experiment.json")
    args = ap.parse_args()

    allowed = [r["name"] for r in yaml.safe_load((BENCH / "config.yaml").read_text())["repos"]]
    repos = {n: load(n, args.label) for n in allowed}
    repos = {n: v for n, v in repos.items() if v is not None}

    # Pooled rows for band cuts + corpus-pooled AUC.
    all_rows = []
    for name, (joined, by_file) in repos.items():
        for d in joined:
            all_rows.append({"nloc": int(d.get("nloc", 0) or 0)})
    cuts = ea.nloc_quartiles([{"nloc": r["nloc"]} for r in all_rows])
    band_labels = [f"Q1 (<= {cuts[0]:.0f})", f"Q2 (<= {cuts[1]:.0f})",
                   f"Q3 (<= {cuts[2]:.0f})", f"Q4 (> {cuts[2]:.0f})"]

    # Candidate interventions: (label, biomarker, floor). floor=inf ⇒ drop globally.
    interventions = [
        ("baseline", None, 0.0),
        ("drop primitive_obsession <file40", "primitive_obsession", 40),
        ("drop primitive_obsession <file50", "primitive_obsession", 50),
        ("drop primitive_obsession <file60", "primitive_obsession", 60),
        ("drop primitive_obsession <Q2(68)", "primitive_obsession", cuts[1] + 1),
        ("drop primitive_obsession ALL", "primitive_obsession", float("inf")),
        ("drop dry_violation <Q2(68)", "dry_violation", cuts[1] + 1),
        ("drop dry_violation ALL", "dry_violation", float("inf")),
        ("drop low_cohesion <Q2(68)", "low_cohesion", cuts[1] + 1),
    ]

    def corpus_pooled_auc(risk_by_repo):
        """Pooled AUC over all files (risk on the common 0-10 health scale)."""
        shim = []
        for name, (joined, _) in repos.items():
            for d, r in zip(joined, risk_by_repo[name]):
                shim.append({**d, "health_score": 10.0 - r})
        return roc_auc(shim)["auc"]

    def band_pooled_auc(risk_by_repo, band):
        shim = []
        for name, (joined, _) in repos.items():
            for d, r in zip(joined, risk_by_repo[name]):
                if ea.band_of(int(d.get("nloc", 0) or 0), cuts) == band:
                    shim.append({**d, "health_score": 10.0 - r})
        if len({1 if d["defect_count"] > 0 else 0 for d in shim}) < 2:
            return None
        return roc_auc(shim)["auc"]

    # Precompute risk per intervention.
    results = {}
    base_risk_by_repo = None
    print(f"label={args.label}  repos={len(repos)}  band cuts={[round(c) for c in cuts]}\n")
    print(f"{'intervention':36s} {'corpAUC':>8s} {'mAUC':>6s} {'mPopt':>6s} "
          f"{'Q1':>6s} {'Q2':>6s} {'Q3':>6s} {'Q4':>6s}")
    for label, bm, floor in interventions:
        risk_by_repo = {}
        per_auc, per_popt = [], []
        for name, (joined, by_file) in repos.items():
            risk = [10.0 - _score(by_file.get(_norm(d["file_path"]), []),
                                  suppress=bm, file_nloc=int(d.get("nloc", 0) or 0),
                                  floor=floor) for d in joined]
            risk_by_repo[name] = risk
            npos = sum(1 for d in joined if int(d.get("defect_count", 0) or 0) > 0)
            if 0 < npos < len(joined):
                per_auc.append(_auc(joined, risk))
                per_popt.append(_popt(joined, risk))
        corp = corpus_pooled_auc(risk_by_repo)
        bands = {b: band_pooled_auc(risk_by_repo, b) for b in band_labels}
        if label == "baseline":
            base_risk_by_repo = risk_by_repo
        results[label] = {"corpus_auc": corp, "mean_auc": _m(per_auc),
                          "mean_popt": _m(per_popt),
                          "band_auc": {b: bands[b] for b in band_labels}}
        bnums = [bands[b] for b in band_labels]
        print(f"{label:36s} {corp:8.3f} {_m(per_auc):>6} {_m(per_popt):>6} " +
              " ".join(f"{(b if b is not None else float('nan')):6.3f}" for b in bnums))

    # Bootstrap CI of the corpus pooled ΔAUC for the most promising gate vs baseline.
    print("\n--- bootstrap 95% CI (corpus pooled ΔAUC vs baseline; resample files within repo) ---")
    for label, bm, floor in interventions:
        if label == "baseline":
            continue
        # pooled bootstrap: resample files within each repo, recompute pooled AUC delta
        rng = random.Random(99)
        deltas = []
        treat = {}
        for name, (joined, by_file) in repos.items():
            treat[name] = [10.0 - _score(by_file.get(_norm(d["file_path"]), []),
                                         suppress=bm, file_nloc=int(d.get("nloc", 0) or 0),
                                         floor=floor) for d in joined]
        for _ in range(400):
            shim_a, shim_b = [], []
            for name, (joined, _bf) in repos.items():
                n = len(joined)
                idx = [rng.randrange(n) for _ in range(n)]
                for i in idx:
                    shim_a.append({**joined[i], "health_score": 10.0 - base_risk_by_repo[name][i]})
                    shim_b.append({**joined[i], "health_score": 10.0 - treat[name][i]})
            try:
                deltas.append(roc_auc(shim_b)["auc"] - roc_auc(shim_a)["auc"])
            except Exception:
                pass
        deltas.sort()
        mean_d = float(np.mean(deltas))
        lo, hi = deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas))]
        results[label]["corpus_delta_auc_ci"] = [mean_d, lo, hi]
        print(f"  {label:36s} Δcorpus AUC {mean_d:+.4f}  [{lo:+.4f}, {hi:+.4f}]")

    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
