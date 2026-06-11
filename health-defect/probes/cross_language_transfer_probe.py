#!/usr/bin/env python3
"""cross_language_transfer_probe.py — leave-one-LANGUAGE-out (LOLO) defect transfer.

RESEARCH ARTIFACT (bench-only). Tests whether defect *structure* (the
coefficient pattern over health biomarkers + size) transfers across programming
languages, or whether only prior-fix history generalizes.

Protocol mirrors the documented file-level calibration EXACTLY (so numbers are
comparable to the leave-one-repo-out / LORO baselines):
  * feature matrix = 24 severity-weighted biomarker hit columns + log1p(NLOC),
    built via error_analysis.build_rows (the same substrate the calibration fits)
  * L2-logistic, C=0.5, class_weight balanced, StandardScaler — the shipped
    calibrate_health_weights design.

LOLO: for each of the 9 languages, train on the OTHER 8 languages' files,
predict the held-out language. Headline metric = WITHIN-REPO-standardized,
within-language pooled AUC (so the language base-rate / intercept cannot
contaminate the ranking, and Simpson's paradox across differently-sized repos is
avoided — we never pool raw scores across repos; we pool tie-aware AUC).

Part A: file-level LOLO + baselines + feature-group ablations + coef stability.
Part B: hunk-level LOLO effort-aware localization (if hunk_dataset.json present).

Run (venv python only):
    set PYTHONIOENCODING=utf-8
    C:/Users/ragha/Desktop/repowise/.venv/Scripts/python.exe \
        local-stash/cross_language_transfer_probe.py
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import warnings

import numpy as np

warnings.filterwarnings("ignore")
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

# Reuse the documented feature construction verbatim.
RND = Path(r"C:/Users/ragha/Desktop/repowise-bench-rnd/health-defect")
_HD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HD / "experiments"))
sys.path.insert(0, str(_HD))
sys.path.insert(0, str(RND))
import error_analysis as ea  # noqa: E402  (build_rows / auc / load_config_langs)

CONFIG = _HD / "config.yaml"
RESULTS = Path(__file__).resolve().parents[2] / "results"
OUT_JSON = RESULTS / "cross_language_transfer_scorecard.json"
OUT_MD = RESULTS / "cross_language_transfer_report.md"

C_FIXED = 0.5

# ---- biomarker roster (kept identical to candidate_eval.BIOMARKERS) ----------
BIOMARKERS = [
    "brain_method", "low_cohesion", "god_class", "nested_complexity",
    "complex_method", "bumpy_road", "complex_conditional", "large_method",
    "primitive_obsession", "dry_violation", "untested_hotspot", "coverage_gap",
    "developer_congestion", "knowledge_loss", "hidden_coupling", "function_hotspot",
    "code_age_volatility", "ownership_risk", "churn_risk", "change_entropy",
    "co_change_scatter", "prior_defect",
    "large_assertion_block", "duplicated_assertion_block",
]
# Feature-group split (the mechanism test). PROCESS = git/history-derived;
# STRUCTURAL = static code-shape biomarkers. nloc_log lives in BOTH base designs
# as the size control but is assigned to neither group's *isolated* ablation
# except as noted (we run structural WITH nloc_log since structural biomarkers
# are size-adjacent, and process WITHOUT nloc_log; full has it once).
PROCESS_BM = [
    "developer_congestion", "knowledge_loss", "code_age_volatility",
    "ownership_risk", "churn_risk", "change_entropy", "co_change_scatter",
    "prior_defect", "function_hotspot", "hidden_coupling",
]
STRUCTURAL_BM = [
    "brain_method", "low_cohesion", "god_class", "nested_complexity",
    "complex_method", "bumpy_road", "complex_conditional", "large_method",
    "primitive_obsession", "dry_violation", "untested_hotspot", "coverage_gap",
    "large_assertion_block", "duplicated_assertion_block",
]
assert set(PROCESS_BM) | set(STRUCTURAL_BM) == set(BIOMARKERS)


def _hit(row, bt):
    return float((row.get("biomarkers") or {}).get(bt, 0.0))


def design(rows, bm_cols, with_nloc):
    feats = list(bm_cols) + (["nloc_log"] if with_nloc else [])
    X = np.array(
        [[_hit(r, bt) for bt in bm_cols]
         + ([float(np.log1p(max(r["nloc"], 0)))] if with_nloc else [])
         for r in rows],
        dtype=float,
    )
    return X, feats


def single_col(rows, kind):
    """Univariate baseline columns (oriented higher = riskier)."""
    if kind == "nloc":
        return np.array([float(np.log1p(max(r["nloc"], 0))) for r in rows])
    if kind == "churn":
        return np.array([float(r.get("commit_count_90d") or 0.0) for r in rows])
    if kind == "prior_fix":
        # prior_defect_count from joined_data (true prior-fix history), fall back
        # to the prior_defect biomarker hit if the count is absent.
        out = []
        for r in rows:
            v = r.get("prior_defect_count")
            out.append(float(v) if v is not None else _hit(r, "prior_defect"))
        return np.array(out)
    raise ValueError(kind)


def fit_logit(X, y):
    sc = StandardScaler().fit(X)
    clf = LogisticRegression(
        penalty="l2", C=C_FIXED, class_weight="balanced", max_iter=5000
    ).fit(sc.transform(X), y)
    return sc, clf


# ---- within-repo-standardized, within-language pooled AUC ---------------------
def within_repo_rank(scores, repos):
    """Map raw scores to per-repo percentile ranks in [0,1] so we never pool raw
    scores across differently-sized repos (the Simpson's-paradox guard)."""
    out = np.empty(len(scores))
    for rp in set(repos):
        idx = [i for i, g in enumerate(repos) if g == rp]
        vals = np.array([scores[i] for i in idx])
        order = vals.argsort()
        ranks = np.empty(len(vals))
        # average-rank for ties
        srt = vals[order]
        i = 0
        rr = np.empty(len(vals))
        while i < len(srt):
            j = i
            while j + 1 < len(srt) and srt[j + 1] == srt[i]:
                j += 1
            rr[i:j + 1] = (i + j) / 2.0
            i = j + 1
        ranks[order] = rr
        ranks = ranks / max(len(vals) - 1, 1)
        for k, i in enumerate(idx):
            out[i] = ranks[k]
    return out


def lang_pooled_auc(y, scores, repos):
    """Tie-aware AUC over the held-out language, after within-repo ranking."""
    ranked = within_repo_rank(scores, repos)
    return ea.auc(list(y), list(ranked))


def per_repo_auc(y, scores, repos):
    out = {}
    for rp in sorted(set(repos)):
        idx = [i for i, g in enumerate(repos) if g == rp]
        yy = [int(y[i]) for i in idx]
        ss = [scores[i] for i in idx]
        a = ea.auc(yy, ss)
        out[rp] = round(a, 4) if a is not None else None
    return out


# ---- LOLO core ---------------------------------------------------------------
def lolo_scores(rows, model_fn):
    """For each held-out language, train model on the other 8 languages' rows,
    return a dict held_lang -> (y, oof_scores, repos)."""
    langs = sorted(set(r["language"] for r in rows))
    out = {}
    for held in langs:
        train = [r for r in rows if r["language"] != held]
        test = [r for r in rows if r["language"] == held]
        ytr = np.array([r["y"] for r in train], int)
        yte = np.array([r["y"] for r in test], int)
        if len(set(ytr.tolist())) < 2 or len(set(yte.tolist())) < 2:
            continue
        scores = model_fn(train, test, ytr)
        out[held] = (yte, np.asarray(scores, float),
                     [r["repo"] for r in test])
    return out


def model_full(bm_cols, with_nloc):
    def fn(train, test, ytr):
        Xtr, _ = design(train, bm_cols, with_nloc)
        Xte, _ = design(test, bm_cols, with_nloc)
        sc, clf = fit_logit(Xtr, ytr)
        return clf.predict_proba(sc.transform(Xte))[:, 1]
    return fn


def model_univariate(kind):
    def fn(train, test, ytr):
        xtr = single_col(train, kind).reshape(-1, 1)
        xte = single_col(test, kind).reshape(-1, 1)
        sc, clf = fit_logit(xtr, ytr)
        return clf.predict_proba(sc.transform(xte))[:, 1]
    return fn


def model_random(seed=7):
    rng = random.Random(seed)

    def fn(train, test, ytr):
        return [rng.random() for _ in test]
    return fn


# ---- LORO reference (leave-one-repo-out), within-repo ranked, pooled ----------
def loro_pooled_auc(rows, bm_cols=BIOMARKERS, with_nloc=True):
    repos = sorted(set(r["repo"] for r in rows))
    oof = {}
    for held in repos:
        train = [r for r in rows if r["repo"] != held]
        test = [r for r in rows if r["repo"] == held]
        ytr = np.array([r["y"] for r in train], int)
        if len(set(ytr.tolist())) < 2:
            continue
        Xtr, _ = design(train, bm_cols, with_nloc)
        Xte, _ = design(test, bm_cols, with_nloc)
        sc, clf = fit_logit(Xtr, ytr)
        p = clf.predict_proba(sc.transform(Xte))[:, 1]
        for r, pv in zip(test, p):
            oof[(r["repo"], r["file_path"])] = pv
    y = [r["y"] for r in rows if (r["repo"], r["file_path"]) in oof]
    s = [oof[(r["repo"], r["file_path"])] for r in rows
         if (r["repo"], r["file_path"]) in oof]
    rp = [r["repo"] for r in rows if (r["repo"], r["file_path"]) in oof]
    return lang_pooled_auc(np.array(y), np.array(s), rp)


# ---- bootstrap: resample repos WITHIN the held-out language -------------------
def boot_lang_auc(y, scores, repos, n_boot=1000, seed=12345):
    rng = random.Random(seed)
    uniq = sorted(set(repos))
    if len(uniq) < 2:
        return None  # cannot resample clusters meaningfully with 1 repo
    idx_by = {g: [i for i, x in enumerate(repos) if x == g] for g in uniq}
    samples = []
    for _ in range(n_boot):
        chosen = [uniq[rng.randrange(len(uniq))] for _ in uniq]
        idx = [i for g in chosen for i in idx_by[g]]
        yy = np.array([y[i] for i in idx])
        if len(set(yy.tolist())) < 2:
            continue
        ss = np.array([scores[i] for i in idx])
        rr = [repos[i] for i in idx]
        a = lang_pooled_auc(yy, ss, rr)
        if a is not None:
            samples.append(a)
    if len(samples) < 20:
        return None
    s = sorted(samples)
    return [round(s[int(0.025 * len(s))], 4), round(s[int(0.975 * len(s))], 4)]


# ==========================================================================
# Part A
# ==========================================================================
def run_part_a(label):
    langs_map, roots = ea.load_config_langs(CONFIG)
    rows = ea.build_rows(RESULTS, langs_map, roots, label=label)
    langs = sorted(set(r["language"] for r in rows))

    # headline models
    full_fn = model_full(BIOMARKERS, with_nloc=True)
    proc_fn = model_full(PROCESS_BM, with_nloc=False)
    struct_fn = model_full(STRUCTURAL_BM, with_nloc=True)

    models = {
        "full": full_fn,
        "process_only": proc_fn,
        "structural_only": struct_fn,
        "prior_fix_only": model_univariate("prior_fix"),
        "nloc_only": model_univariate("nloc"),
        "churn_only": model_univariate("churn"),
        "random": model_random(),
    }
    model_out = {name: lolo_scores(rows, fn) for name, fn in models.items()}

    # LORO reference (full model)
    loro = round(loro_pooled_auc(rows), 4)

    # per-language headline table
    per_lang = {}
    for held in langs:
        rec = {}
        for name, mo in model_out.items():
            if held not in mo:
                rec[name] = {"auc": None, "ci": None}
                continue
            y, s, rp = mo[held]
            a = lang_pooled_auc(y, s, rp)
            ci = boot_lang_auc(y, s, rp)
            rec[name] = {"auc": round(a, 4) if a is not None else None, "ci": ci}
        # within-language fit (train on held lang's OTHER repos, LORO inside lang)
        in_lang = [r for r in rows if r["language"] == held]
        wl = None
        if len(set(r["repo"] for r in in_lang)) >= 2:
            try:
                wl = round(loro_pooled_auc(in_lang), 4)
            except Exception:
                wl = None
        # per-repo full-model AUC under the LOLO fold
        per_repo = None
        if held in model_out["full"]:
            y, s, rp = model_out["full"][held]
            per_repo = per_repo_auc(y, s, rp)
        rec["_within_language_loro"] = wl
        rec["_n_files"] = len(in_lang)
        rec["_n_pos"] = sum(r["y"] for r in in_lang)
        rec["_n_repos"] = len(set(r["repo"] for r in in_lang))
        rec["_per_repo_full_auc"] = per_repo
        per_lang[held] = rec

    # ---- coefficient stability across the 9 LOLO training sets ----------------
    feats = BIOMARKERS + ["nloc_log"]
    fold_coefs = {f: [] for f in feats}
    for held in langs:
        train = [r for r in rows if r["language"] != held]
        ytr = np.array([r["y"] for r in train], int)
        if len(set(ytr.tolist())) < 2:
            continue
        Xtr, fnames = design(train, BIOMARKERS, with_nloc=True)
        sc, clf = fit_logit(Xtr, ytr)
        for f, c in zip(fnames, clf.coef_[0]):
            fold_coefs[f].append(float(c))
    coef_stability = {}
    for f, cs in fold_coefs.items():
        if not cs:
            continue
        arr = np.array(cs)
        pos = int((arr > 0).sum())
        neg = int((arr < 0).sum())
        maj = max(pos, neg)
        coef_stability[f] = {
            "mean": round(float(arr.mean()), 4),
            "min": round(float(arr.min()), 4),
            "max": round(float(arr.max()), 4),
            "range": round(float(arr.max() - arr.min()), 4),
            "n_folds": len(cs),
            "pct_sign_consistent": round(maj / len(cs), 3),
            "dominant_sign": "+" if pos >= neg else "-",
        }

    corpus = {
        "label": label,
        "n_files": len(rows),
        "n_positives": sum(r["y"] for r in rows),
        "languages": {l: {"files": sum(1 for r in rows if r["language"] == l),
                          "pos": sum(r["y"] for r in rows if r["language"] == l),
                          "repos": sorted(set(r["repo"] for r in rows
                                              if r["language"] == l))}
                      for l in langs},
        "loro_reference_auc": loro,
    }
    return {"corpus": corpus, "per_language": per_lang,
            "coef_stability": coef_stability}


# ==========================================================================
# Part B — hunk-level LOLO
# ==========================================================================
import hunk_localization as hl  # noqa: E402


def run_part_b():
    langs_map, _ = ea.load_config_langs(CONFIG)
    # group repos by language
    by_lang = defaultdict(list)
    for repo, lang in langs_map.items():
        d = hl.load_repo(RESULTS, repo)
        if d and d.get("rows"):
            by_lang[lang].append((repo, d["rows"]))
    langs = sorted(by_lang)
    if len(langs) < 3:
        return {"skipped": "fewer than 3 languages have hunk_dataset.json"}

    EFFORT_KS = hl.EFFORT_KS
    FS = hl.FEATURES_SHIP

    per_lang = {}
    for held in langs:
        train_rows = [r for L in langs if L != held for (_, rs) in by_lang[L] for r in rs]
        # held-out: per-repo hunk lists
        held_repos = {repo: list(rs) for repo, rs in by_lang[held]}
        ytr = np.array([r["label"] for r in train_rows], int)
        # need both classes in train and at least some buggy lines held out
        if len(set(ytr.tolist())) < 2:
            per_lang[held] = {"skipped": "train single-class"}
            continue
        tm = hl._surp_train_mean(train_rows)
        hl._impute_rows(train_rows, tm)
        sc, clf = hl._fit(hl.matrix(train_rows, FS), ytr)

        repo_hunks, repo_scores = {}, {"model_eff": {}, "prior_fix": {},
                                       "size_asc": {}, "random": {}}
        rng = random.Random(13)
        n_buggy = 0
        for repo, rows_r in held_repos.items():
            rr = list(rows_r)
            hl._impute_rows(rr, tm)
            risk = clf.decision_function(sc.transform(hl.matrix(rr, FS)))
            la = [float(r["la_hunk"]) for r in rr]
            eff = [(1.0 / (1.0 + math.exp(-risk[i]))) / max(la[i], 1.0)
                   for i in range(len(rr))]
            repo_hunks[repo] = rr
            repo_scores["model_eff"][repo] = eff
            # one-feature prior-fix ranker (effort-aware: prior_fix per added LOC)
            repo_scores["prior_fix"][repo] = [
                float(r["prior_fix_recur"]) / max(r["la_hunk"], 1.0) for r in rr]
            repo_scores["size_asc"][repo] = [-v for v in la]
            repo_scores["random"][repo] = [rng.random() for _ in rr]
            n_buggy += sum(h["n_buggy_lines"] for h in rr)

        if n_buggy == 0:
            per_lang[held] = {"skipped": "no buggy lines in held-out language"}
            continue

        recalls = {}
        for rk in repo_scores:
            loc, tb = hl.pooled_localization(repo_hunks, repo_scores[rk],
                                             EFFORT_KS, seed=1)
            recalls[rk] = {f"{int(k*100)}%": (round(loc[k], 4) if loc[k] is not None else None)
                           for k in EFFORT_KS}
        per_lang[held] = {
            "n_repos": len(held_repos),
            "n_hunks": sum(len(v) for v in repo_hunks.values()),
            "n_buggy_lines": n_buggy,
            "recall_at_loc": recalls,
        }
    return {"per_language": per_lang, "effort_ks": EFFORT_KS}


# ==========================================================================
def main():
    print("=== Part A: file-level LOLO (keyword) ===")
    a_kw = run_part_a("keyword")
    print("=== Part A: file-level LOLO (ag_szz) ===")
    a_szz = run_part_a("szz")
    print("=== Part B: hunk-level LOLO ===")
    b = run_part_b()

    scorecard = {
        "experiment": "cross_language_leave_one_language_out_defect_transfer",
        "C_fixed": C_FIXED,
        "part_a_keyword": a_kw,
        "part_a_szz": a_szz,
        "part_b_hunk": b,
    }
    OUT_JSON.write_text(json.dumps(scorecard, indent=2))
    print(f"Wrote {OUT_JSON}")
    write_report(scorecard)
    print(f"Wrote {OUT_MD}")


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "n/a"


def _ci(c):
    return f"[{c[0]:.3f},{c[1]:.3f}]" if c else "—"


def write_report(sc):
    a = sc["part_a_keyword"]
    az = sc["part_a_szz"]
    L = []
    L.append("# Cross-language defect-structure transfer (LOLO)\n")
    c = a["corpus"]
    L.append(f"**Corpus (keyword label):** {c['n_files']} files, "
             f"{c['n_positives']} positives, 9 languages, 21 repos. "
             f"LORO full-model reference pooled AUC = **{c['loro_reference_auc']}** "
             f"(within-repo ranked).\n")
    L.append("Per-language: " + "; ".join(
        f"{l} ({d['files']}f/{d['pos']}p/{len(d['repos'])}r)"
        for l, d in c["languages"].items()) + "\n")

    # Headline
    L.append("## Headline — file-level LOLO pooled AUC (within-repo ranked), keyword\n")
    L.append("| held-out lang | n_pos | full LOLO | prior-fix | nloc | churn | random | within-lang LORO | LORO ref |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for l, rec in a["per_language"].items():
        def cell(name):
            r = rec.get(name, {})
            return f"{_fmt(r.get('auc'))} {_ci(r.get('ci'))}"
        L.append(f"| {l} | {rec['_n_pos']} | {cell('full')} | {cell('prior_fix_only')} | "
                 f"{cell('nloc_only')} | {cell('churn_only')} | {cell('random')} | "
                 f"{_fmt(rec['_within_language_loro'])} | {c['loro_reference_auc']} |")
    # pooled-over-languages means (unweighted, honest about noise)
    def lang_mean(name):
        vals = [r[name]["auc"] for r in a["per_language"].values()
                if r.get(name, {}).get("auc") is not None]
        return round(sum(vals) / len(vals), 4) if vals else None
    L.append(f"\n_Unweighted mean across 9 language folds — full: "
             f"{lang_mean('full')}, prior-fix: {lang_mean('prior_fix_only')}, "
             f"nloc: {lang_mean('nloc_only')}, churn: {lang_mean('churn_only')}, "
             f"random: {lang_mean('random')}._\n")

    # per-repo detail
    L.append("### Per-repo full-model AUC under each LOLO fold (the honest view)\n")
    for l, rec in a["per_language"].items():
        pr = rec.get("_per_repo_full_auc") or {}
        L.append(f"- **{l}**: " + ", ".join(f"{k}={v}" for k, v in pr.items()))
    L.append("")

    # Ablation
    L.append("## Ablation — process-only vs structural-only vs full (keyword LOLO AUC)\n")
    L.append("| held-out lang | full | process-only | structural-only |")
    L.append("|---|--:|--:|--:|")
    for l, rec in a["per_language"].items():
        L.append(f"| {l} | {_fmt(rec['full']['auc'])} | "
                 f"{_fmt(rec['process_only']['auc'])} | "
                 f"{_fmt(rec['structural_only']['auc'])} |")
    L.append(f"\n_Mean — full {lang_mean('full')}, process-only "
             f"{lang_mean('process_only')}, structural-only "
             f"{lang_mean('structural_only')}._\n")

    # Coef stability
    L.append("## Coefficient stability across the 9 LOLO training folds (keyword)\n")
    L.append("| feature | mean | range[min,max] | %sign-consistent | dom sign |")
    L.append("|---|--:|--:|--:|:--:|")
    cs = a["coef_stability"]
    order = sorted(cs, key=lambda f: -abs(cs[f]["mean"]))
    for f in order:
        d = cs[f]
        L.append(f"| {f} | {d['mean']:+.3f} | [{d['min']:+.3f},{d['max']:+.3f}] "
                 f"| {d['pct_sign_consistent']:.0%} | {d['dominant_sign']} |")
    L.append("")

    # SZZ replication
    L.append("## SZZ replication — file-level LOLO pooled AUC (ag_szz label)\n")
    L.append(f"_LORO ref (szz) = {az['corpus']['loro_reference_auc']}_\n")
    L.append("| held-out lang | n_pos | full | prior-fix | nloc | churn |")
    L.append("|---|--:|--:|--:|--:|--:|")
    for l, rec in az["per_language"].items():
        L.append(f"| {l} | {rec['_n_pos']} | {_fmt(rec['full']['auc'])} | "
                 f"{_fmt(rec['prior_fix_only']['auc'])} | "
                 f"{_fmt(rec['nloc_only']['auc'])} | {_fmt(rec['churn_only']['auc'])} |")
    def lang_mean_z(name):
        vals = [r[name]["auc"] for r in az["per_language"].values()
                if r.get(name, {}).get("auc") is not None]
        return round(sum(vals) / len(vals), 4) if vals else None
    L.append(f"\n_Mean (szz) — full {lang_mean_z('full')}, prior-fix "
             f"{lang_mean_z('prior_fix_only')}, nloc {lang_mean_z('nloc_only')}, "
             f"churn {lang_mean_z('churn_only')}._\n")

    # Part B
    b = sc["part_b_hunk"]
    L.append("## Part B — hunk-level LOLO effort-aware localization\n")
    if b.get("skipped"):
        L.append(f"_Skipped: {b['skipped']}_\n")
    else:
        L.append("Recall of bug-inducing lines @20% LOC (within-language pooled, "
                 "trained on 8 other languages):\n")
        L.append("| held-out lang | n_buggy | model_eff@20 | prior_fix@20 | size_asc(ManualUp)@20 | random@20 |")
        L.append("|---|--:|--:|--:|--:|--:|")
        for l, rec in b["per_language"].items():
            if rec.get("skipped"):
                L.append(f"| {l} | — | _{rec['skipped']}_ |||| ")
                continue
            r = rec["recall_at_loc"]
            L.append(f"| {l} | {rec['n_buggy_lines']} | {r['model_eff'].get('20%')} | "
                     f"{r['prior_fix'].get('20%')} | {r['size_asc'].get('20%')} | "
                     f"{r['random'].get('20%')} |")
    L.append("")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
