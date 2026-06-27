# repowise-bench: the evidence

[Repowise](https://github.com/repowise-dev/repowise) is a tool that reads a
codebase and gives an AI coding agent (and the humans on the team) two things:
fast, curated context instead of a pile of files to grep, and a per-file health
score that estimates where the next bug is likely to be. This repository is where
those claims are tested, on public code, with the scripts to reproduce them.

If you are skeptical, you are asking two questions. **Does the health score
actually find bugs, or is it a dressed-up complexity meter? And does the agent
tooling actually save work, or just move it around?** This page answers both in
plain language, then links down into the full statistical reports for each claim.

[![GitHub stars](https://img.shields.io/github/stars/repowise-dev/repowise?style=flat)](https://github.com/repowise-dev/repowise)
[![License](https://img.shields.io/github/license/repowise-dev/repowise)](https://github.com/repowise-dev/repowise/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/repowise-dev/repowise)](https://github.com/repowise-dev/repowise/releases)

---

## Two proofs a skeptic can check first

**Proof A: it validates on your own repo.** You do not have to trust a number
from someone else's benchmark. After indexing any repo, repowise grades its own
flags against that repo's git history and prints the hit rate:

```text
Does the score find the bugs? 16/20 lowest-health files had a bug fix in the
last 6 months, 3.3x the 24% baseline (80% vs 24%).
```

```python
# the same precision@K / lift stat over MCP, before an agent trusts the score
get_health(include=["accuracy"])
```

**Proof B: it holds up on data it has never seen.** On the public PROMISE/jEdit
defect dataset, which played no part in calibrating repowise, the same biomarkers
reach ROC AUC **0.76 to 0.78**, within about 0.03 of that dataset's own tuned
CK-metric model. A signal that transfers to a corpus it was never fit on is not
overfit to ours. See [health-defect/BENCHMARK_REPORT.md](health-defect/BENCHMARK_REPORT.md).

---

## Is this just a linter?

No, and the difference is a category, not a quality gap. A linter checks whether
a line matches a known-bad pattern. The health score estimates which files are
likely to harbor the next bug, ranks them, and is calibrated and validated
against real defect history. It draws on signals no linter uses: churn,
ownership, co-change, blast radius, and hotspots. No linter is validated against
bug prediction.

The one part of repowise that overlaps a linter's territory is the performance
pillar, and even there it does what a file-local linter structurally cannot. On a
12,000-file benchmark, standard linters (clippy, ruff's PERF rules, ESLint,
golangci-lint) found **0** of the cross-function I/O-in-loop cases, because they
read one function at a time; repowise surfaced **557** findings across the run by
following the call graph across files, classifying each call as real I/O, and
ranking by how central and how churned the code is. Full data and the honest
caveats (including that the clippy head-to-head on Rust was not run end to end
because of a Windows build wall) are in
[perf-detection/](perf-detection/README.md).

---

## Code health: does the score find the bugs?

The headline result. Every file is scored at a historical commit (T0 =
2025-11-23) that **precedes** a 6-month bug-fix window, so no future information
leaks into the score. The score is then checked against which files actually
received bug fixes.

- Across **21 open-source repositories, 9 languages, and 2,826 files**, the
  cross-project mean ROC AUC is **0.737** (95% CI 0.683 to 0.787). An AUC of 0.5
  is a coin flip; 1.0 is perfect ranking.
- It **survives controlling for file size** (partial Spearman -0.156), so it is
  not merely "flag the big files."
- It significantly **out-discriminates raw churn (+0.10 AUC) and a prior-defects
  baseline (+0.117 AUC)**, DeLong p < 1e-9.

**What it does not do, stated plainly:**

- Among files of *similar size* (within an NLOC band) the signal is weak, AUC
  around **0.49**. A real part of the headline is that larger files carry more
  risk; we report the within-band wall rather than hide it.
- A prior-defects baseline still ranks bug-prone files slightly more efficiently
  under a fixed review budget: it **beats repowise on Popt by 0.085**, even
  though repowise beats it on AUC.

Full statistics, per-language breakdown, calibration, and limitations:
[health-defect/BENCHMARK_REPORT.md](health-defect/BENCHMARK_REPORT.md).

---

## Head-to-head vs CodeScene

On **2,770 files shared with CodeScene**, scored at the same leakage-free commit
against the same defect labels, paired significance tests across five axes
(June 2026). This is a distinct corpus from the 21-repo study above, so its
numbers are labeled separately.

| Axis | repowise | CodeScene | Δ (paired) | significance |
|---|---:|---:|---:|---|
| Recall @ 20% of lines | 0.173 | 0.074 | **+0.098** | p = 0.003 (repowise) |
| Effort-aware ranking (Popt) | 0.607 | 0.462 | **+0.144** | p = 0.003 (repowise) |
| Defect density (size-normalized, Alert:Healthy) | 2.18x | 0.56x | **+1.62** | p = 0.003 (repowise) |
| Discrimination (ROC AUC) | 0.731 | 0.705 | +0.026 | p = 0.054 (marginal) |
| Precision @ 20% of lines | 0.580 | 0.636 | -0.056 | p = 0.64 (a tie) |

Read honestly: repowise wins, significantly, on recall, effort-aware ranking, and
defect density. The AUC edge is **marginal** (not significant at 0.05).
Precision @ 20% is a **tie**: CodeScene's nominal lead is not statistically
significant. The reason CodeScene's precision looks higher is an operating-point
choice, not a better model: it flags about **27 files** to repowise's **132**, a
more conservative threshold that trades recall for precision. The named,
fully-qualified comparison, including the open-data business-impact replication
that did **not** reproduce CodeScene's published resolution-time correlation, is
in [health-defect/COMPARISON_REPORT.md](health-defect/COMPARISON_REPORT.md).

---

## Performance bugs a file-local linter cannot see

repowise detects wasted work hidden across function and file boundaries (N+1 and
I/O-in-loop), following the call graph rather than reading one function at a time.

| | result |
|---|---|
| Standard linters (clippy, ruff PERF, ESLint, golangci-lint) | **0** of the cross-function class |
| repowise findings across 12,000+ files | **557**, about 90 spanning functions |
| Hand-labeled precision | Go **96.7%**, TypeScript **100%**, Python **96.2%** |
| Runtime-confirmed fixes | **7 / 7** ran faster, from 2.5x up to ~2,500x at scale (median ~50x) |
| Ranking quality (NDCG) | **0.755** vs 0.292 for severity-only |

Full method, the per-tool baseline (experiment E1), and the honest Rust/clippy
caveat: [perf-detection/README.md](perf-detection/README.md) and
[perf-detection/METHODOLOGY.md](perf-detection/METHODOLOGY.md).

---

## Agent efficiency: the exploration is done once, offline

Most of a coding agent's spend goes to exploration: greping for symbols, reading
candidate files, re-reading them as context grows. repowise does that work once,
so the agent skips it on every query. Paired SWE-QA runs on real repositories
(same model, same harness, with and without repowise's MCP tools):

- Up to **-96% of the tokens** to load a commit's context: `get_context` returns
  **2,391 tokens vs 64,039** raw, about **27x** fewer.
- **-69% to -89% fewer files read** and **-49% to -70% fewer tool calls** at
  answer quality on par with raw exploration.
- `repowise distill` compresses noisy command output (test runs, `git log`,
  `git diff`) by **61% to 89%** before the agent reads it, errors preserved.

Honest note on cost: the dollar savings from fewer reads are real, but agent-side
prompt caching now mutes the per-task cost delta on short tasks, so we lead with
the work-reduction numbers (tool calls, files, tokens) that reproduce robustly
rather than a headline cost figure. The full story and the cost reconciliation
are in the SWE-QA reports below.

---

## Reproduce it yourself

Every benchmark ships its scripts and a fixed config. The two entry points:

```bash
# Agent-efficiency (SWE-QA), paired runs with and without repowise tools
python scripts/download_benchmarks.py --benchmark swe_qa
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/swe_qa_flask48.yaml
python analysis/aggregate_flask48.py

# Code-health vs defects, leakage-free
cd health-defect && python run_benchmark.py     # see health-defect/README.md
```

Full prerequisites, cost, and step-by-step are in the
[SWE-QA reproduction](#swe-qa-reproduction) section below and in
[health-defect/README.md](health-defect/README.md).

---

## Benchmarks at a glance

| Benchmark | What it tests | Headline | Reports |
|-----------|---------------|----------|---------|
| [Code health vs defects](#code-health-does-the-score-find-the-bugs) | Does the score predict real bugs? | Cross-project ROC AUC **0.737** (21 repos, 9 languages); external PROMISE/jEdit 0.76 to 0.78 | [README](health-defect/README.md) · [full report](health-defect/BENCHMARK_REPORT.md) · [vs CodeScene](health-defect/COMPARISON_REPORT.md) |
| [Performance detection](#performance-bugs-a-file-local-linter-cannot-see) | Can it find cross-function N+1 / I/O-in-loop? | **0** linter hits vs **557** findings; 96 to 100% precision; NDCG 0.755 | [README](perf-detection/README.md) · [methodology](perf-detection/METHODOLOGY.md) |
| [SWE-QA agent efficiency](#agent-efficiency-the-exploration-is-done-once-offline) | Does it cut agent work at parity quality? | -49 to -70% tool calls, -69 to -89% files read, quality at parity | [flask48](BENCHMARK_REPORT_FLASK48.md) · [sklearn48](BENCHMARK_REPORT_SKLEARN48.md) · [flask v3](BENCHMARK_REPORT_FLASK_V3.md) |

Two earlier SWE-QA runs are kept for transparency and labeled as interim:
[flask48 v2](BENCHMARK_REPORT_FLASK48_V2.md) (a 24/48 interim run that first
surfaced the cost-caching effect) was superseded by
[flask v3](BENCHMARK_REPORT_FLASK_V3.md) (lean MCP surface plus distill). They are
internal/experimental and not part of the headline.

---

## SWE-QA at a glance

A paired benchmark comparing two coding-agent configurations on SWE-QA tasks
drawn from [`pallets/flask`](https://github.com/pallets/flask) and
[`scikit-learn/scikit-learn`](https://github.com/scikit-learn/scikit-learn). Both
arms use the same model (`claude-sonnet-4-6`), the same prompt scaffolding, the
same per-task budget cap, and the same LLM judge. The only variable is the tool
surface.

| Configuration | Tools available to the agent |
|---------------|------------------------------|
| **C0_bare** | `Read`, `Grep`, `Glob`, `Bash`, `Agent` (built-in coding-agent toolkit) |
| **C2_full** | All of the above plus four MCP tools (`get_answer`, `get_symbol`, `get_context`, `search_codebase`) backed by a precomputed documentation index |

### flask48: `pallets/flask` (48 paired tasks)

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Tool calls (mean) | 7.4 | 3.8 | **-49.2 %** |
| Files read (mean) | 1.9 | 0.2 | **-89.0 %** |
| Wall / task (mean) | 41.7 s | 33.9 s | **-18.6 %** |
| Score (0-10, mean) | 8.82 | 8.81 | tied |

### sklearn48: `scikit-learn/scikit-learn` (48 paired tasks)

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Tool calls (mean) | 8.1 | 2.4 | **-70.5 %** |
| Files read (mean) | 1.8 | 0.6 | **-69.3 %** |
| Wall / task (mean) | 39.7 s | 28.6 s | **-27.9 %** |
| Score (0-10, mean) | 8.72 | 8.23 | similar on this sample |

### Token efficiency: tokens to understand a commit

Measured on the 30 most recent non-merge commits of `pallets/flask`:

| Strategy | Tokens / commit |
|---|---:|
| naive (full contents of changed files) | 64,039 |
| `git diff` only | 14,888 |
| **`get_context`** | **2,391** |

Reduction vs naive: 209x mean, 26.8x pooled, 12.6x median, 1,214x best case.
Reduction vs `git diff`: 41.7x mean, 6.2x pooled. Reproduce:

```bash
.venv/bin/python harness/token_efficiency_bench.py \
    --repo repos/pallets/flask --last 30 --min-repowise-tokens 0
```

---

## Repository layout

```
repowise-bench/
├── README.md                         this file: the skeptic hub and benchmark index
├── requirements.txt                  shared Python dependencies
│
├── health-defect/                    code-health vs defect-prediction benchmark
│   ├── README.md                     overview and reproduction steps
│   ├── BENCHMARK_REPORT.md           full statistical report (21 repos, calibration, limits)
│   ├── COMPARISON_REPORT.md          named head-to-head vs CodeScene (2,770 shared files)
│   ├── config.yaml                   per-repo configuration
│   ├── run_benchmark.py              entry point
│   └── lib/                          benchmark library modules
│
├── perf-detection/                   performance-bug detection benchmark
│   ├── README.md                     overview (0 linter hits vs 557 findings)
│   ├── METHODOLOGY.md                experiments E1 to E5, precision, ranking, runtime
│   └── benchmarks/                   raw runtime-confirmation results
│
├── harness/                          shared runner infrastructure (SWE-QA)
│   ├── run_experiment.py             entry point: orchestrates a paired run
│   ├── swe_qa_runner.py              per-task runner plus LLM-as-judge
│   ├── metrics.py                    RunMetrics, stream parser, BudgetTracker
│   ├── token_efficiency_bench.py     token-efficiency mini-benchmark
│   └── refactoringminer.py           external type-level refactoring oracle
│
├── configs/                          benchmark configuration files (SWE-QA)
│   └── swe_qa_flask48.yaml           canonical SWE-QA / Flask configuration
│
├── data/                             static benchmark datasets
│   └── swe_qa/tasks.json             full SWE-QA task corpus
│
├── analysis/                         aggregation scripts (SWE-QA)
│   └── aggregate_flask48.py
│
├── scripts/                          shared utility scripts
│   └── download_benchmarks.py        fetches SWE-QA dataset and clones repos
│
├── BENCHMARK_REPORT_FLASK48.md       SWE-QA full report: Flask
├── BENCHMARK_REPORT_SKLEARN48.md     SWE-QA full report: scikit-learn
├── BENCHMARK_REPORT_FLASK48_V2.md    interim 24/48 run (superseded, internal)
├── BENCHMARK_REPORT_FLASK_V3.md      lean MCP surface plus distill (cost reconciliation)
│
├── results/                          benchmark outputs (gitignored except baselines)
├── mcp_configs/                      generated MCP server configs (gitignored)
├── indexes/                          generated documentation indexes (gitignored)
├── repos/                            cloned target repositories (gitignored)
└── logs/                             per-run logs (gitignored)
```

---

## Adding a new benchmark

Each benchmark gets its own directory. Convention:

1. **Create a directory** at `repowise-bench/<benchmark-name>/`
2. **Add a `README.md`** with methodology, headline numbers, and reproduction steps
3. **Add a `run_benchmark.py`** (or equivalent entry point) runnable from within the directory
4. **Write results to `../results/<benchmark_name>_{variant}/`** so outputs land in the shared `results/` tree
5. **Update this README** by adding a row to the benchmarks table

Shared repos and indexes can be reused from `../repos/` and `../indexes/`. New
Python dependencies go in the top-level `requirements.txt`.

---

## SWE-QA methodology

### Pairing

Every task is run under both conditions, and every metric is computed per-task
before being aggregated. We never compare a C0 mean against a C2 mean drawn from
a different subset of tasks. If a task fails to complete under one condition, it
is re-run under both conditions and the new pair replaces the old one in full.

### Cost accounting

Cost is read directly from each task's `estimated_cost_usd` field, populated from
the agent runtime's per-model billing roll-up. This sums cost across every model
invoked, both the parent session and any subagents dispatched via the `Agent`
tool. Token-based recomputation is intentionally avoided because it can miss
subagent spend not surfaced in the parent stream's `usage` blocks.

### Judge

Each (task, configuration) pair is scored by an LLM judge using a fixed
five-dimension rubric (correctness, completeness, relevance, clarity, reasoning)
on a 0-10 scale. The judge does not see the configuration label and is the same
model in both arms.

### Reproducibility

Runs are deterministic up to LLM nondeterminism. Model versions, prompt
templates, and the SWE-QA task corpus are pinned in this repository. The only
external dependencies are the repository checkouts (pinned by commit hash in the
documentation index metadata) and the Anthropic API.

---

## SWE-QA reproduction

The full pipeline takes about 30 minutes of wall-clock time per arm and costs
approximately $5 to $10 per arm at list prices, depending on retry behavior.

### Prerequisites

- **Python 3.11+**
- **Claude Code CLI** (`claude`) installed and authenticated (OAuth or
  `ANTHROPIC_API_KEY`)
- **repowise CLI** installed and discoverable on `$PATH`, or a local checkout of
  repowise sibling to this directory
- ~5 GB free disk space for the checkout, index, and run logs

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Fetch the repo checkout and SWE-QA task corpus

```bash
python scripts/download_benchmarks.py --benchmark swe_qa
```

### 3. Build the C2 documentation index (optional, built on demand if absent)

```bash
repowise init repos/pallets/flask --output-dir indexes
```

### 4. Run the benchmark

```bash
PYTHONIOENCODING=utf-8 python harness/run_experiment.py \
    --config configs/swe_qa_flask48.yaml
```

Results are written incrementally to `results/swe_qa_flask48/swe_qa.jsonl`; the
run is safe to interrupt and resume.

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
change is genuinely an "Extract Class" or "Move Method" rather than merely a
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
Repowise health-defect Benchmark: Code Health Scores as Defect Predictors,
21 repositories across 9 languages. 2026.
```

```
Repowise on SWE-QA: A Benchmark Study of Documentation-Augmented Code
Question Answering on Flask and scikit-learn. 2026.
```

---

## License

This benchmark harness is released under the Apache 2.0 license. The repository
checkouts used as targets are owned by their respective projects and licensed
separately. The SWE-QA task corpus is the property of its original authors.
