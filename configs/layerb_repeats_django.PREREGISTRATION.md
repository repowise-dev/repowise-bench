# Pre-registration: the django noise floor, 5 identical repeats

**Committed before the run started and before a cent was spent.** This session
exists to end a class of experiment rather than to produce a result, so the
decision rule has to be fixed before the number exists. If it were written
afterwards, a sigma of 0.8 could be read as "salvageable" or "dead" at will, and
that is precisely the freedom this workstream criticises in other people's
benchmark pages.

The commit carrying this file is the one immediately preceding the run.

---

## 1. Why this run exists

`00_START_HERE.md` carries the arithmetic. At 80% power, paired, the number of
cells needed is `n = 7.849 * sigma^2 / delta^2`, where `sigma` is the standard
deviation of the paired difference across cells. Substituting Go's measured
`sigma = 1.235` gives the published table's `n = 11.96 / delta^2`.

Our own measured ceiling for Layer B quality is **+0.08 to +0.25 judge points**
(`layerb-taxonomy/RESULT.md` section 6b). At Go's sigma, detecting the best case
we believe exists needs **191 pairs (~$92)** and the realistic case needs
**765 (~$367)**, which is more than the entire remaining budget.

**Every Layer B quality experiment this workstream has run was underpowered by
roughly an order of magnitude, including the ones that produced numbers we
liked.** SESSION10's +0.13, the payload re-run's -0.02 and the Go run's +0.556
all sit inside the same band.

But `sigma = 1.235` was measured on **Go**, on 7 bare-vs-bare pairs, on a task
shape and rubric that do not transfer. **Every published Layer B claim is
django.** So the arithmetic that would kill Layer B rests on a number measured
somewhere else. This run measures it where the claims live.

## 2. What is run

**Five repeats of the identical run.** Nothing varies between them:

- the same 15 stratified django questions (`stratified_shapes`, per_shape 3,
  seed 20260803, the identical ids SESSION10 drew and the payload re-run reused)
- the same two arms, `repowise` and `c0-bare`
- the same binary: worktree `C:\Users\ragha\Desktop\repowise-layerb2` at
  `172ce0b8`, carrying #1306
- the same reused index tree `bakeoff/lb-repowise-full-django-django` at django
  `3b161e60964a`, asserted equal to `repos/django/django` HEAD before launch
- the same agent (`claude-sonnet-5`, max_turns 15), judge (`gpt-5.6-luna`),
  prompt style (`neutral`), `max_workers: 1`, no warm-up
- the same config, copied verbatim from `layerb_payload_rerun_django.yaml`, with
  only `experiment_name`, `results_dir`, `logs_dir` and `budget.max_total_usd`
  changed. A machine-checked key-by-key diff against the payload re-run config
  returns exactly those four keys and nothing else. `max_total_usd` drops from
  20.0 to 9.0 because the tracker is per-process and five repeats through one
  20.0 ceiling would be a $100 rail rather than a rail; 9.0 is 58% above the
  payload re-run's measured $5.68 for the identical 30 cells. It is a spend
  ceiling, not a knob the run reads, but it is named here because "nothing
  varies" has to survive a hostile diff

The five configs get separate results directories **because the runner resumes
on `(task_id, condition)`**. Pointed at one directory, repeats 2 to 5 would skip
every cell as already complete and the run would silently cost nothing and
measure nothing. That is a detector failure of exactly the kind this workstream
keeps catching, so it is named here rather than discovered later.

150 cells, roughly $28 at the payload re-run's measured $5.68 per 30.

**Repeats run sequentially, rep1 through rep5, and the boundary is not assumed
away.** Each repeat starts immediately after the previous one ends, so the
ambient prompt-cache state at a repeat's first cell differs from its state at
cell 30. The gap between an arm's own consecutive cells is **measured per cell**
and reported (E11), not corrected for.

## 3. The primary quantity, defined before it is computed

