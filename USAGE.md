# repowise-bench Usage Guide

Benchmark harness for evaluating repowise's impact on AI-assisted software engineering.
Runs Claude Code with and without repowise on established benchmarks (SWE-bench, SWE-QA)
and measures quality, cost, speed, and token efficiency.

---

## Quick Start (SWE-bench overnight run)

```powershell
cd repowise-bench

# 1. Install dependencies
pip install datasets tqdm anthropic pyyaml

# 2. Set UTF-8 encoding (required on Windows — do this once per terminal session)
$env:PYTHONIOENCODING="utf-8"

# 3. Download the benchmark dataset
python scripts/download_benchmarks.py --benchmark swe_bench

# 4. Run C0 vs C1 (leave overnight)
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml
```

> **Windows note**: Always set `$env:PYTHONIOENCODING="utf-8"` before running.
> The `VARNAME=value command` syntax is bash-only.
> In PowerShell, set the env var separately first.

Safe to Ctrl-C at any time — progress saves after every task. Just re-run
the same command to resume.

---

## Ablation Conditions

| Condition | Repowise | Mode | What it tests |
|-----------|----------|------|---------------|
| **C0_bare** | No | — | Baseline: Claude Code with only built-in tools |
| **C1_graph_git** | Yes | `--index-only` | Graph intelligence + Git intelligence (no LLM cost) |
| **C2_full** | Yes | full | All 4 layers (graph + git + LLM docs + decisions) |
| **C3_full_plus** | Yes | full + CLAUDE.md | Everything + generated CLAUDE.md + plugin skills |

### MCP tools per condition

C1 and C2 get different tool sets — only tools that return real data are allowed.

| Tool | C1 (index-only) | C2 (full) | Why |
|------|-----------------|-----------|-----|
| `get_risk` | Allowed | Allowed | Core: hotspots, co-changes, ownership. Pure graph+git. |
| `get_dependency_path` | Allowed | Allowed | Core: trace import chains. Pure graph. |
| `get_overview` | Allowed | Allowed | Entry points + git health. Partial in C1 (no prose). |
| `get_context` | Allowed | Allowed | Symbols, imports, ownership. No docs content in C1. |
| `get_why` | Allowed | Allowed | Git archaeology on file paths. No semantic search in C1. |
| `search_codebase` | **Blocked** | Allowed | Needs vector embeddings — empty in C1. |
| `get_dead_code` | **Blocked** | Allowed | Low value for bug fixing — wastes turns. |
| `get_architecture_diagram` | **Blocked** | Allowed | Low value — raw graph topology not helpful. |

The system prompts are tuned per benchmark type (SWE-bench vs SWE-QA) and per mode
to guide Claude toward the most useful tools and prevent wasted turns.

### Recommended run order

1. **Run C0 + C1 first** — C1 uses `--index-only` which is free (no LLM calls for indexing).
   This gives you the graph + git intelligence comparison at zero indexing cost.

2. **Add C2 later** — Just add the condition to the config and re-run. Existing C0 and C1
   results are preserved (auto-resume skips completed tasks). C2 indexing uses LLM calls
   for doc generation, so it costs money per repo.

3. **Add C3 later** — Same approach. C3 adds CLAUDE.md generation and plugin skills.

### Adding C2 without losing data

Your results live in JSONL files (`results/<experiment>/swe_bench.jsonl`). The runner
appends new results — it never overwrites. To add C2:

1. Edit your config to add the C2 condition:
```yaml
conditions:
  - name: "C0_bare"
    repowise_enabled: false
    repowise_mode: null
    claude_md: false
    plugin_skills: false

  - name: "C1_graph_git"
    repowise_enabled: true
    repowise_mode: "index-only"
    claude_md: false
    plugin_skills: false

  # Add this:
  - name: "C2_full"
    repowise_enabled: true
    repowise_mode: "full"
    claude_md: false
    plugin_skills: false
```

2. Re-run. C0 and C1 tasks are auto-skipped (already done). Only C2 tasks run.

```powershell
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml
```

Or run only C2 explicitly:
```powershell
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml --conditions C2_full
```

**Index caching**: C1 and C2 indexes are cached separately (keyed by `repo_commit_mode`).
C1 indexes are NOT reused for C2 — repowise needs to run full init for doc generation.
But once a C2 index is cached, it won't re-index on subsequent runs.

---

## Benchmarks

### SWE-bench Verified (`--benchmark swe_bench`)

