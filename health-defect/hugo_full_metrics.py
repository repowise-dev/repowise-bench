"""Comprehensive hugo health-vs-defect metrics for the reference doc.

Prints two label families:
  A) PRODUCT label  — `prior_defect` biomarker (what repowise.dev shows),
     over ALL scored files (946).
  B) BENCHMARK label — fix-commit touched in (HEAD-6mo, HEAD], recomputed
     from git, over non-test .go source files (504).

For each: threshold sweep, mutually-exclusive band precision/recall/lift,
top-K precision, density per KLOC. Health scored at HEAD (45c00b7c).
"""
from __future__ import annotations

import gzip
import json
import sys

sys.path.insert(0, ".")
from lib.defect_counter import count_defects_keyword, resolve_t0_sha

HEALTH_GZ = "../../hosted_health.json.gz"
REPO = "../../test-repos/hugo"
HEAD = "45c00b7c162b55ca9bcdd9a664bcf1294aa5d266"
T0_DATE = "2025-11-29"


def load():
    d = json.loads(gzip.decompress(open(HEALTH_GZ, "rb").read()))
    m = {x["file_path"]: x for x in d["metrics"]}
    prior = {}
    for f in d["findings"]:
        if f["biomarker_type"] == "prior_defect":
            prior[f["file_path"]] = f["details"].get("prior_defect_count", 1)
    return m, prior


def report(name, files, label, scores, nloc):
    n = len(files)
    pos = {f for f in files if label.get(f, 0) > 0}
    base = len(pos) / n
    total_nloc = sum(nloc[f] for f in files)
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    print(f"files={n}  bug-fixed={len(pos)} ({len(pos)/n*100:.1f}% base rate)  total_nloc={total_nloc}")

    ranked = sorted(files, key=lambda f: scores[f])

    print("\n-- Top-K precision (K lowest-health files) --")
    for k in (5, 10, 20, 30, 50):
        if k > n:
            continue
        hits = sum(1 for f in ranked[:k] if f in pos)
        print(f"  worst {k:3}: {hits}/{k} = {hits/k*100:.0f}%   lift={ (hits/k)/base:.1f}x")

    print("\n-- Threshold sweep: flag every file with score < T --")
    print(f"  {'T':>4} {'flagged':>8} {'bug-fixed':>10} {'precision':>10} {'recall':>8} {'lift':>6}")
    for t in (4, 5, 6, 7, 8):
        flagged = [f for f in files if scores[f] < t]
        if not flagged:
            continue
        hits = sum(1 for f in flagged if f in pos)
        prec = hits / len(flagged)
        rec = hits / len(pos)
        print(f"  {t:>4} {len(flagged):>8} {hits:>10} {prec*100:>9.0f}% {rec*100:>7.0f}% {prec/base:>5.1f}x")

    print("\n-- Mutually-exclusive bands: precision + density/KLOC --")
    bands = [("0-4 (red)", 0, 4), ("4-6", 4, 6), ("6-8", 6, 8), ("8-10 (green)", 8, 10.01)]
    for lbl, lo, hi in bands:
        grp = [f for f in files if lo <= scores[f] < hi]
        if not grp:
            continue
        hits = sum(1 for f in grp if f in pos)
        touches = sum(label.get(f, 0) for f in grp)
        gnloc = sum(nloc[f] for f in grp)
        print(f"  {lbl:14} files={len(grp):4} bug-fixed={hits:4} ({hits/len(grp)*100:>3.0f}%) "
              f"fix-touches={touches:4} density={touches/gnloc*1000:.2f}/KLOC")

    # concentration: least-healthy 20% share of all bug-fixed files
    kc = max(1, round(n * 0.2))
    share = sum(1 for f in ranked[:kc] if f in pos) / len(pos)
    print(f"\n  concentration: least-healthy 20% ({kc} files) hold {share*100:.0f}% of all bug-fixed files")


def main():
    m, prior = load()
    scores = {f: m[f]["score"] for f in m}
    nloc = {f: (m[f]["nloc"] or 1) for f in m}

    # A) product label, all files
    report("A) PRODUCT label (prior_defect biomarker) — all 946 scored files",
           list(m), prior, scores, nloc)

    # B) benchmark label, non-test .go
    t0 = resolve_t0_sha(REPO, T0_DATE)
    fix_counts = count_defects_keyword(REPO, t0, HEAD, source_root="", extensions=(".go",))
    src = [f for f in m if f.endswith(".go") and not f.endswith("_test.go")]
    report("B) BENCHMARK label (git fix-commit touch, 6mo) — 504 non-test .go",
           src, {f: fix_counts.get(f, 0) for f in src}, scores, nloc)


if __name__ == "__main__":
    main()
