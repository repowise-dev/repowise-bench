# Pre-registration: enforced tool use under Claude Code, Opus, django

**Committed before any cell of the 90 ran and before a cent was spent on it.**

| # | step | config | cells | cost |
|---|---|---|---:|---:|
| 1 | the **gate**, the three arms the mechanism has never touched | `configs/layerb_opus_preguide_gate_django.yaml` | 3 | ~$1 |
| 2 | the **run**, 6 arms x 15 instances | `configs/layerb_opus_preguide_django.yaml` | 90 | ~$30 |

---

## 0. THE QUESTION, AND WHY THE PREVIOUS ANSWER DID NOT EXIST

Finding E13. Under Claude Code the tool under test is mostly not called, so a
token or quality comparison there mostly compares two bare agents: **17 of 30
Claude cells never issued a `ToolSearch`**, the gate deferred MCP schemas put
in front of every server, so the tool was never a CANDIDATE rather than a
rejected one. On the paired Opus baseline repowise adopted 7 of 15 and three
competitors adopted **0 of 15**. Under Codex the identical repowise server,
index and questions gave **15 of 15**.

So Codex has a measured answer to "do these tools save tokens when they are
used" and Claude Code has never had one. **This run buys that answer**, by
making usage a CONDITION of the run instead of an outcome of it.

**Adoption is deliberately not measurable here, and that is the trade.** The
adoption question is already answered and published. Every row records
`force_tool_use`, and no table may pool a nudged cell with an unnudged one.

## 1. THE MECHANISM, AND THE TWO THAT WERE REJECTED FIRST

`harness/force_tool_use.py --mode pre-guide`, attached by the HARNESS as a
`PreToolUse` hook on `Read|Grep|Glob`, identically for every MCP arm with only
that arm's own server prefix and tool names substituted. It injects
`additionalContext` naming the arm's server, stays silent once that server has
been called, and is capped at 3 nudges per session. A mechanism only one vendor
could use would be a handicap on the others, not a countermeasure.

It uses `additionalContext` and deliberately **not** `permissionDecision`.
Denying the read would dictate the agent's workflow, and the arms are not
equally suited to a tool-first one.

Measured on 6 cells that the baseline adopted 0 of 6:

| variant | adoption | what it cost on `django_017`, the cell where the mechanism had to work |
|---|---|---|
| baseline, `neutral` prompt | 0 of 6 | repowise 3259 tokens / 13 turns, codegraph 3443 / 11, tool unused |
| **prompt mandate** | 2 of 6 | **REJECTED.** Both were `django_014`, which adopts unprompted anyway, so not one cell where it changed the outcome. Verified to have arrived (`coaching_mandatory: true`) before that was concluded |
| **Stop block** | 4 of 4 | **REJECTED ON COST.** repowise 5244 / 17, codegraph 7816 / 18, i.e. **+61% and +127%**, because it fires after a complete answer exists and that answer is discarded and rewritten |
| **pre-guide** | 6 of 6 | repowise **3393 / 12**, codegraph **3296 / 11**, at or below baseline cost |

**What that table may NOT be read as.** Only 2 of the 6 pre-guide cells actually
received a nudge; the other 4 called their server before touching `Read` or
`Grep`, so the hook stayed silent and adoption there is not its doing. Adoption
is unstable run to run, which is a prior finding here. What is attributable is
the 2 nudged cells, and they are exactly the 2 where the Stop block was needed
and the prompt mandate failed.

The prompt returns to the byte-identical `neutral` template every published row
in this workstream was produced under.

## 2. THE INSTRUMENT CHECKS, RUN BEFORE THIS FILE WAS COMMITTED

| check | observed |
|---|---|
| the hook derives a correct prefix for every arm | 6 of 6, including `code-review-graph`, whose server is named `crg`; every allowlisted tool matches its arm's prefix, 0 mismatches |
| `c0-bare` receives no hook | confirmed, it has no server and cannot be nudged |
| the nudge cap holds across processes | fixed and self-tested. It keyed on `hash()`, which is salted per process, so every fire named a different marker and the cap never held. Now `hashlib`; the self-test fires 5 times and requires the last 2 silent |
| `PreToolUse` accepts `additionalContext` | present in the Claude Code binary's own response schema at 2.1.224, beside `permissionDecision` |
| bench Claude credentials | hard-linked to a live token, repaired earlier today after they had become a separate file carrying `expiresAt: 0` |
| **serena's surface changed since the baseline** | **10 allowlisted tools in the baseline, 11 now.** `execute_shell_command` was restored after that run. See section 5 |

## 3. WHAT IS NOT CONTROLLED, STATED RATHER THAN DISCOVERED LATER

**`c0-bare` cannot be nudged.** It has no server, so the mechanism cannot reach
it and there is no matched condition for it. On the Stop block that would have
been fatal, because the block bought every treated arm an extra turn the
control never paid for. On `pre-guide` the nudged cells came in at or below
baseline turns (12 and 11 against 13 and 11), so the asymmetry is small, and it
is left UNMATCHED and declared rather than papered over with an invented sham
condition. **Consequence, binding:** the "versus bare" column carries the
nudge's cost on the treated side only, so it is CONSERVATIVE against every
tool, ours included.

**A sham control is the fix if the run shows otherwise.** If treated arms come
in at materially more turns than `c0-bare`, the versus-bare column is retracted
in the same document that reports it and the arm-versus-arm table stands alone.

## 4. PREDICTIONS, fixed before any cell runs

