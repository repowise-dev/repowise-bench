# health-defect — leakage-free, size-orthogonal code-health defect evaluation

The benchmark harness behind Repowise's code-health score and the ICSE 2027 paper
*"The Size-Confound Wall: A Leakage-Free, Size-Orthogonal Re-Evaluation of
Code-Health Defect Prediction."* It scores files at a frozen point in time (T0),
counts the bug-fixing commits that land afterward, and measures whether the health
score discriminates defective files **beyond the file-size confound**.

> This README was rewritten 2026-06-24 (closing code-health-v2 plan item H4). The
> earlier version described a 3-repo Django/Pydantic/FastAPI pilot with at-HEAD
> numbers (AUC ~0.70); those are superseded by the 21-repo, 9-language, T0-anchored
> evaluation below. The honest source of truth for scoring is
> `local-stash/code-health-v2/STATE_OF_HEALTH.md`.

## Headline numbers (canonical 21-repo corpus)

21 open-source repositories, 9 languages, 2{,}826 source files, T0 = 2025-11-23,
6-month forward defect window. Leakage-free: every feature is computed from a
worktree truncated at T0; recency windows are re-anchored to the T0 commit.

| Metric | Value | Note |
|---|---|---|
| ROC AUC, cross-project mean | **0.737 [0.683, 0.787]** | the headline; repo-cluster bootstrap |
| ROC AUC, file-pooled | **0.732** | file-level counterpart |
| Partial Spearman vs NLOC | **-0.156 [-0.233, -0.080]** | discrimination beyond file size |
| Popt vs LOC | **+0.134 [+0.080, +0.198]** | effort-aware, not a size proxy |

Do NOT quote 0.699 (old 13-repo), 0.746 (LORO pooled), or 0.87 (Hugo HEAD-scored)
as the headline. See `BENCHMARK_REPORT.md` for the full table + CIs and
`local-stash/code-health-v2/STATE_OF_HEALTH.md` for the numbers-discipline ledger.

### The size-confound wall (the paper's payload)

Within a fixed NLOC quartile, discrimination collapses on small and medium files:

| NLOC band | files | pos. | within-band AUC [95% CI] |
|---|---:|---:|---|
| Q1 (<=22)   | 727 | 35  | 0.525 [0.438, 0.629] |
| Q2 (23-48)  | 696 | 47  | 0.572 [0.472, 0.700] |
| Q3 (49-108) | 698 | 89  | 0.593 [0.534, 0.642] |
| Q4 (>108)   | 705 | 208 | 0.718 [0.669, 0.757] |

A pooled AUC of 0.73 is largely the between-band size contrast; once size is held
fixed, signal survives only where files are large. Q1/Q2 straddle 0.5.

## Layout

```
health-defect/
  run_benchmark.py          T0 scoring + defect counting + per-repo stats
  reproduce.py              re-derive the locked summaries from cache (seeded)
  statistical_rigor.py      headline AUC / partial-Spearman / Popt + cluster bootstrap CIs
  error_analysis.py         failure forensics + within-band (NLOC-quartile) AUC (--nloc-cuts)
  within_band_ci.py         per-band repo-cluster bootstrap CIs (the wall table, Sec 4.1)
  f4_curve.py               effort-aware cost curve (recall@20%LOC, Popt) vs CodeScene / LOC
  codescene_*.py            CodeScene head-to-head (Sec 5.4 vignette)
  candidate_eval.py         the 5-part promotion scorecard (a column in, a verdict out)
  bootstrap_tost.py         fixed-prediction percentile-bootstrap TOST (six-null equivalence)
  calibrate_health_weights.py   the L2-logistic calibration the score ships
  rescore_benchmark.py      re-score the corpus from a changed scoring model
  experiments/              concluded one-off candidate generators (centrality, change
                            bursts, error-handling, naturalness, GAM, coverage, JIT, ...)
  config.yaml               the corpus (22 repos; cockroach is the large-repo demo, excluded)
  lib/                      shared metric primitives (roc_auc, partial_spearman, popt, ...)
```

`error_analysis.py` lives at BOTH this top level and in `experiments/` (each is
path-adapted to its directory). Run the paper pipeline from this top-level dir so
`import error_analysis` resolves to the top-level copy.

## ICSE R&R experiment gates (2026-06-24)

Pre-June-30 gates the unbiased review panel demanded. Each is cache-only,
deterministic (seed 12345); small JSON summaries are tracked under `../results/`.

