# Pre-registration: Claude Code WITH HOOKS, on Opus, django

**Committed before any cell of this run ran and before a cent was spent on it.**
The comparator already exists and is clean, which is why this is cheap:
`results/bakeoff_2026_08/rung6/layerb_opus_stratified_django/swe_qa.jsonl`, 90
cells of the same 15 stratified django questions on `claude-opus-5`, with
`hook_injections` empty on all 94 rows. This run is a **paired re-run against
those exact cells**.

| # | step | config | cells | cost |
|---|---|---|---:|---:|
| 1 | the **gate**, does the hook actually fire | `configs/layerb_opus_hooks_smoke_django.yaml` | 6 | ~$1.83 |
| 2 | the **run**, 3 arms x 15 instances | `configs/layerb_opus_hooks_django.yaml` | 45 | ~$13.7 |

Step 1 gates step 2. No cell of step 2 runs until step 1 passes.

---

## 0. THE FRAMING CORRECTION IS THE DESIGN, SO IT COMES FIRST

**Hooks do not enforce adoption. They make adoption unnecessary.**

`plugins/claude-code/hooks/hooks.json` fires `repowise-augment` on
`SessionStart` and on `PostToolUse`. It **injects context**. It does not make
the agent call an MCP tool. So an agent can stay on Read and Grep, never issue
a `ToolSearch`, never touch the server, and still receive repowise content.

Four consequences, all binding on how this run is read:

1. **Adoption is NOT the outcome variable on the hooks arm.** It is a category
   error there. Adoption may sit at the baseline's 7 of 15 while context lands
   on 15 of 15, and **that is the finding**, not a null.
2. **The outcome is output tokens against the bare control, plus quality**,
   with `hook_injections` per cell as its own column: context DELIVERED,
   counted separately from tool CALLED.
3. **The honest arms are `c0-bare`, `repowise`, `repowise-hooks`.** No
   competitor ships Claude Code hooks. A hooks-on repowise beside a hookless
   codegraph measures our installer against their tool, which is the graphify
   defect pointing at a competitor again. Competitors add cost and no
   information here, and their columns are read off the paired baseline where
   they were measured under equal conditions.
4. **`repowise` and `repowise-hooks` share one tree and one index and differ in
   exactly one declared field.** Anything else that differs invalidates the
   pair, and the pair is the whole design.

---

## 1. THE INSTRUMENT CHECKS, RUN BEFORE THIS FILE WAS COMMITTED

| check | expected | observed |
|---|---|---|
| the paired baseline is clean | `hook_injections` empty on every row | **0 of 94 rows** carry any injection |
| the baseline's headline recomputed from its own jsonl | repowise 7/15 adoption, -10.1% output tokens | **7/15, -10.1% pooled**, mean-of-ratios 0.949, median 0.986, largest cell 9.3% of the numerator, 0 error cells in any arm |
| the shared tree | present, at the pinned commit, prose index | `Desktop/bakeoff/lb-repowise-full-django-django` @ `3b161e60`, `last_sync_commit` == HEAD, `docs_mode: llm`, `written_by_version: 0.37.0` |
| the binary under test | a CLEAN checkout, not `Desktop/repowise` | `C:/Users/ragha/Desktop/repowise-layerb2` @ `172ce0b8`, working tree clean, `repowise, version 0.38.0`. Same binary the paired baseline ran on |
| `repowise-augment` on PATH | the pinned checkout | **FAILED: `C:\Users\ragha\Desktop\repowise\.venv\Scripts\repowise-augment.exe`**, an editable install of a checkout with uncommitted changes. See section 3 |
| does a declared hook fire at all under Claude Code on Windows | unknown before measuring | **yes.** The shipped POSIX command runs (Claude Code dispatches hook commands through git-bash `sh`) and injected the SessionStart block verbatim |
| the arm pair's declared difference | one field | `{description, hooks}` only; `description` is free text printed in the run header and is read by nobody. Provenance differs in `{arm, hooks_declared}` |
| bench Claude credentials | hard-linked to a live token | **FAILED and repaired.** `.claude_bench_home/.credentials.json` had become a separate file carrying `expiresAt: 0`; every cell would have exited 0 answering "Failed to authenticate". Relinked, same inode, live token |

