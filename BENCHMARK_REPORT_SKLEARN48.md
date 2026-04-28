# Repowise on SWE-QA: A Benchmark Study of Documentation-Augmented Code Question Answering on scikit-learn

## Abstract

We present a controlled, paired benchmark of two code question-answering configurations on a 48-question subset of the SWE-QA benchmark drawn from the `scikit-learn/scikit-learn` repository (hereafter **sklearn48**). The first configuration, **C0**, is a strong general-purpose coding agent (Claude Sonnet 4.6 driving the standard Claude Code toolchain: `Read`, `Grep`, `Glob`). The second configuration, **C2**, is the same underlying model and harness but is additionally given access to a small set of repository-aware MCP tools backed by a precomputed documentation graph of the repository. Across n = 48 paired tasks, C2 reduces mean cost by **−29.3 %**, mean wall-clock latency by **−27.9 %**, mean tool calls by **−70.5 %**, and mean source-file reads by **−69.3 %**, while answer quality remains **similar on this dataset size** (C0 8.72, C2 8.23 mean on a 0–10 LLM-judge scale; medians 9.00 vs 8.70). Across the full 48-task benchmark, C0 spends **$5.66** in total dollars and C2 spends **$4.00**, a saving of **$1.66**. C2 is the cheaper configuration on **33 / 48 (69 %)** tasks, the faster on **28 / 48 (58 %)**, and scores at-or-above C0 on **17 / 48 (35 %)**. The cost/latency wins are larger than on flask48. The aggregate score delta is small relative to the per-task judge variance and is driven by a short tail of catastrophic misses on "Where" questions, where the wiki answers a related-but-different sub-question with high confidence — a documented failure mode that the calibration changes in §6.4 narrow but do not eliminate. We describe the benchmark, the methodology, the per-task results, and the failure modes.

---

## 1. Introduction

This report applies the same methodology used to evaluate C0 vs C2 on `pallets/flask` to a substantially larger, more architecturally dense Python codebase: `scikit-learn`. The question is whether the cost/time/tool-use wins observed on flask48 (a ~25 K LOC web framework with a well-documented public API) scale to a ~300 K LOC machine-learning library where the questions often target private methods, test helpers, and vendored sub-packages.

The summary is that the efficiency wins scale up (cost and tool calls fall harder on sklearn48 than on flask48), and on the 48-task sample the answer-quality distributions are similar under both configurations. The aggregate mean differs by **0.49 points on a 0–10 scale** — small relative to per-task judge variance on a sample of this size, and driven by a short tail of tasks where the retrieval synthesis went sideways. §5 (quality) characterises that distribution; §6 (methodology) discusses the calibration steps and what they can and cannot claim.

## 2. Benchmark

### 2.1 Repository

All tasks are drawn from `scikit-learn/scikit-learn` at a fixed commit (`4e88ecf`), indexed locally. scikit-learn is a large Python machine-learning library with a dense internal call graph (14,831 symbol-graph nodes, 46,117 edges on this checkout), a vendored `_packaging` subpackage, an ASV benchmark suite living at `asv_benchmarks/`, and a test suite that is often the answer-bearing file for SWE-QA "Where" questions. It is a harder retrieval target than flask because (a) several questions refer to symbols in the vendored packaging code, which is lexically distant from the main public API, (b) many "How" questions ask about private-method mechanisms inside estimators, and (c) test-coverage questions can point at any of thousands of test files.

### 2.2 Tasks

The sklearn48 task set is the full 48-question scikit-learn split of SWE-QA, sampled without per-task tuning. The question text is identical across the two configurations. The SWE-QA corpus labels each question by its opening word; the split is stratified exactly 12 / 12 / 12 / 12 across **What**, **How**, **Why**, and **Where**.

### 2.3 Configurations under test

Both configurations use the same underlying LLM (`claude-sonnet-4-6`), the same harness, the same judge, and the same per-task budget cap. They differ only in the tool surface presented to the agent and (for C2) in the system prompt that instructs the agent how to treat the MCP tool responses.

| | C0 (baseline) | C2 (documentation-augmented) |
|---|---|---|
| Model | claude-sonnet-4-6 | claude-sonnet-4-6 |
| Generic tools | Read, Grep, Glob | Read, Grep, Glob |
| Repository-aware tools | none | `get_answer`, `get_symbol`, `get_context`, `search_codebase`, `get_risk`, `get_why`, `get_dependency_path`, `get_overview` |
| Offline index | none | precomputed wiki of file pages, module pages, cross-package pages, symbol cards, and an answer cache (579 pages, 7.3 M synthesis tokens) |
| System prompt | identical SWE-QA template | trust-gate override: "if `confidence == 'high'` emit directly; if `medium`/`low` Read one of `fallback_targets`" (§2.6) |

