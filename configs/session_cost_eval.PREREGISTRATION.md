# Session-cost eval: pre-registration

> **SUPERSEDED 2026-08-10, before any cell ran.** The live pre-registration is
> sections 1 and 2 of
> `local-stash/competitive-proof/50-results/session-cost-eval/RESULT.md`, which
> pins cell A to `Textualize/rich`, the model to `claude-sonnet-5`, and adds the
> M1 mechanism check. The predictions P1 to P6 are identical in both files and
> were written before any spend. Kept rather than deleted so the two can be
> diffed; nothing was run against this version.

**Written 2026-08-10, before any agent cell ran.** Amendments are appended with a
date and never overwrite the original text.

Brief: `local-stash/competitive-proof/40-plan/SESSION_COST_EVAL_PROMPT.md`.
Deliverable: `local-stash/competitive-proof/50-results/session-cost-eval/RESULT.md`.

**INTERNAL. No number from this run reaches `docs/BENCHMARKS.md`, the README or a
PR body.**

---

## 1. The estimand

**Total billed tokens for one continuous multi-task agent session**, per arm,
against a bare control on the same repo and the same ordered task list.

This is deliberately not the estimand of any prior run in this workstream. Every
benchmark we own measures one question per cell, which pays the resident costs
(the `CLAUDE.md` block, the MCP schema) once. A long session pays them on every
API call. The local ledger measured that amplification at **50.4x** at a median
299 API calls per session, so a per-question result that looks neutral can be
badly negative per session.

Primary metric: **total billed tokens per session** =
`input + output + cache_read_input_tokens + cache_creation_input_tokens`,
summed across every model in `result.modelUsage`, with the four components
reported separately as well as the total.

Secondary, labelled exploratory: output tokens; turns and tool calls to
completion; wall clock including hook time; task completion.

Quality is **declared underpowered up front** and is not a metric of this run.
The paired-delta sd on this instrument measured 2.23, so n for a 0.50 effect is
about 156 pairs. At 2 cells this run cannot resolve it and will not pretend to.

## 2. Binary under test, and a deviation from the brief that is declared here

The brief's ledger figures (a 10,350-char resident block; `Bash|PowerShell` at
51% of hook invocations) were measured on the build **before PR #1382**, which
merged to `main` as `5a7a81e0` and both slimmed the resident block and dropped
`Bash|PowerShell` from the augment `PostToolUse` matcher.

**This run measures `origin/main` as shipped**, not the pre-#1382 build the
complaint came from. Measuring a build we have already replaced would answer a
question about the past.

The consequence is declared rather than discovered: **predictions 1 and 2 below
were calibrated on the pre-#1382 build and are scored against the current one.**
If prediction 1 fails, "#1382 already fixed it" is a live explanation and must be
tested against the measured block size rather than assumed. The block size
actually emitted by the binary under test is recorded in the provenance block.

## 3. Design

**Measure sessions, not questions.** One repo, one continuous agent session, a
fixed ordered task list worked start to finish. Resident cost becomes visible,
hooks fire hundreds of times instead of twice, and the shape reproduces the
complaint.

### Cells

| Cell | Repo | Pinned commit | Language | Why |
|---|---|---|---|---|
| A | `pydantic/pydantic` | `a20c0ee2` | Python | 402 source files, runnable pytest suite, never used in this bake-off's dev or sealed splits |
| B | `colinhacks/zod` | `bbc68f99` | TypeScript | language parity; our JS/TS symbol extraction is known weaker (findings A7 / A35). Zero ContextBench instances |

Held out and untouched: the sealed 42, mui, and repowise itself.

**Cell B runs only if cell A's measured cost leaves room under the ceiling.**
Per the brief, cutting cell B is preferred to cutting any arm, because losing
`rw-block` or `rw-mcp` destroys the ablation that is the point of the run.

### Arms

| Arm | MCP | Hooks | CLAUDE.md / AGENTS.md block |
|---|:--:|:--:|:--:|
| `c0-bare` | no | no | no |
| `rw-full` | yes | yes | yes |
| `rw-mcp` | yes | no | no |
| `rw-block` | no | no | yes |
| `rw-hooks` | no | yes | no |
| `codegraph` | yes | no | no |

Two comparisons carry the run: **`rw-mcp` vs `codegraph`** (clean tool quality,
because CodeGraph ships no hooks and no resident block) and **`rw-full` vs
`rw-mcp`** (the price of everything we add on top).

On Codex, `rw-hooks` is a **positive control on the harness**, not a treatment:
Codex cannot honour `updatedToolOutput`, has no `Read`/`Grep`/`Glob` tools, and
has no `PostToolUseFailure`, so the surfaces carrying 84% of the measured token
credit have nothing to attach to. If it differs from `c0-bare` by more than
noise, something is firing that we did not think could fire.

### Harnesses, reported separately and never pooled (finding E14)

| Harness | Question it answers |
|---|---|
| Codex | Is the MCP tool worth its cost when it is actually used? What does the resident `AGENTS.md` block cost over a long session? Adoption is 15/15, so the treatment is exercised |
| Claude Code | Is the tool worth its cost when used, and do hooks help or hurt? Only harness where the replacing surfaces work, and almost certainly the complaint's harness |

### Conditions

**Enforced** is the default for the Claude Code arms, by **PreToolUse guidance**
(`harness/force_tool_use.py --mode pre-guide`), measured in
`50-results/layerb-opus-preguide/RESULT.md` at 6 of 6 adoption at or below
baseline token cost. The `Stop` block is **forbidden** in this run: it works and
it costs +61% to +127% output tokens, which corrupts the only column this run can
resolve. A prompt mandate is used as a free helper in the task preamble and is
**not** treated as the enforcement mechanism, because it is the one mechanism
already measured to fail under Opus (2 of 6, and both were cells that adopt
unprompted).

