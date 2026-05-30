"""Phase 11 Part A — uncertainty, robustness, and significance.

Turns the benchmark's point estimates into a defensible result:

* **Bootstrap 95% CIs + n** on every headline metric (AUC, partial-Spearman vs
  NLOC, Popt, Precision@20%LOC). Files are resampled *within* a repo for the
  per-repo CIs; repos are resampled (a two-stage cluster bootstrap) for the
  cross-project numbers, because the unit of generalization is the repository,
  not the file.
* **Trivial baselines** (LOC, churn, prior-defects, random) scored through the
  identical code path, with cluster-bootstrap CIs and paired deltas.
* **Significance** that the shipped health score beats the best trivial baseline:
  a fast **DeLong** test on the pooled corpus (the field-standard correlated-ROC
  test) *and* a repo-cluster bootstrap of the paired AUC delta (which respects
  the corpus's repo structure where DeLong's i.i.d. assumption does not).

Cache-only — reuses the committed ``results/<repo>/joined_data.json`` (health
score + NLOC + baseline features) overlaid with ``defect_counts_<label>.json``.
No re-index. Deterministic (seeded) so the report reproduces.

Usage::

    ../../.venv/Scripts/python.exe statistical_rigor.py [--label keyword] \
        [--n-boot 2000] [--out ../results/statistical_rigor.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from lib.filters import normalize_path
from lib.stats import (
    auc_metric,
    effort_aware_at_loc,
    partial_spearman,
    popt_metric,
    roc_auc,
)

_BENCH = Path(__file__).resolve().parent
_RESULTS = _BENCH.parent / "results"


# ---------------------------------------------------------------------------
# Corpus loading (cache-only re-join with the chosen label strategy)
# ---------------------------------------------------------------------------
def corpus_repos() -> list[str]:
    cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
    return [r["name"] for r in cfg["repos"]]


def load_repo(name: str, label: str = "keyword") -> list[dict] | None:
    """Re-join the cached health metrics with the chosen defect label.

    Returns the joined rows (``health_score``/``nloc``/baseline features +
    ``defect_count`` under ``label``) or ``None`` if the cache is missing.
    """
    rdir = _RESULTS / f"health_defect_{name}"
    jp = rdir / "joined_data.json"
    lp = rdir / f"defect_counts_{label}.json"
    if not jp.exists() or not lp.exists():
        return None
    joined = json.loads(jp.read_text())
    counts = {normalize_path(k): v for k, v in json.loads(lp.read_text()).items()}
    for d in joined:
        d["defect_count"] = counts.get(normalize_path(d["file_path"]), 0)
    return joined


def load_corpus(label: str = "keyword") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name in corpus_repos():
        j = load_repo(name, label)
        if j is None:
            continue
        # A repo only contributes to discrimination metrics if it has both
        # classes; keep it anyway (AUC degenerates to 0.5, handled downstream).
        out[name] = j
    return out


# ---------------------------------------------------------------------------
# Headline metrics as joined->float callables (for the bootstrap machinery)
# ---------------------------------------------------------------------------
def partial_rho_metric(joined: list[dict]) -> float | None:
    if len(joined) < 4:
        return None
    scores = [d["health_score"] for d in joined]
    defects = [float(d["defect_count"]) for d in joined]
    nlocs = [float(d["nloc"]) for d in joined]
    if len(set(defects)) < 2:
        return None
    return partial_spearman(scores, defects, nlocs)


def precision20_metric(joined: list[dict]) -> float | None:
    if sum(1 for d in joined if d["defect_count"] > 0) == 0:
        return None
    return effort_aware_at_loc(joined, 0.20)["precision"]


def recall20_metric(joined: list[dict]) -> float | None:
    if sum(1 for d in joined if d["defect_count"] > 0) == 0:
        return None
    return effort_aware_at_loc(joined, 0.20)["recall_files"]


HEADLINE_METRICS: dict[str, Callable[[list[dict]], float | None]] = {
    "auc": auc_metric,
    "partial_rho_nloc": partial_rho_metric,
    "popt": popt_metric,
    "precision_at_20pct_loc": precision20_metric,
    "recall_at_20pct_loc": recall20_metric,
}


# ---------------------------------------------------------------------------
# Bootstrap — within-repo (files) and cross-project (cluster: repos+files)
# ---------------------------------------------------------------------------
def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return sorted_vals[int(pos)]
    return sorted_vals[lo] * (hi - pos) + sorted_vals[hi] * (pos - lo)


def _ci(samples: list[float], point: float | None, n: int, ci: float = 0.95) -> dict:
    if not samples or point is None:
        return {"point": point, "lo": None, "hi": None, "n": n, "n_boot": 0}
    s = sorted(samples)
    a = (1 - ci) / 2
    return {
        "point": float(point),
        "lo": _percentile(s, a),
        "hi": _percentile(s, 1 - a),
        "n": n,
        "n_boot": len(s),
        "ci": ci,
    }


def within_repo_ci(
    joined: list[dict],
    metric: Callable[[list[dict]], float | None],
    *,
    n_boot: int,
    seed: int,
) -> dict:
    point = metric(joined)
    n = len(joined)
    if point is None or n < 4:
        return {"point": point, "lo": None, "hi": None, "n": n, "n_boot": 0}
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        rs = [joined[rng.randrange(n)] for _ in range(n)]
        try:
            v = metric(rs)
        except Exception:
            v = None
        if v is not None and v == v:
            samples.append(float(v))
    return _ci(samples, point, n)


def cluster_bootstrap_mean(
    corpus: dict[str, list[dict]],
    metric: Callable[[list[dict]], float | None],
    *,
    n_boot: int,
    seed: int,
    two_stage: bool = True,
) -> dict:
    """CI for the mean-over-repos of ``metric``: resample repos with
    replacement (and files within each, when ``two_stage``) — the unit of
    generalization is the repo."""
    names = list(corpus)
    per_repo = {n: metric(corpus[n]) for n in names}
    valid = [v for v in per_repo.values() if v is not None]
    point = sum(valid) / len(valid) if valid else None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        chosen = [names[rng.randrange(len(names))] for _ in range(len(names))]
        vals: list[float] = []
        for nm in chosen:
            rows = corpus[nm]
            if two_stage:
                m = len(rows)
                rows = [rows[rng.randrange(m)] for _ in range(m)]
            try:
                v = metric(rows)
            except Exception:
                v = None
            if v is not None and v == v:
                vals.append(float(v))
        if vals:
            samples.append(sum(vals) / len(vals))
    out = _ci(samples, point, len(names))
    out["per_repo"] = per_repo
    return out


def pooled_ci(
    corpus: dict[str, list[dict]],
    metric: Callable[[list[dict]], float | None],
    *,
    n_boot: int,
    seed: int,
) -> dict:
    """CI for the *pooled* metric (all files concatenated) via a two-stage
    cluster bootstrap (resample repos, then files within)."""
    names = list(corpus)
    pooled = [r for n in names for r in corpus[n]]
    point = metric(pooled)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        chosen = [names[rng.randrange(len(names))] for _ in range(len(names))]
        rows: list[dict] = []
        for nm in chosen:
            src = corpus[nm]
            m = len(src)
            rows.extend(src[rng.randrange(m)] for _ in range(m))
        try:
            v = metric(rows)
        except Exception:
            v = None
        if v is not None and v == v:
            samples.append(float(v))
    return _ci(samples, point, len(pooled))


# ---------------------------------------------------------------------------
# Baselines as risk vectors (higher = riskier)
# ---------------------------------------------------------------------------
def risk_vectors(joined: list[dict]) -> dict[str, list[float]]:
    return {
        "health": [10.0 - d["health_score"] for d in joined],
        "loc": [float(d.get("nloc", 0)) for d in joined],
        "churn": [float(d.get("commit_count_90d", 0)) for d in joined],
        "prior_defects": [float(d.get("prior_defect_count", 0)) for d in joined],
        "random": [float(hash(d["file_path"]) % 100003) for d in joined],
    }


def _auc_from_risk(joined: list[dict], risk: list[float]) -> float:
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return roc_auc(shim)["auc"]


def predictor_metric(name: str) -> Callable[[list[dict]], float | None]:
    def m(joined: list[dict]) -> float | None:
        if sum(1 for d in joined if d["defect_count"] > 0) == 0:
            return None
        return _auc_from_risk(joined, risk_vectors(joined)[name])
    return m


# ---------------------------------------------------------------------------
# Fast DeLong (Sun & Xu 2014) — covariance of correlated AUCs on one sample
# ---------------------------------------------------------------------------
def _midrank(x: list[float]) -> list[float]:
    order = sorted(range(len(x)), key=lambda i: x[i])
    n = len(x)
    t = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and x[order[j]] == x[order[i]]:
            j += 1
        rank = 0.5 * (i + j - 1) + 1
        for k in range(i, j):
            t[order[k]] = rank
        i = j
    return t


def delong_cov(predictors: dict[str, list[float]], labels: list[int]):
    """Return (auc dict, covariance matrix, name order) via fast DeLong.

    ``predictors``: name -> risk score (higher = more likely positive).
    """
    names = list(predictors)
    pos_idx = [i for i, y in enumerate(labels) if y == 1]
    neg_idx = [i for i, y in enumerate(labels) if y == 0]
    m, n = len(pos_idx), len(neg_idx)
    if m == 0 or n == 0:
        return None
    k = len(names)
    v01 = [[0.0] * m for _ in range(k)]  # placement values over positives
    v10 = [[0.0] * n for _ in range(k)]  # over negatives
    aucs: dict[str, float] = {}
    for r, nm in enumerate(names):
        s = predictors[nm]
        pos = [s[i] for i in pos_idx]
        neg = [s[i] for i in neg_idx]
        tz = _midrank(pos + neg)
        tx = _midrank(pos)
        ty = _midrank(neg)
        for a in range(m):
            v01[r][a] = (tz[a] - tx[a]) / n
        for b in range(n):
            v10[r][b] = 1.0 - (tz[m + b] - ty[b]) / m
        aucs[nm] = sum(tz[:m]) / (m * n) - (m + 1) / (2.0 * n)

    def _cov(vlists: list[list[float]], count: int) -> list[list[float]]:
        means = [sum(v) / count for v in vlists]
        c = [[0.0] * k for _ in range(k)]
        for a in range(k):
            for b in range(k):
                acc = sum(
                    (vlists[a][t] - means[a]) * (vlists[b][t] - means[b])
                    for t in range(count)
                )
                c[a][b] = acc / (count - 1) if count > 1 else 0.0
        return c

    s01 = _cov(v01, m)
    s10 = _cov(v10, n)
    cov = [[s01[a][b] / m + s10[a][b] / n for b in range(k)] for a in range(k)]
    return aucs, cov, names


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def delong_test(
    pooled: list[dict], a: str, b: str
) -> dict[str, Any]:
    """Two-sided DeLong test that AUC(a) != AUC(b) on the pooled sample."""
    labels = [1 if d["defect_count"] > 0 else 0 for d in pooled]
    preds = risk_vectors(pooled)
    res = delong_cov({a: preds[a], b: preds[b]}, labels)
    if res is None:
        return {"a": a, "b": b, "error": "degenerate (single-class)"}
    aucs, cov, order = res
    ia, ib = order.index(a), order.index(b)
    var = cov[ia][ia] + cov[ib][ib] - 2 * cov[ia][ib]
    delta = aucs[a] - aucs[b]
    if var <= 0:
        return {
            "a": a, "b": b, "auc_a": aucs[a], "auc_b": aucs[b],
            "delta": delta, "z": None, "p_value": None,
            "note": "non-positive variance",
        }
    z = delta / math.sqrt(var)
    return {
        "a": a, "b": b, "auc_a": aucs[a], "auc_b": aucs[b],
        "delta": delta, "z": z, "p_value": 2 * _norm_sf(abs(z)),
        "n_pos": sum(labels), "n_neg": len(labels) - sum(labels),
    }


def _popt_predictor_metric(name: str) -> Callable[[list[dict]], float | None]:
    def m(joined: list[dict]) -> float | None:
        if sum(1 for d in joined if d["defect_count"] > 0) == 0:
            return None
        shim = [
            {**d, "health_score": 10.0 - r}
            for d, r in zip(joined, risk_vectors(joined)[name])
        ]
        return popt_metric(shim)
    return m


def paired_delta_cluster(
    corpus: dict[str, list[dict]],
    a: str,
    b: str,
    *,
    n_boot: int,
    seed: int,
    kind: str = "auc",
) -> dict:
    """Repo-cluster bootstrap of the mean paired delta (a − b) for ``kind`` in
    {auc, popt}."""
    names = list(corpus)
    if kind == "popt":
        ma, mb = _popt_predictor_metric(a), _popt_predictor_metric(b)
    else:
        ma, mb = predictor_metric(a), predictor_metric(b)

    def mean_delta(repo_names: list[str]) -> float | None:
        diffs = []
        for nm in repo_names:
            rows = corpus[nm]
            va, vb = ma(rows), mb(rows)
            if va is not None and vb is not None:
                diffs.append(va - vb)
        return sum(diffs) / len(diffs) if diffs else None

    point = mean_delta(names)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        chosen = [names[rng.randrange(len(names))] for _ in range(len(names))]
        v = mean_delta(chosen)
        if v is not None:
            samples.append(v)
    out = _ci(samples, point, len(names))
    # one-sided bootstrap p that delta <= 0
    if samples:
        out["p_boot_le0"] = sum(1 for x in samples if x <= 0) / len(samples)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run(label: str, n_boot: int, seed: int) -> dict[str, Any]:
    corpus = load_corpus(label)
    report: dict[str, Any] = {
        "label": label,
        "n_boot": n_boot,
        "seed": seed,
        "n_repos": len(corpus),
        "n_files": sum(len(v) for v in corpus.values()),
        "n_positive": sum(
            1 for v in corpus.values() for d in v if d["defect_count"] > 0
        ),
    }

    # 1. Headline metrics: per-repo within-file CI + cross-project cluster CI.
    headline: dict[str, Any] = {}
    for mname, mfn in HEADLINE_METRICS.items():
        per_repo = {
            rn: within_repo_ci(corpus[rn], mfn, n_boot=n_boot, seed=seed)
            for rn in corpus
        }
        headline[mname] = {
            "cross_project_mean": cluster_bootstrap_mean(
                corpus, mfn, n_boot=n_boot, seed=seed
            ),
            "pooled": pooled_ci(corpus, mfn, n_boot=n_boot, seed=seed),
            "per_repo": per_repo,
        }
    report["headline"] = headline

    # 2. Baselines (AUC): cross-project mean CI for each predictor.
    baselines: dict[str, Any] = {}
    for pname in ["health", "loc", "churn", "prior_defects", "random"]:
        baselines[pname] = cluster_bootstrap_mean(
            corpus, predictor_metric(pname), n_boot=n_boot, seed=seed
        )
    # Popt for each predictor too (effort-aware ranking).
    baselines_popt: dict[str, Any] = {}
    for pname in ["health", "loc", "churn", "prior_defects", "random"]:
        def popt_for(joined, _p=pname):
            if sum(1 for d in joined if d["defect_count"] > 0) == 0:
                return None
            shim = [
                {**d, "health_score": 10.0 - r}
                for d, r in zip(joined, risk_vectors(joined)[_p])
            ]
            return popt_metric(shim)
        baselines_popt[pname] = cluster_bootstrap_mean(
            corpus, popt_for, n_boot=n_boot, seed=seed
        )
    report["baselines_auc"] = baselines
    report["baselines_popt"] = baselines_popt

    # 3. Significance: health vs each baseline.
    pooled = [r for v in corpus.values() for r in v]
    delong = {
        b: delong_test(pooled, "health", b)
        for b in ["loc", "churn", "prior_defects", "random"]
    }
    cluster = {
        b: paired_delta_cluster(corpus, "health", b, n_boot=n_boot, seed=seed)
        for b in ["loc", "churn", "prior_defects", "random"]
    }
    # best baseline = highest cross-project mean AUC among the trivial three.
    trivial = {b: baselines[b]["point"] for b in ["loc", "churn", "prior_defects"]}
    best = max(trivial, key=lambda k: trivial[k] if trivial[k] is not None else -1)
    trivial_popt = {
        b: baselines_popt[b]["point"] for b in ["loc", "churn", "prior_defects"]
    }
    best_popt = max(
        trivial_popt, key=lambda k: trivial_popt[k] if trivial_popt[k] is not None else -1
    )
    cluster_popt = {
        b: paired_delta_cluster(
            corpus, "health", b, n_boot=n_boot, seed=seed, kind="popt"
        )
        for b in ["loc", "churn", "prior_defects", "random"]
    }
    report["significance"] = {
        "best_trivial_baseline_by_auc": best,
        "best_trivial_baseline_by_popt": best_popt,
        "delong_pooled": delong,
        "cluster_bootstrap_paired_delta_auc": cluster,
        "cluster_bootstrap_paired_delta_popt": cluster_popt,
    }
    return report


def _fmt(ci: dict) -> str:
    if ci.get("point") is None:
        return "n/a"
    lo, hi = ci.get("lo"), ci.get("hi")
    if lo is None:
        return f"{ci['point']:.3f}"
    return f"{ci['point']:.3f} [{lo:.3f}, {hi:.3f}]"


def print_summary(rep: dict) -> None:
    print(f"\n=== Statistical rigor ({rep['label']} labels) ===")
    print(f"repos={rep['n_repos']}  files={rep['n_files']}  positives={rep['n_positive']}")
    print("\nHeadline metrics (cross-project mean over repos / pooled):")
    for m, blk in rep["headline"].items():
        print(f"  {m:24} mean {_fmt(blk['cross_project_mean'])}   pooled {_fmt(blk['pooled'])}")
    print("\nBaselines — cross-project mean AUC:")
    for b, ci in rep["baselines_auc"].items():
        print(f"  {b:16} {_fmt(ci)}")
    print("Baselines — cross-project mean Popt:")
    for b, ci in rep["baselines_popt"].items():
        print(f"  {b:16} {_fmt(ci)}")
    sig = rep["significance"]
    print(f"\nSignificance — AUC (best trivial baseline = {sig['best_trivial_baseline_by_auc']}):")
    for b, d in sig["delong_pooled"].items():
        if d.get("p_value") is None:
            print(f"  DeLong health vs {b:14} Δ={d.get('delta')}  (p n/a)")
        else:
            print(f"  DeLong health vs {b:14} ΔAUC={d['delta']:+.3f}  z={d['z']:+.2f}  p={d['p_value']:.4g}")
    for b, c in sig["cluster_bootstrap_paired_delta_auc"].items():
        p = c.get("p_boot_le0")
        print(f"  cluster ΔAUC health−{b:14} {_fmt(c)}" + (f"  p(≤0)={p:.4g}" if p is not None else ""))
    print(f"\nSignificance — Popt (best trivial baseline = {sig['best_trivial_baseline_by_popt']}):")
    for b, c in sig["cluster_bootstrap_paired_delta_popt"].items():
        p = c.get("p_boot_le0")
        print(f"  cluster ΔPopt health−{b:14} {_fmt(c)}" + (f"  p(≤0)={p:.4g}" if p is not None else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword", choices=["keyword", "szz", "szz_b"])
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--out", type=Path, default=_RESULTS / "statistical_rigor.json")
    args = ap.parse_args()
    rep = run(args.label, args.n_boot, args.seed)
    print_summary(rep)
    args.out.write_text(json.dumps(rep, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
