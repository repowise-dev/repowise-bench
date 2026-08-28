# PRE-REGISTRATION: DeepSWE rerun, two harnesses, four cells per task

**Written 2026-08-28, before a single agent cell of this run was executed.** The
oracle gate, the corpus-version check, the difficulty banding and the D1 CRLF
check below are all $0 and were run first. Everything they found is recorded
here whether or not it flatters the design.

**This is a SMOKE: 3 tasks x 4 cells x 3 reps. It is not a result.** No row from
it may reach `docs/BENCHMARKS.md` or any public surface. What it buys is a
liveness proof, a per-cell adoption count, and a cost model, which is what a
pre-registration for a full corpus run gets written from.

It supersedes `layerb_deepswe_codex.PREREGISTRATION.md` (2026-08-09) for task
selection and arm design, and inherits its defect list unchanged.

---

## A. WHY THIS IS BEING RE-OPENED

The 2026-08-09 smoke ended with a written instruction not to size a full run.
That instruction was correct on what was known then. Two of its three reasons
have since been answered, and by someone else:

1. **Single cells are unstable.** Three bare-equivalent runs of one task
   returned 1, 0, 1. Our conclusion was "do not size a full run". A competitor
   (akonlabs, GitNexus) ran the same corpus at **10 reps per task per arm**,
   3,471 trials. Reps are the correct answer to instability, and it retires our
   own stated blocker rather than arguing with it.
2. **Patch size does not select for difficulty.** Already retired on
   2026-08-09 by the discovery of a real per-task difficulty column. This
   pre-registration uses that column and adds a calibration step (section C).

The third reason is **not** answered and is the whole point of this smoke:
**unforced adoption was 0 of 3 tasks under Codex**, with a provably alive
server. If that reproduces, a bare-vs-treated comparison is two bare agents.

---

## B. CORPUS VERSION, CHECKED BEFORE ANY COMPARISON

The competitor reports a **"116 entry manifest"**. We run 113. This was checked
rather than assumed, because a corpus mismatch would invalidate every
number-to-number comparison.

| check | result |
|---|---|
| our clone | `datacurve-ai/deep-swe` @ `435ee89`, `tasks/manifest.json` says `deep-swe-1-1`, `task_count: 113`, 113 task entries |
| upstream `main` | `0b9fabb`, one commit ahead, and that commit only changes task timeout settings. Still `deep-swe-1-1`, still 113 |
| upstream tags | only `v1.0.0`, which predates the `tasks/` layout entirely |
| **their own difficulty strata** | **17 easy + 38 moderate + 58 hard = 113** |

**Conclusion: their scored corpus is 113, the same as ours.** Their "116" is a
manifest count that does not survive into their own stratification, and
3,471 trials / 3 arms / 10 reps = 115.7 tasks per arm, consistent with ~116
attempted and 113 scored. **We compare on 113 and say so.** If they later
publish a 116-task manifest, every comparison here is void until re-checked.

**Four tasks are excluded because their own reference solutions fail their own
verifiers** (`langchain-request-coalescing`, `narwhals-rolling-window-suite`,
`prometheus-transactional-reload-status`, `skrub-duration-encoding`). Nothing in
the competitor's writeup says they excluded these. Three of the four sit in the
hard tier on our column (ranks 11, 37, 68 of 113 hardest-first), so **including
them would depress a bare arm most in exactly the tier where they report their
largest lift.** We do not know that they included them. We note it as an
unresolved difference and exclude them ourselves.

---

## C. TASK SELECTION, AND THE CALIBRATION THE OLD SCRIPT DID NOT HAVE

Difficulty source: `kimjune01/deepswe-run`, `results/v1.1/heatmap.json` --
113 tasks x 8 frontier models, 8,484 scored attempts. Pooled solve rate 50.5%.
Script: `repowise-bench/scripts/deepswe/difficulty_bands.py`.

**The pooled column cannot be used raw.** It aggregates 8 *frontier* models; the
run under design uses `gpt-5.6-terra`, which is weaker. Worse, the gap is not
uniform. Applying the competitor's own tier sizes (17/38/58) to our pooled
ranking and comparing tier means against their published bare arm:

| tier | n | our pooled mean | their terra bare | ratio |
|---|---:|---:|---:|---:|
| easy | 17 | 84.1% | 80.8% | 0.96 |
| moderate | 38 | 65.7% | 49.9% | 0.76 |
| hard | 58 | 30.6% | 15.9% | **0.52** |

