# Pre-registration: the question-frame A/B, on the held-out 9

**Committed before the run and before any spend.**

Configs: `configs/layerb_go_frameA_heldout.yaml`,
`configs/layerb_go_frameB_heldout.yaml`.
Parent: `50-results/layerb-go-contextbench/RESULT.md`.

---

## 1. The question

The Go run measured adoption at **2 of 9**, and two explanations for that are
now dead:

- **"the agent was never told to use the tools"** -- it was. `prompt_style:
  neutral` names all seven tools, explains that MCP schemas are deferred, and
  says "Start with the repowise tools before reading source files."
- **"the agent could not see what the tools do"** -- the `neutral-described`
  pilot (`607d79b`) put each server's own one-sentence descriptions in front of
  the agent. Discovery moved (0 of 3 issued a `ToolSearch` before, **2 of 3**
  after) and **adoption did not move at all** (0 of 3 either way). The agent read
  accurate descriptions of `get_answer` and `search_codebase` and declined.

**What is left is task fit, and the part of task fit we control is the question
frame -- which is ours, not ContextBench's.** The benchmark supplies a PR
description; the question wrapped around it was written by this workstream, and
it ends:

> Which files, and which functions or methods within them, **does this change
> modify**, and what exactly does it change about them?

That reads as a question about a **diff**, and the agents behaved accordingly:
the first tool call on most cells in **both** arms was `git log --grep`.
repowise serves no tool for "which files did this PR touch."

---

## 2. Design: within-set, one variable

**Both frames are run on the SAME nine instances.** A cross-set comparison --
frame A's 2-of-9 on the drawn 10 against frame B on the held-out 9 -- would
confound the frame with the instances, and that comparison will not be made.

- **Instances: the held-out 9.** `cbgo_03f04397`, `3d85271b`, `3e2d031f`,
  `4606de0b`, `6e022940`, `85c030cf`, `cedbb0cb`, `eb5704a5`, `ff0cfab5`. All
  three strata are present (A: 5, B: 3, C: 1).
- **This is the pre-registered use of the held-out set**: an idea formed on the
  drawn 10, tested on nine instances the idea never saw.
- **Arm: `repowise-go` only, 18 cells, ~$4.50.** The primary outcome is
  adoption, which needs no control. **No control means no quality claim**, and
  none is made -- the noise floor on this benchmark was just measured at
  **1.114 mean absolute**, larger than any effect n=9 could resolve.
- `prompt_style: neutral` for both, matching the main run, so frame A here is
  comparable to frame A there.
- Same build (`172ce0b8`), same trees, same judge, `max_workers 1`.

### Frame B, and the rule it had to satisfy

Identical PR description, identical instruction to name files by
repository-relative path (so the judge target `patch` and the gold-file
detector are unchanged). The two frames share their first **524 characters** and
differ only in the closing sentence:

> **A:** Which files, and which functions or methods within them, does this
> change modify, and what exactly does it change about them?
>
> **B:** How does this repository currently implement the behaviour this change
> is about, and which files and functions would have to change to make it
> happen?

**The rule, fixed in advance: frame B mentions no tool, no tool name, no MCP, no
server, and does not suggest searching.** A frame that nudges toward the tool
would raise adoption and prove nothing. It is a **single variant, run once**, not
a search over wordings for the one that scores best. `scripts/prep_frame_b.py`
asserts that every instance keeps its problem statement, patch and gold files
unchanged across frames.

---

## 3. Predictions

### P1. Frame A on the held-out 9 reproduces the drawn-10 collapse

Predicted **0 to 3 of 9** exercised. If frame A comes in high here, the drawn 10
were unrepresentative and the whole Go adoption finding weakens independently of
the frame question.

### P2. Frame B raises adoption. Predicted **5 or more of 9**

The mechanism: a comprehension question about current code is the shape
`get_answer` and `search_codebase` are for, and the pilot showed the agent will
load their schemas when it can see what they do.

### P3. No quality claim, at any outcome

No control arm; noise floor 1.114. Judge scores are recorded and **will not be
interpreted**. Stated in advance.

---

## 4. The decision rule, fixed before the results exist

Let **d = (frame B exercised) - (frame A exercised)**, out of 9.

| d | reading | consequence |
|---|---|---|
| **>= 4** | the frame is a major driver | The Go adoption headline is restated as **frame-dependent**, not a property of Go. RESULT.md section 7a is rewritten. Any future ContextBench Layer B run uses a comprehension frame, and the drawn 10 would need re-running before its adoption number means anything. |
| **2 to 3** | ambiguous at n = 9 | Report as inconclusive. **Do not pick a side.** |
| **<= 1** | the frame is exonerated | The adoption collapse is a property of the task shape and the tool, not our wording, and is publishable as such. This is the outcome that makes the Go result stronger, not weaker. |

Exact McNemar on the discordant pairs is reported alongside, and at n = 9 it will
almost certainly not reach significance; the decision rests on the count, and the
count is a decision about what to believe next rather than a published number.

---

## 5. Controls

- `arm_exercised` recomputed from `mcp_per_server`, cross-checked with the flag.
- `adoption_probe.py` classifies every non-adopting cell NEVER LOOKED /
  LOOKED, DECLINED / PLUMBING. **A PLUMBING verdict voids that cell.**
- Every build asserted `rc == 0` and `index_vector_dim == 1536`.
- Both frames' rendered text captured verbatim into the result.

### A known, accepted imperfection

`ensure_arm_index` memoises per process, so each of the two runs rebuilds all
nine indexes. The parent run's fingerprint check showed a rebuild holds pages,
symbols, total content characters and the served-path SHA-256 **identical**,
changing only orphan LanceDB rows. **Adoption is decided from the system prompt
before any query is issued**, so the vector store cannot affect the primary
outcome either way. Recorded rather than waved through.
