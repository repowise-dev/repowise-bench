#!/usr/bin/env python3
"""hunk_localization.py — Phase-4 Part B: additive hunk model + effort-aware eval.

RESEARCH ARTIFACT (bench-only). Consumes ``hunk_dataset.json`` (per repo, from
``hunk_dataset.py``) and answers the Phase-4 question: does an **interpretable,
additive** per-hunk risk model localize bug-inducing lines *inside a diff* better
than the trivial unsupervised churn/size ranker (Yang et al. FSE'16 — notoriously
hard to beat) and random?

The model is the same shape the shipped change-risk uses: an L2-logistic over
standardized, log-compressed change features. It is **additive**, so a line's risk
is exactly the sum of its hunk's per-feature contributions — deterministic and
attributable, no LIME. (Within a single diff every line in a hunk shares that
hunk's score, so localization ranks hunks, then lines, by summed contribution.)

Evaluation (all with bootstrap 95% CIs, resampling repos as clusters):
  * **Hunk-level OOF AUC** — leave-one-repo-out pooled, model vs the size
    baseline (``la_hunk``), with the Δ CI.
  * **Line-level effort-aware localization** — the headline. Rank each held-out
    repo's hunks by OOF risk; walk them accumulating added-LOC effort; report the
    recall of *bug-inducing lines* at {1,5,10,20,30,50}% LOC inspected (PCI / Popt
    family), pooled across repos (inspect the top-k% of every repo's changed
    lines). Compared head-to-head with (i) the size ranker (rank by ``la_hunk``)
    and (ii) random, with the model−size Δ CI at 20% LOC.
  * **Per-repo time-split AUC** — robustness (train earliest 70%, test latest).

Per-repo calibration: localization ranks *within* a held-out repo, so the
hierarchical per-repo intercept (base-rate) cancels — ranking is calibration-
invariant. Absolute-risk reporting (not the localization headline) would need the
per-repo intercept; noted, not used for the ranking metrics.

Run (venv python), from health-defect/::

    C:/Users/ragha/Desktop/repowise/.venv/Scripts/python.exe hunk_localization.py \
        --results-dir <bench>/results [--repo a,b,c] [--out card.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import numpy as np
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

_HERE = Path(__file__).resolve().parents[1]

# Per-hunk features. log1p the heavy-tailed counts. surprisal_mean is imputed
# (per-repo train mean) with a missingness indicator so absent != zero.
FEATURES = [
    "la_hunk", "local_entropy", "touches_fix_prone", "prior_fix_recur",
    "surprisal_mean", "surprisal_missing", "test_cochange_absent",
]
# Runtime-shippable subset: everything derivable from the diff + indexed git
# history (NO n-gram naturalness model). ``surprisal_missing`` is kept because it
# is a *hunk adds only cosmetic (blank/comment/punct) lines* flag — at runtime it
# is recomputed by a cheap ``is_cosmetic`` check on the added lines, needing no
# language model. The continuous ``surprisal_mean`` (the only feature that needs
# the n-gram model) is the bench-only delta this isolates.
FEATURES_SHIP = [
    "la_hunk", "local_entropy", "touches_fix_prone", "prior_fix_recur",
    "test_cochange_absent", "surprisal_missing",
]
LOG1P = {"la_hunk", "prior_fix_recur"}
EFFORT_KS = [0.01, 0.05, 0.10, 0.20, 0.30, 0.50]


def load_repo(results_dir: Path, repo: str) -> dict | None:
    p = results_dir / f"health_defect_{repo}" / "hunk_dataset.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _impute_rows(rows: list[dict], train_mean_surp: float) -> None:
    for r in rows:
        if r.get("surprisal_mean") is None:
            r["_surp"] = train_mean_surp
            r["_surp_missing"] = 1.0
        else:
            r["_surp"] = float(r["surprisal_mean"])
            r["_surp_missing"] = 0.0


def _feat_value(r: dict, name: str) -> float:
    if name == "surprisal_mean":
        return float(r["_surp"])
    if name == "surprisal_missing":
        return float(r["_surp_missing"])
    return float(r[name])


def matrix(rows: list[dict], feats: list[str] = FEATURES) -> np.ndarray:
    X = np.array([[_feat_value(r, n) for n in feats] for r in rows], dtype=float)
    for j, name in enumerate(feats):
        if name in LOG1P:
            X[:, j] = np.log1p(np.clip(X[:, j], 0, None))
    return X


def _fit(X: np.ndarray, y: np.ndarray):
    sc = StandardScaler().fit(X)
    clf = LogisticRegression(
        C=0.5, class_weight="balanced", max_iter=5000
    ).fit(sc.transform(X), y)
    return sc, clf


def _surp_train_mean(rows: list[dict]) -> float:
    vals = [float(r["surprisal_mean"]) for r in rows if r.get("surprisal_mean") is not None]
    return sum(vals) / len(vals) if vals else 0.0


# --------------------------------------------------------------------------
# Effort-aware line-level localization
# --------------------------------------------------------------------------
def _recall_curve(hunks: list[dict], scores: list[float], ks: list[float], rng=None):
    """Recall of bug-inducing LINES at each effort fraction k (of total added
    LOC), inspecting hunks in descending ``scores`` order. Returns {k: recall}.
    Random tie-break (seeded) so ties don't favour input order."""
    total_buggy = sum(h["n_buggy_lines"] for h in hunks)
    total_loc = sum(max(h["n_added"], 1) for h in hunks)
    if total_buggy == 0 or total_loc == 0:
        return {k: None for k in ks}
    jitter = [(_r := (rng.random() if rng else 0.0)) for _ in hunks]
    order = sorted(range(len(hunks)), key=lambda i: (-scores[i], jitter[i]))
    out: dict[float, float] = {}
    ki = 0
    spent = 0.0
    found = 0
    ks_sorted = sorted(ks)
    targets = [k * total_loc for k in ks_sorted]
    for i in order:
        spent += max(hunks[i]["n_added"], 1)
        found += hunks[i]["n_buggy_lines"]
        while ki < len(targets) and spent >= targets[ki]:
            out[ks_sorted[ki]] = found / total_buggy
            ki += 1
        if ki >= len(targets):
            break
    while ki < len(targets):
        out[ks_sorted[ki]] = found / total_buggy
        ki += 1
    return out