**`S` = the mean, over the five repeats, of the within-repeat standard deviation
of the paired difference `d_i = mean(judge, repowise, cell i) - mean(judge,
c0-bare, cell i)` across the 15 cells.**

`S` is chosen because it is **exactly the estimator a single n=15 run produces**,
and therefore exactly the sigma that any future paired Layer B design would be
powered against. It contains both between-cell heterogeneity and single-run
noise, which is correct: an n=48 design draws 48 distinct cells and pays for
both.

Judge score per cell is the **mean of the five dimensions**, which is the
reduction every prior Layer B result used. No re-weighting.

Secondary, and free because there are repeats:

- **`S_noise`**: the square root of the mean, over cells, of the within-cell
  variance of `d` across the five repeats. This is the pure test-retest term.
- **`S_cell`**: the between-cell term, `sqrt(max(0, var(cell means of d) -
  S_noise^2 / 5))`.
- **Per-arm test-retest sd** of the raw judge score, `repowise` and `c0-bare`
  separately. This is the number directly comparable to Go's 7c reading.

`S^2` should approximately equal `S_cell^2 + S_noise^2`. If it does not, the
decomposition is reported as failed rather than patched.

## 4. THE DECISION RULE, fixed here

| observed `S` | verdict | what follows |
|---|---|---|
| **`S` <= 0.50** | **Layer B quality is SALVAGEABLE** | n=48 detects `0.404 * S` <= 0.20, at or below the +0.25 ceiling. **Rung 9 at n=48 becomes the right next spend** and the parked decision reopens. |
| **0.50 < `S` < 1.00** | **INCONCLUSIVE, and the spend decision is Raghav's** | Report the required n for delta 0.25 and 0.125 at the observed `S`. Do **not** decide it in-session, and do not round toward the answer the session prefers. |
| **`S` >= 1.00** | **Layer B quality is formally DEAD** | Detecting the +0.25 best case needs >= 126 pairs and the realistic +0.125 case >= 502. **Stop paying for Layer B quality. Never publish a quality delta from it, in either direction, including the favourable ones already measured.** |

The detectable effect at n=48 is `delta = S * sqrt(7.849 / 48) = 0.404 * S`.

**No reclassification after seeing the number.** If `S` lands at 0.99 or 1.01 it
is read off this table as written.

## 5. Predictions, so a post-hoc read is visible as one

### P1. `S` will land in the INCONCLUSIVE band, closer to the DEAD boundary

Point prediction **`S` between 0.7 and 1.1**. django is a smaller, better-known
repo with authored gold answers and a same-family rubric, so it should be
quieter than Go's 1.235, but the workstream's quoted 0.46 was itself estimated
once and every direct measurement so far has come back larger than the estimate
it replaced.

### P2. `S_noise` will be the majority of `S`

Predicted `S_noise^2 / S^2 > 0.5`. If it is not, the variance is between cells,
which would mean a paired design is more efficient than assumed and the reading
changes. Stated in advance because it is the one way this session's verdict
could be too pessimistic.

### P3. Adoption is unstable, and 15/15 vs 12/15 is variance

Predicted: **at least one cell flips** its `arm_exercised` verdict across the
five repeats, and the per-repeat adoption count spans **at least 2**. Go
observed a flip twice (`03f04397`, `eb5704a5`) at n=9 without measuring its
rate. If instead all five repeats return an identical count with no cell
flipping, adoption is a stable measure and the 15/15 to 12/15 drop was a real
change, which would be the strongest positive result available here.

### P4. Tool choice is less stable than adoption

Predicted: **more cells flip `get_answer` vs `search_codebase` than flip
adoption.** The payload re-run saw `get_answer` fall 15/15 to 10/15 while
adoption fell only 15/15 to 12/15, with nothing changed on our side.

### P5. Dollars move more than output tokens across repeats (E11)

