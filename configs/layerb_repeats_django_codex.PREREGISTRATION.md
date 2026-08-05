# Pre-registration: is the adoption collapse a Claude thing?

**Committed before these cells were run and before a cent was spent on them.**
A second addition to `layerb_repeats_django.PREREGISTRATION.md`, alongside the
codegraph one. Neither changes the parent: not the primary quantity, not the
`S <= 0.50` / `S >= 1.00` decision rule, not the predictions.

## The question, and why it is a different question from the codegraph one

Repeat 1 returned repowise adoption **4 of 15** against 12/15 and 15/15 on the
same build, index, questions, model and prompt style. Two additions now probe
two different explanations, and they are **orthogonal**:

| addition | holds constant | varies | answers |
|---|---|---|---|
| **codegraph** (`c9b4069`) | agent harness (Claude), model, questions | the MCP server under test | is it **our tool surface**, or every server's? |
| **codex** (this file) | the MCP server (repowise), questions | the agent harness **and** its model | is it **Claude**, or every agent? |

Together they separate three candidate causes: our surface, the Claude harness
or model, and something about the questions themselves that would suppress
adoption for everyone everywhere.

## What is run

**Codex harness, repowise arm only, the same 15 stratified django questions, 1
run, 15 cells, roughly $1 to $2.**

- **repowise arm only, no `c0-bare`.** Adoption is a single-arm property. A
  control answers a quality question this addition does not ask.
- **One run, not repeats.** This measures a *level*, not a variance. The parent
  run measures variance and it does so on Claude. If Codex comes back near the
  Claude number, one run has already answered the question; if it comes back far
  away, repeats become worth buying and that is a decision for afterwards.
- Same draw (`stratified_shapes`, per_shape 3, seed 20260803), same
  `prompt_style: neutral`, same `max_turns: 15`, same reused index
  `bakeoff/lb-repowise-full-django-django`, same build `172ce0b8`.
- **Runs after the codegraph repeats**, never interleaved.

## What is NOT held constant, stated plainly

Changing harness also changes the model: Claude cells run `claude-sonnet-5`,
Codex cells run `gpt-5.6-sol`. **These are confounded and cannot be separated by
this design.** A difference means "the Claude harness or the Claude model", not
one or the other. Separating them would need the same model under both
harnesses, which neither vendor's CLI offers.

Two further asymmetries, recorded so no comparison silently assumes them away:

- **The tool-loading path differs.** In the Claude harness the repowise tools
  are deferred and must be loaded with `ToolSearch` before they can be called,
  which is a real second step at which an agent can decline. Codex mounts its
  MCP surface differently. **An adoption difference could be that mechanism
  rather than a preference**, and this run cannot tell them apart. It is the
  most likely alternative explanation for any gap and it is named in advance.
- **Cost is not comparable in either direction.** Codex reports token counts and
  no cost, so a Codex dollar figure is computed by `codex_runner.py` from list
  rates rather than reported by the CLI. No Codex dollar goes in a column with a
  Claude dollar (E11 applies twice over).

## The prior

The only existing Codex-plus-repowise data is SESSION9's: **4 of 4 cells
adopted** (3 in `layerb_codex_n3_django`, 1 in the smoke). n=4, one session,
so a weak prior, but it points high.

## The prediction

### CX1. Codex adopts at a higher rate than Claude's repeat 1

Predicted **Codex adoption at or above 9 of 15**, against Claude's 4 of 15 on
the identical questions and server. Based on the 4/4 prior and on the
`ToolSearch` asymmetry above.

### The reading table, fixed in advance

| observed | reading |
|---|---|
| Codex >= 9/15 **and** codegraph <= 9/15 | the collapse is **harness or model specific, not server specific**. Every server suffers under Claude; repowise is fine under Codex. Our tool surface is exonerated and the adoption table becomes a statement about the agent, not about the tools. |
| Codex <= 6/15 **and** codegraph <= 9/15 | **everything collapsed everywhere.** Points at the questions or at a change common to both vendors. The published adoption table is unsafe at any n and needs re-measurement, not error bars. |
| Codex >= 9/15 **and** codegraph >= 12/15 | the collapse is **ours under Claude specifically**. The worst outcome available here and a product finding. |
| Codex <= 6/15 **and** codegraph >= 12/15 | repowise is declined by both agents while codegraph is not. Also a product finding, and a stronger one. |

**No reclassification after seeing the number.** Intermediate values (7 to 8)
are reported as intermediate and no side is picked, exactly as the Go frame A/B
did at d = 2.

## What this addition may NOT be used for

- **No quality claim.** No control arm, no paired comparison. Judge scores are
  recorded and not interpreted.
- **No cost claim.** Computed rates, one arm, different vendor.
- **No cross-harness quality or cost table.**
- **It does not change the parent verdict on Layer B quality**, which is read
  off `S` from the Claude repowise repeats alone.
