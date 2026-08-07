# Pre-registration: Layer B on mui, under Codex

**Committed before any cell of this run ran and before a cent was spent on it.**
Layer A on mui is graded (75 cells, `50-results/layera-mui/RESULT.md`) and mui
has no agent-loop row. This buys one.

| # | step | config / script | cells | cost |
|---|---|---|---:|---:|
| 0 | the **prose build**, our arm's second index | `configs/layerb_mui_prose_build.yaml` | 13 builds | ~5.2 h, ~$1.10 LLM |
| 1 | the **gate**, per arm proof of life | `scripts/assert_codex_proof_of_life.py` | 12 | ~$0.70 |
| 2 | the **run**, 6 arms x 15 instances | `configs/layerb_codex_mui_dev15.yaml` | 90 | ~$5-10 |

Step 0 gates nothing but pays for everything. Step 1 gates step 2.

---

## 0. THE CORPUS HAD NO QUESTIONS IN IT, AND THAT IS THE FIRST FINDING

The brief named `data/cb_mui/swe_qa/tasks.json` as the corpus. **Its rows have
no `question` field and no `answer` field.** They carry `problem_statement`,
`gold_files` and `gold_spans`, because `prep_mui_instances.py` wrote them for
Layer A, which grades retrieval and needs nothing else.

`swe_qa_runner` puts `task["question"]` in the agent prompt and hands
`task["answer"]` to the judge. Run as briefed, **every mui cell would have asked
a null question**, on all six arms at once, and the failure mode is the one this
workstream keeps naming: it would have looked like six tools that cannot
retrieve.

`scripts/prep_mui_layerb_questions.py` fixes it by applying the transformation
the Go ContextBench corpus **already ships**, to the same source parquet:

```
question = FRAME % problem_statement          # verbatim from data/cb_go
answer   = "The change was implemented by the following diff...\n\n" + patch
```

Both frame strings are copied byte-for-byte out of `data/cb_go/swe_qa/tasks.json`
and `data/cb_go_frameB/swe_qa/tasks.json` rather than retyped, because a frame
rewritten by hand is a new frame and not a reused one.

**FRAME A, and the choice is registered here rather than argued later.**
`configs/layerb_go_frame_ab.PREREGISTRATION.md` ran A against B on the Go
held-out nine and the result was **inconclusive by its own rule**. There is
therefore no measured reason to prefer B, and A is the frame every published
ContextBench Layer B row was produced on. `--frame b` exists so the choice stays
checkable.

**Output goes to `data/cb_mui_layerb/`, never to `data/cb_mui/`.** Layer A's
graded corpus is not edited in place by a Layer B script, and the one-shared-
tasks.json footgun is already documented in `layera_mui_dev15.yaml`'s own header.

**What this costs the run, stated plainly:** the mui question shape is a
change-description question, not SWE-QA's interrogative shape. **No mui row here
is comparable with a django row on quality or on tokens**, in either direction.
It is comparable with the Go ContextBench rows, which use the same frame.

---

## 1. THE INSTRUMENT CHECKS, RUN BEFORE THIS FILE WAS COMMITTED

| check | expected | observed |
|---|---|---|
| every competitor arm tree at its task's `base_commit` | 15 x 4 at pinned commits | **60 of 60 ok** (codegraph, graphify, code-review-graph, serena) |
| the two existing prose trees | at `base_commit`, 1536-dim | `cbmui_2bb4ea7a` @ `04fae47c2a` and `cbmui_8fcb53e6` @ `eb8e95bacf`, both rc=0, `index_vector_dim: 1536`, `index_embedder_mock: false` |
| `repowise-full` trees for the other 13 | absent, to be cut | **absent, all 13** |
| `c0-bare` trees for mui | absent, to be cut | **absent, all 15** |
| `core.longpaths` on the mui base clones | **REQUIRED, and it is NOT SET** | not set on any of the 15. See below |
| `OPENAI_API_KEY` | present in the build and the server env | set from `.repowise/.env` before step 0 |
| the binary under test | a CLEAN checkout | `C:/Users/ragha/Desktop/repowise-layerb2` @ `172ce0b8`, working tree clean. **NOT** `Desktop/repowise`, which has uncommitted changes and whose editable install would publish a dirty tree as a version |

**`core.longpaths=true` is unset on all 15 mui base clones and it is a
prerequisite, not a nicety.** On Layer A a Windows MAX_PATH failure scored a
clean 0/1 for all five arms at once. The competitor worktrees already exist so
they survived it; **the 28 trees this run still has to cut (13 prose + 15 bare)
have not been through it.** `git config core.longpaths true` is applied to each
of the 15 base clones before any tree is cut, and worktrees inherit it because
config lives in the common dir.

---

