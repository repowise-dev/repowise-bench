#!/usr/bin/env python3
"""Throwaway probe (local-stash, NOT committed): is naturalness worth *replacing*
a weak/floored biomarker, rather than only *adding* it?

The §3 gate tests ADDITION (full 24-biomarker model vs +candidate). This asks the
distinct question the user raised: could mean-line-surprisal SUBSTITUTE for one of
the AUC-weak biomarkers (low_cohesion / brain_method / dry_violation /
developer_congestion / primitive_obsession / bumpy_road / knowledge_loss) and net
≥ the full model — i.e. a cleaner roster for free?

For each weak biomarker w it computes LOO pooled OOF AUC of:
  full              — all 24 + log-NLOC
  full - w          — drop w (does w even contribute?)
  full - w + nat    — drop w, add naturalness (the replacement)
on the identical naturalness-computable universe, with a cluster-bootstrap CI on
the (full - w + nat) − full delta. Reuses candidate_eval internals verbatim.
"""
from __future__ import annotations

import json
import random
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np

# candidate_eval.py lives in the bench R&D worktree (uncommitted); lib/ +
# error_analysis are in both. Put the R&D worktree first.
_HD_RND = Path(r"C:\Users\ragha\Desktop\repowise-bench-rnd\health-defect")
_HD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HD))
sys.path.insert(0, str(_HD_RND))
import candidate_eval as ce  # noqa: E402

RESULTS = Path(__file__).resolve().parents[2] / "results"
CONFIG = _HD / "config.yaml"
WEAK = ["low_cohesion", "brain_method", "dry_violation", "developer_congestion",
        "primitive_obsession", "bumpy_road", "knowledge_loss"]


def _repos():
    import yaml
    return [r["name"] for r in yaml.safe_load(CONFIG.read_text())["repos"]]


def load_mean_col():
    col = {}
    for repo in _repos():
        p = RESULTS / f"health_defect_{repo}" / "naturalness.json"
        if p.exists():
            d = json.loads(p.read_text())
            col[repo] = {k.replace("\\", "/").strip("/"): v["mean_line_surprisal"]
                         for k, v in d["files"].items()}
    return col


def load_pagerank_col():
    col = {}
    for repo in _repos():
        p = RESULTS / f"health_defect_{repo}" / "graph_centrality.json"
        if p.exists():
            d = json.loads(p.read_text())
            pr = d.get("pagerank") or {}
            col[repo] = {k.replace("\\", "/").strip("/"): v for k, v in pr.items()}
    return col


CANDIDATES = {"naturalness_mean": load_mean_col, "pagerank": load_pagerank_col}


def oof_auc(X, y, groups):
    return ce._pooled_auc(y, ce._oof_predictions(X, y, groups))


def run(label: str, cand_name: str, col: dict):
    rows = ce.load_corpus(RESULTS, CONFIG, label)

    def cand(r):
        return (col.get(r["repo"]) or {}).get(r["file_path"])

    uni = [r for r in rows if cand(r) is not None]
    y = np.array([r["y"] for r in uni], int)
    groups = np.array([r["repo"] for r in uni])
    Xb, feats = ce.base_matrix(uni)            # 24 biomarkers + nloc_log
    natcol = np.array([float(cand(r)) for r in uni], float)
    idx = {f: i for i, f in enumerate(feats)}

    full = oof_auc(Xb, y, groups)
    full_nat = oof_auc(np.column_stack([Xb, natcol]), y, groups)

    print(f"\n=== candidate={cand_name}  label={label}  n={len(uni)}  "
          f"pos={int(y.sum())}  repos={len(set(groups))} ===")
    print(f"full (24+nloc)            OOF AUC {full:.4f}")
    print(f"full + {cand_name:18s} OOF AUC {full_nat:.4f}  "
          f"(Δ add = {full_nat-full:+.4f})")
    print(f"\n{'replace weak w':24s} {'full-w':>8s} {'full-w+nat':>11s} "
          f"{'Δ(repl vs full)':>16s}")
    repo_list = sorted(set(groups))
    idx_by_repo = {g: np.where(groups == g)[0] for g in repo_list}
    rng = random.Random(7)
    for w in WEAK:
        keep = [i for f, i in idx.items() if f != w]
        Xw = Xb[:, keep]
        Xw_nat = np.column_stack([Xw, natcol])
        a_drop = oof_auc(Xw, y, groups)
        a_repl = oof_auc(Xw_nat, y, groups)
        # cluster-bootstrap CI on (full-w+nat) - full
        oof_full = ce._oof_predictions(Xb, y, groups)
        oof_repl = ce._oof_predictions(Xw_nat, y, groups)
        deltas = []
        for _ in range(600):
            chosen = [repo_list[rng.randrange(len(repo_list))] for _ in repo_list]
            sel = np.concatenate([idx_by_repo[g] for g in chosen])
            yy = y[sel]
            if len(set(int(v) for v in yy)) < 2:
                continue
            af = ce._pooled_auc(yy, oof_full[sel])
            ar = ce._pooled_auc(yy, oof_repl[sel])
            if af == af and ar == ar:
                deltas.append(ar - af)
        deltas.sort()
        lo = deltas[int(0.025 * len(deltas))] if deltas else float("nan")
        hi = deltas[int(0.975 * len(deltas))] if deltas else float("nan")
        print(f"{w:24s} {a_drop:8.4f} {a_repl:11.4f} "
              f"{a_repl-full:+9.4f} [{lo:+.4f},{hi:+.4f}]")


if __name__ == "__main__":
    for cand_name, loader in CANDIDATES.items():
        col = loader()
        for lbl in ("keyword", "szz"):
            run(lbl, cand_name, col)
