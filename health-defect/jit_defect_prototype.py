#!/usr/bin/env python3
"""PROTOTYPE — just-in-time (commit-level) defect prediction.

The file-level health score is size-dominated because big files have more code
and more bugs. A different, well-established paradigm (Kamei et al., "A
large-scale empirical study of just-in-time quality assurance") predicts risk at
the **change** level: is *this commit* likely to introduce a defect? Its features
are properties of the *diff* (size, diffusion across files/dirs, change entropy,
purpose, author experience) — not the size of any one file — so it sidesteps the
file-size confound and is directly useful as a PR / pre-merge gate.

This is a feasibility probe on 1-2 cloned repos. NO repowise re-index — it is
pure ``git`` diff-walking:

  Labels (SZZ): a commit is defect-inducing iff a later bug-fix's blame points
  back to a line it authored (AG-SZZ refinements: skip cosmetic lines and
  fix-of-fix inducers). Reuses lib/szz's git helpers.

  Features (Kamei subset, from ``git show --numstat`` + commit metadata):
    LA, LD       lines added / deleted
    NF, ND, NS   files / directories / top-level subsystems touched
    entropy      Shannon entropy of the per-file change distribution (diffusion)
    FIX          is the commit itself a fix (fixes correlate with inducing)
    EXP          author's prior commit count (experience)
    age_days     commit age (older commits have had more time to be fixed)

  Evaluation: time-ordered split (train on earlier commits, test on later) →
  ROC AUC + effort-aware recall@20%-churn (inspect the 20% of changed lines the
  model flags riskiest; how many inducing commits caught). Compared to two
  trivial baselines: churn-only (LA+LD) and random.

CAVEAT (stated, not hidden): SZZ labels use *future* fixes, so the most recent
commits are under-labelled (not yet fixed). The probe drops the most recent
``--gap-days`` of history from evaluation to reduce this right-censoring. This is
a feasibility signal, not a calibrated result.

Run (venv python), e.g.:
    ../../.venv/Scripts/python.exe jit_defect_prototype.py \
        --repo ../repos/clap --source-root "" --ext .rs --window 1500
"""
from __future__ import annotations

import argparse
import math
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from lib.defect_counter import find_fix_commits  # type: ignore
from lib.szz import (  # type: ignore
    _blame_range,
    _deleted_line_ranges,
    _ext,
    _file_lines,
    _is_cosmetic_line,
    _parent,
)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def bug_inducing_set(repo, fixes, source_root, exts, fix_shas, max_fixes):
    """AG-SZZ bug-inducing commit SHAs over the most recent ``max_fixes`` fixes."""
    inducing: set[str] = set()
    for fix_sha, _msg in fixes[:max_fixes]:
        parent = _parent(repo, fix_sha)
        if parent is None:
            continue
        files = _git(["show", "--name-only", "--format=", fix_sha], repo).split("\n")
        for file in files:
            file = file.strip()
            if not file or not file.startswith(source_root) or not file.endswith(exts):
                continue
            ranges = _deleted_line_ranges(repo, parent, fix_sha, file)
            if not ranges:
                continue
            plines = _file_lines(repo, parent, file)
            n = len(plines)
            ext = _ext(file)
            for start, count in ranges:
                end = min(start + count - 1, n) if n else start + count - 1
                for lineno, blamed in _blame_range(repo, parent, file, start, end):
                    content = plines[lineno - 1] if 1 <= lineno <= n else ""
                    if _is_cosmetic_line(content, ext):
                        continue
                    if blamed in fix_shas:
                        continue  # fix-of-fix
                    inducing.add(blamed)
    return inducing


def commit_features(repo, window, source_root, exts):
    """Per-commit Kamei features over the most recent ``window`` non-merge commits."""
    fmt = "%H%x00%an%x00%ct"
    log = _git(["log", "--no-merges", f"-{window}", f"--format={fmt}"], repo)
    commits = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        sha, author, ct = line.split("\x00")
        commits.append({"sha": sha, "author": author, "ct": int(ct)})
    commits.reverse()  # oldest first → for EXP accrual + time split

    # author experience = prior commit count (over this window, oldest-first).
    exp_counter: dict[str, int] = defaultdict(int)
    now = max(c["ct"] for c in commits) if commits else 0
    feats = []
    for c in commits:
        sha = c["sha"]
        numstat = _git(["show", sha, "--numstat", "--format="], repo)
        la = ld = nf = 0
        dirs, subs, per_file = set(), set(), []
        for row in numstat.strip().split("\n"):
            if not row:
                continue
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            if not path.startswith(source_root) or not path.endswith(exts):
                continue
            a = int(a) if a.isdigit() else 0
            d = int(d) if d.isdigit() else 0
            la += a
            ld += d
            nf += 1
            churn = a + d
            if churn:
                per_file.append(churn)
            segs = path.split("/")
            dirs.add("/".join(segs[:-1]))
            subs.add(segs[0])
        exp = exp_counter[c["author"]]
        exp_counter[c["author"]] += 1
        if nf == 0:  # touched no source → skip
            continue
        total = sum(per_file) or 1
        entropy = -sum((p / total) * math.log2(p / total) for p in per_file) if per_file else 0.0
        feats.append({
            "sha": sha, "ct": c["ct"],
            "la": la, "ld": ld, "nf": nf, "nd": len(dirs), "ns": len(subs),
            "entropy": entropy, "exp": exp,
            "age_days": (now - c["ct"]) / 86400.0,
            "churn": la + ld,
        })
    return feats


