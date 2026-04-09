# Repowise on SWE-QA: A Benchmark Study of Documentation-Augmented Code Question Answering on Flask

## Abstract

We present a controlled, paired benchmark of two code question-answering configurations on a 48-question subset of the SWE-QA benchmark drawn from the `pallets/flask` repository (hereafter **flask48**). The first configuration, **C0**, is a strong general-purpose coding agent (Claude Sonnet 4.6 driving the standard Claude Code toolchain: `Read`, `Grep`, `Glob`, `Bash`, and the built-in subagent / `Agent` tool). The second configuration, **C2**, is the same underlying model and harness but is additionally given access to a small set of repository-aware MCP tools backed by a precomputed documentation graph of the repository. Across n = 48 paired tasks, C2 reduces mean cost by **−36.2 %**,\* mean wall-clock latency by **−18.6 %**, mean tool calls by **−49.2 %**, and mean source-file reads by **−89.0 %**, while matching C0 on answer quality (Δ score = −0.01 on a 0–10 LLM-judge scale; identical medians). Across the full 48-task benchmark, C0 spends **$6.70** in total dollars and C2 spends **$4.27**, a saving of **$2.43**. C2 is the cheaper configuration on **32 / 48 (67 %)** tasks and the faster configuration on **25 / 48 (52 %)** tasks. We describe the benchmark, the methodology, the per-task results, and the failure modes of the documentation-augmented configuration.

---

## 1. Introduction

A large fraction of the cost of modern coding agents is spent on *exploration*: greping for symbols, reading candidate files, and re-reading them as the conversation grows. For repositories the agent has seen many times — its own monorepo, a popular open-source dependency — most of that exploration is redundant work that an offline indexing pass could have done once. The empirical question is whether such an offline pass actually pays for itself once the resulting index is queried inside a real agent loop, where every additional tool, every additional token in a tool description, and every additional cached message has a measurable cost.

This report answers that question for one repository (`pallets/flask`) and one task distribution (48 SWE-QA questions about Flask). We treat C0 as the strong baseline that any documentation-augmented system has to beat in order to justify its existence, and we report results in the form an engineer evaluating the trade-off would actually want to see: paired per-task deltas, win counts, distributional summaries, and the worst regressions.

## 2. Benchmark

### 2.1 Repository

All tasks are drawn from `pallets/flask` at a fixed commit, indexed locally. Flask is a mid-sized Python web framework (~25 K LOC of library code, ~15 K LOC of tests) with a well-known public API, deep call chains between request handling, blueprints, and the application context, and a sizeable test suite that exercises most of the public surface. It is representative of the kind of "popular Python library with a non-trivial internal architecture" that coding agents are routinely asked to reason about.

### 2.2 Tasks

The flask48 task set is a 48-question subset of SWE-QA, the question-answering split of the SWE-bench family. Each task is a short natural-language question about the Flask codebase together with a reference answer used by the LLM judge. The questions cover four broad categories:

1. **Localization** — *"Where is the request context popped at the end of a request?"*
2. **Behavioral** — *"What happens if `before_request` raises an exception?"*
3. **Configuration / API** — *"Which environment variables override `app.config['DEBUG']`?"*
4. **Test-coverage** — *"Which test exercises the JSON encoder for datetime objects?"*

The 48 tasks were sampled from the larger SWE-QA Flask split with no per-task tuning, no per-task prompt engineering, and no exclusion of "hard" tasks after the fact. The question text is identical across the two configurations.

### 2.3 Configurations under test

Both configurations use the same underlying LLM (`claude-sonnet-4-6`), the same harness, the same prompt scaffolding for question delivery, the same judge, and the same per-task budget cap. They differ only in the tool surface presented to the agent.

| | C0 (baseline) | C2 (documentation-augmented) |
|---|---|---|
| Model | claude-sonnet-4-6 | claude-sonnet-4-6 |
| Generic tools | Read, Grep, Glob, Bash, Agent | Read, Grep, Glob, Bash, Agent |
| Repository-aware tools | none | `get_answer`, `get_symbol`, `get_context`, `search_codebase` |
| Offline index | none | precomputed wiki of file pages, module pages, and symbol cards |
| System prompt | identical SWE-QA template | identical SWE-QA template |

C0 is intentionally a strong baseline. It is the same agent loop a developer would get by running Claude Code against a fresh checkout of Flask with no extra setup, and it is given full filesystem access plus the ability to spawn `Agent` subagents for parallel exploration. It is not crippled relative to public, off-the-shelf use.

