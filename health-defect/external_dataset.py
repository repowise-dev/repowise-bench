"""Phase 11 Part B — external published-dataset comparison.

Evaluates the shipped health score against a *published* defect dataset so the
result is comparable to the field, not just to our own corpus. We use the
**Jureczko/Madeyski jEdit** datasets (PROMISE / "tera-PROMISE"), the canonical
CK-metrics-plus-post-release-bugs benchmark used across the defect-prediction
literature.

Pipeline:

1. The dataset ships one row per top-level Java class: 20 CK/McCabe metrics +
   a post-release ``bug`` count. We checkout the *matching jEdit release source*
   and run the shipped ``repowise health`` on it (a single-commit snapshot, so
   only the structural/size/cohesion biomarkers fire — there is no git history,
   which is exactly the right, conservative test of the *code-structure* half of
   the score on code it has never seen).
2. Map each dataset class ``a.b.C`` → ``a/b/C.java`` and join on the intersection
   (reporting match coverage honestly).
3. Score: our health (lower = riskier) vs the published trivial **LOC baseline**
   (the dataset's own ``loc`` column) and a within-dataset **full CK-metric
   logistic** (stratified CV) — the "their metrics, their model" reference — and
   random. Bootstrap 95% CI on our AUC. Compare to literature jEdit AUCs.

Usage::

    ../../.venv/Scripts/python.exe external_dataset.py \
        --health ../results/external/jedit40_health.json \
        --csv ../results/external/jedit-4.0.csv --name jedit-4.0
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

_BENCH = Path(__file__).resolve().parent
_RESULTS = _BENCH.parent / "results"

_CK_COLS = [
    "wmc", "dit", "noc", "cbo", "rfc", "lcom", "ca", "ce", "npm", "lcom3",
    "loc", "dam", "moa", "mfa", "cam", "ic", "cbm", "amc", "max_cc", "avg_cc",
]


def auc(labels: list[int], risk: list[float]) -> float | None:
    """ROC AUC via the Mann–Whitney rank statistic (ties → 0.5)."""
    pos = [r for r, y in zip(risk, labels) if y == 1]
    neg = [r for r, y in zip(risk, labels) if y == 0]
    if not pos or not neg:
        return None
    order = sorted(range(len(risk)), key=lambda i: risk[i])
    ranks = [0.0] * len(risk)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and risk[order[j]] == risk[order[i]]:
            j += 1
        avg = (i + j - 1) / 2.0 + 1
        for k in range(i, j):
            ranks[order[k]] = avg
        i = j
    rank_sum_pos = sum(ranks[i] for i in range(len(labels)) if labels[i] == 1)
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def popt(labels_loc: list[tuple[int, float]], risk: list[float]) -> float | None:
    """Mende–Koschke Popt: risk-ordered effort/recall curve vs optimal & worst,
    effort = LOC. ``labels_loc`` is [(bug_count, loc)]."""
    total_loc = sum(max(l, 1) for _, l in labels_loc)
    total_def = sum(b for b, _ in labels_loc)
    if total_def <= 0:
        return None
    idx = list(range(len(risk)))

    def area(order: list[int]) -> float:
        a = cx = cy = px = py = 0.0
        for i in order:
            cx += max(labels_loc[i][1], 1)
            cy += labels_loc[i][0]
            x, y = cx / total_loc, cy / total_def
            a += (x - px) * (y + py) / 2
            px, py = x, y
        return a

    model = sorted(idx, key=lambda i: -risk[i])
    optimal = sorted(idx, key=lambda i: labels_loc[i][0] / max(labels_loc[i][1], 1), reverse=True)
    worst = list(reversed(optimal))
    a_m, a_o, a_w = area(model), area(optimal), area(worst)
    return 1.0 - (a_o - a_m) / (a_o - a_w) if a_o != a_w else 0.5


def boot_ci(labels: list[int], risk: list[float], *, n_boot=2000, seed=12345):
    point = auc(labels, risk)
    if point is None:
        return {"point": None}
    rng = random.Random(seed)
    n = len(labels)
    samples = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        v = auc([labels[i] for i in idx], [risk[i] for i in idx])
        if v is not None:
            samples.append(v)
    samples.sort()

    def pct(q):
        pos = q * (len(samples) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(samples) - 1)
        return samples[lo] * (hi - pos) + samples[hi] * (pos - lo)

    return {"point": point, "lo": pct(0.025), "hi": pct(0.975), "n": n}


def ck_model_oof_auc(rows: list[dict], *, seed=42, folds=10) -> float | None:
    """Within-dataset full-CK-metric L2 logistic, stratified k-fold OOF AUC —
    the 'their metrics, their model' reference point."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception:
        return None
    X = np.array([[float(r[c]) for c in _CK_COLS] for r in rows])
    y = np.array([1 if float(r["bug"]) > 0 else 0 for r in rows])
    if y.sum() == 0 or y.sum() == len(y):
        return None
    rng = np.random.RandomState(seed)
    pos_idx = rng.permutation(np.where(y == 1)[0])
    neg_idx = rng.permutation(np.where(y == 0)[0])
    fold = np.zeros(len(y), dtype=int)
    for k, i in enumerate(pos_idx):
        fold[i] = k % folds
    for k, i in enumerate(neg_idx):
        fold[i] = k % folds
    oof = np.zeros(len(y))
    for f in range(folds):
        tr, te = fold != f, fold == f
        if y[tr].sum() == 0 or te.sum() == 0:
            continue
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(max_iter=2000, C=1.0)
        clf.fit(sc.transform(X[tr]), y[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    return auc(y.tolist(), oof.tolist())


def load_health(path: Path) -> dict[str, dict]:
    d = json.loads(path.read_text())
    return {m["file_path"]: m for m in d.get("metrics", [])}


def run(health_path: Path, csv_path: Path, name: str) -> dict:
    health = load_health(health_path)
    rows = list(csv.DictReader(csv_path.open()))

    joined: list[dict] = []
    matched = 0
    for r in rows:
        fp = r["name"].replace(".", "/") + ".java"
        m = health.get(fp)
        if m is None:
            continue
        matched += 1
        joined.append({
            "file_path": fp,
            "health_score": m["score"],
            "nloc": m.get("nloc", 0),
            "their_loc": float(r["loc"]),
            "bug": int(float(r["bug"])),
            "ck": r,
        })

    labels = [1 if d["bug"] > 0 else 0 for d in joined]
    health_risk = [10.0 - d["health_score"] for d in joined]
    loc_risk = [d["their_loc"] for d in joined]
    our_nloc_risk = [float(d["nloc"]) for d in joined]
    rng = random.Random(7)
    rand_risk = [rng.random() for _ in joined]
    labels_loc = [(d["bug"], d["their_loc"]) for d in joined]

    result = {
        "dataset": name,
        "csv_classes": len(rows),
        "matched": matched,
        "match_pct": matched / len(rows) if rows else 0,
        "n_buggy": sum(labels),
        "n_clean": len(labels) - sum(labels),
        "auc": {
            "health": boot_ci(labels, health_risk),
            "loc_published": auc(labels, loc_risk),
            "our_nloc": auc(labels, our_nloc_risk),
            "ck_metric_model_oof": ck_model_oof_auc([d["ck"] for d in joined]),
            "random": auc(labels, rand_risk),
        },
        "popt": {
            "health": popt(labels_loc, health_risk),
            "loc": popt(labels_loc, loc_risk),
        },
    }
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--health", type=Path, required=True)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", type=Path, default=_RESULTS / "external_dataset.json")
    args = ap.parse_args()
    res = run(args.health, args.csv, args.name)

    print(f"\n=== External dataset: {res['dataset']} ===")
    print(f"classes={res['csv_classes']} matched={res['matched']} "
          f"({res['match_pct']*100:.1f}%) buggy={res['n_buggy']} clean={res['n_clean']}")
    h = res["auc"]["health"]
    print("\nAUC (defective-vs-clean, higher=better):")
    print(f"  health (ours)         {h['point']:.3f} [{h['lo']:.3f}, {h['hi']:.3f}]")
    print(f"  LOC (published col)   {res['auc']['loc_published']:.3f}")
    print(f"  our nloc              {res['auc']['our_nloc']:.3f}")
    ck = res["auc"]["ck_metric_model_oof"]
    print(f"  full CK logistic OOF  {ck:.3f}" if ck else "  full CK logistic OOF  n/a")
    print(f"  random                {res['auc']['random']:.3f}")
    print(f"\nPopt (effort=LOC):  health {res['popt']['health']:.3f}  loc {res['popt']['loc']:.3f}")

    args.out.write_text(json.dumps(res, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
