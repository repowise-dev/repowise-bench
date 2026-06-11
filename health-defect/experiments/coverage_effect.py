#!/usr/bin/env python3
"""Isolate the marginal predictive value of ingested coverage (Phase 7 Part C).

RESEARCH ARTIFACT — local-stash only, never committed. Reuses
``calibrate_health_weights`` to fit the continuous-feature model on the subset
of repos for which a coverage artifact was acquired, WITH vs WITHOUT the two
coverage columns (``uncovered_frac``, ``coverage_known``), and reports the
pooled-out-of-fold-AUC delta.

On the covered subset ``coverage_known`` is constant (==1 for every file), so it
contributes no signal there — the delta is attributable to ``uncovered_frac``,
the continuous "fraction of lines not covered" gradient. This measures coverage's
value even though the *binary* untested_hotspot/coverage_gap biomarkers fire
sparsely on these high-coverage (91–98%) repos.

Usage:
    .venv/Scripts/python.exe local-stash/coverage_effect.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibrate_health_weights as cal  # noqa: E402

_RES = Path(__file__).resolve().parents[2] / "results"
# Repos with a coverage_t0.json acquired (Codecov Tier-1 + c8 Tier-2).
COVERED = ["rich", "litestar", "hono", "gin", "fiber", "axios", "fastify"]


def load(names: list[str]) -> dict:
    repos = {}
    for n in names:
        d = _RES / f"health_defect_{n}"
        loaded = cal.load_repo(d)
        if not loaded:
            continue
        counts = {cal._norm(k): v for k, v
                  in json.loads((d / "defect_counts_keyword.json").read_text()).items()}
        joined, findings = loaded
        for row in joined:
            row["defect_count"] = counts.get(cal._norm(row["file_path"]), 0)
        repos[n] = (joined, findings)
    return repos


def best_auc(X, y, g) -> float:
    return max(cal.cross_project_auc(X, y, g, C)[0] for C in (0.1, 0.25, 0.5, 1.0))


def main() -> None:
    repos = load(COVERED)
    X, y, g, fn = cal.build_matrix(repos, continuous=True)
    with_cov = best_auc(X, y, g)
    drop = {fn.index("uncovered_frac"), fn.index("coverage_known")}
    keep = [i for i in range(X.shape[1]) if i not in drop]
    no_cov = best_auc(X[:, keep], y, g)
    out = {
        "covered_repos": list(repos),
        "n_files": int(len(y)),
        "n_positives": int(y.sum()),
        "continuous_with_coverage_oof_auc": round(with_cov, 4),
        "continuous_without_coverage_oof_auc": round(no_cov, 4),
        "coverage_marginal_delta_auc": round(with_cov - no_cov, 4),
    }
    print(json.dumps(out, indent=2))
    (Path(__file__).resolve().parent / "coverage_effect.json").write_text(
        json.dumps(out, indent=2)
    )


if __name__ == "__main__":
    main()
