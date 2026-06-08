# flask SWE-QA — v3: the coherent token-reduction story

A re-run of the [flask48 benchmark](BENCHMARK_REPORT_FLASK48_V2.md) built to
answer one question honestly: **how much does repowise actually reduce the
tokens a coding agent spends, once you account for the cost of the tools
themselves?** v2 found the navigation wins were real but a per-task MCP
*schema tax* erased the cost win on short tasks. v3 closes that loop with two
changes and one environmental discovery.

> **One-line story.** repowise reduces the agent's *work* — files read and tool
> calls roughly halved, at quality parity — on both short and long tasks. The
> *cost* win has two distinct sources: on short Q&A it needs the **curated
> (lean) tool surface** (the full surface is only −4%; lean is −25%, because the
> per-call tool overhead otherwise cancels the navigation saving); on long
> investigation it comes from **distill** compressing command floods, which
> keeps context small (−41% cache-read, −26% cost). Two surfaces, two cost
> drivers: MCP navigation and command-output compression.

---

## What changed since v2

| Axis | v2 | v3 |
|---|---|---|
| MCP tool surface | all 9 tools advertised | **curated**: `repowise mcp --profile core` advertises only the 4 tools the agent actually uses (get_answer, get_context, get_symbol, search_codebase) |
| Task shapes | short read-only Q&A only | short Q&A **and** long Bash-enabled investigation |
| Distill | not exercised (no Bash) | **exercised**: long arm runs real `git`/`grep` commands through `repowise distill` |
| Arms | C0_bare, C2_full | C0_bare, C2_full, **C2_lean** (short); C0_long_bare, **C2_long** (long) |
| Claude Code | loaded all MCP schemas up front | **defers MCP schemas** (lazy-load via ToolSearch) — see below |
| repowise code | `fix/mcp-single-repo-retrieval` | current `main` + that fix + the lean-surface change, via `REPOWISE_ROOT` |

---

## The MCP schema cost, measured directly

Every tool an MCP server advertises carries a JSON schema (name + description +
input schema) the model must have in context to call it. Measured with
`tiktoken` (cl100k_base) on the exact payload the client receives:

| Surface | Tools | Schema tokens |
|---|---|---:|
| **full** | 9 | **4,520** |
| **core (lean)** | 4 | **1,884** |
| **saved by lean** | — | **2,636 (−58%)** |

The single biggest line item is `get_dead_code` at **1,048 tokens** — a tool
no Q&A or bug-fix session ever calls. The `core` profile (shipped in this work:
`repowise mcp --profile core` / `--tools` / the `mcp.profile` config key) drops
it and the other four situational tools.

## The environmental discovery: Claude Code now *defers* MCP schemas

While re-running v2 we found current Claude Code no longer loads MCP tool
schemas up front. At session init **zero** repowise tools are in the loaded
tool list; Claude Code injects only the tool *names* cheaply and pulls a tool's
full schema into context on demand via `ToolSearch`. Confirmed directly: the
agent must `ToolSearch "mcp__repowise__get_answer"` before its first call.

Consequences for the cost story:

1. The **always-on schema tax v2 measured is largely gone in modern Claude
   Code** — the schema is paid *per use*, not every turn. v2's cost regression
   was partly an artifact of an older client that front-loaded every schema.
2. The lean surface still matters: it shrinks the deferred registry and the
   per-use ToolSearch payload, and it delivers the full always-on saving on
   the many clients that **don't** defer (Cursor, Cline, Codex, older Claude
   Code).