### The hook surface being measured is 0.38.0's, which is TWO events, not three

The brief describes three: `SessionStart`, `PostToolUse`, `PostToolUseFailure`.
That is the operator's main checkout at `57ad2679`. **The pinned checkout at
`172ce0b8` ships two**, and its `augment_cmd` does not handle
`PostToolUseFailure` at all. Declaring the third would have wired a hook the
binary under test cannot answer, and the treatment would have been a
benchmark's idea of the product rather than the product.

The pinned checkout is not negotiable here: it is the binary that produced the
`repowise` column this run is paired against, and swapping it would put a
second difference into a two-arm pair. **So the wrong-path rescue is out of
scope of this run and its absence is not evidence about it.**

---

## 2. WHAT CHANGED IN THE HARNESS. THREE THINGS, ALL SMALL.

The harness already did most of this: per-arm declared hooks written into
`--settings` (`arms.py::generate_settings`), the operator's own hooks stripped
via `CLAUDE_CONFIG_DIR` (`arms.py::prepare_claude_home`, measured at 0 hooks
and 0 plugins), `--include-hook-events` passed, `hook_events` and
`hook_injections` captured per cell, and `canary_gate --allow-hooks`.

1. **`repowise-hooks` in `configs/arms.yaml`.** `shares_index_with:
   repowise-full`, identical `mcp`, `client_tools`, `coaching` and `warm` to
   `repowise`, plus a `hooks:` block copied byte-for-byte from the pinned
   checkout's `hooks.json` (minus `statusMessage`, which is display text the
   stream never carries).
2. **`swe_qa_runner`'s D16 check is arm-aware.** Any injection used to print
   "the arm was not run in the pinned environment" and set
   `attach_guard_fired`. On a declared-hooks arm injection **is the treatment**,
   so contamination is now "injections on an arm that declared no hooks", and
   that check stays exactly as loud as it was for `c0-bare` and `repowise`. The
   opposite failure prints too: **a declared hook that injected nothing** is
   named as an arm running with its treatment switched off.
3. **The cell's PATH names the pinned binary.** `repowise_exe()` pinned what
   the *harness* spawns; a hook is spawned by the *cell*, and took whatever
   PATH offered, which was the dirty checkout. `swe_qa_runner` now prepends the
   pinned binary's directory to the agent subprocess's PATH, verified to make
   `command -v repowise-augment` resolve there under the `sh` that runs hook
   commands. Applied to every arm, because a PATH that differed per arm would
   be a second difference between the pair; it is a no-op for an arm that
   spawns no repowise binary.

No new script, no new report, no new metric field. `hooks_declared` was already
on every row via `arm.provenance()`.

---

## 3. THE GATE, AND ITS FIRST ITEM IS THE ONE THAT WILL ACTUALLY BITE

**The shipped hook command is `if command -v repowise-augment >/dev/null 2>&1;
then exec repowise-augment; fi`, and it exits 0 IN SILENCE when the binary is
not on the cell's PATH.** A hooks arm that fires nothing is indistinguishable
from hooks that do not help, and it would be published as "hooks make no
difference". PATH did in fact resolve the wrong binary before this run; that is
measured above, not hypothetical.

**Gate shape: 3 arms x 2 questions = 6 cells, ~$1.83.** Two questions of two
different shapes, named here rather than taken by position, because the two
failure modes live at opposite ends:

- **`django_014`, symbol-lookup, the FLOOR.** One lookup, few tool calls. If
  the `SessionStart` block is the only injection a cell ever gets, it shows here.
- **`django_017`, multi-hop-flow, the CEILING.** Repeated Grep and Read, so
  the `PostToolUse` handlers get many chances to speak.

