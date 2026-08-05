# Pre-registration: the django bake-off on the harness that actually calls the tools

**Committed before any cell of this arc ran and before a cent was spent on it.**
It covers four runs that share one instrument decision, and it fixes the
readings, the failure handling and the harness primacy in advance:

| # | run | config | cells | rough cost |
|---|---|---|---:|---:|
| 0 | the proof-of-life **gate** | `layerb_codex_gate_django.yaml` | 12 | ~$1 |
| 1 | the **flagship** bake-off | `layerb_codex_bakeoff_django.yaml` | 90 | ~$6-11 |
| 2 | the **Go adoption re-test** | `layerb_codex_go_heldout.yaml` | 18 | ~$2 |
| 3 | the **judge-agreement** check | `harness/judge_agreement.py` | 0 new agent cells | ~$2 |

Run 0 gates run 1. Runs 2 and 3 are independent of both.

## 0. Why the instrument changed, and why Claude is not being dropped

`50-results/layerb-repeats/RESULT.md` section 6, finding E13. The Claude harness
defers MCP schemas behind `ToolSearch`, and **17 of 30 Claude cells never issued
one**: the tool was never a candidate rather than a rejected one. The same
repowise server, index, questions, build and prompt style under Codex was called
**15 of 15**.

So **Codex is the instrument for quality and cost, because it exercises the
treatment.** That is instrument choice, not benchmark shopping, and the
distinction only holds if the losing harness is published too.

**Claude is not dropped and is not re-run.** Its 2026-08-03 and 2026-08-05 cells
stand as measured, and the collapse is published as its own finding, because it
is a real defect on the most popular agent harness. Adding a harness is a second
source; replacing Claude with the one that flatters us is the exact failure this
workstream exists to criticise.

### Harness primacy, fixed here before the numbers exist

| claim | PRIMARY harness | why |
|---|---|---|
| **quality** (per-arm and per-shape judge scores) | **Codex** | it is the only harness where the tool under test is reliably called, so a quality contrast there is a contrast between tools rather than between two bare agents |
| **cost / token efficiency** | **Codex**, with the computed-dollar caveat carried on every figure | same reason; and see the standing caveat below |
| **adoption** | **Claude** | the defect lives there. The Codex adoption figures are reported as the contrast that identifies it, not as the headline |
| **the category framing** ("whether an agent calls a codebase server at all depends more on the harness than on the server") | **both, jointly** | the claim is about the gap, so it needs both sides |

**Both harnesses are published in full regardless of outcome**, including any
row where Codex is worse for us than Claude was.

### Standing caveats that travel with every number from these runs

1. **Codex reports tokens and no cost.** Every Codex dollar in this arc is
   computed from list rates by `harness/codex_runner.py::compute_cost_usd`, and
   every published Codex dollar says so. No Codex dollar shares a column with a
   Claude dollar (E11 applies twice over).
2. **Harness and model are confounded and this design cannot separate them.**
   Claude cells run `claude-sonnet-5`, Codex cells `gpt-5.6-sol`. Every
   cross-harness statement here means "the Claude harness or the Claude model",
   never one of the two.
3. **The tool surfaces differ in shape.** Claude cells get `Read,Grep,Glob` and
   no Bash; Codex's core tool is a sandboxed shell that cannot be removed.
   `--sandbox read-only` with the working root pinned to the arm's worktree is
   the nearest equivalent, not the same restriction.
4. **The judges differ across harnesses.** Claude cells were graded by
   `gpt-5.6-luna`, Codex cells resolve to `claude-sonnet-5`. Run 3 exists to
   measure that and it is not optional if any table carries both.

## 1. Instrument checks, run before this file was committed

| check | expected | observed |
|---|---|---|
| `assert_embedder.py` | `c8emb-repowise-{cli,django}` LIVE at 1536; the `c8-` mock pair 8-dim, so **exit 1 is the expected state** | c8emb-cli 1536/1154 LIVE, c8emb-django 1536/3423 LIVE, c8-cli 8, c8-django 8. exit 1 |
| every django arm tree HEAD == `repos/django/django` HEAD | `3b161e6096` | all six at `3b161e6096`: `lb-c0-bare-`, `lb-repowise-full-`, `lb-codegraph-`, `lb-graphify-`, `lb-code-review-graph-`, `lb-serena-django-django` |
| every arm's index artifact present, so nothing rebuilds | present | `.repowise/`, `.codegraph/`, `graphify-out/graph.json` (81 MB), `.code-review-graph/`, `.serena/`; `c0-bare` has none by design |