| # | prediction |
|---|---|
| **P1** | every MCP arm reaches **>= 13 of 15** adoption. The mechanism, not the tool, is what this predicts, and it must hold for competitors as much as for us |
| **P2** | at least one of the three never-nudged arms (`graphify`, `serena`, `code-review-graph`) needs a fix its README would not have predicted. Every arm that has ever had a first cell in this workstream did. **P2 failing is reported as P2 failing** |
| **P3** | with usage enforced, **repowise's output-token reduction against `c0-bare` is larger than the 7-of-15 baseline's -10.1%**. This is the whole point of the run and it is the prediction most likely to embarrass us |
| **P4** | **the token ordering across arms changes** against the baseline, where graphify at 0 of 15 adoption "beat" codegraph at 4 of 15. An ordering produced by arms that never called their servers should not survive making them all call |
| **P5** | **no arm separates from `c0-bare` on quality** at 0.50. n=15 against a paired noise floor of 0.6896 detects nothing smaller. Predicts a null |
| **P6** | fewer than half the cells actually receive a nudge, because the `neutral` prompt already tells every arm to start with its server. The mechanism is a backstop, and if it fires on nearly every cell the prompt is doing less than it appears to |

## 5. THE READING TABLES, fixed before the numbers exist

### Tokens

**Output tokens only. No dollar column** (E10: cache warming and arm ordering
moved the dollar delta 39 points; an arm that never called its server measured
43% cheaper than bare).

**No pooled percentage travels alone at n=15.** Every pooled figure publishes
beside the mean of per-instance ratios, the median ratio, and the largest single
instance's share of the pooled numerator.

| observed | reading |
|---|---|
| repowise leads and the median agrees with the pooled figure | reported with the full triple, and with the baseline's 7-of-15 figure beside it so the enforcement is visible as the change it is |
| repowise leads pooled but not on the median | **reported as carried by a minority of cells, explicitly** |
| **a competitor leads** | **reported in full and at the top.** Named here because it is the outcome most likely to be quietly de-emphasised |
| **no arm beats `c0-bare` once all of them actually call their servers** | **it is the headline.** That would say the whole category saves nothing under this harness when used, and it is a finding worth more than a favourable ranking |

### Quality

**Underpowered at n=15 and registered as underpowered.** Paired noise floor
S = 0.6896; at 80% power n=15 detects 0.50 and nothing smaller. Inconclusive
unless a contrast clears 0.50. A downward separation by repowise is the
headline if it happens.

### Serena

**No table pools serena's row here with its baseline row.** It ran 10
allowlisted tools there and 11 here, the 11th being `execute_shell_command`,
which runs inside serena's own process. Its row carries that difference
wherever it appears.

## 6. FAILURE HANDLING

- **auth failure**: the cell never ran. Retry it.
- **turn-limit exhaustion or timeout**: an AGENT OUTCOME. Exclude and name it.
  **Never retry it.**
- **judge failure**: re-grade and stamp the re-grade.
- **no prompt or hook is tuned against cells that are then reported on.**
- **the sealed 30 are not touched.**
- **scaling is not pre-committed.** A repeat or the 48 is decided and
  pre-registered then.

## 7. DELIVERABLE

`local-stash/competitive-proof/50-results/layerb-opus-preguide/RESULT.md`,
carrying this file quoted against what happened including every failed
prediction, per-arm output tokens against the bare control with the pooled
figure never alone, the per-arm adoption and nudge counts as separate columns,
the gate table, the unmatched-control limit from section 3, and a provenance
block. No em dashes.

---

## 8. AMENDMENT, written after the gate and before the 90 spent anything

**The gate FAILED on `code-review-graph`, and P2 is confirmed.**

| arm | nudges delivered | naming its own prefix | server called | isError |
|---|---|---|---|---|
| `graphify` | 3 | yes, `mcp__graphify__` | **yes**, `query_graph` | 0 |
| `serena` | 3 | yes, `mcp__serena__` | **yes**, `find_symbol`, `search_for_pattern` | 0 |
| `code-review-graph` | 3 | yes, `mcp__crg__` | **NO** | 0 |

`graphify` and `serena` both adopted 0 of 15 in the paired baseline and both
adopted here after nudges, which is the mechanism working on arms it had never
touched.

**One fix was tried, and it did not work.** The nudge named EVERY allowlisted
tool, so `crg` received a 20-name wall where `codegraph` received a single
name; the nudge's quality varied with a property of the vendor's surface
instead of being the same nudge for everyone. That is a real defect in the
mechanism and it is fixed regardless of outcome: at most 3 tools are named, cut
in the arm's own allowlist order, so nudge length is now 260 to 326 characters
across the whole field instead of varying five-fold.

`crg` was then re-gated on `django_012`, **a question OUTSIDE the stratified
15**, specifically so the fix was not tuned against a cell this run reports on.
It failed again: 3 nudges, `Glob` and five `Read`s, **and no `ToolSearch` at
all**. So the fix was a fairness fix and not a cure, and it is kept on those
grounds alone.

**THE DECISION, fixed here before the 90 ran.** `code-review-graph` STAYS in
the run, at 6 arms, and its row is reported as **UNENFORCEABLE, never as a
zero**:

1. Its token column is a **bare agent wearing the arm's name** and may not
   enter any tool-versus-tool table. It is labelled as such wherever it appears.
2. Excluding it would let a reader assume every arm was enforced. The
   countermeasure has a limit, one vendor's surface resists it even under
   direct instruction, and that limit is a result rather than an inconvenience.
3. **It is the strongest evidence in this run for E13 itself.** An agent told
   three times, by name, to load a tool with `ToolSearch` did not issue one.
   The discovery gate is not merely a default-behaviour effect for this arm; it
   survives explicit instruction.

**No further tuning.** Two fixes have now been tried against `crg` and the
second was already at the edge of tuning against a competitor. A third would be
fitting the mechanism to one arm's behaviour, and the fact that it would cut
AGAINST us does not make it sound.