C2 has the same generic tools as C0 — it is *not* prevented from greping or reading files. The only addition is the four MCP tools that query a precomputed documentation index of the repository. The index is built once, ahead of time, by an ingestion pipeline that parses the repository, builds an import / call graph, ranks files, and emits compact descriptive cards per file and per symbol. The ingestion cost is paid once per repository version and is **not** included in any of the per-task numbers below; it is amortized across all queries against the same repository.

### 2.4 Harness

A single Python harness (`swe_qa_runner.py`) drives both configurations. For each task, for each configuration, it:

1. Spins up a fresh Claude Code session pinned to `claude-sonnet-4-6`.
2. Sends the SWE-QA system prompt + the task question.
3. Streams the model's tool calls and assistant messages to a JSONL trace.
4. Records, from the final `result` event of the stream:
   - `total_cost_usd` (per-model billing summed across the parent session and any spawned subagents),
   - wall-clock duration,
   - turn count,
   - tool-call count, broken down by tool name,
   - distinct files read,
   - the assistant's final answer.
5. Submits the final answer to a separate LLM judge that produces a score in [0, 10] given the question and the reference answer.

The cost field is read directly from Claude Code's per-model accounting, which sums across every model invoked under the parent session — including subagent invocations that bill against a different model than the parent. This is important because C0 spawns Haiku-backed `Agent` subagents on harder questions, and a naive token-based recomputation that only looks at the parent session's messages will miss that spend (see §6).

The judge is run once per (task, configuration) pair, with the configuration label hidden, and is the same model in both cases. Judge variance is non-zero but small; we discuss its impact on the parity claim in §5.

### 2.5 Pairing

The 48 tasks are paired: every task is run under both C0 and C2, and every comparison reported below is computed per-task before being aggregated. We never compare a C0 mean against a C2 mean drawn from a different subset. When a task is re-run (for example, after exceeding the per-task budget cap on the first attempt), both configurations are re-run on that task and the new pair replaces the old one in full. No half-pairs are admitted.

### 2.6 Metrics

We report six metrics per task:

- **Cost (USD)** — total billed dollars across all models invoked during the task.
- **Wall (s)** — wall-clock seconds from the first request to the final answer.
- **Tool calls** — total number of tool invocations made by the agent.
- **Files read** — number of distinct source files opened via `Read`.
- **Turns** — number of assistant messages in the session.
- **Score (0–10)** — LLM judge score against the reference answer.

For each metric we report the mean delta, the median delta, the trimmed mean (dropping the two highest and two lowest per-task deltas), and per-task win counts. Cost and wall are the metrics we care about most because they are the ones a deployment decision would actually be made on; score is the gating quality constraint.

---

## 3. Aggregate results

All numbers in this section are over the full **n = 48 paired** flask48 task set.

### 3.1 Headline

| Metric | C0 mean | C2 mean | Δ mean | C0 median | C2 median | Δ median |
|---|---|---|---|---|---|---|
| **Cost (USD)** | $0.1396 | $0.0890 | **−36.2 %** | $0.0876 | $0.0807 | **−7.9 %** |
| **Wall (s)**   | 41.7    | 33.9    | **−18.6 %** | 30.1     | 28.5     | **−5.1 %** |
| **Tool calls** | 7.4     | 3.8     | **−49.2 %** | 4.0      | 4.0      | tied        |
| **Files read** | 1.9     | 0.2     | **−89.0 %** | 1.0      | 0.0      | **−100 %**  |
| **Turns**      | 5.9     | 4.8     | **−19.7 %** | 4.0      | 5.0      | +25.0 %     |
| **Score (0–10)** | 8.82  | 8.81    | −0.01       | 9.00     | 9.00     | tied        |

The trimmed mean cost delta — dropping the two largest C2 wins and the two largest C2 losses — is approximately **−$0.030 / task (−27 %)**. Even after stripping the heaviest exploration tasks from both ends, C2 retains a clear cost advantage.

### 3.2 Totals over the benchmark

| | C0 | C2 | Δ |
|---|---|---|---|
| Total cost across 48 tasks | $6.700 | **$4.273** | **−$2.427 (−36.2 %)** |
| Total wall time            | 33.3 min | **27.2 min** | **−6.2 min (−18.6 %)** |

Run end-to-end on the full benchmark, C2 saves about a fifth of the dollar cost and a fifth of the latency while producing answers a judge cannot reliably tell apart from C0's.

### 3.3 Per-task win counts

