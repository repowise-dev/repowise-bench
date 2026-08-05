# Pre-registration: Layer B stratified re-run with #1306 in

**Committed before the run started and before a cent was spent.** SESSION10 set
this precedent and it is the reason its result is quotable. This session has a
**strong prior**, which is exactly the condition under which a post-hoc read is
most tempting and least detectable, so the predictions are fixed here in
advance, including the one that predicts nothing will happen.

The commit carrying this file is the one immediately preceding the run.

---

## What is being re-run, and what is not

15 stratified django questions (`stratified_shapes`, per_shape 3, seed
20260803, the identical 15 task ids SESSION10 drew), two arms: `repowise` and
`c0-bare`. **Competitors are not re-run.** Their 2026-08-03 cells stand and no
table may place them beside these rows without saying ours were measured on a
later build.

The binary under test is `repowise-layerb2` at `172ce0b8` (origin/main),
carrying **#1306** `feat(answer): name what each candidate file defines, not
just its path`. The 2026-08-03 rows were produced by `49a1795c` (v0.37.0).

The **index is reused, not rebuilt**: same tree, same django commit
`3b161e60964a`, same `wiki.db`. #1306 is a query-time change and holding
retrieval byte-constant is what makes any payload difference attributable.

---

## The prior, and where it comes from

`50-results/layerb-taxonomy/RESULT.md`, sections 4, 5, 6 and 6b:

- the `get_answer` payload is worth **1.49 judge points less** than the bytes a
  bare agent fetches for itself, at matched context size, 12 of 14 decided
  cells, exact sign test **p = 0.0129**;
- **89%** of the agent's post-answer POINTED_AT searches were judged EXPAND, the
  payload naming a path and carrying none of its substance;
- thin-payload cells ran **9.5** tool calls against healthy-payload cells'
  **5.3**, pooled 7.5;
- thin-payload cells cost **-30.3%** against control, healthy **-38.2%**, pooled
  **-33.5%**;
- the quality ceiling is **+0.08 (S1) to +0.25 (S2)** judge points against
  same-family judge noise measured at **0.46**.

---

## The predictions

### P1. Tool calls fall

`repowise` pooled tool calls fall from **7.5** toward roughly **5.3**.
Direction is the prediction; the magnitude is a target, not a claim.

### P2. Cost improves

Cost against `c0-bare` improves from **-33.5%** toward roughly **-38%**.

### P3. NO detectable quality movement

**Predicted explicitly so it cannot be spun after the fact.** The ceiling is
+0.08 to +0.25 judge points and the judge's own same-family noise is 0.46. The
ceiling sits **inside the instrument**. So:

> Pooled judge quality is predicted to move by **less than the judge's noise**,
> and any movement observed, in either direction, is **not** to be reported as a
> quality result. A quality improvement here would be indistinguishable from
> noise and must not be claimed as a win.

This prediction is made **because** the prior is strong on cost. A session that
predicted quality gains and then found them would have no way to show it had not
selected for them.

### P4. A34, the trap, and the condition that makes this NOT a win

Finding A34: five thin-payload cells won or tied live **only because the agent
fetched what the payload lacked**. The published +0.13 is propped up by that
covering behaviour, so the two columns are not independent readings.

Fixed in advance:

| observed | reading |
|---|---|
| quality holds (within noise) **and** cost falls | the payload is genuinely better: a propped-up result became a robust one |
| quality **falls** **and** cost falls | **the agent stopped covering for us.** This is A34 firing. It is **NOT a win** and must not be reported as one |
| quality falls **and** cost does not fall | the change is a regression on this set |
| quality rises beyond noise | treat as unexplained, not as vindication; the ceiling says it should not happen |

**A cost-only improvement is not a win.** Written here so the run cannot later
be summarised as one.

---

## What this run may NOT be used for

- **No tuning.** Layer B has no dev/test split and never will. It is the
  confirmation, not the evidence. The `get_answer` payload work is justified by
  Layer A's sealed test half and would be justified if this run never happened.
- **No equivalence claim.** TOST is not run, so "quality at parity" may not be
  said in either direction.
- **No competitor comparison** on these rows.
- **n = 15**, one repo, one commit, one draw, equal allocation over an unequal
  population. A pooled mean here is **not** an estimate of any arm's mean over
  all 48.

---

## Instrument state at pre-registration time

Recorded before the run so it cannot be reconstructed favourably afterwards.

- `assert_embedder.py`: `c8emb-repowise-cli` **1536**/1154 LIVE,
  `c8emb-repowise-django` **1536**/3423 LIVE. Reproduces the taxonomy session
  row for row. The mock 8-dim pair is unchanged by design and is why the script
  exits 1.
- **Detector proof, two-sided, run before any spend.** #1306 reads `WikiSymbol`
  rows from an index built by v0.37.0, so "the fix silently does not fire
  against this index" was a live way to publish a false null.
  - POSITIVE (hydrator live): **209 of 256** candidate paths carry `defines`
    (**81.6%**), mean 1,434 chars per response, payload growth **1.10x**.
  - NEGATIVE (hydrator no-opped, same process, same index, same code):
    **exactly 0**. The detector can read zero when zero is true.
  - IDENTITY: served path list **set-identical on all 15**, no path added or
    dropped.
- **One order difference was found and chased rather than waved through.**
  `django_006` reordered its served-path tail (position 20 of 23) across arms.
  A same-condition replication then showed the identical cross-arm comparison
  order-identical, and a 5-pass fixed-condition rate test reordered
  `django_006` again with the change **held constant** (1 of 60 same-condition
  comparisons). So it is pipeline nondeterminism at the candidate tail, which is
  the property #1306's own validation named, and **not** the change moving
  retrieval. Set membership never moved in any test.