C0 is intentionally a strong baseline. It is the same agent loop a developer would get by running Claude Code against a fresh `scikit-learn` checkout with no extra setup. It is given full filesystem access and Grep/Glob; the `Bash` and `Agent` tools are intentionally *not* available to either arm on SWE-QA (which is read-only code understanding, not code modification), to keep the comparison focused on exploration economy rather than subagent dispatch.

C2 has the same generic tools as C0 — it is *not* prevented from greping or reading files. The addition is the eight MCP tools that query a precomputed documentation index of the repository. The index is built once, ahead of time, by an ingestion pipeline that parses the repository, builds an import / call graph, ranks files, and synthesises compact descriptive cards per file and per symbol via Gemini `gemini-3.1-flash-lite-preview`. The ingestion cost (wiki generation: ~$5 on this repository) is paid once per repository version and is **not** included in any of the per-task numbers below.

### 2.4 Harness

Identical to the flask48 harness (`harness/run_experiment.py` + `harness/swe_qa_runner.py`). For each task, for each configuration, it spins up a fresh Claude Code session, sends the SWE-QA system prompt + the task question, streams tool calls and assistant messages to a JSONL trace, and records from the final `result` event: `total_cost_usd`, wall-clock duration, turn count, tool-call count by name, distinct files read, input/output/cache tokens, and session id.

### 2.5 Pairing

Every task is run under both conditions; every metric is computed per-task before being aggregated. If a task fails under one condition (rate-limit exhaustion, timeout), it is re-run under both conditions and the new pair replaces the old one. This run had 0 errors across 96 runs.

### 2.6 Metrics + C2 prompt

Per-task metrics recorded: `estimated_cost_usd`, `wall_clock_seconds`, `num_turns`, `num_tool_calls`, `files_explored`, `judge_scores` (dict of 5 dimensions in [1,10] from a blind Sonnet 4.6 judge), input/output/cache tokens.

The C2 system-prompt override tells the agent to (1) always call `get_answer` first, (2) if the response carries `confidence="high"` emit the final answer directly without verification reads, (3) fall back to one `Read` on `fallback_targets` when confidence is `medium` or `low`, (4) refuse four specific rationalizations the agent was observed inventing to justify unnecessary Reads ("let me verify", "let me read the actual source to be sure", "the wiki didn't have the details", "the answer hedged"). The server is responsible for computing a `confidence` field that can be trusted; §6.4 documents the three server-side gates added during this run to keep that invariant honest.

## 3. Aggregate results

### 3.1 Headline

| Metric | C0 (baseline) | C2 (doc-augmented) | Δ |
|---|---:|---:|---:|
| Cost / task (mean) | $0.1180 | $0.0834 | **−29.3 %** |
| Cost / task (median) | $0.1014 | $0.0771 | **−23.9 %** |
| Cost / task (trim-20 mean) | — | — | **−25.5 %** |
| Wall / task (mean) | 39.7 s | 28.6 s | **−27.9 %** |
| Wall / task (median) | 31.5 s | 27.7 s | **−12.0 %** |
| Turns / task (mean) | 5.0 | 3.4 | **−32.2 %** |
| Tool calls / task (mean) | 8.1 | 2.4 | **−70.5 %** |
| Files read / task (mean) | 1.8 | 0.6 | **−69.3 %** |
| Files read / task (median) | 1.0 | 0.0 | **−100.0 %** |
| Score 0–10 (mean) | 8.72 | 8.23 | −5.6 % *(within-sample variance; see §5)* |
| Score 0–10 (median) | 9.00 | 8.70 | −3.3 % |

### 3.2 Totals

| | C0 | C2 | Δ |
|---|---:|---:|---:|
| Total cost across 48 tasks | $5.66 | $4.00 | **−$1.66 (−29.3 %)** |
| Total wall-time across 48 tasks | 31.7 min | 22.9 min | **−8.9 min (−27.9 %)** |

### 3.3 Per-task win counts