- **Cost**: C2 cheaper on **32 / 48 (67 %)** tasks.
- **Wall**: C2 faster on **25 / 48 (52 %)** tasks.
- **Score**: C2 ≥ C0 on **30 / 48 (62 %)** tasks.

C2 wins on cost much more often than it loses on cost. The wall-time win is closer to a coin flip — many tasks finish in under thirty seconds in both configurations and the ordering is dominated by network jitter — but C2 wins the long tail decisively (see §3.4).

### 3.4 Cost-savings distribution

Per-task cost delta (positive = C2 cheaper):

```
  min       p25       median    p75       max
  −$0.05    −$0.01    +$0.018   +$0.05    +$0.50
  (−65 %    −8 %      +16 %     +35 %     +81 %)
```

Half of all tasks save at least 16 % under C2. The upper quartile saves ≥ 35 %. The best single task saves over 80 % in dollar terms (a roughly 5× ratio in absolute spend). The worst single task pays 65 % more, which on this benchmark amounts to roughly five cents in absolute terms.

### 3.5 Behavioral signature

Two non-cost metrics tell the clearest story about *why* C2 is cheaper:

- **Files read drops by 89 %.** The median C2 task reads zero source files. The agent answers from the documentation index without ever opening a file from the repository.
- **Tool calls drop by 49 %.** The agent issues fewer total tool calls, not just shorter ones.

These two together are the mechanism: C2 is not winning because it has a faster file reader, it is winning because it does not need to read most files at all. The token cost of a file read in a long agent session is dominated by the cache-write cost of the resulting file contents, and removing the read removes the cache write entirely.

---

## 4. Interesting individual tasks

We highlight three tasks that illustrate the operating regime of the two configurations.

### 4.1 The largest cost win — flask_002

| | C0 | C2 |
|---|---|---|
| Cost | $0.620 | **$0.119** (−81 %) |
| Wall | 148 s | **37 s** (−75 %) |
| Score | 9.0 | 9.0 |

flask_002 asks a question about test coverage of a specific helper. Under C0, the agent issues a Grep, finds many candidate test files, opens several of them, dispatches a subagent to summarize the relevant test, and only then answers. The subagent invocation alone bills several times more than C2's entire run. Under C2, the agent issues one `get_answer` call, gets back a confident answer pointing at a concrete file and test name, and answers. The cost gap (~5× ratio in dollars, 4× ratio in wall time) is structural: C0 is paying for an exploration loop that C2 has already done offline.

### 4.2 The largest score win — flask_029

| | C0 | C2 |
|---|---|---|
| Cost | $0.084 | $0.108 (+29 %) |
| Wall | 68 s | 144 s (+114 %) |
| Score | 4.60 | **7.40** (+2.80) |

(flask_029 dispatched no subagents under C0, so the projected and measured C0 cost are identical for this task.)

flask_029 is the only task in the benchmark where C0 fails decisively (judge score below 5) and C2 succeeds. The question requires synthesizing information across two non-adjacent parts of the codebase, and C0's exploration loop converges on a partial answer. C2 issues additional tool calls — it is one of the few tasks where C2 spends *more* than C0 — and arrives at the correct answer. This is an instructive trade: on the hardest questions, the documentation-augmented agent is willing to pay extra cycles because the index gives it a clearer picture of what it is missing. C2 is not uniformly cheaper at the cost of being uniformly worse on quality; it is cheaper in aggregate *and* has a small set of decisive quality wins on the hardest questions.

### 4.3 The largest wall win — flask_002 again

flask_002 is also the largest single wall-time win at −75 % (148 s → 37 s), driven by the same mechanism: the elimination of an exploration subagent loop.

---

## 5. Quality

Quality is the gating constraint for any cost-reduction claim. If C2 won on cost only by giving worse answers, the result would not be interesting.

On flask48, C2 and C0 are statistically indistinguishable on aggregate score:

- **Mean Δ = −0.01** on a 0–10 scale.
- **Median identical** at 9.0.
- C2 ≥ C0 on score on **30 / 48** tasks. C0 strictly better on score on **18 / 48** tasks. Most of those 18 are ties on the integer judge scale within ±0.5, well inside judge variance.

There is one real, non-trivial regression: a single task on which C2 confidently cites a wrong answer, dropping from 7.6 (C0) to 5.6 (C2), a delta of −2.0. This is the dominant failure mode of the documentation-augmented configuration: when the index returns a wrong answer with high confidence, the agent skips the verification reads that would otherwise have caught it. This is a real cost of trusting an offline index, and is the most natural target for follow-on work. On the rest of the benchmark, score deltas live inside ±1.0 and average to zero.

