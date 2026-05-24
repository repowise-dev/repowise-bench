# repowise-bench — Benchmark Suite

A collection of reproducible benchmarks for measuring the impact of code intelligence on developer workflows. Each benchmark is self-contained in its own directory with its own detailed report; this file is the entry point.

---

## Benchmarks

| Benchmark | Status | Headline | Report |
|-----------|--------|----------|--------|
| [**SWE-QA**](#swe-qa-coding-agent-efficiency) | Complete | -36-70% tool calls, -29-36% cost, quality at parity | [flask48](BENCHMARK_REPORT_FLASK48.md) · [sklearn48](BENCHMARK_REPORT_SKLEARN48.md) |
| [**health-defect**](#health-defect-code-health-vs-defect-prediction) | Complete | 10-75x defect ratio, ROC AUC 0.70-0.74 | [README](health-defect/README.md) · [full report](health-defect/BENCHMARK_REPORT.md) |

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

### flask48 — `pallets/flask` (48 paired tasks)

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Cost / task (mean) | $0.1396 | $0.0890 | **-36.2 %** |
| Wall / task (mean) | 41.7 s | 33.9 s | **-18.6 %** |
| Tool calls (mean) | 7.4 | 3.8 | **-49.2 %** |
| Files read (mean) | 1.9 | 0.2 | **-89.0 %** |
| Score (0-10, mean) | 8.82 | 8.81 | tied |

**32 / 48 (67 %)** tasks are cheaper under C2; quality is at parity.

Full report: [**BENCHMARK_REPORT_FLASK48.md**](BENCHMARK_REPORT_FLASK48.md)

### sklearn48 — `scikit-learn/scikit-learn` (48 paired tasks)

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

How many tokens does each strategy require for a model to understand a commit,
measured on the 30 most recent non-merge commits of `pallets/flask`?

| Strategy | Tokens / commit |
|---|---:|
| naive (full contents of changed files) | 64,039 |
| `git diff` only | 14,888 |
| **`get_context`** | **2,391** |

Reduction vs **naive**: **209x mean**, 26.8x pooled, 12.6x median, 1,214x best case.
Reduction vs **`git diff`**: 41.7x mean, 6.2x pooled.

Reproduce:

```bash
.venv/bin/python harness/token_efficiency_bench.py \
    --repo repos/pallets/flask --last 30 --min-repowise-tokens 0
```

Raw data: `results/token_efficiency/results.csv`.

---

## health-defect — Code Health vs. Defect Prediction

A reproducible benchmark proving that deterministic code health scores predict
real-world defects in open-source Python projects. Health scores are collected
at a historical snapshot (T0); bug-fixing commits are counted over the following
6 months (T0 -> T1); the two are correlated.

### Headline numbers

Across three public repositories (862 source files, 6-month defect window):

| Repo | Files | Spearman ρ | p-value | Defect ratio | ROC AUC | Precision@20 |
|------|------:|----------:|---------:|-------------:|--------:|-------------:|
| Django | 542 | **-0.337** | <0.0001 | **12x** | 0.698 | **70 %** |
| Pydantic | 216 | -0.229 | 0.0007 | 10x | **0.742** | 30 % |
| FastAPI | 104 | -0.272 | 0.0053 | 75x | 0.715 | 35 % |

**Files scoring below 4.0 have 10-75x more bug-fixing commits than files
scoring above 8.0.** The correlation is statistically significant (p < 0.01)
across all three codebases.

Top biomarker predictors (by Cliff's delta effect size):

1. `developer_congestion` — δ = +0.78 (Django)
2. `untested_hotspot` — δ = +0.69 (Django), +0.67 (FastAPI)
3. `brain_method` — δ = +0.62 (Pydantic), +0.43 (Django)

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