## 2. STEP 0. THE PROSE BUILD, WHICH IS THE WHOLE BILL

**"Build once, serve both layers" holds for every competitor and fails for us.**
Layer A is `--no-prose`; Layer B is prose. codegraph, graphify,
code-review-graph and serena reuse their Layer A artifact unchanged. Ours needs
a second index per instance.

**Thirteen builds, not fifteen.** The Layer A smoke built the `repowise` arm by
mistake, which resolved to the full-prose Layer B index. That was wrong for
Layer A and was retracted there; it is exactly right here, and both trees verify
at their pinned commits.

Budgeted from Layer A's own 15-point curve (166.2s at 2,322 files to 1,866.0s at
28,346) times the prose multiplier measured on both ends of that same range
(**2.208x** small, **1.380x** large), interpolated log-linearly in file count:

| | |
|---|---:|
| 13 prose builds | **~18,580 s ~= 5.2 h** |
| LLM spend | **~$1.10** (13 x ~$0.085 measured) |

The multiplier **shrinks** with size because `FILE_PAGE_AUTO_CEILING = 4500`
caps file pages on the large trees. Layer B keeps that cap because Layer B is
what a user actually runs, so Layer A's `--max-file-pages 0` figures do not
transfer.

**Quiet machine, and it is a rule rather than a preference.** These seconds are
a published column. Contention is arm-specific and uncorrectable: one stray
background process inflated codegraph 2.5-3.3x and repowise 1.03-1.20x on these
exact trees. Nothing else runs beside step 0. If that is violated the numbers
become provenance and RESULT.md says so.

### The inline-rebuild hazard, found while reading and fixed before it fired

`arm_registry.build_index` has **no skip guard**: it re-runs an arm's index
command every time a fresh process reaches it, because `_ARM_INDEX_DONE`
memoises within one process only. A prebuild therefore did **not** prevent the
run from rebuilding every competitor index **inline, inside timed cells**. This
is not hypothetical, it is what the Go ContextBench run did, and its own
prebuild script records the consequence: *"prebuild_indexes.py did not prevent
inline builds, which put an E11 confound in that run's cost column."*

`ensure_arm_index` now honours the `.bench_prebuild__<arm>.json` stamp the
prebuild writes (and only ever writes after a build exits 0), checking both the
requested arm name and its index owner, because a sharing arm stamps under the
sharer's name and resolves to the owner's. **The embedding proof is re-run live
rather than read out of the stamp**, so D13 still refuses an 8-dimension index
on a path where no build ran. Verified against the existing prose tree before
this file was committed: returns `skipped: prebuild stamp on tree`,
`prebuild_seconds: 367.0`, `index_vector_dim: 1536`.

---

## 3. STEP 2, THE SIXTH ARM. **IT IS `c0-bare`. cocoindex IS DEFERRED.**

The brief asked for a decision and forbade leaving it unstated. The decision and
the reasons, in order of weight:

1. **The control is not optional.** Every headline column here is a delta
   against `c0-bare`: output tokens, tool calls, work saved. An arm-versus-arm
   table with no bare reference cannot answer the question the run is bought to
   answer.
2. **`prompt_style: neutral` structurally forbids the mitigation cocoindex
   needs.** `search`'s `refresh_index` defaults TRUE, Layer B cannot pin an
   agent's arguments, so the only lever is coaching. The cocoindex arm's
   coaching block already carries "Pass refresh_index=false" -- and
   `neutral_coaching()` **discards per-arm coaching entirely**, substituting one
   template with the server and tool names filled in. Adding a cocoindex-only
   sentence back is exactly the asymmetry `neutral` exists to prevent, and the
   whole cross-tool table rests on it.
3. **Under the default it is not merely slower, it may zero the arm.** A default
   `refresh_index=true` reindexes before every search. On a 28,346-file tree
   that is minutes inside a cell with a 1200s ceiling, and a timeout is an agent
   outcome: excluded and named. An arm excluded for timing out reads exactly
   like an arm that could not retrieve, which is the graphify defect pointing at
   a competitor again.
4. **`ccc mcp` spawns `_bg_index` at startup unconditionally**, with no flag to
   suppress it. Layer A could ignore it because Layer A published no timings.
   Layer B publishes wall clock, so every cocoindex cell would be billed a
   reindex it never asked for.

**What is NOT claimed by deferring it.** cocoindex is not weak and is not
dropped from the workstream: its Layer A row stands, and it is the only arm of
six that scores on non-code gold. Its absence here is a harness limit on our
side, stated as one.

**The cheap thing that would discharge it, pre-registered so it cannot be
skipped later:** two throwaway cocoindex cells on the largest mui tree,
measuring `_bg_index` startup latency and one default-argument `search`
latency. If both are small relative to a cell, the arm can enter a timed layer
once the coaching problem has a neutral-safe answer. Not run here, and its
absence from this run is not evidence about cocoindex.

