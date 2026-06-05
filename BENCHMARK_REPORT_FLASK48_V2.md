# SWE-QA flask48 — v2 rerun (2026-06)

A full re-run of the published [flask48 benchmark](BENCHMARK_REPORT_FLASK48.md)
against the current repowise codebase (post-Distill, post-perf-overhaul),
with the documentation index regenerated on OpenAI `gpt-5.4-nano` instead of
Gemini. Same 48 paired tasks, same C0/C2 design, same model family for agent
and judge.

> **Status: stopped deliberately at 24/48 pairs** (0 errors, clean paired
> data) once the cost verdict was structurally clear — see
> [Results](#results-24-paired-tasks--run-stopped-early). A rerun is planned
> after MCP-surface changes.

---

## What changed since v1

| Axis | v1 (published) | v2 (this run) |
|---|---|---|
| repowise code | 0.13-era (May 2026) | current `main` + MCP retrieval fixes (see below) |
| Doc model | `gemini-3.1-flash-lite-preview` | `gpt-5.4-nano` |
| Embedder | gemini | openai |
| Index pages | 26 | 26 (identical type distribution) |
| Flask commit | `7ee9ceb` | `7ee9ceb` (same) |
| Tasks / design | 48 paired, C0 vs C2 | identical |
| Config | `configs/swe_qa_flask48.yaml` | `configs/swe_qa_flask48_v2.yaml` |

Run it:

```powershell
$env:REPOWISE_ROOT = "<repowise checkout or worktree>"   # optional override
$env:OPENAI_API_KEY = "<key>"   # docs + embeddings + get_answer synthesis
python harness/run_experiment.py --config configs/swe_qa_flask48_v2.yaml
python analysis/aggregate_flask48.py --results results/swe_qa_flask48_v2/swe_qa.jsonl
```

The committed index cache (`indexes/pallets_flask_full/`) lets the C2 arm run
without paying the doc-generation cost again — the harness restores it into
the checkout automatically.

---

## The bugs the rerun flushed out

Setting up the rerun surfaced three compounding MCP-server bugs that had been
silently degrading C2-style usage (semantic retrieval running BM25-only, or
against the wrong repo). All are fixed on the repowise branch
`fix/mcp-single-repo-retrieval`; without these fixes the v2 numbers would have
been dramatically worse than v1, not better.

### 1. Workspace hijack of nested repos

A repo path passed explicitly to `repowise mcp <path>` was silently re-routed
to an *enclosing workspace's* default repo whenever the path sat below a
workspace root — even when the path carries its own `.repowise/` index. Any
nested checkout (like this benchmark's `repos/pallets/flask` under a developer
machine's workspace) served the wrong index: `search_codebase` returned `[]`,
`get_answer` answered "No wiki hits" on every question.

**Fix:** subpath matching treats a self-indexed nested checkout as a distinct
repo and serves it single-repo.

### 2. First-call vector-store stall (30s) → silent BM25 degradation

Vector stores were loaded in an asyncio background task. Under the stdio
transport that task did not get resumed until the next event-loop wake-up: a
tool call waiting on `vector_store_ready` timed out after its full 30s while
the load itself (~1.5s of real work) only completed at ~31s. Every session's
first search paid a 30s stall and then ran keyword-only against an empty
placeholder store.

**Fix:** stores load inline at server startup (~1.5s, well inside MCP client
connect timeouts).

### 3. Stale store snapshot in the tool context

`RepoContext` froze the placeholder store at construction time, so even after
readiness fired, tools searched the empty placeholder and silently fell back
to BM25. **Fix:** single-repo contexts hold a delegating view that always
reads the live store.

### 4. Confidence-gate miscalibration on prose questions

`get_answer`'s identifier-citation gate treated acronyms ("JSON", "ASCII") and
capitalised nouns in paraphrased questions as code identifiers, then demanded
a hydrated-symbol match they can never satisfy — downgrading every
high-confidence answer to medium (which tells the agent to re-verify, erasing
the single-call win). **Fix:** the gate only fires on strong identifiers
(snake_case, dotted paths, digits, true CamelCase). High-confidence rate on a
16-question sample: 2/16 → 4/16, with 9/16 synthesizing an answer.

---

## Results (24 paired tasks — run stopped early)

| Metric (mean/task) | C0 bare | C2 repowise | Δ v2 | v1 published Δ |
|---|---:|---:|---:|---:|
| Tool calls | 8.38 | 5.67 | **−32%** | −49% |
| Files read | 2.00 | 0.92 | **−54%** | −89% |
| Wall clock | 54.3s | 46.5s | **−14%** | −19% |
| Cost | $0.1611 | $0.2082 | **+29%** | −36% |
| Judge score (0–10) | 8.75 | 8.53 | ≈parity | parity |

C2 cheaper on 3/24 pairs, faster on 10/24. C2 repowise tool mix:
`get_symbol` ×21, `get_answer` ×6, `get_context` ×6, `search_codebase` ×2.

### Why cost flipped relative to v1

1. **The baseline changed under us.** In v1, ~30% of C0's dollars went to
   `Agent` subagent dispatches on hard tasks — the main spend C2's
   single-call answers eliminated. In this run, **C0 dispatched zero
   subagents across all 48 rows**; the current agent runtime greps/reads
   directly with strong prompt caching, making raw exploration much cheaper
   than in May.
2. **C2's fixed overhead is now the whole delta.** C2 writes +14.6k more
   cache tokens per task than C0 (mean 29.1k vs 14.4k — the 9 MCP tool
   schemas plus the managed CLAUDE.md), ≈ $0.05/task at sonnet cache-write
   pricing — almost exactly the observed +$0.047/task gap. The navigation
   savings are real but cannot amortize the schema tax on a ~3-turn task.

**Takeaway:** the navigation and latency claims reproduce directionally
(−32% tool calls, −54% file reads, −14% wall, quality at parity); the
per-task cost claim does not reproduce on the current agent runtime and
should be re-scoped. The actionable product lever is shrinking the per-task
MCP overhead (leaner/fewer tool schemas, compact agent mode, terser managed
CLAUDE.md) — a rerun is planned after that work.

### Note on Distill

This benchmark's task shape (read-only Q&A: `Read/Grep/Glob` + MCP) cannot
exercise the Distill command path — no Bash, so nothing runs
`repowise distill <cmd>` and no rewrite hook fires. MCP response budgeting
never triggered either (flask responses fit the 8k budget; 0 omission events
across the scanned streams). Distill's measured savings live in
[docs/DISTILL.md](https://github.com/repowise-dev/repowise/blob/main/docs/DISTILL.md);
an agent-level Distill benchmark needs a bug-fix task shape (SWE-bench-style,
Bash enabled) and is tracked as follow-up work.

---

## Raw data

- Paired rows: `results/swe_qa_flask48_v2/swe_qa.jsonl` (48 rows / 24 pairs)
- Raw agent streams: `logs/swe_qa_flask48_v2/raw_outputs/`
- Session notes + rerun playbook: `local-stash/swe-qa-rerun/` (local)
