# Pre-registration: Layer B, second language (cli/cli Go, ContextBench)

**Committed before the run starts and before a cent is spent**, per the
precedent SESSION10 set and the payload re-run followed. Every prediction below
is quoted back in `50-results/layerb-go-contextbench/RESULT.md` against what
actually happened, including the ones that fail.

Config: `configs/layerb_go_contextbench.yaml`.
Arm overlay: `configs/arms.d/repowise_go.yaml`.
Draw: `50-results/layerb-go-contextbench/draw.json`.

---

## 1. What is being measured

Every Layer B number this workstream owns is django, Python, one commit, and in
every model's training data. This is the second language.

- **Repo**: `cli/cli`, Go, 949 files.
- **Instances**: 10, drawn from the 20 cli/cli instances on Layer A's **dev**
  half. **The 12 Go test instances are sealed and are not loaded by any script
  in this run.**
- **Arms**: `c0-bare` and `repowise-go`. **No competitor is re-run.** Their
  2026-08-03 cells stand and, per **E10**, may not be placed in a cost column
  beside anything measured at a different arm count.
- **Cells**: 20.
- **Build under test**: worktree `C:\Users\ragha\Desktop\repowise-layerb2` at
  `172ce0b8`, clean, imports verified into the worktree, carries **#1306**
  (`b24226c0`). **The identical binary the django payload re-run used**, which
  is what makes any cross-language statement possible at all.

### These numbers do not pool with the django 15

Three independent reasons, all structural:

1. **Different task shape.** django asks a typed question; this asks the agent
   to localise a described change.
2. **Different gold.** django has a written reference answer; this has an
   upstream diff.
