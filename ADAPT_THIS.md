# ADAPT THIS TO YOUR SETUP

Things in this harness that I had to guess about and you need to verify/fix.
Check each one before running even the dry run.


## 1. Claude Code CLI flags

The harness assumes Claude Code CLI works like this:
```bash
claude -p "prompt" --output-format json --max-turns 15 --model claude-sonnet-4-20250514 --allowedTools "Read,Grep,Glob,Bash,mcp__repowise*"
```

Verify:
- Does `claude --output-format json` actually output JSON? What's the schema?
- What are the exact tool names? (`Read` vs `read_file`, etc.)
- How does `--allowedTools` work for MCP tools? Is it `mcp__repowise*` or something else?
- Does claude CLI support `--model` flag?

Fix in: `harness/swe_qa_runner.py` → `run_claude_code()`


## 2. Claude Code JSON output schema

The `parse_claude_code_output()` function in `harness/metrics.py` guesses at the
JSON structure. Run this to see the actual format:

```bash
claude -p "What is 2+2?" --output-format json | python -m json.tool
```

Then update `parse_claude_code_output()` to match.

Key fields needed:
- Token usage (input_tokens, output_tokens) — where in the JSON?
- Tool invocations — how are they represented?
- Final answer text — how to extract it?


## 3. Repowise CLI

The harness assumes:
```bash
repowise init              # Full index (all 4 layers)
repowise init --index-only # Graph + Git only (no LLM docs)
```

Verify:
- Is `--index-only` the actual flag name?
- Where does repowise store its index? (assumed `.repowise/` in repo root)
- How do you configure the doc model? (assumed REPOWISE_DOC_MODEL env var)
- How does the MCP server start? Does Claude Code auto-detect it?

Fix in: `harness/swe_qa_runner.py` → `index_repo()` and `harness/swe_bench_runner.py` → `index_at_commit()`


## 4. Repowise MCP tool names

For the ablation to work, we need to know exactly which MCP tools map to which
intelligence layer. The harness currently tracks `mcp__repowise*` tool calls
generically. For the paper, you want to know which specific tools were called.

Fill in this mapping:
```
Layer 1 (Graph):     mcp__repowise__??? (get_dependencies, get_graph, ...)
Layer 2 (Git):       mcp__repowise__??? (get_hotspots, get_ownership, ...)
Layer 3 (Docs):      mcp__repowise__??? (search_docs, get_summary, ...)
Layer 4 (Decisions): mcp__repowise__??? (get_why, ...)
```

This lets you analyze WHICH tools the agent uses per question type.
Fix in: `harness/metrics.py` → `parse_claude_code_output()`


## 5. SWE-QA dataset field names

I don't know the exact field names in the HuggingFace dataset. The code
assumes fields like `question`, `answer`, `repo`, `question_type`, `id`.
Run this to check:

```python
from datasets import load_dataset
ds = load_dataset("PengWeiHan/SWE-QA")
print(ds)
print(ds["test"][0].keys())
```

The dataset ID might also be different. Search HuggingFace if loading fails.
Fix in: `harness/swe_qa_runner.py` → `load_swe_qa_tasks()` and `run_swe_qa_task()`


## 6. SWE-bench test commands

Each SWE-bench task has a `test_cmd` field that specifies how to verify the fix.
Check that:
- The field is actually called `test_cmd` (might be `test_patch` or similar)
- You have the right Python version and dependencies per repo
- Docker isolation is set up if needed

The official SWE-bench repo has Docker configs for each repo. Consider using their
evaluation harness directly instead of rolling your own test runner.
See: https://github.com/SWE-bench/SWE-bench

Fix in: `harness/swe_bench_runner.py` → `run_tests()`


## 7. What to do if SWE-QA isn't on HuggingFace

The SWE-QA paper (arxiv 2509.14635) may not have released the dataset publicly yet.
If so, your options are:
1. Email the authors — academics usually share datasets on request
2. Use SWE-QA-Pro instead (seems more recent, check HuggingFace)
3. Use DeepCodeBench from Qodo (check their GitHub for the dataset)
4. Build a small custom QA set from your demo repos (FastAPI, LangChain)

Option 4 is fastest but least credible for the paper. Option 1 is best.


## 8. Parallelization

The harness runs tasks sequentially. For SWE-QA (576 × 4 = 2304 runs),
that's slow. Once you've validated the pipeline:

- Run each condition as a separate process:
  ```bash
  python harness/run_experiment.py --config configs/swe_qa.yaml --conditions C0_bare &
  python harness/run_experiment.py --config configs/swe_qa.yaml --conditions C1_graph_git &
  python harness/run_experiment.py --config configs/swe_qa.yaml --conditions C2_full &
  python harness/run_experiment.py --config configs/swe_qa.yaml --conditions C3_full_plus &
  ```

- For SWE-bench, you can also parallelize by repo since they use separate checkouts.
- The JSONL append is thread-safe (atomic line writes).
- Budget tracking won't work across processes — track total cost manually.