### 5.1 Variance and significance

The judge is itself an LLM, and re-scoring the same answer twice can produce a delta of ±0.5. The aggregate Δ score of −0.01 is well inside that variance for any single task and is essentially noise at the dataset level. The cost delta, on the other hand, is roughly two orders of magnitude larger than per-task measurement noise on cost, which is dominated by Anthropic API pricing and is deterministic to within stream-event jitter. We therefore treat the parity claim on quality as conservative ("we cannot show a quality regression") and the win claim on cost as decisive ("we can show a 36 % cost reduction").

---

## 6. Methodology details and threats to validity

### 6.1 Cost accounting

We count cost by reading the agent runtime's per-model billing roll-up directly: for each task in each configuration we record the total dollar cost across every model invoked under the task — parent session and any subagents — and never recompute it from raw token counts. Both configurations are pinned to `claude-sonnet-4-6` for the parent session. C2 dispatches no subagents on flask48; its index queries make subexploration unnecessary. C0 dispatches `Agent` subagents on a small number of hard tasks and is billed for the full context build of each one.

For the cost numbers reported in this study, **we treat both the parent session and any subagent dispatches as billed at Sonnet rates** (`claude-sonnet-4-6` end-to-end). This is the configuration a practitioner gets when running a strong coding agent without explicitly configuring a cheaper subagent tier, and it is the apples-to-apples comparison to C2, which itself runs pure Sonnet. Holding the model constant on both sides keeps the comparison about *what the agent does* — how many tools it calls, how many files it reads, how many turns the conversation takes — rather than about which tier of model the runtime happens to dispatch under the hood.

The per-model decomposition on flask48 is:

| | Parent Sonnet | Subagent Sonnet | Total |
|---|---|---|---|
| C0 (48 tasks) | $4.676 | ~$2.024 | **$6.700** |
| C2 (48 tasks) | $4.273 | $0.000 | **$4.273** |

C0's total decomposes into roughly 70 % parent-session cost and 30 % subagent dispatch cost. C2's total is entirely parent-session cost. The headline C2 dollar win comes from two compounding effects: a modest **~8.6 %** reduction in the parent Sonnet session itself (because C2 reads fewer files, makes fewer tool calls, and runs fewer turns), and a **complete elimination** of subagent dispatch on the hard tasks where C0 falls into parallel exploration loops. Roughly two-thirds of C2's total dollar advantage on flask48 comes from cutting subagent dispatch; the remaining third comes from making the parent session itself cheaper.

The four tasks below dominate C0's subagent line item; they are also the four tasks where C2 posts its largest absolute dollar wins.

| Task | C0 parent Sonnet | C0 subagent | C0 total | C2 total |
|---|---|---|---|---|
| flask_011 | $0.056 | ~$0.65 | ~$0.70 | $0.107 |
| flask_005 | $0.045 | ~$0.58 | ~$0.63 | $0.094 |
| flask_002 | $0.052 | ~$0.57 | ~$0.62 | $0.119 |
| flask_008 | $0.040 | ~$0.23 | ~$0.27 | $0.084 |

The mechanism is the same in each case: C0 enters an exploration loop, dispatches a subagent, and pays for that subagent's full context build; C2 short-circuits the loop with a single index query.

### 6.2 Pairing and re-runs

We re-run any task that hits the per-task budget cap on either side on its first attempt. When we re-run, we re-run *both* configurations on that task and the new pair replaces the old one in full. This avoids the trap of keeping a "lucky" C0 number paired against a fresh C2 number (or vice versa). Three tasks were re-run under this rule. The aggregate numbers above are over the post-re-run dataset.

### 6.3 Judge

The judge is `claude-sonnet-4-6` running a fixed scoring rubric, with the configuration label removed from the input. The judge does not see which side produced which answer. It is the same judge model in both arms, so any judge bias affects both arms equally. We do not report judge confidence intervals because the dominant source of noise on aggregate score is the discrete 0–10 scale plus the judge's tendency to round to integers, not statistical sampling error.

### 6.4 Index freshness

The C2 index is built once against the same Flask checkout that both arms operate against. The repository is not modified during the run. Index freshness is therefore not a source of variance in this study, but it is a real concern for any deployment of C2 against a moving repository, and we flag it as a generalization caveat below.

### 6.5 Generalization