3. **Different index mode.** The django Layer B tree was built by the
   `repowise_legacy` full path, which generates LLM prose. This run builds with
   standing rule 5's flags (`--no-prose --embedder openai --max-file-pages 0
   --no-workspace --no-editor-setup --yes`), which is what every published
   Layer A number rests on. See `configs/arms.d/repowise_go.yaml`.

Two benchmarks side by side. Never one column.

---

## 2. What the judge grades against, decided rather than defaulted into

**The judge grades against `patch`, the upstream diff. Not `gold_context`.**

`gold_context` is the *retrieval* target and Layer A already publishes File
Coverage against it; putting it in the judge's reference answer would make
Layer B a noisier duplicate of a number this workstream already holds. `patch`
is the only artefact that says what the change *is*, which is what an answer
should contain.

**Localisation is scored separately and deterministically.** Whether the answer,
the payload and the agent's reads name the `gold_context` files is measured by
`scripts/gold_coverage.py` with **no LLM in the loop**, so it carries none of
the judge's noise. Two orthogonal columns rather than one confounded one.

The question frame and the reference frame are fixed, identical across arms, and
therefore cannot move a delta. Both are in `scripts/prep_go_tasks.py`.

### The detector was wrong once already and the fix is pre-registered

The first version of `gold_coverage.py` accepted a two-segment tail match, so
gold `pkg/cmd/release/list/list.go` was scored found by the unrelated
`pkg/cmd/issue/list/list.go`. Its control passed anyway because the control
tested a *sibling directory*, which differs inside the last two segments, while
the colliding case differs only outside them. A suffix now counts only when
`git ls-files` at that base_commit says it resolves to exactly one file, and six
controls run before any cell is scored, two of them the colliding-tail cases.

---

## 3. The draw: 10 measured, 9 held out, stratified by difficulty

A uniform sample could return nine single-file cells and say nothing about the
claim that matters. Difficulty is proxied on two axes, both fixed before any
cell ran, neither derived from an outcome:

- **Primary (stratum): gold-file count.** A = 1 file, B = 2, C = 3+. Rung 5
  measured multi-hop retrieval at recall@10 **0.44** against **0.94**
  single-file, so C is where the retrieval claim lives and A is where a bare
  agent is already strong.
- **Secondary (within stratum): the author's own PR description length**, 72 to
  1,063 characters here. Terse means less to go on. Each stratum takes an even
  spread across its length ordering, so the shortest and longest in every
  stratum are both drawn.

| stratum | pool | drawn | held out |
|---|---:|---:|---:|
| A single file | 9 | 4 | 5 |
| B two files | 7 | 4 | 3 |
| C multi-hop (3+) | 3 | 2 | 1 |

**Stratum C is deliberately over-sampled, so the drawn 10 is NOT representative
of the pool.** RESULT.md reports per stratum and gives a pool-weighted mean
beside the raw one; weights are in `draw.json::stratum_weights`.

`cbgo_03f04397` is **forced into the held-out half**: it was smoke-tested before
this draw existed and its judge scores have been seen. A pre-registration
containing a cell whose result the author has already read is not one. This is
procedural, not outcome-dependent: excluded for having been observed, not for
what it showed. `9e8a151e` never entered the pool (55,149-char patch over 37
gold files; a repo-wide sweep, not a localisation).

### The held-out 9 exist for a reason, and it is already live

Layer B has never had a dev/test split. The smoke cell surfaced **three
candidate payload changes** (see section 6). Any of them adopted on the strength
of the drawn 10 is validated on the held-out 9 -- same repo, same language, same
adapter, never seen by the tuning. Every stratum survives in both halves, so
validation is not restricted to easy cells.

---

## 4. The prior, and it is the Go row, not the published pooled one

The published `get_answer` figures (0.810 dev, 0.876 sealed) pool django and
cli. Rung 8's pre-fix run recorded repowise at **0.025 on the 26 Go instances**
while every competitor cleared 0.50, a collapse the pooled number hid. So the
prior for a Go run is the Go row:

| Layer A File Coverage | all 70 | **Go 20** | python 50 | **the drawn 10** |
|---|---:|---:|---:|---:|
| `get_answer`, pre-fix (dev-emb1) | 0.528 | 0.646 | 0.479 | **0.667** |
| `get_answer`, post-fix (dev-fix2) | 0.810 | **0.749** | 0.835 | **0.667** |
| `search_codebase`, untouched control | 0.684 | 0.624 | 0.708 | 0.467 |

Two facts that shape the predictions below:

- **The drawn 10 are harder than the Go average** on the metric we already have
  (0.667 against 0.749), which is what over-sampling stratum C should do.
- **The gate fixes moved these ten cells by exactly 0.000.** Go's +0.103 landed
  entirely on the other ten. So nothing here rides on #1284/#1289.

---

## 5. Predictions

### P1. Adoption: 8 to 10 of 10 cells issue an MCP call

django Layer B ran 15/15 on 2026-08-03 and **12/15** on 2026-08-05 with nothing
changed on our side. A31 says adoption is decided from the tool NAME, and no
name or description differs here.

**Below 8 of 10 is a larger finding than any quality number in this file**: it
would mean adoption is question-dependent rather than name-dependent, and every
adoption figure this workstream has published is a measurement of a moment.

### P2. Tool choice: `get_answer` on 6 to 10 of the adopting cells

django was 15 of 15, then 10 of 15. Recorded per cell, because a cell that never
called the tool under test measures nothing about it.

### P3. Quality: NO detectable movement

**Predicted explicitly so it cannot be spun later**, exactly as the payload
re-run's P3 was. The judge's same-family noise floor is **0.46** (django). The
prediction is `|mean judge delta vs c0-bare| < 0.46`.

Caveat stated in advance: **that 0.46 is borrowed from django and is itself
n=1-per-cell.** Half 2 of this session measures the noise floor properly. Any
quality statement here inherits that weakness and says so.

### P4. Cost: repowise is MORE expensive than the control, and cache read is over half the gap

**This contradicts the django headline (-33.5%, then +6.5%) and it is
pre-registered because it is the prediction most likely to be wrong and the most
tempting to omit afterwards.**

Grounded in the smoke cell's mechanism, decomposed to the cent: the extra cost
was 60% cache **read**, 22% cache write, 18% output. Cache read is the
conversation prefix re-charged every turn, and a 19,086-character `get_answer`
payload arriving at turn 2 of 10 is carried through eight further turns. **A
large MCP payload is billed roughly `remaining_turns x payload`, not once.**

Predicted: mean `estimated_cost_usd` higher for `repowise-go` than `c0-bare`,
and `cache_read_tokens` accounting for **> 50%** of the mean dollar gap.

**If repowise comes in cheaper, the smoke was misleading and P4 fails.** At n=1
on a cold cache that is entirely possible, and it is the point of running 10.

### P5. Output tokens do NOT reproduce django's -13% to -17%

django's output-token advantage survived a change of run shape (-16.6% then,
-12.9% now) and is the candidate restatement of the cost claim under E10.
Predicted here: `repowise-go` output tokens within **±10%** of the control, or
higher. **If Go does land at -13% to -17%, that materially strengthens the
cache-independent claim as a cross-language property** and should be reported as
the run's most valuable result.

### P6. Localisation: payload surfaces at least one gold file on 7+ of 10, and the gap over the control is larger in stratum C than A

Grounded in Layer A's 0.667 File Coverage on exactly these ten. Stratum C is
where multi-hop retrieval should pay and where a bare agent's directory
fumbling should cost most.

### P7. The turn multiplier is real

Across `repowise-go` cells, `cache_read_tokens` correlates positively with
(payload characters x turns remaining after the payload arrived), Pearson
**r > 0.5**. This is the mechanism P4 rests on, tested directly rather than
assumed.

---

## 6. How each outcome is to be read, fixed in advance

Adapted from the payload re-run's P4 table, which returned a row the table did
not anticipate. This one includes the "both worse" and "both better" corners.

| quality | cost | reading |
|---|---|---|
| holds (inside noise) | falls | the tool is doing its job on a second language |
| holds | rises | **the payload is being paid for and is not being repaid.** Not a win. This is the smoke cell's shape. |
| falls | falls | A34: the agent has stopped covering for a thin payload. **A cost-only improvement is NOT a win.** |
| falls | rises | worse on both. Report it plainly as the run's headline. |
| rises beyond noise | either | unexplained at this n; not vindication. Do not claim it. |

**A cell that never called an MCP tool measures nothing about the tool, however
its score moved.** Both denominators are published, as in the payload re-run,
and the measuring subset is defined the same way: cells where `get_answer` was
actually exercised.

### The three payload changes the smoke surfaced, and their status

Named here so that adopting one later cannot be presented as a fresh idea, and
so the tuning boundary is on the record **before** the results exist:

1. **Byte-identical duplication.** Two 1,500-char excerpts appear in both
   `retrieval[].excerpt` and `best_guesses[].excerpt`; `retrieval[].snippet` is
   a prefix of `excerpt`. ~19.5% of payload content, re-charged every turn.
   **Information-preserving, therefore NOT benchmark tuning** -- an OSS product
   bug on its own merits.
2. **Dependency-list padding.** Excerpts are dominated by `## Depends on` lists
   of 25-34 paths including test files. **This IS tuning**: it changes what the
   agent is shown.
