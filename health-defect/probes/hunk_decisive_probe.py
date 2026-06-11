#!/usr/bin/env python3
"""Phase-4 decisive probe (settles the two open questions):

A. ONE-FEATURE TEST. Does the full additive model beat the obvious one-line
   heuristic built from its strongest feature — rank by prior_fix_recur / lines-
   added — at corpus-level effort-aware line localization? If the model still wins,
   it earns its keep; if the heuristic matches, ship the heuristic.

B. WITHIN-FILE TEST. prior_fix_recur is a FILE property (identical for every hunk
   in the same file), so it ranks which *file* in a PR is risky, not which *line*.
   The real test of a line-level surface: inside a single changed file (>=2 hunks,
   >=1 buggy line), does the model beat "biggest hunk first"? Reports how many buggy
   lines even live in multi-hunk files (the addressable surface), top-ranked-hunk
   hit rate, and recall@50% within-file LOC, with repo-cluster bootstrap CIs.

Run from health-defect/ with the venv python (reuses hunk_localization)."""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

HD = Path(r"C:/Users/ragha/Desktop/repowise-bench-rnd/health-defect")
sys.path.insert(0, str(HD))
import hunk_localization as H  # noqa: E402

R = Path("C:/Users/ragha/Desktop/repowise/repowise-bench/results")
EFFORT_KS = [0.10, 0.20, 0.30, 0.50]


def load():
    repos = [r["name"] for r in yaml.safe_load((HD / "config.yaml").read_text())["repos"]]
    data = {}
    for rp in repos:
        d = H.load_repo(R, rp)
        if d and d["rows"]:
            data[rp] = d["rows"]
    return data


def loo_scores(data):
    """Per repo: model_eff_ship (density P/la), model_raw (P), aligned to rows."""
    repo_list = sorted(data)
    model_eff, model_raw = {}, {}
    for held in repo_list:
        tr = [r for rp in repo_list if rp != held for r in data[rp]]
        te = list(data[held])
        tm = H._surp_train_mean(tr)
        H._impute_rows(tr, tm)
        H._impute_rows(te, tm)
        ytr = np.array([r["label"] for r in tr])
        yte = np.array([r["label"] for r in te])
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            model_eff[held] = [0.0] * len(te)
            model_raw[held] = [0.0] * len(te)
            continue
        sc, clf = H._fit(H.matrix(tr, H.FEATURES_SHIP), ytr)
        p = clf.predict_proba(sc.transform(H.matrix(te, H.FEATURES_SHIP)))[:, 1]
        la = [max(float(r["la_hunk"]), 1.0) for r in te]
        model_eff[held] = [p[i] / la[i] for i in range(len(te))]
        model_raw[held] = list(map(float, p))
    return model_eff, model_raw


# ---------------- Part A: corpus one-feature test ----------------
def part_a(data, model_eff):
    repo_hunks = {rp: data[rp] for rp in data}
    scores = {
        "model_eff_ship": model_eff,
        "prior_density": {rp: [float(r["prior_fix_recur"]) / max(r["la_hunk"], 1)
                               for r in data[rp]] for rp in data},
        "prior_raw": {rp: [float(r["prior_fix_recur"]) for r in data[rp]] for rp in data},
        "size_asc": {rp: [-float(r["la_hunk"]) for r in data[rp]] for rp in data},
        "random": {rp: [random.Random(7).random() for _ in data[rp]] for rp in data},
    }
    # fix random per-repo deterministically
    for rp in data:
        rng = random.Random(hash(rp) & 0xffff)
        scores["random"][rp] = [rng.random() for _ in data[rp]]

    loc = {}
    for i, rk in enumerate(scores):
        loc[rk], _ = H.pooled_localization(repo_hunks, scores[rk], EFFORT_KS, seed=1 + i)

    # bootstrap delta model - prior_density @20%
    hk = list(repo_hunks)
    deltas = []
    prior_vs_mu = []
    brng = random.Random(123)
    for b in range(1000):
        chosen = [hk[brng.randrange(len(hk))] for _ in hk]
        rh = {f"{rp}#{i}": repo_hunks[rp] for i, rp in enumerate(chosen)}
        lm, tb = H.pooled_localization(
            rh, {f"{rp}#{i}": model_eff[rp] for i, rp in enumerate(chosen)}, [0.20], seed=2000 + b)
        lp, _ = H.pooled_localization(
            rh, {f"{rp}#{i}": scores["prior_density"][rp] for i, rp in enumerate(chosen)}, [0.20], seed=2000 + b)
        ls, _ = H.pooled_localization(
            rh, {f"{rp}#{i}": scores["size_asc"][rp] for i, rp in enumerate(chosen)}, [0.20], seed=2000 + b)
        if tb and lm[0.20] is not None and lp[0.20] is not None:
            deltas.append(lm[0.20] - lp[0.20])
            prior_vs_mu.append(lp[0.20] - ls[0.20])

    def ci(xs):
        s = sorted(xs)
        return [round(s[int(.025 * len(s))], 4), round(s[int(.975 * len(s))], 4)] if len(xs) >= 20 else None
    return loc, ci(deltas), ci(prior_vs_mu)