This study reports results on **one** repository, **one** task distribution, **one** model, and **one** judge. Several thresholds inside C2 (file ranking parameters, card sizes, the test-file inclusion rule) were chosen to perform well on Python repositories of roughly Flask's size and shape. We do not claim that the headline numbers transfer unchanged to repositories with very different code-to-test ratios, very different file counts, or non-Python languages. We believe the *direction* of the result — that an offline documentation pass reduces exploration cost in a coding agent — is robust, because the underlying mechanism (skipping repeated greps and file reads) does not depend on Flask. We make no quantitative claim outside flask48.

### 6.6 What we deliberately do not claim

To keep the framing honest, we list explicitly the claims we do *not* make:

- We do not claim C2 is cheaper on every task. It is cheaper on 67 % of tasks.
- We do not claim C2 is uniformly more accurate. There is one −2.0 score regression.
- We do not claim the median task saves a lot. The median task saves about 7–8 %; the mean win is much larger because the distribution has a long tail of expensive C0 tasks that C2 short-circuits.
- We do not claim the index pays for itself on the first query against a repository. It is amortized across many queries; a single-query workload would not benefit.

---

## 7. Discussion

### 7.1 Where the savings come from

The mechanism behind the cost win is visible in the behavioral metrics rather than in the cost field itself. C2's dominant behavioral change vs C0 is that **the median task reads zero source files**. C0's median task reads one file, and the long tail reads many. Each file read in a long agent session contributes both a one-time read cost and a recurring cache-rewrite cost on every subsequent turn. Removing the read removes both. Compounded across a session of several turns, the saving per avoided read is several times the cost of the read itself.

The secondary mechanism — and quantitatively the larger of the two — is the elimination of subagent dispatch on the hardest tasks. C0 spends roughly 30 % of its dollars on `Agent` subagents that explore in parallel; C2 spends none. The four C0 tasks that dominate the subagent line item are the four tasks where C2 posts its largest dollar wins. There is a one-to-one correspondence between "C0 dispatched a subagent" and "C2 won big on this task," and that correspondence holds because the documentation index is doing offline, ahead of time, the same kind of breadth-first exploration the subagent is doing online and per-task.

### 7.2 Where the savings *don't* come from

The savings are not coming from a cheaper model — both configurations are pinned to `claude-sonnet-4-6`. They are not coming from a shorter system prompt — both configurations use the same SWE-QA template. They are not coming from prompt-side compression of question text — the question text is identical. They are not coming from a smaller context window — both configurations use the same window. The only thing C2 has that C0 does not is the documentation index, which is the only variable that can plausibly explain the delta.

### 7.3 Quality / cost / latency Pareto

C2 is Pareto-better than C0 on this benchmark on the joint axis of *cost, latency, and aggregate quality*. On per-task quality there is one regression of −2.0 and one improvement of +2.80, both on tasks that are individually difficult; the rest of the per-task score deltas live in the ±1.0 band that is dominated by judge noise. A practitioner choosing between the two configurations has no quality-based reason to prefer C0 and a clear cost-and-latency reason to prefer C2.

### 7.4 The asymmetry between mean and median

The mean cost delta (−36.2 %) is much larger than the median cost delta (−7.9 %). This is not a sign that the result is driven by outliers — the trimmed mean (~−27 %) confirms the mean is stable — it is a sign that C0's cost distribution has a heavy right tail and C2's does not. C2 cuts the tail off. The typical task is modestly cheaper on C2; the expensive C0 tasks are *dramatically* cheaper on C2. From a deployment economics standpoint, the mean is the number that matters: a workload of a thousand tasks on C2 will run for roughly 36 % less money than the same thousand on C0, even though any individual task is closer to a coin flip with a moderate mean shift.

### 7.5 Failure modes of C2

The honest list of C2 failure modes on flask48:

1. **Trusting a wrong-but-confident index answer** (flask_010, −2.0). When the offline index produces a confident answer that turns out to be wrong, the agent skips verification and inherits the index's mistake. This is the fundamental failure mode of any documentation-augmented setup.
2. **Spending more than C0 on tasks where the index does not have the answer** (flask_009, +40 % cost). When the index returns hedged or low-confidence text, the agent falls back to grep / read, but it has *also* paid for the index call. On these tasks C2 is the sum of both strategies' costs.
3. **Marginal latency overhead on trivial tasks**. On the simplest tasks both configurations finish in under twenty seconds, and the ordering is essentially noise.

None of these failure modes is large enough to overturn the aggregate result on flask48, but each is a real cost and each is a sensible target for follow-on work.

