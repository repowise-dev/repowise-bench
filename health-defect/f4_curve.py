#!/usr/bin/env python3
"""F4 cost-effectiveness (Popt / effort-aware) curve extractor.

READ-ONLY. Rebuilds the paired CodeScene intersection exactly as
``codescene_headtohead.build_paired_corpus`` does (load_repo for the repowise
health series; swap health_score with the cached CodeScene score; drop the
files CodeScene returned null/absent for), adds a LOC-only baseline series, and
computes the cumulative-defects-vs-cumulative-LOC effort-aware recall curve at
5/10/20/30/50/100% LOC budgets plus Popt, pooled across the corpus.

Uses the canonical ``lib.stats.effort_aware_at_loc`` / ``popt`` so the ranking
convention (lowest health first, tie-break smaller files) is identical to the
shipped metrics. recall_files (defective-files recall) is the cache's
recall_at_20pct_loc field. Cross-checks the health/CodeScene anchors against
``results/codescene_headtohead.json``.

Usage (venv python -- needs the results cache populated):
    ../../.venv/Scripts/python.exe f4_curve.py
"""
import json
import sys
from pathlib import Path

HD = Path(__file__).resolve().parent
sys.path.insert(0, str(HD))

from statistical_rigor import load_repo, normalize_path  # noqa: E402
from lib.stats import effort_aware_at_loc, popt  # noqa: E402

RESULTS = HD.parent / "results"
H2H = json.load(open(RESULTS / "codescene_headtohead.json"))
REPOS = H2H["repos"]
LABEL = H2H["label"]

# Patch the module-level _RESULTS so load_repo reads the canonical cache.
import statistical_rigor as sr  # noqa: E402
sr._RESULTS = RESULTS


def build_paired():
    """(repowise_pooled, codescene_pooled) row-aligned, replicating
    build_paired_corpus minus the git/t0 step (codescene_scores.json is keyed
    by the same rel paths joined_data uses)."""
    rw_pool, cs_pool = [], []
    n_null = n_absent = 0
    for name in REPOS:
        rows = load_repo(name, LABEL)
        if not rows:
            continue
        cs_path = RESULTS / f"health_defect_{name}" / "codescene_scores.json"
        cs_scores = {normalize_path(k): v for k, v in json.loads(cs_path.read_text()).items()}
        for r in rows:
            fp = normalize_path(r["file_path"])
            if fp not in cs_scores:
                n_absent += 1
                continue
            s = cs_scores[fp]
            if s is None:
                n_null += 1
                continue
            rw_pool.append(r)
            cs_pool.append({**r, "health_score": float(s)})
    return rw_pool, cs_pool, n_null, n_absent


def loc_only_series(rows):
    """LOC-only baseline: biggest files = highest risk. effort_aware ranks by
    (health_score, nloc) ascending, so set health_score = -nloc to rank the
    biggest files first."""
    return [{**r, "health_score": -float(max(r["nloc"], 1))} for r in rows]


BUDGETS = [0.05, 0.10, 0.20, 0.30, 0.50, 1.00]


def curve(rows):
    out = {}
    for b in BUDGETS:
        ea = effort_aware_at_loc(rows, b)
        out[b] = ea["recall_files"]
    return out, popt(rows).get("popt")


def main():
    rw, cs, n_null, n_absent = build_paired()
    loc = loc_only_series(rw)
    n_def = sum(1 for d in rw if d["defect_count"] > 0)
    print(f"paired files={len(rw)} defective={n_def} (null={n_null}, absent={n_absent}) label={LABEL}")

    series = {"health(repowise)": rw, "codescene": cs, "loc_only": loc}
    curves = {}
    print("\n=== Effort-aware recall_files (defective-file recall) by LOC budget ===")
    hdr = "series".ljust(18) + "".join(f"{int(b*100):>8}%" for b in BUDGETS) + "   Popt"
    print(hdr)
    for name, rows in series.items():
        c, pv = curve(rows)
        curves[name] = {"recall_files": {f"{int(b*100)}pct": round(c[b], 4) for b in BUDGETS},
                        "popt": round(pv, 4) if pv is not None else None,
                        "recall_at_20pct_loc": round(c[0.20], 4)}
        cells = "".join(f"{c[b]*100:>8.1f}" for b in BUDGETS)
        print(name.ljust(18) + cells + f"   {pv:.4f}")

    print("\n=== recall@20%LOC scalar (recall_files) ===")
    for name in series:
        print(f"  {name:18s} {curves[name]['recall_at_20pct_loc']}")

    # cross-check against cache pooled recall_at_20pct_loc
    print("\n=== cross-check vs headtohead cache pooled recall_at_20pct_loc ===")
    for k, tool in (("health(repowise)", "repowise"), ("codescene", "codescene")):
        cached = H2H[tool]["recall_at_20pct_loc"]["pooled"]["point"]
        print(f"  {k:18s} computed={curves[k]['recall_at_20pct_loc']}  cache={round(cached,4)}")
    for k, tool in (("health(repowise)", "repowise"), ("codescene", "codescene")):
        cached = H2H[tool]["popt"]["pooled"]["point"]
        print(f"  Popt {k:13s} computed={curves[k]['popt']}  cache={round(cached,4)}")

    out = {"label": LABEL, "n_paired": len(rw), "n_defective": n_def,
           "budgets_pct": [int(b*100) for b in BUDGETS], "series": curves}
    (RESULTS / "f4_curve.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote {RESULTS / 'f4_curve.json'}")


if __name__ == "__main__":
    main()
