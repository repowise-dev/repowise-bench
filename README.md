# repowise-bench — Benchmark Suite

> **[Repowise](https://github.com/repowise-dev/repowise)** is the codebase
> intelligence layer for AI coding agents. It indexes repositories into five
> intelligence layers — dependency graphs, git analytics, auto-generated docs,
> architectural decisions, and code health scores — and exposes them through
> nine MCP tools. The result: fewer tool calls, fewer file reads, lower LLM
> costs, and health scores that predict real-world defects.
>
> **This repo proves those claims with reproducible benchmarks on public
> codebases.**

[![GitHub stars](https://img.shields.io/github/stars/repowise-dev/repowise?style=flat)](https://github.com/repowise-dev/repowise)
[![License](https://img.shields.io/github/license/repowise-dev/repowise)](https://github.com/repowise-dev/repowise/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/repowise-dev/repowise)](https://github.com/repowise-dev/repowise/releases)

---

## Benchmarks

| Benchmark | Status | Headline | Report |
|-----------|--------|----------|--------|
| [**SWE-QA**](#swe-qa-coding-agent-efficiency) | v1 superseded by v2/v3 | -32-70% tool calls, -54-89% file reads, quality at parity. Cost is configuration-dependent and the v1 flask cost figure did **not** reproduce | [flask48 v1](BENCHMARK_REPORT_FLASK48.md) · [flask48 **v2 rerun**](BENCHMARK_REPORT_FLASK48_V2.md) · [flask **v3**](BENCHMARK_REPORT_FLASK_V3.md) · [sklearn48](BENCHMARK_REPORT_SKLEARN48.md) |
| [**health-defect**](#health-defect-code-health-vs-defect-prediction) | Complete | ROC AUC 0.737 [0.683, 0.787] cross-project, 21 repos / 9 languages, leakage-free T0 | [README](health-defect/README.md) · [full report](health-defect/BENCHMARK_REPORT.md) |

---

## SWE-QA — Coding Agent Efficiency

A paired benchmark comparing two coding-agent configurations on
[SWE-QA](https://arxiv.org/abs/2401.00000) tasks drawn from
[`pallets/flask`](https://github.com/pallets/flask) and
[`scikit-learn/scikit-learn`](https://github.com/scikit-learn/scikit-learn).

**What is compared:**

| Configuration | Tools available to the agent |
|---------------|------------------------------|
| **C0_bare** | `Read`, `Grep`, `Glob`, `Bash`, `Agent` (built-in coding-agent toolkit) |
| **C2_full** | All of the above **plus** four MCP tools (`get_answer`, `get_symbol`, `get_context`, `search_codebase`) backed by a precomputed documentation index of the repository |

Both configurations use the same model (`claude-sonnet-4-6`), the same SWE-QA
prompt scaffolding, the same per-task budget cap, and the same LLM judge. The
only variable is the tool surface presented to the agent.

> **Read this before quoting any number below.**
>
> Every SWE-QA row on this page was measured on **`claude-sonnet-4-6`**, on the
> Claude Code runtime as it stood in **April 2026** (flask48 v1, sklearn48) or
> **June 2026** (flask48 v2, flask v3). A number without that stamp has no shelf
> life, because the agent runtime is part of the measurement.
>
> The **v1 flask48 cost figure below (-36.2 %) did not reproduce.** The
> [v2 rerun](BENCHMARK_REPORT_FLASK48_V2.md) against current `main` reproduces the
> navigation wins with smaller magnitudes (tool calls -32 %, files read -54 %) but
> the cost result **inverts to +29 %**. The mechanism is a runtime change, not a
> repository property: v1's savings were largely driven by the baseline arm
> dispatching subagents that the current runtime no longer dispatches, and the MCP
> schema tax (~14.6k extra cache-write tokens per task) is no longer amortized
> against savings that no longer exist. [flask v3](BENCHMARK_REPORT_FLASK_V3.md)
> shows a cost win can return with a lean tool profile, at n=5-6, directional only.
>
> **sklearn48 has never been rerun.** The mechanism that flipped flask48's cost
> sign is a property of the runtime, so treat its -29.3 % cost figure as carrying
> the same risk flask48's did before v2 was run.

### flask48 v1 — `pallets/flask` (48 paired tasks, 2026-04-09, `claude-sonnet-4-6`)

**Superseded on cost by [the v2 rerun](BENCHMARK_REPORT_FLASK48_V2.md).** Kept
here because the navigation results reproduce directionally and because publishing
the row that did not hold up is the point.

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Cost / task (mean) | $0.1396 | $0.0890 | **-36.2 %** |
| Wall / task (mean) | 41.7 s | 33.9 s | **-18.6 %** |
| Tool calls (mean) | 7.4 | 3.8 | **-49.2 %** |
| Files read (mean) | 1.9 | 0.2 | **-89.0 %** |
| Score (0-10, mean) | 8.82 | 8.81 | tied |

**32 / 48 (67 %)** tasks are cheaper under C2; quality is at parity.

Full report: [**BENCHMARK_REPORT_FLASK48.md**](BENCHMARK_REPORT_FLASK48.md)

### sklearn48 — `scikit-learn/scikit-learn` (48 paired tasks, 2026-04-28, `claude-sonnet-4-6`, never rerun)

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Cost / task (mean) | $0.1180 | $0.0834 | **-29.3 %** |
| Wall / task (mean) | 39.7 s | 28.6 s | **-27.9 %** |
| Tool calls (mean) | 8.1 | 2.4 | **-70.5 %** |
| Files read (mean) | 1.8 | 0.6 | **-69.3 %** |
| Score (0-10, mean) | 8.72 | 8.23 | similar on this sample |

**33 / 48 (69 %)** tasks are cheaper under C2; **28 / 48 (58 %)** are faster.

Full report: [**BENCHMARK_REPORT_SKLEARN48.md**](BENCHMARK_REPORT_SKLEARN48.md)

### Bonus: token-efficiency benchmark

How many tokens does each strategy require for a model to understand a commit?
Measured on the 30 most recent non-merge commits of `pallets/flask` pinned at
`7ee9ceb7` (2023-01-20 to 2023-03-11), no LLM in the loop: all three counts are
deterministic `tiktoken` (`cl100k_base`), and `get_context` is served from the
prebuilt index.

**Re-measured 2026-08-01** on repowise `8cb7fba3` (v0.37.0), with the harness's own
`--min-repowise-tokens 200` guard **active**. 30 of 30 commits passed the guard.

| Strategy | Tokens / commit |
|---|---:|
| naive (full contents of changed files) | 13,984 |
| `git diff` only | 1,408 |
| **`get_context`** | **393** |

Reduction vs **naive**: **35.6x pooled**, 29.3x mean, 7.9x median, 133.8x best case.
Reduction vs **`git diff`**: 3.6x pooled, 2.8x mean.

**Lead with the pooled figure (35.6x).** Pooled is sum-of-tokens over
sum-of-tokens, so it weights each commit by the tokens actually at stake. A mean
of per-commit ratios does not: a trivial commit where `get_context` correctly
returns 40 tokens contributes a huge ratio that counts equally against a commit
saving a hundred thousand. `--min-repowise-tokens` (default 200) exists to drop
those, and it should stay on.

> **Correction, 2026-08-01.** An earlier version of this section published
> "**209x mean**, 26.8x pooled, 12.6x median, 1,214x best case" over
> naive 64,039 / diff 14,888 / `get_context` 2,391 tokens per commit, and told
> readers to reproduce it with `--min-repowise-tokens 0`, which switches the guard
> off. No raw CSV for that run was ever committed, so it cannot be verified or
> re-derived, and the numbers above do not reproduce it on the pinned checkout.
> The figures in this section replace it. Note that with the guard active the mean
> (29.3x) now lands *below* the pooled figure (35.6x) rather than 7.8x above it,
> which is the signature of the small-denominator inflation the guard prevents.

Reproduce:

```bash
.venv/Scripts/python.exe harness/token_efficiency_bench.py \
    --repo repos/pallets/flask --last 30
```

(The default `--min-repowise-tokens 200` is the correct setting. Do not pass `0`.)

Raw data: [`results/token_efficiency/results.csv`](results/token_efficiency/results.csv),
committed so the numbers above are checkable per commit.

---

## health-defect — Code Health vs. Defect Prediction

A reproducible benchmark measuring whether deterministic code health scores
predict real-world defects. Health scores are collected at a historical snapshot
(T0) from a worktree truncated at that commit; bug-fixing commits are counted
over the following 6 months (T0 -> T1); the two are correlated. The truncation is
what makes it leakage-free: scoring at HEAD would let the score see the fixes it
is supposed to predict.

### Headline numbers (canonical 21-repo corpus)

21 open-source repositories, 9 languages, 2,826 source files, T0 = 2025-11-23,
6-month forward defect window.

| Metric | Value | Note |
|---|---|---|
| ROC AUC, cross-project mean | **0.737 [0.683, 0.787]** | the headline; repo-cluster bootstrap |
| ROC AUC, file-pooled | **0.732** | file-level counterpart |
| Partial Spearman vs NLOC | **-0.156 [-0.233, -0.080]** | discrimination beyond file size |
| Popt vs LOC | **+0.134 [+0.080, +0.198]** | effort-aware, not a size proxy |

**The disclosed limit, stated up front:** within a fixed NLOC quartile,
discrimination collapses on small and medium files (Q1 AUC 0.525, Q2 0.572,
Q3 0.593, Q4 0.718). A pooled 0.73 is largely the between-band size contrast.
Signal survives where files are large; Q1 and Q2 straddle coin-flip. Full table
with CIs in `health-defect/README.md`.

> **Correction, 2026-08-01.** This section previously headlined
> "**10-75x defect ratio**, ROC AUC 0.70-0.74" from a 3-repo
> Django/Pydantic/FastAPI pilot of 862 files scored **at HEAD**, i.e. not
> leakage-free. `health-defect/README.md` had already superseded that pilot on
> 2026-06-24 and says in its own words not to quote those numbers as the headline.
> This page contradicted its own subfolder for five weeks. The 21-repo T0-anchored
> figures above are canonical. Do not quote 0.699, 0.746 or 0.87 either.

Full report: [**health-defect/BENCHMARK_REPORT.md**](health-defect/BENCHMARK_REPORT.md)
Reproduction steps: [**health-defect/README.md**](health-defect/README.md)

---

## Repository layout

```
repowise-bench/
├── README.md                         — this file (index of all benchmarks)
├── requirements.txt                  — shared Python dependencies
│
├── harness/                          — shared runner infrastructure (SWE-QA)
│   ├── run_experiment.py             — entry point: orchestrates a paired run
│   ├── swe_qa_runner.py              — per-task runner + LLM-as-judge
│   ├── metrics.py                    — RunMetrics, stream parser, BudgetTracker
│   └── token_efficiency_bench.py     — token-efficiency mini-benchmark
│
├── configs/                          — benchmark configuration files (SWE-QA)
│   └── swe_qa_flask48.yaml           — canonical SWE-QA / Flask configuration
│
├── data/                             — static benchmark datasets
│   └── swe_qa/tasks.json             — full SWE-QA task corpus
│
├── analysis/                         — aggregation scripts (SWE-QA)
│   └── aggregate_flask48.py
│
├── scripts/                          — shared utility scripts
│   └── download_benchmarks.py        — fetches SWE-QA dataset and clones repos
│
├── results/                          — all benchmark outputs (gitignored except baselines)
│   ├── swe_qa_flask48/               — SWE-QA Flask results
│   ├── swe_qa_sklearn48/             — SWE-QA scikit-learn results
│   ├── token_efficiency/             — token-efficiency results
│   └── health_defect_{repo}/         — one directory per health-defect repo
│       ├── correlation.json
│       ├── defect_counts.json
│       ├── joined_data.json
│       ├── health_scores.json
│       └── charts/
│
├── BENCHMARK_REPORT_FLASK48.md       — SWE-QA full report: Flask
├── BENCHMARK_REPORT_SKLEARN48.md     — SWE-QA full report: scikit-learn
│
├── health-defect/                    — self-contained health-defect benchmark
│   ├── README.md                     — benchmark overview and reproduction steps
│   ├── BENCHMARK_REPORT.md           — full statistical report
│   ├── config.yaml                   — per-repo configuration
│   ├── run_benchmark.py              — entry point
│   └── lib/                          — benchmark library modules
│
├── mcp_configs/                      — generated MCP server configs (gitignored)
├── indexes/                          — generated documentation indexes (gitignored)
├── repos/                            — cloned target repositories (gitignored)
└── logs/                             — per-run logs (gitignored)
```

---

## Adding a new benchmark

Each benchmark gets its own directory. Convention:

1. **Create a directory** at `repowise-bench/<benchmark-name>/`
2. **Add a `README.md`** with methodology, headline numbers, and reproduction steps
3. **Add a `run_benchmark.py`** (or equivalent entry point) runnable from within the directory
4. **Write results to `../results/<benchmark_name>_{variant}/`** so outputs land in the shared `results/` tree
5. **Update this README** — add a row to the [Benchmarks](#benchmarks) table

Shared repos and indexes can be reused from `../repos/` and `../indexes/`. New Python dependencies go in the top-level `requirements.txt`.

---

## SWE-QA methodology

### Pairing

Every task is run under both conditions, and every metric is computed per-task
before being aggregated. We never compare a C0 mean against a C2 mean drawn
from a different subset of tasks. If a task fails to complete under one
condition, it is re-run under both conditions and the new pair replaces the
old one in full.

### Cost accounting

Cost is read directly from each task's `estimated_cost_usd` field, populated
from the agent runtime's per-model billing roll-up. This sums cost across
every model invoked — both the parent session and any subagents dispatched
via the `Agent` tool. Token-based recomputation is intentionally avoided
because it can miss subagent spend not surfaced in the parent stream's
`usage` blocks.

### Judge

Each (task, configuration) pair is scored by an LLM judge using a fixed
five-dimension rubric (correctness, completeness, relevance, clarity,
reasoning) on a 0-10 scale. The judge does not see the configuration label
and is the same model in both arms.

### Reproducibility

Runs are deterministic up to LLM nondeterminism. Model versions, prompt
templates, and the SWE-QA task corpus are pinned in this repository. The
only external dependencies are the repository checkouts (pinned by commit
hash in the documentation index metadata) and the Anthropic API.

---

## SWE-QA reproduction

The full pipeline takes about 30 minutes of wall-clock time per arm and costs
approximately $5-10 per arm at list prices, depending on retry behavior.

### Prerequisites

- **Python 3.11+**
- **Claude Code CLI** (`claude`) installed and authenticated (OAuth or
  `ANTHROPIC_API_KEY`)
- **repowise CLI** installed and discoverable on `$PATH`, or a local checkout
  of repowise sibling to this directory
- ~5 GB free disk space for the checkout, index, and run logs

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Fetch the repo checkout and SWE-QA task corpus

```bash
python scripts/download_benchmarks.py --benchmark swe_qa
```

### 3. Build the C2 documentation index (optional — built on demand if absent)

```bash
repowise init repos/pallets/flask --output-dir indexes
```

### 4. Run the benchmark

```bash
PYTHONIOENCODING=utf-8 python harness/run_experiment.py \
    --config configs/swe_qa_flask48.yaml
```

Results are written incrementally to `results/swe_qa_flask48/swe_qa.jsonl`;
the run is safe to interrupt and resume.

### 5. Aggregate the results

```bash
python analysis/aggregate_flask48.py
```

For health-defect reproduction steps, see [health-defect/README.md](health-defect/README.md).

---

## SWE-QA output schema

Each row of `results/swe_qa_flask48/swe_qa.jsonl` contains:

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Unique task identifier (e.g. `flask_017`) |
| `benchmark` | string | Always `swe_qa` |
| `condition` | string | `C0_bare` or `C2_full` |
| `repo` | string | Source repository (e.g. `pallets/flask`) |
| `question_type` | string | SWE-QA question category (What / Where / How / Why) |
| `answer` | string | The agent's final answer |
| `judge_scores` | dict[str,float] | Judge dimension scores in [0, 10] |
| `estimated_cost_usd` | float | Total dollar cost across all models invoked |
| `wall_clock_seconds` | float | End-to-end wall-clock duration |
| `num_tool_calls` | int | Total tool invocations made by the agent |
| `files_explored` | list[str] | Distinct file paths opened via `Read` |

For the health-defect output schema, see [health-defect/README.md](health-defect/README.md).

---

## Validation tooling

### RefactoringMiner oracle (`harness/refactoringminer.py`)

An external, type-level check for the refactoring code generation in Repowise.
The product generates a diff from a deterministic plan and self-checks it
in-process with an LCOM4/TCC cohesion delta (a *metric* answer: "did cohesion
improve?"). This oracle adds the complementary *type* answer:
[RefactoringMiner](https://github.com/tsantalis/RefactoringMiner) (MIT) detects
which refactoring kinds occur between two commits, so it confirms a generated
change is genuinely an "Extract Class" / "Move Method" rather than merely a
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

The verdict pairs the RefactoringMiner type confirmation with a TCC before/after
delta computed by reusing Repowise core's class walker
(`walk_file(...).classes[*].tcc`), the same metric the in-process self-check
reports. No new Python dependencies; RefactoringMiner is an external Java jar.

---

## Citation

If you use these benchmarks or their results, please cite the relevant report:

```
Repowise on SWE-QA: A Benchmark Study of Documentation-Augmented Code
Question Answering on Flask. 2026.
```

```
Repowise health-defect Benchmark: Code Health Scores as Defect Predictors
Across Django, FastAPI, and Pydantic. 2026.
```

---

## License

This benchmark harness is released under the Apache 2.0 license. The repository
checkouts used as targets are owned by their respective projects and licensed
separately. The SWE-QA task corpus is the property of its original authors.
