#!/usr/bin/env python3
"""B1 ship validation: re-score the cached T0 benchmark WITH the new
error_handling biomarker and report the corpus / per-language AUC deltas.

No re-index. Baseline scores are recomputed from the cached findings via the
live ``scoring.score_file`` (so before/after differ only by the new findings);
the "after" pass appends one LOW ``error_handling`` finding per anti-pattern
hit from the cached per-file counts (``error_handling.json``, produced by the
frontier experiment's T0 tree-sitter pass over the same file universe).

Gate (HEALTH_SCORE_PLAN B1):
  1. corpus AUC delta CI contains 0 (it is a FLOOR signal, not a predictor),
     under BOTH keyword and SZZ labels;
  2. no per-language regression beyond noise;
  3. per-file score movement bounded by the 0.5 category cap, and only on
     files with hits.

Run:
    $env:PYTHONIOENCODING="utf-8"
    C:\\Users\\ragha\\Desktop\\repowise\\.venv\\Scripts\\python.exe \
        local-stash/code-health/eh_ship_validation.py
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
_SEV = {
    "low": Severity.LOW, "medium": Severity.MEDIUM,
    "high": Severity.HIGH, "critical": Severity.CRITICAL,
}
_EH_KINDS = ("swallowed_catch", "bare_except", "unsafe_unwrap", "go_swallow")
_N_BOOT = 2000
_SEED = 20260611


def _result(bt: str, sev: Severity) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=bt, severity=sev, function_name=None,
        line_start=None, line_end=None, details={}, reason="",
    )


def _scores(health: dict, eh: dict[str, int] | None) -> dict:
    """Recompute every metric's score from cached findings; when *eh* is
    given, append that many LOW error_handling findings per file."""
    by_file: dict[str, list[BiomarkerResult]] = {}
    for f in health.get("findings", []):
        bt = f.get("biomarker_type")
        if bt not in _REAL:
            continue
        by_file.setdefault(f["file_path"], []).append(
            _result(bt, _SEV.get(str(f.get("severity")).strip().lower(), Severity.MEDIUM))
        )
    new_metrics = []
    for m in health.get("metrics", []):
        results = list(by_file.get(m["file_path"], []))
        if eh:
            for _ in range(eh.get(m["file_path"], 0)):
                results.append(_result("error_handling", Severity.LOW))
        nm = dict(m)
        nm["score"] = score_file(results)[0] if results else 10.0
        new_metrics.append(nm)
    return {**health, "metrics": new_metrics}


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
    rows = []  # (repo, language, n_hits_matched, {label: (before, after)})
    movement_max = 0.0
    movement_violations = []

    for r in cfg["repos"]:
        name, lang = r["name"], r.get("language", "?")
        d = RESULTS / f"health_defect_{name}"
        health = json.loads((d / "health_scores.json").read_text())
        eh_raw = json.loads((d / "error_handling.json").read_text())["files"]

        metric_paths = {m["file_path"] for m in health.get("metrics", [])}
        eh: dict[str, int] = {}
        for path, counts in eh_raw.items():
            n = int(counts.get("eh_count", 0))
            if n > 0 and path in metric_paths:
                eh[path] = n
        unmatched = sum(
            1 for p, c in eh_raw.items()
            if int(c.get("eh_count", 0)) > 0 and p not in metric_paths
        )

        before = _scores(health, None)
        after = _scores(health, eh)

        # Bounded-movement check: only hit files move, by at most the 0.5 cap.
        b_by = {m["file_path"]: m["score"] for m in before["metrics"]}
        for m in after["metrics"]:
            delta = b_by[m["file_path"]] - m["score"]
            if m["file_path"] not in eh:
                if abs(delta) > 1e-9:
                    movement_violations.append((name, m["file_path"], delta))
            else:
                movement_max = max(movement_max, delta)
                if delta > 0.5 + 1e-9 or delta < -1e-9:
                    movement_violations.append((name, m["file_path"], delta))

        per_label = {}
        for label, fn in (("keyword", "defect_counts_keyword.json"),
                          ("szz", "defect_counts_szz.json")):
            dp = d / fn
            if not dp.exists():
                dp = d / "defect_counts.json"
            defects = json.loads(dp.read_text())
            excl = list(r.get("exclude") or [])
            per_label[label] = (
                _auc(before, defects, defaults, excl),
                _auc(after, defects, defaults, excl),
            )
        rows.append((name, lang, sum(eh.values()), unmatched, per_label))

    for label in ("keyword", "szz"):
        print(f"\n=== {label} labels ===")
        print(f"{'repo':12s} {'lang':10s} {'hits':>5s} {'unm':>4s} {'AUC before':>11s} {'AUC after':>10s} {'delta':>8s}")
        deltas = []
        by_lang: dict[str, list[float]] = {}
        for name, lang, hits, unm, per_label in rows:
            b, a = per_label[label]
            delta = a - b
            deltas.append(delta)
            by_lang.setdefault(lang, []).append(delta)
            print(f"{name:12s} {lang:10s} {hits:>5d} {unm:>4d} {b:>11.4f} {a:>10.4f} {delta:>+8.4f}")
        mean = sum(deltas) / len(deltas)
        rng = random.Random(_SEED)
        boots = []
        for _ in range(_N_BOOT):
            sample = [deltas[rng.randrange(len(deltas))] for _ in deltas]
            boots.append(sum(sample) / len(sample))
        boots.sort()
        lo, hi = boots[int(0.025 * _N_BOOT)], boots[int(0.975 * _N_BOOT)]
        print(f"{'MEAN':12s} {'':10s} {'':>5s} {'':>4s} {'':>11s} {'':>10s} {mean:>+8.4f}  "
              f"[{lo:+.4f}, {hi:+.4f}]  CI contains 0: {lo <= 0.0 <= hi}")
        print("per-language mean delta:")
        for lang, ds in sorted(by_lang.items()):
            print(f"  {lang:10s} {sum(ds)/len(ds):>+8.4f}  (n={len(ds)})")

    print(f"\nmax per-file score movement: {movement_max:.4f} (cap 0.5)")
    print(f"movement violations: {len(movement_violations)}")
    for v in movement_violations[:10]:
        print(f"  {v}")


if __name__ == "__main__":
    main()