Both are members of the stratified 15. **Chosen on shape; noticed afterwards
and recorded because it cuts the other way:** in the paired baseline the
`repowise` arm was NOT exercised on either of them. So the gate runs on two
cells where the tool was never called, which is precisely the territory the
hooks claim to cover, and it is a harder gate than a pair where the tool was
already in use.

Assertions, on every non-error cell:

| arm | assertion |
|---|---|
| `repowise-hooks` | `hook_injections` **> 0**, and the injected text names repowise content (`[repowise]`) |
| `repowise` | `hook_injections` **== 0** (D16 still applies) |
| `c0-bare` | `hook_injections` **== 0** (D16 still applies) |
| all three | same tree HEAD, same index, same binary; `repowise` and `repowise-hooks` resolve to the SAME worktree |

**Decision rule, fixed in advance.** The gate passes and step 2 runs, or the
arm is fixed and re-gated, or **the run is declared UNRUNNABLE in this file
with the failure quoted, before step 2 spends anything.** If injections are 0
on the hooks arm, **the arm is broken, not the hooks**, and no token number
from it is published.

### The dedup window, found while gating and named because it looks exactly like a broken hook

`augment_cmd._claim_emission` writes a machine-global marker in the temp
directory keyed on `(event, emitted text)` with an **8 second TTL**, so two
callers computing the same enrichment within 8 seconds emit once. The
`SessionStart` text is byte-identical across cells (same tree, same HEAD), so
**two cells starting within 8 seconds of each other would show the second with
zero injections and no error anywhere on the row.** It cost one probe run
before it was understood. `max_workers: 1` and cells of tens of seconds keep
this out of reach, and it is named here so that a future run raising
`max_workers` knows what it would be measuring.

---

## 4. PREDICTIONS, fixed before any cell runs

| # | prediction |
|---|---|
| **H1** | `repowise-hooks` records `hook_injections > 0` on **15 of 15** non-error cells. Context is DELIVERED everywhere, because `SessionStart` speaks in any indexed tree regardless of what the agent does |
| **H2** | `repowise-hooks` adoption does **not** exceed `repowise`'s 7/15 by more than 2 cells. Hooks inject context; they do not make the agent call the server, and predicting otherwise would be predicting the category error section 0 rejects |
| **H3** | **output tokens move by less than 10 points against `repowise`'s -10.1%.** Registered as a weak prediction against a noisy quantity: the paired baseline's own median ratio is 0.986 while its pooled figure is -10.1%, so most of that saving lives in a minority of cells |
| **H4** | **hooks cost more INPUT than they save.** Injected context is extra input on the SessionStart turn and on every matched tool call, and nothing in the mechanism reduces input. Input tokens are recorded and reported; they are not the outcome variable (E10), and this prediction is about direction only |
| **H5** | **no arm separates from `c0-bare` on quality** at the pre-registered 0.50 threshold. The baseline's own paired `repowise - c0-bare` difference is **-0.12 with sd 1.17**, an order below what n=15 can see. This predicts a null and the null is the expected outcome |
| **H6** | at least one cell shows an injection the `SessionStart` block did not produce, i.e. a `PostToolUse` handler speaking (zero-result grep, flood triage, or stale read). If **every** injection in all 15 cells is the SessionStart block, the PostToolUse half of the product is inert on this workload and **that is reported as the finding it is** |

---

## 5. THE READING TABLES, fixed before the numbers exist

### Tokens

**Output tokens only. No dollar column.** E10: prompt-cache warming and arm
ordering moved the dollar delta 39 points, and an arm that never called its
server measured 43% cheaper than bare. Correlation with run position is +0.010
for output tokens against -0.487 for dollars.

**No pooled percentage travels alone at n=15.** Every pooled figure is
published beside the **mean of per-instance ratios**, the **median ratio**, and
**the largest single instance's share of the pooled numerator**.

