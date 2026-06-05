#!/usr/bin/env python3
"""Benchmark validation of the issue-#361 hotspot absolute floors, at T0.

The floors change *which files carry the hotspot bit*. The bit feeds the
shipped score only through (a) the ``untested_hotspot`` trigger and (b)
severity escalations in churn_risk / change_entropy / ownership_risk /
knowledge_loss / prior_defect. Both effects are exactly reconstructable
from the cached T0 findings PLUS the per-file T0 git activity — so, as in
``gate_experiment.py``, no health re-walk is needed.

Per repo this script:

1. Resolves the benchmark T0 sha, adds a detached worktree, and runs the
   product GitIndexer (ESSENTIAL tier, windows anchored to the worktree's
   own HEAD = T0, same excludes as the benchmark run). The percentile
   ranking key (temporal score, c90 tiebreak) is tier-independent, so the
   hotspot set matches the benchmark's FULL-tier classification.
2. Computes the set of files DEMOTED by the floors (old rule hot, new
   rule not).
3. Re-scores the cached findings through the product's own
   ``scoring.score_file`` twice — baseline as cached, treatment with each
   demoted file's findings transformed exactly per the biomarker source:
     - untested_hotspot: dropped unless the biomarker's own fallback
       still fires (temporal >= 0.8; c90 >= 8 is impossible for a
       demoted file — that's the floors' escape hatch).
     - churn_risk / change_entropy: CRITICAL -> HIGH (hotspot was the
       escalator).
     - ownership_risk: re-grade without the hotspot bit.
     - knowledge_loss: re-grade without the hotspot bit.
     - prior_defect: CRITICAL -> HIGH when count is 3-4.
4. Reports corpus pooled AUC, per-repo mean AUC / Popt, and a bootstrap
   95% CI on the corpus pooled delta.

Run:  ../../.venv/Scripts/python.exe t0_floor_experiment.py [--label keyword]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

os.environ["REPOWISE_GIT_WINDOW_ANCHOR"] = "head"

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "repowise-bench" / "health-defect"
REPOS = ROOT / "repowise-bench" / "repos"
RESULTS = ROOT / "repowise-bench" / "results"
sys.path.insert(0, str(BENCH))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    sys.path.insert(0, str(ROOT / p))

from lib.defect_counter import resolve_t0_sha  # noqa: E402
from lib.stats import ALL_BIOMARKERS, popt, roc_auc  # noqa: E402

from repowise.core.analysis.health import scoring  # noqa: E402
from repowise.core.analysis.health.biomarkers.base import BiomarkerResult  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402

_REAL = set(ALL_BIOMARKERS)
_SEV = {"low": Severity.LOW, "medium": Severity.MEDIUM,
        "high": Severity.HIGH, "critical": Severity.CRITICAL}


def _sev(s) -> Severity:
    return _SEV.get(str(s).strip().lower(), Severity.MEDIUM)


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


# ---------------------------------------------------------------------------
# T0 git activity (worktree + ESSENTIAL index, cached per repo)
# ---------------------------------------------------------------------------


def _t0_git_meta(name: str, repo_cfg: dict) -> dict[str, dict] | None:
    """{file_path -> {c90, temporal, churn_pct}} at the benchmark T0 sha."""
    cache = RESULTS / f"health_defect_{name}" / "t0_git_activity.json"
    if cache.exists():
        return json.loads(cache.read_text())

    repo_dir = REPOS / name
    if not (repo_dir / ".git").exists():
        return None
    t0_sha = resolve_t0_sha(str(repo_dir), repo_cfg["t0_date"])
    wt = Path(tempfile.mkdtemp(prefix=f"t0floor_{name}_"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt), t0_sha],
        cwd=repo_dir, check=True, capture_output=True,
    )
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier

        indexer = GitIndexer(
            wt, tier=GitIndexTier.ESSENTIAL,
            exclude_patterns=list(repo_cfg.get("exclude") or []),
        )
        _summary, metas = asyncio.run(indexer.index_repo("t0-floor-experiment"))
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=repo_dir, capture_output=True,
        )
    out = {
        _norm(m["file_path"]): {
            "c90": int(m.get("commit_count_90d") or 0),
            "temporal": float(m.get("temporal_hotspot_score") or 0.0),
            "churn_pct": float(m.get("churn_percentile") or 0.0),
        }
        for m in metas
    }
    cache.write_text(json.dumps(out))
    return out


def _demoted(t0_meta: dict[str, dict]) -> set[str]:
    """Files hot under the OLD rule but not the NEW floors, at T0."""
    from repowise.core.ingestion.git_indexer.enrich import meets_hotspot_floors

    out = set()
    for fp, m in t0_meta.items():
        if m["churn_pct"] >= 0.75 and m["c90"] > 0:  # old rule
            shim = {"commit_count_90d": m["c90"], "temporal_hotspot_score": m["temporal"]}
            if not meets_hotspot_floors(shim):
                out.add(fp)
    return out


# ---------------------------------------------------------------------------
# Finding transformation (mirrors each biomarker's escalation logic)
# ---------------------------------------------------------------------------


def _transform(f: dict, m: dict) -> dict | None:
    """Re-grade one cached finding for a file demoted by the floors.

    Returns the (possibly modified) finding, or None to drop it. ``m`` is
    the file's T0 activity {c90, temporal, churn_pct}; for a demoted file
    c90 < 8 always (the >= 8 escape keeps such files hot).
    """
    bt = f.get("biomarker_type")
    sev = str(f.get("severity", "")).lower()
    det = f.get("details") or {}

    if bt == "untested_hotspot":
        # Biomarker fallback: temporal >= 0.8 (c90 >= 8 impossible here).
        return f if m["temporal"] >= 0.8 else None

    if bt in ("churn_risk", "change_entropy"):
        if sev == "critical":
            return {**f, "severity": "high"}
        return f

    if bt == "ownership_risk":
        minor = int(det.get("minor_contributors") or 0)
        new = "high" if minor >= 5 else ("medium" if minor >= 3 else "low")
        return {**f, "severity": new}

    if bt == "knowledge_loss":
        primary = (det.get("primary_owner") or "").strip()
        recent = (det.get("recent_owner") or "").strip()
        share = float(det.get("recent_owner_share") or 0.0)
        gone = bool(recent) and primary != recent
        quiet = share < 0.2
        return {**f, "severity": "medium" if (gone and quiet) else "low"}

    if bt == "prior_defect":
        count = int(det.get("prior_defect_count") or 0)
        if sev == "critical" and 3 <= count <= 4:
            return {**f, "severity": "high"}
        return f

    return f


def _score(findings: list[dict]) -> float:
    results = [
        BiomarkerResult(
            biomarker_type=f["biomarker_type"], severity=_sev(f.get("severity")),
            function_name=None, line_start=None, line_end=None, details={}, reason="",
        )
        for f in findings
        if f.get("biomarker_type") in _REAL
    ]
    return scoring.score_file(results)[0] if results else 10.0


# ---------------------------------------------------------------------------
# Cache loading (gate_experiment conventions)
# ---------------------------------------------------------------------------


def load(name: str, label: str):
    d = RESULTS / f"health_defect_{name}"
    hp, jp = d / "health_scores.json", d / "joined_data.json"
    if not (hp.exists() and jp.exists()):
        return None
    health, joined = json.loads(hp.read_text()), json.loads(jp.read_text())
    if not joined:
        return None
    if label != "joined":
        lp = d / f"defect_counts_{label}.json"
        if not lp.exists():
            return None
        counts = {_norm(k): v for k, v in json.loads(lp.read_text()).items()}
        for r in joined:
            r["defect_count"] = counts.get(_norm(r["file_path"]), 0)
    by_file: dict[str, list[dict]] = {}
    for f in health.get("findings", []):
        by_file.setdefault(_norm(f["file_path"]), []).append(f)
    return joined, by_file


def _auc(joined, risk):
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return roc_auc(shim)["auc"]


def _popt(joined, risk):
    shim = [{**d, "health_score": 10.0 - r} for d, r in zip(joined, risk)]
    return (popt(shim) or {}).get("popt")


def _m(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 4) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--out", type=Path, default=RESULTS / "t0_floor_experiment.json")
    args = ap.parse_args()

    cfg = {r["name"]: r for r in yaml.safe_load((BENCH / "config.yaml").read_text())["repos"]}

    base_by_repo, treat_by_repo, joined_by_repo = {}, {}, {}
    per_auc_a, per_auc_b, per_popt_a, per_popt_b = [], [], [], []
    summary_rows = []

    for name, rc in cfg.items():
        loaded = load(name, args.label)
        if loaded is None:
            print(f"  {name}: cache missing — skipped")
            continue
        joined, by_file = loaded
        t0_meta = _t0_git_meta(name, rc)
        if t0_meta is None:
            print(f"  {name}: clone missing — skipped")
            continue
        demoted = _demoted(t0_meta)
        old_hot = sum(1 for m in t0_meta.values() if m["churn_pct"] >= 0.75 and m["c90"] > 0)

        base_risk, treat_risk = [], []
        changed_files = 0
        for d in joined:
            fp = _norm(d["file_path"])
            findings = by_file.get(fp, [])
            base_risk.append(10.0 - _score(findings))
            if fp in demoted and findings:
                m = t0_meta[fp]
                t_findings = [tf for tf in (_transform(f, m) for f in findings) if tf]
                if [f.get("severity") for f in t_findings] != [f.get("severity") for f in findings]:
                    changed_files += 1
                treat_risk.append(10.0 - _score(t_findings))
            else:
                treat_risk.append(base_risk[-1])

        base_by_repo[name], treat_by_repo[name] = base_risk, treat_risk
        joined_by_repo[name] = joined
        npos = sum(1 for d in joined if int(d.get("defect_count", 0) or 0) > 0)
        row = (name, old_hot, len(demoted), changed_files)
        summary_rows.append(row)
        if 0 < npos < len(joined):
            per_auc_a.append(_auc(joined, base_risk))
            per_auc_b.append(_auc(joined, treat_risk))
            per_popt_a.append(_popt(joined, base_risk))
            per_popt_b.append(_popt(joined, treat_risk))
        print(f"  {name:12s} T0 old_hot={old_hot:4d} demoted={len(demoted):4d} "
              f"score-changed files={changed_files}")

    def pooled(risks):
        shim = []
        for name, joined in joined_by_repo.items():
            for d, r in zip(joined, risks[name]):
                shim.append({**d, "health_score": 10.0 - r})
        return roc_auc(shim)["auc"]

    corp_a, corp_b = pooled(base_by_repo), pooled(treat_by_repo)
    print(f"\nlabel={args.label}")
    print(f"corpus pooled AUC : {corp_a:.4f} -> {corp_b:.4f}  (delta {corp_b - corp_a:+.4f})")
    print(f"mean per-repo AUC : {_m(per_auc_a)} -> {_m(per_auc_b)}")
    print(f"mean per-repo Popt: {_m(per_popt_a)} -> {_m(per_popt_b)}")

    # Bootstrap CI on the corpus pooled delta (resample files within repo).
    rng = random.Random(99)
    deltas = []
    for _ in range(400):
        shim_a, shim_b = [], []
        for name, joined in joined_by_repo.items():
            n = len(joined)
            idx = [rng.randrange(n) for _ in range(n)]
            for i in idx:
                shim_a.append({**joined[i], "health_score": 10.0 - base_by_repo[name][i]})
                shim_b.append({**joined[i], "health_score": 10.0 - treat_by_repo[name][i]})
        try:
            deltas.append(roc_auc(shim_b)["auc"] - roc_auc(shim_a)["auc"])
        except Exception:
            pass
    deltas.sort()
    mean_d = float(np.mean(deltas))
    lo, hi = deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas))]
    print(f"bootstrap delta corpus AUC: {mean_d:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")

    args.out.write_text(json.dumps({
        "label": args.label,
        "corpus_auc": [corp_a, corp_b],
        "mean_auc": [_m(per_auc_a), _m(per_auc_b)],
        "mean_popt": [_m(per_popt_a), _m(per_popt_b)],
        "delta_corpus_auc_ci": [mean_d, lo, hi],
        "per_repo": [
            {"name": n, "t0_old_hot": oh, "t0_demoted": dm, "score_changed_files": ch}
            for n, oh, dm, ch in summary_rows
        ],
    }, indent=2))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
