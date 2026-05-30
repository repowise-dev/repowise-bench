# Same-Repo Tool Comparison & Issue-Resolution-Time Analysis

> Companion to `BENCHMARK_REPORT.md`. Where that report establishes that
> Repowise's code-health score predicts defects in absolute terms, this one
> answers two competitive questions the absolute study cannot:
>
> 1. **Same-repo head-to-head** — scoring the *same* files at the *same* commit
>    with *both* Repowise and an external Code-Health scorer, joining the *same*
>    defect labels, and comparing on identical metrics with a paired significance
>    test. This is the only design that can say "better", not just "good".
> 2. **Business-impact replication** — reproducing (on open data) the
>    resolution-time / cost-of-fixing claims that motivate code-health scoring,
>    using GitHub issue/PR timestamps as the open analog of proprietary
>    issue-tracker cycle time.
>
> Every headline number carries a repo-cluster bootstrap 95% CI and an n.
> Cache-only and deterministic (seeded); reproduces from the committed
> `results/` cache. Scripts: `codescene_headtohead.py`, `resolution_time.py`,
> `pr_effort_signals.py`.

## Abstract

On **2,770 source files across nine languages** — the same files, scored at the
same leakage-free T0 commit, joined to the same bug-fix defect labels — Repowise's
deterministic code-health score is compared head-to-head against an established
external Code-Health scorer, on five independent axes with **paired** significance
tests (the same resampled repos for both tools, so every test is on the *gap*,
not on two separate CIs):

- **Discrimination (AUC):** Repowise 0.731 vs 0.705; paired DeLong ΔAUC +0.026,
  z = 1.93, **p = 0.054** — a consistent edge right at the threshold.
- **Effort-aware ranking (Popt):** paired Δ **+0.144 [+0.028, +0.236],
  p = 0.003** — significant Repowise lead. Under a fixed review budget this is
  the metric that matters, and recall@20%LOC is **+0.098 [+0.030, +0.209],
  p = 0.003**, also significant.
- **Beyond file size:** a tie that *both* win — size-controlled partial Spearman
  excludes zero for both tools (−0.148 vs −0.137), so neither score is a proxy
  for lines of code.
- **Defect density (size-normalized concentration):** Repowise's Alert:Healthy
  defects/KLOC lead is paired Δ **+1.62 [+0.31, +3.04], p = 0.003** —
  significant.