# ---------------- Part B: within-file test ----------------
def part_b(data, model_eff, model_raw):
    # group hunks by (repo, commit, file); keep multi-hunk groups with a buggy line
    groups = []  # each: dict(repo, idxs, hunks, scores...)
    total_buggy_all = 0
    for rp in data:
        rows = data[rp]
        by = defaultdict(list)
        for i, r in enumerate(rows):
            by[(r["commit"], r["file_path"])].append(i)
            total_buggy_all += r["n_buggy_lines"]
        for key, idxs in by.items():
            tb = sum(rows[i]["n_buggy_lines"] for i in idxs)
            if len(idxs) >= 2 and tb >= 1:
                groups.append((rp, idxs))
    buggy_in_groups = sum(sum(data[rp][i]["n_buggy_lines"] for i in idxs) for rp, idxs in groups)

    rankers = ["model_eff", "model_raw", "size_desc", "size_asc", "random"]

    def group_scores(rp, idxs, rk, rng):
        rows = data[rp]
        if rk == "model_eff":
            return [model_eff[rp][i] for i in idxs]
        if rk == "model_raw":
            return [model_raw[rp][i] for i in idxs]
        if rk == "size_desc":
            return [float(rows[i]["la_hunk"]) for i in idxs]
        if rk == "size_asc":
            return [-float(rows[i]["la_hunk"]) for i in idxs]
        return [rng.random() for _ in idxs]

    def eval_groups(group_list):
        # returns dict rk -> (top_hunk_hit_rate, recall@50LOC)
        out = {}
        for rk in rankers:
            rng = random.Random(99)
            hits = 0
            ng = 0
            caught = 0
            tot = 0
            for rp, idxs in group_list:
                rows = data[rp]
                sc = group_scores(rp, idxs, rk, rng)
                jit = [rng.random() for _ in idxs]
                order = sorted(range(len(idxs)), key=lambda j: (-sc[j], jit[j]))
                # top-ranked hunk hit
                ng += 1
                if rows[idxs[order[0]]]["n_buggy_lines"] > 0:
                    hits += 1
                # recall@50% within-file LOC
                tl = sum(max(rows[i]["n_added"], 1) for i in idxs)
                tb = sum(rows[i]["n_buggy_lines"] for i in idxs)
                tot += tb
                spent = 0.0
                f = 0
                for j in order:
                    spent += max(rows[idxs[j]]["n_added"], 1)
                    f += rows[idxs[j]]["n_buggy_lines"]
                    if spent >= 0.5 * tl:
                        break
                caught += f
            out[rk] = (hits / ng if ng else None, caught / tot if tot else None)
        return out

    point = eval_groups(groups)

    # bootstrap over repos for the two headline deltas @ recall@50 and top-hit
    repos = sorted({rp for rp, _ in groups})
    by_repo = defaultdict(list)
    for g in groups:
        by_repo[g[0]].append(g)
    brng = random.Random(321)
    d_top = []  # model_eff top-hit - size_desc top-hit
    d_rec = []  # model_eff recall50 - size_desc recall50
    for b in range(1000):
        chosen = [repos[brng.randrange(len(repos))] for _ in repos]
        gl = [g for rp in chosen for g in by_repo[rp]]
        if not gl:
            continue
        e = eval_groups(gl)
        if e["model_eff"][0] is not None and e["size_desc"][0] is not None:
            d_top.append(e["model_eff"][0] - e["size_desc"][0])
        if e["model_eff"][1] is not None and e["size_desc"][1] is not None:
            d_rec.append(e["model_eff"][1] - e["size_desc"][1])

    def ci(xs):
        s = sorted(xs)
        return [round(s[int(.025 * len(s))], 4), round(s[int(.975 * len(s))], 4)] if len(xs) >= 20 else None
    return {
        "n_groups": len(groups),
        "buggy_lines_in_multihunk_files": buggy_in_groups,
        "total_buggy_lines": total_buggy_all,
        "frac_buggy_addressable_within_file": round(buggy_in_groups / total_buggy_all, 4) if total_buggy_all else None,
        "point": {rk: {"top_hunk_hit": round(point[rk][0], 4), "recall@50LOC": round(point[rk][1], 4)} for rk in rankers},
        "model_eff_minus_size_desc": {"top_hunk_hit_ci": ci(d_top), "recall50_ci": ci(d_rec)},
    }


