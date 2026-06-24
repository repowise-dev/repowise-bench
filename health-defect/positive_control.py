#!/usr/bin/env python3
"""Gate G2 - within-band positive control + per-band power / MDE (R&R panel, R1/R4).

The sharp R1/R4 point: the Q1->Q4 within-band gradient (0.525/0.572/0.593/0.718)
co-moves with the positive COUNT per band (35/47/89/208). Could the Q1/Q2
"collapse to chance" be a base-rate / low-power artifact of having only 35-47
positives, rather than a genuine absence of size-orthogonal signal?

This script answers that with a POSITIVE CONTROL run through the identical
within-band pipeline, plus an analytic per-band MDE:

1. Construct a synthetic risk score with a KNOWN, size-orthogonal effect: every
   file gets an iid latent N(0,1); positives are shifted by delta, where the
   binormal relation A* = Phi(delta/sqrt2) fixes the true within-band AUC to A*
   in EVERY band by construction (the shift does not depend on NLOC or band, so
   the signal is size-orthogonal). The synthetic score reuses the real corpus's
   band membership, per-band n, per-band positive count, and repo clustering.

2. Monte-Carlo power: over M seeded trials, recompute the within-band AUC and a
   one-sided Mann-Whitney test vs 0.5 in each band; report the mean recovered AUC
   (should track A*) and the empirical POWER (fraction of trials whose band test
   rejects AUC <= 0.5 at one-sided 0.05). High power at Q1/Q2 means the within-band
   test WOULD detect a genuine size-orthogonal effect at those sample sizes, so the
   observed Q1/Q2 collapse is not a power artifact.

3. Analytic MDE: the smallest true AUC each band can detect at 80% power, two-sided
   0.05, from the exact null SE of AUC (no-tie Mann-Whitney variance).

4. For the headline-matching effect (A* = 0.72) it also pushes ONE seeded synthetic
   realization through within_band_ci.bootstrap_ci (the exact 2000-rep repo-cluster
   bootstrap from Sec 4.1) to show the per-band 95% CI excludes 0.5 in Q1/Q2.

Cache-only, deterministic. Seed 12345. Canonical 21-repo corpus, cuts 22/48/108.

Usage (venv python):
    PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe positive_control.py
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import NormalDist

import numpy as np

from error_analysis import auc, band_of, build_rows, load_config_langs
from within_band_ci import CUTS, EXCLUDE, BANDS, N_BOOT, SEED, bootstrap_ci

_ND = NormalDist()
_TARGETS = [0.62, 0.72]          # modest and headline-matching size-orthogonal effects
_M = 2000                        # Monte-Carlo trials for empirical power
_RESULTS = Path(__file__).resolve().parent.parent / "results"


def load_rows() -> list[dict]:
    here = Path(__file__).resolve().parent
    langs, roots = load_config_langs(here / "config.yaml")
    langs = {k: v for k, v in langs.items() if k not in EXCLUDE}
    rows = build_rows(_RESULTS, langs, roots, label="keyword")
    for r in rows:
        r["band"] = band_of(r["nloc"], CUTS)
    return rows


def null_se(n_pos: int, n_neg: int) -> float:
    """Exact null SE of AUC (no-tie Mann-Whitney variance)."""
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return ((n_pos + n_neg + 1) / (12.0 * n_pos * n_neg)) ** 0.5


def mde(n_pos: int, n_neg: int, power: float = 0.80, alpha: float = 0.05) -> float:
    """Minimum detectable AUC above 0.5 at the given power (two-sided alpha)."""
    se0 = null_se(n_pos, n_neg)
    z = _ND.inv_cdf(1 - alpha / 2) + _ND.inv_cdf(power)
    return 0.5 + z * se0


def power_sim(rows: list[dict], a_star: float, m: int, seed: int) -> dict:
    """Monte-Carlo power: synthetic size-orthogonal score with true AUC a_star."""
    delta = (2 ** 0.5) * _ND.inv_cdf(a_star)
    rng = np.random.default_rng(seed)
    y = np.array([r["y"] for r in rows])
    bands = np.array([r["band"] for r in rows])
    band_idx = {b: np.where(bands == b)[0] for b in BANDS}
    z_alpha = _ND.inv_cdf(1 - 0.05)          # one-sided 0.05 detection test
    recovered = {b: [] for b in BANDS}
    rejects = {b: 0 for b in BANDS}
    for _ in range(m):
        latent = rng.standard_normal(len(rows)) + delta * y
        for b in BANDS:
            idx = band_idx[b]
            yb = y[idx].tolist()
            sb = latent[idx].tolist()
            a = auc(yb, sb)
            if a is None:
                continue
            recovered[b].append(a)
            npos = int(sum(yb))
            nneg = len(yb) - npos
            se0 = null_se(npos, nneg)
            if se0 == se0 and (a - 0.5) / se0 >= z_alpha:
                rejects[b] += 1
    out = {}
    for b in BANDS:
        rec = recovered[b]
        out[b] = {
            "mean_recovered_auc": float(np.mean(rec)) if rec else None,
            "sd_recovered_auc": float(np.std(rec)) if rec else None,
            "power": rejects[b] / m,
        }
    return out


def main() -> None:
    rows = load_rows()
    band_counts = {}
    for b in BANDS:
        members = [r for r in rows if r["band"] == b]
        npos = sum(r["y"] for r in members)
        band_counts[b] = {"n": len(members), "positives": npos,
                          "negatives": len(members) - npos}
    print(f"Gate G2 positive control | {len(set(r['repo'] for r in rows))} repos | "
          f"{len(rows)} files | cuts {CUTS} | seed {SEED} | M={_M}\n")

    # Per-band analytic MDE.
    print("--- Per-band analytic MDE (smallest true AUC detectable, 80% power, two-sided 0.05) ---")
    mde_tbl = {}
    for b in BANDS:
        c = band_counts[b]
        m_ = mde(c["positives"], c["negatives"])
        mde_tbl[b] = m_
        print(f"  {b:14s} n={c['n']:4d} pos={c['positives']:3d} neg={c['negatives']:3d}  "
              f"null-SE={null_se(c['positives'], c['negatives']):.4f}  MDE-AUC={m_:.3f}")

    # Monte-Carlo power for each target effect.
    sims = {}
    for a_star in _TARGETS:
        sim = power_sim(rows, a_star, _M, SEED)
        sims[f"{a_star:.2f}"] = sim
        print(f"\n--- Positive control: injected size-orthogonal AUC A*={a_star:.2f} "
              f"({_M} trials) ---")
        print(f"  {'band':14s} {'mean recovered':>15s} {'power(>0.5)':>12s}")
        for b in BANDS:
            s = sim[b]
            print(f"  {b:14s} {s['mean_recovered_auc']:15.3f} {s['power']:12.3f}")

    # One seeded realization of A*=0.72 through the exact Sec 4.1 cluster bootstrap.
    a_star = 0.72
    delta = (2 ** 0.5) * _ND.inv_cdf(a_star)
    rng = np.random.default_rng(SEED)
    latent = rng.standard_normal(len(rows)) + delta * np.array([r["y"] for r in rows])
    syn_rows = [{**r, "risk": float(latent[i])} for i, r in enumerate(rows)]
    band_ci, pooled_ci = bootstrap_ci(syn_rows, np.random.default_rng(SEED))
    print(f"\n--- One synthetic realization A*={a_star:.2f} through Sec 4.1 cluster "
          f"bootstrap ({N_BOOT} reps) ---")
    realized = {}
    for b in BANDS:
        members = [r for r in syn_rows if r["band"] == b]
        a = auc([r["y"] for r in members], [r["risk"] for r in members])
        lo, hi, reps = band_ci[b]
        excl = (lo is not None and lo > 0.5)
        realized[b] = {"within_auc": a, "ci_lo": lo, "ci_hi": hi,
                       "excludes_0.5": excl, "boot_reps": reps}
        print(f"  {b:14s} within-AUC={a:.3f}  95% CI [{lo:.3f}, {hi:.3f}]  "
              f"excludes 0.5: {excl}")

    out = {
        "corpus_repos": len(set(r["repo"] for r in rows)),
        "n_files": len(rows), "cuts": CUTS, "seed": SEED, "n_mc": _M,
        "n_boot": N_BOOT, "band_counts": band_counts,
        "mde_80pct_two_sided_05": mde_tbl,
        "power_sim": sims,
        "realization_A072_cluster_bootstrap": realized,
    }
    op = _RESULTS / "positive_control.json"
    op.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {op}")


if __name__ == "__main__":
    main()
