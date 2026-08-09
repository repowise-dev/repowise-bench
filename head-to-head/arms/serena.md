# Serena

**A different category of tool, and it carries no retrieval row because of it.**

| | |
|---|---|
| What it is | A language-server wrapper: go to definition, find references, symbol overview |
| Index | **none**. Nothing persistent is built; the language server answers on demand |
| Tools served | **29** |
| Tools allowlisted | **10 of 29**, every exclusion named below |
| Coverage, sealed 42 | **no row.** It serves no retrieval-by-question tool at all, see below |
| Agent loop, Codex | **-14.8%** output tokens vs bare, but **10.1 tool calls** against the bare agent's 7.2 · 3rd of five |
| Build cost | **0.0s** everywhere |

## Why it has no coverage row

**Serena has no retrieval-by-question tool at all.** `search_for_pattern` is the
closest thing it offers, and it takes a substring, not a question.

That asymmetry is the finding rather than a rigging. A single-shot agent-free
retrieval probe is the wrong instrument for a language-server wrapper: the number
it produced would measure our harness, not Serena. So it is excluded from the
Layer A table by name, with this reason, rather than scored and quietly placed
last.

Its index is still built (there is nothing to build), so it can be named
explicitly on any run that wants it.

## What it is good at

**Symbolic navigation that nothing else here does.** Go to definition, find
implementations, find referencing symbols, per-file diagnostics, all from a real
language server rather than a heuristic graph. On "where is this exact symbol
defined and who calls it", it is answering from the compiler's own view.

**Zero index cost.** No build, no disk, no staleness. On a repository changing
under you, that is a real architectural advantage that a bake-off structured
around pinned commits cannot show.

**It does reduce agent work.** -14.8% output tokens against a bare agent on the
48-question Codex run, p < 0.0001, third in the field.

## What it is bad at

**It is busier, not leaner.** It reaches that -14.8% while calling tools **10.1
times per question against the bare agent's 7.2**, a 42% increase. Every other
tool in the field reduces both. That is the shape of navigation without
retrieval: it answers each hop correctly and there are many hops.

**Adoption under Claude Code**: 4 of 15 questions.

## Setup traps

**`activate_project` is required even with `--project` on the command line.**
Without it every tool answers "No active project" and the arm scores a clean
0.000. It is run in the arm's `activate` block, before anything scored.

## Exclusions, and one of them is a fairness defect we are keeping on the record

19 of 29 tools excluded. The routine ones:

| excluded | why |
|---|---|
| `create_text_file`, `insert_after_symbol`, `insert_before_symbol`, `replace_content`, `replace_in_files`, `replace_symbol_body`, `rename_symbol`, `safe_delete_symbol` | mutate source. No arm is given Edit or Write, so these cannot be exercised and allowing them would break read-only parity |
| `write_memory`, `delete_memory`, `edit_memory`, `rename_memory` | mutate Serena's own memory store, which **persists across cells**. A cell that writes a memory changes the next cell's starting state |
| `read_memory`, `list_memories` | read that same cross-cell store, which nothing legitimately put anything into |
| `activate_project` | the harness runs it before the cell |
| `onboarding`, `initial_instructions`, `get_current_config` | session setup and self-description, not retrieval |

`read_file`, `list_dir` and `find_file` **are** allowlisted although Read, Glob
and Grep already cover them. They are what Serena ships, and duplicating a
capability the agent already has is not an advantage. Dropping them would be us
deciding which of a competitor's tools count.

### `execute_shell_command`: excluded on a false premise

The original reason, kept verbatim in the arm definition because **a competitor
handicap that is quietly edited out is worse than one that is recorded**:

> "a shell. Bash is denied to every arm (repo escape, and it can read the
> benchmark's own answer key); one arm reaching a shell through its server is not
> the same experiment."

**Bash is not denied.** Measured in the streams: the bare control issued 11 Bash
calls in one run and 8 in another; repowise issued 0 and 1. On the Go corpus both
arms lean on it harder still, and Bash is the first tool called on most cells.

Nor was the answer key reachable. An audit walked all 943 tool calls across both
runs and all six arms and found **0** naming the benchmark data, results or logs;
all 41 Bash calls printed in full are greps, finds and gits inside the arm's own
tree.

**So Serena was denied a tool on the grounds that every arm lacked something every
arm in fact had.** That is the allowlist rule failing in the direction this bench
keeps warning about: against a competitor, in our favour. Same defect as giving
Graphify 1 tool of the 10 it serves.

**It is still excluded, and the reason is now honest rather than wrong:** no
Serena number has been produced since the correction, so enabling it today would
put a fresh Serena row beside months-old rows for every other competitor. **Any
published Serena row carries this note, and a Serena rerun must enable it.**

## Where its numbers are

- Agent loop: [`../../results/bakeoff_2026_08/rung9/`](../../results/bakeoff_2026_08/rung9/)
- The fairness correction: `50-results/layerb-payload-rerun/RESULT.md` section 4
- Arm definition: [`../../configs/arms.yaml`](../../configs/arms.yaml), `serena`
