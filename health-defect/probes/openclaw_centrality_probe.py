#!/usr/bin/env python3
"""openclaw_centrality_probe.py — does the small-file PageRank lead hold on a
high-power corpus? (THROWAWAY, local-stash.)

The 21-repo corpus had only 82 small-file positives, so the small-file centrality
lead's OOF-AUC delta CI crossed 0 (under-powered). openclaw adds hundreds of
small-file positives. The full §3 gate needs openclaw's biomarker health index,
but that is impractical here: repowise's *git* indexing walks ~40k pre-T0 commits
on a 300-commit/day repo (15-40 min, didn't finish). The *graph* build, however,
is cheap (~70s) — so we can run the **within-band acid test** directly:

    Does graph centrality rank openclaw's small-file bugs above chance — with a
    tight CI, given the high positive count?

This is the size-orthogonal acid test (plan §3 part 3 / §5). It does NOT control
for the biomarker model (that needs the health index), so a win here is
"centrality carries small-file signal", not "centrality beats the calibrated
model". Reported with bootstrap CIs vs the trivial baselines (rank-by-size,
random).

Run (venv python, from repowise-bench/health-defect)::

    ../../.venv/Scripts/python.exe ../local-stash/openclaw_centrality_probe.py \
        --worktree C:/Users/.../oc-t0 --t0-sha <sha>
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

_BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BENCH))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for corpus_screen reuse
# centrality_experiment.py + candidate_eval.py live in the bench R&D worktree.
_RND = Path(r"C:/Users/ragha/Desktop/repowise-bench-rnd/health-defect")
if _RND.exists():
    sys.path.insert(0, str(_RND))

import error_analysis as ea  # tie-aware auc  # noqa: E402
from centrality_experiment import build_graph_metrics  # noqa: E402
from corpus_screen import fix_touches, nloc_at_t0  # noqa: E402

CENTRALITY = ["pagerank", "eigenvector", "betweenness", "in_degree", "out_degree"]
SRC_EXTS = {".ts"}
# Graph excludes (keep source; drop vendored/generated/docs). Tests stay as graph
# nodes (they're real dependents) but are dropped from the EVALUATED universe.
GRAPH_EXCLUDE = ["vendor/", "docs/", "assets/", "test-fixtures/"]


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def _is_eval_file(path: str) -> bool:
    p = _norm(path)
    if not p.endswith(".ts"):
        return False
    if p.endswith(".d.ts") or ".test." in p or ".spec." in p:
        return False
    if p.startswith(("vendor/", "docs/", "test/", "test-fixtures/", "assets/")):
        return False
    return True


def boot_auc_ci(y: list[int], score: list[float], n_boot=1000, seed=7):
    """Bootstrap CI for tie-aware AUC by resampling files."""
    base = ea.auc(y, score)
    if base is None:
        return None, None, None
    rng = random.Random(seed)
    n = len(y)
    samples = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        a = ea.auc([y[i] for i in idx], [score[i] for i in idx])
        if a is not None:
            samples.append(a)
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[int(0.975 * len(samples))]
    return round(base, 4), round(lo, 4), round(hi, 4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True, type=Path,
                    help="detached worktree checked out at T0 (for the graph build)")
    ap.add_argument("--repo-dir", required=True, type=Path,
                    help="main clone with full history (HEAD=real tip) for fix labels")
    ap.add_argument("--t0-sha", required=True)
    ap.add_argument("--small", type=int, default=48)
    ap.add_argument("--min-nloc", type=int, default=10)
    args = ap.parse_args()

    wt = args.worktree
    print("Building dependency graph at T0 (no health index, no git walk)...")
    cols = build_graph_metrics(wt, GRAPH_EXCLUDE)
    meta = cols.pop("_meta", {})
    print(f"  graph: {meta.get('file_nodes')} nodes, {meta.get('edges')} edges, "
          f"{meta.get('parsed_files')} parsed")

    print("Counting NLOC at T0 + keyword fix labels over (T0, HEAD]...")
    # NLOC + fix history come from the MAIN clone (real HEAD); the worktree's HEAD
    # is pinned at T0 so `T0..HEAD` would be empty there.
    nloc = nloc_at_t0(args.repo_dir, args.t0_sha, SRC_EXTS)
    nloc = {_norm(k): v for k, v in nloc.items() if v >= args.min_nloc}
    n_fix, touches = fix_touches(args.repo_dir, args.t0_sha, SRC_EXTS)
    pos_files = {_norm(k) for k, v in touches.items() if v > 0}

    # Evaluated universe: source files present at T0 with a centrality value.
    pr = cols.get("pagerank", {})
    files = [f for f in nloc if _is_eval_file(f) and f in pr]
    y = [1 if f in pos_files else 0 for f in files]
    nl = [nloc[f] for f in files]
    npos = sum(y)
    print(f"\nEvaluated universe: {len(files)} source files | {npos} positives "
          f"({npos/max(len(files),1):.0%}) | {n_fix} fix commits")

    # NLOC quartile bands.
    cuts = [float(np.percentile(nl, q)) for q in (25, 50, 75)]
    print(f"NLOC quartile cuts: {[round(c) for c in cuts]}")

    def band(n):
        return 0 if n <= cuts[0] else 1 if n <= cuts[1] else 2 if n <= cuts[2] else 3
    band_names = [f"Q1(<= {cuts[0]:.0f})", f"Q2(<= {cuts[1]:.0f})",
                  f"Q3(<= {cuts[2]:.0f})", f"Q4(> {cuts[2]:.0f})"]

    print(f"\n{'signal':12s} {'overall AUC (95% CI)':>26s}  per-NLOC-band AUC "
          f"(candidate ranks defect)")
    # size baseline: rank by NLOC (bigger = riskier)
    rows = [("size(nloc)", nl)]
    for c in CENTRALITY:
        col = cols.get(c, {})
        rows.append((c, [float(col.get(f, 0.0)) for f in files]))

    for name, score in rows:
        a, lo, hi = boot_auc_ci(y, score)
        bands = []
        for b in range(4):
            idx = [i for i in range(len(files)) if band(nl[i]) == b]
            ab = ea.auc([y[i] for i in idx], [score[i] for i in idx])
            bands.append(f"{band_names[b].split('(')[0]}={ab:.3f}" if ab is not None else "n/a")
        print(f"{name:12s} {f'{a} [{lo},{hi}]':>26s}  " + " ".join(bands))

    # Small-file focus: AUC + cost-effective Top-k of PageRank vs size vs random.
    sf = [i for i in range(len(files)) if nl[i] <= args.small]
    sf_y = [y[i] for i in sf]
    sf_pos = sum(sf_y)
    print(f"\n=== SMALL FILES (<= {args.small} LOC): {len(sf)} files, {sf_pos} positives "
          f"({sf_pos/max(len(sf),1):.0%}) ===")
    for name, score in rows:
        sc = [score[i] for i in sf]
        a, lo, hi = boot_auc_ci(sf_y, sc, seed=11)
        print(f"  {name:12s} small-file AUC = {a} [{lo}, {hi}]")
    print(f"\n(n={len(sf)} small files / {sf_pos} positives — vs 82 small-file "
          f"positives in the entire 21-repo corpus)")


if __name__ == "__main__":
    main()
