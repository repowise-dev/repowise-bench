# Repowise Benchmark Harness

Ablation study measuring how [Repowise](https://repowise.dev) codebase intelligence affects AI agent performance on code understanding and bug-fixing tasks. Targeting **ESEM 2026** paper.

## Directory Structure

```
repowise-bench/
  configs/           # YAML experiment configs (budgets, conditions, models)
    mini_test.yaml   # 3 hand-crafted tasks for pipeline validation (use this first)
    dry_run.yaml     # 5 tasks from SWE-QA, C0 only
    swe_qa.yaml      # Full SWE-QA: 576 tasks x 4 conditions
    swe_bench.yaml   # SWE-bench Verified: 100 tasks x 4 conditions
    fea_bench.yaml   # FEA-Bench Lite: 50 tasks x 2 conditions
  harness/
    run_experiment.py   # Main CLI entry point
    swe_qa_runner.py    # SWE-QA task runner + LLM-as-judge
    swe_bench_runner.py # SWE-bench runner (NOT YET VALIDATED)
    metrics.py          # RunMetrics dataclass, output parsers, BudgetTracker
  scripts/
    download_benchmarks.py  # HuggingFace dataset downloader + repo cloner
    estimate_costs.py       # Pre-flight cost calculator
  analysis/
    quick_check.py       # Post-run signal check (C0 vs C2 delta)
    generate_tables.py   # LaTeX + CSV table generation for paper
  data/swe_qa/test.json  # Mini test dataset (3 hand-crafted questions)
  repos/                 # Cloned target repos (created at runtime)
  results/               # JSONL results per experiment (created at runtime)
  indexes/               # Cached repowise indexes (created at runtime)
  logs/                  # Experiment logs (created at runtime)
```

## Ablation Conditions

| Condition | Repowise | Mode | Description |
|-----------|----------|------|-------------|
| C0_bare | No | -- | Bare Claude Code agent, no repowise tools |
| C1_graph_git | Yes | `--index-only` | Graph + git intelligence only (no LLM docs) |
| C2_full | Yes | full | All 4 layers: graph, git, docs, decisions |
| C3_full_plus | Yes | full | Full + CLAUDE.md + plugin skills |

## Benchmarks

| Benchmark | What it tests | Eval method | Status |
|-----------|--------------|-------------|--------|
| SWE-QA | Code understanding (Q&A) | LLM-as-judge (5 dimensions, 1-10) | Working |
| SWE-bench Verified | Bug fixing | Test suite pass/fail | Runner exists, NOT validated |
| FEA-Bench Lite | Feature implementation | Test suite pass/fail | NO runner implemented |

## Prerequisites

- **Python 3.11+**
- **Claude Code CLI** (`claude`) installed and authenticated via OAuth or API key
- **repowise** installed (`pip install repowise`)
- Python deps: `pip install tqdm pyyaml anthropic`
- Optional (for full benchmark datasets): `pip install datasets` and a GitHub token

## How It Works

1. For each task, the harness calls `claude -p "<prompt>" --output-format json --model sonnet --allowed-tools "Read,Grep,Glob,Bash"` as a subprocess
2. For repowise conditions (C1-C3), it first runs `repowise init` on the target repo, then adds `mcp__repowise__*` to allowed tools
3. Claude's JSON output provides: `result` (answer text), `usage` (tokens), `total_cost_usd`, `num_turns`
4. For SWE-QA, the answer is scored by an LLM judge (also via Claude CLI) on 5 dimensions: correctness, completeness, relevance, clarity, reasoning
5. Results are saved as JSONL (one line per task), crash-safe with `--resume` support

## Quick Start

### Step 1: Validate the pipeline with mini test (costs ~$0.50)

```bash
cd repowise-bench
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/mini_test.yaml
```

This runs 3 hand-crafted questions against the local repowise repo with C0_bare only.
Requires a junction/symlink at `repos/repowise` pointing to the repowise repo root:

```bash
# Windows (run as admin or with dev mode enabled):
mklink /J repos\repowise C:\path\to\repowise

# Linux/macOS:
ln -s /path/to/repowise repos/repowise
```

Check results:
```bash
python -c "
import json
with open('results/mini_test/swe_qa.jsonl') as f:
    for line in f:
        d = json.loads(line)
        scores = d.get('judge_scores', {})
        avg = sum(v for v in scores.values() if isinstance(v, (int,float))) / max(len(scores),1) if 'error' not in scores else 'ERR'
        print(f\"{d['task_id']} | {d['condition']} | turns={d['num_turns']} | cost=\${d['estimated_cost_usd']:.3f} | judge={avg}\")
"
```

### Step 2: Run SWE-QA with real dataset

```bash
# Download datasets and clone repos
python scripts/download_benchmarks.py

# Estimate costs
python scripts/estimate_costs.py

# Run all conditions
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/swe_qa.yaml

# Or run specific conditions in parallel (budget tracking won't work cross-process):
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/swe_qa.yaml --conditions C0_bare &
PYTHONIOENCODING=utf-8 python harness/run_experiment.py --config configs/swe_qa.yaml --conditions C2_full &
```

### Step 3: Check signal before spending more

```bash
python analysis/quick_check.py results/swe_qa/
```

This shows C0 vs C2 delta. If delta > 10% on judge scores, the signal is strong.

### Step 4: Generate paper tables

```bash
python analysis/generate_tables.py results/
```

Outputs LaTeX (.tex) and CSV files to `results/tables/`.

## Config Reference

Each YAML config has these sections:

```yaml
experiment_name: "swe_qa"

benchmarks:
  swe_qa:
    enabled: true
    max_tasks: 576           # Cap number of tasks
    question_types: [...]    # Filter: What, Why, Where, How
    repos: [...]             # Filter: specific repos only

conditions:                  # Which ablation conditions to run
  - name: "C0_bare"
    repowise_enabled: false
    repowise_mode: null

agent:
  model: "sonnet"            # Model alias or full name
  max_turns: 15              # (informational only, not enforced by CLI)
  timeout_seconds: 300       # Kill agent after this many seconds

budget:
  max_total_usd: 200.0       # Total experiment budget
  max_per_task_usd: 2.0      # Per-task cost cap (passed to --max-budget-usd)
  abort_on_exceed: true

evaluation:
  judge_model: "claude-sonnet-4-20250514"  # Model for LLM-as-judge
```

## CLI Flags Reference (Verified 2026-04-05)

The harness uses these Claude Code CLI flags:
- `-p "<prompt>"` -- non-interactive print mode
- `--output-format json` -- returns single JSON object with `result`, `usage`, `num_turns`, `total_cost_usd`
- `--model sonnet` -- model alias (also accepts full names like `claude-sonnet-4-20250514`)
- `--allowed-tools "Read,Grep,Glob,Bash"` -- restrict available tools
- `--max-budget-usd 2.0` -- cost cap per invocation

**NOT supported** (common mistakes):
- ~~`--max-turns`~~ -- does not exist
- ~~`--bare`~~ -- skips OAuth auth, only works with `ANTHROPIC_API_KEY` env var

## Output Format

Results are saved as JSONL in `results/<experiment>/swe_qa.jsonl`. Each line:

```json
{
  "task_id": "mini_001",
  "benchmark": "swe_qa",
  "condition": "C0_bare",
  "repo": "local/repowise",
  "question_type": "What",
  "answer": "...",
  "judge_scores": {"correctness": 10, "completeness": 9, "relevance": 10, "clarity": 10, "reasoning": 9},
  "input_tokens": 600,
  "output_tokens": 1392,
  "num_turns": 9,
  "estimated_cost_usd": 0.1322,
  "wall_clock_seconds": 41.2,
  "error": null
}
```

## Validated Test Run (2026-04-05)

Mini test with 3 hand-crafted questions about the repowise repo, C0_bare, Sonnet model:

| Task | Type | Turns | Cost | Judge Avg |
|------|------|-------|------|-----------|
| mini_001 | What | 9 | $0.13 | 10.0/10 |
| mini_002 | How | 14 | $0.29 | pending |
| mini_003 | Where | 7 | $0.11 | 9.4/10 |

Total: $0.53 for 3 tasks, ~50s/task average.

## Known Issues and TODOs

- **Windows encoding**: Must set `PYTHONIOENCODING=utf-8` on Windows or emoji in print statements will crash
- **SWE-QA dataset**: The `PengWeiHan/SWE-QA` HuggingFace dataset may not be public yet. Alternatives: email authors, use SWE-QA-Pro, build custom QA set
- **Tool call tracking**: `--output-format json` does not include tool call details (names, file paths). Use `--output-format stream-json --verbose` for that -- a `parse_claude_stream_output()` function exists in `metrics.py` but is not yet wired into the runner
- **SWE-bench runner**: Exists but has NOT been validated. Test commands and Docker isolation need verification
- **FEA-bench runner**: Does not exist. `fea_bench.yaml` config is present but no `fea_bench_runner.py`
- **C3 condition**: `claude_md` and `plugin_skills` flags are in configs but not implemented in `run_claude_code()`
- **Judge auth**: The judge uses Claude CLI (not Anthropic SDK directly) because the harness relies on OAuth. If you have `ANTHROPIC_API_KEY` set, it will use the SDK instead (cheaper, no tool overhead)
- **`max_turns` in config**: Stored for documentation but not enforced -- Claude CLI has no `--max-turns` flag. Budget cap (`--max-budget-usd`) is the actual limiter
- **Parallelization**: Tasks run sequentially. For speed, run conditions as separate processes. JSONL append is atomic but budget tracking won't work cross-process
