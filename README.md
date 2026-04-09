# repowise-bench — flask48 SWE-QA Benchmark

A reproducible paired benchmark comparing two coding-agent configurations on
a 48-question subset of [SWE-QA](https://arxiv.org/abs/2401.00000) drawn from
the [`pallets/flask`](https://github.com/pallets/flask) repository.

The full results, methodology, per-task tables, and discussion are reported
in [**BENCHMARK_REPORT_FLASK48.md**](BENCHMARK_REPORT_FLASK48.md). This README
covers what the repository contains, how the experiment is set up, and how to
reproduce the numbers from a clean checkout.

---

## Headline numbers

On 48 paired tasks (`pallets/flask` SWE-QA subset, `claude-sonnet-4-6` end-to-end):

| Metric            | C0 (baseline) | C2 (doc-augmented) | Δ        |
|-------------------|--------------:|-------------------:|---------:|
| Cost / task (mean)|       $0.1396 |            $0.0890 | **−36.2 %** |
| Wall / task (mean)|         41.7 s|             33.9 s | **−18.6 %** |
| Tool calls (mean) |           7.4 |                3.8 | **−49.2 %** |
| Files read (mean) |           1.9 |                0.2 | **−89.0 %** |
| Score (0–10, mean)|          8.82 |               8.81 | tied        |

**32 / 48 (67 %)** tasks are cheaper under C2; quality is at parity (Δ = −0.01
on a 0–10 LLM-judge scale; identical medians).

---

## Bonus: a token-efficiency benchmark, for completeness

There is a small genre of "token efficiency" benchmarks going around at
the moment, and it would be impolite not to contribute one. Ours runs on
the 30 most recent non-merge commits of `pallets/flask` and asks a single
question: *to understand a commit, how many tokens does each strategy ask
the model to read?*

| Strategy                                | Tokens / commit |
|-----------------------------------------|----------------:|
| naive (full contents of changed files)  |          64,039 |
| `git diff` only                         |          14,888 |
| **repowise `get_context`**              |       **2,391** |

Reduction vs **naive**: **209× mean**, 26.8× pooled (Σ/Σ), 12.6× median,
1,214× best case.
Reduction vs **`git diff`**: 41.7× mean, 6.2× pooled.

`tiktoken` `cl100k_base` for all three columns, no per-strategy fudge,
same file list everywhere. We report mean, pooled and median together
because picking just one would be the kind of thing other people in this
genre seem to do.

Reproduce:

```bash
.venv/bin/python harness/token_efficiency_bench.py \
    --repo repos/pallets/flask --last 30 --min-repowise-tokens 0
```

Raw data: `results/token_efficiency/results.csv`. Treat this as a
sanity-check, not a leaderboard — the actual evaluation in this repo is
the SWE-QA run above, which has third-party ground truth and an
independently-scored LLM judge.

> See `BENCHMARK_REPORT_FLASK48.md` for the full report, the per-model cost
> decomposition, the trimmed-mean and median analyses, the per-task best/worst
> tables, and the methodology footnote on cost pricing.

---

## What the benchmark compares

| Configuration | Tools available to the agent                                                  |
|---------------|--------------------------------------------------------------------------------|
| **C0_bare**   | `Read`, `Grep`, `Glob`, `Bash`, `Agent` (built-in coding-agent toolkit)       |
| **C2_full**   | All of the above **plus** the four repowise MCP tools (`get_answer`, `get_symbol`, `get_context`, `search_codebase`) backed by a precomputed documentation index of the repository |

Both configurations use the same model (`claude-sonnet-4-6`), the same SWE-QA
prompt scaffolding, the same per-task budget cap, and the same LLM judge. The
only variable is the tool surface presented to the agent.

The repowise documentation index is built once per repository version and is
amortized across all queries against the same repository. Index build cost is
**not** counted in the per-task numbers above; see the report for discussion.

---

## Repository layout

```
repowise-bench/
├── README.md                       — this file
├── BENCHMARK_REPORT_FLASK48.md     — full report (methodology, results, discussion)
├── requirements.txt
├── configs/
│   └── swe_qa_flask48.yaml         — canonical benchmark configuration
├── data/
│   └── swe_qa/
│       └── tasks.json              — full SWE-QA task corpus (the harness selects flask48)
├── harness/
│   ├── run_experiment.py           — entry point: orchestrates a paired run
│   ├── swe_qa_runner.py            — per-task runner + LLM-as-judge
│   └── metrics.py                  — RunMetrics, stream parser, BudgetTracker
├── analysis/
│   └── aggregate_flask48.py        — produces the canonical results table
├── scripts/
│   └── download_benchmarks.py      — fetches the SWE-QA dataset and clones target repos
├── results/
│   └── swe_qa_flask48/
│       ├── swe_qa.jsonl            — final results: 96 rows = 48 tasks × 2 conditions
│       └── experiment_meta.json    — experiment configuration snapshot
├── mcp_configs/                    — generated MCP server configs per repo
├── indexes/                        — generated repowise indexes per repo (gitignored)
├── repos/                          — cloned target repositories (gitignored)
└── logs/                           — per-run logs (gitignored)
```

The committed `results/swe_qa_flask48/swe_qa.jsonl` is the canonical result
set used to produce the report. Re-running the benchmark writes new rows
into the same file (or a copy you point at via `--output-dir`).

---

## Methodology

### Pairing

Every task in the benchmark is run under both conditions, and every metric is
computed per-task before being aggregated. We never compare a C0 mean against
a C2 mean drawn from a different subset of tasks. If a task fails to complete
under one condition (for example, by hitting the per-task budget cap), it is
re-run under both conditions and the new pair replaces the old one in full.

### Cost accounting

Cost is read directly from each task's `estimated_cost_usd` field. The harness
populates this from the agent runtime's per-model billing roll-up, which sums
the cost across every model invoked under the task — both the parent session
and any subagents the parent dispatches via the `Agent` tool. Token-based
recomputation is intentionally avoided because it can miss subagent spend that
the parent stream's `usage` blocks do not surface.

The cost numbers in `BENCHMARK_REPORT_FLASK48.md` price subagent dispatches at
Sonnet rates to keep the comparison apples-to-apples with C2, which itself
runs pure Sonnet end-to-end. The report's footnote explains this in detail.
The raw per-task `estimated_cost_usd` field in the JSONL is the cost as
measured by the agent runtime (i.e. with whatever models the runtime
dispatched at runtime); the canonical aggregator (`analysis/aggregate_flask48.py`)
prints those raw measured numbers without adjustment.

### Judge

Each (task, configuration) pair is scored by an LLM judge using a fixed
five-dimension rubric (correctness, completeness, relevance, clarity,
reasoning) on a 0–10 scale. The judge does not see the configuration label
and is the same model in both arms.

### Reproducibility

Runs are deterministic up to LLM nondeterminism. Model versions, prompt
templates, and the SWE-QA task corpus are pinned in this repository. The only
external dependencies are the `pallets/flask` checkout (pinned by commit hash
in the documentation index metadata) and the Anthropic API.

---

## Reproduction

The full pipeline takes about 30 minutes of wall-clock time per arm and costs
approximately $5–10 per arm at list prices, depending on retry behavior.

### Prerequisites

- **Python 3.11+**
- **Claude Code CLI** (`claude`) installed and authenticated (OAuth or
  `ANTHROPIC_API_KEY`)
- **repowise CLI** installed and discoverable on `$PATH`, *or* a local checkout
  of repowise sibling to this directory
- ~5 GB free disk space for the Flask checkout, repowise index, and run logs

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Fetch the Flask checkout and the SWE-QA task corpus

```bash
python scripts/download_benchmarks.py --benchmark swe_qa
```

This clones `pallets/flask` into `repos/pallets/flask` and writes the SWE-QA
task corpus into `data/swe_qa/tasks.json`. The harness selects the 48 Flask
tasks automatically when it sees `repos: ["pallets/flask"]` in the config.

### 3. Build the C2 documentation index (optional — the harness builds it on
demand)

```bash
repowise init repos/pallets/flask --output-dir indexes
```

The first benchmark run will trigger this automatically if the index is not
already present. Building it explicitly is useful when you want to time the
ingestion pass separately from the per-task numbers.

### 4. Run the benchmark

```bash
PYTHONIOENCODING=utf-8 python harness/run_experiment.py \
    --config configs/swe_qa_flask48.yaml
```

This runs both arms (C0_bare and C2_full) on all 48 tasks in a single
invocation. Results are written incrementally to
`results/swe_qa_flask48/swe_qa.jsonl`; the run is safe to interrupt with
Ctrl-C and resume by re-invoking the same command.

### 5. Aggregate the results

```bash
python analysis/aggregate_flask48.py
```

This prints the per-task table, the metric summary, the per-metric best /
worst tasks, and the totals. The output should match the headline numbers
above and the tables in `BENCHMARK_REPORT_FLASK48.md` (modulo LLM
nondeterminism on a fresh run).

To aggregate a different results file:

```bash
python analysis/aggregate_flask48.py --results path/to/swe_qa.jsonl
```

---

## Output schema

Each row of `results/swe_qa_flask48/swe_qa.jsonl` contains the following
fields (selected; the runner records additional diagnostic fields not used
by the aggregator):

| Field                 | Type            | Description                                            |
|-----------------------|-----------------|--------------------------------------------------------|
| `task_id`             | string          | Unique task identifier (e.g. `flask_017`)              |
| `benchmark`           | string          | Always `swe_qa`                                        |
| `condition`           | string          | `C0_bare` or `C2_full`                                 |
| `repo`                | string          | Source repository (e.g. `pallets/flask`)               |
| `question_type`       | string          | SWE-QA question category (What / Where / How / Why)    |
| `answer`              | string          | The agent's final answer                               |
| `judge_scores`        | dict[str,float] | Judge dimension scores in [0, 10]                      |
| `estimated_cost_usd`  | float           | Total dollar cost across all models invoked            |
| `wall_clock_seconds`  | float           | End-to-end wall-clock duration of the task             |
| `num_turns`           | int             | Number of assistant turns in the agent session         |
| `num_tool_calls`      | int             | Total tool invocations made by the agent               |
| `files_explored`      | list[str]       | Distinct file paths opened via `Read`                  |
| `input_tokens`        | int             | Parent-session input tokens                            |
| `output_tokens`       | int             | Parent-session output tokens                           |
| `model_used`          | string          | Concrete model identifier reported by the runtime      |
| `timestamp`           | string          | ISO-8601 timestamp of task start                       |

---

## Citation

If you use this benchmark or its results, please cite the report:

```
Repowise on SWE-QA: A Benchmark Study of Documentation-Augmented Code
Question Answering on Flask. 2026.
```

---

## License

This benchmark harness is released under the Apache 2.0 license. The Flask
checkout used as the target repository is owned by the Pallets Projects and
licensed separately; the SWE-QA task corpus is the property of its original
authors.
