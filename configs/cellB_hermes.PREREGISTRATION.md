# Cell B (hermes) scored re-run: pre-registration

**Written 2026-08-27, before the index was rebuilt and before any arm ran.**
Amendments are appended with a date and never overwrite the original text.

INTERNAL. Nothing here reaches `docs/BENCHMARKS.md`, the README, a PR body or
any public surface without explicit sign-off.

Supersedes nothing. The 57 rows in `sessions_cellB_scored.jsonl` are a partial
run that stopped at arm 13 of 24 on 2026-08-12; they are **not a result**, they
are not quoted here as one, and this run does not resume them. The index they
were served predates weeks of indexer change.

---

## 1. The estimand

**Billed cost per task and oracle pass rate per task**, treated vs bare, on the
same repo, the same ordered task list and the same pinned index generation.

Primary metrics, reported together and never apart:

1. **Billed cost per task** = `input + output + cache_read + cache_creation`
   priced per model, divided by tasks completed in the arm.
2. **Oracle pass rate per task**, over the five oracle-backed tasks.

Rule, binding: **no cost number is reported in a row that does not also carry
its oracle number.** A cheaper arm that failed the task is a failure, not a
saving.

Secondary, exploratory: tool calls and MCP calls per session, raw file reads
displaced, turns per task, cost per retrieval call against the **$0.036**
break-even (last measured $0.085).

## 2. Design

- Repo: `NousResearch/hermes-agent` @ `c0106e50`.
- Ten tasks, five oracle-backed: #83389 (B03), #83131 (B04), #83807 (B06),
  #84289 (B07), #83869 (B10).
- **Short sessions, not long ones.** Three identical long bare sessions cost
  $5.00 / $8.22 / $4.82, CV 31.9%; session variance is a per-session branch that
  compounds rather than averaging down. Short sessions buy 7.78x more draws per
  dollar at indistinguishable CV, and reach a resolvable band (~17 points) for
  ~$79 where long sessions cannot resolve under 40.
- **Arm order counterbalanced**: odd reps bare-first, even reps treated-first.
- Fresh index at a pinned build, stamped onto arm trees from one golden index
  rather than re-indexed per tree. `index_vector_dim` re-asserted on every copy.
- Tree slug bumped to a new generation so no arm can land on an August tree.
- **No aborted arm is re-run inside the 5-minute prompt-cache TTL.** One arm was
  contaminated exactly this way and had to be discarded.

## 3. Predictions

Each carries a direction and a reason. Direction is what gets graded; the reason
is what gets learned from when the direction is wrong.

**P1 — Cost. Treated will not show a saving that clears noise. Predicted
direction: no significant difference, with the point estimate slightly favouring
treated (0% to -10%).**
Reason: the response-contract work bought -33.9% of payload aggregate but only
**-17.9% call-weighted**, which lands a retrieval call at $0.070 against a
$0.036 break-even — still 1.94x the threshold. A saving needs the call *count*
to fall, and cost is dominated by cache read and cache creation (44.0% and
47.9%; output is 8.1%), which scale with turns rather than with payload bytes.
The partial August run's -9% sat inside the noise floor and is the best prior
available.

**P2 — Completion. Oracle pass rate will be equal or better for treated, and
most likely equal. Predicted direction: no significant difference.**
Reason: completion was **identical arm for arm on every graded task** in the
partial run. The five oracles grade whether a specific known defect was actually
fixed, and on four of the five the defect site is findable by grep. The
mechanism by which retrieval helps is narrowing where to look, which matters on
B07 (gating spread across call sites) more than on the mechanical ones. This is
the number most worth watching precisely because "same cost, more issues
actually fixed" is a stronger claim than any token percentage — but it is not
the predicted outcome.