Predicted: the **range across the five repeats of the dollar delta** (repowise
against c0-bare, percent) is **larger than the range of the output-token
delta**. E11 says the dollar column carries run-shape variance that the token
column does not. If it fails, E11 is weaker than stated and that gets written
down.

### P6. No stable quality effect

Predicted: the pooled 75-pair mean `d` lies within **+/- 0.5** of the payload
re-run's -0.02, and **no repeat's own 15-cell mean `d` is significant at
p < 0.05** on a paired t-test after Bonferroni across the five. If a single
repeat does come back significant while the others do not, that is a
demonstration of the false-positive rate, not a finding, and is reported that
way.

## 6. What this run may NOT be used for

- **No quality claim, in either direction.** Whatever the pooled `d` is, it is a
  measurement of the instrument, not of the tool. The run is designed so that
  every pair is a repeat, which means a favourable pooled `d` is the least
  interesting thing it could produce.
- **No tuning.** Layer B still has no dev/test split.
- **No competitor comparison.** Competitors are not run.
- **No pooling with Go.** Different task shape, gold and rubric.
- **The dollar column may not be placed beside any run with a different arm
  count** (E11).

## 7. Validity conditions, checked before grading

- Every cell asserts `arm_exercised` from `mcp_per_server` and **cross-checks it
  against the recorded flag**; a disagreement is reported, not resolved by
  preferring one source.
- Cells with `error` set are excluded from `S` and named. The
  variance-decomposition uses only cells complete in all five repeats and both
  arms; its n is stated separately from the per-repeat n.
- **The adoption and tool-choice detectors carry a two-sided proof** run on the
  payload re-run's data before this run's data exists: the detector must read a
  positive where a positive is known and a zero where a zero is known. A
  detector that can only ever return the value we expect is what produced three
  false zeros on 2026-08-05.
- Which repowise **tool** each cell called is reported per cell. A cell that
  never called the tool under test measures nothing about it, and its `d`
  measures agent-plus-judge variance only, which for this run is the point
  rather than a defect.

## 8. Instrument state at pre-registration time

Recorded before the run so it cannot be reconstructed favourably afterwards.

- `assert_embedder.py`: `c8emb-repowise-cli` **1536**/1154 LIVE,
  `c8emb-repowise-django` **1536**/3423 LIVE. The mock 8-dim pair is unchanged
  by design and is why the script exits 1.
- **Arm index under test**: `bakeoff/lb-repowise-full-django-django`,
  `wiki_pages` at **1536 dims, 4297 rows**. Tree HEAD `3b161e6096` equals
  `repos/django/django` HEAD `3b161e6096`, so no rebuild is triggered.
- **Build**: `repowise-layerb2` at `172ce0b8`, clean tree, `repowise.server` and
  `repowise.core` both verified to resolve into the worktree, `b24226c0` (#1306)
  verified an ancestor.
- **Judge control, standing rule 9, run before any spend**: known-perfect
  **10.0**, known-wrong **1.2**, discrimination **8.8**, verdict **PASS**
  (`50-results/layerb-repeats/judge_control.json`).
- **Detector proof, four-sided, run before any spend** and against a run whose
  answer is already published so it cannot be adjusted to fit
  (`50-results/layerb-repeats/detector_proof.json`):
  - POSITIVE: adoption reads **12/15** and `get_answer` **10/15** on the payload
    re-run, matching its published table exactly.
  - NEGATIVE: the three known misses read NOT exercised with empty tool lists.
  - MUTATION: blanking `mcp_per_server` in memory on a known-positive cell flips
    the reading to NOT exercised. This is the side the two broken detectors of
    2026-08-05 would have failed, because it proves the reading is computed from
    the evidence rather than from a field that merely correlates with it.
  - CROSS-CHECK: `mcp_per_server` and `arm_exercised` agree on all 15, and the
    mutated cell is correctly reported as a disagreement.
  - Verdict **PASS**.
