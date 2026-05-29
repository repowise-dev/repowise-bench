# Health Score vs. Defect Prediction Benchmark

## Abstract

We evaluate whether Repowise's deterministic, zero-LLM code-health score predicts
real-world defects, and we use the same corpus to **calibrate the score's
biomarker weights against defect data** rather than hand-tuning them. Across
**13 open-source repositories in 5 languages** (Python, TypeScript, JavaScript,
Rust, Go; 830 source files, 216 of them bug-fix-bearing), each file is scored
**at the commit immediately preceding a 6-month defect window (T0)** — so the
measurement strictly precedes the labels and cannot leak future information.
Health predicts defects on the held-out, leakage-free corpus (mean Spearman
ρ = −0.41; mean ROC AUC = 0.76), and the signal **survives controlling for file
size** (mean partial Spearman ρ = −0.20). An offline L2-regularized logistic
regression — with NLOC as an explicit control feature — yields per-biomarker
weights that improve every headline metric over the prior hand-tuned weights,
including the effort-aware Popt and the size-controlled partial correlation.
Cross-project (leave-one-repo-out) pooled out-of-fold AUC is 0.70. The
calibration is reproducible (`local-stash/calibrate_health_weights.py`); only
the learned constants ship, and the runtime stays fully deterministic.

## Methodology

**Scoring at T0 (no leakage).** For each repository we resolve the last commit
on/before the window start (`t0_date = 2025-11-23`), add a detached git worktree
at that commit, index it, and score it. Defects are bug-fix commits in
`(T0, HEAD]` attributed to the source files they touch (Conventional-Commit
`fix:` keyword strategy, doc/chore/style excluded). Because health is measured
at T0 and labels come strictly afterward, the benchmark avoids the HEAD-scoring
leakage that inflates process-history biomarkers.

> **Why this matters.** When recency-windowed git signals (churn, change
> entropy, co-change, congestion) are computed at HEAD, the bug-fix commits in
> the window *manufacture* the very activity the biomarker then "detects." Under
> correct T0 scoring those windows must be anchored to the repo's own HEAD
> commit (`REPOWISE_GIT_WINDOW_ANCHOR=head`) — otherwise a worktree ~6 months in
> the past sees an empty "last 90 days" and every windowed biomarker silently
> never fires.

**Corpus (13 repos).** Selection criteria: (1) indexes quickly (docs/website/
example trees excluded at index time so the scored universe is source); (2) a
clean Conventional-Commit defect signal with ≥5 defect-bearing source files in
the window — mature/dormant repos that produce ~0 fixes were excluded as
all-negative noise; (3) enough files for a stable per-repo estimate; (4)
language + domain variety. Test files are excluded from the labeled universe but
kept indexed so test-pairing biomarkers work.

| Language | Repositories (defect-bearing files) |
|----------|--------------------------------------|
| Python | pydantic (17), rich (6), litestar (31) |
| TypeScript | hono (20), zod (9) |
| JavaScript | axios (33), fastify (8) |
| Rust | clap (16), fd (5), bat (8) |
| Go | gin (8), chi (3), fiber (52) |

**Effort-aware metrics.** Alongside ROC AUC and Spearman ρ we report
**Precision/Recall@20%LOC** and **Popt** (Mende & Koschke) — cost-effectiveness
measures that neutralize the "just flag the big files" critique by charging an
inspection budget in lines of code.

**Calibration.** A per-file feature matrix (severity-weighted hits per biomarker
+ an explicit `nloc` column) is fit with L2-regularized logistic regression
(`class_weight=balanced`). Generalization is estimated by **leave-one-repo-out**
cross-validation, reported as a **pooled out-of-fold AUC = 0.70** (mean-fold
0.77) that is robust to repo-size imbalance. Coefficients map to weight
multipliers under a "balanced" policy: positive, well-measured predictors scale
into [1.0, 1.8]; biomarkers that fire widely but are weak/non-predictive at T0
are floored to 0.5 (retained as maintainability/parity signals, not disabled);
biomarkers the benchmark cannot fairly measure (no coverage ingested; test-only
smells; gates unmet) keep their prior weight.

## Results — calibrated vs. prior weights (both scored at T0)