**P3 — Calls per session. Treated MCP calls per session will fall modestly
versus the partial run's treated arms, and total tool calls will fall less than
MCP calls do. Predicted direction: MCP calls down, total tool calls roughly
flat.**
Reason: this grades the prediction inherited from the retrieval-precision work,
which predicted a modest decrease because protecting exact identifier and path
hits should remove corrective searches and put answer-bearing evidence in the
first window more often. It also runs against a known counter-mechanism: when
payloads were trimmed hard before, the agent made **more** calls (24 -> 35) to
buy back what was cut. "Modest" is registered as under 20%; anything larger
should be treated as suspicious until the did-less detector clears it.

**P4 — The `get_symbol` run will survive.** Predicted direction: `get_symbol`
remains the most-called tool in the treated arm, and the share of its calls that
sit inside a multi-call run stays above 60%.
Reason: uncoached Codex sessions put 87.6% of `get_symbol` calls inside runs of
two or more, mean run 3.02, and 59.4% of the time the next MCP call after a
`get_symbol` is another one. Nothing in the work under test changes the contract
that makes a run happen — a response that hands back an id instead of content
has bought itself a follow-up call. If P4 fails, that is the most interesting
result in the run and needs its own explanation.

**P5 — Read displacement holds for `get_symbol` and not for `get_answer`.**
Predicted direction: raw reads of a file named by a `get_symbol` call stay rare;
reads following a `get_answer` stay materially higher.
Reason: measured 2.2% and 30.8% respectively in the uncoached corpus. The
`get_answer` figure is a trust problem rather than a size problem — on B03 the
treated arm was cheaper and wrong because it trusted a truncated answer, while
bare grepped and found both sites.

**P6 — Cost per retrieval call will not reach $0.036.** Predicted direction:
above break-even, in the $0.055 to $0.090 band.
Reason: $0.085 last measured, and the call-weighted payload reduction of 17.9%
maps to roughly $0.070 if the relationship is linear, which it only partly is.

## 4. The did-less detector, in code and not in vigilance

**Cost down + calls down + completion down is a regression, not a win.** It has
appeared twice. One arm scored -16.5% cost with **zero retrieval calls and 3 of
6 oracles**: it was cheap because it did not do the work.

Registered before the run, so it cannot be argued with afterwards. An arm is
**flagged `did_less`** and excluded from any cost claim when, against the paired
arm on the same session and rep:

- `oracle_pass_rate` is strictly lower, **and**
- `cost_usd` is lower, **and**
- either `mcp_calls` or `tool_calls` is lower.

An arm is **flagged `no_retrieval`** and excluded from any treated-arm claim
when `condition == treated` and `mcp_calls == 0`. A treated arm that never
called the tool is not a measurement of the tool.

Both flags are computed by the analysis code, emitted per row, and the flagged
row count is reported in the result whether or not any fired. A detector whose
failure path has never fired is not a detector, so both are proved against
constructed rows in a self-test before the run.

## 5. What would falsify the track's premise

Stated up front so it cannot be reframed after the fact:

- If P1 and P2 both come back flat, the honest reading is **the surface costs
  about what it saves and does not change what gets fixed**, on this repo, at
  this size, with this model. That is a publishable-internal negative and it is
  the modal outcome.
- If cost improves and completion drops, the did-less detector should have
  fired; if it did not, the detector is wrong and gets fixed before the number
  is quoted.
- If completion improves at flat cost, that is the result worth having, and it
  needs n before it is claimed. Five oracle-backed tasks per rep is small.

## 6. Out of scope for this run, stated so nothing is quietly folded in

- The published **-31.6%** output-token agent-loop bench on `django/django`. It
  is a different experiment with a different unit, it is public, and moving it
  needs a full six-arm redraw rather than a patch.
- The **240/280** graph precision table.
- The sealed contract corpus and specialist graduation.

## 7. Analysis plan, fixed now

- Per task per arm: cost, oracle, tool calls, MCP calls, turns, and the two
  flags.
- Report n and spread on every aggregate. **n=1 prices are draws, not rates** —
  a budget built from one smoke price came in 35% low.
- No metric is averaged across task kinds. Retrieval, architecture, edit and
  mechanical tasks fail for different reasons and a mean hides all of them.
- Predictions P1 to P6 are graded in the result, **including the missed ones**,
  with the reason each was wrong.
