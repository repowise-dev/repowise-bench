# CockroachDB head-to-head: Repowise vs. CodeScene

*A like-for-like comparison of two automated code-health scorers on one very large
enterprise codebase, measured so that neither tool could "see" the bugs it was
later judged against.*

---

## Overview

Both Repowise and CodeScene assign every source file a "code health" score that is
meant to predict where bugs will appear. To test that honestly, we took
**CockroachDB** (a large open-source distributed SQL database written in Go, the
kind of big enterprise codebase CodeScene is built for), rewound it to a commit
from **late November 2025**, and scored every file *as the code looked then*. We
then looked at which files actually received bug-fixes over the **following ~6.5
months** and asked: did the early health scores point at the files that later broke?
We ran **both tools on exactly the same files, at the same point in history,
against the same list of real bug-fixes**, and scored them with identical math. The
result on this one repo: the two tools are **roughly tied** on raw accuracy,
Repowise is **slightly better at the "review-efficiency" metrics** (catching more
bugs per unit of reviewer effort), and CodeScene is **more conservative** (it flags
far fewer files, so the few it flags are purer but it misses more). This is **one
repository**, so it is a *demonstration*, not a statistical proof; the proof is the
21-repository study this accompanies.

A glossary defining every technical term is at the **end** of this document. Terms
are also explained inline the first time they appear.

---

## 1. What we measured and why it is trustworthy

The central risk in any "our score predicts bugs" claim is **leakage**: if you score
the code *and* count its bugs from the same snapshot, the score has effectively
already seen the answer, and every number is inflated. We avoid this by design.

- **T0 ("time zero").** We pick a commit in the past, `T0 = 2025-11-22`, and check
  the repository out exactly as it stood at that commit (a detached *worktree*, so
  the live repo is untouched). All health scoring happens on this T0 snapshot.
