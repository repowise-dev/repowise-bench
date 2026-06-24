"""Ad-hoc health-vs-defect benchmark for gohugoio/hugo, HEAD-scored.

Simplification vs. the main benchmark: health is scored at HEAD (the hosted
snapshot's commit) rather than at a leakage-free T0. Everything else mirrors
the benchmark:
  * defects  = keyword fix-commits in (t0, HEAD]   (t0 = HEAD_date - 6 months)
  * universe = scored non-test .go source files
  * baselines (churn 90d, prior-defects 6mo) anchored BEFORE t0 so they stay
    disjoint from the label window.
Health scores come from the hosted snapshot's health.json (the real product
output), gunzipped at ../../hosted_health.json.gz.
"""
from __future__ import annotations

import gzip
import json
import sys

sys.path.insert(0, ".")
from lib import baselines, stats
from lib.defect_counter import count_defects_keyword, find_fix_commits, resolve_t0_sha

REPO = "../../test-repos/hugo"
HEALTH_GZ = "../../hosted_health.json.gz"
T0_DATE = "2025-11-29"          # HEAD (2026-05-29) minus ~6 months
HEAD = "45c00b7c162b55ca9bcdd9a664bcf1294aa5d266"
EXT = (".go",)


def load_health():
    data = json.loads(gzip.decompress(open(HEALTH_GZ, "rb").read()))
    return {m["file_path"]: m for m in data["metrics"]}, data["findings"]


def main():
    metrics, findings = load_health()
    t0_sha = resolve_t0_sha(REPO, T0_DATE)
    fixes = find_fix_commits(REPO, t0_sha, HEAD, strategy="keyword")
    defects = count_defects_keyword(REPO, t0_sha, HEAD, source_root="", extensions=EXT)

    # Universe: scored, source .go, exclude tests (kept indexed, not labeled).
    universe = [
        p for p in metrics
        if p.endswith(".go") and not p.endswith("_test.go")
    ]
    joined = [
        {
            "file_path": p,
            "health_score": metrics[p]["score"],
            "nloc": metrics[p]["nloc"] or 0,
            "defect_count": defects.get(p, 0),
        }
        for p in universe
    ]

    n = len(joined)
    n_def = sum(1 for d in joined if d["defect_count"] > 0)
    print(f"window: {t0_sha[:10]} ({T0_DATE}) .. HEAD ({HEAD[:10]})")
    print(f"fix commits in window: {len(fixes)}")
    print(f"universe (non-test .go): {n}   defect-bearing files: {n_def} ({n_def*100//n}%)")
    print(f"total defect touches: {sum(d['defect_count'] for d in joined)}")
    print()

    # Core metrics
    auc = stats.bootstrap_ci(joined, stats.auc_metric, n_boot=2000)
    pop = stats.bootstrap_ci(joined, stats.popt_metric, n_boot=2000)
    sp = stats.spearman_correlation(
        [d["health_score"] for d in joined], [d["defect_count"] for d in joined]
    )
    psp = stats.partial_spearman(
        [d["health_score"] for d in joined],
        [float(d["defect_count"]) for d in joined],
        [float(d["nloc"]) for d in joined],
    )
    eff = stats.effort_aware_at_loc(joined, 0.20)

    print("=== HEALTH SCORE vs DEFECTS (hugo, HEAD-scored) ===")
    print(f"ROC AUC           : {auc['point']:.3f}  [95% CI {auc['lo']:.3f}, {auc['hi']:.3f}]  (n={auc['n']})")
    print(f"Popt (effort-aware): {pop['point']:.3f}  [95% CI {pop['lo']:.3f}, {pop['hi']:.3f}]")
    print(f"Spearman rho       : {sp['rho']:.3f}  (p={sp['p_value']:.2e})")
    print(f"partial rho (ctrl NLOC): {psp:.3f}")
    print(f"Precision@20%LOC   : {eff['precision']:.3f}")
    print(f"Recall@20%LOC(files): {eff['recall_files']:.3f}   Recall@20%LOC(defects): {eff['recall_defects']:.3f}")
    print()

    # Baselines
    baselines.attach_baseline_features(
        joined, REPO, t0_sha, T0_DATE, source_root="", extensions=EXT, strategy="keyword"
    )
    allb = baselines.all_baselines(joined)
    print("=== vs TRIVIAL BASELINES (same universe & labels) ===")
    print(f"{'predictor':18} {'AUC':>7} {'Popt':>7}")
    for name, r in allb.items():
        pv = r['popt'] if r['popt'] is not None else float('nan')
        print(f"{name:18} {r['auc']:7.3f} {pv:7.3f}")

    # density by health band
    print()
    print("=== defect density by health band ===")
    for b in stats.defect_density_by_bucket(joined, [4.0, 6.0, 8.0]):
        print(f"  {b['bucket']:9} files={b['file_count']:4d}  defect-touches={b['total_defects']:4d}  per-KLOC={b['defects_per_kloc']:.2f}")


if __name__ == "__main__":
    main()