| Gate | Script | Artifact | Result |
|---|---|---|---|
| **F** prior-defect ablation | `prior_defect_ablation.py` | `prior_defect_ablation.json` | Drop `prior_defect` from the calibrated score: mean AUC unchanged 0.737, pooled 0.732->0.729, partial-rho strengthens -0.156->-0.160, wall stays monotone 0.535/0.557/0.597/0.709. The wall is not an artifact of it. |
| **G2** positive control | `positive_control.py` | `positive_control.json` | A synthetic size-orthogonal effect run through the within-band pipeline: per-band MDE (80% power) Q1 0.640 / Q2 0.622; MC power at A*=0.72 is 0.998/1.000 in Q1/Q2. The Q1/Q2 collapse is a real absence of signal, not a base-rate/power artifact. |
| **G5** refit-resampling TOST | `refit_bootstrap_tost.py` | `refit_bootstrap_tost.json` | Bootstrap TOST that re-runs the full LORO fit per replicate (includes model-refit variance). On review coverage the 90% CI widens 1.9x and the verdict moves equivalent@0.02 -> non-equivalent. The three firm nulls need their candidate columns regenerated from `experiments/` (R&R window). |

The full gate ledger (with the R&R-window gates G1/G3/G4) lives in
`research/40_experiments/R_AND_R_EXPERIMENTS.md`.

The validated build of `prior_defect_ablation.py`: the shipped file score is
exactly `health = max(1, 10 - sum|impact|)` over the scoring biomarkers (verified
on all 2{,}826 files, 0 mismatch), so dropping a biomarker is reconstructed
faithfully from the cached per-finding `health_impact`.

## Methodology

```
T0 (2025-11-23)                       T0 + 6 months
│                                      │
│  1. Score each repo at T0 with a     │
│     worktree truncated to T0         │
│     -> per-file health (1-10)        │
│                                      │
│  2. Count bug-fixing commits T0->T1  │
│     under the keyword AND SZZ labels │
│     -> per-file defect counts        │
│                                      │
│  3. Evaluate discrimination, holding │
│     file size fixed (within-band)    │
└──────────────────────────────────────┘
```

- **Labels.** Per repo: `keyword` / `prefix` / `gitmoji` heuristics, plus an
  independent SZZ label set, so the wall can be shown label-robust.
- **Metrics.** ROC AUC, size-controlled partial Spearman, effort-aware Popt and
  recall@20%LOC, all with two-stage repo-cluster bootstrap CIs (the repository is
  the unit of generalization).
- **Filters.** Source files only (no tests/docs/config), minimum NLOC, under
  `source_root`.

## Reproduction

Run with the shared venv and `PYTHONIOENCODING=utf-8` (the scorecard writers crash
on cp1252). Deterministic, seed 12345.

```bash
cd repowise-bench/health-defect

# Re-derive the locked headline summaries from the committed cache (no re-index):
PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe reproduce.py

# Headline AUC / partial-Spearman / Popt with bootstrap CIs:
PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe statistical_rigor.py

# The within-band wall + per-band CIs:
PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe within_band_ci.py

# The R&R gates:
PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe prior_defect_ablation.py   # F
PYTHONIOENCODING=utf-8 ../../.venv/Scripts/python.exe positive_control.py        # G2
PYTHONIOENCODING=utf-8 PYTHONPATH=. ../../.venv/Scripts/python.exe refit_bootstrap_tost.py  # G5
```

Re-scoring from source requires the corpus checkouts under `../repos/` (gitignored,
fetched on demand) and a per-repo Repowise index; see `run_benchmark.py --help`.
The committed `../results/health_defect_<repo>/{joined_data,health_scores}.json`
caches let every headline number reproduce without re-indexing.

## Adding a repo

Add an entry to `config.yaml` (`name`, `repo_url`, `language`, `source_root`,
`t0_date`, `defect_strategy` + its keyword/prefix/gitmoji fields), clone + index it
under `../repos/`, then `python run_benchmark.py --repo <name>`.

## Pointers

- Honest scoring source of truth: `local-stash/code-health-v2/STATE_OF_HEALTH.md`.
- Live scoring code: `packages/core/src/repowise/core/analysis/health/`.
- ICSE paper workspace (single entry point): `research/README.md`.
- Numbers discipline + the six-null ledger: `code-health-v2/OSS_PROMOTION_LEDGER.md`.
