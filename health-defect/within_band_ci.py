#!/usr/bin/env python3
"""Within-band (NLOC-quartile) AUC of the shipped health score, with repo-cluster
bootstrap confidence intervals, under both the keyword and SZZ labels.

Closes the R1 review gate: the canonical wall table (Sec 4.1) ships point
estimates only and under the keyword label only. This adds (a) a per-band
two-stage cluster-bootstrap CI (resample the 21 corpus repositories with
replacement, recompute the within-band AUC, 2,000 seeded replicates) and (b) the
same table under the SZZ label, so the wall can be shown robust to label choice in
the small-file band the thesis hinges on.

Read-only: consumes the same cached T0 artifacts error_analysis.py uses. The
canonical 21-repo corpus is config.yaml minus the large-repo demonstration subject
(cockroach), and the canonical NLOC cuts are the fixed 22/48/108 quartiles.

Usage (venv python):
    PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe within_band_ci.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from error_analysis import auc, band_of, build_rows, load_config_langs

CUTS = [22.0, 48.0, 108.0]            # canonical fixed quartile cuts (Sec 4.1)
EXCLUDE = {"cockroach"}               # large-repo demonstration, not in the corpus
N_BOOT = 2000
SEED = 12345
BANDS = [f"Q1 (<= {CUTS[0]:.0f})", f"Q2 (<= {CUTS[1]:.0f})",
         f"Q3 (<= {CUTS[2]:.0f})", f"Q4 (> {CUTS[2]:.0f})"]


def within_band_auc(rows: list[dict]) -> dict[str, float | None]:
    out = {}
    for b in BANDS:
        members = [r for r in rows if r["band"] == b]
        out[b] = auc([r["y"] for r in members], [r["risk"] for r in members])
    return out


def bootstrap_ci(rows: list[dict], rng: np.random.Generator):
    """Two-stage cluster bootstrap: resample repos with replacement, pool their
    files, recompute within-band + pooled AUC. Returns per-band and pooled
    (lo, hi) 95% percentile CIs plus the number of usable replicates per band."""
    by_repo: dict[str, list[dict]] = {}
    for r in rows:
        by_repo.setdefault(r["repo"], []).append(r)
    repos = list(by_repo)
    n_repos = len(repos)

    band_samples: dict[str, list[float]] = {b: [] for b in BANDS}
    pooled_samples: list[float] = []
    for _ in range(N_BOOT):
        pick = rng.integers(0, n_repos, size=n_repos)
        boot: list[dict] = []
        for i in pick:
            boot.extend(by_repo[repos[i]])
        wb = within_band_auc(boot)
        for b in BANDS:
            if wb[b] is not None:
                band_samples[b].append(wb[b])
        p = auc([r["y"] for r in boot], [r["risk"] for r in boot])
        if p is not None:
            pooled_samples.append(p)

    def ci(xs: list[float]):
        if not xs:
            return (None, None, 0)
        a = np.asarray(xs)
        return (float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5)), len(a))

    return {b: ci(band_samples[b]) for b in BANDS}, ci(pooled_samples)


def run_label(results_dir: Path, langs, roots, label: str) -> dict:
    rows = build_rows(results_dir, langs, roots, label=label)
    for r in rows:
        r["band"] = band_of(r["nloc"], CUTS)
    n, npos = len(rows), sum(r["y"] for r in rows)
    pooled = auc([r["y"] for r in rows], [r["risk"] for r in rows])
    point = within_band_auc(rows)
    rng = np.random.default_rng(SEED)
    band_ci, pooled_ci = bootstrap_ci(rows, rng)

    print(f"\n=== label={label} | {len(set(r['repo'] for r in rows))} repos | "
          f"{n} files | {npos} positives ({npos/n:.1%}) | pooled AUC {pooled:.3f} "
          f"[{pooled_ci[0]:.3f}, {pooled_ci[1]:.3f}] ===")
    print(f"{'band':18s} {'n':>5s} {'pos':>4s} {'within-AUC':>10s} {'95% CI':>20s} {'reps':>6s}")
    table = {}
    for b in BANDS:
        members = [r for r in rows if r["band"] == b]
        pos = sum(r["y"] for r in members)
        lo, hi, reps = band_ci[b]
        a = point[b]
        astr = f"{a:.3f}" if a is not None else "n/a"
        cistr = f"[{lo:.3f}, {hi:.3f}]" if lo is not None else "n/a"
        print(f"{b:18s} {len(members):5d} {pos:4d} {astr:>10s} {cistr:>20s} {reps:6d}")
        table[b] = {"n": len(members), "positives": pos,
                    "within_auc": a, "ci_lo": lo, "ci_hi": hi, "boot_reps": reps}
    return {"label": label, "files": n, "positives": npos,
            "pooled_auc": pooled, "pooled_ci": [pooled_ci[0], pooled_ci[1]],
            "bands": table}


def main() -> None:
    here = Path(__file__).resolve().parent
    results_dir = here.parent / "results"
    langs, roots = load_config_langs(here / "config.yaml")
    langs = {k: v for k, v in langs.items() if k not in EXCLUDE}
    print(f"Canonical corpus: {len(langs)} repos (config minus {sorted(EXCLUDE)}); "
          f"cuts {CUTS}; {N_BOOT} bootstrap reps; seed {SEED}")

    out = {"corpus_repos": len(langs), "cuts": CUTS, "n_boot": N_BOOT, "seed": SEED,
           "results": {}}
    for label in ("keyword", "szz"):
        out["results"][label] = run_label(results_dir, langs, roots, label)

    op = results_dir / "within_band_ci.json"
    op.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {op}")


if __name__ == "__main__":
    main()
