# repowise-bench

The evidence behind every number [repowise](https://github.com/repowise-dev/repowise)
publishes, plus the harness to rerun it. Public repositories, pinned commits,
scripts included, and the rows we lose printed beside the rows we win.

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
| Work saved in a real agent loop | the same field plus Serena and a bare agent | **we win**, -31.6% output tokens, n=43, p<0.0001 | [§2](#2-work-saved-in-a-real-agent-loop) |
| Does the health score predict real bugs | CodeScene | **we win** on recall, effort-aware ranking and defect density, p=0.003 | [§3](#3-does-the-health-score-predict-real-bugs) |
| Cross-function performance bugs | clippy, ruff PERF, ESLint, golangci-lint | **0 linter hits vs 557 findings** | [§4](#4-performance-bugs-a-file-local-linter-cannot-see) |
| Loading one commit's context | naive file reads, `git diff` | 393 tokens vs 13,984, **35.6x** pooled | [§5](#5-the-easy-number-loading-one-commits-context) |
| Indexing time | CodeGraph, Graphify, code-review-graph | **we lose**, about 22x slower | [§6](#6-indexing-time-the-row-we-lose) |

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

112 instances of `django/django`, split **70 development / 42 sealed** by
instance id and pinned before any work began. The 42 were evaluated once.

| Tool | File coverage | n | Precision | Files served |
|---|---:|---:|---:|---:|
| **repowise** (`get_answer`) | **0.876** | 42 | 0.087 | 19.2 |
| **repowise** (`search_codebase`) | **0.742** | 42 | **0.168** | **8.2** |
| CodeGraph | 0.610 | 42 | 0.093 | 14.0 |
| Graphify | 0.546 | 42 | 0.033 | 34.5 |
| code-review-graph | 0.445 | 42 | 0.240 | 5.4 |

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

## Adding a benchmark

Each benchmark gets its own directory:

1. **Create** `repowise-bench/<benchmark-name>/`
2. **Add a `README.md`** with methodology, headline numbers and reproduction steps
3. **Add a `run_benchmark.py`** runnable from within the directory
4. **Write results to `../results/<benchmark_name>_{variant}/`**
5. **Add a row** to the table at the top of this file

Shared repos and indexes are reusable from `../repos/` and `../indexes/`. New
Python dependencies go in the top-level `requirements.txt`.

**Adding a competing tool** is different and cheaper: it is a YAML block in
[`configs/arms.yaml`](configs/arms.yaml), no Python and no runner change. Drop
files into `configs/arms.d/*.yaml` and they merge over it, so a third party can
add an arm without editing a tracked file.
[head-to-head/THE_LOOP.md](head-to-head/THE_LOOP.md) says what a fair arm
definition has to contain.

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