`bakeoff/django/django` sits at `838e432e3e` and is **not** one of the six arm
trees; it is the calibration checkout and no run here touches it.

## 2. RUN 0 — THE GATE. 6 arms x 2 questions.

### What has actually run under Codex, measured not assumed

| arm | Codex cells to date |
|---|---|
| `repowise` | 19/19 ok (15 noise-floor + 3 n3 + 1 smoke) |
| `c0-bare` | 4/4 ok (3 n3 + 1 smoke) |
| `codegraph`, `graphify`, `serena`, `code-review-graph` | **0. Never.** |

`mcp_overrides` (`codex_runner.py:255`) translates each arm's `.mcp.json` into
`-c mcp_servers.<name>.…` generically. That is why no new code is needed, and it
is also exactly the kind of generic path that works for the one server it was
tested on and silently fails for another. **The arm that gets silently zeroed is
never ours, because ours is the only output format we already know**: graphify
scored 0.012 MRR from a path regex, code-review-graph returned 84/84 `isError`
from a tool-name suffix.

### The two questions, named before the run

`django_014` (symbol-lookup) and `django_017` (multi-hop-flow), both members of
the flagship's own stratified draw. Two shapes rather than two of one, because a
server can be alive for a lookup and dead for a traversal. Selected by
`task_ids`, not by position: `max_tasks: 2` in file order is the `What` block and
nothing else.

### The assertions, per arm

**The four competitors** (`codegraph`, `graphify`, `serena`,
`code-review-graph`), on **both** of that arm's cells:

- `served_count > 0`
- `mcp_isError_count == 0`
- **at least one ANSWERED call** in `mcp_per_server` (an `ok` count above zero).
  Issued is not used: the first Codex repowise cell ever run reported
  `arm_exercised: true`, one `get_answer`, `error: null` and judge 9.0/10, and
  the call itself had returned `user cancelled MCP tool call` in a run with no
  user in it.

**`c0-bare`: the INVERSE assertion, and it is not symmetry.** Finding D16 was
that the C0 control was never actually bare, and D16 was found on Claude's
`--strict-mcp-config` path. Codex isolates differently: a bench-owned
`CODEX_HOME` plus per-invocation `-c mcp_servers.…` overrides, with
`--ignore-user-config` explicitly NOT the mechanism. **That isolation has never
been verified on a bare arm under Codex.** Assert:

- no server mounted: `codex mcp list --json` under the bench `CODEX_HOME`
  returns `[]`, and `mcp_overrides(c0-bare, …)` returns no arguments
- zero MCP calls on the cell: `mcp_tools_issued == []`, `mcp_per_server == {}`,
  `mcp_isError_count == 0`
- `served_count` is null, because no server was probed

Codex's stream has **no init event**, so "zero servers mounted" is not readable
from the cell the way it is on the Claude side. The config-level view plus the
empty call ledger is the honest substitute and its limit is stated wherever the
result is used.

**`repowise`**: already proven at 19/19. Included only to keep the smoke in the
same run shape as the flagship, and as the gate's positive control.

### The detector is proved before any zero is recorded. Four sides, one of them a mutation.

A zero from a competitor arm is the most expensive reading in this workstream
and a one-sided control passes while broken. `scripts/assert_codex_proof_of_life.py`
carries `--self-test`, which must pass before it is allowed to read real rows:

| side | input | required reading |
|---|---|---|
| **positive** | a real exercised row (repowise, `mcp_per_server: {repowise: {ok: 1, error: 0}}`) | EXERCISED |
| **negative** | a real bare row (`c0-bare`, empty ledger) | NOT EXERCISED |
| **mutation A** | the positive row with its **ledger blanked** | reading must **flip** to NOT EXERCISED |
| **mutation B** | the negative row with **one `ok` call injected** | reading must **flip** to EXERCISED |

If either mutation fails to flip the reading, the detector is broken and no zero
from this gate may be recorded.

### The gate's decision rule, fixed in advance

- **An arm that passes** enters the flagship.
- **An arm that fails** is fixed and re-gated, **or is declared UNRUNNABLE UNDER
  CODEX in this file** with the failure quoted, before the flagship spends a
  cent. An arm declared unrunnable is reported as unrunnable in RESULT.md and
  **never as a zero**.
