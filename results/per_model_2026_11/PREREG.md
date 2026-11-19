# Per-model savings bench: pre-registration

Written 2026-11-18 before the full grid. A 3-question plumbing pilot (Sonnet 5.5, one run per arm)
ran before this file was frozen; its runs live in `runs/pilot_sonnet` and are excluded from results.
The zero-tool memory probe (`probe/memory`) also ran before freezing. Nothing below changes after
the first grid run; any later change is listed under "Amendments" with its reason and time.

## Question

Does an agent with Repowise (core four tools, always-load on) do the same code-understanding work
with fewer output tokens, tool calls, files opened and less wall clock than the same agent bare, at
equal answer quality, and how does that differ by model?

## Repositories (held out, unmemorised)

| Repo | Language | Commit | Created | Code lines | Files |
|---|---|---|---|---|---|
| thruwire/foreman | Python | c3142ef636617f32f772140125aa310d81ceece9 | 2026-09-17 | 16.3k | 145 |
| okf-memory/okf-agent-memory | Go | 4cbbf831224ec76bc450522faac9249905fdc51a | 2026-09-05 | 27.4k | 282 |
| elstongun/leviathan | Rust | 21563697e1a9c99f31b28108ed332649044d066a | 2026-10-05 | 16.9k | 82 |

All three were created after every tested model's training cutoff. Memory check (zero tools, no
checkout, one locate question per repo, 7 models): no model named any distinctive gold identifier
(0 of 21; the only hit is the generic file name `lock.go`, guessed by 4 models). Never used in any
earlier repowise probe, gate or tuning set.

Indexes: `repowise init --yes --no-prose --commit-limit 1000` (keyless, full clone) on Modal
(profile content, origin/main 061b2bc79, repowise 0.61.0+), `.repowise/` copied into the clone,
`.repowise/` added to `.git/info/exclude` as `init` does, `mcp.always_load: true` in its config.

## Questions

13 per repository, 39 in all (`questions/<repo>.json`): about 4 locate, 5 flow, 4 impact per repo.
Written by a separate agent from the code before any run; every gold item verified at a cited line.
Three gold items (LEV-05, LEV-06, LEV-07) that matched the question's own text were replaced or
dropped before any run (recorded in the file's `edits` field).

## Models

Claude Code 2.1.288 headless (`claude -p`, stream-json) on the subscription, model default effort:
`claude-fable-5-1` (alias fable), `claude-opus-5-5` (opus), `claude-sonnet-5-5` (sonnet),
`claude-haiku-4-5` (haiku), `claude-opus-4-8`, `claude-opus-4-6`, `claude-sonnet-4-6`.
All seven answered the availability probe on the subscription.

Codex CLI 0.154.0 on the ChatGPT subscription, `gpt-5.6-terra` (the account rejects `gpt-5.6-sol`),
if the arm can be isolated from the operator's Codex hooks and MCP servers; otherwise not run, and
the table says so. No API-key spend is planned; no open-weight model is planned.

## Arms

| Arm | cwd | Tools | Prompt |
|---|---|---|---|
| bare | clean clone, no `.repowise` | built-ins, no MCP (`--strict-mcp-config`, empty config) | base prompt |
| repowise | indexed clone | built-ins plus `get_answer`, `get_context`, `search_codebase`, `get_symbol` (`--tools` core four), keyless, answer cache off, `mcp.always_load: true` | base prompt plus one sentence: "Use the repowise tools (get_answer, get_context, search_codebase, get_symbol) to find the code, and do not re-read code a tool result already delivered." |

Both arms: `--setting-sources project` (operator hooks, plugins, user settings off),
`--disable-slash-commands`, `--no-session-persistence`, no WebFetch/WebSearch, read-only
permissions (`dontAsk` with Read, Grep, Glob, Task and read-only shell commands), `--max-turns 60`,
900 s timeout. Fresh session per run: no seeded history, no resume (no double-counted seed).
Arm order per question drawn from seed 20261118. Two sessions at a time (amended to three, A2).

## n

3 runs per (model, question, arm). Per model: 39 questions x 3 runs x 2 arms = 234 sessions.

## Metrics (per run)

- Output tokens: sum of `outputTokens` over the result's `modelUsage` (every model in the session,
  sub-agents and side calls included). Cross-checked against per-message usage de-duplicated by id.
- Tool calls: every `tool_use` in the stream, sub-agent calls included.
- Files opened: distinct repo-relative paths over Read calls plus file arguments of shell read
  commands (cat, head, tail, sed, nl, awk, less, more).