- **What**: 500 real GitHub bug reports. Agent must fix the bug by editing source code.
- **Dataset**: `princeton-nlp/SWE-bench_Verified` on HuggingFace (human-verified subset — the industry standard for leaderboard comparisons).
- **Evaluation**: Captures agent's `git diff` as a patch. Formal evaluation (running FAIL_TO_PASS tests) requires the official SWE-bench Docker harness — we capture patches for offline evaluation.
- **Repos**: django (231), sympy (75), sphinx (44), matplotlib (34), scikit-learn (32), astropy (22), xarray (22), pytest (19), pylint (10), requests (8), seaborn (2), flask (1).
- **Difficulty**: `<15 min fix` (194), `15 min - 1 hour` (261), `1-4 hours` (42), `>4 hours` (3).

### SWE-QA (`--benchmark swe_qa`)

- **What**: 720 code-understanding questions across 15 OSS Python repos.
- **Dataset**: `SWE-QA/SWE-QA` on HuggingFace.
- **Evaluation**: LLM-as-judge scores on 5 dimensions (correctness, completeness, relevance, clarity, reasoning), 1-10 scale.
- **Repos**: 48 questions each across 15 repos (flask, django, requests, pytest, etc.)

---

## Controlling What Runs

### By repo count

Edit `repos:` in the config. The current overnight config includes 11 repos (498 tasks):

```yaml
repos:
  - "psf/requests"              #   8 tasks
  - "pallets/flask"             #   1 task
  - "pytest-dev/pytest"         #  19 tasks
  - "pylint-dev/pylint"         #  10 tasks
  - "django/django"             # 231 tasks (biggest)
  - "sympy/sympy"               #  75 tasks
  - "sphinx-doc/sphinx"         #  44 tasks
  - "matplotlib/matplotlib"     #  34 tasks
  - "scikit-learn/scikit-learn" #  32 tasks
  - "astropy/astropy"           #  22 tasks
  - "pydata/xarray"             #  22 tasks
```

### By task count

```yaml
max_tasks: 50    # only first 50 tasks across all repos
```

### By difficulty (SWE-bench only)

```yaml
difficulty:
  - "<15 min fix"
  - "15 min - 1 hour"
```

### At runtime (CLI overrides, no config edit needed)

```powershell
# Just one repo
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml --repos psf/requests

# Just one condition
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml --conditions C0_bare

# Combine
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml --conditions C1_graph_git --repos django/django,sympy/sympy
```

---

## Folder Structure

```
repowise-bench/
  configs/                      # Experiment configurations
    swe_bench_overnight.yaml    # Production SWE-bench (498 tasks x 2 conditions)
    swe_qa_overnight.yaml       # Production SWE-QA config
    swe_bench_validate.yaml     # Quick 1-task validation
    validate.yaml               # Quick SWE-QA validation

  data/                         # Downloaded datasets
    swe_bench/tasks.json        # 500 SWE-bench Verified tasks
    swe_qa/tasks.json           # 720 SWE-QA tasks

  harness/                      # Core runner code
    run_experiment.py            # Main orchestrator (entry point)
    swe_bench_runner.py          # SWE-bench: clone, checkout, index, agent, patch
    swe_qa_runner.py             # SWE-QA: clone, index, agent, judge
    metrics.py                   # RunMetrics, BudgetTracker, ResultWriter, parsers

  scripts/
    download_benchmarks.py       # Download datasets from HuggingFace
    estimate_costs.py            # Pre-flight cost calculator

  analysis/
    quick_check.py               # Quick signal check (needs update)
    generate_tables.py           # LaTeX + CSV tables for paper (needs update)

  repos/                         # Cloned repos (auto-created at runtime)
    psf/requests/
    django/django/
    ...

  indexes/                       # Cached repowise indexes
    psf_requests_22623bd8_index-only/
      .repowise/
        wiki.db                  # The actual graph+git database
        config.yaml
        mcp.json

  mcp_configs/                   # Auto-generated per-repo MCP server configs
    psf_requests.json

  results/                       # Experiment outputs (the gold)
    swe_bench_overnight_v1/
      swe_bench.jsonl            # One JSON line per task+condition
      experiment_meta.json       # Config snapshot + start time

  logs/                          # Detailed logs
    swe_bench_overnight_v1/
      raw_outputs/               # Full Claude stream-json per task+condition
        psf__requests-1142_C0_bare.json
        psf__requests-1142_C1_graph_git.json
      patches/                   # Git diffs (SWE-bench only)
        psf__requests-1142_C0_bare.patch
        psf__requests-1142_C1_graph_git.patch
        psf__requests-1142_gold.patch
```