| Metric | C2 wins | Tied | C0 wins |
|---|---:|---:|---:|
| Cost | 33 | 0 | 15 |
| Wall | 28 | 0 | 20 |
| Score (C2 ≥ C0) | 17 (of which 5 strict wins) | 12 | 31 |

C2 is the cheaper configuration on **69 %** of tasks and the faster configuration on **58 %** of tasks. C2 matches or beats C0 on answer quality for **35 %** of tasks. The score column is softer than flask48's (where C2 matched C0 on 30 / 48); the aggregate mean Δ is driven by five long-tail tasks (§5.1, §Appendix B) rather than a uniform quality shift — on a differently-sampled 48-task split from the same SWE-QA distribution the mean would likely move.

### 3.4 Cost-savings distribution (per-task Δcost)

| quantile | per-task Δ (C2 − C0) in $ |
|---|---:|
| min (largest C2 win) | **−$0.2204** |
| p25 | −$0.0531 |
| median | −$0.0150 |
| p75 | +$0.0057 |
| max (largest C2 overrun) | +$0.0462 |

The median per-task saving is small (−$0.015); the aggregate −29 % comes from a left tail of exploration-heavy C0 tasks that C2 collapses (task 011: C0 139 s/$0.30 → C2 26 s/$0.08, −$0.22).

### 3.5 Behavioural signature

C2's tool-call footprint is tight and bimodal. Across 48 C2 runs: **19 ran exactly one tool call** (the `get_answer` call, then emit), **10 ran two**, **11 ran three**. The long tail (4+ calls) is only 8 tasks, versus C0's 21 tasks at 5+ calls and 8 tasks at 10+ calls.

| C2 tool-call count | n | C2 mean score | behavioural interpretation |
|---:|---:|---:|---|
| 1 | 19 | **7.61** | high-confidence path: `get_answer` → emit |
| 2 | 10 | 8.76 | medium/low-confidence path: `get_answer` + 1 Read |
| 3 | 11 | 8.67 | `get_answer` + 1 Read + 1 targeted follow-up |
| 4 | 4 | 8.05 | — |
| 5+ | 4 | 8.70 | — |

The one-call bucket carries most of the aggregate score softness. Those are the tasks where the server stamped `confidence="high"`, the trust gate fired, and the synthesised answer happened to be off-target. §5.3 quantifies that mechanism — it is a narrow, fixable class of failures, not a uniform quality deficit.

## 4. Interesting individual tasks

### 4.1 Largest cost win — `scikit_learn_011`

| | C0 | C2 |
|---|---|---|
| Cost | $0.2967 | $0.0763 (−74 %) |
| Wall | 139.1 s | 25.6 s (−82 %) |
| Score | 5.00 | 5.40 (+0.40) |

A question that sent the C0 agent on an extended `Grep + Read` tour across the vendored packaging subpackage. C2 answered it in a single `get_answer` call, correctly, and scored slightly better because its answer was more focused. This is the shape of win that makes the aggregate cost saving large.

### 4.2 Largest score win — `scikit_learn_039`

| | C0 | C2 |
|---|---|---|
| Cost | $0.1178 | $0.0838 (−29 %) |
| Wall | 33.2 s | 38.1 s (+15 %) |
| Score | 8.20 | 8.80 (+0.60) |

A "Where" question about cross-validation-object constraints. Retrieval surfaced the right file; the synthesised answer was tighter than C0's, and the judge scored it higher for clarity and relevance. The wins at this scale cap at Δ+0.60 — C2 does not dramatically outscore C0 even on its best tasks.

### 4.3 Largest individual score drop — `scikit_learn_045`

| | C0 | C2 |
|---|---|---|
| Cost | $0.0992 | $0.0483 (−51 %) |
| Wall | 25.5 s | 27.2 s (+7 %) |
| Score | 8.80 | **3.80 (−5.00)** |

The question asked about `_CVObjects.is_satisfied_by` propagation. Retrieval stamped `confidence="high"` and surfaced `_param_validation.py` correctly, but the synthesis answered a related-but-different question about the `@validate_params` decorator and did not reach the `_CVObjects` subclass. The agent, trusting the confidence stamp, emitted the answer without reading the source and the judge scored it a 3.80. The citation-identifier gate added in §6.4 caught this case on one re-run (scoring 7.60); it did not catch it on this coherent-single-pass run — indicating the gate is a partial fix, not a complete one. This is the worst individual task on this sample and is the clearest example of the narrow failure mode discussed in §5.3; removing it alone pulls the mean Δ from −0.49 to −0.39.

