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
Cross-project (leave-one-repo-out) pooled out-of-fold AUC is 0.70. Against
trivial baselines the score **significantly out-discriminates recent churn and
prior-defect history** (ΔAUC +0.10 / +0.09, bootstrap CIs excluding 0) and is
**not reducible to file size** — it significantly beats a LOC-only ranking on the
effort-aware Popt; for pure cost-effective inspection ordering, however, the
cheap process-history baselines remain stronger. Re-deriving the labels with
leakage-free **SZZ bug-inducing-commit attribution** leaves accuracy essentially
unchanged (mean AUC 0.744 → 0.734), so on this corpus label noise is not the
dominant accuracy ceiling. The calibration is reproducible
(`local-stash/calibrate_health_weights.py`); only the learned constants ship, and
the runtime stays fully deterministic.

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

## Ground-truth labels (SZZ + issue linkage) and trivial baselines

The headline above uses the **keyword** label — a file is defective if a
Conventional-Commit `fix:` touched it in `(T0, HEAD]`. The literature warns this
is noisy (a fix commit can touch innocent files; a "fix" can be a refactor), so
we recompute the labels two stricter ways and re-test against trivial baselines.

**SZZ (bug-inducing-commit attribution).** For every fix we `git blame -w -C` the
lines it changed back to the commit(s) that last wrote them; a file is defective
**iff a bug-inducing commit that already existed at T0 (an ancestor of the T0
commit) touched it**. This attributes the defect to the file that *contained* the
bug at T0 and drops fixes whose buggy lines were introduced *after* T0 — exactly
what health-at-T0 should predict. We compute AG-SZZ (default: ignore blank/
comment/punctuation lines, drop fix-of-fix inducers) and B-SZZ (every blamed
ancestor). Labels are deterministic and cached.

**Issue linkage.** Fixes that close a GitHub issue labeled bug/defect/regression
(resolved via the `gh` API) form a near-ground-truth subset. On this corpus it is
**too sparse to calibrate on**: across all 13 repos only **5** fix commits link to
a bug-labeled issue (2 survive SZZ). These repos use Conventional Commits with
`(#PR)` suffixes rather than `fixes #issue` links to triaged bugs — a real
issue-hygiene limitation, reported rather than worked around.

**What SZZ changes — and what it doesn't.** AG-SZZ strips **17%** of the keyword
positives (216 → 179): the leakage (post-T0-introduced lines) and pure-addition
fixes (nothing to blame) the keyword label wrongly kept. Yet the measured score
accuracy is **essentially unchanged**:

| | keyword label | SZZ (AG) label |
|---|--:|--:|
| Corpus positives | 216 | 179 |
| Health mean ROC AUC | 0.744 | 0.734 |
| Health mean Popt | 0.497 | 0.485 |
| Calibration pooled OOF AUC | **0.699** | 0.661 |

Re-calibrating on the SZZ labels yields a **lower** cross-project pooled OOF AUC
(0.661 vs 0.699), and the shipped score's own discrimination moves <0.02 between
the two label definitions. So on this corpus, leakage-free ground-truth
attribution is **not** the dominant accuracy lever the literature suggests, and
the SZZ-fit weights do not generalize better — **the shipped (keyword-calibrated)
weights are kept unchanged.** The score predicts "where bugs originate" (SZZ)
about as well as "where fixes land" (keyword).

**Trivial baselines (SZZ labels; mean over 13 repos).** A predictor is only
interesting if it beats what is free. We score the identical universe by file
size, recent churn, prior defects, and a deterministic pseudo-random order:

| Predictor | ROC AUC | Popt |
|-----------|--------:|-----:|
| **health (calibrated)** | **0.734** | **0.485** |
| LOC-only (size) | 0.742 | 0.376 |
| churn (commits ≤90d at T0) | 0.629 | 0.544 |
| prior defects (<T0 bug-fixes) | 0.644 | 0.630 |
| random | 0.490 | 0.469 |

Paired health − baseline deltas, **bootstrap 95% CI resampled over the 13 repos**
(SZZ labels; `*` = CI excludes 0):

| Comparison | ΔROC AUC [95% CI] | ΔPopt [95% CI] |
|------------|------------------:|---------------:|
| health − LOC-only | −0.008 [−0.044, +0.029] | **+0.109 [+0.039, +0.204] \*** |
| health − churn | **+0.105 [+0.048, +0.173] \*** | −0.059 [−0.112, −0.001] \* |
| health − prior-defects | **+0.090 [+0.032, +0.143] \*** | −0.145 [−0.194, −0.085] \* |

Read together, this is the honest standing of the score:

- **It is not a size proxy.** On the effort-aware Popt — which charges an
  inspection budget in LOC and so penalizes "just read the big files" — health
  **significantly beats LOC-only** (+0.109). On raw AUC the two tie, because raw
  AUC rewards size (big files carry more bugs); Popt is the size-fair metric and
  health wins it.
- **As a discriminator it beats the process baselines.** Health significantly
  out-ranks both churn (+0.105 AUC) and prior-defects (+0.090 AUC) — it separates
  buggy from clean files better than recent activity or recurrence alone.
- **As a cost-effective inspection order it does not beat them.** On Popt the
  cheap process-history baselines (prior-defects 0.630, churn 0.544) remain
  stronger than health (0.485). "Inspect what broke before / what's churning"
  is hard to beat for raw bug-finding efficiency — a well-known result. Health's
  value is in *discrimination and explanation* (a calibrated, attributable
  structural signal), not in replacing process history for triage ordering.