def main():
    data = load()
    model_eff, model_raw = loo_scores(data)

    print("=" * 70)
    print("PART A — full model vs the one-feature heuristic (corpus, effort-aware)")
    print("=" * 70)
    loc, delta_ci, prior_vs_mu_ci = part_a(data, model_eff)
    print(f"{'ranker':16s} " + " ".join(f"{int(k*100)}%" .rjust(7) for k in EFFORT_KS))
    for rk in loc:
        print(f"{rk:16s} " + " ".join(f"{loc[rk][k]:.3f}".rjust(7) for k in EFFORT_KS))
    print(f"\nΔ @20%LOC  model_eff_ship − prior_density : "
          f"{loc['model_eff_ship'][0.20]-loc['prior_density'][0.20]:+.4f}  CI {delta_ci}")
    print(f"Δ @20%LOC  prior_density − ManualUp(size_asc): "
          f"{loc['prior_density'][0.20]-loc['size_asc'][0.20]:+.4f}  CI {prior_vs_mu_ci}")

    print("\n" + "=" * 70)
    print("PART B — within a single changed FILE, does the model beat biggest-first?")
    print("=" * 70)
    b = part_b(data, model_eff, model_raw)
    print(f"multi-hunk-file groups (>=2 hunks, >=1 buggy line): {b['n_groups']}")
    print(f"buggy lines that even LIVE in multi-hunk files: {b['buggy_lines_in_multihunk_files']}"
          f" / {b['total_buggy_lines']} ({b['frac_buggy_addressable_within_file']:.1%}) "
          f"— the rest are in single-hunk files (line==file)")
    print(f"\n{'ranker':12s} {'top_hunk_hit':>13s} {'recall@50LOC':>13s}")
    for rk, v in b["point"].items():
        print(f"{rk:12s} {v['top_hunk_hit']:>13.3f} {v['recall@50LOC']:>13.3f}")
    print(f"\nmodel_eff − size_desc  top_hunk_hit CI : {b['model_eff_minus_size_desc']['top_hunk_hit_ci']}")
    print(f"model_eff − size_desc  recall@50   CI : {b['model_eff_minus_size_desc']['recall50_ci']}")

    out = {"part_a_recall": {rk: {str(k): loc[rk][k] for k in EFFORT_KS} for rk in loc},
           "part_a_delta_model_minus_prior_ci": delta_ci,
           "part_a_delta_prior_minus_manualup_ci": prior_vs_mu_ci,
           "part_b": b}
    (R / "phase4_decisive_scorecard.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote {R / 'phase4_decisive_scorecard.json'}")


if __name__ == "__main__":
    main()
