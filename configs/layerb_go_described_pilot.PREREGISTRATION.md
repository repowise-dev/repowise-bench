# Pre-registration: `neutral-described` adoption pilot, 3 cells

**Committed before the run and before any spend.** Small (~$0.60), but a prompt
A/B is exactly where a post-hoc read is most tempting, and the decision rule for
scaling it to 10 has to exist before the 3 results do.

Config: `configs/layerb_go_described_pilot.yaml`.
Parent run: `50-results/layerb-go-contextbench/RESULT.md`.

---

## 1. What this tests, and what it does NOT

The Go run measured adoption at **2 of 9**. That is not the agent weighing the
tool and declining: on 6 of 9 cells it never issued a `ToolSearch` at all, and
**it had already been told to use the tools**. The exact system prompt every
repowise cell received under `prompt_style: neutral` includes:

> Available tools: `mcp__repowise__get_answer`, `get_context`, `get_overview`,
> `get_risk`, `get_symbol`, `get_why`, `search_codebase`
>
> These tools are loaded on demand and are NOT in your initial tool list: call
> ToolSearch with the tool name first ... **Start with the repowise tools before
> reading source files.**

So "add a line telling the agent to use the tools" is already done, and was
ignored. **Adding a second copy of an ignored instruction is not the experiment.**

The remaining structural candidate is that MCP schemas are DEFERRED: under
`neutral` the agent decides whether a tool is worth a `ToolSearch` round trip
**from the tool's NAME alone**. `prompt_style: neutral-described` adds one
sentence per tool, **taken from each server's own schema description** at cell
time, capped at 200 characters so no vendor can hand itself a longer prompt than
another. It already exists in the harness and was built for this exact problem.

**This does not test the other candidate**, which is that the question frame is
mine and reads as a git question. That is a separate experiment on the held-out
9 and is not confounded into this one: the question text here is byte-identical
to the parent run's.

---

## 2. Design

- **Cells: 3**, one per difficulty stratum, **all of which scored NEVER LOOKED
  under `neutral`** -- the cleanest possible baseline, since the outcome cannot
  regress:

  | task | stratum | author's description | `neutral` verdict |
  |---|---|---:|---|
  | `cbgo_1b8cfbf9` | C multi-hop | 115 chars | NEVER LOOKED |
  | `cbgo_3d1b3145` | A single file | 336 chars | NEVER LOOKED |
  | `cbgo_3deeea9c` | B two files | 399 chars | NEVER LOOKED |

- **Arm: `repowise-go` only.** `c0-bare` is not re-run because
  `Arm.resolved_coaching` returns `""` for any arm with no MCP server, under
  every style -- so `prompt_style` cannot reach the control, and its parent-run
  cells are valid unchanged. `scripts/assert_control_unaffected.py` asserts this
  rather than assuming it.
- **Everything else held**: same build (`172ce0b8`), same trees, same indexes,
  same question text, same judge, same agent, `max_workers 1`.

### The index must be proved constant, not assumed

`ensure_arm_index` memoises per process, so this run WILL rebuild the three
indexes before querying them (measured in the parent run:
`scripts/check_index_reuse.py`). `--no-prose` builds should be deterministic --
A33's nondeterminism is LLM prose generation, which is off -- but "should be" is
not the standard.

So `scripts/index_fingerprint.py` recorded page count, symbol count, LanceDB row
count, total content characters and a **SHA-256 of the sorted served-path list**
for each of the three trees BEFORE this run, and re-checks them after.
**Any difference means the pilot has two variables and its adoption number may
not be attributed to the prompt.**

Baseline, committed with this file:

| task | pages | symbols | lance rows | path sha256 |
|---|---:|---:|---:|---|
| `cbgo_1b8cfbf9` | 518 | 2090 | 532 | `ff1c06b3cd2ea6fe` |
| `cbgo_3d1b3145` | 801 | 3646 | 814 | `00e1a74d1c0d5902` |
| `cbgo_3deeea9c` | 379 | 1431 | 385 | `cf7d9cd7f3bfcd25` |

---

## 3. Predictions

### P1. Adoption rises. Predicted **2 or 3 of 3** exercise the arm

Baseline is **0 of 3** on these exact cells. The mechanism being tested is that
the agent could not see what the tools do; describing them should remove that.

### P2. `get_answer` is the tool chosen where the arm is exercised

Both adopting cells in the parent run chose `get_answer`, and A31 holds that
adoption is decided from the name. If `neutral-described` instead shifts choice
toward `search_codebase` or `get_context`, that is evidence the DESCRIPTIONS are
doing the work rather than the names, which is a different and more useful
finding than a bare adoption count.

### P3. No quality claim is made, at any outcome

n = 3, against a noise floor this workstream just measured at **1.114 mean
absolute** on this benchmark. Judge scores are recorded and **will not be
interpreted**. Stated in advance so a favourable number cannot be promoted after
the fact.

---

## 4. The decision rule, fixed before the results exist

| adoption observed | decision |
|---|---|
| **3 of 3** | scale to all 10 cells; the effect is worth measuring properly |
| **2 of 3** | scale to all 10 cells |
| **1 of 3** | do NOT scale. One cell of three is inside what a 0/3 baseline can produce by chance, and spending $6 to chase it is how a null gets talked into a result |
| **0 of 3** | do NOT scale. The prompt is not the lever; the question frame becomes the live hypothesis |

Fisher's exact on 0/3 against 3/3 is **p = 0.10**, so even the best outcome here
is suggestive rather than significant. **The pilot's job is to decide whether to
spend, not to produce a publishable number**, and no adoption rate from these 3
cells is reported as a result on its own.

---

## 5. Controls

- `assert_control_unaffected.py`: `resolved_coaching` returns `""` for `c0-bare`
  under both `neutral` and `neutral-described`, so reusing the parent run's
  control cells is sound.
- `index_fingerprint.py after`: all six fields identical on all three trees.
- `arm_exercised` recomputed from `mcp_per_server` and cross-checked with the
  flag.
- `adoption_probe.py`: every non-adopting cell classified NEVER LOOKED / LOOKED,
  DECLINED / PLUMBING. **A PLUMBING verdict voids the cell.**
- The rendered prompt for both styles is captured verbatim into the result, so
  the treatment is on the record rather than described.
