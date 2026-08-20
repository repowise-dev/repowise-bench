# repowise-bench

The evidence behind every number [repowise](https://github.com/repowise-dev/repowise)
publishes, plus the harness to rerun it. Public repositories, pinned commits,
scripts included, and the rows we lose printed beside the rows we win.

**Arrived from
[docs/BENCHMARKS.md](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md)?**
That page is the summary: every headline with its sample size, its test and its
caveats. This repository is the layer underneath it, so start with
[graph/](graph/README.md) for call-graph correctness,
[head-to-head/](head-to-head/) for retrieval and the agent loop,
[health-defect/](health-defect/) for defect prediction, and
[repro/](repro/README.md) if you want to rerun a specific claim.

[![GitHub stars](https://img.shields.io/github/stars/repowise-dev/repowise?style=flat)](https://github.com/repowise-dev/repowise)
[![License](https://img.shields.io/github/license/repowise-dev/repowise)](https://github.com/repowise-dev/repowise/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/repowise-dev/repowise)](https://github.com/repowise-dev/repowise/releases)

---

## Four things this bench found out about its own numbers

Benchmarks in this category are usually written to win. This one keeps producing
results that its authors did not want, and those turn out to be the useful part.
If you read nothing else, read these.

**1. We ran the first head-to-head against the field and came last.** File
coverage 0.228 against CodeGraph's 0.609. We published that before we knew what
caused it. The cause was ours: a query-time gate discarded most candidates
before ranking ever ran. Fixed, `get_answer` reaches **0.876** on a half of the
corpus that was sealed before any of the work started, and the sealed half
scores **higher** than the half we developed against (0.810). Overfitting moves
that comparison the other way, which is the only reason the number is worth
anything. [Section 1](#1-head-to-head-against-the-agent-context-field).

**2. A competitor scored 0.012 MRR because of a regex in our code.** Graphify
writes its node lines as `NODE foo() [src=path loc=L149]`, and the path pattern
reading its output wanted whitespace before a path. Its true score was **0.539**,
a factor of 45 out. Nothing about the summary row looked wrong: a broken
extractor and a genuinely bad tool produce the same table. Every arm's path
extractor is now proved by hand against a captured response before a single cell
is graded, and the raw responses are kept on disk beside what came out of them.

**3. A tool that never called its server once came out 43% cheaper than a bare
agent.** code-review-graph, 0 calls across 15 questions, carrying 28,118 extra
characters of tool schema that should have made it cost *more*. The cause is
prompt caching: whichever arm runs first pays to warm the cache and every arm
after it reads it cheaply. Correlation between an arm's position in the cycle and
its dollar cost was **-0.487**. That result retired dollars-per-question as a
metric here. Output tokens correlate **+0.010** with position, so output tokens
are what gets published.

**4. Two clean zeros that were not measurements.** Grading a newer corpus, our
own arm was querying with **no embedder while every health field read clean**:
the key was missing, the server silently resolved a mock embedder, built
8-dimension question vectors against a 1536-dimension index, and swallowed the
failure on every query. Separately, one repository could not be checked out on
Windows at all, because a 260-character path limit killed the grading worktree,
and that scored **0/1 for five arms at once** rather than raising anything. Both
were caught by the same rule: grade a known-correct and a known-wrong prediction
before grading anything real.

The pattern behind all four is that **a wrong number in this field looks exactly
like a right one**. That is what the gates in
[head-to-head/THE_LOOP.md](head-to-head/THE_LOOP.md) are for, and every one of
them exists because something above got past its predecessor.

---

## What is measured, and against whom

| What | Against | Result | Where |
|---|---|---|---|
| Finding the files a fix touches | CodeGraph, Graphify, code-review-graph, cocoindex | **we win**, 0.876 vs 0.610, n=42 sealed, p=0.00004 | [§1](#1-head-to-head-against-the-agent-context-field) · [head-to-head/](head-to-head/README.md) |
| Work saved in a real agent loop | the same field plus Serena and a bare agent | **we win**, -31.6% output tokens, n=43, p<0.0001, on all three agent harnesses tried | [§2](#2-work-saved-in-a-real-agent-loop) |
| Does the health score predict real bugs | CodeScene | **we win** on recall, effort-aware ranking and defect density, p=0.003 | [§3](#3-does-the-health-score-predict-real-bugs) |
| Cross-function performance bugs | clippy, ruff PERF, ESLint, golangci-lint | **0 linter hits vs 557 findings** | [§4](#4-performance-bugs-a-file-local-linter-cannot-see) |
| Loading one commit's context | naive file reads, `git diff` | 393 tokens vs 13,984, **35.6x** pooled | [§5](#5-the-easy-number-loading-one-commits-context) |
| Indexing time | CodeGraph, Graphify, code-review-graph | **we lose**, about 22x slower | [§6](#6-indexing-time-the-row-we-lose) |
| Are the call graph's edges true | CodeGraph, codebase-memory-mcp | **we win** on precision against the Go compiler, most precise in all 5 cells | [§7](#7-is-the-call-graph-correct) · [graph/](graph/README.md) |
| Does the call graph find every edge | the same two | **we lose**, on both coverage and oracle recall | [§7](#7-is-the-call-graph-correct) |

Two things are deliberately absent. **Documentation generation** (DeepWiki,
Swimm) and **PR review** (CodeRabbit, Greptile) are capability comparisons we
have not measured, and we would rather write "not measured" than let a checkmark
do a number's job.

---

## 1. Head-to-head against the agent-context field

Before a tool can save an agent any work it has to point at the right code. This
measures only that, and **grading is deterministic**: ContextBench ships gold
file spans, a tool either returns them or it does not, and no LLM judge is
involved anywhere in the number.

112 instances of `django/django` and `cli/cli`, split **70 development / 42
sealed** by instance id and pinned before any work began. The sealed 42 are 30
Python and 12 Go, at 42 distinct base commits, and were evaluated once.

| Tool | File coverage | n | Precision | Files served |
|---|---:|---:|---:|---:|
| **repowise** (`get_answer`) | **0.876** | 42 | 0.087 | 19.2 |
| **repowise** (`search_codebase`) | **0.742** | 42 | **0.168** | **8.2** |
| CodeGraph | 0.610 | 42 | 0.093 | 14.0 |
| Graphify | 0.546 | 42 | 0.033 | 34.5 |
| code-review-graph | 0.445 | 42 | 0.240 | 5.4 |
| cocoindex | 0.361 | 41 | 0.092 | 7.1 |

cocoindex's row was measured later than the other five, on the same instances and
gold spans with the same deterministic grading, and its n is 41: one instance
served its tool and never answered, even queried alone, so it is named and
excluded rather than counted as a zero.

Per instance against CodeGraph: `get_answer` **19 wins, 1 loss, 22 ties, sign
test p = 0.00004**; `search_codebase` **13 wins, 3 losses, 26 ties, p = 0.021**.

**Those are two tools with two profiles and we do not average them into one
claim.** `get_answer` finds the most, out of about 19 files. `search_codebase`
finds fewer and is the most efficient per file served in the table: 0.742 from
8.2 files. If you pay by the token, that is the row to read.

**Precision is not our column.** code-review-graph's 0.240 is more than double
ours, and part of that is mechanical, because precision rises for whoever returns
fewest files. That is exactly why files-served is a column here and not a
footnote. Graphify serves 34.5 files per query to reach 0.546, which is the worst
of both.

Producing this cost **748 index builds and roughly 78 machine-hours**, because
every arm builds its own index of every instance's repository at that instance's
own `base_commit`. Nothing is shared between arms and nothing is cached across
instances: a stale checkout is a wrong answer, not a fast one.

**A JavaScript/TypeScript corpus is in flight** on `mui/material-ui`, six arms
including cocoindex, with the same 15 development / 30 sealed structure. Its
coverage row is **not published here**, because the sealed 30 have not been run
and publishing a development-half figure is precisely what that split exists to
prevent. What the run has already produced and what it cost, per arm, is in
[head-to-head/README.md](head-to-head/README.md).

**Depth:** [head-to-head/README.md](head-to-head/README.md) for who wins what and
one page per competitor · [head-to-head/THE_LOOP.md](head-to-head/THE_LOOP.md)
for the method and every gate · [`results/bakeoff_2026_08/rung8/`](results/bakeoff_2026_08/rung8/)
for raw cells · [repro/README.md](repro/README.md) for what each claim costs to
rerun.

---

## 2. Work saved in a real agent loop

Retrieval quality is not the product claim. This is: does an agent given the tool
finish the job having done less work?

48 questions on `django/django`, six arms, byte-identical prompt, each tool given
its **full advertised surface**, each with a freshly built index on the same
pinned commit. The bare-agent control was verified free of local hooks, so it is
a real control.

**The headline run is Codex, and that is a deliberate instrument choice.** Under
Codex (`gpt-5.6-sol`) every tool in the field gets called on every question, so
the comparison is between the tools. Under Claude Code most of them are barely
called at all, so a comparison there mostly measures bare agents against each
other. Claude Code is reported below as a secondary proof point, and its own
collapse is published as a finding rather than quietly dropped.

### Main run: 48 questions on Codex

| Tool | Agent used it | Output tokens | vs bare agent | Tool calls | Leaner on | p |
|---|---:|---:|---:|---:|---:|---:|
| **repowise** | **44 / 44** | **1,250** | **-31.6%** | **3.8** | **37 of 44** | **<0.0001** |
| CodeGraph | 44 / 44 | 1,383 | **-24.4%** | 4.0 | 37 of 44 | **<0.0001** |
| Serena | 43 / 43 | 1,550 | -14.8% | 10.1 | 35 of 43 | <0.0001 |
| Graphify | 43 / 43 | 1,658 | -8.9% | 7.4 | 31 of 43 | 0.003 |
| code-review-graph | 43 / 43 | 1,710 | -6.0% | 7.2 | 26 of 43 | 0.046 |
| *bare agent (control)* | 0 / 44 | 1,828 | baseline | 7.2 | n/a | n/a |

**CodeGraph is a genuine second.** The honest reading is that we lead a field in
which more than one tool works, not that we are the only one that does. Serena is
the interesting counter-case: it writes less than the bare agent while calling
tools 42% more often. Busier, not leaner.

**Where the saving is largest**, splitting the run at the median by how much work
the bare agent needed: the easier half saves 27.2%, the harder half **34.3%**,
correlation +0.379. Pre-computed structure replaces exploration, and harder
questions contain more exploration to replace.

**Three things this run does not say.**

- **It is not a quality result.** A blind judge scored every tool in the field,
  ours included, a fraction *below* the bare agent, in a range smaller than the
  0.69 points by which this benchmark moves when rerun unchanged. No tool here
  measurably changed answer quality in either direction.
- **Adoption is not a property of a tool.** Whether an agent calls a codebase
  server at all depends more on the harness than on the server. Rerunning the
  Claude Code half with nothing changed moved us from 15/15 to 4/15 to 3/15, and
  CodeGraph from 13/15 to 2/14. Every adoption figure needs its harness and its
  date attached.
- **No dollar figure.** See finding 3 at the top of this page.

### Secondary: the same setup on Claude Code

Held out as a second harness because switching to the one that flatters us is the
exact failure this repository exists to criticise. It produced two results, and
neither is a tool comparison.

**Sonnet.** repowise was the only tool to clear the bar on both harnesses
(-15.9%, 12 of 15, p = 0.035), but the useful column is adoption:
code-review-graph advertises 30 tools over a graph of 40,904 nodes and was called
**zero times in 15 questions**; Graphify three times. Nothing differed about the
servers, questions or indexes between the two harnesses. Claude Code loads MCP
schemas on demand, so the agent has to go looking before it can call anything,
and often never does.

**Opus**, run to separate harness from model, came back **inconclusive by its own
pre-registered rule**: 7 of 15 against bands of >=12 (model) and <=6 (harness),
fixed before any spend so a favourable 15 could not become a 48. Opus went
looking on 11 of 15 and declined about a third of the times it looked, so schema
deferral is part of the story and not all of it. Its token column **failed its
own control** (-9.3% when the tool was called against -10.7% when it was not), so
no token claim comes off that run for any tool, ours included.
[`results/bakeoff_2026_08/rung6/`](results/bakeoff_2026_08/rung6/).

### Third harness: a local 8B model, zero inference cost

`qwen3:8b` under Ollama, driven by opencode, on the same 15 django questions drawn
with the same seed. **This is the one row in this repository a third party can
reproduce with no account and no API key at all.**

| Row | Used it | Output tokens | vs bare | Leaner on | p | Wall clock | vs bare |
|---|---:|---:|---:|---:|---:|---:|---:|
| **repowise, full surface** | **15 / 15** | **1,319** | **-40.8%** | **15 of 15** | **0.00006** | **117s** | **-27.5%** |
| **repowise, local-only tools** | **15 / 15** | **1,172** | **-47.9%** | **15 of 15** | **0.00006** | **96s** | **-41.5%** |
| *bare agent (control)* | 0 / 15 | 2,336 | baseline | n/a | n/a | 171s | baseline |

**Two rows, never combined.** `get_answer` writes its answer with a hosted model, so
a row using it is not a local-only result. The second row switches it off and leaves
only tools that run against the local index. The restriction was verified rather than
assumed: instructed directly and repeatedly to call `get_answer`, that agent could
not reach it in any of its 15 cells.

**The mechanism inverts.** repowise roughly doubles the tokens fed in on a single
step while cutting steps from 3.3 to 2.1. Reading a large payload once is cheap on a
GPU; generating text token-by-token across several rounds is not. So a bigger payload
and a shorter loop is a straight win here, where on a hosted harness a big payload is
a cost.

**And this run is also where the quality column was declined, deliberately.** The full
surface scored +1.32 on a 0 to 10 judge scale, above the 0.69 noise floor, but the
win-loss count is 10 to 5 at p = 0.30 and dropping the single best question leaves
+0.99. The local-only row is **+0.20, a null**. Splitting the full-surface cells by
whether `get_answer` actually ran gives **+3.30 when called (n=4), +0.60 when not
(n=11), +0.20 pure-local (n=15)**: the gain is monotonic in how much a *hosted* model
did. A two-condition design would have published +1.32 as a local-model number, which
is exactly the shape of error this repository exists to catch.

---

## 3. Does the health score predict real bugs

Every file is scored at a historical commit (T0 = 2025-11-23) that **precedes** a
six-month bug-fix window, so no future information leaks into the score. The
score is then checked against which files actually received bug fixes.

- Across **21 repositories, 9 languages, 2,826 files**, cross-project mean ROC
  AUC **0.737** (95% CI 0.683 to 0.787). 0.5 is a coin flip.
- It **survives controlling for file size** (partial Spearman -0.156), so it is
  not "flag the big files".
- It out-discriminates raw churn by **+0.10 AUC** and a prior-defects baseline by
  **+0.117**, DeLong p < 1e-9.
- On the public **PROMISE/jEdit** defect dataset, which played no part in
  calibrating anything, the same biomarkers reach **0.76 to 0.78**, within about
  0.03 of that dataset's own tuned CK-metric model.

**Where it stops working, stated plainly.** Among files of *similar size* (within
an NLOC band) the signal is weak, AUC around **0.49**. A real part of the
headline is that larger files carry more risk. And a prior-defects baseline still
ranks bug-prone files more efficiently under a fixed review budget: it **beats
repowise on Popt by 0.085** even while losing on AUC.

**Against CodeScene**, on 2,770 files shared with it, scored at the same
leakage-free commit against the same labels (a distinct corpus from the 21-repo
study, so labelled separately):

| Axis | repowise | CodeScene | Δ paired | significance |
|---|---:|---:|---:|---|
| Recall @ 20% of lines | 0.173 | 0.074 | **+0.098** | p = 0.003 |
| Effort-aware ranking (Popt) | 0.607 | 0.462 | **+0.144** | p = 0.003 |
| Defect density (Alert:Healthy) | 2.18x | 0.56x | **+1.62** | p = 0.003 |
| Discrimination (ROC AUC) | 0.731 | 0.705 | +0.026 | p = 0.054, marginal |
| Precision @ 20% of lines | 0.580 | 0.636 | -0.056 | p = 0.64, a tie |

The AUC edge is marginal and not significant at 0.05. Precision is a tie, and
CodeScene's nominal lead there is an operating-point choice rather than a better
model: it flags about **27 files** to repowise's **132**. The open-data
business-impact replication that did **not** reproduce CodeScene's published
resolution-time correlation is in the comparison report.

**Depth:** [health-defect/BENCHMARK_REPORT.md](health-defect/BENCHMARK_REPORT.md)
· [health-defect/COMPARISON_REPORT.md](health-defect/COMPARISON_REPORT.md)

### Checking it on your own repository

You do not have to take a number from someone else's corpus. After indexing any
repo, repowise grades its own flags against that repo's git history:

```text
Does the score find the bugs? 16/20 lowest-health files had a bug fix in the
last 6 months, 3.3x the 24% baseline (80% vs 24%).
```

The same precision@K and lift statistic is available over MCP as
`get_health(include=["accuracy"])`, so an agent can check the score before it
trusts it.

---

## 4. Performance bugs a file-local linter cannot see

repowise follows the call graph across files to find wasted work (N+1 and
I/O-in-loop) hidden across function boundaries, which is a class a linter reading
one function at a time cannot reach by construction.

| | result |
|---|---|
| clippy, ruff PERF, ESLint, golangci-lint | **0** of the cross-function class |
| repowise across 12,000+ files | **557** findings, about 90 spanning functions |
| Hand-labelled precision | Go **96.7%**, TypeScript **100%**, Python **96.2%** |
| Runtime-confirmed fixes | **7 / 7** ran faster, 2.5x to ~2,500x, median ~50x |
| Ranking quality (NDCG) | **0.755** vs 0.292 for severity-only |

The honest caveat, which is in the report rather than hidden by it: the
clippy head-to-head on Rust was **not run end to end** because of a Windows build
wall.

**Depth:** [perf-detection/README.md](perf-detection/README.md) ·
[perf-detection/METHODOLOGY.md](perf-detection/METHODOLOGY.md)

---

## 5. The easy number: loading one commit's context

This is the one-payload measurement almost everybody in this category publishes.
It is real, it is easy, and it is not the same question as section 2. Measured on
the 30 most recent non-merge commits of `pallets/flask`, counted with
deterministic `tiktoken` (`cl100k_base`):

| Strategy | Tokens / commit |
|---|---:|
| naive (full contents of changed files) | 13,984 |
| `git diff` only | 1,408 |
| **`get_context`** | **393** |

**35.6x pooled**, 29.3x mean, 7.9x median, 133.8x best case. Lead with the pooled
figure: pooled is sum over sum, so it weights each commit by the tokens actually
at stake, where a mean of per-commit ratios lets a one-line commit returning 40
tokens count as much as one saving a hundred thousand. The
`--min-repowise-tokens 200` guard drops those degenerate rows and 30 of 30
commits passed it.

```bash
.venv/Scripts/python.exe harness/token_efficiency_bench.py \
    --repo repos/pallets/flask --last 30
```

Paired SWE-QA runs put the same effect in an agent loop: **-49% to -70% tool
calls** and **-69% to -89% fewer files read** at answer quality on par with raw
exploration, and `repowise distill` compresses noisy command output (test runs,
`git log`, `git diff`) by **61% to 89%** with errors preserved. Full tables in
[§7](#swe-qa-in-detail).

---

## 6. Indexing time, the row we lose

| tool | django, one index |
|---|---:|
| CodeGraph | 16.4s |
| **repowise**, prose off | **366.8s** |
| **repowise**, prose on | **1,058s** |

About **22x** slower than the fastest tool in the field, because the same pass
builds four more layers. We publish it with the work-done split rather than
without it, and the fitted cost curves across a 12x repository-size range for
five tools are in [head-to-head/README.md](head-to-head/README.md).

---

## 7. Is the call graph correct

Every benchmark above asks whether a tool helps. This one opens the index and
asks whether the edges inside it are true, which is a different question: a tool
can point an agent at the right neighbourhood while the arrows between the houses
are wrong.

[graph/](graph/README.md) answers it twice, by two methods, because each one
alone has a weakness the other covers.

**Against an oracle we do not control.** On Go the answer key is the Go team's
own RTA call graph from `golang.org/x/tools`, computed over the type-checked
program; on TypeScript it is the `tsc` checker's own resolution of every call
site. We cannot tune either, and anyone with the toolchain can regenerate both.
Five tools, seven cells, five repositories, **37,853 oracle edges**.

**The headline is a Pareto claim, and it is deliberately not "most precise".**

> In all seven cells, no tool that recovers as much of the call graph as we do
> gets more of it right.

Precision alone is exactly as gameable as coverage alone, in the opposite
direction, and both failure modes are in the data. One arm draws 12,533 edges on
syft and more than a third are calls the compiler says do not exist. Another
scores the highest precision anywhere in this repository, 0.997, from a graph
holding 17% of the calls in the repository; on gitleaks that figure rests on 76
resolved edges out of 4,367 stored rows. So the claim names no threshold, which
means it cannot be tuned by picking a cutoff and adding a competitor can only
break it. **Two arms were added after it was written and it held in all seven
cells.**

Read outright rather than as a pair, we are the most precise arm in **one cell of
seven**, tied in one, and **beaten in five**: by code-review-graph on cobra and
both syft cells, by Graphify on both gitleaks cells. Against the two arms this
experiment started with, CodeGraph and codebase-memory-mcp, we are most precise in
seven of seven, and that narrower claim always carries its label.

**Hand-graded across nine languages, both sides read.** 30 rows per language per
tool, seed 2026, stratified by resolution strategy, every row read from source:
**ours 229/270 = 84.8%** [80.0, 88.6] against **CodeGraph's 154/270 = 57.0%**
[51.1, 62.8], intervals disjoint. Four of the nine cells separate; five are ties
and are reported as ties. Read the other way round, roughly fifteen percent of our
call edges are wrong.

**And we lose the other half of the same question.** On cross-file coverage across
35 repositories, codebase-memory-mcp separates from us on 15 and we separate on
none. On oracle recall we lead the two TypeScript cells and **none of the five Go
cells**. It recovers more of the true call graph and invents far more that is not
in it, which is one trade seen from two directions. The reason both are always
printed is that coverage counts the files an edge reaches and never asks whether
the edge is real, so a tool that emits more edges wins it either way. No table in
[graph/](graph/README.md) prints a coverage number without a precision number
beside it.

One result there is worth more than any competitive row. **The oracle reproduced
our own hand-graded audit**: 30 rows read from source said 96.7% on Go for both
arms, and the compiler, over roughly 1,600 edges, says **97.6% and 97.2%**. A
person reading source and a type checker agreeing to within a point is the best
evidence available that the hand-grading is accurate rather than self-serving.

Limits: the oracle is two languages and stops at two. C#, Java, Kotlin and C++
each need a toolchain and a working build per repository; Rust has the toolchain
and no sound call-graph tool exists; Python, Ruby and PHP admit no oracle even in
principle, because what a call resolves to can change at runtime. On those
languages the hand-graded audit is the permanent method rather than a stopgap.
Full results, method and caveats in [graph/README.md](graph/README.md).

---

## How to read a number on this page

These rules are applied everywhere in this repository and each one exists because
breaking it produced a wrong published figure at least once.

- **Pre-register before spending.** The reading rule is committed as its own
  commit before a run starts, so a favourable result cannot become a different
  question afterwards.
- **Seal a half.** Every corpus is split by instance id before any work begins,
  and the sealed half is evaluated once, at publication.
- **Median beside mean**, because at small n a few items carry a mean.
- **Precision and files-served beside coverage**, never averaged into one figure.
- **Never a pooled percentage alone at small n.** It travels with the
  mean-of-per-item value, the median, and the largest single item's share of the
  total. Where pooled and mean-of-ratios disagree in sign, the number is an
  artifact and is reported as one.
- **An arm gets its full advertised tool surface**, chosen from its own
  documentation, with every exclusion named and justified in
  [`configs/arms.yaml`](configs/arms.yaml). We got this wrong once, in our own
  favour, and shipped it into a table.
- **Prove an arm was alive and its extractor works before recording a zero.**
- **Publish the losing rows.** Sections 3, 4 and 6 above each contain one.

---

## Reproduce it

Every benchmark ships its scripts and a fixed config.

```bash
# Agent-efficiency (SWE-QA), paired runs with and without repowise tools
python scripts/download_benchmarks.py --benchmark swe_qa
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/swe_qa_flask48.yaml
python analysis/aggregate_flask48.py

# Code health vs defects, leakage-free
cd health-defect && python run_benchmark.py     # see health-defect/README.md
```

[repro/README.md](repro/README.md) says, per published claim, what it costs to
reproduce, how long it takes, and which ones need credentials we cannot hand you.
Full prerequisites are in [§7](#swe-qa-in-detail) below.

---

## Repository layout

```
repowise-bench/
├── README.md                         this file: the index and the findings
├── CONTRIBUTING.md                   how to add an arm, dispute a number, or add a benchmark
├── requirements.txt                  shared Python dependencies
│
├── head-to-head/                     the 2026-08 bake-off against the field
│   ├── README.md                     who wins what, and the depth ladder
│   ├── THE_LOOP.md                   the method, and every gate with the finding behind it
│   └── arms/                         one page per competitor, setup traps included
│
├── health-defect/                    code-health vs defect-prediction benchmark
│   ├── README.md                     overview and reproduction steps
│   ├── BENCHMARK_REPORT.md           full statistics (21 repos, calibration, limits)
│   ├── COMPARISON_REPORT.md          named head-to-head vs CodeScene
│   ├── config.yaml                   per-repo configuration
│   ├── run_benchmark.py              entry point
│   └── lib/                          benchmark library modules
│
├── graph/                            call-graph correctness benchmark
│   ├── README.md                     results index: precision, coverage, cost
│   ├── METHODOLOGY.md                the measurement rules and why each exists
│   ├── arms/                         one page per tool, normalisation decisions
│   ├── corpus/                       35 repositories, pinned, 11 languages
│   ├── experiments/<id>/             preregistration, result page, run scripts
│   └── tools/                        table renderers; no table here is typed
│
├── perf-detection/                   performance-bug detection benchmark
│   ├── README.md                     overview (0 linter hits vs 557 findings)
│   ├── METHODOLOGY.md                experiments E1 to E5
│   └── benchmarks/                   raw runtime-confirmation results
│
├── configs/
│   ├── arms.yaml                     THE ARM REGISTRY. Adding a competitor is a YAML block
│   ├── *.PREREGISTRATION.md          one per scored run, committed before any spend
│   └── swe_qa_flask48.yaml           canonical SWE-QA configuration
│
├── harness/                          shared runner infrastructure
│   ├── run_experiment.py             entry point: orchestrates a paired run
│   ├── arms.py                       arm resolution, MCP config generation, isolation
│   ├── swe_qa_runner.py              per-task runner plus LLM-as-judge
│   ├── metrics.py                    RunMetrics, stream parser, BudgetTracker
│   ├── token_efficiency_bench.py     token-efficiency mini-benchmark
│   └── refactoringminer.py           external type-level refactoring oracle
│
├── results/bakeoff_2026_08/          every graded cell behind the head-to-head
├── repro/README.md                   per-claim cost and time to reproduce
├── data/                             static benchmark datasets
├── analysis/                         aggregation scripts
├── scripts/                          staging, prebuild and download utilities
│
├── BENCHMARK_REPORT_FLASK48.md       SWE-QA full report: Flask
├── BENCHMARK_REPORT_SKLEARN48.md     SWE-QA full report: scikit-learn
├── BENCHMARK_REPORT_FLASK48_V2.md    interim 24/48 run (superseded, internal)
├── BENCHMARK_REPORT_FLASK_V3.md      lean MCP surface plus distill
│
├── indexes/                          generated documentation indexes (gitignored)
├── repos/                            cloned target repositories (gitignored)
└── logs/                             per-run logs (gitignored)
```

Two earlier SWE-QA runs are kept and labelled as interim rather than deleted:
[flask48 v2](BENCHMARK_REPORT_FLASK48_V2.md), a 24/48 run that first surfaced the
cost-caching effect, superseded by [flask v3](BENCHMARK_REPORT_FLASK_V3.md).

---

## Contributing

**This benchmark is meant to be argued with**, and the most valuable contribution is
a number of ours that turns out to be wrong. You do not need to run anything;
reading and disagreeing counts.

- **Your tool is in the field and its arm is set up wrong?** That is the fix we most
  want, and it needs no Python: a YAML block dropped in
  [`configs/arms.d/`](configs/arms.d/README.md), which merges over the tracked
  registry. Four arms here have scored a clean 0.000 purely because we guessed one
  of their setup steps wrong.
- **Think one of our numbers is wrong?** Open an issue. Every verbatim response is on
  disk beside what the extractor pulled out of it, so this is checkable rather than a
  matter of trust.
- **Want to add a whole benchmark?** One directory, one `README.md`, one
  `run_benchmark.py`, one row in the table at the top of this file.

Full guide, including the eight levels of depth to read at and the rules a
contribution has to meet: **[CONTRIBUTING.md](CONTRIBUTING.md)**.

---

## SWE-QA in detail

A paired benchmark comparing two coding-agent configurations on SWE-QA tasks
drawn from [`pallets/flask`](https://github.com/pallets/flask) and
[`scikit-learn/scikit-learn`](https://github.com/scikit-learn/scikit-learn). Both
arms use the same model (`claude-sonnet-4-6`), the same prompt scaffolding, the
same per-task budget cap and the same LLM judge. The only variable is the tool
surface.

| Configuration | Tools available to the agent |
|---------------|------------------------------|
| **C0_bare** | `Read`, `Grep`, `Glob`, `Bash`, `Agent` |
| **C2_full** | All of the above plus four MCP tools (`get_answer`, `get_symbol`, `get_context`, `search_codebase`) backed by a precomputed index |

### flask48: `pallets/flask`, 48 paired tasks

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Tool calls (mean) | 7.4 | 3.8 | **-49.2%** |
| Files read (mean) | 1.9 | 0.2 | **-89.0%** |
| Wall / task (mean) | 41.7s | 33.9s | **-18.6%** |
| Score (0-10, mean) | 8.82 | 8.81 | tied |

### sklearn48: `scikit-learn/scikit-learn`, 48 paired tasks

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Tool calls (mean) | 8.1 | 2.4 | **-70.5%** |
| Files read (mean) | 1.8 | 0.6 | **-69.3%** |
| Wall / task (mean) | 39.7s | 28.6s | **-27.9%** |
| Score (0-10, mean) | 8.72 | 8.23 | similar on this sample |

### Method

**Pairing.** Every task runs under both conditions and every metric is computed
per task before aggregation. A C0 mean is never compared against a C2 mean drawn
from a different subset. If a task fails under one condition it is rerun under
both and the new pair replaces the old one in full.

**Cost accounting.** Cost is read from each task's `estimated_cost_usd`,
populated from the runtime's per-model billing roll-up, so it sums across every
model invoked including subagents. Token-based recomputation is deliberately
avoided because it misses subagent spend absent from the parent stream's `usage`
blocks.

**Judge.** Each (task, configuration) pair is scored by an LLM judge on a fixed
five-dimension rubric (correctness, completeness, relevance, clarity, reasoning),
0 to 10. The judge does not see the configuration label and is the same model in
both arms.

**Reproducibility.** Deterministic up to LLM nondeterminism. Model versions,
prompt templates and the task corpus are pinned here; the external dependencies
are the repository checkouts (pinned by commit hash in the index metadata) and
the Anthropic API.

### Reproduction

About 30 minutes of wall clock per arm, roughly $5 to $10 per arm at list prices.

Prerequisites: **Python 3.11+**; **Claude Code CLI** authenticated (OAuth or
`ANTHROPIC_API_KEY`); **repowise CLI** on `$PATH` or a sibling checkout; ~5 GB
free disk.

```bash
pip install -r requirements.txt
python scripts/download_benchmarks.py --benchmark swe_qa
repowise init repos/pallets/flask --output-dir indexes   # optional, built on demand
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/swe_qa_flask48.yaml
python analysis/aggregate_flask48.py
```

Results are written incrementally to `results/swe_qa_flask48/swe_qa.jsonl` and
the run is safe to interrupt and resume. For health-defect reproduction see
[health-defect/README.md](health-defect/README.md).

### Output schema

Each row of `results/swe_qa_flask48/swe_qa.jsonl`:

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Unique task identifier (e.g. `flask_017`) |
| `benchmark` | string | Always `swe_qa` |
| `condition` | string | `C0_bare` or `C2_full` |
| `repo` | string | Source repository |
| `question_type` | string | SWE-QA category (What / Where / How / Why) |
| `answer` | string | The agent's final answer |
| `judge_scores` | dict[str,float] | Judge dimension scores in [0, 10] |
| `estimated_cost_usd` | float | Total cost across all models invoked |
| `wall_clock_seconds` | float | End-to-end duration |
| `num_tool_calls` | int | Total tool invocations |
| `files_explored` | list[str] | Distinct file paths opened via `Read` |

---

## Validation tooling

### RefactoringMiner oracle (`harness/refactoringminer.py`)

An external, type-level check on repowise's refactoring code generation. The
product generates a diff from a deterministic plan and self-checks it in-process
with an LCOM4/TCC cohesion delta, which is a *metric* answer ("did cohesion
improve?"). This oracle adds the *type* answer:
[RefactoringMiner](https://github.com/tsantalis/RefactoringMiner) (MIT) detects
which refactoring kinds occur between two commits, confirming a generated change
is genuinely an "Extract Class" or "Move Method" rather than merely a
cohesion-friendly edit.

It is Java-only and commit-based, so it lives in the harness rather than the
product. Apply a generated refactoring as a commit on a Java test repo, then:

```bash
# Gated on the jar; skips cleanly when REFACTORINGMINER_JAR is unset.
REFACTORINGMINER_JAR=/path/to/RefactoringMiner.jar \
  python -m harness.refactoringminer \
    --repo /path/to/java-repo --commit <sha> --type extract_class \
    --before-file src/Big.java --after-file src/Big.java

# Validate the JSON parser without Java present:
python -m harness.refactoringminer --self-test
```

The verdict pairs the type confirmation with a TCC before/after delta computed by
reusing repowise core's class walker (`walk_file(...).classes[*].tcc`), the same
metric the in-process self-check reports. No new Python dependencies.

---

## Citation

```
Repowise health-defect Benchmark: Code Health Scores as Defect Predictors,
21 repositories across 9 languages. 2026.
```

```
Repowise on SWE-QA: A Benchmark Study of Documentation-Augmented Code
Question Answering on Flask and scikit-learn. 2026.
```

## License

This benchmark harness is released under the Apache 2.0 license. The repository
checkouts used as targets are owned by their respective projects and licensed
separately. The SWE-QA task corpus is the property of its original authors.