---

## Config Reference

Full annotated config with all options:

```yaml
experiment_name: "my_experiment"      # Used in log messages

benchmarks:
  swe_bench:
    enabled: true                     # Toggle this benchmark
    max_tasks: null                   # null = all tasks, or integer to limit
    repos:                            # Filter to specific repos (omit for all)
      - "psf/requests"
      - "django/django"
    difficulty:                       # Filter by difficulty (SWE-bench only)
      - "<15 min fix"
      - "15 min - 1 hour"

  swe_qa:
    enabled: false
    max_tasks: null
    repos:
      - "pallets/flask"

  fea_bench:
    enabled: false                    # Not implemented yet

conditions:                           # List of ablation conditions to run
  - name: "C0_bare"                   # Unique name (appears in results)
    repowise_enabled: false
    repowise_mode: null               # null, "index-only", or "full"
    claude_md: false                  # Future: inject generated CLAUDE.md
    plugin_skills: false              # Future: enable repowise plugin skills

  - name: "C1_graph_git"
    repowise_enabled: true
    repowise_mode: "index-only"       # Graph + Git layers only (free)
    claude_md: false
    plugin_skills: false

  # - name: "C2_full"                 # Uncomment to add later
  #   repowise_enabled: true
  #   repowise_mode: "full"           # All 4 layers (costs LLM calls)
  #   claude_md: false
  #   plugin_skills: false

agent:
  model: "sonnet"                     # "sonnet", "opus", or full model ID
  max_turns: 25                       # Reference only (no CLI flag for this)
  timeout_seconds: 600                # Per-task wall clock limit

repowise:
  binary: "repowise"                  # Path to repowise CLI
  index_dir: "./indexes"              # Where to cache indexes
  doc_model: "gemini-2.0-flash-lite"  # LLM for doc generation (C2 only)

paths:
  results_dir: "./results/my_exp"     # JSONL results go here
  repos_dir: "./repos"                # Cloned repos
  logs_dir: "./logs/my_exp"           # Raw outputs, patches

budget:
  max_total_usd: 300.0               # Hard stop across all tasks
  max_per_task_usd: 3.0              # Claude --max-budget-usd per invocation
  abort_on_exceed: true               # Stop experiment on budget breach

parallelism:
  max_workers: 2                      # Concurrent workers
                                      # SWE-bench: parallel across repos,
                                      #   sequential within (git checkout safety)
                                      # SWE-QA: fully parallel

# SWE-QA only:
evaluation:
  judge_model: "sonnet"               # LLM judge model
  dimensions:                         # Scoring dimensions
    - "correctness"
    - "completeness"
    - "relevance"
    - "clarity"
    - "reasoning"
```

---

## Resilience

### Rate limits
The runner detects rate-limit errors from Claude Code and retries with exponential
backoff: 30s -> 60s -> 120s -> 240s -> 480s -> 900s (max 15 min). Up to 6 retries per task.

Detected patterns: `rate limit`, `429`, `too many requests`, `overloaded`, `capacity`,
`usage limit`, `exceeded quota`, `throttl`, `resource_exhausted`, `try again`.

### Usage caps
Claude Code usage caps (daily/hourly) trigger the same retry logic. The runner will
wait up to 15 minutes per retry, giving usage caps time to reset. For overnight runs,
this means the pipeline may pause but will resume automatically.

### Crashes / interruptions
- Progress is saved to JSONL after every single task.
- Ctrl-C triggers graceful shutdown (finishes current tasks, then exits).
- Re-running the same command auto-resumes from where it left off.
- Only error-free completions count as "done" — failed tasks are retried on resume.

### Errors
- Individual task errors (timeout, parse failure, etc.) are logged and the pipeline
  continues to the next task. One failure never crashes the pipeline.
- Budget exceeded stops the entire experiment gracefully.

---

## Results Format

### JSONL records (`results/<exp>/swe_bench.jsonl`)

Each line is a JSON object with these fields:

```
task_id              Instance ID (e.g., "psf__requests-1142")
benchmark            "swe_bench" or "swe_qa"
condition            "C0_bare", "C1_graph_git", etc.
repo                 GitHub org/name
question_type        Difficulty level (SWE-bench) or split name (SWE-QA)

answer               Git diff patch (SWE-bench) or text answer (SWE-QA)
judge_scores         LLM judge scores (SWE-QA only)
resolved             Test pass/fail (null until Docker evaluation)

input_tokens         API input tokens
output_tokens        API output tokens
cache_read_tokens    Cached input tokens
cache_write_tokens   Cache creation tokens
total_tokens         input + output

num_turns            Conversation turns
num_tool_calls       Total tool invocations (Read, Grep, MCP, etc.)
files_explored       Files read by agent
files_edited         Files modified by agent
repowise_tools_called  Which repowise MCP tools were used (e.g. ["get_risk", "get_overview"])

wall_clock_seconds   Total time including network
index_time_seconds   Time for repowise indexing
judge_time_seconds   Time for LLM judge (SWE-QA)
estimated_cost_usd   Actual cost from Claude CLI

retries              Number of rate-limit retries
error                Error message (null if success)
timed_out            Whether task hit timeout

timestamp            ISO timestamp
model_used           Model name
session_id           Claude session ID
stop_reason          How the conversation ended
duration_api_ms      API-side duration
repo_commit          Git commit hash (base_commit for SWE-bench)
raw_output_file      Path to full Claude stream-json output
prompt_sent          The prompt given to the agent
```

### Raw outputs (`logs/<exp>/raw_outputs/`)

Full Claude stream-json output per task+condition, including every tool call, tool
result, and assistant message. Use these for post-hoc analysis of agent behavior
(which tools were called, what they returned, how the agent reasoned).

### Patches (`logs/<exp>/patches/`)

SWE-bench only. Three files per task:
- `{instance_id}_C0_bare.patch` — Agent's fix under C0
- `{instance_id}_C1_graph_git.patch` — Agent's fix under C1
- `{instance_id}_gold.patch` — Ground truth fix from the dataset

---

## Cost Estimates

### SWE-bench (Sonnet)
- Per task: ~$0.10-0.30 (depends on repo size and bug complexity)
- 38 tasks (requests+flask+pytest+pylint) x 2 conditions: ~$8-20
- 498 tasks (all 11 repos) x 2 conditions: ~$100-300

### SWE-QA (Sonnet)
- Per task: ~$0.08-0.25
- 96 tasks (flask+requests) x 2 conditions: ~$20-50
- 720 tasks x 2 conditions: ~$100-350

### Indexing (C1, index-only)
- Free (no LLM calls). Takes 10-120s per repo+commit depending on size.
- Cached after first run per repo+commit — subsequent tasks at same commit skip indexing.

### Indexing (C2, full)
- Costs LLM calls for doc generation. ~$0.50-5.00 per repo depending on size.
- Cached after first run — no re-indexing on subsequent tasks in same repo+commit.

---

## Workflow: Running the Full Experiment

### Phase 1: C0 vs C1

```powershell
$env:PYTHONIOENCODING="utf-8"

# Download dataset
python scripts/download_benchmarks.py --benchmark swe_bench

# Run (498 tasks x 2 conditions = 996 runs)
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml

# Safe to Ctrl-C and re-run to resume at any time
```

### Phase 2: Add C2 (later)

Edit `configs/swe_bench_overnight.yaml`, uncomment the C2 condition block.
Re-run — C0 and C1 tasks are auto-skipped:

```powershell
python harness/run_experiment.py --config configs/swe_bench_overnight.yaml
```

### Phase 3: Evaluate patches (Docker)

See "What's Pending" section below.

### Phase 4: Run SWE-QA

```powershell
python scripts/download_benchmarks.py --benchmark swe_qa
python harness/run_experiment.py --config configs/swe_qa_overnight.yaml
```

---

## Technical Details

### Database location

Repowise by default stores its database globally at `~/.repowise/wiki.db`. The benchmark
harness overrides this via `REPOWISE_DB_URL` env var to store the DB locally at
`<repo>/.repowise/wiki.db` so the MCP server can find it per-repo.

The index cache (`indexes/`) stores a copy of `.repowise/` (including `wiki.db`) keyed
by `repo_commit_mode`. When a cached index exists, it's restored to the repo dir instead
of re-indexing.

### Stream-JSON output

The runner uses `--output-format stream-json --verbose` to capture full tool call details.
Each raw output file contains one JSON object per line — assistant messages with tool_use
blocks, user messages with tool_result blocks, and a final result summary. The parser
(`parse_claude_stream_output` in `metrics.py`) extracts tool calls, file operations,
repowise MCP tool usage, and the final result.