- **The bugs come strictly afterward.** A file is labeled "defective" only if a
  **bug-fix commit landed on it in the window *after* T0** (from T0 up to the
  repository's current HEAD). Because the score is fixed at T0 and the bugs are
  discovered later, **the score cannot have used future information.** This is
  called *leakage-free forward prediction*, and it is the property that makes a
  defect-prediction result trustworthy.
- **Time-window signals are anchored correctly.** Some health signals look at
  recent activity ("changed in the last 90 days," "how many authors touched this").
  On a 6-month-old snapshot, a naive "last 90 days" would be empty. We anchor those
  windows to the snapshot's own HEAD so they behave as they did at T0.

Everything below is computed on cached, deterministic artifacts (committed under
`results/health_defect_cockroach/`), so the same inputs always produce the same
numbers.

---

## 2. The codebase and the test set

| Item | Value | What it means |
|---|---|---|
| Repository | `cockroachdb/cockroach` | Open-source distributed SQL database, written in Go; a genuinely large enterprise codebase. |
| Git history | 118,945 commits (full clone) | Not a shallow/partial clone, so the bug-history and authorship signals are complete. |
| **T0 commit** | `47a00e3e…` (2025-11-22) | The single past commit everything is scored at. |
| Source files indexed | 7,784 (10,289 total files walked) | Go source files; the rest are configs/docs/scripts. |
| Knowledge-graph nodes | 27,207 | The dependency/structure graph Repowise builds for the repo. |
| **Files in the scored test set** | **5,373** | Files kept after restricting to the Go source tree (`pkg/`), removing machine-generated and vendored code, dropping files under 10 lines, and excluding test files from the labeled set. |
| **Files that later got bug-fixes** | **352 (6.5%)** | The "positives" the scores are judged against. 475 separate bug-fix touches in total. |

**Why we exclude things.** A health score is only meaningful on **human-written
source**. CockroachDB contains large amounts of **machine-generated Go** (Protocol
Buffer code `*.pb.go`, the `execgen`/`optgen` code generators `*.eg.go`/`*.og.go`,
the `stringer` files `*_string.go`), a vendored copy of third-party dependencies,
and a TypeScript web console. Scoring those would measure code no human maintains.
We exclude them up front. **Sanity check (passed):** after exclusions, the 60
worst-scoring files are all genuine, hand-written CockroachDB source (its
storage/replication/SQL-execution layers); **zero generated files leaked into the
"worst" list**, including the notorious 8,000+ line generated SQL parser. So the
comparison is on real engineering code, not auto-generated noise.

---

## 3. How a file is labeled "defective"

A score can only be judged against a definition of "bug." We use the
software-engineering-standard approach and, importantly, we test that the verdict
does not depend on which definition we pick.

- **Keyword label (primary).** A file is defective if a commit whose message marks
  it as a fix (`fix`, `bug`, `patch`, `resolve`; documentation/formatting/chore
  commits excluded) modified it in the post-T0 window. This is the headline label.
- **SZZ label (robustness check).** *SZZ* is the academic-standard algorithm that,
  for each bug-fix, uses `git blame` to trace the fixed lines back to the commit
  that originally **introduced** the bug, and attributes the defect to the file that
  *contained* the bug at T0. This is stricter (it drops fixes whose buggy lines were
  written after T0). We run two SZZ variants (AG-SZZ and B-SZZ).

If the two very different labeling methods give the same verdict, the result is not
an artifact of how we defined "bug." (They do — see §6.)

---

## 4. The head-to-head result

Both tools scored the **same 5,373 files at the same T0 commit**. CodeScene could
not score 678 of them (explained in §5); the comparison therefore runs on the
**4,695 files both tools scored** (the *paired intersection*, 99.7% of it Go source,
347 of them defective). Every metric is computed with the **identical** code for
both tools, so this measures the two *scorers*, not two different setups.

| Metric (plain-English meaning) | Repowise | CodeScene | Who leads |
|---|--:|--:|---|
| **ROC AUC** — chance a random buggy file scores worse than a random clean one (0.5 = coin-flip, 1.0 = perfect) | **0.761** [0.736, 0.787] | 0.754 [0.725, 0.783] | tie |
| **Popt** — bugs caught per unit of *reviewer effort* when you read files worst-first on a line-of-code budget (0.5 = random order, 1.0 = optimal) | **0.541** [0.509, 0.575] | 0.525 [0.490, 0.560] | Repowise (slight) |
| **Recall @ 20% LOC** — of all real bugs, the fraction you catch by reviewing the 20%-of-code the tool flags riskiest | **0.213** [0.179, 0.253] | 0.176 [0.141, 0.210] | Repowise (slight) |
| **Precision @ 20% LOC** — of the files inside that 20% budget, the fraction that actually had bugs | 0.273 [0.222, 0.336] | **0.565** [0.460, 0.660] | CodeScene |
| **Partial correlation vs. size** — does the score still predict bugs *after* removing the effect of file size? (negative = healthier code, fewer bugs) | −0.066 | −0.055 | both beat size |
| **Defect density per 1,000 lines** — how concentrated bugs are in "red" vs "green" files, *adjusted for file size* | **1.30×** | 1.19× | Repowise |
| **Files flagged "alert" / "healthy"** | 442 / 3,058 | 76 / 3,963 | (different operating points) |

The square-bracket ranges are **95% confidence intervals** — the band the true value
most likely sits in. They overlap heavily here, which is exactly what one expects
from a single repository (see §7 on why one repo means wide uncertainty).

### What the table says, in words

- **Raw accuracy (ROC AUC): a tie.** Both tools land at ~0.76, meaning each one,
  given a buggy file and a clean file at random, ranks the buggy one as worse about
  76% of the time. That is a useful triage signal (well above the 0.5 coin-flip),
  and the two are statistically indistinguishable here (formal test in §7).

- **Review efficiency (Popt, recall): Repowise ahead.** These are the metrics that
  matter operationally: *given a fixed amount of reviewer time, how many real bugs do
  you find?* Reviewing the riskiest 20% of the code (by line count) in Repowise's
  ranking catches **21.3%** of all bugs versus CodeScene's **17.6%** — about a fifth
  more bugs for the same review budget — and Repowise's effort-ranking (Popt) is
  higher too. The margins on this single repo are modest; the same advantage appears
  at a larger, statistically significant scale across the multi-repo study.

- **Precision at a small budget: CodeScene's conservative operating point.**
  CodeScene flags only **76 files** as "alert" (unhealthy) versus Repowise's **442**.
  A short, selective red list is naturally *purer*, so within the top-20% budget a
  higher fraction of CodeScene's flagged files really are buggy (56.5% vs 27.3%) —
  but the trade-off is that it catches fewer of the total bugs (the lower recall
  above). The two tools simply sit at different points on the same precision-vs-recall
  curve.

- **Not just "big files are buggy."** Large files contain more bugs in essentially
  all software, so any score that correlates with size will look good. The **partial
  correlation** removes the size effect; it stays negative for **both** tools
  (−0.066, −0.055), so neither score is merely a proxy for file size. And the
  **size-normalized density** (bugs per 1,000 lines in red vs green files) favors
  Repowise (1.30× vs 1.19×) — its "red" flags mark genuinely bug-dense code, not just
  large code.

### Verdict for this repo

**At enterprise scale the two tools are neck-and-neck, with Repowise ahead on the
review-efficiency metrics** (more bugs caught per unit of review effort) and
CodeScene tuned to a more conservative, higher-precision operating point. This
shows Repowise holds its own on a large enterprise codebase, the kind CodeScene is
built for. The decisive, statistically significant version of the review-efficiency
advantage is established across many repositories in the companion study.

> **Note: two Repowise AUC figures appear in this document.** §4
> reports Repowise AUC **0.761** on the 4,695-file *paired* set (the files CodeScene
> could also score); §6 reports **0.784** on the full 5,373-file set. The difference
> is expected: the paired set drops the 678 files CodeScene declined, most of which
> are easy "clean" negatives, and removing easy negatives slightly lowers AUC. Both
> are correct; they are simply measured on different file sets. The head-to-head
> uses the paired set so both tools see an identical universe.

---

## 5. CodeScene's unscored files

CodeScene returned "no scorable code" for 678 of the 5,373 files, which at face
value looks like a 12.6% coverage gap. It is not: **546 of those are non-Go files**
(Protocol-Buffer schemas, shell scripts, JSON, YAML, Markdown, SQL) that entered the
set only because Repowise scores every file it walks, and that CodeScene reasonably
does not assign a code-health score to. The remaining **132 are tiny
declaration-only Go files** (constants, type definitions, test helpers) with no
functions for a code-health metric to act on. CodeScene's decline rate on actual Go
source is therefore **2.7%**, consistent with the 2.2% it shows across the 21-repo
study. The paired comparison drops these files from **both** tools so the universe
is identical.

---

## 6. Repowise standing on the full file set, and robustness

These numbers are Repowise-only, on the complete 5,373-file set, and exist to place
the head-to-head in context.

### Beating the "free" baselines

A predictor is only worth shipping if it beats the cheap things you could do for
free. We compare Repowise health against ranking files by: their size (`loc_only`),
how much they changed recently (`churn_only`), how many bugs they had before T0
(`prior_defects`), and random order.

| Predictor | ROC AUC | Popt | Reading |
|---|--:|--:|---|
| **Repowise health** | **0.784** | **0.598** | — |
| File size only | 0.786 | 0.470 | Ties health on raw accuracy, but **health beats it by +0.128 on effort-aware Popt** — i.e. health is *not* just a size proxy. |
| Recent churn | 0.721 | 0.627 | Health out-discriminates it by +0.063 AUC. |
| Prior bug history | 0.604 | 0.606 | Health out-discriminates it by +0.180 AUC. |
| Random | 0.498 | 0.502 | Sanity floor. |

One caveat: for pure *inspection ordering on a budget* (Popt), the cheap
"re-check what changed / what broke before" heuristics remain competitive — a
well-known result. Repowise's edge is in **discrimination plus explainability** (a
calibrated, attributable structural signal), not in replacing process history for
triage ordering.

