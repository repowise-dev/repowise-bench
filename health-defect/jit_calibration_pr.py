#!/usr/bin/env python3
"""PR-granularity recalibration of the shipped change-risk model.

``jit_calibration.py`` fits one row per non-merge commit. That is the wrong unit
for what the product actually scores: most modern repos squash-merge, the PR bot
scores whole PR diffs by construction, and a per-commit-calibrated band reads a
PR-sized change as several commits' worth of diff against a one-commit scale.

This script changes two things and measures whether either earns its place:

1. **The unit.** Rows are ``--first-parent`` spans. A merge commit's span is the
   diff it brought onto the mainline (its PR); a plain first-parent commit is a
   span of one. Repos with no merge commits have no recoverable PR boundary and
   are excluded rather than guessed at.

2. **The features.** Two size-orthogonal additions, both ratios so neither grows
   with diff size:

   - ``fix_density`` — churn-weighted mean of the touched files' prior bug-fix
     counts, each fix decayed by how long ago it landed. Counted strictly
     *before* the span, so it never sees the future fixes the SZZ labels are
     built from.
   - ``test_gap`` — share of changed source churn in files the span did not also
     touch a test alongside. A git-derivable stand-in for the runtime's
     line-precise coverage gap, which cannot be computed here: the corpus clones
     have no ingested coverage map, so a real coverage feature has no ground
     truth to fit against on this corpus.

Everything else is held identical to ``jit_calibration.py`` — same AG-SZZ labels,
same right-censoring gap, same L2-logistic, same leave-one-repo-out protocol —
so a difference in the reported AUC is a difference in unit and features, not in
method.

Run (venv python), from health-defect/:
    ../../.venv/Scripts/python.exe jit_calibration_pr.py \
        --repos clap,fd,gin,fastify,bat,chi --window 1500 --gap-days 120
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent / "experiments"))

import numpy as np
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from jit_defect_prototype import bug_inducing_set  # type: ignore
from lib.defect_counter import find_fix_commits  # type: ignore

HERE = Path(__file__).resolve().parent
REPOS = (HERE.parent / "repos").resolve()

#: Feature set under test. The first seven are today's; the last two are the
#: size-orthogonal additions this recalibration exists to evaluate.
COLS = ["la", "ld", "nf", "nd", "ns", "entropy", "exp", "fix_density", "test_gap"]
LOG1P = {"la", "ld", "nf", "nd", "ns", "exp", "fix_density"}

#: Half-life for prior-fix recency decay. Matches the product's 180-day
#: ``PRIOR_DEFECT_WINDOW_DAYS``: defect history is a slower-moving cluster than
#: recent churn, so a fix from six months ago still counts, at half weight.
FIX_HALF_LIFE_DAYS = 180.0

#: Repos below this many merge commits have no recoverable PR boundary (they
#: squash-merge, so the mainline is already one-commit-per-PR and grouping would
#: be a no-op dressed up as a unit change). Excluded from the fit, and named in
#: the output so the exclusion is visible rather than silent.
MIN_MERGES = 60


def _git(args: list[str], cwd: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def load_repo_cfg() -> dict[str, dict]:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    return {r["name"]: r for r in cfg.get("repos", [])}


def _is_test_path(path: str) -> bool:
    low = path.lower()
    return (
        "test" in low
        or "spec" in low
        or low.endswith(("_test.go", ".test.ts", ".test.js", ".spec.ts", ".spec.js"))
    )


def fix_history(repo: str, exts: tuple[str, ...], source_root: str) -> dict[str, list[float]]:
    """Per-file bug-fix timestamps, whole history, one walk.

    Keyword rule matches ``find_fix_commits`` (and the product's
    ``is_fix_commit``), so what is counted here is what the product counts.
    """
    out = _git(
        ["log", "--no-merges", "--format=%x1e%H%x1f%ct%x1f%s", "--name-only"], repo
    )
    from lib.defect_counter import _DEFAULT_EXCLUDE, _DEFAULT_INCLUDE  # type: ignore

    def is_fix(subject: str) -> bool:
        if any(p.search(subject) for p in _DEFAULT_EXCLUDE):
            return False
        return any(p.search(subject) for p in _DEFAULT_INCLUDE)

    history: dict[str, list[float]] = defaultdict(list)
    for block in out.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        head = lines[0].split("\x1f")
        if len(head) != 3:
            continue
        _sha, ts, subject = head[0], float(head[1]), head[2]
        if not is_fix(subject):
            continue
        for path in lines[1:]:
            path = path.strip()
            if path and path.startswith(source_root) and path.endswith(exts):
                history[path].append(ts)
    return history


def _decayed_fixes(timestamps: list[float], now: float) -> float:
    """Prior fixes on one file, each weighted by ``0.5 ** (age / half-life)``."""
    total = 0.0
    for ts in timestamps:
        if ts >= now:
            continue  # strictly before: never let a future fix inform the score
        total += 0.5 ** ((now - ts) / (FIX_HALF_LIFE_DAYS * 86400.0))
    return total


def first_parent_spans(repo: str, window: int) -> list[dict]:
    """The mainline's ``--first-parent`` spans, oldest first.

    Each span is one row: a merge's span is the set of commits it brought in, a
    plain first-parent commit is a span containing only itself.
    """
    log = _git(
        ["log", "--first-parent", f"-{window}", "--format=%H%x00%P%x00%an%x00%ct", "HEAD"],
        repo,
    )
    spans: list[dict] = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\x00")
        if len(parts) != 4:
            continue
        sha, parents_raw, author, ct = parts
        parents = parents_raw.split()
        base = parents[0] if parents else ""
        is_merge = len(parents) >= 2
        if is_merge:
            members = [
                s for s in _git(["rev-list", f"{base}..{sha}", "--no-merges"], repo).split("\n") if s
            ]
        else:
            members = [sha]
        spans.append({
            "sha": sha, "base": base, "author": author, "ct": int(ct),
            "members": members, "is_merge": is_merge,
        })
    spans.reverse()  # oldest first, for experience accrual and the time split
    return spans


def span_features(
    repo: str,
    spans: list[dict],
    source_root: str,
    exts: tuple[str, ...],
    history: dict[str, list[float]],
) -> list[dict]:
    """One feature row per span."""
    exp_counter: dict[str, int] = defaultdict(int)
    rows: list[dict] = []
    for span in spans:
        sha, base = span["sha"], span["base"]
        numstat = (
            _git(["diff", "--numstat", base, sha], repo)
            if base
            else _git(["show", sha, "--numstat", "--format="], repo)
        )
        la = ld = nf = 0
        dirs: set[str] = set()
        subs: set[str] = set()
        per_file: list[int] = []
        weighted_fixes = 0.0
        churn_total = 0
        test_dirs: set[str] = set()
        source_churn: list[tuple[str, int]] = []
        touched_tests = False
        for row in numstat.strip().split("\n"):
            if not row:
                continue
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            a_raw, d_raw, path = parts
            a = int(a_raw) if a_raw.isdigit() else 0
            d = int(d_raw) if d_raw.isdigit() else 0
            if _is_test_path(path):
                touched_tests = True
                test_dirs.add("/".join(path.split("/")[:-1]))
            if not path.startswith(source_root) or not path.endswith(exts):
                continue
            la += a
            ld += d
            nf += 1
            churn = a + d
            if churn:
                per_file.append(churn)
            segs = path.split("/")
            dirs.add("/".join(segs[:-1]))
            subs.add(segs[0])
            if not _is_test_path(path):
                source_churn.append((path, churn))
            weighted_fixes += churn * _decayed_fixes(history.get(path, []), float(span["ct"]))
            churn_total += churn

        # Experience accrues per span, matching the row unit: a maintainer with
        # ten merged PRs is what "experienced" means when the row is a PR.
        exp = exp_counter[span["author"]]
        exp_counter[span["author"]] += 1
        if nf == 0:
            continue

        total = sum(per_file)
        entropy = (
            -sum((p / total) * math.log2(p / total) for p in per_file if p > 0)
            if total > 0 and len(per_file) >= 2
            else 0.0
        )
        # Churn-weighted mean prior-fix pressure: a ratio, so it does not grow
        # with diff size. A 40-line edit to a file fixed 20 times outscores a
        # 4000-line edit to files never fixed.
        fix_density = weighted_fixes / churn_total if churn_total else 0.0
        src_total = sum(c for _, c in source_churn)
        test_gap = 0.0 if touched_tests else (1.0 if src_total else 0.0)

        rows.append({
            "sha": sha, "ct": span["ct"], "members": span["members"],
            "la": la, "ld": ld, "nf": nf, "nd": len(dirs), "ns": len(subs),
            "entropy": entropy, "exp": exp,
            "fix_density": fix_density, "test_gap": test_gap,
            "churn": la + ld, "is_merge": span["is_merge"],
        })
    return rows


def build_repo(name: str, rc: dict, window: int, max_fixes: int, gap_days: float):
    repo = str((REPOS / name).resolve())
    exts = tuple(rc.get("extensions", [".py"]))
    sroot = rc.get("source_root", "")
    merges = int(_git(["rev-list", "--count", "--merges", "HEAD"], repo).strip() or 0)
    if merges < MIN_MERGES:
        return None, merges, 0, 0

    roots = _git(["rev-list", "--max-parents=0", "HEAD"], repo).strip().split("\n")
    root = roots[-1] if roots and roots[-1] else "HEAD"
    fixes = find_fix_commits(repo, root, "HEAD", strategy="keyword")
    fix_shas = {s for s, _ in fixes}
    inducing = bug_inducing_set(repo, fixes, sroot, exts, fix_shas, max_fixes)

    history = fix_history(repo, exts, sroot)
    spans = first_parent_spans(repo, window)
    rows = span_features(repo, spans, sroot, exts, history)
    # A span is defect-inducing iff any commit it brought in is.
    for r in rows:
        r["y"] = 1 if any(m in inducing for m in r["members"]) else 0

    now = max((r["ct"] for r in rows), default=0)
    cutoff = now - gap_days * 86400.0
    return [r for r in rows if r["ct"] <= cutoff], merges, len(fixes), len(inducing)


def matrix(rows, cols=None):
    cols = cols or COLS
    X = np.array([[r[c] for c in cols] for r in rows], float)
    for j, c in enumerate(cols):
        if c in LOG1P:
            X[:, j] = np.log1p(X[:, j])
    y = np.array([r["y"] for r in rows], int)
    return X, y


def loo_auc(rows_by_repo, cols=None):
    """Leave-one-repo-out pooled OOF AUC (model vs churn). Protocol unchanged."""
    repos = list(rows_by_repo)
    oy, op, oc = [], [], []
    for held in repos:
        tr = [r for rp in repos if rp != held for r in rows_by_repo[rp]]
        te = rows_by_repo[held]
        Xtr, ytr = matrix(tr, cols)
        Xte, yte = matrix(te, cols)
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        sc = StandardScaler().fit(Xtr)
        clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(
            sc.transform(Xtr), ytr
        )
        op.extend(float(v) for v in clf.predict_proba(sc.transform(Xte))[:, 1])
        oc.extend(float(r["churn"]) for r in te)
        oy.extend(int(v) for v in yte)
    if len(set(oy)) < 2:
        return None
    model = roc_auc_score(oy, op)
    churn = roc_auc_score(oy, oc)
    # Per-repo AUC as well as pooled. Pooling compares predictions made by six
    # different held-out fits on one scale, which penalizes a fitted model for a
    # calibration difference rather than a ranking one; a raw feature has no such
    # handicap. Within a repo that artifact is gone, and within a repo is also
    # how the score is actually used.
    per_repo = {}
    for held in repos:
        te = rows_by_repo[held]
        tr = [r for rp in repos if rp != held for r in rows_by_repo[rp]]
        Xtr, ytr = matrix(tr, cols)
        Xte, yte = matrix(te, cols)
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        sc = StandardScaler().fit(Xtr)
        clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(
            sc.transform(Xtr), ytr
        )
        p = clf.predict_proba(sc.transform(Xte))[:, 1]
        per_repo[held] = {
            "model": round(float(roc_auc_score(yte, p)), 4),
            "churn": round(float(roc_auc_score(yte, [r["churn"] for r in te])), 4),
            "la": round(float(roc_auc_score(yte, [r["la"] for r in te])), 4),
            "fix_density": round(float(roc_auc_score(yte, [r["fix_density"] for r in te])), 4),
            "n": len(te),
        }
    rng = random.Random(7)
    diffs = []
    n = len(oy)
    ay, ap, ac = np.array(oy), np.array(op), np.array(oc)
    for _ in range(500):
        idx = [rng.randrange(n) for _ in range(n)]
        yy = ay[idx]
        if len(set(yy.tolist())) < 2:
            continue
        diffs.append(roc_auc_score(yy, ap[idx]) - roc_auc_score(yy, ac[idx]))
    diffs.sort()
    ci = (
        [round(float(np.mean(diffs)), 4), round(diffs[int(0.025 * len(diffs))], 4),
         round(diffs[int(0.975 * len(diffs))], 4)]
        if diffs
        else [None, None, None]
    )
    return {"model_oof_auc": round(model, 4), "churn_oof_auc": round(churn, 4),
            "delta_vs_churn_ci": ci, "n": len(oy), "pos": int(sum(oy)),
            "per_repo": per_repo}


def univariate_delta(rows, cols, mean, std, coef, intercept) -> dict:
    """How closely ``la`` alone reproduces the full score.

    The headline symptom: if this is small, every other feature is decoration
    and the score is diff size in a different unit.
    """
    la_i = cols.index("la")

    def full(r):
        z = intercept
        for j, c in enumerate(cols):
            x = math.log1p(r[c]) if c in LOG1P else r[c]
            z += coef[j] * (x - mean[j]) / (std[j] or 1.0)
        return round(10.0 / (1.0 + math.exp(-z)), 1)

    def la_only(r):
        x = math.log1p(r["la"])
        z = intercept + coef[la_i] * (x - mean[la_i]) / (std[la_i] or 1.0)
        return round(10.0 / (1.0 + math.exp(-z)), 1)

    deltas = sorted(abs(full(r) - la_only(r)) for r in rows)
    return {
        "mean_abs_delta": round(sum(deltas) / len(deltas), 3),
        "p95_abs_delta": round(deltas[int(0.95 * (len(deltas) - 1))], 3),
        "max_abs_delta": round(deltas[-1], 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default="clap,fd,gin,fastify,bat,chi")
    ap.add_argument("--window", type=int, default=1500)
    ap.add_argument("--max-fixes", type=int, default=400)
    ap.add_argument("--gap-days", type=float, default=120.0)
    ap.add_argument("--out", type=Path, default=HERE.parent / "results" / "jit_calibration_pr.json")
    args = ap.parse_args()

    rc = load_repo_cfg()
    names = [n.strip() for n in args.repos.split(",") if n.strip()]
    rows_by_repo, excluded = {}, {}
    for name in names:
        if name not in rc:
            print(f"  ! unknown repo {name}; skipping")
            continue
        rows, merges, nfix, nind = build_repo(name, rc[name], args.window, args.max_fixes, args.gap_days)
        if rows is None:
            excluded[name] = merges
            print(f"  {name:10s} EXCLUDED: {merges} merge commits (< {MIN_MERGES}); no PR boundary")
            continue
        npos = sum(r["y"] for r in rows)
        nmerge_rows = sum(r["is_merge"] for r in rows)
        print(f"  {name:10s} spans={len(rows):5d} ({nmerge_rows} merges) pos={npos:4d} "
              f"fixes={nfix} inducing={nind}")
        if npos >= 10:
            rows_by_repo[name] = rows

    if not rows_by_repo:
        raise SystemExit("no repo produced enough positives to fit")

    print("\n=== leave-one-repo-out pooled ===")
    variants = {
        "full": COLS,
        "no_new_features": ["la", "ld", "nf", "nd", "ns", "entropy", "exp"],
        "no_collinear": ["la", "ld", "entropy", "exp", "fix_density", "test_gap"],
        "no_fix_density": ["la", "ld", "entropy", "exp", "test_gap"],
        "no_test_gap": ["la", "ld", "entropy", "exp", "fix_density"],
    }
    loo_by_variant = {}
    for label, cols in variants.items():
        loo = loo_auc(rows_by_repo, cols)
        loo_by_variant[label] = loo
        if loo:
            print(f"  {label:18s} model {loo['model_oof_auc']:.4f}  churn {loo['churn_oof_auc']:.4f}  "
                  f"delta {loo['delta_vs_churn_ci'][0]:+.4f} "
                  f"[{loo['delta_vs_churn_ci'][1]:+.4f},{loo['delta_vs_churn_ci'][2]:+.4f}]  "
                  f"(n={loo['n']}, pos={loo['pos']})")
            if label in ("full", "no_new_features"):
                for repo, pr in loo["per_repo"].items():
                    print(f"      {repo:10s} model {pr['model']:.4f}  churn {pr['churn']:.4f}  "
                          f"la {pr['la']:.4f}  fix_density {pr['fix_density']:.4f}  (n={pr['n']})")

    all_rows = [r for rows in rows_by_repo.values() for r in rows]

    # Univariate AUC per feature, against the same labels. If `la` alone scores
    # about what the fitted model scores, the label is largely "how big was this
    # change" and no size-orthogonal feature can win on this metric — which is a
    # fact about the labels, not about the features.
    y_all = [r["y"] for r in all_rows]
    univariate_auc = {}
    for c in COLS + ["churn"]:
        vals = [float(r[c]) for r in all_rows]
        if len(set(y_all)) < 2 or len(set(vals)) < 2:
            continue
        univariate_auc[c] = round(float(roc_auc_score(y_all, vals)), 4)
    print("\n=== univariate AUC (single feature vs the label) ===")
    for c, a in sorted(univariate_auc.items(), key=lambda kv: -kv[1]):
        print(f"  {c:12s} {a:.4f}")

    fits = {}
    for label, cols in variants.items():
        X, y = matrix(all_rows, cols)
        scaler = StandardScaler().fit(X)
        clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(
            scaler.transform(X), y
        )
        mean = [round(float(v), 6) for v in scaler.mean_]
        std = [round(float(v), 6) for v in scaler.scale_]
        coef = [round(float(v), 6) for v in clf.coef_[0]]
        intercept = round(float(clf.intercept_[0]), 6)
        fits[label] = {
            "features": cols, "log1p": [c in LOG1P for c in cols],
            "mean": mean, "std": std, "coef": coef, "intercept": intercept,
            "la_only_ablation": univariate_delta(all_rows, cols, mean, std, coef, intercept),
        }

    print("\n=== pooled fits ===")
    for label, fit in fits.items():
        ab = fit["la_only_ablation"]
        print(f"\n  [{label}]  la-only ablation: {ab['mean_abs_delta']} pts mean, {ab['p95_abs_delta']} p95")
        for c, m, s, w in zip(fit["features"], fit["mean"], fit["std"], fit["coef"]):
            print(f"    {c:12s} mean={m:9.3f} std={s:9.3f} coef={w:+.4f}")
        print(f"    intercept={fit['intercept']:+.4f}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "unit": "first-parent span (PR)",
        "n_train": len(all_rows),
        "n_positive": int(sum(r["y"] for r in all_rows)),
        "repos": list(rows_by_repo),
        "excluded_repos": excluded,
        "gap_days": args.gap_days,
        "window": args.window,
        "loo": loo_by_variant,
        "fits": fits,
    }, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