Enforcement applies to **every MCP arm identically**, `codegraph` included, with
the same wording naming that arm's own tools. Enforcing for ourselves and not for
a competitor is a handicap, not a measurement.

**Unenforced**: `c0-bare` and `rw-full` also run without any enforcement, on
Claude Code only. Two extra sessions per cell. Reported as their own condition
and never averaged with the enforced cells. This pair is the run's most direct
answer to the complaint: it measures the user who pays for the resident block and
the hooks on every call and may never get the tool benefit, because the harness
defers MCP schemas.

Adoption is a **gate**, not a result: every cell asserts the tool was called and
records how many times, and every result is split by whether the tool was called.

### Task sets

8 to 12 ordered tasks per cell, worked in ONE session, mixed as:

- 2 to 3 retrieval-heavy (where should X change, what calls Y)
- 2 to 3 architecture questions (how does subsystem Z work)
- 3 to 4 edits with a test oracle (fix this bug, add this small feature)
- 1 to 2 pure mechanical edits where repowise should not help at all

The last group is the **honesty control**. If repowise "helps" on a task with no
retrieval component, the instrument is measuring position or variance.

Tasks are authored from the repo's own issue tracker and git history, **never by
looking at what repowise is good at**, and are committed before the first run.
This is the overfitting protocol applied to a task set.

## 4. Pre-registered predictions

Scored held or failed in the result file. Written before any cell ran.

1. The `CLAUDE.md` / `AGENTS.md` block is the dominant cost, larger than hooks
   and MCP combined.
2. Hooks are roughly break-even on tokens and clearly negative on wall clock.
3. MCP is barely called under Claude Code in the unenforced condition, and
   PreToolUse guidance lifts it close to Codex levels in the enforced one.
4. `rw-full` costs **more** total tokens than `c0-bare` in a long session under
   Claude Code, in both conditions.
5. On Codex, `rw-hooks` is indistinguishable from `c0-bare`.
6. Enforcement makes repowise look **worse** on tokens, not better, reproducing
   the preguide result's sign flip at session scale.

If the run agrees, we have a mechanism and a fix list. If it disagrees, one of
the two instruments is broken, and finding that out is worth the whole run.

## 5. Stopping and validity rules

- **An arm that fails its configuration gate is rebuilt, not graded.** The gate
  is in section 6.
- **A cell whose tool was never called gets no efficiency claim**, only an
  adoption row. An unexercised arm is a bare agent with extra steps
  (standing rule 15).
- **Every arm runs in its own process with a cold cache**, and cache read and
  creation are reported per arm, because prompt caching has a 5-minute TTL and an
  arm's cost otherwise depends on how many other arms ran between its cells
  (finding E10, worth 39 points on a published dollar delta).
- **Never launch with `Start-Process`**, and no timed work runs while another
  process pool is alive (finding E1, 65% inflation).
- **Cost cell A end to end and write the number down before cell B or the
  remaining arms run.** A run that would breach the standing $350 ceiling stops
  and asks rather than reporting the overage afterwards.
- Invalidated runs are never deleted; they get an `INVALID:` banner and the
  numbers stay visible.

## 6. The configuration gate, run before any cell is graded

Each arm asserts what it is from evidence, not from its config file. A flag's
name is not a measurement: `--settings` merged rather than replaced, and
`--ignore-user-config` left `$CODEX_HOME/hooks.json` firing seven times with the
flag set.

- `c0-bare`, `rw-mcp`, `rw-block`, `codegraph`: **zero** rows written to
  `.repowise/sessions/sessions.db` for that session.
- `rw-hooks`, `rw-full`: **hundreds** of rows and a non-empty `hook_runs` table.
  On Claude Code, at least one `skeleton_served` or `digest_served` row, or the
  replacing surfaces are not actually on.
- `rw-block`, `rw-full`: the block is present in the file, and the arm's first
  API call shows the expected prefix size.
- `c0-bare`, `rw-mcp`, `rw-hooks`, `codegraph`: no `## Codebase Intelligence for`
  heading anywhere the agent can see.
- Every MCP arm: `isError` false, served tool list recorded, response size
  recorded per call.
- **Embedder live and `index_vector_dim: 1536`** on every repowise row. D13 is
  the reason.
- **Positive control on every detector before any zero is recorded.** Three
  plausible zeroes came from broken detectors in one afternoon once.

Standing rule 5 applies in full: `--no-prose --embedder openai
--max-file-pages 0 --no-workspace --no-editor-setup --yes`, plus
`REPOWISE_SKIP_EDITOR_SETUP=1` and `DO_NOT_TRACK=1`. Note the tension:
`--no-editor-setup` is what keeps hooks off, so the **hooks-on arms need a
deliberate, scoped install** into the pinned `CLAUDE_CONFIG_DIR` rather than a
plain `init`, and that install is undone afterwards. There is ONE global
`repowise` MCP key and an unguarded `init` repoints the operator's editor.

## 7. What this run will not do

- Put any number in `docs/BENCHMARKS.md`, the README, or a PR body.
- Tune anything against these task sets.
- Enforce with a `Stop` block.
- Enforce for repowise and not for CodeGraph.
- Pool the enforced and unenforced conditions, the two harnesses, or the
  repowise-flavoured arms.
- Touch the sealed 42 or mui.
- Write a ceiling as a forecast.