### The verdict does not depend on the bug definition

| Bug-labeling method | Defective files | ROC AUC | Popt |
|---|--:|--:|--:|
| Keyword (primary) | 352 | 0.784 | 0.598 |
| AG-SZZ (bug-inducing-commit) | 242 | 0.790 | 0.600 |
| B-SZZ | 255 | 0.788 | 0.596 |
| Issue-linked | 0 | n/a | n/a |

AUC moves by at most 0.006 between "where fixes landed" (keyword) and "where bugs
originated" (SZZ) — the result is not an artifact of one labeling choice. The
issue-linked label is empty because CockroachDB references **pull-request numbers**
(`(#12345)`) in fix commits rather than linking to triaged bug issues — an
issue-tracking convention, not a measurement fault.

---

## 7. How confident should you be?

**This is one repository, so it is a demonstration, not a statistical proof.**

- **Confidence intervals are wide on one repo.** A *95% confidence interval* is the
  range the true value most likely occupies. With a single repository the intervals
  for the two tools overlap heavily, so no single metric here should be read as a
  decisive win or loss.
- **The one valid significance test agrees it is a tie.** The *DeLong test* is the
  standard statistical test for whether two ROC AUC scores genuinely differ. Here:
  **ΔAUC = +0.008 in Repowise's favor, p = 0.33** — a p-value of 0.33 means a gap
  this small would arise by chance about a third of the time, i.e. **not
  statistically significant**. The tools are tied on discrimination.