**terra falls away fastest exactly where the tasks are hardest.** A single flat
ratio (0.73 overall) would mis-band the hard end badly, so selection
interpolates piecewise between those three anchors. Those anchors are external
and unverified by us; **they are used only to choose tasks, never to report a
result.** Our own bare arm is measured.

**Band: predicted terra bare 20-60%, which maps to pooled 35.5-71.1%, n = 46**
after removing the 4 defective-gold and 11 giveaway tasks.

### The three chosen tasks

| task | lang | pooled | predicted terra bare | tier | gold files | prompt words | gold named in prompt |
|---|---|---:|---:|---|---:|---:|---:|
| `bandit-interprocedural-taint-checks` | python | 47.3% | 32.1% | hard | 9 | 142 | 0 |
| `python-statemachine-state-data-scoping` | python | 38.2% | 23.2% | hard | **17** | 270 | 0 |
| `onedump-dump-encryption-pipeline` | go | 35.5% | 20.7% | hard | 11 | 346 | 0 |

Selection reasons, stated before the run:

* **All three sit in the lower half of the band** (predicted bare 21-32%). Two
  heuristics disagree about where to sample and this is where they overlap. The
  brief's rule says target where the control is unreliable. The competitor's own
  stratification says expected discordance is *highest* in the hard tier (0.54,
  against 0.50 moderate and 0.23 easy) and so is their lift (+40.0pp hard,
  +27.4pp moderate, +13.9pp easy). The lower half of the band satisfies both.
* **We deliberately avoid the extreme tail.** The 2026-08-09 run burned a 1.3%
  task where all three arms scored 67/69 and failed the *identical four tests*
  on a spec-comprehension rule stated in the prompt. Retrieval cannot fix that.
  Nothing below ~20% pooled is selected.
* **Localization burden is maximised within the band.** `python-statemachine`
  has 17 gold files and a 270-word prompt naming none of them; that is the
  strongest localization demand in the band. `bandit` has the shortest prompt
  (142 words) for 9 unnamed gold files.
* **One non-Python task** (`onedump`, Go) so a cross-language failure of the MCP
  arm cannot hide inside a Python-only smoke.
* `bandit-interprocedural-taint-checks` reuses a repo, base commit and image
  path from the 2026-08-09 smoke, which is a real setup saving. **It was
  selected from the band on the heatmap column, not because we already ran it**,
  and its 2026-08-09 outcome (bare passed, at `sol`) is not evidence about
  `terra`.

---

## D. ARM DESIGN: FOUR CELLS, AND ONE ASYMMETRY STATED UP FRONT

The plan was "1 bare, 1 Claude, 1 Codex". **A bare arm is harness-specific**: a
Claude treated arm cannot be compared against a Codex bare arm, because the
harnesses differ in tool surface, MCP schema loading and token accounting. So:

| harness | arm | model |
|---|---|---|
| Codex | bare | `gpt-5.6-terra` |
| Codex | repowise | `gpt-5.6-terra` |
| Claude Code | bare | Claude (see below) |
| Claude Code | repowise | Claude (see below) |

**Compare within harness. Across harnesses compare only the lift
(treated - bare), never the absolute pass rate.**

**The model asymmetry is unavoidable and is declared rather than hidden.** The
competitor ran `gpt-5.6-terra`. Claude Code cannot run `gpt-5.6-terra`. So:

* **Only the Codex column is ever placed beside their numbers**, and it matches
  their model, corpus and grading.
* The Claude Code column is reported as a **lift only**, and its absolute pass
  rate is never printed in the same column as a terra number.
* Retrieval headroom is largest when the model is weakest, so a stronger model
  compresses the bare-to-treated gap for reasons that have nothing to do with
  the tool. Any cross-harness lift comparison carries that caveat inline.

**Reps are allocated unevenly on purpose.** Codex cells run on a ChatGPT
subscription (the 2026-08-09 smoke's total metered API spend was $0.01, all of
it index build); Claude Code cells are metered. Splitting reps evenly by reflex
would spend the budget in the wrong place. The smoke runs 3 reps everywhere to
establish the cost model; the full run does not have to.

---

## E. THE PRIMARY QUESTION THIS SMOKE MUST ANSWER

