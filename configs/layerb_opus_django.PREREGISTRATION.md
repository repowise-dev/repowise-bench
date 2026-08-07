# Pre-registration: the Claude Code half, rerun on Opus

**Committed before the run started and before a cent was spent.** The decision
rule below is fixed here because the quantity being measured, adoption, is one
this workstream has already watched move from 15/15 to 4/15 to 3/15 with nothing
changed on anyone's side. A threshold written after seeing the number could be
read either way, and that freedom is precisely what this page criticises in
other people's benchmarks.

The commit carrying this file is the one immediately preceding the run.

---

## 1. Why this run exists

`docs/BENCHMARKS.md` says, in print, in a shipped section:

> We used Sonnet here. Sonnet reaches for MCP tools noticeably less than Codex
> does under an identical setup, and harness and model cannot be separated by
> this design, so **we plan to rerun this half on Opus** to see which of the two
> is doing the work.

That is a public commitment and this run discharges it.

**The confound, stated exactly.** Under Claude Code with `claude-sonnet-5`,
adoption collapsed: 17 of 30 cells never issued a `ToolSearch` at all, so the
tool was never a candidate rather than a rejected one (finding E13). Under
Codex, on the identical server, index and questions, every tool was called on
every question. **Harness and model are varied together in that comparison and
nothing run so far separates them.**

Opus under Claude Code holds the harness fixed and varies only the model. It is
the missing cell of the design, and either answer is publishable:

- if adoption recovers under Opus, the collapse is a **model** property and the
  harness is fine
- if it does not, the collapse is the **harness's schema deferral** and it hits
  the strongest model too, which is the more useful finding for the ecosystem

## 2. What is run

Two stages. The first is a gate and produces no published number.

### Stage 1, the gate: 6 arms x 1 question = 6 cells

`configs/layerb_opus_smoke_django.yaml`. One named question, `django_000`, drawn
from the same stratified 15 the flagship uses so the smoke exercises a real
member of the draw rather than a question nothing else touches.

Its job is three things, none of them a measurement:

1. **Prove `claude-opus-5` runs end to end through this harness.** No Opus cell
   has ever been run by it. The model string is validated (`canonicalModel:
   claude-opus-5`) but the harness path is not.
2. **Prove every arm is alive under Opus**, per the standing rule that a dead
   server scores as a bad arm. Assertions in section 5.
3. **Price an Opus cell.** Section 7's cost figure is a 5x extrapolation off
   Sonnet list rates and nothing else. The gate replaces it with a measurement
   before the flagship commits.

### Stage 2, the flagship: 6 arms x 15 questions = 90 cells

`configs/layerb_opus_stratified_django.yaml`. The same 15 stratified questions,
computed rather than listed (`stratified_shapes`, per_shape 3, seed 20260803):

```
django_000 django_004 django_006 django_008 django_011
django_014 django_017 django_020 django_028 django_031
django_033 django_034 django_040 django_044 django_045
```

**The two stages use separate results directories.** The runner resumes on
`(task_id, condition)`, so pointing them at one directory would let the gate's
six cells satisfy the flagship. That is fine when the gate passes and silently
wrong when the gate fails and a config is then corrected, because the corrected
run would skip exactly the cells the correction was for. Six duplicated cells
are cheaper than that failure mode.

### The one change permitted after the gate, and why it is not selection

**Arms may be dropped between stage 1 and stage 2 if the measured per-cell cost
makes 90 cells unaffordable.** Dropping arms is a reduction in scope decided on
**cost**, which is not the outcome variable, on a **single question**, which is
not the measurement. It cannot move the repowise-versus-bare contrast, because
that pair is retained in every permitted variant.

`repowise` and `c0-bare` are retained unconditionally. If arms are dropped, the
fact is recorded in RESULT.md with the measured cost that forced it, and the
dropped arms' Sonnet rows are not reprinted beside the surviving Opus rows.

**No other post-gate change is permitted** without a superseding
pre-registration commit.

## 3. What is held fixed, and the single variable

The variable is the model string, `claude-sonnet-5` to `claude-opus-5`.
Everything else is pinned to the Sonnet repeats:

| | pinned to |
|---|---|
| questions | the same stratified 15, seed 20260803 |
| build | worktree `C:\Users\ragha\Desktop\repowise-layerb2` at `172ce0b8`, carrying #1306 |
| index trees | reused, `bakeoff/lb-*-django-django`, asserted at django `3b161e6096` before launch, nothing rebuilds |
| harness | `claude_code`, `max_turns` 15, `max_workers` 1 |
| prompt | `prompt_style: neutral`, byte-identical across arms |
| judge | `gpt-5.6-luna`, unchanged, and see below |
| arm surfaces | `configs/arms.yaml` allowlist rule, unchanged |

**Effort is deliberately not pinned.** `--effort` exists on the CLI, and the
Sonnet cells passed it no value and ran under a replaced config root
(`CLAUDE_CONFIG_DIR`, `configs/bench_settings.json`) which sets no
`effortLevel`. They therefore took the CLI's built-in default. Passing
`--effort medium` for Opus would vary a second thing against the only
comparator that exists, and adoption is plausibly *sensitive* to effort, since a
lower-effort agent plausibly issues fewer `ToolSearch` calls, which is the
outcome being measured. **The flag is not passed, so both models take the same
built-in default and the model remains the only difference.**

**The judge is unchanged and this is checked, not assumed.**
`_resolve_judge_model` refuses a same-family judge. `claude-opus-5` resolves to
the `anthropic` family exactly as `claude-sonnet-5` does, so the judge stays
`gpt-5.6-luna`. **The Opus and Sonnet quality columns are therefore same-harness
and same-judge and may be compared.** They still may not be compared against any
Codex column, per the standing constraint that no quality delta is ever computed
across harnesses.

**The operator's own settings do not reach the cells, and this was verified for
this run.** `~/.claude/settings.json` on this machine carries
`"effortLevel": "medium"` and `"env": {"ENABLE_TOOL_SEARCH": "true"}`, either of
which would invalidate the whole E13 finding if it leaked in.
`prepare_claude_home` replaces the entire config root rather than merging
(`--settings` merges and does not), and `configs/bench_settings.json` sets
neither key. **E13 is not an artifact of the operator's configuration.**

## 4. The decision rule, fixed before the numbers exist

### 4a. Primary: repowise adoption under Opus, over the 15 flagship cells

Adoption is a **single-arm property**: counted over every non-error cell of the
repowise arm, never over the paired set.

| repowise adoption, Opus | reading |
|---|---|
| **>= 12 / 15** | the collapse was the **MODEL**. Sonnet under-uses MCP tools; the harness is fine. |
| **<= 6 / 15** | the collapse was the **HARNESS's schema deferral**, and it reaches Opus too. |
| 7 to 11 | **inconclusive, and it is published as inconclusive.** No reclassification after the fact. |

**The comparator is named here, because it is not a single number.** The
same-build Sonnet readings are the two repeats, **4/15 and 3/15**, both on
`repowise-layerb2` at `172ce0b8`. The 15/15 of 2026-08-03 was on an earlier
build and is reported alongside but is not the contrast. A result of 7 to 11 is
therefore a genuinely likely outcome and the inconclusive row is not a formality.

### 4b. Secondary and mechanistic: `ToolSearch` issuance

Recorded as its own column, per arm, separately from adoption, because they are
different events and E13 is a claim about the first one:

- **cells that issued at least one `ToolSearch`** (the tool became a candidate)
- **cells that then successfully called the arm's server** (adoption)

A cell that issues a `ToolSearch` and declines to call is a **rejection**. A
cell that never issues one is a **discovery failure**. The Sonnet reading was 13
of 30 issuing. If Opus issues on 15 of 15 but calls on 8, that is a different
finding from Opus never looking, and the primary rule in 4a cannot tell them
apart. This column is what makes the inconclusive band informative.

### 4c. Not a decision rule

**Quality is not a pre-registered outcome of this run and no quality claim will
be made from it.** n=15 against a paired sigma of 0.69 detects nothing smaller
than about 0.5 judge points at 80% power. The quality column is recorded and
published for completeness and is explicitly labelled underpowered.

## 5. Proof of life, asserted per arm before any number counts

The standing rule: before recording any zero, prove the arm was alive and the
detector works. A zero from a dead server is indistinguishable from a zero from
a declining agent unless this is checked.

Per arm on the gate:

- **the five tool arms** (`repowise`, `codegraph`, `graphify`, `serena`,
  `code-review-graph`): `served_count > 0` and `mcp_isError_count == 0`.
- **`c0-bare`: the inverse assertion, and it is not symmetry.** Finding D16 was
  that the C0 control was never actually bare. Assert **no server mounted and
  zero MCP calls**.
- **a served tool that is never called is a valid gate pass.** The gate proves
  the server answers, not that the agent chose it. Conflating those would make
  the gate reject exactly the finding this run exists to measure.

## 6. What would invalidate this run

Named now so none of them is reasoned about after the fact:

1. **Any index tree not at django `3b161e6096`.** Asserted before launch.
2. **An unauthenticated cell.** `claude -p` exits 0 with a "Not logged in"
   result and `total_cost_usd: 0`, which records as a cheap wrong answer. The
   runner treats it as a hard error; if one appears, the run stops.
3. **A turn-limit exhaustion counted as an agent outcome and retried anyway.**
   Auth failures mean the cell never ran and are retried. Turn-limit
   exhaustions are outcomes, are excluded, and are named. Retrying them deletes
   the run's hardest attempts.
4. **Any cell run with `--effort` set**, per section 3.
5. **Launching with `Start-Process`.** Eleven sealed cells returned
   `McpError: Connection closed` under it on healthy builds. Launch through the
   harness; a killed run costs nothing because resume is on
   `(task_id, condition)`.

## 7. Cost, and it is an extrapolation until the gate replaces it

Sonnet ran **$18.77 for these same 90 cells**. Opus list rates are roughly 5x
Sonnet's, so the flagship is estimated at **$90 to $140**, the upper end
allowing for higher output token counts. Against a ceiling of $350 with roughly
$90 spent, the flagship at its upper estimate lands near $230.

**That estimate is arithmetic on list rates and nothing else.** The gate's six
cells replace it with a measurement, and section 2's arm-dropping clause is what
happens if the measurement comes back above the estimate.

`budget.abort_on_exceed` is `true` in both configs. The rails are spend limits,
not knobs the run reads.

## 7b. n stops at 15. Fixed before the 15-question run returned.

**Decided 2026-08-06 (Raghav), with the flagship in flight and no cell of it
read.** The Opus half is n=15 and does not scale to 48 on the strength of what
comes back.

This is recorded here rather than in a session note because the alternative was
a rule of the form "scale to 48 if the 15 gives good results", and that rule is
**conditional-on-outcome scaling**: the published 48 would then be conditioned
on a favourable 15, and the honest description of the method becomes "we kept
going until it looked good". It is the same family as optional stopping, it is
the exact failure this workstream exists to criticise in other people's
benchmark pages, and it would have been invisible in the final table.

Two alternatives were considered and both are legitimate; neither was taken:

- **unconditional 48**, matching the Codex table's n for the output-token column
- **conditional on the pre-registered INCONCLUSIVE band (7 to 11) only**, which
  triggers on ambiguity rather than on favourability, so a clear but unflattering
  result such as 2/15 would still stop the run

**What n=15 costs us, stated rather than discovered later.** At django's paired
sigma of 0.69, `n = 7.849 * sigma^2 / delta^2` gives a smallest detectable
effect of **0.50 judge points at n=15** against **0.28 at n=48**. Our own
believed quality ceiling is +0.08 to +0.25, so **the quality column is
underpowered at both n and 48 would not have rescued it.** What 48 would have
bought is a tighter output-token column and adoption over more cells. Neither is
purchased.

n=15 also matches the published Claude Code table exactly, which is the table
this run reruns.

**If 48 is ever run on this half, it is pre-registered as its own commit before
it starts, and this section is quoted in it.** A later decision to extend is not
forbidden; extending without a fresh pre-registration is.

## 8. Deliverable

`local-stash/competitive-proof/50-results/layerb-opus-claude/RESULT.md`, with
this pre-registration quoted back against what happened including any prediction
that failed, the per-arm proof-of-life table from the gate, adoption counted per
arm over that arm's own cells, and the `ToolSearch` issuance column beside it.

Published regardless of outcome, and `docs/BENCHMARKS.md` gets the result
whichever way it falls, because the commitment quoted in section 1 does not have
an escape clause.