3. **A hedging sign-off that invites another round trip.** The smoke payload
   ended "If you want, paste the relevant snippet ... and I can point to the
   exact code path", and reported `confidence: "medium"` /
   `retrieval_quality: "weak"` with no rule attached to "medium". **This IS
   tuning.**

**Nothing in this list is changed before this run.** Items 2 and 3, if adopted,
are validated on the held-out 9.

---

## 7. Controls that must pass before any cell is graded

- Embedder: `c8emb-repowise-{cli,django}` at **1536** dims. Run and passed.
- Every build: `rc == 0` and `index_vector_dim == 1536`. `ensure_arm_index`
  refuses a mock-embedded index (finding D13).
- `arm_exercised` **recomputed from `mcp_per_server`** and cross-checked against
  the flag; disagreement is an instrument failure, not resolved in either
  direction.
- Judge control: a known-perfect and a known-wrong answer graded on a Go
  instance from this draw **before any real cell is graded** (standing rule 9).
- Gold-file detector: six controls including both colliding-tail cases.
- Arm parity: `repowise-go` differs from `repowise` in `index` and
  `description` only (`scripts/assert_arm_parity.py`).
- Launched through the harness. **Never `Start-Process`.**

---

## 8. Budget

~$0.30/cell measured on the smoke, so **~$6** for 20 cells against a $15 config
cap. Layer B total stands at roughly **$64** of the **$350** ceiling.