### Git checkout safety

SWE-bench requires checking out specific commits per task. `git clean -fdx -e .repowise`
is used to clean the working tree while preserving the repowise index. Tasks within the
same repo run sequentially (not parallel) to avoid git conflicts. Different repos can
run in parallel.

---

## Troubleshooting

### `UnicodeEncodeError` on Windows
Always set UTF-8 encoding first:
```powershell
$env:PYTHONIOENCODING="utf-8"
```

### Repowise indexing fails with "Aborted!"
Use `repowise_mode: "index-only"` (not "full") unless you have an LLM provider
configured. Full mode requires provider setup (e.g., `--provider gemini`).

### Repowise tools return "No repositories found"
The `wiki.db` must be at `<repo>/.repowise/wiki.db`. If indexes were cached before
this fix, clear and re-index:
```powershell
Remove-Item -Recurse indexes
```

### "Not logged in" errors
The runner uses Claude Code's OAuth auth:
```powershell
claude auth status
```

### Clone failures / disk space
Large repos (django, sympy) need full clones for SWE-bench (git checkout to specific
commits). Budget ~10-15 GB for all repos. Clones are cached in `repos/`.

### Budget exceeded
Increase `budget.max_total_usd` in the config, or filter to fewer repos/conditions.

---

## What's Pending / Roadmap

### SWE-bench Patch Evaluation (Docker) — NOT YET BUILT

The current pipeline captures agent patches but does **not** run the test suites to
verify if bugs are actually fixed. This is the `resolved` field in results (currently
always `null`).

**Why it's separate**: Each SWE-bench task requires a specific Python version and
dependency set. The official approach uses Docker containers per repo/version. This is
a heavy offline step — not something to do during the overnight agent run.

**Plan**:
1. Run the overnight pipeline -> captures patches to `logs/<exp>/patches/`
2. Post-process patches using the official SWE-bench evaluation harness:

```bash
pip install swebench

# Convert our patches to SWE-bench format
python scripts/prepare_predictions.py \
    --patches-dir logs/swe_bench_overnight_v1/patches \
    --output predictions.json

# Run evaluation (requires Docker + Linux recommended)
python -m swebench.harness.run_evaluation \
    --predictions_path predictions.json \
    --swe_bench_tasks data/swe_bench/tasks.json \
    --log_dir ./eval_logs \
    --testbed ./testbed \
    --timeout 900
```

3. Merge `resolved` (true/false) back into our JSONL results for analysis.

**What needs to be built**:
- [ ] `scripts/prepare_predictions.py` — Convert our `patches/` dir into the SWE-bench predictions format (`instance_id` + `model_patch` JSONL)
- [ ] `scripts/run_evaluation.py` — Wrapper around `swebench.harness.run_evaluation` that handles Docker setup and result collection
- [ ] `scripts/merge_eval_results.py` — Merge pass/fail verdicts back into our JSONL results and set the `resolved` field
- [ ] Docker environment setup (Linux recommended — Docker on Windows has overhead)

**Alternative**: Run evaluation on a Linux VM or CI server where Docker is native.
Upload the `patches/` directory and `data/swe_bench/tasks.json`, run evaluation there,
download results.

### SWE-QA Benchmark — BUILT, READY TO RUN

The SWE-QA pipeline is fully working (validated on Flask). Ready to run:
```powershell
python scripts/download_benchmarks.py --benchmark swe_qa
python harness/run_experiment.py --config configs/swe_qa_overnight.yaml
```

720 tasks x 2 conditions = 1,440 runs. Estimated ~$100-350.

### C3 Condition (CLAUDE.md + Plugin Skills) — NOT YET BUILT

C3 would test the full repowise experience: index + generated CLAUDE.md injected into
the repo + plugin skills enabled. Needs:
- [ ] Run `repowise generate-claude-md` and place the output in the repo
- [ ] Pass generated CLAUDE.md content via `--system-prompt` or let Claude discover it
- [ ] Define what "plugin skills" means in practice (which skills to enable)

### FEA-bench — NOT YET BUILT

Feature implementation benchmark from Microsoft. Needs a separate runner since tasks
involve multi-file feature additions rather than bug fixes. Lower priority than
SWE-bench and SWE-QA.

### Analysis / Paper Tables — NEEDS UPDATE

`analysis/generate_tables.py` and `analysis/quick_check.py` exist but reference the
old data format. Need updating once we have real results to analyze.