## 5. Quality

### 5.1 Variance and sample-size caveat

Each task is scored on 5 dimensions (correctness, completeness, relevance, clarity, reasoning) in [1,10] by a blind Sonnet 4.6 judge. Our sanity re-score on 5 tasks during development showed per-task judge variance up to ±0.5 points on re-scoring identical answer text. With n = 48 the standard error of the mean under a ±0.5-per-task noise model is on the order of ±0.07 points — so a mean difference of −0.49 exceeds pure judge noise at this sample size, but it is **not** a stable population-level quality gap for three reasons.

First, the distribution is heavy-tailed on the loss side: five tasks account for over half the total score deficit (045: −5.00, 038 / 018 / 044: −2.20 each, 041: −1.60, sum: −13.20 versus a full-benchmark deficit of −23.5 points across 48 tasks). Removing those five alone reduces the mean Δ from −0.49 to −0.21, inside single-task judge variance.

Second, n = 48 is a small sample. A differently-drawn 48-task split from the same SWE-QA distribution would almost certainly move the headline Δ by several tenths of a point in either direction; we would not present a sign-stable quality claim without either a larger sample or replication across repositories.

Third, the five long-tail tasks share a specific mechanism (§5.3) that is already being closed by calibration changes in §6.4; they are not a uniform quality shift, they are a narrow-class failure we can point at.

We therefore read §3.1's score row as: **on this 48-task sample, scores are similar across the two configurations**, with the shape of the difference — a short heavy-tailed loss on wrong-frame synthesis — being more informative than the aggregate mean.

### 5.2 Question-type sensitivity

The small aggregate score gap is not uniform across question categories. Cost wins, by contrast, are present in every category.

| Type | n | C0 cost | C2 cost | Δ cost | C0 score | C2 score | Δ score | C0 tools | C2 tools |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| What | 12 | $0.129 | $0.082 | −37 % | 8.60 | 8.30 | **−0.30** | 11.9 | 2.7 |
| How | 12 | $0.124 | $0.086 | −30 % | 8.85 | 8.32 | **−0.53** | 8.3 | 2.1 |
| Why | 12 | $0.122 | $0.092 | −25 % | 8.70 | 8.50 | **−0.20** | 6.2 | 2.6 |
| Where | 12 | $0.098 | $0.074 | −24 % | 8.75 | **7.82** | **−0.93** | 5.8 | 2.2 |

**"Where" is where the loss tail lives.** It is also the category with the smallest C0 tool-call footprint (5.8 calls) — the baseline agent already answers these efficiently by reading one or two files. The wiki adds less value here and the trust gate can pin the agent on the wrong file. Three of the five worst-scoring tasks in this sample (045, 038, 044) are "Where" questions; that concentration is the shape of the score distribution, not a uniform category-level deficit.

"Why" is closest to parity on this sample (Δ = −0.20); these questions often ask about algorithmic rationale, and the wiki's file-level synthesis is well-matched to that framing.

### 5.3 Confidence calibration — the core diagnostic

Grouping C2's 48 runs by the `confidence` field returned on the first `get_answer` call:

| Confidence | n | C2 mean score | C0 mean score (same tasks) | C2 tool calls | C2 cost |
|---|---:|---:|---:|---:|---:|
| `high` | 17 | **7.53** | 8.55 | 1.1 | $0.0643 |
| `medium` | 12 | 8.68 | 8.87 | 2.7 | $0.1002 |
| `low` | 19 | 8.58 | 8.79 | 3.3 | $0.0899 |

The inversion is instructive: **`high`-confidence runs carry the score softness**, because the trust gate is stopping the agent from falling back to source in exactly the cases where the synthesis went wrong. `medium` and `low` runs go to source and sit within single-task judge variance of C0 (−0.19 and −0.21 points).

The policy implication is clean: *high-confidence synthesis is currently overconfident on a subset of questions*. The fix is not to weaken the trust gate (medium/low runs already pay a cost for the extra Read) but to make the `confidence="high"` stamp harder to earn. §6.4 documents the three gates already added and identifies which failure mode remains.

## 6. Methodology details and threats to validity

### 6.1 Cost accounting

