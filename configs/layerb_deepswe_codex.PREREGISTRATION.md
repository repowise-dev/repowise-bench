# PRE-REGISTRATION: DeepSWE as a fourth Layer B harness (Codex / gpt-5.6-sol)

**Written 2026-08-09, before a single agent cell was run.** The oracle gate and
all instrument checks below are $0 and were run first; everything they found is
recorded in section F whether or not it flattered the design.

**This is a SMOKE, n = 2 tasks x 2 arms = 4 cells. It is not a result and no row
from it may reach `docs/BENCHMARKS.md`.** What it buys is a cost model and a
liveness proof, which is what a pre-registration for the full corpus gets
written from.

---

## A. WHY DeepSWE, AND WHY IT IS NOT A ContextBench SUBSTITUTE

DeepSWE (Datacurve, arXiv 2607.07946, Apache-2.0, `datacurve-ai/deep-swe`) is
113 original long-horizon tasks over 91 active OSS repos in 5 languages, with
**hand-written behavioral verifiers** rather than mined PR tests. Reference
solutions were written from scratch and never contributed upstream, so they are
not in pretraining data. Independent LLM review disagrees with its verifiers
1.4% of the time against SWE-Bench Pro's 32.4%.

**It is a Layer B instrument only.** Layer A grades retrieval against gold files
deterministically at $0; DeepSWE grades *implementation* pass/fail. It cannot
replace the ContextBench Layer A row and is not being asked to.

**What it is for: it retires the LLM judge.** Every quality column this
workstream has produced is unclaimable, and the 2026-08-09 methods correction
made that worse, not better: the paired-delta sd is 2.23, so n for a 0.50 effect
is ~156, not 15. DeepSWE replaces a judge score with a **binary verifier
outcome** over a corpus of 113. That is the first Layer B outcome measure here
that is not a model's opinion.

