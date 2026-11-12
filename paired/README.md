# Paired agent sessions

The same task, run by the same agent with and without Repowise, back to back,
graded blind. This is the harness behind the "real agent sessions" table.

```
python paired/run_paired.py --tasks paired/tasks/table2.json --repos-dir <clones> --out runs/t2 --arms bare,rw --reps 1
python paired/judge.py      --tasks paired/tasks/table2.json --repos-dir <clones> --out runs/t2
python paired/analyze.py    runs/t2 > runs/t2/analysis.md
```

## Arms

| arm | MCP server | agent file |
|---|---|---|
| `bare` | none | none |
| `rw` | Repowise core tools only: `get_answer`, `get_context`, `search_codebase`, `get_symbol` | the steer in [STEER.md](STEER.md) |
| `rw-ns` | the same server | none (tracks adoption without the steer; internal) |

The server runs keyless (`repowise init --no-prose` index, no provider key, answer
cache off), so every number here is what a user gets without a model key.

## What makes a run fair

- **Isolated config.** Every arm gets its own `CLAUDE_CONFIG_DIR`, so the
  operator's hooks, plugins, skills and user `CLAUDE.md` never reach either arm.
- **Realistic prefix** (`--prefix realistic`, the default). All built-in tools,
  plus a per-repository conversation history built once from the task file's
  `seed_prompt` and resumed with `--resume <seed> --fork-session` by every arm.
  First-turn context is about 60k tokens, as in a working session. `--prefix
  minimal` restricts built-ins to Read, Grep, Glob and Bash with no history.
- **Warm cache, back to back.** Each arm's prefix is warmed once before its first
  measured run (not counted), and the arms of a task run back to back in an order
  drawn from `--seed`, so no arm pays to warm the cache for the other.
- **Cold starts count.** A run whose first tool call waits for the server is kept.
- **Edit tasks** run in a fresh git worktree at the task's commit with Edit and
  Write allowed; `git diff` is kept for the judge.
- **Per-run budget** via `--max-budget-usd` (`--run-budget`, default 4), and a
  total cap (`--cap-usd`) checked before every run. Resumable: a run with a
  `meta.json` is never repeated.

## Metrics (per run, `meta.json`)

Cost (`total_cost_usd`), tokens from `modelUsage` (fresh input + cache write +
cache read + output) with a per-message cross-check de-duplicated by message id,
output tokens, API calls, tool calls, files opened (distinct Read paths), wall
time, Repowise calls by tool, whether the first tool call was Repowise, and the
answer. `analyze.py` reports per-arm means and medians, paired differences
against `bare` with a bootstrap interval, per-tier sums, the Repowise tool-use
rate of every cell, and correctness.

## Judge

One blind judge per task (`judge.py`): a Claude Code session in the task's
checkout with Read, Grep and Glob, so it verifies claims against the code. It
sees the task, the gold notes and every arm's answer (and diff) under opaque
ids; arm names, tool traces and confidence lines are removed, and the id map is
written outside the checkout.

## Other harnesses

- **opencode, open-weight models via OpenRouter** (`--harness opencode --model
  openrouter/<model>`): same tasks and arms, the steer passed as an instruction
  file, the key read from the keys file into the opencode process only. Set
  `REPOWISE_ROOT` (a repowise checkout) and `REPOWISE_EXE` (its `repowise`
  console script) for the shared opencode parser's import checks. No seeded
  history: the prefix is opencode's own.
- **Codex CLI** (`--harness codex`): reserved, not wired yet.

## Task files

`<clones>/<name>` is a full clone at the commit the task file pins, with its `repowise init --no-prose` index. `tasks/validation.json` (six read tasks, minimal prefix, the shape of the first
paired pilot) and `tasks/table2.json` (three repository sizes; three edit tasks
per repository as the primary tier, plus flow, exploration and locate). Every
task carries its gold notes, checked against the pinned clone before any run.