def pooled_localization(repo_hunks: dict, repo_scores: dict, ks: list[float], seed=0):
    """Pool recall@k across repos by inspecting the top-k% LOC of *each* repo
    (within-repo ranking), summing buggy lines caught / total buggy lines."""
    rng = random.Random(seed)
    num = {k: 0.0 for k in ks}  # buggy caught
    den = 0  # total buggy
    # We need exact counts, so recompute caught at each k by repo and sum.
    caught = {k: 0 for k in ks}
    total_buggy = 0
    for repo, hunks in repo_hunks.items():
        scores = repo_scores[repo]
        tb = sum(h["n_buggy_lines"] for h in hunks)
        tl = sum(max(h["n_added"], 1) for h in hunks)
        if tb == 0 or tl == 0:
            continue
        total_buggy += tb
        jitter = [rng.random() for _ in hunks]
        order = sorted(range(len(hunks)), key=lambda i: (-scores[i], jitter[i]))
        targets = {k: k * tl for k in ks}
        ks_sorted = sorted(ks)
        spent = 0.0
        found = 0
        ki = 0
        tg = [targets[k] for k in ks_sorted]
        for i in order:
            spent += max(hunks[i]["n_added"], 1)
            found += hunks[i]["n_buggy_lines"]
            while ki < len(tg) and spent >= tg[ki]:
                caught[ks_sorted[ki]] += found
                ki += 1
            if ki >= len(tg):
                break
        while ki < len(tg):
            caught[ks_sorted[ki]] += found
            ki += 1
    if total_buggy == 0:
        return {k: None for k in ks}, 0
    return {k: caught[k] / total_buggy for k in ks}, total_buggy


