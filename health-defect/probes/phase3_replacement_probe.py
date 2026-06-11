#!/usr/bin/env python3
"""Throwaway probe (local-stash, NOT committed): do the Phase-3 candidates earn a
slot by *replacing* a weak/floored biomarker, not just by *adding* (the §3 gate
tests addition only)? Generalizes ``naturalness_replacement_probe.py`` to the
three Phase-3 columns (change bursts, error-handling density, review coverage).

For each weak biomarker w and each candidate column it computes LOO pooled OOF
AUC of: full (24+nloc), full-w, full-w+cand — on the candidate's computable
universe — with a cluster-bootstrap CI on (full-w+cand) − full. Reuses
candidate_eval internals verbatim.
"""
from __future__ import annotations

import json
import random
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np

_HD_RND = Path(r"C:\Users\ragha\Desktop\repowise-bench-rnd\health-defect")
_HD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HD))
sys.path.insert(0, str(_HD / "experiments"))
sys.path.insert(0, str(_HD_RND))
import candidate_eval as ce  # noqa: E402
import change_burst_experiment as cbe  # noqa: E402

RESULTS = Path(__file__).resolve().parents[2] / "results"
CONFIG = _HD / "config.yaml"
WEAK = ["low_cohesion", "brain_method", "dry_violation", "developer_congestion",
        "primitive_obsession", "bumpy_road", "knowledge_loss"]


def _repos():
    import yaml
    return [r["name"] for r in yaml.safe_load(CONFIG.read_text())["repos"]]


def _norm(p):
    return p.replace("\\", "/").strip("/")


def load_bursts():
    """n_bursts G=7 B=2 (the within-band-passing winner) from change_times.json."""
    times = {}
    for repo in _repos():
        p = RESULTS / f"health_defect_{repo}" / "change_times.json"
        if p.exists():
            d = json.loads(p.read_text())
            times[repo] = {k: v for k, v in d.items() if k != "_meta"}
    col = {}
    for repo, files in times.items():
        col[repo] = {_norm(f): cbe.burst_features(ts, 7, 2)["n_bursts"]
                     for f, ts in files.items()}
    return col


def load_eh_density():
    col = {}
    for repo in _repos():
        p = RESULTS / f"health_defect_{repo}" / "error_handling.json"
        if p.exists():
            d = json.loads(p.read_text())
            col[repo] = {_norm(k): (float(r["eh_count"]) / float(r.get("code_lines", 1) or 1))
                         for k, r in d["files"].items()}
    return col


def load_review():
    p = RESULTS / "review_coverage_columns.json"
    if not p.exists():
        return {}
    by_feat = json.loads(p.read_text()).get("by_feat", {})
    rf = by_feat.get("reviewed_fraction", {})
    return {repo: {_norm(k): v for k, v in files.items()} for repo, files in rf.items()}


CANDIDATES = {"n_bursts_G7B2": load_bursts, "eh_density": load_eh_density,
              "reviewed_fraction": load_review}


def oof_auc(X, y, groups):
    return ce._pooled_auc(y, ce._oof_predictions(X, y, groups))


def run(label, cand_name, col):
    if not col:
        print(f"\n=== {cand_name} {label}: no column data — skipped ===")
        return
    rows = ce.load_corpus(RESULTS, CONFIG, label)

    def cand(r):
        return (col.get(r["repo"]) or {}).get(r["file_path"])

    uni = [r for r in rows if cand(r) is not None]
    y = np.array([r["y"] for r in uni], int)
    groups = np.array([r["repo"] for r in uni])
    Xb, feats = ce.base_matrix(uni)
    cc = np.array([float(cand(r)) for r in uni], float)
    idx = {f: i for i, f in enumerate(feats)}
    full = oof_auc(Xb, y, groups)
    full_c = oof_auc(np.column_stack([Xb, cc]), y, groups)
    print(f"\n=== candidate={cand_name}  label={label}  n={len(uni)}  "
          f"pos={int(y.sum())}  repos={len(set(groups))} ===")
    print(f"full (24+nloc)            OOF AUC {full:.4f}")
    print(f"full + {cand_name:18s} OOF AUC {full_c:.4f}  (Δ add = {full_c-full:+.4f})")
    print(f"{'replace weak w':24s} {'full-w':>8s} {'full-w+c':>9s} {'Δ(repl vs full)':>16s}")
    repo_list = sorted(set(groups))
    idx_by_repo = {g: np.where(groups == g)[0] for g in repo_list}
    rng = random.Random(7)
    for w in WEAK:
        keep = [i for f, i in idx.items() if f != w]
        Xw = Xb[:, keep]
        Xw_c = np.column_stack([Xw, cc])
        a_drop = oof_auc(Xw, y, groups)
        a_repl = oof_auc(Xw_c, y, groups)
        oof_full = ce._oof_predictions(Xb, y, groups)
        oof_repl = ce._oof_predictions(Xw_c, y, groups)
        deltas = []
        for _ in range(500):
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
        print(f"{w:24s} {a_drop:8.4f} {a_repl:9.4f} {a_repl-full:+9.4f} [{lo:+.4f},{hi:+.4f}]")


if __name__ == "__main__":
    only = sys.argv[1:] or list(CANDIDATES)
    for cand_name in only:
        col = CANDIDATES[cand_name]()
        for lbl in ("keyword", "szz"):
            run(lbl, cand_name, col)