## Test-coverage ingestion and continuous biomarker features

The headline above scores every biomarker as a **binary** severity-weighted hit
("fired / didn't"), which discards magnitude (CCN = 30 reads identically to
CCN = 9), and ingests **no test coverage** (so `untested_hotspot` /
`coverage_gap` ran on a test-file-presence fallback). Both gaps are addressed
here.

**Coverage acquisition (tiered, near-T0).** Per-file line coverage was acquired
for **7 of 13 repos**, normalized to one schema keyed by repo-relative path:

| repo | lang | source | date | skew from T0 | files |
|---|---|---|---|--:|--:|
| fiber | Go | Codecov | 2025-11-23 | 0d | 115 |
| axios | JS | run (c8) | 2025-11-23 | 0d | 55 |
| fastify | JS | run (c8) | 2025-11-23 | 0d | 29 |
| hono | TS | Codecov | 2025-11-22 | 1d | 170 |
| litestar | Py | Codecov | 2025-11-22 | 1d | 296 |
| gin | Go | Codecov | 2025-11-20 | 3d | 41 |
| rich | Py | Codecov | 2025-10-09 | 45d | 74 |

Tier 1 scrapes the Codecov v2 public API at the covered commit nearest T0
(≤120-day skew guard; stale multi-year reports rejected — absent ≠ zero). Tier 2
runs the suite at the T0 worktree under coverage (`pytest --cov`; `c8` for
JS/TS). The remaining 6 repos have no near-T0 coverage source (Rust/Go without a
local toolchain, or no Codecov upload) and stay coverage-blind — marked absent,
never imputed to 0%.

**What real coverage changes.** The corpus repos are 91–98% covered, so the
binary coverage biomarkers fire **sparsely** (coverage_gap: axios 3, litestar/
hono/fiber 1 each; untested_hotspot: fastify 1) — correct behavior, not a bug:
few files dip below the <60% / <40% gates. The predictive signal instead lives
in the **continuous** uncovered fraction.

**Continuous features (ablation, full corpus, keyword labels).** Replacing each
biomarker's binary hit with the log of its underlying magnitude (CCN, nesting,
entropy, scatter, …) and adding max-CCN / max-nesting / coverage columns:

| feature encoding | pooled OOF AUC |
|---|--:|
| binary severity-weighted hits | 0.693 |
| **continuous magnitudes + coverage** | **0.744** |
| | **ΔAUC +0.051** |

In the continuous fit the **uncovered fraction is the single strongest positive
coefficient** (+0.42, above change-entropy +0.35 and log-NLOC +0.34).

**Coverage's isolated value (the 7 covered repos).** Fitting the continuous
model with vs. without the coverage columns — where the coverage-known indicator
is constant and contributes nothing, so the delta is pure uncovered-fraction:

| | pooled OOF AUC |
|---|--:|
| continuous, no coverage | 0.650 |
| **+ coverage** | **0.706** |
| | **ΔAUC +0.056** |

So coverage predicts defects **even though the binary coverage biomarkers barely
fire** on these high-coverage repos — the signal is in the continuous gradient,
which the current binary gates under-exploit.

**Ship decision.** These gains require a continuous / non-linear runtime scoring
term, whereas the shipped score uses interpretable per-finding severity
deductions (a linear-attribution constraint we keep deliberately). So the
**calibrated weights are unchanged**; the result quantifies real headroom
(≈ +0.05 AUC) for a future continuous-coverage scoring track. The coverage
*ingestion* path itself ships (normalized-JSON artifact → `repowise health
--coverage`).

## Honest limitations

- **Modest absolute accuracy.** Mean AUC ≈ 0.76 and pooled cross-project OOF AUC
  ≈ 0.70 — a useful triage signal, not a precise oracle. File-level defect
  prediction from static + process signals has a ceiling in this range.
- **Coverage is partial + skewed.** Coverage was acquired for 7/13 repos at a
  0–45-day skew from T0 (Rust and toolchain-less repos stay blind). Where present
  it is a strong continuous predictor (+0.056 AUC), but the binary coverage
  biomarkers fire rarely on 91–98%-covered repos and so calibrate weakly; the
  continuous coverage gradient is the lever, and capturing it is a runtime
  model change deferred on interpretability grounds.
- **Per-repo variance.** Smaller repos (fd, chi, zod) have wide confidence
  intervals; the pooled OOF AUC is the trustworthy summary, not any single fold.
- **Label definition.** Results are reported on the keyword label; leakage-free
  SZZ bug-inducing-commit attribution and a bug-labeled-issue subset were tested
  and changed measured accuracy by <0.02 AUC (see the labels/baselines section),
  so the keyword-calibrated weights are kept. SZZ is itself imperfect (blame
  noise, shallow-clone blame boundaries) and is reported as a cross-check, not
  treated as perfect ground truth.
- **Triage ordering vs. discrimination.** On effort-aware Popt the cheap
  process-history baselines (prior-defects, churn) out-rank health; the score's
  edge is discrimination (AUC) and attributable explanation, not raw
  bug-finding-per-LOC. Combining health with prior-defect history is the natural
  next step.
- **Reproducibility.** `t0_date = 2025-11-23`; full clones (never blobless);
  `calibrate_health_weights.py` reproduces the shipped constants from the cached
  per-repo results.