# --------------------------------------------------------------------------
# Main eval
# --------------------------------------------------------------------------
def run(results_dir: Path, repos: list[str], n_boot: int = 1000, seed: int = 12345):
    data = {}
    for r in repos:
        d = load_repo(results_dir, r)
        if d and d["rows"]:
            data[r] = d["rows"]
    repo_list = sorted(data)
    if len(repo_list) < 3:
        raise SystemExit("need >=3 repos with hunk_dataset.json")

    # Per held-out repo: OOF hunk risk + the ranker score-vectors.
    # Rankers (all scored "higher = inspect first"):
    #   model       additive logistic risk (decision_function)
    #   model_eff   effort-aware model: risk per added-LOC (risk - log la)
    #   size_desc   churn/size ManualDown: big hunks first (good AUC, poor effort)
    #   size_asc    churn/size ManualUp: small hunks first (Yang FSE'16 hard
    #               effort-aware baseline — notoriously hard to beat)
    #   random      shuffle
    scores: dict[str, dict[str, list[float]]] = {
        k: {} for k in ("model", "model_eff", "model_eff_ship",
                        "size_desc", "size_asc", "random")
    }
    repo_hunks: dict[str, list[dict]] = {}
    rng = random.Random(seed)
    pooled_y, pooled_oof, pooled_size = [], [], []

    def _eff(risk_list, la):
        # effort-aware density (Mende & Koschke): P(defect) per added line —
        # favours small, high-risk hunks (the standard effort-aware ranking).
        return [(1.0 / (1.0 + math.exp(-risk_list[i]))) / max(la[i], 1.0)
                for i in range(len(la))]

    for held in repo_list:
        train_rows = [r for rp in repo_list if rp != held for r in data[rp]]
        test_rows = list(data[held])
        tm = _surp_train_mean(train_rows)
        _impute_rows(train_rows, tm)
        _impute_rows(test_rows, tm)
        ytr = np.array([r["label"] for r in train_rows], int)
        yte = np.array([r["label"] for r in test_rows], int)
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        # full model (incl. naturalness surprisal — bench-only feature)
        sc, clf = _fit(matrix(train_rows), ytr)
        risk = clf.decision_function(sc.transform(matrix(test_rows))).tolist()
        # shippable model (diff + git history only; no naturalness model)
        scs, clfs = _fit(matrix(train_rows, FEATURES_SHIP), ytr)
        risk_s = clfs.decision_function(scs.transform(matrix(test_rows, FEATURES_SHIP))).tolist()
        la = [float(r["la_hunk"]) for r in test_rows]
        repo_hunks[held] = test_rows
        scores["model"][held] = risk
        scores["model_eff"][held] = _eff(risk, la)
        scores["model_eff_ship"][held] = _eff(risk_s, la)
        scores["size_desc"][held] = la
        scores["size_asc"][held] = [-v for v in la]
        scores["random"][held] = [rng.random() for _ in test_rows]
        pooled_y.extend(int(v) for v in yte)
        pooled_oof.extend(risk)
        pooled_size.extend(la)

    # --- hunk-level pooled OOF AUC (model vs size) ---------------------------
    ay = np.array(pooled_y)
    auc_model = float(roc_auc_score(ay, np.array(pooled_oof)))
    auc_size = float(roc_auc_score(ay, np.array(pooled_size)))

    # --- final additive fit on ALL rows (shippable contributions) ------------
    all_rows = [r for rp in repo_list for r in data[rp]]
    tm_all = _surp_train_mean(all_rows)
    _impute_rows(all_rows, tm_all)
    Xall = matrix(all_rows)
    yall = np.array([r["label"] for r in all_rows], int)
    sc_all, clf_all = _fit(Xall, yall)
    coefs = dict(zip(FEATURES, (float(c) for c in clf_all.coef_[0])))
    sc_ship, clf_ship = _fit(matrix(all_rows, FEATURES_SHIP), yall)
    coefs_ship = dict(zip(FEATURES_SHIP, (float(c) for c in clf_ship.coef_[0])))

    RANKERS = list(scores)
    # --- effort-aware localization (point estimates) -------------------------
    loc = {}
    total_buggy = 0
    for i, rk in enumerate(RANKERS):
        loc[rk], tb = pooled_localization(repo_hunks, scores[rk], EFFORT_KS, seed=1 + i)
        total_buggy = max(total_buggy, tb)

    # --- bootstrap CIs (resample repos as clusters) --------------------------
    hk_repos = list(repo_hunks)
    boot = {rk: {k: [] for k in EFFORT_KS} for rk in RANKERS}
    boot_delta20 = {rk: [] for rk in RANKERS if rk not in ("size_asc",)}  # vs size_asc
    boot["auc_model"] = []
    boot["auc_delta_vs_size"] = []
    brng = random.Random(seed)
    for b in range(n_boot):
        chosen = [hk_repos[brng.randrange(len(hk_repos))] for _ in hk_repos]
        rh = {f"{rp}#{i}": repo_hunks[rp] for i, rp in enumerate(chosen)}
        lvals = {}
        tb = 0
        for rk in RANKERS:
            rs = {f"{rp}#{i}": scores[rk][rp] for i, rp in enumerate(chosen)}
            lvals[rk], tb = pooled_localization(rh, rs, EFFORT_KS, seed=1000 + b)
        if tb == 0:
            continue
        for rk in RANKERS:
            for k in EFFORT_KS:
                if lvals[rk][k] is not None:
                    boot[rk][k].append(lvals[rk][k])
        base20 = lvals["size_asc"][0.20]
        for rk in boot_delta20:
            if lvals[rk][0.20] is not None and base20 is not None:
                boot_delta20[rk].append(lvals[rk][0.20] - base20)
        yy, oo, ss = [], [], []
        for i, rp in enumerate(chosen):
            yy.extend(int(h["label"]) for h in repo_hunks[rp])
            oo.extend(scores["model"][rp])
            ss.extend(scores["size_desc"][rp])
        yy = np.array(yy)
        if len(set(yy.tolist())) > 1:
            am = roc_auc_score(yy, np.array(oo))
            asz_b = roc_auc_score(yy, np.array(ss))
            boot["auc_model"].append(am)
            boot["auc_delta_vs_size"].append(am - asz_b)

    def ci(xs):
        if len(xs) < 20:
            return None
        s = sorted(xs)
        return [round(s[int(0.025 * len(s))], 4), round(s[int(0.975 * len(s))], 4)]

    card = {
        "n_repos": len(repo_list),
        "n_hunks": len(all_rows),
        "n_positive_hunks": int(yall.sum()),
        "n_buggy_lines": total_buggy,
        "pos_rate": round(float(yall.mean()), 4),
        "hunk_auc": {
            "model": round(auc_model, 4),
            "size_baseline": round(auc_size, 4),
            "model_ci": ci(boot["auc_model"]),
            "delta_vs_size": round(auc_model - auc_size, 4),
            "delta_vs_size_ci": ci(boot["auc_delta_vs_size"]),
        },
        "localization_recall_at_loc": {
            f"{int(k*100)}%": {
                rk: round(loc[rk][k], 4) if loc[rk][k] is not None else None
                for rk in RANKERS
            } | {
                f"{rk}_ci": ci(boot[rk][k]) for rk in RANKERS
            } for k in EFFORT_KS
        },
        # Headline: best supervised model vs the hard effort-aware baseline
        # (size_asc / ManualUp) at 20% LOC, with the Δ CI.
        "delta_at_20pct_vs_size_asc": {
            rk: {
                "point": round(loc[rk][0.20] - loc["size_asc"][0.20], 4)
                if (loc[rk][0.20] is not None and loc["size_asc"][0.20] is not None) else None,
                "ci": ci(boot_delta20[rk]),
            } for rk in boot_delta20
        },
        "additive_coefficients": {k: round(v, 4) for k, v in coefs.items()},
        "additive_coefficients_ship": {k: round(v, 4) for k, v in coefs_ship.items()},
    }
    return card