| observed | reading |
|---|---|
| `repowise-hooks` beats `repowise` on output tokens, and the median agrees with the pooled figure | hooks help, reported with the full triple and the paired-baseline comparison beside it |
| `repowise-hooks` beats `repowise` pooled but **not** on the median | **reported as carried by a minority of cells, explicitly**, with the largest cell's share named. Not a headline |
| **`repowise-hooks` is WORSE than `repowise` on output tokens** | **it is the headline, at the top of RESULT.md.** Injected context is extra input on every matched tool call and may buy nothing back. A shipped feature that costs tokens is a finding we publish, not one we bury |
| `repowise-hooks` and `repowise` are indistinguishable | **published as indistinguishable at n=15**, with the required-n arithmetic. The `hook_injections` column still stands on its own: context delivered on N of 15 while the tool was called on M of 15 is the result even when tokens do not move |

### Quality

**Underpowered at n=15 and registered as underpowered rather than discovered as
underpowered.** The judge's paired noise floor on django is **S = 0.6896**; at
80% power n=15 detects **0.50** and nothing smaller, and 0.25 would need 60
pairs.

| observed | reading |
|---|---|
| no arm differs from `c0-bare` by **>= 0.50** | **quality is INCONCLUSIVE at n=15 and is published as inconclusive** |
| `repowise-hooks` separates upward by >= 0.50 | reported, with the required-n arithmetic beside it and an explicit note that **H5 predicted a null** |
| **`repowise-hooks` separates DOWNWARD by >= 0.50** | **it is the headline.** Injected context that makes answers worse is a finding we publish |

No reclassification after seeing the number. No re-running until it improves.

### Adoption, and injection

**Adoption is a SINGLE-ARM property**, counted over **every non-error cell of
that arm**, never over the paired set. Counting over pairs is what let a
control's turn exhaustion delete a treatment adoption and report 2/15 where the
truth was 3/15.

**`hook_injections` is a separate column and is never merged into adoption.**
The two answer different questions: context DELIVERED against tool CALLED. A
table that adds them, or that reports "hooks raised usage to 15/15" by counting
injections as usage, is the exact confusion this pre-registration exists to
prevent, whichever way it flatters us.

---

## 6. FAILURE HANDLING, fixed in advance

- **An auth failure means the cell never ran.** Retry it. No outcome was
  observed, so nothing is being selected on. This run has already met one: the
  bench credential link was dead before the gate.
- **A turn-limit exhaustion or a timeout is an AGENT OUTCOME.** Exclude it and
  name it. **Never retry it.** Retrying exhausted cells until they succeed
  deletes the run's hardest attempts.
- **A judge failure means the grade never happened.** Re-grade, and stamp every
  re-grade.
- **No prompt is tuned against cells that are then reported on.**
- **Launch through the harness, never `Start-Process`.** Resume is on
  `(task_id, condition)`, so a killed run costs nothing.
- **Scaling is not pre-committed.** If the 15 look good, a repeat or the 48 is
  decided and pre-registered **then**, in a superseding file, not here.

---

## 7. WHAT IS NOT RUN, AND WHY

- **The competitors.** Section 0, point 3.
- **The sealed 30 are not touched.** Not staged, not queried, not loaded by any
  script in this run.
- **No index is rebuilt.** `repowise` and `repowise-hooks` query the existing
  prose tree, which is the same tree the paired baseline queried.
- **No Codex half.** Codex already calls the tool on every question, so hooks
  have nothing to make unnecessary there; it is a different experiment.
- **`PostToolUseFailure`**, per section 1: absent from the pinned build.

---

## 8. DELIVERABLE

`local-stash/competitive-proof/50-results/layerb-opus-hooks/RESULT.md`, written
as the work proceeds, carrying:

- this pre-registration quoted against what happened, **including every
  prediction that failed**
- **output tokens per arm against the bare control**, pooled never alone
- **`hook_injections` per cell as its own column**, with what was injected and
  by which event, so SessionStart and PostToolUse can be told apart
- **adoption per arm**, counted over that arm's own non-error cells, and not as
  the headline
- the gate table, and any assertion that failed
- a provenance block: commits, trees and their HEADs, binary, index, models,
  judge, exact configs, wall clock
- no em dashes