**The six arms:** `c0-bare`, `repowise`, `codegraph`, `graphify`,
`code-review-graph`, `serena`.

---

## 4. SERENA'S `execute_shell_command` IS RESTORED, AND THIS IS THE RE-RUN THAT DISCHARGES IT

It was excluded on a premise that was **false**: that Bash is denied to every
arm. It is not. `c0-bare` issued 11 Bash calls in the 2026-08-03 run and 8 in
the payload re-run, and `scripts/answer_leak_audit.py` walked all 943 tool calls
across both runs and found none reaching the benchmark's data, results or logs.

It stayed excluded for an honest second reason: no serena number had been
produced since the correction, so flipping the flag would have put a re-run
serena row beside every other competitor's 2026-08-03 row. **That condition is
met here.** This is a fresh run in which every arm is measured from scratch.

Restored in `configs/arms.yaml` with the original wrong reason kept verbatim
above the correction, because a competitor handicap quietly edited out is worse
than one recorded. Three consequences carried on every serena row:

1. serena's mui surface is **11 allowlisted tools, not 10**. No table pools this
   row with serena's django rows.
2. `execute_shell_command` runs inside **serena's own process**, therefore
   **outside** codex's `--sandbox read-only`. This is the one arm whose shell is
   not sandbox-bound.
3. Because of (2), `scripts/answer_leak_audit.py` is run over **every command
   serena actually issued**, and its result is published in RESULT.md whether it
   is clean or not. That audit needs the arguments, not the tool names, which is
   why `codex_runner` now records `mcp_call_args` per cell (values clipped at
   400 characters; `refresh_index: false` and a shell command survive whole).

**If the audit finds a leak, the serena row is retracted in the same document
that reports it.** Registered now, before the commands exist.

---

## 5. THE GATE. Not optional, and already written.

`scripts/assert_codex_proof_of_life.py`, whose `--self-test` must pass on all
four sides (positive, negative, and **two mutations that must flip the
reading**) before it is allowed to read a real row.

Per MCP arm, on every non-error cell:

- `served_count > 0`
- `mcp_isError_count == 0`
- **at least one ANSWERED call.** ISSUED IS NOT USED. The first Codex repowise
  cell ever run reported `arm_exercised: true`, one `get_answer`, `error: null`
  and judge 9.0/10, and that call had returned `user cancelled MCP tool call` in
  a run with no user in it.

`c0-bare` gets the inverse assertion: no server mounted, empty call ledger, zero
isError, `served_count` null. Codex's stream has no init event, so the
config-level view plus an empty ledger is the honest substitute and its limit is
printed rather than hidden.

**Gate shape:** 6 arms x 2 questions = 12 cells, on two different question
shapes rather than two of one, since a server can be alive for a lookup and dead
for a traversal. The two are named before the run: **`cbmui_1ac0bb81`** (single
gold file, small tree) and **`cbmui_8fcb53e6`** (7 gold files, the 28,346-file
tree). Selected by `task_ids`, never by position.

**Decision rule, fixed in advance.** An arm that passes enters step 2. An arm
that fails is fixed and re-gated, **or is declared UNRUNNABLE UNDER CODEX ON mui
in this file with the failure quoted, before step 2 spends anything.** An arm
declared unrunnable is reported as unrunnable and **never as a zero**. No arm is
dropped silently.

**Three arms have never had a Codex cell on mui and two have never had one at
all on this corpus.** `codegraph`, `graphify`, `code-review-graph` and `serena`
have Codex django cells but no mui cells; `serena` additionally runs an
11th tool here that has never been exercised anywhere.

---

## 6. PREDICTIONS, fixed before any cell runs

| # | prediction |
|---|---|
| **M1** | `repowise` adoption **>= 13/15**, consistent with django's 15/15 and 44/48 under the same harness |
| **M2** | **every gated competitor arm adopts at >= 9/15.** If a gated arm comes in below 9/15, adoption is a property of that server's surface after all, and that is a finding against E13's generality |
| **M3** | at least one of the four never-run-on-mui competitor arms **fails its first mui gate**. Base rate: every arm that has ever had a first cell in this workstream needed a fix that was not guessable from its README. M3 predicting a failure is deliberate; a gate everyone passes first try is weak evidence the gate works, and **M3 failing is reported as M3 failing** |
| **M4** | `repowise` output-token reduction against `c0-bare` is **>= 15% pooled**, against django rung 9's -31.6%. Lower, deliberately: mui is JavaScript-majority, and Layer A measured our symbol density halving across this size range (0.86/file at 2,322 to 0.41 at 28,346, findings A7/A35), so there is less pre-computed structure to substitute for exploration |
| **M5** | **no arm's quality separates from `c0-bare`** at the pre-registered 0.50 threshold. This predicts a null and the null is the expected outcome |
| **M6** | cost per cell lands in **$0.05 to $0.15**, against $0.0559 measured on django rung 9, allowing for 10x larger trees |
| **M7** | the token saving is **larger on the instances with more gold files** than on the single-gold-file ones, because pre-computed structure replaces exploration and multi-file changes contain more of it |