**Does implementation-shaped work suppress tool use?**

Evidence for, from two directions:

| source | harness | result |
|---|---|---|
| DeepSWE smoke, 2026-08-09 | Codex / sol | unforced adoption **0 of 3 tasks**, server provably alive |
| django questions, same workstream | Codex / sol | **15/15**, always exactly 1 call, always `get_answer` |
| mui gate | Codex | 2/2 |
| django questions, 2026-08-27 | Claude Code / sonnet-5 | mean 1.69 calls, **20% zero-call** rows |
| django questions, 2026-08-27 | Claude Code / opus-5 | mean 2.37 calls, **29% zero-call** rows |

Question-shaped tasks elicit calls; implementation-shaped tasks did not, at
n=3. This smoke tests it at n=3 tasks x 3 reps x 2 harnesses = 18 treated cells.

**Adoption is measured server-side and only server-side.** The model's
self-report of its own tool surface is **not a detector**: `gpt-5.6-sol` gave
different tool lists under identical config and twice said `NO_REPOWISE_TOOLS`
while the tools were present and callable. The detector is
`RUST_LOG=codex_rmcp_client=info` and a count of `CallToolRequest` lines in
`codex.txt`. Codex's own `list_mcp_resources` surfaces as `mcp_tool_call` items,
so stream item counts **overstate** usage and are not used.

---

## F. PREDICTIONS, REGISTERED BEFORE THE RUN

| # | prediction | how it is scored |
|---|---|---|
| **P1** | Every chosen task's oracle gate passes and its empty-patch gate fails | reward 1 with gold, reward 0 with nop, per task |
| **P2** | The Codex bare arm fails at least one of the three tasks in at least one rep | any bare cell with reward 0 |
| **P3** | Unforced adoption under Codex is **0 calls on a majority of treated cells** | server-side `CallToolRequest` count |
| **P4** | Claude Code shows **higher mean calls but a nonzero zero-call share** than Codex | per-cell counts, both harnesses |
| **P5** | At least one **discordant pair** (bare and treated disagree) exists across the 3 tasks | per-task, per-harness, across reps |
| **P6** | Our measured Codex bare pass rate does **not** land at their 37.0% | reported with its own CI, never replaced by theirs |

**No quality or efficiency claim is registered.** The 2026-08-09 stopping rule
stands: if the arm is not exercised, no quality or efficiency column is reported
at all, because an unexercised arm is a bare agent with extra steps.

---

## G. STOPPING RULES, BINDING

1. **If unforced adoption is 0 on a majority of treated cells**, the comparison
   is two bare agents. Report that as the finding, run the forced arm to
   quantify what enforcement buys, and **do not size a full run on the unforced
   arm**. An unforced zero is a finding about the product and gets published,
   not hidden behind a forced arm.
2. **If P5 fails** (no discordant pair anywhere), the draw discriminates
   nothing and the smoke stops without a quality column, exactly as 2026-08-09
   did. Stopping twice for a measured reason beats a full run that measures
   nothing.
3. **Any cell whose index, embedder or MCP liveness check fails is discarded
   before its reward is read**, not after. On 2026-08-09 three repowise cells
   had to be thrown away before one was valid, and two of the discarded cells
   carried a reward that would have read as a repowise result.

---

## H. INSTRUMENT CHECKS ALREADY RUN, AND THEIR RESULTS

All $0, all completed before this document was finalised.

| check | result |
|---|---|
| Docker orphan sweep (trap 6) | 5 stale containers, none from DeepSWE. Clean |
| **D1** CRLF, with positive **and** negative control (trap D2) | 226 shell scripts, **0 with CRLF**. Detector proven to fire on a known-CRLF file and not on a known-LF file. System `core.autocrlf` is still `true`, so every new clone uses `-c core.autocrlf=false` |
| repowise build fingerprint (trap 9) | Host `repowise --version` says 0.46.0 but `repowise.core.__file__` resolved into the **primary checkout's working tree** -- an editable install, not a pinned build. Replaced with an isolated `uv tool` pin of `repowise==0.46.0`, re-fingerprinted to the pinned venv |
| **D3** still present in pier 0.3.0 | `pier/agents/installed/codex.py:1020` writes `command = "{shlex.join([command] + args)}"`. `claude_code.py:1186` in the same package emits `command` and `args` separately and is correct. Codex arm keeps the no-argument wrapper workaround |
| oracle gate | see section I |