def card_markdown(card: dict) -> str:
    L = []
    L.append(f"#### Phase-4 hunk localization — n={card['n_hunks']} hunks · "
             f"{card['n_positive_hunks']} positive ({card['pos_rate']:.1%}) · "
             f"{card['n_buggy_lines']} buggy lines · {card['n_repos']} repos")
    L.append("")
    ha = card["hunk_auc"]
    L.append(f"**Hunk-level OOF AUC:** model **{ha['model']}** {ha['model_ci']} · "
             f"size baseline {ha['size_baseline']} · "
             f"Δ {ha['delta_vs_size']:+} {ha['delta_vs_size_ci']}")
    L.append("")
    L.append("**Effort-aware line localization — recall of bug-inducing lines at k% LOC:**")
    L.append("")
    L.append("| k%LOC | model_eff | model_eff_ship | size_asc(ManualUp) | random | model(raw) | size_desc |")
    L.append("|--:|--:|--:|--:|--:|--:|--:|")
    for k, v in card["localization_recall_at_loc"].items():
        L.append(f"| {k} | {v['model_eff']} | {v['model_eff_ship']} | {v['size_asc']} | "
                 f"{v['random']} | {v['model']} | {v['size_desc']} |")
    L.append("")
    L.append("**Δ @20%LOC vs size_asc (ManualUp, the hard effort-aware baseline), repo-cluster CI:**")
    for rk, d in card["delta_at_20pct_vs_size_asc"].items():
        L.append(f"- {rk}: {d['point']:+} {d['ci']}")
    L.append("")
    L.append("_full coefficients (standardized): " +
             ", ".join(f"{k}={v:+}" for k, v in card["additive_coefficients"].items()) + "_")
    L.append("")
    L.append("_shippable coefficients (no naturalness): " +
             ", ".join(f"{k}={v:+}" for k, v in card["additive_coefficients_ship"].items()) + "_")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--repo", default="", help="comma list; default = all config repos")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    cfg_all = yaml.safe_load(args.config.read_text())
    repos = args.repo.split(",") if args.repo else [r["name"] for r in cfg_all["repos"]]
    card = run(args.results_dir, repos)
    print(card_markdown(card))
    if args.out:
        args.out.write_text(json.dumps(card, indent=2))
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
