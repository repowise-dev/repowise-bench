# Pre-registration: cocoindex on the ContextBench SEALED 42

**Written before a single index was built and before any cell ran.** This adds
a sixth arm to a table that is **already published** on `docs/BENCHMARKS.md`,
against a **sealed set that is evaluated once**. There is no second attempt.

| # | step | what | cost |
|---|---|---|---|
| 0 | instrument checks | free, offline, section 2 | $0 |
| 1 | build | 42 worktrees + `ccc index` each | ~2-3 h machine, ~4 GB, **$0 API** |
| 2 | gate | proof of life + extractor, on DEV instances only | $0 |
| 3 | the sealed run | 42 cells, one shot | **$0 API** |

**Zero API cost throughout.** cocoindex embeds locally with
`sentence-transformers` / `Snowflake/snowflake-arctic-embed-xs` (384 dim) and
needs no key; Layer A grades deterministically against gold spans with no LLM
judge anywhere. The entire cost of this run is machine time and the
irreversibility of the seal.

---

## 0. WHAT IS BEING ADDED, AND TO WHAT

The published table, `docs/BENCHMARKS.md`, on the 42 sealed ContextBench
instances:

| Tool | File coverage | Precision | Files served |
|---|---:|---:|---:|
| repowise (`get_answer`) | 0.876 | 0.087 | 19.2 |
| repowise (`search_codebase`) | 0.742 | 0.168 | 8.2 |
| CodeGraph | 0.610 | 0.093 | 14.0 |
| Graphify | 0.546 | 0.033 | 34.5 |
| code-review-graph | 0.445 | 0.240 | 5.4 |

**cocoindex has never been run on ContextBench at all.** Its only Layer A row is
mui, on the development 15, which is not published and may not be.

**The set:** 30 `django/django` + 12 `cli/cli`, **42 distinct base commits**, so
42 worktrees and 42 indexes with no sharing. 227 gold files, median 4 per
instance, max 15, and **only 1 of 42 is single-file**. This is the multi-file
end of the corpus, and rung 8 measured that the single-to-multi drop holds for
the whole field (CodeGraph 0.800 to 0.419, Graphify 0.629 to 0.514, repowise
0.265 to 0.191 on the dev 70, pre-D13-fix).

## 1. THE PREDICTION THAT MATTERS, AND IT PREDICTS A LOW PLACE FOR THE NEW ARM

**cocoindex's one differentiator does not transfer to this set, and the reason
is measurable in advance.** On mui it was the only arm of six to score on
non-code gold, 2 of 8 where every other arm scored 0, and it paid for that
breadth at 5 of 30 on code gold against repowise's 20 of 30.

**The sealed 42 contain essentially no non-code gold: 1 file of 227**, and that
one is a `.txt`. The extension census is `.py` 163, `.go` 60, two Makefile-ish
paths, one `.html`, one `.txt`. So **0.4% here against 21% on mui.**

The differentiator is absent by construction, the cost it was paid for is not,
and this is registered now precisely because it will look like a post-hoc excuse
if it is written after the number.

| # | prediction |
|---|---|
| **C1** | **cocoindex places LAST or second-to-last** on file coverage among the six. Registered against us in the sense that matters: it is a prediction that the arm we chose to add will look weak, and publishing it anyway is the point |
| **C2** | cocoindex's coverage on the **12 Go instances is not worse than on the 30 python ones**, relative to its own mean. It claims 28+ languages and its mui row was JavaScript, so a language collapse would be a finding about the tool rather than about this corpus. **Note the direction of the trap:** repowise once scored **0.025 on 26 Go instances** while every competitor cleared 0.50, so a Go collapse is a thing that happens here and is not hypothetical |
| **C3** | cocoindex's **precision is high and its files-served low**, in the shape of code-review-graph (0.240 / 5.4) rather than Graphify (0.033 / 34.5), because `search` returns ranked chunks with a `limit` rather than a graph neighbourhood |
| **C4** | **no result here changes any published repowise number.** The four existing rows are not re-run. If any of them moves, something is wrong with the harness and the run is void |

## 2. INSTRUMENT CHECKS, ALL FREE, ALL BEFORE STEP 1