def effort_recall(rows, score, frac=0.20):
    """Recall of inducing commits within the riskiest ``frac`` of total churn."""
    total_churn = sum(max(r["churn"], 1) for r in rows)
    total_pos = sum(r["y"] for r in rows)
    if total_pos == 0:
        return None
    order = sorted(range(len(rows)), key=lambda i: -score[i])
    spent, found = 0.0, 0
    budget = total_churn * frac
    for i in order:
        c = max(rows[i]["churn"], 1)
        if spent + c > budget and spent > 0:
            break
        spent += c
        found += rows[i]["y"]
    return found / total_pos


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--source-root", default="")
    ap.add_argument("--ext", default=".py", help="comma list, e.g. .py or .ts,.tsx")
    ap.add_argument("--window", type=int, default=1500, help="recent non-merge commits")
    ap.add_argument("--max-fixes", type=int, default=400)
    ap.add_argument("--gap-days", type=float, default=120.0)
    ap.add_argument("--train-frac", type=float, default=0.7)
    args = ap.parse_args()

    repo = str(args.repo.resolve())
    exts = tuple(args.ext.split(","))
    sroot = args.source_root

    # Whole-history fix set: start the range at the (first) root commit so the
    # find_fix_commits `t0..HEAD` range spans essentially all of history.
    roots = _git(["rev-list", "--max-parents=0", "HEAD"], repo).strip().split("\n")
    root = roots[-1] if roots and roots[-1] else "HEAD"
    fixes = find_fix_commits(repo, root, "HEAD", strategy="keyword")
    fix_shas = {s for s, _ in fixes}
    print(f"\n=== JIT prototype: {args.repo.name} ===")
    print(f"  fixes found: {len(fixes)} (using most recent {min(len(fixes), args.max_fixes)})")
    inducing = bug_inducing_set(repo, fixes, sroot, exts, fix_shas, args.max_fixes)
    print(f"  bug-inducing commits (AG-SZZ): {len(inducing)}")

    feats = commit_features(repo, args.window, sroot, exts)
    for r in feats:
        r["y"] = 1 if r["sha"] in inducing else 0
    # right-censoring guard: drop the most recent gap-days from evaluation.
    now = max(r["ct"] for r in feats)
    cutoff = now - args.gap_days * 86400.0
    eval_rows = [r for r in feats if r["ct"] <= cutoff]
    npos = sum(r["y"] for r in eval_rows)
    print(f"  commits (touch source, in window): {len(feats)}; "
          f"evaluable (older than {args.gap_days:.0f}d): {len(eval_rows)}; "
          f"inducing among them: {npos} ({npos/max(len(eval_rows),1):.1%})")
    if npos < 10:
        print("  too few positives for a stable estimate — widen --window / --max-fixes.")
        return

    cols = ["la", "ld", "nf", "nd", "ns", "entropy", "exp", "age_days"]
    X = np.array([[r[c] for c in cols] for r in eval_rows], float)
    # log-compress heavy-tailed size/diffusion counts.
    for j, c in enumerate(cols):
        if c in ("la", "ld", "nf", "nd", "ns", "exp"):
            X[:, j] = np.log1p(X[:, j])
    y = np.array([r["y"] for r in eval_rows], int)

    # time-ordered split (eval_rows already oldest-first).
    k = int(len(eval_rows) * args.train_frac)
    tr, te = slice(0, k), slice(k, None)
    if len(set(y[te])) < 2 or len(set(y[tr])) < 2:
        print("  degenerate split — adjust --train-frac.")
        return
    sc = StandardScaler().fit(X[tr])
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(sc.transform(X[tr]), y[tr])
    p = clf.predict_proba(sc.transform(X[te]))[:, 1]
    auc = roc_auc_score(y[te], p)

    test_rows = eval_rows[k:]
    churn_score = [r["churn"] for r in test_rows]
    rng = np.random.default_rng(0)
    rand_score = rng.random(len(test_rows))
    er_model = effort_recall(test_rows, list(p))
    er_churn = effort_recall(test_rows, churn_score)
    er_rand = effort_recall(test_rows, list(rand_score))

    print(f"\n  time-split test set: {len(test_rows)} commits, {sum(y[te])} inducing")
    print(f"  JIT model     AUC = {auc:.3f}   effort-recall@20%churn = {er_model:.3f}")
    print(f"  churn-only    AUC = {roc_auc_score(y[te], churn_score):.3f}   "
          f"effort-recall@20%churn = {er_churn:.3f}")
    print(f"  random        AUC = {roc_auc_score(y[te], rand_score):.3f}   "
          f"effort-recall@20%churn = {er_rand:.3f}")
    print("\n  standardized coefficients (what drives JIT risk):")
    for c, w in sorted(zip(cols, clf.coef_[0]), key=lambda t: -abs(t[1])):
        print(f"    {c:10s} {w:+.3f}")


if __name__ == "__main__":
    main()
