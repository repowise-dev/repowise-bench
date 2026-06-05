# SWE-QA flask48 — v2 rerun (2026-06)

A full re-run of the published [flask48 benchmark](BENCHMARK_REPORT_FLASK48.md)
against the current repowise codebase (post-Distill, post-perf-overhaul),
with the documentation index regenerated on OpenAI `gpt-5.4-nano` instead of
Gemini. Same 48 paired tasks, same C0/C2 design, same model family for agent
and judge.

> **Status: in progress.** Interim numbers below are from the first completed
> pairs; the final table will replace them when all 48 pairs land.

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

## Interim results (first 4 pairs — NOT final)

| Metric (mean/task) | C0 bare | C2 repowise | Δ | v1 published Δ |
|---|---:|---:|---:|---:|
| Tool calls | 12.0 | 4.0 | **−67%** | −49% |
| Files read | 2.25 | 0.50 | **−78%** | −89% |
| Wall clock | 63.9s | 34.4s | **−46%** | −19% |
| Cost | $0.173 | $0.176 | +1% | −36% |
| Judge score (0-10) | 8.80 | 8.60 | ≈parity | parity |

Early observations:

- Navigation efficiency (tool calls / file reads / wall) is at or beyond the
  published claims.
- The cost delta is flat on the early (easy) slice: C2 carries a fixed
  per-task overhead (~11k cache-write tokens for MCP tool schemas + managed
  CLAUDE.md) that easy 2-call C0 tasks don't give it room to amortize. In v1
  the cost win was concentrated in the hard tail where C0 dispatches `Agent`
  subagents (~30% of C0 dollars); the verdict on cost waits for the full 48.
- C0 explores harder than in v1 (12 vs 7.4 mean tool calls) — newer agent
  defaults search more aggressively, which raises the baseline C2 is measured
  against on every metric except cost-per-call.

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

## Final results

*Pending — to be filled when the run completes.*
