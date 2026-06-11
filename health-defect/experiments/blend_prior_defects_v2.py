#!/usr/bin/env python3
"""Health × prior-defects blend — scored with the bench's OWN baseline code.

Uses ``lib.baselines._auc_popt_for_risk`` + ``lib.stats`` so the blend's AUC/Popt
are computed identically to the published per-repo baselines (no LOO, no
training — rank files by a risk signal, score within each repo, mean across
repos). The blend is a training-free **rank-average**: within a repo, convert
each signal to a percentile rank and average — the obvious "combine two cheap
signals" move, comparable to the single-signal baselines.

Question: does blending health with prior-defects (or churn) beat prior-defects
alone on mean per-repo Popt — the metric where the cheap baselines out-rank the
health score?

    .venv/Scripts/python.exe local-stash/blend_prior_defects_v2.py [--label szz]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml

import sys
_BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BENCH))

from lib.baselines import _auc_popt_for_risk, all_baselines  # noqa: E402
from lib.filters import normalize_path  # noqa: E402


def _ranks(vals: list[float]) -> list[float]:
    """Average-rank percentile of each value (ties share the mean rank)."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    n = len(vals) or 1
    return [r / n for r in ranks]


def _blend_risk(joined: list[dict], signals: list[str]) -> list[float]:
    """Rank-average of the named per-file signals (higher = riskier).

    ``health`` → risk = 10 - health_score; counts used directly.
    """
    cols = []
    for s in signals:
        if s == "health":
            cols.append([10.0 - float(d.get("health_score", 10.0)) for d in joined])
        elif s == "prior":
            cols.append([float(d.get("prior_defect_count", 0) or 0) for d in joined])
        elif s == "churn":
            cols.append([float(d.get("commit_count_90d", 0) or 0) for d in joined])
        else:
            raise ValueError(s)
    rank_cols = [_ranks(c) for c in cols]
    return [float(np.mean([rc[i] for rc in rank_cols])) for i in range(len(joined))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path,
                    default=Path(__file__).resolve().parents[2] / "results")
    ap.add_argument("--config", type=Path, default=_BENCH / "config.yaml")
    ap.add_argument("--label", default="joined",
                    help="joined (use joined_data defect_count) or a strategy "
                         "name (keyword/szz/...) → swap defect_counts_<label>.json")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "blend_prior_defects_v2_output.json")
    args = ap.parse_args()

    allowed = {r["name"] for r in yaml.safe_load(args.config.read_text())["repos"]}
    specs = {
        "health": ["health"], "prior": ["prior"], "churn": ["churn"],
        "health+prior": ["health", "prior"],
        "health+churn": ["health", "churn"],
        "health+prior+churn": ["health", "prior", "churn"],
    }
    rows = {k: {"auc": [], "popt": []} for k in (*specs, "loc", "random")}
    n_repos = 0
    skipped_popt = []

    for d in sorted(args.results_dir.glob("health_defect_*")):
        name = d.name.replace("health_defect_", "")
        if name not in allowed:
            continue
        jp = d / "joined_data.json"
        if not jp.exists():
            continue
        joined = json.loads(jp.read_text())
        if not joined:
            continue
        if args.label != "joined":
            lp = d / f"defect_counts_{args.label}.json"
            if not lp.exists():
                continue
            counts = {normalize_path(k): v for k, v in json.loads(lp.read_text()).items()}
            for r in joined:
                r["defect_count"] = counts.get(normalize_path(r["file_path"]), 0)
        npos = sum(1 for r in joined if int(r.get("defect_count", 0) or 0) > 0)
        if npos == 0 or npos == len(joined):
            skipped_popt.append(name)
            continue
        n_repos += 1
        base = all_baselines(joined)  # health / loc / churn / prior / random (report method)
        rows["health"]["auc"].append(base["health"]["auc"]);  rows["health"]["popt"].append(base["health"]["popt"])
        rows["prior"]["auc"].append(base["prior_defects"]["auc"]); rows["prior"]["popt"].append(base["prior_defects"]["popt"])
        rows["churn"]["auc"].append(base["churn_only"]["auc"]); rows["churn"]["popt"].append(base["churn_only"]["popt"])
        rows["loc"]["auc"].append(base["loc_only"]["auc"]);   rows["loc"]["popt"].append(base["loc_only"]["popt"])
        rows["random"]["auc"].append(base["random"]["auc"]);  rows["random"]["popt"].append(base["random"]["popt"])
        for spec in ("health+prior", "health+churn", "health+prior+churn"):
            res = _auc_popt_for_risk(joined, _blend_risk(joined, specs[spec]))
            rows[spec]["auc"].append(res["auc"]); rows[spec]["popt"].append(res["popt"])

    def _mean(xs):
        xs = [x for x in xs if x is not None]
        return round(float(np.mean(xs)), 4) if xs else None

    print(f"Label='{args.label}'  repos scored={n_repos}"
          + (f"  (skipped single-class: {skipped_popt})" if skipped_popt else "") + "\n")
    print(f"  {'model':22s} {'mean AUC':>9s} {'mean Popt':>10s}")
    out = {}
    order = ["health", "loc", "churn", "prior", "random",
             "health+prior", "health+churn", "health+prior+churn"]
    for k in order:
        a, p = _mean(rows[k]["auc"]), _mean(rows[k]["popt"])
        out[k] = {"mean_auc": a, "mean_popt": p, "n": len(rows[k]["auc"])}
        print(f"  {k:22s} {a if a is not None else float('nan'):>9.4f} "
              f"{p if p is not None else float('nan'):>10.4f}")

    bp = out["health+prior"]["mean_popt"]; pr = out["prior"]["mean_popt"]; he = out["health"]["mean_popt"]
    print(f"\n  Popt: health+prior {bp:.3f}  vs prior {pr:.3f}  vs health {he:.3f}  "
          f"-> blend {'BEATS' if bp > pr else 'does NOT beat'} prior; "
          f"vs health {'BEATS' if bp > he else 'no better'} (Pareto: "
          f"AUC {out['health+prior']['mean_auc']:.3f} vs {out['health']['mean_auc']:.3f})")
    args.out.write_text(json.dumps({"label": args.label, "n_repos": n_repos,
                                    "results": out}, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
