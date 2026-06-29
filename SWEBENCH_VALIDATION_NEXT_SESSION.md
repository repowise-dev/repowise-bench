# Next session — validate "repowise helps Claude Code resolve SWE tasks" (SMOKE FIRST)

Paste the fenced block into a fresh session. Everything below it is grounding the
next session should read before touching code.

```
Validate the hypothesis: Claude Code resolves SWE-bench tasks BETTER with the
repowise MCP server (index-only) than without it. Do NOT do a full run yet —
run 1-2 paired tasks first and decide if it's worth scaling.

Read first, in order:
  1. repowise-bench/harness/swe_qa_runner.py  — the existing runner. We are
     copying its machinery, not rewriting it: index_repo(mode="index-only"),
     generate_mcp_config(profile="core"), get_c0_worktree, the claude -p
     invocation (~line 940), leak scrubbing, budget/resume.
  2. repowise-bench/harness/run_experiment.py — the driver (config-driven,
     parallel, resumable). Note benchmarks.swe_bench is a config placeholder
     that is NOT wired yet.
  3. repowise-bench/configs/swe_qa_flask48_v2.yaml — the config schema +
     C0_bare / C2_full condition shape.
  4. This whole file.

Then do, in order (SMOKE — keep it to 1-2 tasks):
  1. Pick tasks by MINING FAILURES, not random sampling (see "Task selection"
     below). Stage A: from github.com/swe-bench/experiments, rank flask/requests
     instances by how many strong frontier systems FAILED them. Take the hard
     tail. These are the candidates; confirm with C0 in step 4.
  2. Build the SCORER and test IT before any agent runs: apply the GOLD patch
     at base_commit, apply test_patch, run FAIL_TO_PASS + PASS_TO_PASS. The
     gold patch MUST score "resolved". If it doesn't, the harness is wrong —
     stop and fix the scorer, not the agent. This is the gate.
  3. Index the repo once, index-only: the existing index_repo(mode="index-only")
     path. Free, ~20 min cap, cached under indexes/.
  4. Run the shortlist under C0_bare first; KEEP the ones C0 actually fails
     (confirmed same-scaffold failures). Then TRIAGE each C0 failure: was it a
     LOCALIZATION failure (wrong / incomplete file set) or a reasoning / spec /
     flaky-test failure? Only localization failures are fair game — repowise
     cannot fix the others. Record the split.
       C0_bare         — no repowise (git worktree, no .repowise present)
       C1_index_only   — repowise MCP, --profile core, index-only
     Reuse the claude -p invocation from swe_qa_runner (stream-json,
     --strict-mcp-config --mcp-config, --allowed-tools, --append-system-prompt).
  5. Run C1_index_only on exactly the C0 failures (paired). Report per task:
     did C1 flip it to resolved? turns, wall-clock, $ cost, and the repowise
     tool calls C1 made + whether they surfaced the right file / a silent
     co-change partner. A flip ON a localization failure WITH the mechanism
     visible is the result we want.
  6. Write a short SMOKE_FINDINGS.md: of N C0 failures, M were localization-
     flavored; C1 resolved K of M. Is the signal worth a 30-50 task run on
     SWE-bench Pro? Recommend scale / don't-scale / fix-and-retry.
```

---

## The hypothesis (be honest about what we're testing)

repowise's MCP tools give the agent **graph + git intelligence** the bare agent
lacks: which files co-change with the suspect file (often without an import
link), where the hotspots are, the symbol skeleton without reading whole files.
On a bug fix, the payoff is **localization** — finding the right file(s) and the
silent parallel edits — not raw reasoning. So the realistic win is: C1 resolves
tasks C0 misses *because C0 edited the wrong/incomplete set of files*. If we
don't see that mechanism even when C1 wins, the win is noise.

Index-only (not full docs) is deliberate: it's free and fast, and it's the cheap
tier we'd ship by default. The cost is that `search_codebase`, `get_overview`,
and `get_answer` return empty/weak without the wiki — so C1's prompt must lean on
`get_context` + `get_risk` + `get_symbol` (the tools that work index-only). The
existing `SWEBENCH_PROMPT_INDEX_ONLY` in swe_qa_runner.py already encodes this;
reuse/adapt it.

## Benchmark — smoke on Verified-hard, scale on Pro

Two stages, two benchmarks, on purpose:

- **Smoke (this session): SWE-bench Verified, native, FAILURE-MINED.** Verified
  is what the labs report (Anthropic/OpenAI/Cursor), and frontier agents hit
  ~70-78% on it — NOT 100%, but high enough that a *random* sample saturates and
  hides any C1-vs-C0 delta. Worse, most Verified bugs are single-file and well-
  localized — the worst case for us, since repowise's edge is cross-file
  localization. Both problems are solved by SELECTION (see "Task selection"):
  pick the instances frontier systems FAIL. Use flask/requests so tests run
  natively (`pip install -e .[dev]` + `pytest`) — no Docker, the smoke stays
  cheap and Windows-friendly.
- **Scale (after the smoke proves plumbing + mechanism): SWE-bench Pro.** Scale
  AI's 2025 set is the right number-to-publish: contamination-resistant
  (GPL/commercial repos), multi-file tasks (exercises the localization
  mechanism), frontier resolve rates far lower (real headroom). Cost: every
  instance is a Docker image and the repos are bigger/non-standard — so Pro is
  NOT where you debug the harness. Prove it native first, then pay the Docker
  tax once for the real run.

Why not start on Pro: a 1-2 task Pro smoke on Windows burns the session on
Docker/env setup, testing infra instead of the hypothesis. Native flask first.

Dataset: `princeton-nlp/SWE-bench_Verified` on HuggingFace. Each instance has
`instance_id, repo, base_commit, problem_statement, patch (gold), test_patch,
FAIL_TO_PASS, PASS_TO_PASS, environment_setup_commit`.

## Task selection — mine failures, don't sample randomly

The experiment can only show a signal on tasks that are (a) hard enough that C0
fails and (b) hard for a LOCALIZATION reason repowise can fix. Find them:

1. **Stage A — leaderboard hardness (offline, free).** Clone
   `github.com/swe-bench/experiments`; per submission, `evaluation/verified/<system>/
   results/results.json` lists `resolved_ids`. Aggregate across the strong
   systems → for each instance, the fraction that FAILED it. Filter to
   flask/requests and take the hard tail (failed by most/all systems). This is
   a cheap PRIOR, not the verdict — those systems use other models/scaffolds.
2. **Stage B — confirm with C0.** Run bare Claude Code on the shortlist; keep
   only the instances OUR C0 actually fails (same model+scaffold as C1).
3. **Stage C — triage the failure mode.** For each C0 failure, read the
   transcript: localization failure (wrong/incomplete file set) vs reasoning /
   underspecified-issue / flaky-test failure. ONLY localization failures test
   the hypothesis; repowise structurally can't fix the rest. Counting non-
   localization failures as a repowise null result would be meaningless.

The honest writeup is then: "of N C0 failures, M were localization-flavored;
repowise (index-only) resolved K of those M" — a paired, mechanism-grounded
number, not a random-sample win that could be noise.

Caveat: "hard for everyone" can also mean a broken/flaky instance. The gold-
patch gate (below) still applies per instance, and drop any whose gold patch
won't score resolved natively.

## What already EXISTS (reuse, do not rebuild)

All in `harness/swe_qa_runner.py` unless noted:
- `index_repo(..., mode="index-only")` — free index, cached, 20-min cap.
- `generate_mcp_config(repo, profile="core")` — limited MCP surface = the
  "selectively choose MCP" knob. Writes a per-repo `.mcp.json` pointing at
  `repowise mcp <repo> --transport stdio --profile core`.
- `get_c0_worktree(repo)` — clean no-repowise condition via a git worktree
  (untracked `.repowise/` is physically absent — no delete dance).
- The `claude -p` agent invocation (~line 940): `--output-format stream-json`,
  `--strict-mcp-config --mcp-config <path>` (C0 uses configs/_empty_mcp.json),
  `--allowed-tools`, `--disallowed-tools`, `--append-system-prompt`, budget.
- Leak scrubbing before C0 (`.repowise`, `.mcp.json`, `CLAUDE.md`).
- `run_experiment.py`: config-driven conditions, BudgetTracker, ResultWriter
  (JSONL append = crash-safe resume), parallel workers.
- `SWEBENCH_PROMPT_INDEX_ONLY` / full prompt strings (bug-fix tuned).

## What to BUILD (the gap)