**Instrument choice is Codex / gpt-5.6-sol, and the reason is finding E13**, not
convenience. Under Claude Code the tools are mostly never called (repowise 7/15
on this workstream's django draw), so a quality A/B there largely compares two
bare agents. Codex exercises the treatment: 15/15 on django, 2/2 on the mui
gate, and it is the harness where even code-review-graph gets called. **Claude
Code is owed as a second harness and its result is published either way**, per
the standing rule that replacing the harness that embarrasses us with the one
that flatters us is the exact failure this workstream exists to criticise.

---

## B. CENSUS, COMPUTED BEFORE ANY TASK WAS CHOSEN

Counted on the live clone of all 113 tasks. **The cocoindex lesson binds here:
count the thing the metric divides by.**

| | value |
|---|---:|
| tasks | 113 |
| mean gold files per task (from `solution/solution.patch`) | **7.41** |
| reference solution size, added lines | **441 to 1,655** |
| languages | typescript 35, python 34, go 34, rust 5, javascript 5 |

**The finding that decided task selection: DeepSWE is localization-hostile, and
that is favourable to a context tool.** Counting how many of a task's gold files
its own `instruction.md` names verbatim:

| | tasks | share |
|---|---:|---:|
| prompt names **zero** gold files | **102** | **90.3%** |
| prompt names **all** gold files | 0 | 0% |
| mean fraction of gold files named | 0.039 | |

So on 9 tasks in 10 the agent must find 7-ish files it was never told about.
That is the condition under which a retrieval tool can matter at all.

**The 11 giveaway tasks are a named confound, not noise.**
`returns-validated-error-accumulation` was this run's original "hard" pick on
patch size (1,031 added lines, 2nd largest Python) and was **rejected on this
census**: its prompt says "you must also update: `returns/methods/cond.py`,
`returns/contrib/hypothesis/containers.py`, `returns/pointfree/__init__.py`" and
"Study `returns/interfaces/specific/result.py`". The prompt does repowise's job.
**Any future full-corpus run must report the giveaway split, because pooling the
11 with the 102 dilutes the treatment with tasks where it has nothing to add.**

---

## C. THE TWO TASKS, AND HOW THEY WERE CHOSEN

**No per-task frontier verdicts are public.** Not in the repo, not on the run
page; `deepswe.dev` does not resolve. So "a task the frontier models failed" has
no free source and **difficulty here is a PROXY, declared as one.** Nothing in
the RESULT may be phrased as "a task GPT-5.5 failed".

Proxy = reference-patch size, which is the benchmark's own stated difficulty
axis, filtered to zero-giveaway Python (repowise's strongest language, fewest
confounds for a smoke).

| role | task | added lines | gold files | giveaway | repo @ base commit |
|---|---|---:|---:|---|---|
| easy | `igel-persist-feature-schema` | 485 | 5 | none | `nidhaloff/igel` @ `bf4544d6` |
| hard | `bandit-interprocedural-taint-checks` | 851 | 9 | none | `PyCQA/bandit` @ `b46fa3a2` |

`igel` is the smallest Python patch in the corpus; `bandit` is 4th largest and
carries 9 unnamed gold files, so it is hard on implementation and on
localization at once.

**Excluded up front, on a third-party audit rather than our own rediscovery:**
`langchain-request-coalescing`, `narwhals-rolling-window-suite`,
`prometheus-transactional-reload-status`, `skrub-duration-encoding` fail their
own verifiers (`kimjune01/deepswe-run`, 4 of 113). Neither chosen task is among
them; our own oracle gate re-tests the claim on the two we use.

---

## D. ARM DESIGN, AND EVERY DIFFERENCE FROM THE CONTROL

`c0-bare` is the **upstream task directory, unmodified**. The repowise arm is a
copy whose `task.toml` differs by exactly two things, verified by diff:

1. `docker_image` points at a child image built `FROM` the task's own image
2. one `[[environment.mcp_servers]]` stdio entry

Instruction, tests, verifier, collect hook, timeouts, cpus and memory are
byte-identical. **A difference in outcome can therefore only be the tool**,
which is the attribution standard Layer A got for free and Layer B has never had.

The child image adds only:

- `uv tool install repowise==0.39.0` into its own venv, so none of the task's
  own dependencies are perturbed
- a **prebuilt index at `/app/.repowise`**, built outside the container on a
  clone pinned to the same `base_commit_hash` the image carries
- `.repowise/` appended to `/app/.git/info/exclude`

### Disclosures, each with the cost it carries

| # | decision | why | what it costs |
|---|---|---|---|
| D1 | index built **outside** the container and baked in | an in-container build has no key at build time and D13 would silently write 8-dim mock vectors while every health field read clean | index build time is not charged to the arm's wall clock. Any latency column must say so |
| D2 | synthesis provider repointed to **OpenAI** | Gemini is not in Codex's network allowlist; OpenAI is (see E2). Keeps the arm inside the sandbox's existing policy | `get_answer` synthesis is no longer the same model as prior Layer B runs, so its quality column is **not comparable cross-run** |
| D3 | index built **with prose** | Layer B convention; Layer A is `--no-prose` | real LLM spend at build (igel $0.01) |
| D4 | `--no-claude-md --no-agents --no-codex` | repowise ships AGENTS.md generation, which Codex reads. Including it would confound *tool use* with *static injected context* | the arm is **MCP-only** and is therefore a LOWER bound on what the product does. "repowise + AGENTS.md" is a named future arm, not a silent one |
| D5 | `.repowise/` git-excluded | the instruction says "commit everything"; the verifier grades `git diff base..HEAD`. Without this, `git add -A` sweeps megabytes of index into the graded patch | none. Asserted at image build: the tree must be clean or the build fails |
| D6 | difficulty is a **proxy** | no public per-task frontier verdicts | no claim of the form "a task frontier models failed" may be made |

---

## E. INSTRUMENT CHECKS, ALL RUN BEFORE ANY SPEND

Standing rule: before recording any zero, prove the arm was alive and the
detector works.

| # | check | result |
|---|---|---|
| E1 | **D13**: index vector dimensionality | **1536 on both**, live, not the 8-dim mock |
| E2 | query-time egress from inside the sandbox | **PASS.** Codex's default network allowlist is `api.openai.com`, the exact domain the OpenAI embedder needs. No policy widening |
| E3 | index portability across absolute path | **PASS.** Only absolute host path in `.repowise` is `mcp.json`, which is unused (repo path passed to `repowise mcp` explicitly) |
| E4 | in-container index identity | **PASS.** `repowise status /app` reports the exact pinned base commit on both tasks, 59 / 216 pages |
| E5 | wrong-repo hazard (A9's shape) | **PASS.** Semantic search in-container returns `igel/preprocessing.py` and `igel/igel.py`, both gold files of that task |
| E6 | vector leg live in-container | **PASS.** `--mode semantic` returns graded scores 0.374 to 0.265. The CLI's `fulltext` default had first shown flat 0.000 scores, which is a CLI default and not a failure |
| E7 | MCP stdio handshake in-container | **PASS.** initialize OK, **11 tools** (correct single-repo surface), `search_codebase` `isError=False` |
| E8 | oracle gate | see section F |
| E9 | **CRLF corruption of the benchmark's own scripts on a Windows host** | **FOUND AND FIXED. See below.** |

### E9, and it would have scored every arm a clean zero at once

`core.autocrlf` is set to `true` at system level on this box, so the first clone
of `deep-swe` rewrote **all 226 shell scripts in the corpus** to CRLF. A verifier
entry point beginning `#!/bin/bash\r` fails with

```
bash: line 1: /tests/test.sh: cannot execute: required file not found
```

which names a file that plainly exists, and pier surfaces it only as
`RewardFileNotFoundError`. **Both oracle cells failed this way in 36 and 34
seconds.** Had this been an agent run rather than the oracle, every arm would
have scored 0, the arms would have been indistinguishable, and the natural
reading is "DeepSWE is so hard nothing passes". It is the same class as the
Windows MAX_PATH failure that scored a clean zero for all five arms in the mui
Layer A run.

Fixed by re-cloning with `-c core.autocrlf=false -c core.eol=lf`.

**The detector for it was ALSO wrong, in the opposite direction, and that is the
part worth keeping.** A first pass using `od -c | grep '\\r'` reported **226 of
226 files still corrupt** on the freshly repaired clone, while `od` on a single
file plainly showed a clean `#!/bin/bash\n`. Rebuilt with a positive control on
a known-CRLF and a known-LF file, the detector returns **0 of 226**. This
workstream's standing rule is "before recording any zero, prove the detector
works"; this is the same failure with a plausible **non**-zero, and it argues the
rule should read "before recording any number".

**Standing consequence for any Windows host in this workstream: clone every
benchmark corpus with `core.autocrlf=false`, and assert on the shebang bytes of
one verifier script before the first cell.**

**E7 also caught a hazard and it is pre-registered as a known risk, not a
surprise.** The server logs `ready (vector stores loading in background)` and
the probe's first `search_codebase` came back `"sources": ["fts"]`, keyword-only
with no semantic agreement. **This is finding A8's shape at the MCP layer.** If
an agent's first call races the vector-store load it gets a degraded answer, and
that would read as a bad arm. The RESULT must report, per cell, whether the
first repowise call had semantic agreement.

---

## F. PREDICTIONS. Committed before any agent cell ran.

| # | prediction | how it is falsified |
|---|---|---|
| **P1** | The **oracle passes** the verifier on both tasks | either task returns reward 0 under `--agent oracle`. If so the task is defective, is swapped, and the audit's 109/113 is amended by our own count |
| **P2** | `c0-bare` **fails at least one** of the two tasks | both pass. Leaderboard tops out at 70% and both tasks are large-patch, so two clean passes would suggest our harness is easier than the published one |
| **P3** | The repowise arm is **exercised on both cells** (a `get_answer` or `search_codebase` call appears in the trajectory) | zero repowise tool calls on either cell. Codex was 15/15 on django and 2/2 on the mui gate, so a 0 here is a harness defect to chase, not a finding about the tool |
| **P4** | Direction only: repowise **does not increase** output tokens against bare | tokens up on both cells. Prior runs: -31.6% django, -20.4% mui. **At n=2 this is a direction check and cannot be a result** |
| **P5** | **Quality is NOT predicted and will NOT be reported as an effect.** n=2 | any RESULT sentence that reads as a quality claim |

**Stopping rule.** If P1 fails on both tasks, the run stops and the harness is
the finding. If P3 fails, no quality or efficiency column is reported at all,
because an unexercised arm is a bare agent with extra steps (finding D1).

**Publication commitment, agreed in advance:** this smoke's outcome is written
to `50-results/deepswe-smoke/RESULT.md` whichever way it lands, including a
result where repowise makes things worse.

---

## G. PROVENANCE

| | |
|---|---|
| benchmark | `datacurve-ai/deep-swe`, dataset `deep-swe-1-1`, 113 tasks, Apache-2.0 |
| runner | `pier` (Harbor fork), installed from `git+https://github.com/datacurve-ai/pier` |
| agent | `codex` CLI 0.145.0, model **gpt-5.6-sol** |
| repowise | **0.39.0** from PyPI, matching the local working tree's version |
| index provider | openai, `gpt-5.4-nano`; embedder openai, 1536 dim |
| index flags | `--prose --provider openai --embedder openai --max-file-pages 0 --no-workspace --no-editor-setup --no-claude-md --no-agents --no-codex --no-distill-hook --yes`, plus `REPOWISE_SKIP_EDITOR_SETUP=1` and `DO_NOT_TRACK=1` |
| host | Windows 11, Docker Desktop 28.4.0, WSL2 linux containers, Compose v2.39.4 |
| task images | `public.ecr.aws/d3j8x8q7/swe-bench-202605`, per-task tags, Debian 12 / Python 3.12.12 |
| arm images | `deepswe-arm/igel:repowise`, `deepswe-arm/bandit:repowise` |
| trees | `C:\Users\ragha\Desktop\bakeoff\deepswe\` |
| budget | ceiling $350, ~$58 spent before this run |
| date | 2026-08-09 |