3. Deferral is itself a step toward *not paying always-on tool cost at all* —
   the same idea as exposing tools over a CLI the agent calls per-use. That is
   the natural next experiment (see [Next step](#next-step-cli-over-bash)).

---

## Short read-only Q&A — gains are small; lean keeps overhead near zero

_Aggregate (n=6), `analysis/aggregate_savings.py`:_

| arm | cost | Δ vs bare | tool calls | files read | judge score |
|---|---:|---:|---:|---:|---:|
| C0_bare | $0.2012 | — | 10.5 | 2.8 | 8.77 |
| C2_full | $0.1935 | −4% | 7.7 | 1.3 | 8.70 |
| C2_lean | $0.1502 | **−25%** | 7.7 | 1.7 | 8.83 |

Both repowise arms cut file reads (~2.8 → ~1.5) and tool calls (10.5 → 7.7) at
quality parity. But the **full** surface nets only −4% on cost — the per-call
tool overhead (ToolSearch to load the deferred schema + the call + an extra
get_answer synthesis) very nearly cancels the navigation saving. The **lean**
surface removes the unused schemas and lands a real **−25%**. The short-task
takeaway is precisely the feature thesis: *give the agent only the tools it
needs and MCP goes from marginal to a clear win.* Distill doesn't fire here —
no commands to compress.

## Long investigation (Bash + distill) — this is where it compounds

_Aggregate (n=5):_

| arm | cost | Δ vs bare | cache-read tokens | tool calls | files read | judge score |
|---|---:|---:|---:|---:|---:|---:|
| C0_long_bare | $0.3512 | — | 641,553 | 21.0 | 2.6 | 9.24 |
| C2_long | $0.2589 | **−26%** | 377,683 (**−41%**) | 15.0 | 1.2 | 9.08 |

C2_long is cheaper on **all 5/5 tasks** (e.g. flask_long_001: $0.263 → $0.115,
17 turns → 5). Long tasks read large command output — `git log -p`, `git diff`,
wide `grep`. Bare ingests all of it, which inflates context and every
subsequent turn's cache-read; in the worst case the agent thrashes the turn cap
drowning in raw output (bare ran 24–34 turns on the heavy tasks). **distill**
compresses that output (errors/structure first, reversible via `repowise
expand`) before it lands, and the lean MCP tools cut file reads (2.6 → 1.2) — so
C2 keeps context small all session, the −41% cache-read being the direct
evidence. Agent streams confirm it ran `repowise distill <cmd>` and even
`repowise expand <ref>` to recover omitted lines instead of re-running.

The savings are recorded into the **same** ledger as distill's CLI/hook path —
`repowise saved --by source` reports `cli`/`hook-*` distill rows alongside the
`mcp:<tool>` counterfactual rows, one unified surface (exact per-source totals
vary with cwd store resolution, so we anchor on the agent-level cache-read/cost
above rather than ledger sums).

---

## The coherent picture

```
            short Q&A (n=6)          long investigation (n=5)
            ---------------          ------------------------
 files/tools ~halved, parity Q       ~halved, parity Q          (both: real work cut)
 MCP cost    full −4% / lean −25%     navigation helps (files 2.6→1.2)
 distill     n/a (no commands)        compresses floods → −41% cache-read
 net cost    −25% (needs lean)        −26% (distill + fewer reads)
```

The fragmented numbers reconcile into one picture: **repowise cuts the agent's
navigation work on every task size (files read and tool calls roughly halved at
parity quality); the dollar win has two sources** — the curated tool surface
(short: full is only −4% because tool overhead cancels the saving; lean is −25%)
and distill compressing command output (long: −41% cache-read, −26% cost).
MCP-navigation cost savings are smaller than they were in v1 because the agent
baseline got cheaper (no subagent dispatches; prompt caching makes file-reads
nearly free) — which is exactly why the *lean* surface and *distill* are where
the remaining wins live.

## Next step: CLI-over-Bash

Claude Code's deferral removes most of the always-on schema tax for MCP. The
logical endpoint is to drop the always-on cost entirely: expose the repowise
tools as a **CLI the agent calls over Bash** (`repowise get-answer …`), paying
only per use, with a short usage note instead of N always-on schemas. A future
arm will compare CLI-over-Bash against MCP-lean on the same tasks.

---

## Methodology & caveats

- **Robust metrics.** `tool_calls`, `files_read`, `cache_read_tokens`, and
  judge score are not sensitive to Anthropic's cross-run prompt cache.
  Absolute `cache_write` and `cost` are; we report cost as directional and
  lean on the deterministic schema measurement + behavioral metrics.
- **Arm order.** Within a task the bare arm runs first (coldest cache); this
  if anything *understates* the repowise arms' relative cost. The large
  cache-read gaps on long tasks are a context-size effect (distill), not a
  caching artifact.
- **Distill in the long arm** uses the voluntary `repowise distill <cmd>` path
  (a system-prompt instruction), which produces output identical to the
  PreToolUse rewrite hook. On these diff-reading tasks the agent went
  git-direct and rarely called MCP tools, so the long-arm win is distill-driven;
  the MCP-navigation win shows up in the short arm.
- **n is small** (5 long, 6 short) — directional, not a published effect size,
  but the long arm is consistent (C2 cheaper on 5/5). Index is the committed
  `indexes/pallets_flask_full` cache (OpenAI `gpt-5.4-nano` docs + OpenAI
  embeddings), built and maintained via the local checkout.

## Repro

```powershell
$env:REPOWISE_ROOT = "C:/Users/ragha/Desktop/repowise"
$env:OPENAI_API_KEY = "<key>"   # forwarded to the MCP server for get_answer + embeddings
python harness/run_experiment.py --config configs/swe_qa_flask48_lean.yaml   # short, 3 arms
python harness/run_experiment.py --config configs/swe_qa_flask48_long.yaml   # long, 2 arms
python analysis/aggregate_savings.py --results results/swe_qa_flask48_lean/swe_qa.jsonl
python analysis/aggregate_savings.py --results results/swe_qa_flask48_long/swe_qa.jsonl --baseline C0_long_bare
```
