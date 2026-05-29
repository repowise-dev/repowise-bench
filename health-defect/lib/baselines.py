"""Trivial baselines the calibrated health score must beat.

A defect predictor is only interesting if it beats what you'd get for free:

* **LOC-only** — rank by file size (nloc desc). The size confound incarnate;
  the bar every code-quality metric is accused of merely re-deriving.
* **churn-only** — rank by recent commit activity (``commit_count_90d`` at T0).
* **prior-defects** — files that already had a bug-fix *before* T0. The
  strongest trivial baseline (defects cluster in the same files) and the real
  bar: beating LOC/churn is table stakes, beating prior-defects is the result.
* **random** — sanity floor (AUC ≈ 0.5).

Each baseline is scored with the same AUC/Popt the health score uses, over the
same joined universe, so the comparison is apples-to-apples.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .defect_counter import _git, find_fix_commits, resolve_t0_sha
from .filters import normalize_path
from .stats import popt, roc_auc


def _commit_count_90d(
    repo_dir: str, t0_sha: str, t0_date: str
) -> dict[str, int]:
    """Per-file commit count in the 90 days ending at T0 — the churn baseline's
    risk signal, computed from history reachable at T0 (no leakage)."""
    t0_dt = datetime.strptime(t0_date, "%Y-%m-%d")
    since = (t0_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    out = _git(
        ["log", t0_sha, f"--since={since}", f"--before={t0_date}T23:59:59",
         "--name-only", "--pretty=format:%x00"],
        cwd=repo_dir,
    )
    counts: dict[str, int] = {}
    for line in out.split("\n"):
        line = line.strip()
        if not line or line == "\x00":
            continue
        fp = normalize_path(line)
        counts[fp] = counts.get(fp, 0) + 1
    return counts


def _prior_defect_count(
    repo_dir: str, t0_sha: str, t0_date: str,
    source_root: str, extensions: tuple[str, ...],
    *, strategy: str, emoji: str, prefix: str,
    include: list[str] | None, exclude: list[str] | None,
    window_months: int = 6,
) -> dict[str, int]:
    """Per-file count of bug-fix commits in the ``window_months`` BEFORE T0 —
    the prior-defects baseline (defects cluster in the same files)."""
    t0_dt = datetime.strptime(t0_date, "%Y-%m-%d")
    prior_date = (t0_dt - timedelta(days=window_months * 30)).strftime("%Y-%m-%d")
    try:
        prior_sha = resolve_t0_sha(repo_dir, prior_date)
    except ValueError:
        return {}
    fixes = find_fix_commits(
        repo_dir, prior_sha, t0_sha, strategy=strategy,
        emoji=emoji, prefix=prefix, include=include, exclude=exclude,
    )
    from .defect_counter import _attribute  # local: avoid widening public API
    return _attribute(repo_dir, [s for s, _ in fixes], source_root, extensions)


def attach_baseline_features(
    joined: list[dict],
    repo_dir: str,
    t0_sha: str,
    t0_date: str,
    *,
    source_root: str,
    extensions: tuple[str, ...],
    strategy: str = "keyword",
    emoji: str = "\U0001F41B",
    prefix: str = "Fixed #",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> None:
    """Attach ``commit_count_90d`` + ``prior_defect_count`` to each joined row,
    in place, so the churn/prior-defects baselines can score them."""
    churn = _commit_count_90d(repo_dir, t0_sha, t0_date)
    prior = _prior_defect_count(
        repo_dir, t0_sha, t0_date, source_root, extensions,
        strategy=strategy, emoji=emoji, prefix=prefix,
        include=include, exclude=exclude,
    )
    for d in joined:
        fp = normalize_path(d["file_path"])
        d["commit_count_90d"] = churn.get(fp, 0)
        d["prior_defect_count"] = prior.get(fp, 0)


def _auc_popt_for_risk(joined: list[dict], risk: list[float]) -> dict[str, Any]:
    """AUC + Popt for an arbitrary per-file risk score (higher = riskier).

    Reuses ``stats.roc_auc`` / ``stats.popt``, which both consume ``health_score``
    (lower = riskier). We invert risk into a synthetic health score
    (``10 - normalized risk``) so a single code path scores every baseline
    identically to the real metric.
    """
    lo = min(risk) if risk else 0.0
    hi = max(risk) if risk else 1.0
    span = (hi - lo) or 1.0
    shim = [
        {**d, "health_score": 10.0 - 10.0 * (r - lo) / span}
        for d, r in zip(joined, risk, strict=True)
    ]
    return {"auc": roc_auc(shim)["auc"], "popt": (popt(shim) or {}).get("popt")}


def loc_only(joined: list[dict]) -> dict[str, Any]:
    return _auc_popt_for_risk(joined, [float(d.get("nloc", 0)) for d in joined])


def churn_only(joined: list[dict]) -> dict[str, Any]:
    return _auc_popt_for_risk(
        joined, [float(d.get("commit_count_90d", 0)) for d in joined]
    )


def prior_defects(joined: list[dict]) -> dict[str, Any]:
    """Files with >=1 pre-T0 bug-fix are risky; ties broken by count."""
    return _auc_popt_for_risk(
        joined, [float(d.get("prior_defect_count", 0)) for d in joined]
    )


def random_baseline(joined: list[dict]) -> dict[str, Any]:
    """Deterministic pseudo-random risk (hash of path) — AUC ≈ 0.5, no RNG so
    the report reproduces exactly."""
    risk = [float(hash(d["file_path"]) % 100003) for d in joined]
    return _auc_popt_for_risk(joined, risk)


def health_score(joined: list[dict]) -> dict[str, Any]:
    """The score under test, via the identical code path (lower health = risk)."""
    return {"auc": roc_auc(joined)["auc"], "popt": (popt(joined) or {}).get("popt")}


def all_baselines(joined: list[dict]) -> dict[str, dict[str, Any]]:
    return {
        "health": health_score(joined),
        "loc_only": loc_only(joined),
        "churn_only": churn_only(joined),
        "prior_defects": prior_defects(joined),
        "random": random_baseline(joined),
    }