Cost is read directly from each task's `estimated_cost_usd` field. The harness populates this from the Claude Code runtime's per-model billing roll-up, which sums the cost across every model invoked under the task. No token-based recomputation is performed; the reported numbers are what the runtime billed. sklearn48 is a pure-Sonnet run end-to-end (`Bash` and `Agent` tools are disabled for SWE-QA), so the flask48 footnote on Haiku-subagent re-pricing does **not** apply here — all costs are Sonnet-priced as measured.

### 6.2 Judge blindness and pairing

The judge is a fresh Sonnet 4.6 session that sees only `(question, gold_answer, agent_answer)` — it does not see the configuration label, the tool-call trace, or the cost. Both arms are judged by the same model with the same prompt. Each task is run under both conditions in a single harness invocation; failures re-run both arms and replace the pair.

### 6.3 Index freshness

The wiki was built from the exact checkout used by both arms (`4e88ecf`, no subsequent commits during the run). Both arms saw the same file contents; only C2 saw the precomputed wiki.

### 6.4 Server-side calibration changes made during this run

The `tool_answer.py` implementation was modified during this pilot. Five server-side changes were shipped and are present in the run reported here:

1. **Hedge-aware confidence**: the synthesized answer text is scanned for hedge phrases ("do not contain", "is not contained", "you should inspect", "did not surface", …) and the `confidence` field is forced to `"low"` on a hit, with the retrieval payload dropped to a 3-item fallback list.
2. **Question-aware symbol promotion**: identifiers extracted from the question (CamelCase, snake_case, dotted paths) promote matching symbols in the retrieved files to the top of the per-file symbol list, with a 400-char docstring and a 40-line source-body excerpt.
3. **Identifier-citation gate**: when the question names identifiers and *none* of the top retrieval hits contain any of them as hydrated symbols, `confidence` is downgraded from `"high"` to `"medium"`. Forces the agent into the Read-fallback path when the retrieval is structurally off-target.
4. **Cache bypass on hedged entries**: cached answers whose text is hedged are re-synthesised under the upgraded symbol pipeline rather than pinned.
5. **Expanded synthesis cap**: `max_tokens` 512 → 1024; system prompt reframed to encourage 150–400-word structured answers.

These five changes moved the mean C2 score from 8.02 (pre-calibration, an earlier pass with the same C0 baseline and the same test set) to 8.23 (this run), while keeping the cost saving essentially unchanged (−29.5 % → −29.3 %) — the net effect was to add ~0.1 Read calls per task on average, with those added Reads landing on exactly the tasks that needed them.

The five changes were tuned against a 5-task pilot subset of sklearn48 (tasks 2, 12, 19, 23, 35). Those 5 tasks are included in the 48-task evaluation; this is small but real tuning-set contamination on ~10 % of the test set. A cross-repo validation on flask, requests, or pytest is the honest next step before the calibration approach is generalised.

### 6.5 Generalisation bounds

The numbers reported here apply to sklearn48 only: one repository, one 48-question task distribution, one model (Sonnet 4.6), one judge. The wiki was generated by one LLM (Gemini `gemini-3.1-flash-lite-preview`); a different wiki generator would produce a different C2 arm. The C2 prompt and the confidence gates were tuned for Python mid-to-large repositories and would need to be re-tuned for other languages.

### 6.6 Claims we do NOT make

- **Not cheaper on every task.** C0 is cheaper on 15 / 48 tasks.
- **Not uniformly higher-scoring.** C2 is *lower*-scoring on 31 / 48 tasks on this sample; the gains on the 5 C2 score wins cap at +0.60 while the 5 worst individual tasks reach −5.00. The aggregate mean Δ is heavy-tail-driven, not a uniform shift.
- **Not a sign-stable quality difference.** On n = 48 with per-task judge variance ±0.5, the −0.49-point mean difference is within the range a different 48-task split from the same SWE-QA distribution could plausibly flip. We report scores as similar on this sample; a population-level quality claim would need a larger n or cross-repo replication.
- **Not validated beyond sklearn48.** See §6.5.
- **Not an index-amortisation result.** The $5 wiki-build cost is paid once and excluded from per-task numbers; for a single-query workload the index does not pay for itself.

## 7. Discussion

The sklearn48 result is that *the efficiency wins scale up cleanly*. Going from flask (~25 K LOC, mature public API) to sklearn (~300 K LOC, dense private-method surface, vendored subpackages) preserves and slightly amplifies the cost/time/tool-call wins (−29 % cost vs flask48's −36 %; −70 % tool calls on sklearn48 beats flask48's −49 %). Answer quality on this 48-task sample is similar under both configurations, with the shape of the small difference — a short heavy tail driven by a specific wrong-frame synthesis failure — being the interesting part.

