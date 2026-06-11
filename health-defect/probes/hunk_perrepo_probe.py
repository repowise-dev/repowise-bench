#!/usr/bin/env python3
"""Throwaway: per-repo recall@20%LOC breakdown for the Phase-4 hunk localizer
(model_eff full / shippable vs ManualUp vs random). Confirms the pooled win is
not a single-repo artifact. Run from health-defect/ with the venv python."""
import math
import random
import sys
from pathlib import Path

import numpy as np
import yaml

HD = Path(r"C:/Users/ragha/Desktop/repowise-bench-rnd/health-defect")
sys.path.insert(0, str(HD))
import hunk_localization as H  # noqa: E402

R = Path(__file__).resolve().parents[2] / "results"
repos = [r["name"] for r in yaml.safe_load((HD / "config.yaml").read_text())["repos"]]
data = {}
for rp in repos:
    d = H.load_repo(R, rp)
    if d and d["rows"]:
        data[rp] = d["rows"]
repo_list = sorted(data)
print(f"{'repo':12s} {'pos':>4} {'buggy':>5} {'m_eff':>6} {'ship':>6} {'MUp':>6} {'rand':>6}")
wins = 0
n = 0
for held in repo_list:
    tr = [r for rp in repo_list if rp != held for r in data[rp]]
    te = list(data[held])
    tm = H._surp_train_mean(tr)
    H._impute_rows(tr, tm)
    H._impute_rows(te, tm)
    ytr = np.array([r["label"] for r in tr])
    yte = np.array([r["label"] for r in te])
    if len(set(ytr)) < 2 or len(set(yte)) < 2:
        print(f"{held:12s} {int(yte.sum()):>4} (skip)")
        continue
    sc, clf = H._fit(H.matrix(tr), ytr)
    risk = clf.decision_function(sc.transform(H.matrix(te)))
    scs, clfs = H._fit(H.matrix(tr, H.FEATURES_SHIP), ytr)
    risks = clfs.decision_function(scs.transform(H.matrix(te, H.FEATURES_SHIP)))
    la = [float(r["la_hunk"]) for r in te]
    eff = [(1 / (1 + math.exp(-risk[i]))) / max(la[i], 1) for i in range(len(la))]
    effs = [(1 / (1 + math.exp(-risks[i]))) / max(la[i], 1) for i in range(len(la))]
    rng = random.Random(0)
    rnd = [rng.random() for _ in te]
    sasc = [-v for v in la]

    def rec(score):
        return H._recall_curve(te, score, [0.20], rng=random.Random(1))[0.20]

    me, ms, mm, mr = rec(eff), rec(effs), rec(sasc), rec(rnd)
    if me is not None and mm is not None:
        n += 1
        wins += 1 if me >= mm else 0
    fmt = lambda x: f"{x:.3f}" if x is not None else "  -  "
    print(f"{held:12s} {int(yte.sum()):>4} {sum(r['n_buggy_lines'] for r in te):>5} "
          f"{fmt(me):>6} {fmt(ms):>6} {fmt(mm):>6} {fmt(mr):>6}")
print(f"\nmodel_eff >= ManualUp in {wins}/{n} repos with both defined")
