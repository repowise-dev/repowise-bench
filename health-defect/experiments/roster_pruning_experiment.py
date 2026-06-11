#!/usr/bin/env python3
"""B3 roster pruning: re-score the cached T0 benchmark WITHOUT the three
floored, measured-weak biomarkers (dry_violation, knowledge_loss,
low_cohesion) and report the corpus / per-language AUC deltas.

Pure cache re-score, no re-index. Baseline = all cached findings through the
live ``scoring.score_file``; pruned = same minus the dropped biomarkers.
Both label sets. Decision bar (HEALTH_SCORE_PLAN B3): keep the biomarkers
unless removal is AUC-positive with CI support AND they show no standalone
maintainability defense.

Run:
    C:\\Users\\ragha\\Desktop\\repowise\\.venv\\Scripts\\python.exe \
        local-stash/code-health/roster_pruning_experiment.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import os as _os
BENCH = Path(__file__).resolve().parents[1]
RESULTS = BENCH.parent / "results"
sys.path.insert(0, str(BENCH))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(BENCH.parents[1])))
for p in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / p
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

import yaml  # noqa: E402

from lib.stats import ALL_BIOMARKERS, roc_auc  # noqa: E402
from run_benchmark import join_and_filter  # noqa: E402

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult  # noqa: E402
from repowise.core.analysis.health.models import Severity  # noqa: E402
from repowise.core.analysis.health.scoring import score_file  # noqa: E402

_REAL = set(ALL_BIOMARKERS)
_DROP = {"dry_violation", "knowledge_loss", "low_cohesion"}
_SEV = {
    "low": Severity.LOW, "medium": Severity.MEDIUM,
    "high": Severity.HIGH, "critical": Severity.CRITICAL,
}
_N_BOOT = 2000
_SEED = 20260611


def _scores(health: dict, drop: set[str]) -> dict:
    by_file: dict[str, list[BiomarkerResult]] = {}
    fired: dict[str, int] = {}
    for f in health.get("findings", []):
        bt = f.get("biomarker_type")
        if bt not in _REAL:
            continue
        if bt in _DROP:
            fired[bt] = fired.get(bt, 0) + 1
        if bt in drop:
            continue
        by_file.setdefault(f["file_path"], []).append(
            BiomarkerResult(
                biomarker_type=bt,
                severity=_SEV.get(str(f.get("severity")).strip().lower(), Severity.MEDIUM),
                function_name=None, line_start=None, line_end=None,
                details={}, reason="",
            )
        )
    new_metrics = []
    for m in health.get("metrics", []):
        results = by_file.get(m["file_path"], [])
        nm = dict(m)
        nm["score"] = score_file(results)[0] if results else 10.0
        new_metrics.append(nm)
    return {**health, "metrics": new_metrics, "_fired": fired}


def _auc(health: dict, defects: dict, defaults: dict, exclude: list[str]) -> float:
    joined = join_and_filter(
        health, defects,
        min_nloc=defaults["min_nloc"],
        exclude_tests=defaults["exclude_test_files"],
        exclude_patterns=exclude,
    )
    return roc_auc(joined)["auc"]


def main() -> None:
    cfg = yaml.safe_load((BENCH / "config.yaml").read_text())
    defaults = cfg["defaults"]
    rows = []
    fired_total: dict[str, int] = {}

    for r in cfg["repos"]:
        name, lang = r["name"], r.get("language", "?")
        d = RESULTS / f"health_defect_{name}"
        health = json.loads((d / "health_scores.json").read_text())
        base = _scores(health, set())
        pruned = _scores(health, _DROP)
        for k, v in base["_fired"].items():
            fired_total[k] = fired_total.get(k, 0) + v
        per_label = {}
        for label, fn in (("keyword", "defect_counts_keyword.json"),
                          ("szz", "defect_counts_szz.json")):
            dp = d / fn
            if not dp.exists():
                dp = d / "defect_counts.json"
            defects = json.loads(dp.read_text())
            excl = list(r.get("exclude") or [])
            per_label[label] = (
                _auc(base, defects, defaults, excl),
                _auc(pruned, defects, defaults, excl),
            )
        rows.append((name, lang, per_label))

    print(f"dropped: {sorted(_DROP)}")
    print(f"corpus findings from dropped biomarkers: {fired_total}")
    for label in ("keyword", "szz"):
        print(f"\n=== {label} labels (AUC with full roster -> pruned roster) ===")
        deltas = []
        by_lang: dict[str, list[float]] = {}
        for name, lang, per_label in rows:
            b, a = per_label[label]
            delta = a - b
            deltas.append(delta)
            by_lang.setdefault(lang, []).append(delta)
            print(f"{name:12s} {lang:10s} {b:>8.4f} -> {a:>8.4f}  {delta:>+8.4f}")
        mean = sum(deltas) / len(deltas)
        rng = random.Random(_SEED)
        boots = []
        for _ in range(_N_BOOT):
            sample = [deltas[rng.randrange(len(deltas))] for _ in deltas]
            boots.append(sum(sample) / len(sample))
        boots.sort()
        lo, hi = boots[int(0.025 * _N_BOOT)], boots[int(0.975 * _N_BOOT)]
        print(f"{'MEAN':12s} {'':10s} {'':>8s}    {'':>8s}  {mean:>+8.4f}  "
              f"[{lo:+.4f}, {hi:+.4f}]")
        print("per-language mean delta:")
        for lang, ds in sorted(by_lang.items()):
            print(f"  {lang:10s} {sum(ds)/len(ds):>+8.4f}  (n={len(ds)})")


if __name__ == "__main__":
    main()
