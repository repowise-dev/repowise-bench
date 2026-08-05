# Pre-registration: is the adoption collapse ours, or the category's?

**Committed before these cells were run and before a cent was spent on them.**
This is an addition to `layerb_repeats_django.PREREGISTRATION.md`, not a
revision of it. Nothing in the parent pre-registration changes: not the primary
quantity, not the S <= 0.50 / S >= 1.00 decision rule, not the predictions.

## Why this exists, and it is a mid-run addition, which is stated rather than hidden

Repeat 1 of the noise-floor run returned **adoption 4 of 15** for the repowise
arm. That is against **12 of 15** in the payload re-run two days earlier and
**15 of 15** in SESSION10 two days before that, on the **same build**
(`172ce0b8`), the **same reused index**, the **same 15 questions**, the **same
`claude-sonnet-5`**, and the **same `neutral` prompt style**. Nothing on our
side changed between the three readings.

It is not plumbing. Every cell recorded `served_count: 11`,
`mcp_isError_count: 0`, and four cells called `get_answer` successfully through
the identical configuration. By the Go run's classification this is a DECISION,
not a REFUSAL.

**This addition was proposed by Raghav after seeing that number**, and the
sequence is recorded here because a design that changes after data arrives is
exactly what this workstream distrusts in other people's pages. Two things
contain the damage:

1. It **adds** a question and answers it with new cells. It does not re-cut,
   re-weight or reinterpret the parent run's data, and it cannot change the
   parent verdict.
2. Its own prediction is committed below, before its cells run.

## The question

**Is the adoption collapse a property of repowise's tool surface, or of the
harness and model on this day?** Those have opposite consequences:

- **Category-wide** -> adoption is an unstable measure for everyone, the
  adoption table (repowise 15/15, codegraph 13/15, serena 4/15,
  code-review-graph 0/15) needs error bars before it is published as our best
  Layer B claim, and our 15/15 was partly luck.
- **Ours alone** -> something about our surface stopped being chosen, that is a
  product finding rather than an instrument finding, and the adoption row is
  worse news for us than the published table says.

## What is run

**codegraph only, the same 15 stratified django questions, 2 repeats, 30
cells, roughly $6.**

- `c0-bare` is **not** included. Adoption is a single-arm property: whether the
  agent calls the mounted server is decided inside one cell with one server. A
  control arm would double the cost and answer a different question (quality),
  which this addition explicitly does not ask.
- **A separate experiment, not a third arm on the repeats.** Adding an arm
  changes how many cells sit between an arm's own consecutive cells, which is
  finding E11 and would contaminate the cost column the repeats exist to
  measure. It would also make repeats 3 to 5 non-identical to repeats 1 and 2,
  and "nothing varies between repeats" is the design.
- **Runs after the repowise repeats**, never interleaved with them.
- Index **reused, not rebuilt**: `bakeoff/lb-codegraph-django-django`, already
  built, at django `3b161e6096`, asserted equal to `repos/django/django` HEAD.
  codegraph **1.5.0**.
- codegraph is given `codegraph_explore`, chosen from **its own README** which
  says that tool "answers almost any question in one call". Unchanged from the
  2026-08-03 run, so the surface is not a variable.

## Comparability, and the one objection a hostile reader would raise

Codegraph's **13/15 was measured in a six-arm run**; these cells run in a
one-arm run. **Arm count does not affect adoption**: each cell is a single agent
invocation with a single MCP server mounted, and the agent cannot see the other
arms. Arm count affects prompt-cache warmth and therefore billed dollars (E11),
which is why no dollar figure from this addition may be placed beside the
2026-08-03 cost column.

**What is NOT controlled**: codegraph's own version may differ from 2026-08-03,
and the model may have drifted server-side. Both cut across arms equally, so
they cannot bias repowise against codegraph **within this session's cells**,
which is the comparison that matters. The cross-day comparison to 13/15 carries
both confounds and is labelled as suggestive, not measured.

## The prediction

### CG1. codegraph also collapses

Predicted **codegraph adoption at or below 9 of 15 pooled over the two
repeats**, against its 13/15 on 2026-08-03. The reasoning is that repeat 1's
collapse arrived with nothing changed on our side, which points at the harness
or the model rather than at our tool surface.

### CG2. codegraph's adoption is unstable between its own two repeats

Predicted **the two repeats differ by at least 2 cells**, and **at least one
cell flips**. Go observed per-cell adoption flipping twice under identical
conditions; if it is an instrument property it should appear for codegraph too.

### The reading table, fixed in advance

| observed | reading |
|---|---|
| codegraph <= 9/15 **and** flips between repeats | **category-wide instability.** Adoption is not a stable measure for anyone. The published adoption table needs error bars before it is used, and our 15/15 was partly luck. |
| codegraph >= 12/15 **and** stable | **ours alone.** The collapse is a property of our surface, it is a product finding, and the adoption row is worse for us than published. |
| codegraph <= 9/15 **but** stable across its repeats | drifted for both but not noisy within a session, which points at a **between-day** change (model or harness) rather than per-cell randomness. |
| codegraph >= 12/15 **but** flips between repeats | unstable for both, and our 4/15 is a draw from a wide distribution rather than a level shift. |

**No reclassification after seeing the number.**

## What this addition may NOT be used for

- **No quality claim.** There is no control arm and no paired comparison. Judge
  scores are recorded and are **not interpreted**, exactly as in the Go frame
  A/B.
- **No cost claim.** One-arm run, E11.
- **It does not change the parent verdict on Layer B quality**, which is read
  off `S` from the repowise repeats alone.
