"""One-command reproduction of the benchmark report's headline numbers.

Runs every **cache-based** analysis behind ``BENCHMARK_REPORT.md`` from the
committed ``results/`` artifacts — no re-index, no network — and prints the
tables the report cites. Deterministic (seeded bootstraps).

    ../../.venv/Scripts/python.exe reproduce.py

What it runs:
  1. Statistical rigor (bootstrap CIs, DeLong + cluster-bootstrap significance,
     baselines) under both the keyword and SZZ labels        → statistical_rigor.py
  2. External-dataset comparison on jEdit 4.0 / 4.1 (PROMISE) → external_dataset.py

Two parts of the study re-index and are therefore run separately (documented in
the report's Reproduction section), not by this script:
  * Temporal cross-validation (re-indexes the corpus at 3 rolling T0s)
        ../../.venv/Scripts/python.exe temporal_cv.py --t0 2025-05-23 \
            --t0 2025-08-23 --t0 2025-11-23 --repos pydantic,hono,zod,axios,clap,bat,gin,chi,spdlog
  * Weight calibration (offline L2-logistic fit; reproduces the shipped
    constants) lives in the private analysis scripts and re-scores cached
    findings — see the report.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parent
_PY = sys.executable
_RESULTS = _BENCH.parent / "results"


def _run(args: list[str], title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    r = subprocess.run([_PY, *args], cwd=_BENCH)
    if r.returncode != 0:
        print(f"  !! step failed (exit {r.returncode})")


def main() -> None:
    _run(
        ["statistical_rigor.py", "--label", "keyword",
         "--out", str(_RESULTS / "statistical_rigor_keyword.json")],
        "1a. Statistical rigor — keyword labels (CIs, DeLong, baselines)",
    )
    _run(
        ["statistical_rigor.py", "--label", "szz",
         "--out", str(_RESULTS / "statistical_rigor_szz.json")],
        "1b. Statistical rigor — SZZ labels (label-robustness cross-check)",
    )
    for ver, csv in (("4.0", "jedit-4.0.csv"), ("4.1", "jedit-4.1.csv")):
        health = _RESULTS / "external" / f"jedit{ver.replace('.', '')}_health.json"
        if not health.exists():
            print(f"\n  (skip jEdit {ver}: {health.name} not cached — "
                  f"see report's External-dataset section to regenerate)")
            continue
        _run(
            ["external_dataset.py", "--health", str(health),
             "--csv", str(_RESULTS / "external" / csv), "--name", f"jedit-{ver}",
             "--out", str(_RESULTS / f"external_jedit{ver.replace('.', '')}.json")],
            f"2. External-dataset comparison — jEdit {ver} (PROMISE/Jureczko)",
        )
    print("\nDone. JSON artifacts written under results/.")


if __name__ == "__main__":
    main()
