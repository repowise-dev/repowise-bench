#!/usr/bin/env python3
"""Controlled before/after measurement of the prior_defect biomarker's effect on
the SHIPPED health score, scored with the benchmark's own AUC/Popt.

Method (fully controlled — only prior_defect toggles):
  * Load each repo's cached T0 findings (health_scores.json) + joined_data.json.
  * baseline_score = score_file(findings WITHOUT prior_defect) under shipped weights
    → the pre-Phase-8.5 product score.
  * treatment_score(W) = score_file(findings WITH prior_defect at weight W) under
    the same shipped weights for the other 23 biomarkers.
  * Build joined rows with health_score := baseline / treatment, then evaluate
    with lib.stats.roc_auc / popt (the published metric) per repo; mean across
    repos with bootstrap 95% CIs; also the prior-defects rank baseline for context.
  * Sweep W to find the weight; report keyword AND szz label sets.

Run:
    PYTHONPATH=... .venv/Scripts/python.exe local-stash/measure_prior_defect.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml

import os as _os
BENCH = Path(__file__).resolve().parents[1]
RESULTS = BENCH.parent / "results"
sys.path.insert(0, str(BENCH))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(BENCH.parents[1])))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / p
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

from lib.baselines import _auc_popt_for_risk  # noqa: E402
from lib.filters import normalize_path  # noqa: E402
from lib.stats import ALL_BIOMARKERS, popt, roc_auc  # noqa: E402

from repowise.core.analysis.health import scoring  # noqa: E402
from repowise.core.analysis.health.biomarkers.base import BiomarkerResult  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402

_REAL = set(ALL_BIOMARKERS)
_SEV = {"low": Severity.LOW, "medium": Severity.MEDIUM,
        "high": Severity.HIGH, "critical": Severity.CRITICAL}


def _sev(s) -> Severity:
    return _SEV.get(str(s).strip().lower(), Severity.MEDIUM)


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def _score_file(findings: list[dict], *, include_prior: bool) -> float:
    results = []
    for f in findings:
        bt = f.get("biomarker_type")
        if bt not in _REAL:
            continue
        if bt == "prior_defect" and not include_prior:
            continue
        results.append(BiomarkerResult(
            biomarker_type=bt, severity=_sev(f.get("severity")),
            function_name=None, line_start=None, line_end=None, details={}, reason="",
        ))
    return scoring.score_file(results)[0] if results else 10.0


def _bootstrap_delta(joined, risk_a, risk_b, metric_fn, *, n_boot=1000, seed=4242):
    """Bootstrap CI of metric(b) - metric(a) over files resampled within the repo."""
    import random
    rng = random.Random(seed)
    n = len(joined)
    deltas = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        ja = [joined[i] for i in idx]
        ra = [risk_a[i] for i in idx]
        rb = [risk_b[i] for i in idx]
        try:
            va = metric_fn(ja, ra)
            vb = metric_fn(ja, rb)
        except Exception:
            continue
        if va is not None and vb is not None and va == va and vb == vb:
            deltas.append(vb - va)
    if not deltas:
        return None
    deltas.sort()
    lo = deltas[int(0.025 * len(deltas))]
    hi = deltas[int(0.975 * len(deltas))]
    return (float(np.mean(deltas)), float(lo), float(hi))


def _auc_for(joined, risk):
    # risk higher = riskier → invert to a synthetic health_score, reuse roc_auc.
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return roc_auc(shim)["auc"]


def _popt_for(joined, risk):
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return (popt(shim) or {}).get("popt")


def load_repo(name, label):
    d = RESULTS / f"health_defect_{name}"
    hp, jp = d / "health_scores.json", d / "joined_data.json"
    if not (hp.exists() and jp.exists()):
        return None
    health = json.loads(hp.read_text())
    joined = json.loads(jp.read_text())
    if not joined:
        return None
    if label != "joined":
        lp = d / f"defect_counts_{label}.json"
        if not lp.exists():
            return None
        counts = {_norm(k): v for k, v in json.loads(lp.read_text()).items()}
        for r in joined:
            r["defect_count"] = counts.get(_norm(r["file_path"]), 0)
    # findings by file
    by_file: dict[str, list[dict]] = {}
    for f in health.get("findings", []):
        by_file.setdefault(_norm(f["file_path"]), []).append(f)
    return joined, by_file


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--weights", default="1.0,1.3,1.6,1.8,2.0")
    ap.add_argument("--config", type=Path, default=BENCH / "config.yaml")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "prior_defect_measure_output.json")
    args = ap.parse_args()

    allowed = [r["name"] for r in yaml.safe_load(args.config.read_text())["repos"]]
    weights = [float(w) for w in args.weights.split(",")]

    repos = {}
    for name in allowed:
        r = load_repo(name, args.label)
        if r is not None:
            repos[name] = r

    # Per repo, build baseline risk (10 - baseline_score) and prior baseline.
    out = {"label": args.label, "n_repos": len(repos), "weights": {}}
    print(f"label={args.label}  repos={len(repos)}\n")

    # Precompute baseline + prior risk per repo (independent of W).
    base = {}
    for name, (joined, by_file) in repos.items():
        npos = sum(1 for d in joined if int(d.get("defect_count", 0) or 0) > 0)
        if npos == 0 or npos == len(joined):
            continue
        base_risk = []
        prior_risk = []
        for d in joined:
            fp = _norm(d["file_path"])
            fnd = by_file.get(fp, [])
            base_risk.append(10.0 - _score_file(fnd, include_prior=False))
            prior_risk.append(float(d.get("prior_defect_count", 0) or 0))
        base[name] = (joined, by_file, base_risk, prior_risk)

    # Baseline (no prior_defect) + prior-only baseline means.
    base_auc = [_auc_for(j, br) for j, _, br, _ in base.values()]
    base_popt = [_popt_for(j, br) for j, _, br, _ in base.values()]
    prior_auc = [_auc_for(j, pr) for j, _, _, pr in base.values()]
    prior_popt = [_popt_for(j, pr) for j, _, _, pr in base.values()]

    def _m(xs):
        xs = [x for x in xs if x is not None]
        return round(float(np.mean(xs)), 4) if xs else None

    print(f"  {'model':24s} {'mean AUC':>9s} {'mean Popt':>10s}")
    print(f"  {'health (no prior)':24s} {_m(base_auc):>9} {_m(base_popt):>10}")
    print(f"  {'prior_defects (raw)':24s} {_m(prior_auc):>9} {_m(prior_popt):>10}")
    out["baseline"] = {"health_no_prior": {"auc": _m(base_auc), "popt": _m(base_popt)},
                       "prior_defects": {"auc": _m(prior_auc), "popt": _m(prior_popt)}}

    for W in weights:
        scoring._BIOMARKER_WEIGHT_MULTIPLIER["prior_defect"] = W
        treat_auc, treat_popt = [], []
        d_auc_cis, d_popt_cis = [], []
        for name, (joined, by_file, base_risk, _prior) in base.items():
            treat_risk = [10.0 - _score_file(by_file.get(_norm(d["file_path"]), []),
                                             include_prior=True) for d in joined]
            treat_auc.append(_auc_for(joined, treat_risk))
            treat_popt.append(_popt_for(joined, treat_risk))
            da = _bootstrap_delta(joined, base_risk, treat_risk, _auc_for)
            dp = _bootstrap_delta(joined, base_risk, treat_risk, _popt_for)
            if da:
                d_auc_cis.append(da)
            if dp:
                d_popt_cis.append(dp)
        # mean of per-repo bootstrap delta means + mean CI bounds
        mean_dauc = _m([d[0] for d in d_auc_cis])
        mean_dpopt = _m([d[0] for d in d_popt_cis])
        ci_dauc = (_m([d[1] for d in d_auc_cis]), _m([d[2] for d in d_auc_cis]))
        ci_dpopt = (_m([d[1] for d in d_popt_cis]), _m([d[2] for d in d_popt_cis]))
        print(f"\n  W={W}")
        print(f"  {'health + prior':24s} {_m(treat_auc):>9} {_m(treat_popt):>10}")
        print(f"    ΔAUC  {mean_dauc:+.4f}  per-repo-mean 95% CI [{ci_dauc[0]:+.4f}, {ci_dauc[1]:+.4f}]")
        print(f"    ΔPopt {mean_dpopt:+.4f}  per-repo-mean 95% CI [{ci_dpopt[0]:+.4f}, {ci_dpopt[1]:+.4f}]")
        out["weights"][str(W)] = {
            "treat_auc": _m(treat_auc), "treat_popt": _m(treat_popt),
            "delta_auc": mean_dauc, "delta_auc_ci": ci_dauc,
            "delta_popt": mean_dpopt, "delta_popt_ci": ci_dpopt,
        }

    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