---

## I. ORACLE GATE, BOTH DIRECTIONS

A verifier that passes everything, or fails everything, produces a
healthy-looking cell that measures nothing. Each task is gated with the gold
patch (must score 1) and with a no-op agent (must score 0).

| task | gold patch | empty patch |
|---|---|---|
| `bandit-interprocedural-taint-checks` | **reward 1**, F2P 66/66, P2P 293/293 | **reward 0**, F2P 0/66, P2P 293/293 |
| `python-statemachine-state-data-scoping` | **reward 1**, F2P 72/72, P2P 1286/1286 | **reward 0**, F2P 0/72, P2P 1286/1286 |
| `onedump-dump-encryption-pipeline` | **reward 1**, F2P 82/82, P2P 6/6 | **reward 0**, F2P 0/82, P2P 6/6 |

**One caveat recorded now rather than after the fact:** `onedump` carries only
**6 P2P tests**, against 293 for `bandit` and 1,286 for `python-statemachine`.
So its "nothing that already passed breaks" half is weakly enforced, and a
regression-inducing patch is likelier to survive there. It stays in the smoke
because the cross-language check is worth more than the weakness costs, but its
reward is the least trustworthy of the three and any full run should say so.

The empty-patch direction is the one that matters most here, and it is doing
more than proving non-degeneracy: **P2P stays at full marks while F2P drops to
zero.** That separates "the feature is absent" from "the environment is broken",
which is precisely the confusion D1 produced on 2026-08-09.

---

## J. AMENDMENT, 2026-08-28, AFTER 3 CELLS AND BEFORE THE REST

**The treated arm as originally built was not the product, and this is recorded
here rather than quietly absorbed.**

The arm registered the MCP server and nothing else: no `AGENTS.md`, no
`CLAUDE.md`, no guidance of any kind, inherited from the 2026-08-09 config
(`--no-claude-md --no-agents --no-codex`) without being re-examined. Registering
a server tells an agent that tools *exist*; it does not tell it when they are
worth calling. `repowise init` writes an editor guidance file by default, and in
real use that file is how the tools get adopted at all. So the original arm was
repowise with its onboarding surface removed.

**What the original arm measured is kept, because it is a real finding**: with a
provably alive server, unforced adoption was **0 calls on 2 of 2 treated cells**
across both harnesses, and the Codex unguided cell scored **F2P 52/66, byte-identical
to its own bare control**. An unexercised arm is a bare agent with extra steps,
and here it measured exactly that.

**The treated arm from this point is enforced**, delivered through pier's prompt
template (`enforced_prompt.j2`), which renders around `instruction.md` so the
task bytes and checksum are unchanged.

Two mechanisms were rejected, each for a measured reason:

| mechanism | why not |
|---|---|
| the `AGENTS.md`/`CLAUDE.md` repowise actually generates | it carries an architecture summary, key modules, entry points and hotspot files, so it can name a task's own gold files and hand localization to the arm whose entire claim is localization |
| writing our own `AGENTS.md`/`CLAUDE.md` into `/app` | **`python-statemachine` tracks its own `AGENTS.md` and `CLAUDE.md`.** Overwriting them destroys repo content the control still sees and pushes a modification into the graded diff. Verified: the arm image build fails its own `/app` clean check |

The template names four tools (`get_answer`, `search_codebase`, `get_context`,
`get_symbol`) and says nothing about the task, the repository, or any file --
the same fairness cap the 2026-08-09 run arrived at.

**The cost of this arm, stated before its numbers are read:** it receives prompt
the control does not. Its token and cost columns are therefore **not**
attributable to the tool alone. The quality column still is. And the first
enforced cell makes that caution concrete rather than theoretical: it scored
**F2P 63/66 against the control's 52/66 while making zero tool calls**, so that
entire difference is a prompt effect with no tool in it.

---

## K. WHAT THIS SMOKE MAY NOT DO

* No number leaves this run without sign-off.
* **No cost number without its completion number in the same row.** Cost per
  solved task satisfies this by construction and is the reported form.
* Nothing is tuned to the benchmark. A change that helps the number and hurts
  the tool loses.
* Their published figures stay theirs. If our reproduction of a bare arm lands
  somewhere else, we report ours with the method and do not quietly adopt theirs.