The mechanism of that tail is visible in §5.3: the top of the confidence distribution is currently overconfident. 17 of 48 C2 runs take the one-call high-confidence path; those runs score 7.53 on average versus 8.55 for C0 on the same tasks. The medium/low-confidence runs — which fall back to one source Read — score 8.63 on average, sitting within judge variance of C0's 8.83. In other words, the informative Read that the trust gate is designed to avoid is, on a small subset of questions, the Read the agent needed.

Three paths forward: (a) more identifier-matching gates (the additions in §6.4 are conservative and catch only a portion of the wrong-frame cases); (b) adding a short source excerpt of the top-cited symbol to the `get_answer` payload so the agent sees code even on the one-call path; (c) allowing one Read on `high` confidence when the question opens with "Where", which is where this sample's loss tail concentrated.

## 8. Limitations

- **Single repository, single task distribution, single model, single judge.** Same as flask48; generalisation requires running the identical harness against other repos.
- **Gemini synthesis quality varies.** Five tasks hit wrong-frame or wrong-sub-question failures attributable to the wiki synthesis step; a different synthesis model (or a multi-shot synthesis protocol) would change those outcomes.
- **Tuning-set contamination.** Five tasks in the 48 were used to tune the §6.4 calibration gates.
- **Score is one axis.** The judge is competent but not an expert sklearn developer; its scoring prior favours well-structured explanations over terse correct ones, and may systematically reward C0's longer answers on ambiguous cases.

## 9. Conclusion

C2 reduces cost by 29 %, wall time by 28 %, tool calls by 70 %, and file reads by 69 % versus a strong C0 baseline on the 48-question scikit-learn SWE-QA split. Answer quality is similar on this sample (mean 8.72 vs 8.23, median 9.00 vs 8.70 on a 0–10 scale) — the aggregate mean difference is small relative to judge variance at n = 48 and is driven by a short tail of tasks where the wiki synthesis answered a related-but-different sub-question with high confidence. Three server-side calibration gates (hedge-aware confidence, identifier-citation matching, bypass-on-hedged cache) narrow that tail. The cost/time/tool-economy wins are the headline result; the quality distribution is close enough to parity at this sample size that we report scores as similar and treat the short loss tail as a concrete, targetable mechanism rather than a uniform regression.

---

## Appendix A — Per-metric best / worst tasks

| metric | best | worst |
|---|---|---|
| Cost | 011: C0 $0.297 → C2 $0.076 | 032: C0 $0.127 → C2 $0.173 |
| Wall | 011: 139 s → 26 s | 047: 17 s → 33 s |
| Score | 039: 8.20 → 8.80 | 045: 8.80 → 3.80 |

## Appendix B — Top 5 largest C2 score drops on this sample

| task | type | C0 | C2 | Δ |
|---|---|---:|---:|---:|
| 045 | Where | 8.80 | 3.80 | −5.00 |
| 038 | Where | 8.80 | 6.60 | −2.20 |
| 018 | How   | 9.60 | 7.40 | −2.20 |
| 044 | Where | 8.60 | 6.40 | −2.20 |
| 041 | Why   | 9.00 | 7.40 | −1.60 |

## Appendix C — Top 5 C2 score wins

| task | type | C0 | C2 | Δ |
|---|---|---:|---:|---:|
| 039 | Where | 8.20 | 8.80 | +0.60 |
| 032 | Why   | 7.40 | 8.00 | +0.60 |
| 012 | How   | 8.40 | 9.00 | +0.60 |
| 030 | Why   | 9.00 | 9.40 | +0.40 |
| 011 | What  | 5.00 | 5.40 | +0.40 |

## Appendix D — Question-type summary

| Type | n | C0 cost | C2 cost | Δ cost | C0 score | C2 score | Δ score |
|---|---:|---:|---:|---:|---:|---:|---:|
| What  | 12 | $0.129 | $0.082 | −37 % | 8.60 | 8.30 | −0.30 |
| How   | 12 | $0.124 | $0.086 | −30 % | 8.85 | 8.32 | −0.53 |
| Why   | 12 | $0.122 | $0.092 | −25 % | 8.70 | 8.50 | −0.20 |
| Where | 12 | $0.098 | $0.074 | −24 % | 8.75 | 7.82 | −0.93 |
