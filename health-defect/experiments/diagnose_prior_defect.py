#!/usr/bin/env python3
"""Thorough diagnosis: why does prior_defect as a capped biomarker NOT reproduce
the blend's +0.05 Popt win?

Sections:
  1. Per-repo Popt/AUC delta (shared org cap, shipped severity tiers).
  2. Cap-saturation test: does a DEDICATED category/cap recover the win?
  3. Redundancy: correlation of prior_defect_count with the existing process
     signals (commit_count_90d, change_entropy, co_change, ownership proxy).
  4. Rank-average (blend) vs capped-deduction (biomarker) on the SAME signal —
     isolates how much the scoring architecture, not the signal, kills the win.
  5. On files where prior_defect fires: how often is the org category already
     saturated by the other process biomarkers (the mechanism).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import yaml

import os as _os
BENCH = Path(__file__).resolve().parents[1]
RES = BENCH.parent / "results"
sys.path.insert(0, str(BENCH))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(BENCH.parents[1])))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / p
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

from lib.stats import ALL_BIOMARKERS, popt, roc_auc  # noqa: E402

from repowise.core.analysis.health import scoring  # noqa: E402
from repowise.core.analysis.health.biomarkers.base import BiomarkerResult  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402

REAL = set(ALL_BIOMARKERS)
SEV = {"low": Severity.LOW, "medium": Severity.MEDIUM, "high": Severity.HIGH,
       "critical": Severity.CRITICAL}
ORG = {"developer_congestion", "knowledge_loss", "hidden_coupling", "function_hotspot",
       "code_age_volatility", "ownership_risk", "churn_risk", "change_entropy",
       "co_change_scatter", "prior_defect"}


def norm(p):
    return p.replace("\\", "/").strip("/")


def load(name, label="keyword"):
    d = RES / f"health_defect_{name}"
    if not (d / "health_scores.json").exists():
        return None
    h = json.load(open(d / "health_scores.json"))
    j = json.load(open(d / "joined_data.json"))
    lp = d / f"defect_counts_{label}.json"
    if lp.exists():
        cnt = {norm(k): v for k, v in json.load(open(lp)).items()}
        for r in j:
            r["defect_count"] = cnt.get(norm(r["file_path"]), 0)
    bf = {}
    for f in h["findings"]:
        bf.setdefault(norm(f["file_path"]), []).append(f)
    return j, bf


def score(findings, include_prior):
    res = []
    for f in findings:
        bt = f["biomarker_type"]
        if bt not in REAL:
            continue
        if bt == "prior_defect" and not include_prior:
            continue
        res.append(BiomarkerResult(bt, SEV.get(str(f.get("severity")).lower(),
                   Severity.MEDIUM), None, None, None, {}, ""))
    return scoring.score_file(res)[0] if res else 10.0


def auc_popt(joined, risk):
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return roc_auc(shim)["auc"], (popt(shim) or {}).get("popt")


def _ranks(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    n = len(vals) or 1
    return [x / n for x in r]


def repos_with_signal(label="keyword"):
    out = {}
    for name in [r["name"] for r in yaml.safe_load((BENCH / "config.yaml").read_text())["repos"]]:
        r = load(name, label)
        if r is None:
            continue
        j, bf = r
        npos = sum(1 for x in j if int(x.get("defect_count", 0) or 0) > 0)
        if npos == 0 or npos == len(j):
            continue
        out[name] = (j, bf)
    return out


def section1(repos):
    print("\n=== 1. Per-repo ΔPopt/ΔAUC (shared org cap, W=1.6 shipped tiers) ===")
    scoring._BIOMARKER_CATEGORY["prior_defect"] = "organizational"
    scoring.CATEGORY_CAPS.pop("prior", None)
    scoring._BIOMARKER_WEIGHT_MULTIPLIER["prior_defect"] = 1.6
    print(f"  {'repo':10s} {'nfire':>5s} {'basePopt':>9s} {'+prior':>9s} {'dPopt':>8s} {'dAUC':>8s}")
    dps, das = [], []
    for name, (j, bf) in repos.items():
        nfire = sum(1 for x in j if any(f["biomarker_type"] == "prior_defect"
                    for f in bf.get(norm(x["file_path"]), [])))
        base = [10.0 - score(bf.get(norm(x["file_path"]), []), False) for x in j]
        trt = [10.0 - score(bf.get(norm(x["file_path"]), []), True) for x in j]
        ba, bp = auc_popt(j, base)
        ta, tp = auc_popt(j, trt)
        dp = (tp - bp) if (bp is not None and tp is not None) else None
        da = (ta - ba) if (ba is not None and ta is not None) else None
        if dp is not None:
            dps.append(dp)
        if da is not None:
            das.append(da)
        print(f"  {name:10s} {nfire:5d} {bp:9.3f} {tp:9.3f} "
              f"{(dp if dp is not None else float('nan')):+8.3f} "
              f"{(da if da is not None else float('nan')):+8.3f}")
    print(f"  {'MEAN':10s} {'':5s} {'':9s} {'':9s} {np.mean(dps):+8.3f} {np.mean(das):+8.3f}")


def section2(repos):
    print("\n=== 2. Cap-saturation test: dedicated category/cap for prior_defect ===")
    print("  (if a private cap recovers Popt, the shared org cap was eating it)")
    for cap, W in [(None, 1.6), (1.5, 1.6), (2.0, 2.0), (3.0, 3.0)]:
        if cap is None:
            scoring._BIOMARKER_CATEGORY["prior_defect"] = "organizational"
            scoring.CATEGORY_CAPS.pop("prior", None)
        else:
            scoring._BIOMARKER_CATEGORY["prior_defect"] = "prior"
            scoring.CATEGORY_CAPS["prior"] = cap
        scoring._BIOMARKER_WEIGHT_MULTIPLIER["prior_defect"] = W
        dps, das = [], []
        for _name, (j, bf) in repos.items():
            base = [10.0 - score(bf.get(norm(x["file_path"]), []), False) for x in j]
            trt = [10.0 - score(bf.get(norm(x["file_path"]), []), True) for x in j]
            ba, bp = auc_popt(j, base)
            ta, tp = auc_popt(j, trt)
            if bp is not None and tp is not None:
                dps.append(tp - bp)
            if ba is not None and ta is not None:
                das.append(ta - ba)
        tag = "shared org cap" if cap is None else f"own cap={cap}"
        print(f"  {tag:18s} W={W}  ΔPopt={np.mean(dps):+.4f}  ΔAUC={np.mean(das):+.4f}")
    # restore
    scoring._BIOMARKER_CATEGORY["prior_defect"] = "organizational"
    scoring.CATEGORY_CAPS.pop("prior", None)


def section3(repos):
    print("\n=== 3. Redundancy: corr(prior_defect_count, other process signals) ===")
    cols = {"prior": [], "churn90d": [], "entropy": [], "cochange": [], "owner_minor": []}
    for _name, (j, bf) in repos.items():
        for x in j:
            fp = norm(x["file_path"])
            fnd = bf.get(fp, [])
            pd = next((f["details"].get("prior_defect_count", 0)
                       for f in fnd if f["biomarker_type"] == "prior_defect"), 0)
            ent = next((f["details"].get("change_entropy", 0.0)
                        for f in fnd if f["biomarker_type"] == "change_entropy"), 0.0)
            cc = next((f["details"].get("scatter", 0)
                       for f in fnd if f["biomarker_type"] == "co_change_scatter"), 0)
            owner = next((f["details"].get("minor_contributors", 0)
                          for f in fnd if f["biomarker_type"] == "ownership_risk"), 0)
            cols["prior"].append(float(pd))
            cols["churn90d"].append(float(x.get("commit_count_90d", 0) or 0))
            cols["entropy"].append(float(ent))
            cols["cochange"].append(float(cc))
            cols["owner_minor"].append(float(owner))
    p = np.array(cols["prior"])
    for k in ("churn90d", "entropy", "cochange", "owner_minor"):
        v = np.array(cols[k])
        if v.std() > 0 and p.std() > 0:
            print(f"  corr(prior, {k:12s}) = {np.corrcoef(p, v)[0, 1]:+.3f}")
    # share of defect signal already covered: of files with a defect, how many
    # are flagged by EXISTING process signals already (prior adds nothing new).
    have_proc = covered = pri_only = 0
    for _name, (j, bf) in repos.items():
        for x in j:
            if int(x.get("defect_count", 0) or 0) == 0:
                continue
            fp = norm(x["file_path"])
            types = {f["biomarker_type"] for f in bf.get(fp, [])}
            proc = types & (ORG - {"prior_defect"})
            pri = "prior_defect" in types
            if proc:
                have_proc += 1
            if pri and not proc:
                pri_only += 1
            if pri and proc:
                covered += 1
    print(f"  defective files flagged by an EXISTING process signal: {have_proc}")
    print(f"    ...also flagged by prior_defect (redundant overlap): {covered}")
    print(f"    flagged ONLY by prior_defect (unique catch): {pri_only}")


def section4(repos):
    print("\n=== 4. Rank-average (blend) vs capped biomarker on the SAME signal ===")
    print("  isolates scoring-architecture loss: same prior signal, two combiners")
    # blend = rank-avg(health_no_prior_risk, prior_count); biomarker = capped score
    scoring._BIOMARKER_CATEGORY["prior_defect"] = "organizational"
    scoring.CATEGORY_CAPS.pop("prior", None)
    scoring._BIOMARKER_WEIGHT_MULTIPLIER["prior_defect"] = 1.6
    bl_dp, bm_dp = [], []
    for _name, (j, bf) in repos.items():
        base = [10.0 - score(bf.get(norm(x["file_path"]), []), False) for x in j]
        prior = []
        for x in j:
            fp = norm(x["file_path"])
            pd = next((f["details"].get("prior_defect_count", 0)
                       for f in bf.get(fp, []) if f["biomarker_type"] == "prior_defect"), 0)
            prior.append(float(pd))
        # blend: rank-average of base-risk and prior
        rb, rp = _ranks(base), _ranks(prior)
        blend = [(a + b) / 2 for a, b in zip(rb, rp)]
        # biomarker: capped treatment score
        trt = [10.0 - score(bf.get(norm(x["file_path"]), []), True) for x in j]
        _, bp = auc_popt(j, base)
        _, blp = auc_popt(j, blend)
        _, bmp = auc_popt(j, trt)
        if bp is not None:
            if blp is not None:
                bl_dp.append(blp - bp)
            if bmp is not None:
                bm_dp.append(bmp - bp)
    print(f"  rank-average blend   mean ΔPopt = {np.mean(bl_dp):+.4f}  (the reported win)")
    print(f"  capped biomarker     mean ΔPopt = {np.mean(bm_dp):+.4f}  (what we can ship)")


def section5(repos):
    print("\n=== 5. Mechanism: org-category saturation on prior_defect-firing files ===")
    scoring._BIOMARKER_WEIGHT_MULTIPLIER["prior_defect"] = 1.6
    scoring._BIOMARKER_CATEGORY["prior_defect"] = "organizational"
    scoring.CATEGORY_CAPS.pop("prior", None)
    cap = scoring.CATEGORY_CAPS["organizational"]
    fired = saturated = 0
    for _name, (j, bf) in repos.items():
        for x in j:
            fp = norm(x["file_path"])
            fnd = bf.get(fp, [])
            if not any(f["biomarker_type"] == "prior_defect" for f in fnd):
                continue
            fired += 1
            # org deduction from OTHER biomarkers (pre-prior)
            org_raw = 0.0
            for f in fnd:
                bt = f["biomarker_type"]
                if bt == "prior_defect" or scoring.biomarker_category(bt) != "organizational":
                    continue
                org_raw += scoring.severity_deduction(
                    SEV.get(str(f.get("severity")).lower(), Severity.MEDIUM)
                ) * scoring.biomarker_weight(bt)
            if org_raw >= cap:
                saturated += 1
    print(f"  org cap = {cap}")
    print(f"  files where prior_defect fires: {fired}")
    print(f"    of those, org category ALREADY at/over cap from other signals: "
          f"{saturated} ({100 * saturated / max(fired, 1):.0f}%)")
    print("  → on saturated files prior_defect's deduction is fully scaled away.")


def main():
    repos = repos_with_signal("keyword")
    print(f"Diagnosis on {len(repos)} repos (keyword labels)")
    section1(repos)
    section2(repos)
    section3(repos)
    section4(repos)
    section5(repos)


if __name__ == "__main__":
    main()