1. `harness/swe_bench_runner.py` (sibling of swe_qa_runner.py):
   - `load_swe_bench_tasks(dataset, repos, include_indices, ...)` — from the
     HF dataset (or a vendored JSONL subset under `data/`).
   - `run_swe_bench_task(task, condition, config, budget, raw_saver)` — checkout
     base_commit in an isolated worktree, run the agent, capture its diff
     (`git diff`), then SCORE.
   - `score_resolved(repo, base_commit, agent_diff, test_patch, FAIL_TO_PASS,
     PASS_TO_PASS)` — apply agent_diff, apply test_patch, run the named tests,
     resolved == all FAIL_TO_PASS pass AND all PASS_TO_PASS still pass. For the
     flask smoke, run pytest natively in a per-repo venv. Leave a Docker path
     (official `swebench` package / `sb-cli`) as a TODO for scale-up.
2. Wire `swe_bench` into `run_experiment.py` (mirror `run_swe_qa_experiment`);
   the config key `benchmarks.swe_bench.enabled` already exists.
3. `configs/swe_bench_smoke.yaml` — 2 flask instances, conditions C0_bare +
   C1_index_only, `repowise_mode: index-only`, `--profile core`, tiny budget
   (max_total_usd ~3, max_per_task ~1).

## Structured + tested (the user asked for this explicitly)

- **Gold-patch gate (do this before any agent run):** the scorer must mark the
  GOLD patch "resolved" for each chosen instance. If gold doesn't pass, the
  instance's env/test setup is wrong — fix or drop the instance. No agent metric
  means anything until this passes.
- **Empty-diff gate:** an empty agent diff must score "not resolved" (catches a
  scorer that trivially passes).
- **Pairing:** run C0 and C1 on the SAME instances; never compare across
  different task subsets.
- **Isolation:** each run in its own worktree at base_commit; assert the working
  tree is clean before applying a diff (no leakage between conditions/tasks).
- Keep n tiny (1-2) until the gates are green and one full paired task works
  end-to-end. Only then talk about 30-50 tasks.

## Gotchas (carried from the existing harness + known traps)

- **Index-only blinds some tools.** `search_codebase` / `get_overview` /
  `get_answer` are empty/weak without the wiki. The C1 prompt must steer to
  `get_context` + `get_risk` + `get_symbol`, and `--disallowed-tools` should
  block the dead ones so the agent doesn't waste turns on denied calls.
- **No Docker for the smoke.** flask/requests run pytest natively; that's why
  they're the smoke repos. Don't start on django/sympy (they effectively need
  the Dockerized SWE-bench env).
- **SWE-bench tests mutate the tree.** `test_patch` adds/edits test files —
  apply it AFTER the agent diff, score, then hard-reset the worktree.
- **Windows file locks:** a long-lived MCP server holds `wiki.db`/lancedb open;
  the harness already handles this with `_safe_rmtree` + cache-skip. Reuse it;
  don't re-wipe `.repowise` under a live server.
- **Contamination caveat for the writeup:** SWE-bench Verified instances predate
  these models' training cutoffs — a resolved task may be partly recall. It
  still measures C1-vs-C0 *delta* fairly (same model both arms), which is all we
  need; state it.
- Use `.venv\Scripts\python.exe` and `.venv\Scripts\repowise.exe` (editable
  checkout), not bare `python` (stale miniconda copy).

## Success criteria for the smoke (what "worth scaling" means)

The smoke is run on C0-failures that triaged as LOCALIZATION failures (Stage C).
Green-light a 30-50 task SWE-bench Pro run if:
  (a) C1 FLIPS at least one such failure to resolved, AND the transcript shows a
      repowise tool (get_risk co-change partner / get_context / get_symbol)
      surfaced the file or silent coupling that made the difference; or
  (b) even without a flip, C1 shows materially better localization on them
      (edits the correct file set where C0 edited the wrong/incomplete one,
      fewer wrong-file edits / fewer turns) — the mechanism is working, n is
      just too small to land the flip.
Red-light / fix-and-retry if: C1 wastes turns on denied index-only tools, the
scorer is flaky (gold/empty gates not rock-solid), or C1 shows no localization
improvement on tasks that were specifically localization failures.

n is tiny by design — this is a go/no-go on PLUMBING + MECHANISM, not a
statistical result. The statistical claim comes from the Pro scale run.
```