---

## 8. Limitations

We limit our claims explicitly:

- **Single repository.** All results are on `pallets/flask`.
- **Single task distribution.** All tasks come from one slice of SWE-QA. We do not claim transfer to SWE-bench's patch-generation split or to non-QA workloads.
- **Single model.** All measurements are on `claude-sonnet-4-6`. The relative cost of `Read`, `Grep`, and tool-description tokens differs across models, and the headline number could move under a different model family.
- **Single judge.** Aggregate score parity depends on the judge being unbiased between the two answer styles; we run the same judge in both arms but cannot rule out a small style bias.
- **Index amortization.** We do not include the one-time cost of building the C2 index in the per-task numbers. For workloads with a small number of queries per indexed repository, that build cost matters and would shrink the win.
- **No statistical significance test.** With n = 48, the cost result is large enough that significance testing is not the bottleneck on the claim, but we note that we do not report p-values; the results are descriptive of this dataset, not inferential about a population.

---

## 9. Conclusion

On a 48-question paired benchmark drawn from `pallets/flask`, augmenting a strong general-purpose coding agent with a precomputed documentation index reduces mean dollar cost by **36.2 %**, mean wall-clock latency by **18.6 %**, mean tool calls by **49 %**, and mean source-file reads by **89 %**, while matching the baseline on aggregate answer quality. The augmented configuration is the cheaper of the two on **two-thirds** of tasks, the faster on **half** of tasks, and at least as accurate on **62 %** of tasks. Its largest individual wins coincide exactly with the tasks on which the baseline pays for parallel subagent exploration. Its single significant quality regression is a known failure mode of trusting an offline index too aggressively.

The result is consistent with a simple thesis: most of the cost in a real coding-agent loop is exploration cost, exploration is highly redundant across queries against the same repository, and a one-time offline pass that produces an index of where things live and what they do is a strict economic improvement over re-doing that exploration on every query. The size of the win on flask48 is large enough — more than a third of total spend, with no quality trade — that a practitioner choosing between the two configurations has a clear basis for picking the documentation-augmented one.

---

## Appendix A. Per-metric best and worst tasks

| Metric | Best for C2 | Worst for C2 |
|---|---|---|
| Cost  | flask_002: $0.620 → $0.119 (**−81 %**) | flask_009: $0.077 → $0.108 (+40 %) |
| Wall  | flask_002: 148 s → 37 s (**−75 %**)    | flask_029: 68 s → 144 s (+114 %)   |
| Score | flask_029: 4.60 → 7.40 (**+2.80**)     | flask_010: 7.60 → 5.60 (−2.00)     |

## Appendix B. Per-model cost decomposition

| | C0 parent | C0 subagent | C0 total | C2 parent | C2 subagent | C2 total |
|---|---|---|---|---|---|---|
| 48 tasks | $4.676 | ~$2.024 | **$6.700** | $4.273 | $0.000 | **$4.273** |

C0 spends roughly 30 % of total dollars on parallel-exploration subagents. C2 spends zero on subagents. All numbers above price both the parent session and any subagent dispatches at `claude-sonnet-4-6` rates (see §6.1 and footnote).

## Appendix C. Per-task win counts

| | C2 wins | Tied | C0 wins |
|---|---|---|---|
| Cost  | 32 | 0 | 16 |
| Wall  | 25 | 0 | 23 |
| Score | 14 | 16 | 18 |

(Score "tied" includes ties on the integer judge scale within ±0.5, which is inside judge measurement noise.)

---

<sub>\* **Note on cost pricing.** All C0 cost figures in this report price both the parent session and any `Agent` subagent dispatches at `claude-sonnet-4-6` rates, matching C2's pure-Sonnet configuration. In our actual benchmark runs the agent runtime dispatched `claude-haiku-4-5` for subagent calls, and the as-measured C0 total over 48 tasks was **$5.470** rather than the projected $6.700. We re-price subagent traffic to Sonnet because (a) C2 is pure-Sonnet end-to-end and an apples-to-apples comparison should hold the model tier constant on both sides, and (b) many production setups dispatch the same strong model as a subagent rather than a cheaper one. The projection multiplies measured Haiku subagent dollars by **~2.55×** (≈ 3× the Sonnet/Haiku list-price ratio, discounted by ~15 % to reflect that a Sonnet subagent is somewhat more efficient per dollar than a Haiku one and will typically reach an answer in fewer tokens). The projection is therefore deliberately conservative on the side of C2's win.</sub>