- **No arm is dropped silently, and no failure is discovered after the money is
  spent.**

### Gate predictions

| # | prediction |
|---|---|
| **G1** | at least one of the four never-run competitor arms **fails** its first Codex gate. Based on the base rate: every arm that has ever had a first cell in this workstream needed a fix that was not guessable from its README |
| **G2** | `c0-bare` passes the inverse assertion, i.e. Codex's isolation path is genuinely bare. Weak prior: 4/4 clean Codex `c0-bare` cells, none of which was checked for this |
| **G3** | `repowise` passes 2/2, consistent with 19/19 |

G1 predicting a failure is deliberate: a gate that everybody passes on the first
try is weak evidence that the gate works, and G1 failing (all four pass) is
reported as G1 failing.

## 3. RUN 1 — THE FLAGSHIP. 6 arms x 15 questions under Codex.

Arms: `c0-bare`, `repowise`, `codegraph`, `graphify`, `code-review-graph`,
`serena`, minus any arm the gate declared unrunnable. Same 15 stratified
questions as every recent django run (`stratified_shapes`, per_shape 3, seed
20260803), same `prompt_style: neutral`, same `max_turns: 15`, same reused
indexes.

**`judge_model` is deliberately UNSET.** `_resolve_judge_model` refuses a
same-family judge, so a `gpt-5.6-sol` agent resolves to `claude-sonnet-5`.
Copying `gpt-5.6-luna` across from a Claude config is what cost a launch last
session (0 cells, no spend, `da136ed`). The refusal message recommends the wrong
model for an openai agent because it looks up the `anthropic` key; **that is a
string bug and its suggestion is ignored.**

### Predictions

| # | prediction |
|---|---|
| **F1** | **repowise adoption >= 13/15** under Codex, consistent with 15/15 and 19/19 |
| **F2** | **every gated competitor arm adopts at >= 9/15**, i.e. the discovery gate was the dominant term for the whole category and not only for us. If a gated arm comes in below 9/15 under Codex, adoption is a property of that server's surface after all, and that is a finding against E13's generality |
| **F3** | the **pooled quality spread across the six arms is under 1.5 judge points**, given `S` = 0.6896 on this question set and 75.7% of the variance being instrument |
| **F4** | **no arm's pooled quality differs from `c0-bare` at p < 0.05** after Bonferroni over five comparisons. n=15 detects 0.50 at 80% power on one pair; five pairs at the same n detects less |
| **F5** | Codex cost per cell lands in **$0.05 to $0.12**, against the measured $0.065 on the repowise-only run and Claude's $0.21 |

**F4 predicts a null and it is pre-registered as the expected outcome.** This run
is bought for the adoption and proof-of-life table and for a per-shape picture,
not because 15 questions can settle quality. A significant result at n=15 after
Bonferroni would be surprising and would be reported as surprising rather than
as the headline.

### The reading table for quality, fixed in advance

| observed | reading |
|---|---|
| F4 holds (no arm separates) | **quality is inconclusive at n=15 and is reported as inconclusive.** The publishable content of this run is the adoption table, the proof-of-life table and the per-shape descriptive picture |
| repowise separates from `c0-bare` after Bonferroni | reported, with the required-n arithmetic beside it and an explicit note that it was not predicted |
| a competitor separates from `c0-bare` and repowise does not | reported in full and at the top. This is the outcome most likely to be quietly de-emphasised, so it is named here |

**No pooled number from this draw is an estimate of the arms' mean over all 48
django questions.** The allocation is equal per shape, deliberately, and the
pooled row is a convenience labelled as one.

## 4. RUN 2 — THE GO ADOPTION RE-TEST. The held-out 9.

Go adoption collapsed to **2 of 9** and reproduced at 2 of 9 on the held-out
nine. That session killed three explanations (the agent was told; it could see
the descriptions; the question-frame A/B was inconclusive by its own rule) and
never found the cause. **E13 is a fourth explanation that none of them tested,
and it is the one that just explained django.**

The same nine held-out cli/cli instances (`layerb_go_frameA_heldout.yaml`'s
`task_ids`, verbatim), the same `repowise-go` arm, the same trees, under Codex.

