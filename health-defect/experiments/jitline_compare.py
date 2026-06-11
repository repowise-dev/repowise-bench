#!/usr/bin/env python3
"""jitline_compare.py — Phase-4 head-to-head: interpretable additive change
features vs a JITLine-style token bag-of-words model, SAME hunks, SAME
effort-aware localization protocol.

RESEARCH ARTIFACT (bench-only). Both arms are evaluated leave-one-repo-out and
ranked by effort-aware density (P(defect)/added-LOC), then scored on the identical
within-repo line-recall@k%LOC curve (`hunk_localization`). The only difference is
the feature set:

  * **additive_ship** — the Phase-4 interpretable change features (la, prior-fix
    recurrence, fix-prone file, test-co-change, lexical entropy, cosmetic flag);
    a transparent L2-logistic, exact additive attribution.
  * **token (JITLine-style)** — TF-IDF over the bag of tokens of the hunk's added
    lines (JITLine's feature philosophy). Classifier: class-balanced L2-logistic
    over the sparse TF-IDF (a linear model rather than JITLine's RandomForest, for
    tractability at 184k hunks — and a linear model's probabilities calibrate
    *better* for ranking, so this does not handicap the token arm). NO LIME: the
    token model scores the hunk directly and is localized through the SAME protocol
    as the additive arm — a cleaner apples-to-apples than mixing line-attribution
    schemes.

Headline: recall of bug-inducing lines @20% LOC for each arm + the additive−token
Δ with a repo-cluster bootstrap 95% CI.

Run (venv python), from health-defect/::

    C:/Users/ragha/Desktop/repowise/.venv/Scripts/python.exe jitline_compare.py \
        --results-dir <bench>/results [--out card.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hunk_localization as H  # noqa: E402

EFFORT_KS = H.EFFORT_KS


def load_both(results_dir: Path, repo: str):
    p1 = results_dir / f"health_defect_{repo}" / "hunk_dataset.json"
    p2 = results_dir / f"health_defect_{repo}" / "hunk_tokens.json"
    if not (p1.exists() and p2.exists()):
        return None
    ds = json.loads(p1.read_text())["rows"]
    tk = json.loads(p2.read_text())["rows"]
    if len(ds) != len(tk):
        print(f"  !! {repo}: hunk count mismatch ds={len(ds)} tk={len(tk)} — skipping")
        return None
    # sanity: labels must align position-for-position (same deterministic walk)
    if any(ds[i]["label"] != tk[i]["label"] for i in range(len(ds))):
        print(f"  !! {repo}: label misalignment — skipping")
        return None
    return ds, tk


def _eff(prob, la):
    return [prob[i] / max(la[i], 1.0) for i in range(len(la))]


def run(results_dir: Path, repos: list[str], n_boot: int = 1000, seed: int = 12345):
    data = {}
    for r in repos:
        b = load_both(results_dir, r)
        if b and b[0]:
            data[r] = b
    repo_list = sorted(data)
    if len(repo_list) < 3:
        raise SystemExit("need >=3 repos with both datasets")

    repo_hunks: dict[str, list[dict]] = {}
    scores = {"additive_ship": {}, "token": {}, "size_asc": {}, "random": {}}
    rng = random.Random(seed)

    for held in repo_list:
        tr_ds = [r for rp in repo_list if rp != held for r in data[rp][0]]
        tr_tk = [r for rp in repo_list if rp != held for r in data[rp][1]]
        te_ds, te_tk = data[held]
        ytr = np.array([r["label"] for r in tr_ds], int)
        yte = np.array([r["label"] for r in te_ds], int)
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        la = [float(r["la_hunk"]) for r in te_ds]
        repo_hunks[held] = te_ds

        # --- additive (shippable) arm ---
        tm = H._surp_train_mean(tr_ds)
        H._impute_rows(tr_ds, tm)
        H._impute_rows(te_ds, tm)
        sc, clf = H._fit(H.matrix(tr_ds, H.FEATURES_SHIP), ytr)
        pa = clf.predict_proba(sc.transform(H.matrix(te_ds, H.FEATURES_SHIP)))[:, 1]
        scores["additive_ship"][held] = _eff(pa.tolist(), la)

        # --- token (JITLine-style) arm ---
        vec = TfidfVectorizer(
            token_pattern=r"(?u)\S+", lowercase=False,
            max_features=20000, min_df=3,
        )
        Xtr = vec.fit_transform(r["tokens"] for r in tr_tk)
        Xte = vec.transform(r["tokens"] for r in te_tk)
        tclf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
        tclf.fit(Xtr, ytr)
        pt = tclf.predict_proba(Xte)[:, 1]
        scores["token"][held] = _eff(pt.tolist(), la)

        scores["size_asc"][held] = [-v for v in la]
        scores["random"][held] = [rng.random() for _ in te_ds]

    RANKERS = list(scores)
    loc = {}
    total_buggy = 0
    for i, rk in enumerate(RANKERS):
        loc[rk], tb = H.pooled_localization(repo_hunks, scores[rk], EFFORT_KS, seed=1 + i)
        total_buggy = max(total_buggy, tb)

    # bootstrap (resample repos) for each arm + the additive−token delta
    hk = list(repo_hunks)
    boot = {rk: {k: [] for k in EFFORT_KS} for rk in RANKERS}
    boot_delta = {"additive_vs_token": [], "additive_vs_sizeasc": [], "token_vs_sizeasc": []}
    brng = random.Random(seed)
    for b in range(n_boot):
        chosen = [hk[brng.randrange(len(hk))] for _ in hk]
        rh = {f"{rp}#{i}": repo_hunks[rp] for i, rp in enumerate(chosen)}
        lv = {}
        tb = 0
        for rk in RANKERS:
            rs = {f"{rp}#{i}": scores[rk][rp] for i, rp in enumerate(chosen)}
            lv[rk], tb = H.pooled_localization(rh, rs, EFFORT_KS, seed=1000 + b)
        if tb == 0:
            continue
        for rk in RANKERS:
            for k in EFFORT_KS:
                if lv[rk][k] is not None:
                    boot[rk][k].append(lv[rk][k])
        a, t, s = lv["additive_ship"][0.20], lv["token"][0.20], lv["size_asc"][0.20]
        if None not in (a, t, s):
            boot_delta["additive_vs_token"].append(a - t)
            boot_delta["additive_vs_sizeasc"].append(a - s)
            boot_delta["token_vs_sizeasc"].append(t - s)

    def ci(xs):
        if len(xs) < 20:
            return None
        s = sorted(xs)
        return [round(s[int(0.025 * len(s))], 4), round(s[int(0.975 * len(s))], 4)]

    card = {
        "n_repos": len(repo_list),
        "n_buggy_lines": total_buggy,
        "recall_at_loc": {
            f"{int(k*100)}%": {rk: round(loc[rk][k], 4) if loc[rk][k] is not None else None
                               for rk in RANKERS}
            for k in EFFORT_KS
        },
        "delta_at_20pct": {
            "additive_vs_token": {"point": round(loc["additive_ship"][0.20] - loc["token"][0.20], 4),
                                  "ci": ci(boot_delta["additive_vs_token"])},
            "additive_vs_sizeasc": {"point": round(loc["additive_ship"][0.20] - loc["size_asc"][0.20], 4),
                                    "ci": ci(boot_delta["additive_vs_sizeasc"])},
            "token_vs_sizeasc": {"point": round(loc["token"][0.20] - loc["size_asc"][0.20], 4),
                                 "ci": ci(boot_delta["token_vs_sizeasc"])},
        },
    }
    return card


def card_md(card: dict) -> str:
    L = [f"#### JITLine-style token model vs interpretable additive — "
         f"{card['n_repos']} repos · {card['n_buggy_lines']} buggy lines", ""]
    L.append("| k%LOC | additive_ship | token(JITLine) | size_asc(ManualUp) | random |")
    L.append("|--:|--:|--:|--:|--:|")
    for k, v in card["recall_at_loc"].items():
        L.append(f"| {k} | {v['additive_ship']} | {v['token']} | {v['size_asc']} | {v['random']} |")
    L.append("")
    d = card["delta_at_20pct"]
    L.append("**Δ @20%LOC (repo-cluster bootstrap CI):**")
    L.append(f"- additive − token: {d['additive_vs_token']['point']:+} {d['additive_vs_token']['ci']}")
    L.append(f"- additive − ManualUp: {d['additive_vs_sizeasc']['point']:+} {d['additive_vs_sizeasc']['ci']}")
    L.append(f"- token − ManualUp: {d['token_vs_sizeasc']['point']:+} {d['token_vs_sizeasc']['ci']}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--repo", default="")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    repos = args.repo.split(",") if args.repo else [r["name"] for r in cfg["repos"]]
    card = run(args.results_dir, repos)
    print(card_md(card))
    if args.out:
        args.out.write_text(json.dumps(card, indent=2))
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