| check | how |
|---|---|
| `ccc version` read off the installed binary, not PyPI | recorded in RESULT.md; mui recorded 0.2.41 where the web page said 0.2.40 |
| the embedding model and dimensions | machine-global config, recorded verbatim |
| `rung8_runner.py --split test` resolves exactly the 42 sealed ids | assert against `results/bakeoff_2026_08/dev_test_split.json`; it is binding and never revised |
| `BUILD_ARMS` in `rung8_runner.py` includes cocoindex | **it does not today.** One-line change, made before step 1, not during it |
| the wrong-repo hazard is bound | `ccc mcp` resolves its project from the working directory and has no path flag. `canary_allarms.query_arm` passes `StdioServerParameters(cwd=...)` and records the launch directory per cell |
| `refresh_index` is pinned FALSE | Layer A calls the tool directly, so it can pin. `tool_args` is recorded per cell so the claim is checkable rather than asserted |

**The `cwd` binding is proven POSITIVELY, not asserted.** Two different instance
trees are asked the same question and the answers **must differ**, because a
server that resolved to one repository returns identical bytes for both. This is
the check that would catch answering every question about the wrong repository
while reporting healthy, which is finding A9's shape and cost this workstream a
rung.

**`_bg_index` is unsuppressible and costs this run nothing.** `ccc mcp` spawns
an incremental reindex at startup with no flag to disable it. On detached
worktrees pinned to their base commit it finds nothing changed, and **Layer A
publishes no cell timings**, so it cannot alter what is retrieved or what is
reported. It is named here so a future TIMED cocoindex run accounts for it
rather than discovering it.

## 3. THE GATE, AND IT RUNS ON DEV INSTANCES, NEVER ON THE 42

**A zero is only publishable once the arm is shown to be alive and the
extractor is shown to work.** Graphify once scored 0.012 MRR against a true
0.539 because a regex wanted whitespace before a path, and a broken extractor
and a genuinely bad arm produce identical summary rows.

Gate assertions, on **dev-split instances only**, one python and one Go:

- `served_count == 1` and `served_tools == ["search"]`, read off the running
  server, not the README. One of one is cocoindex's FULL surface and is not the
  graphify handicap.
- at least one **ANSWERED** call. Issued is not used.
- the response is parsed by hand once and kept verbatim beside what the
  extractor pulled out of it.
- `tool_args` shows `refresh_index: false`.

**Touching the sealed 42 before step 3 voids the run.** Not staged, not queried,
not loaded for a smoke, not used to debug the extractor.

## 4. THE READING TABLE, fixed before the numbers exist

| observed | reading |
|---|---|
| cocoindex places last or second-to-last | **published as measured**, with the non-code-gold census beside it so the reader can see the differentiator was absent by construction rather than lost |
| **cocoindex beats a repowise row** | **it is the headline, at the top of RESULT.md and carried into `docs/BENCHMARKS.md` in the same edit as any other row.** Named here because it is the outcome most likely to be quietly delayed |
| cocoindex collapses on Go specifically | reported as a language finding about the tool, with the repowise 0.025-on-Go precedent quoted so the shape is recognised rather than presented as novel |
| the four existing rows move | **the run is VOID.** They are not re-run; nothing in this run touches them |

**Both repowise rows stay unpooled.** `get_answer` and `search_codebase` are two
tools with two profiles and the page already refuses to average them.

**The median travels with the mean.** 42 instances, gold counts from 1 to 15, so
one instance must not be able to carry a column.

## 5. PUBLICATION, and what the page has to say

The row goes into the existing `docs/BENCHMARKS.md` table **only after** this
file is quoted against the result, including every prediction that failed.

Two disclosures the page owes, neither optional:

1. **cocoindex's row is dated later than the other four.** They were produced on
   the run of 2026-08-02 into 2026-08-06; this one is produced later, on the
   same sealed instances and the same fixed gold spans, with deterministic
   grading and no judge. That is why the comparison holds, and the date is
   printed rather than smoothed away.
2. **cocoindex's mui row still may not be published**, because the mui sealed 30
   are unrun. Adding cocoindex here does not unseal anything there.

## 6. FAILURE HANDLING

- **A build failure is not a zero.** An instance whose index did not build is
  named and excluded, and the count of excluded instances is published beside
  the coverage figure. n is stated on every row.
- **A dead server is not a bad arm.** The gate exists for this.
- **No retry that selects on the outcome.** A transport error may be retried; a
  cell that returned a poor answer may not.
- **No tuning of anything against the 42.** The extractor, the arm config and
  the prompt are frozen when step 3 starts.
- **One shot.** If the run is botched, that is reported; it is not re-run.

## 7. DELIVERABLE

`local-stash/competitive-proof/50-results/layera-cocoindex-contextbench/RESULT.md`,
carrying this file quoted against what happened including every failed
prediction, the six-arm table with medians beside means and n on every row, the
per-language split, the gate table, the `cwd` positive proof, the build times as
their own column, and a provenance block naming the `ccc` version off the
binary, the commit, and the exact instance ids. No em dashes.