- **Precision at a tiny budget:** the external tool leads 0.64 vs 0.58, but the
  paired delta is **not** significant (−0.056 [−0.176, +0.122], p = 0.64); it
  reflects a deliberately more conservative operating point (27 Alert files vs
  Repowise's 132), not a calibration flaw.

The external tool could not score 61 of 2,831 files (2.2%, pure-declaration);
those are reported, not hidden, and the comparison runs on the 2,770-file paired
intersection.

On **business impact**, the open GitHub resolution-time proxy does **not**
reproduce the published −0.58 health-vs-resolution-time correlation: pooled over
17 repos the wall-clock PR-merge-time correlation is ≈0 and the Alert:Healthy
ratio CI spans 1. The reason is structural — GitHub merge-time is dominated by
maintainer review-queue latency, not change difficulty. We then tested
**queue-independent** GitHub-native effort signals (review iterations,
post-review rework, comment density) that should isolate change difficulty from
queue latency; at full corpus scale these too are flat (all Spearman ≈ 0, CIs
spanning zero). The honest conclusion: **open GitHub metadata does not reproduce
the proprietary-Jira business-impact finding** — a faithful replication needs
issue-tracker in-development cycle time (e.g. Apache Jira status transitions),
named as future work, not run here. The defect-*prediction* superiority (Part 1)
stands on its own; the business-impact axis remains CodeScene's, unreplicated on
open data.

---

## Part 1 — Same-repo, same-label head-to-head

### Design

For each of the 21 corpus repos we take the committed `joined_data.json` — every
source file scored by Repowise at the repo's T0 commit, with its NLOC and our
keyword defect label — and add a **second** health score for the *identical*
files at the *identical* T0 commit from the external scorer's CLI
(`<T0_SHA>:./path` review, 1–10 scale, same Healthy ≥ 8 / Alert < 4 bucketing as
ours). Files the external tool cannot score (returns "no scorable code") are
dropped from **both** corpora and counted as a coverage gap. Both score columns
then run through the **same** metric code path (`statistical_rigor.py`):
ROC AUC, size-controlled partial Spearman, Popt, precision/recall@20%LOC — each
with a two-stage cluster bootstrap (resample repos, then files) — plus a **paired
DeLong** test on the two AUCs over the pooled, row-aligned files.

This is a fair fight by construction: same files, same commit, same labels, same
estimator. Neither score was tuned to these labels.

### Coverage

| | Repowise | External tool |
|---|---:|---:|
| Files scored | 2,831 | 2,770 |
| Could not score | 0 | 61 (2.2%) |
| Paired files compared | — | **2,770** |
| Defective (paired) | — | 376 |

The 61 unscored files are pure-declaration / no-executable-code files the
external tool declines; Repowise scores them. We compare on the 2,770-file
paired intersection so both tools see exactly the same universe.

### Results — per-tool point estimates (pooled, 95% CI)

| Metric | Repowise | External tool |
|---|---|---|
| ROC AUC (pooled) | 0.731 [0.660, 0.791] | 0.705 [0.659, 0.743] |
| ROC AUC (cross-project mean) | 0.740 [0.687, 0.791] | 0.709 [0.650, 0.761] |
| Popt (effort-aware ranking) | 0.607 [0.485, 0.687] | 0.462 [0.377, 0.558] |
| Recall @ 20% LOC | 0.173 [0.117, 0.276] | 0.074 [0.023, 0.129] |
| Partial Spearman vs NLOC | −0.148 [−0.237, −0.073] | −0.137 [−0.215, −0.063] |
| Precision @ 20% LOC | 0.580 [0.441, 0.747] | 0.636 [0.358, 0.825] |
| Defect conc. (defects/file, Alert:Healthy) | 16.9× [8.6, 29.0] | 14.2× [5.5, 30.1] |
| Defect conc. (defects/KLOC, size-norm.) | 2.18× [1.00, 3.58] | 0.56× [0.26, 1.37] |

Two overlapping per-tool CIs are **not** a test on the difference. The verdicts
below come from **paired** tests — the same resampled repos for both tools so the
estimate is the gap itself (paired DeLong for AUC; repo-cluster bootstrap of the
per-replicate Repowise−External delta for the rest, `codescene_paired_deltas.py`).

### Results — paired significance (the actual tests)

| Axis | Test | Δ (RW − Ext) [95% CI] | p (two-sided) | Verdict |
|---|---|---|---|---|
| **Discrimination** | paired DeLong, AUC | +0.026, z = 1.93 | **0.054** | Edge, just shy of sig. |
| **Effort-ranking** | cluster boot, ΔPopt | **+0.144 [+0.028, +0.236]** | **0.003** | ✅ Repowise sig. |
| **Effort-ranking** | cluster boot, Δrecall@20% | **+0.098 [+0.030, +0.209]** | **0.003** | ✅ Repowise sig. |
| **Beyond size** | partial-ρ vs NLOC (both) | −0.148 vs −0.137; both excl. 0 | — | Tie — both beat size |
| **Density** | cluster boot, Δ(defects/KLOC ratio) | **+1.62 [+0.31, +3.04]** | **0.003** | ✅ Repowise sig. |
| **Density (raw)** | cluster boot, Δ(defects/file ratio) | +2.76 [−8.92, +12.26] | 0.65 | n.s. (heavy tail) |
| **Precision-at-budget** | cluster boot, Δprecision@20% | −0.056 [−0.176, +0.122] | 0.64 | n.s. (External leads, not sig.) |

(376 positives / 2,394 negatives; paired = same resampled repos for both tools,
delta recomputed per replicate; **two-sided** bootstrap p that the gap is zero,
used for every axis including precision where the external tool leads; 2,000
resamples, seeded; `codescene_paired_deltas.py`. DeLong's p is its standard
two-sided form.)

### Reading — five independent axes

The eight rows above collapse to five axes a sharp reader can't dismiss as padded
correlated metrics:

- **Discrimination (AUC): a real edge, marginally shy of significance.** Paired
  DeLong p = 0.054. The honest statement is "at least as good, consistent small
  edge," not "significantly better on AUC."
- **Effort-aware ranking: Repowise wins, significantly.** ΔPopt +0.144
  (p = 0.003) and Δrecall@20%LOC +0.098 (p = 0.003) — both paired CIs exclude
  zero. Under a fixed review budget, ranking by Repowise health surfaces more
  than twice the defects (recall 0.173 vs 0.074). This is the operationally
  decisive result and it is now demonstrated, not asserted.
- **Beyond file size: a tie that both win.** Size-controlled partial Spearman
  excludes zero for *both* tools (−0.148 vs −0.137) — neither score is merely a
  proxy for lines of code. This is the clean "beyond size" result; the
  size-normalized **density** lead below is the additional, also-significant
  evidence, not the load-bearing claim.
- **Defect density: Repowise's size-normalized concentration wins, significantly.**
  The paired Δ(defects/KLOC ratio) is +1.62 [+0.31, +3.04], p = 0.003 — so
  Repowise's "red" flag marks genuinely defect-dense code, not just big files.
  (The raw defects/file ratio delta is not significant — heavy-tailed — so the
  size-normalized version is the one to cite.)
- **Precision at a tiny budget: the external tool leads, but not significantly.**
  0.636 vs 0.580, paired Δ −0.056 [−0.176, +0.122], p = 0.64. This is a genuine,
  defensible operating-point choice on its part: it flags far fewer files as
  unhealthy (27 Alert vs Repowise's 132), so for a reviewer with a *very* small
  budget its top band is purer, at the cost of much lower recall and Popt. The two
  tools simply tune to different points on the same signal (see
  `BENCHMARK_REPORT.md` "Two operating points") — not a flaw in either.

**Defensible claim.** *On the same 2,770 files across nine languages with
identical defect labels, Repowise's deterministic, open, zero-LLM code-health
score is significantly better on effort-aware ranking (Popt +0.144, p = 0.003;
recall +0.098, p = 0.003) and on size-normalized defect density (p = 0.003),
holds a consistent discrimination edge marginally shy of significance (AUC DeLong
p = 0.054), and is equally robust to the file-size confound. The external tool's
only lead — precision at a very small inspection budget — is a deliberate
conservative operating point and is not statistically significant (p = 0.64).
All p-values two-sided.*

---

## Part 2 — Business-impact (resolution-time) replication on open data

The published Code Red study's strongest evidence is business impact: **+124%**
mean issue-resolution time in low-quality vs healthy code, and Pearson **−0.58**
between Code Health and mean resolution time per file — both from proprietary
Jira cycle-time data. We test how far an **open** proxy reproduces this.

### 2a. Wall-clock resolution time (GitHub PR merge-time)

For every post-T0 bug-fix commit referencing a GitHub ticket (`fixes|closes #N`),
the ticket's `closed_at − created_at` is attributed to the source files that
commit changed; per file we take the mean. **Corpus reality:** these repos
squash-merge, so fix commits reference their **PR** number, not a bug issue —
17 of 21 repos linked fixes to a timed PR (the four misses carry no `#refs` at
all). We therefore measure **PR open→merge time** (a populated proxy) and report
the issue-only path as sparse.

**Result (17 repos, 271 files):**

| Metric | Published (Jira) | Ours (GitHub PR merge-time) |
|---|---|---|
| Alert:Healthy mean ratio | +124% (2.24×) | 5.55× [0.35, 23.06] |
| Alert:Healthy **median** ratio (robust) | — | 1.43× [0.28, 6.69] |
| Pearson(health, time) | **−0.58** | −0.09 [−0.19, 0.14] |
| Spearman(health, time) (robust) | — | +0.02 [−0.20, 0.23] |

**We do not reproduce the claim with this proxy.** Every CI spans the null. The
dramatic mean ratio is a heavy-tail artifact (a few stale long-open PRs); the
robust median ratio and rank correlation are flat.

**Why — and it is structural, not a coding error.** GitHub PR open→merge time is
dominated by **maintainer review-queue latency**: a trivial fix can wait weeks
for review while a hard fix is merged in hours by an active maintainer. This
wall-clock proxy measures *availability*, not *difficulty*, so it cannot carry
the difficulty signal the Jira in-development cycle time does. A faithful
replication needs issue-tracker status-transition cycle time — the **Apache Jira**
route (status-change parsing on a Java OSS project with good Jira hygiene) is the
heavier true-cycle-time path, named here as future work.

### 2b. Queue-independent effort signals (GitHub-native)

To separate change *difficulty* from review *latency*, we test signals that do
not depend on how long a maintainer took to look, attributed to the files each
fix PR changed and bucketed by health:

- **review_rounds** — number of submitted reviews (passes to get it right)
- **commits_after_first_review** — rework volume after first feedback
- **review_comment_density** — inline review comments per changed line
- **n_commits** / **commit_span_h** — branch iteration count / first→last commit span

A **negative** Spearman(health, signal) means unhealthy (low-score) files require
more effort to fix.

**Result (17 repos, 271 files with a linked fix PR):**

| Signal | Alert:Healthy (median) | Spearman(health, signal) [95% CI] |
|---|---|---|
| commit_span_h | 3.29× [0.05, 132.87] | +0.035 [−0.245, +0.267] |
| n_commits | 1.00× [0.46, 1.75] | +0.069 [−0.233, +0.323] |
| review_rounds | 1.00× [0.43, 2.83] | +0.020 [−0.273, +0.302] |
| changes_requested | — | −0.008 [−0.158, +0.155] |
| commits_after_first_review | — | +0.034 [−0.291, +0.324] |
| review_comment_density | — | −0.103 [−0.307, +0.067] |

**Also flat.** Every signal's correlation CI spans zero; none shows the expected
negative (harder-to-fix-in-unhealthy-code) relationship at corpus scale. An early
3-repo slice suggested review_rounds ρ ≈ −0.29, but that did not survive
expansion — it is small-sample noise, the same failure mode as the resolution-time
ratio. The reason these signals also fail is that they pick up **change size and
review culture** as much as difficulty: a large, well-reviewed feature PR in
*healthy* code racks up commits, rounds and comments, while a one-line fix in
*unhealthy* code is merged fast. Separating difficulty from size/culture is
exactly what an issue-tracker's in-development cycle time does and GitHub PR
metadata does not.

**Honest conclusion for Part 2.** On open GitHub data — wall-clock *or*
queue-independent — we cannot reproduce CodeScene's resolution-time business-impact
claim. This is a limitation of the open proxy, not evidence against the claim; the
proprietary-Jira replication (Apache projects with status-transition cycle time)
is the correct future-work path. Part 1's defect-prediction result does not depend
on this.

---

## Honest limitations

- **AUC edge is marginal.** The head-to-head DeLong p = 0.054 is just above
  0.05; the strong, significant wins are on Popt, recall, and size-normalized
  density (paired CIs exclude zero), not on raw AUC. "Significantly better" is
  scoped to those axes — verified with `codescene_paired_deltas.py`, not inferred
  from overlapping per-tool CIs.
- **One external scorer, its own metric.** The external Code-Health score is its
  own definition, not tuned to our labels (as ours is not tuned to theirs); a
  different external tool could land differently.
- **Defect labels are bug-fix commits, not Jira bugs.** Both tools are judged
  against the same keyword/SZZ labels used throughout `BENCHMARK_REPORT.md`,
  which carry the documented label noise; the comparison is internally
  consistent but not against a curated bug oracle.
- **Resolution-time proxy is wall-clock, not cycle time.** GitHub merge-time
  includes triage and review latency; it is an upper bound on in-development time
  and does not reproduce the proprietary-Jira finding. The true replication
  (Apache Jira status transitions) is future work.
- **Reproducibility.** `t0_date = 2025-11-23`; all analyses cache-based and
  seeded; the external tool's per-file scores are cached under
  `results/<repo>/codescene_scores.json` and resume across runs. Re-running
  Part 1 requires the external CLI + a free access token (neither committed);
  Parts 2a/2b require the authenticated `gh` CLI.
