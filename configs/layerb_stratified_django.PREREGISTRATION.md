# Pre-registration: the stratified django run

**Committed before the run started.** This file exists so that no choice in it
can be read as having been made after seeing a number. The full write-up lives at
`local-stash/competitive-proof/50-results/rung6-layerB-pilot/SESSION10.md`
section 0; this is the tracked, timestamped copy of the decisions that had to be
made in advance.

Config: `configs/layerb_stratified_django.yaml`. Six arms, 15 questions, 90
cells, `prompt_style: neutral`, agent `claude-sonnet-5`, judge `gpt-5.6-luna`.

## 1. serena and code-review-graph run anyway (option a)

Expected on current evidence (serena 0 of 3 cells exercised, crg 0 of 2, across
two prompt styles): 30 clean, well-scored, fully-billed cells in which their own
servers are never called, all 30 excluded by `arm_exercised`, ~$6 spent to
measure a bare agent three times over.

Spent anyway, because **the rate is the finding**. A31 has no denominator.
0-for-15 across five question shapes is a materially stronger statement than
0-for-3 on one question, and buying it inside a run that is happening anyway is
cheaper than a separate adoption probe.

Reporting, fixed in advance:

- Zero answered MCP calls across the draw -> **no quality row and no cost row in
  any cross-tool table** for that arm. It gets an adoption row only:
  answered-MCP-call cells / cells run, per shape.
- Exercised on some cells -> quality row over the exercised cells only,
  exclusions stated as a count rather than netted out, n printed beside the mean.
- The bare-agent cells such an arm produces are **not** pooled into `c0-bare`.
  Different tree, different prompt (it names a server), different allowlist.

`neutral-described` is **not** run at n=15 for these two arms this session.
Session 9 refuted discoverability at n=1 per arm per style; doubling a 30-cell
block expected to be entirely unexercised is not worth it. Deliberate omission.

## 2. Per-slice expectations, named before the numbers exist

- **symbol-lookup**: a bare agent with Grep should be hardest to beat here. A
  loss is **expected** and is reported as expected.
- **multi-hop-flow**: Layer A measured us weakest here (rung 5, own repo,
  recall@10 0.44 multi-hop vs 0.94 single-file). A loss is two unrelated layers
  agreeing on one defect.
- **architecture-why**: largest slice in the population (16 of 48); where a
  synthesised answer should pay for itself if it ever does.
- **performance-why**: 4 questions, a shape PLAN.md's taxonomy did not have.
  Reported as its own slice, not pooled away.
- **history-why**: empty, 0 of 48. Published as it stands. Nothing adjacent is
  relabelled into it.

## 3. Equal allocation, so the pooled row is a convenience

3 per non-empty shape. The population is 16 / 14 / 9 / 5 / 4 / 0. **A pooled
mean over these 15 is not an estimate of any arm's mean over all 48**, and every
pooled figure must carry that sentence.

## 4. What the result is FOR

A **direction check**. It does not create improvement work; it orders work that
is already justified elsewhere. Layer B has no dev/test split, so a change
justified by a Layer B number is tuned against the only set that exists.

| this run shows | then |
|---|---|
| loss on multi-hop-flow | multi-hop gating (rung 5) first |
| loss on architecture-why | `get_answer` payload confirmed as the weak path |
| loss on symbol-lookup | expected; say so; no work created |
| no clear loss | `get_answer`, on the Layer A test-split evidence, which stands without this run |

Validation for any change must be on something Layer B never saw: the 25 flow
questions in `local-stash/agent-context/bench/eval/repowise_retrieval_v2.yaml`,
and `mui` (pinned `b8a28e13`). Layer B is then re-run once, at the end.

## 5. The draw, printed before the run

`harness.question_shapes.stratified`, seed 20260803, 3 per non-empty shape,
computed from `data/swe_qa/django_question_shapes.json` (classified in session 9
from question text alone, before any per-question performance was looked at).

| shape | n in population | drawn |
|---|---:|---|
| architecture-why | 16 | django_004 django_006 django_020 |
| multi-hop-flow | 14 | django_017 django_040 django_044 |
| symbol-lookup | 9 | django_014 django_034 django_045 |
| cross-file-impact | 5 | django_000 django_008 django_011 |
| performance-why | 4 | django_028 django_031 django_033 |
| history-why | 0 | none |