- **The other metrics are reported as point estimates, not significance-tested
  here.** On a **single** repository a paired significance test is mathematically
  degenerate (only one repository to resample, so it returns a zero-width interval
  and `p = 0.000`) — a known artifact of having one repo, not real significance.
  Genuine multi-metric significance requires resampling across **many** repositories,
  which is what the companion 21-repo study (`COMPARISON_REPORT.md`) provides, and
  where Repowise's review-efficiency wins are large and significant.

In short: this document is an enterprise-scale demonstration; statistical
significance is established in the 21-repo study.

---

## 8. Independent fairness check — CodeScene's own published number reproduces

As an external check that the comparison is fair, we verified CodeScene's *own*
headline statistic on our data. CodeScene publishes that its "red" (unhealthy) files
carry roughly **14.8×** the defect concentration of healthy files. On CockroachDB,
its red-vs-healthy concentration came out at **~20.7×** — the same order of magnitude
— confirming the CodeScene CLI ran correctly and behaved as advertised on this
corpus.

---

## 9. Limitations

- **One repository.** Wide uncertainty; this is a demonstration of enterprise-scale
  parity, not a significance result.
- **File size is the dominant signal in this whole field** — for every tool,
  including CodeScene. Both scores add real signal beyond size (the partial
  correlations exclude zero), but neither is size-independent.
- **Different operating points, not a single "winner."** Repowise flags more files
  (higher recall, lower precision); CodeScene flags fewer (higher precision, lower
  recall). Which is preferable depends on whether a team wants broad coverage or a
  short, high-purity list.
- **Business-impact axis is not addressed here.** CodeScene's published claim that
  unhealthy code costs more *time* to change comes from proprietary issue-tracker
  cycle-time data. That axis is genuinely CodeScene's and is not part of this
  defect-prediction comparison.

---

## 10. Reproduce it yourself

All steps are deterministic and run from the committed result cache. From the
`health-defect/` directory of this repository, using the project's Python
environment:

```bash
# 1. Score CockroachDB at T0 with Repowise, and compute the defect labels.
python run_benchmark.py --repo cockroach --score-at t0 --label-strategy keyword

# 2. Score the same files at the same commit with the CodeScene CLI.
#    CS_BIN  = path to your CodeScene CLI binary (v1.0.29 was used here)
#    CS_ACCESS_TOKEN = a free codescene.io personal access token
export CS_BIN=<path-to-codescene-cli>
export CS_ACCESS_TOKEN=<your-codescene-token>
python codescene_headtohead.py  --repos cockroach --label keyword
python codescene_paired_deltas.py --repos cockroach --label keyword
```

Outputs (committed):
- `results/COCKROACH_HEADTOHEAD.md` — this report.
- `results/health_defect_cockroach/` — every backing artifact: per-file health
  scores, the defect labels (keyword + SZZ variants), the joined dataset, the
  per-tool comparison JSON, and charts.

Methodology and the full multi-repo study: `health-defect/BENCHMARK_REPORT.md`
(the 21-repo / 9-language defect-prediction study) and `COMPARISON_REPORT.md`
(the multi-repo paired CodeScene head-to-head, where significance is established).

---

## Glossary

- **T0** — the past commit (2025-11-22) at which all code-health scoring is done.
  Bugs are counted only *after* T0, so scores cannot have seen them.
- **Leakage-free** — measuring the score strictly *before* the bugs it is judged
  against, so no future information contaminates the prediction.
- **Defect / positive** — a source file that received a bug-fix in the window after
  T0.
- **Keyword label** — defining "buggy" as "a commit marked as a fix touched this
  file." **SZZ label** — the academic method that traces a fix back to the commit
  that introduced the bug and blames that file. Used as a robustness cross-check.
- **ROC AUC** — probability the score ranks a random buggy file as worse than a
  random clean file. 0.5 = no better than a coin flip; 1.0 = perfect separation;
  ~0.76 = a useful triage signal.
- **Popt (effort-aware)** — how close to the best-possible bug-catching you get by
  reviewing files worst-first under a budget measured in lines of code. Penalizes
  "just flag the big files." 0.5 = random ordering, 1.0 = optimal.
- **Recall @ 20% LOC** — fraction of *all* real bugs caught by reviewing the riskiest
  20% of the codebase (by lines of code).
- **Precision @ 20% LOC** — fraction of the files inside that 20% budget that
  actually turned out to be buggy.
- **NLOC / LOC / KLOC** — (net) lines of code / lines of code / thousands of lines.
- **Partial correlation vs. NLOC** — the score-to-bug relationship after
  statistically removing file size, proving the score is more than a size proxy.
- **Defect density (per KLOC)** — how concentrated bugs are in flagged ("red") vs
  unflagged ("green") files, per 1,000 lines of code so it adjusts for file size.
- **Alert / Healthy** — a tool's "red" (unhealthy) vs "green" (healthy) buckets.
- **Operating point** — how aggressively a tool flags files; flagging fewer raises
  precision but lowers recall, and vice-versa.
- **95% confidence interval (CI)** — the range the true value most likely falls in;
  wider means more uncertainty (e.g. from having only one repository).
- **DeLong test / p-value** — the standard test for whether two ROC AUCs differ; a
  p-value of 0.33 means the difference is well within chance (not significant).
- **Cluster bootstrap** — estimating uncertainty by resampling whole repositories;
  it needs many repositories to be meaningful (hence one repo cannot prove
  significance).
- **Baseline** — a cheap predictor (file size, recent churn, prior bugs, random) a
  useful score must beat.
- **Biomarker** — an individual code-health signal (e.g. complexity, low cohesion,
  change coupling) that contributes to the overall score.
- **Generated / vendored code** — machine-written code (Protocol Buffers, code
  generators, `stringer`) and copied third-party dependencies; excluded so the score
  measures human-maintained source.