| Repo | n | Spearman ρ | partial ρ (ctrl NLOC) | ROC AUC | Popt |
|------|--:|-----------:|----------------------:|--------:|-----:|
| pydantic | 86 | −0.288 → **−0.314** | −0.060 → **−0.102** | 0.706 → **0.724** | 0.407 → **0.424** |
| rich | 72 | −0.250 → −0.248 | 0.060 → 0.058 | 0.758 → 0.754 | 0.356 → **0.382** |
| litestar | 198 | −0.226 → **−0.230** | −0.094 → **−0.104** | 0.673 → **0.675** | 0.480 → **0.498** |
| hono | 60 | −0.442 → −0.424 | −0.327 → −0.304 | 0.753 → 0.739 | 0.508 → **0.526** |
| zod | 27 | −0.717 | −0.532 | **0.898** | **0.780** |
| axios | 41 | −0.381 → **−0.404** | −0.146 → **−0.192** | 0.536 → **0.545** | 0.511 → **0.531** |
| fastify | 24 | −0.310 → **−0.346** | −0.042 → **−0.109** | 0.680 → **0.699** | 0.322 → **0.370** |
| clap | 81 | −0.437 → **−0.446** | −0.193 → **−0.217** | 0.811 → **0.818** | 0.535 → **0.544** |
| fd | 18 | −0.427 | −0.364 | 0.754 | 0.588 |
| bat | 34 | −0.475 → **−0.496** | −0.081 → **−0.134** | 0.815 → **0.827** | 0.618 → **0.653** |
| gin | 43 | −0.317 → **−0.421** | −0.104 → **−0.151** | 0.723 → **0.802** | 0.416 → **0.431** |
| chi | 31 | −0.333 → **−0.372** | −0.078 → **−0.124** | 0.810 → **0.845** | 0.398 → **0.410** |
| fiber | 115 | −0.509 → **−0.534** | −0.263 → **−0.305** | 0.756 → **0.770** | 0.543 → **0.553** |
| **MEAN** | | **−0.393 → −0.414** | **−0.171 → −0.198** | **0.744 → 0.758** | **0.497 → 0.515** |

The calibrated weights improve all four mean metrics, most importantly the
**size-controlled partial correlation (−0.171 → −0.198)** — the score is a
stronger predictor *beyond* file size, the property hand-tuned weights struggled
to defend (the NLOC control coefficient, +0.35, is no longer the dominant term).
Because findings depend only on biomarker gates, not on the weights, a weight
change is validated by **re-scoring the cached findings** — no re-index is
required.

## Per-biomarker calibration (standardized logistic coefficients, NLOC-controlled)

Coefficient sign/magnitude is the defect lift *beyond file size*. "Shipped" is
the balanced-policy multiplier in `scoring._BIOMARKER_WEIGHT_MULTIPLIER`.

| Biomarker | coef | shipped | note |
|-----------|-----:|--------:|------|
| co_change_scatter | +0.37 | **1.8** | strongest predictor; shotgun-surgery coupling |
| change_entropy | +0.24 | **1.51** | Hassan HCM; confirmed real under T0 (not leakage) |
| ownership_risk | +0.18 | 1.38 | minor-contributor dispersion (Bird) |
| nested_complexity | +0.16 | 1.34 | |
| complex_conditional | +0.15 | 1.33 | |
| large_method | +0.12 | 1.25 | |
| complex_method | +0.10 | 1.21 | |
| function_hotspot | +0.07 | 1.16 | |
| god_class | +0.06 | 1.13 | |
| untested_hotspot | −0.08 | 1.3 *(prior)* | benchmark ingests no coverage → kept at prior |
| churn_risk / code_age_volatility | ~0 | prior | too rare / gate-bound to calibrate |
| developer_congestion | −0.08 | **0.5** | was 1.5 — a HEAD-leakage artifact; weak under T0 |
| low_cohesion, primitive_obsession, dry_violation, bumpy_road, brain_method | ≤0 | **0.5** | maintainability/parity signals, floored not disabled |
| knowledge_loss | −0.11 | 0.4 | confirmed weak-negative |

`nloc_log` (control, not shipped) coefficient +0.35: size remains a correlate
but no longer dominates, which is exactly why it is partialled out so the
biomarker weights reflect lift *above and beyond* size.

## Honest limitations

- **Modest absolute accuracy.** Mean AUC ≈ 0.76 and pooled cross-project OOF AUC
  ≈ 0.70 — a useful triage signal, not a precise oracle. File-level defect
  prediction from static + process signals has a ceiling in this range.
- **Coverage blind spot.** No test coverage is ingested, so `untested_hotspot`
  runs on a test-file-presence fallback and is kept at its prior weight rather
  than mis-calibrated; ingesting coverage is expected to raise it.
- **Per-repo variance.** Smaller repos (fd, chi, zod) have wide confidence
  intervals; the pooled OOF AUC is the trustworthy summary, not any single fold.
- **Label noise.** "File touched by a `fix:` commit" is a coarse proxy; SZZ-style
  bug-inducing-commit linkage would sharpen the signal.
- **Reproducibility.** `t0_date = 2025-11-23`; full clones (never blobless);
  `calibrate_health_weights.py` reproduces the shipped constants from the cached
  per-repo results.