---

## 7. THE READING TABLES, fixed before the numbers exist

### Quality

**Underpowered at n=15 and it is registered as underpowered rather than
discovered as underpowered.** The judge's paired noise floor is **S = 0.6896**;
at 80% power n=15 detects **0.50** and nothing smaller, and 0.25 would need 60
pairs. Every per-arm quality effect measured in this workstream so far is
smaller than the noise.

| observed | reading |
|---|---|
| no arm differs from `c0-bare` by **>= 0.50** | **quality is INCONCLUSIVE at n=15 and is published as inconclusive.** The publishable content is the work-saved row, the adoption table and the proof-of-life table |
| `repowise` separates upward by >= 0.50 | reported, with the required-n arithmetic beside it and an explicit note that **M5 predicted a null** |
| **`repowise` separates DOWNWARD by >= 0.50** | **it is the headline, at the top of RESULT.md.** A tool that saves tokens while making answers worse is a finding we publish, not one we bury in a limits bullet |
| a competitor separates and `repowise` does not | **reported in full and at the top.** Named here because it is the outcome most likely to be quietly de-emphasised |

No reclassification after seeing the number. No re-running until it improves.

### Tokens

**Output tokens only. No dollar column.** E10: prompt-cache warming and arm
ordering moved the dollar delta 39 points, and an arm that never called its
server measured 43% cheaper than bare. Correlation with run position is +0.010
for output tokens against -0.487 for dollars.

**No pooled percentage travels alone at n=15.** Every pooled figure is published
beside the **mean of per-instance ratios**, the **median ratio**, and **the
largest single instance's share of the pooled numerator**, so a result carried
by one big cell cannot read as a property of the set.

### Adoption

**A SINGLE-ARM property**, counted over **every non-error cell of that arm**,
never over the paired set. Counting over pairs is what let a control's turn
exhaustion delete a treatment adoption and report 2/15 where the truth was 3/15.
`adoption_over_pairs_only` stays a separate field and is never the published
figure.

---

## 8. FAILURE HANDLING, fixed in advance

- **An auth failure means the cell never ran.** Retry it. No outcome was
  observed, so nothing is being selected on.
- **A turn-limit exhaustion or a timeout is an AGENT OUTCOME.** Exclude it and
  name it. Never retry it. Retrying exhausted cells until they succeed deletes
  the run's hardest attempts, and in one past session both exhausted cells were
  `c0-bare`, which biases the surviving set toward questions the control could
  finish.
- **A judge failure means the grade never happened.** Re-grade, and stamp every
  re-grade.
- **No prompt is tuned against cells that are then reported on.**
- **Launch through the harness, never `Start-Process`.** Resume is on
  `(task_id, condition)`, so a killed run costs nothing.

---

## 9. WHAT IS NOT RUN, AND WHY

- **The sealed 30 are not touched.** Not staged, not queried, not loaded by any
  script in this run. Same rule as django's 42.
- **No competitor index is rebuilt.** They are on disk behind stamps, and the
  `ensure_arm_index` fix in section 2 is what now makes that true of the run as
  well as of the prebuild.
- **No Claude half.** It is optional, it is a second row, and it would never be
  a correction to the Codex one.
- **cocoindex**, per section 3.
- **No judge-agreement run.** Nothing here mixes judges: every cell in this run
  is graded by the same `claude-sonnet-5` the django Codex runs used, and no
  table crosses into a `gpt-5.6-luna`-graded Claude number.

---

## 10. DELIVERABLE

`local-stash/competitive-proof/50-results/layerb-mui-codex/RESULT.md`, written
as the work proceeds, carrying:

- this pre-registration quoted against what happened, **including every
  prediction that failed**
- **per-arm output tokens and tool calls against the bare control**, with the
  pooled figure never alone
- **adoption per arm**, counted over that arm's own non-error cells
- the **per-arm proof-of-life table**, with any unrunnable arm named as
  unrunnable and never as a zero
- **the prose-build cost as its own column**, since it is the cost of this layer
  for us and for nobody else
- the serena shell audit, clean or not
- a provenance block: commits, trees and their HEADs, binary, indexes, models,
  judge, exact configs, wall clock
- no em dashes