`c0-bare` is included as a same-run reference point and **contributes nothing to
the adoption reading**, which is a single-arm property. It is in the run because
the brief asked for it and because it keeps the run shape comparable; it is not
a control to subtract, and note that the Claude run this is compared against was
single-arm, so the run shapes are NOT identical and no cost claim crosses them.
Only 1 of the 9 instances has an existing `c0-bare` tree; the other 8 stage as
bare checkouts with no index build.

### The reading, fixed before the run

| observed | reading |
|---|---|
| **>= 7 of 9** | **the entire Go adoption finding is retired as a Claude discovery-gate artifact.** It was never a property of Go, of ContextBench, or of our question frame |
| **<= 3 of 9** | **Go is genuinely different** and E13 does not explain it. The Go finding stands and the cause remains unknown |
| **4 to 6** | **inconclusive, and it says so.** No side is picked and no post-hoc reclassification |

Predicted: **>= 7 of 9** (G-pred), on the strength of django's 15/15.

## 5. RUN 3 — THE JUDGE-AGREEMENT CHECK. Not optional if publishing.

Published Claude numbers are graded by `gpt-5.6-luna`; Codex cells resolve to
`claude-sonnet-5`. **Any table carrying both has two judges in it**, and a
reviewer finds that hole before anyone else does.

Per PLAN.md: grade a **stratified subset** with both judges and publish the
agreement. Subset: one cell per shape per harness from the flagship and from the
Claude repeats, drawn by the same seed, so the comparison spans all five
non-empty shapes on both sides. Report mean absolute difference and rank
correlation, and publish them **beside** any mixed table rather than in a
footnote.

**Pre-registered threshold**: a mean absolute difference above **1.0 judge
points** means no table may mix the two judges' cells in one column at all, and
the harnesses are reported in separate tables only. 1.114 mean absolute was the
judge noise measured on the Go run's no-tool pairs, so 1.0 is a demanding but not
arbitrary bar.

### Before any real cell is graded

**A known-perfect and a known-wrong answer are graded first**, on both judges. A
judge that scores the known-wrong answer highly is not measuring quality, and
that is checked before the agreement number exists rather than argued about
after.

## 6. Failure handling, fixed in advance for every run here

**Two kinds of failure and only one of them is retried.**

- **An auth failure means the cell never ran.** Retry it. No outcome was
  observed, so nothing is being selected on.
- **A turn-limit exhaustion is an AGENT OUTCOME.** Exclude it and name it.
  Retrying exhausted cells until they succeed deletes the run's hardest
  attempts, and in the last session **both exhausted cells were `c0-bare`**,
  which biases the surviving set toward questions the control could finish.

**Adoption is a SINGLE-ARM property.** It is counted over **every non-error cell
of that arm**, never over the paired set. Counting over pairs is what let a
control's turn exhaustion delete a treatment adoption last session and report
2/15 where the truth was 3/15. `adoption_over_pairs_only` is kept as a separate
field and is never the published figure.

**No prompt is tuned against cells that are then reported on.**

**Launch through the harness, never `Start-Process`.** Resume is on
`(task_id, condition)`, so a killed run costs nothing.

## 7. What is NOT run, and why

- **Rung 9 is not launched.** At the Claude adoption rate its quality column
  would spend most of a $135 budget on cells where the tool under test is never
  called.
- **cli/cli with competitors is not started.** No competitor trees exist for Go:
  an index build per arm per instance, hours of machine time. It waits until
  runs 0 to 2 are done and read.
- **Competitors are not re-run under Claude.** Their 2026-08-03 cells stand.
- **Layer A's sealed 42 and ContextBench's 12 Go test instances are not
  touched**, by any run in this arc, and no script in it loads them.
- **Claude repeats 3 to 5 are not run.** If Codex is the quality instrument they
  buy a firmer `S` for a column that is no longer primary.

## 8. Deliverable

`local-stash/competitive-proof/50-results/layerb-codex-bakeoff/RESULT.md`,
written as the work proceeds, carrying:

- this pre-registration quoted against what happened, **including every
  prediction that failed**
- the **per-arm proof-of-life table** from run 0, with any arm declared
  unrunnable named as unrunnable and not as a zero
- **adoption counted per arm over that arm's own non-error cells**
- a mandatory provenance block: commits, trees and their HEADs, build, indexes,
  models, judges, exact configs, wall clock, dollars with their source
- no em dashes