- Wall clock: the result's `duration_ms`.
- Answer quality: deterministic. Share of the question's `must` regexes matched in the ANSWER
  section (case-insensitive); correct = all matched. No model judge.
- Also recorded, not headline: total tokens (input + cache write + cache read + output), notional
  list-price cost, Repowise calls, first-turn context.

## Analysis

Per model: per-arm means over questions (each question's runs averaged first); paired difference
repowise minus bare per question; mean difference and percent of the bare mean with a 95% bootstrap
interval over questions (10,000 resamples); "leaner on k of n" = questions whose mean output is
lower with Repowise; correct runs per arm; share of repowise-arm runs that called a Repowise tool.
Failed runs (non-zero exit, timeout, server not connected) are retried; a run that fails three times
is reported as a failure row, never dropped.

## Table rows (exactly these, one per model, every model reported whatever the result)

| Model | always_load | Repowise used | Output tokens bare / repowise / change [95% CI] | Tool calls | Files opened | Wall clock | Correct bare / repowise | Leaner on |

Rows: Fable 5.1, Opus 5.5, Sonnet 5.5, Opus 4.8, Opus 4.6, Sonnet 4.6, Haiku 4.5, and Codex
gpt-5.6-terra (or a row saying not run, with the reason). Per-repo and per-kind breakdowns go in the
detail tables.

## Amendments

A1, 2026-11-18 ~23:58 IST, before any grid run. Reason: subscription capacity (the bench shares
limits with build sessions). Questions cut to 8 per repository, 24 in all, by a fixed rule that keeps
the locate/flow/impact mix: per repository the quota is 8 x (category count / 13) with largest
remainder, ties broken locate, flow, impact; then the first k of each category in file order. Result
3 locate, 3 flow, 2 impact per repo; ids in `questions/grid_set.json`. The rule does not look at the
pilot. n stays 3: 7 x 24 x 2 x 3 = 1,008 sessions.

A2, same time. Model order: haiku-4-5, sonnet-4-6, opus-4-8, sonnet 5.5, opus 5.5, fable 5.1,
opus-4-6 last (reported as "not run: capacity" if limits stop it). Three sessions at a time. A run
that fails on a usage or rate limit stops the driver; it is never counted as a result and is rerun
after the reset. Codex runs with an isolated CODEX_HOME (auth only, no hooks, rules, skills or MCP
servers from the operator's config), one session at a time, in parallel with the Claude rows.

A3, 2026-11-19 ~00:20 IST. Codex row: not run. With an isolated CODEX_HOME (auth plus model only) the
Windows elevated sandbox helper failed to launch and, in both the isolated and the operator's own
home, `codex exec` (CLI 0.154.0, ChatGPT login, gpt-5.6-terra) reported no tools from the stdio
Repowise server although the server initialised (smoke runs in `runs/codex-smoke`). With no usable
Repowise arm there is nothing to compare; the table carries a "not run" row with this reason.
The copied auth file was deleted after the check.


## Amendment A4 (2026-11-19 02:10, founder, before these rows ran beyond 30 sessions)
Reason: subscription capacity shared with build work. Fable 5.1 and Opus 4.6 become "not run: capacity" rows.
Remaining rows (Opus 4.8, Sonnet 5.5, Opus 5.5) run 2 runs per arm (r1, r2) on the same 24 questions:
96 sessions per row. Opus 4.8 already has r3 on 7 questions; the table uses r1 and r2 only for every row
of this amendment so all cells have equal n, and reports n per row. Haiku 4.5 and Sonnet 4.6 keep n=3.
Not decided from results: Opus 4.8 partial numbers were seen, but the cut applies to all remaining rows equally.

## Amendment A5 (2026-11-19, after the grid, before regrading)
Reason: gold error found in the miss review (MISSES.md). The LEV-02 item `half_life` used the regex
`\bhalf_life\s*\(`, which requires a "(" after the name. Answers that name the correct method as
`Settings::half_life` without parentheses (haiku-4-5 rw r3) were graded wrong. Change: that one regex
becomes `\bhalf_life\b`. No other gold item changes, including the private-helper items the review
also discussed (OKF-07 `saveConceptLocked`, FOR-11 `_assess_event`), which stay as registered.
Both arms of every model are regraded with the same gold. RESULTS.md reports correctness before and
after A5 for every row. Grading stays deterministic (regex, no judge); no run is repeated.
