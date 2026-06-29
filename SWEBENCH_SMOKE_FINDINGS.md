# SWE-bench smoke — findings (gates stage)

Date: 2026-06-29. Scope: build the scorer and pass the **gold-patch + empty-diff
gates** natively (no Docker) before spending any agent budget, per
`SWEBENCH_VALIDATION_NEXT_SESSION.md`. No paid agent runs yet — this is the
plumbing/scorer gate only.

## What was built

- `harness/swe_bench_runner.py` — sibling of `swe_qa_runner.py`. Reuses its
  machinery (`_UTF8_ENV`, worktree/leak helpers) and adds:
  - `load_swe_bench_tasks()` — reads the vendored `data/swe_bench/tasks.json`
    (full SWE-bench Verified, 500 instances; decodes F2P/P2P JSON strings).
  - `make_instance_worktree()` / `reset_worktree()` — isolated git worktree per
    instance at `base_commit`, hard-reset between conditions.
  - `ensure_instance_venv()` — per-instance venv built from **Python 3.11**
    (conda `slate`), package installed editable, era-pinned test deps.
  - `score_resolved()` — apply solution + test patch, run named tests,
    `resolved == all F2P pass AND all P2P still pass`.
  - `run_gold_gate()` + CLI: `python -m harness.swe_bench_runner gold-gate`.

## Gates: PASS where the platform allows

| Instance | Repo | Gold | Empty | Gate |
|---|---|---|---|---|
| `pallets__flask-5014` | flask | resolved (1/1 F2P, 59/59 P2P) | not-resolved | **PASS** |
| `psf__requests-5414`  | requests | resolved (1/1 F2P, 130/130 P2P) | not-resolved | **PASS** |
| `psf__requests-6028`  | requests | 2/2 F2P, **182/185** P2P | not-resolved | blocked* |
| `psf__requests-2931`  | requests | collection import error | — | dead** |
| `psf__requests-1142/1724/1766/1921/2317` | requests | editable install fails | — | dead** |

\* 6028's 3 remaining failures are all `TestExtractZippedPaths` — zip-path tests
that assume `/` separators and genuinely fail on Windows. Not a scorer bug;
would pass on Linux.
\** These requests instances vendor a pre-2017 `urllib3` that does
`from collections import Mapping` (removed in Python 3.10), so `import requests`
fails at collection on any Python ≥3.10. They need Python ≤3.9.

The empty-diff gate correctly returns not-resolved on every instance, so the
scorer cannot trivially pass.

## Scorer bugs found and fixed during the gate (all in `_run_pytest`)

1. **Deps too new.** Modern Werkzeug 3.x dropped `werkzeug.__version__` that
   Flask 2.3's `flask.testing` reads. Pin `Werkzeug>=2.3,<3.0` and install pins
   *after* the editable package so era pins win over pyproject ranges.
2. **Python too new.** pytest 8 removed `_pytest.monkeypatch.notset` (Flask
   conftest needs it); pytest 7 trips Python 3.13's `ast.Str` removal. Build
   venvs from **Python 3.11**, pin `pytest>=7,<8`.
3. **addopts collision.** requests sets `addopts = --doctest-modules`, which adds
   a second `<DoctestModule>` collector and breaks node-id resolution. Run with
   `-o addopts=`.
4. **argv mangling + abort-on-not-found.** SWE-bench parametrized node ids
   contain spaces and embedded quotes (`test_parse_dict_header[foo="is a
   fish"]`). Passing them as argv on Windows mangles them, and a single
   unresolved id makes pytest exit 4 and run nothing. Fix: **run pytest on the
   test files** and match results by parsing `-rA` output, never pass node ids
   as argv.
5. **Truncated ids in the vendored data.** The vendored `tasks.json` truncates
   parametrized ids at the first whitespace (`...[foo="is`). Matcher now
   prefix-matches truncated ids (unbalanced `[`) and passes only if every
   prefix-matched test passed (conservative; exact on gold).

## The blocker for the actual experiment

Native-on-Windows yields only **2 clean instances** (flask-5014, requests-5414),
and both are `<15 min fix`, single-file, well-localized bugs — the **worst case**
for repowise, whose edge is *cross-file* localization (the plan says this
explicitly). The Verified subset only has 1 flask + 8 requests instances total,
and 6 of the requests ones are dead on Python ≥3.10. So:

- There is no room to **failure-mine hard localization tasks** natively on
  Windows. C0 (bare Claude) will almost certainly resolve both easy instances,
  leaving zero C0-failures to pair C1 against — a near-certain null result that
  burns agent budget to learn nothing about the mechanism.
- The scorer itself is **proven faithful** and is reusable as-is for a Dockerized
  / WSL run on the full Verified set (django/sympy/sphinx/etc.) or SWE-bench Pro,
  where hard multi-file tasks actually live.

## Pivot to WSL/Docker — DONE, foundation proven (2026-06-29)

Decision taken: move off Windows-native to the official Dockerized harness.
Status of the new foundation:

- **Official harness runs in WSL.** `swebench==4.1.0` installed in a WSL
  Ubuntu-22.04 venv (`~/swebench_venv`). Docker Desktop is reachable from WSL
  (no settings change needed once the daemon is up). The Windows host CANNOT run
  the harness (`import resource` is Unix-only) — so the harness lives in WSL; the
  agent stage stays on Windows.
- **Gold eval PROVEN end-to-end.** `run_evaluation --predictions_path gold
  --instance_ids pallets__flask-5014` pulled the prebuilt image from Docker Hub
  (`swebench/sweb.eval.x86_64.pallets_1776_flask-5014`, 3.99GB — no local build)
  and reported `resolved_instances: 1`. Matches our native result. Prebuilt
  images exist for the Verified set, so django/sympy/sphinx will pull, not build.
- **Failure mining works.** `scripts/mine_swebench_failures.py` over a sparse
  clone of `swe-bench/experiments` (134 systems) → `results_failure_ranking.json`.
  23 django/sympy/sphinx instances were failed by ALL 134 systems; many are the
  "1-4 hours" multi-file tasks where cross-file localization is the bottleneck
  (django-10554/13212/15629/16631, sympy-13852/16597, sphinx-11510, ...). These
  are the candidate pool — a genuine hard tail, not a saturated random sample.

The Windows-native scorer (`swe_bench_runner.py`) stays as a fast local
cross-check but is not on the critical path anymore.

## Remaining build (the paid / heavy part — gated on a go decision)

1. **Agent → predictions pipeline** (Windows): for each shortlisted instance ×
   condition {C0_bare, C1_index_only}, checkout a worktree at base_commit, (C1
   only) `index_repo(mode="index-only")` + `generate_mcp_config(profile="core")`
   + CLAUDE.md, run `run_claude_code(benchmark="swe_bench")`, capture `git diff`
   as `model_patch`. Emit one SWE-bench predictions JSONL per condition
   (`{instance_id, model_name_or_path, model_patch}`).
2. **Eval** (WSL): `run_evaluation --predictions_path <cond>.jsonl` per condition
   → resolved per instance. Pair C0 vs C1 on the SAME instances.
3. **Stage B/C**: keep only instances OUR C0 fails; triage each as localization
   vs reasoning; run C1 only on the localization failures; write SMOKE_FINDINGS.

Cost note: this is where agent budget and repowise indexing of large repos
(django/sympy) get spent, so it is gated behind an explicit go decision on
run shape (which repos, how many tasks, budget) rather than launched
automatically.

## Full pipeline PROVEN end-to-end (2026-06-29)

`harness/swe_bench_agent.py` built and validated. A bare-Claude (C0) run on
`pallets__flask-5014` produced a correct 433-char patch (5 turns, $0.16, 26s),
and the official WSL Docker harness scored that agent patch
`resolved_instances: 1` (18s). The complete chain works:

    Claude Code (Windows, base_commit worktree) -> git diff -> predictions.jsonl
      -> WSL `run_evaluation` (Docker) -> resolved verdict

Key reliability fixes landed while proving this:
- **Streaming visibility.** `run_claude_code(stream_log_path=...)` tees the
  stream-json to a file live (Popen + manual timeout), so a timeout no longer
  discards the trail — `swe_bench_agent` saves per-run `.stream.jsonl`.
- **Budget reality.** The `claude -p` startup pays a ~33k-token cache tax from
  the user's global plugins/hooks/skills (~$0.10-0.20/run, auth is subscription
  so it can't be trivially blanked). Hard "1-4 hour" django tasks need a real
  budget: $1.5 ran out during exploration, and 1200s timed out with no edit on
  `django-10554`. flask (easy) finished in 26s/$0.16. So the django mechanism
  runs need a larger per-task budget/timeout than the flask smoke.

Status: apparatus complete and validated. Remaining = run the paired C0/C1
matrix on the failure-mined django tasks (tuning timeout/budget per task
hardness) and write the per-task localization analysis.

## First paired django run + a bug it surfaced (2026-06-29)

Ran C0 and C1 on `django__django-10554` (a 1-4hr, 2-file query/compiler bug,
failed by all 134 leaderboard systems). Outcome and lessons:

- **MCP `--profile` bug (fixed).** The first C1 made zero repowise calls: the
  repowise CLI on `main` has no `repowise mcp --profile` option, so launching the
  server with `--profile core` crashed it on startup and the agent silently fell
  back to bare Read/Grep. Fixed by not passing a profile for the index-only arm
  (the tool surface is already restricted client-side via TOOLS_INDEX_ONLY).
  After the fix, ToolSearch resolves `mcp__repowise__get_context`/`get_risk` and
  the agent loads + calls them. Lesson: always assert the C1 stream contains
  `mcp__repowise__*` calls before trusting a C1 result.

- **Task too hard to yield signal.** Both arms TIMED OUT at 30 min with no patch:
  C0 made ~56 tool calls, valid C1 made ~50 (incl. 2 repowise calls) — neither
  ever committed an edit. django-10554 is simply beyond sonnet in 30 min/$5
  whether or not repowise is present. A both-arms-DNF instance gives no
  mechanism comparison: you need a task where each arm at least *produces* a
  patch, so you can compare which files they localized to.

- **Recommendation for the real run:** select hard-tail django instances rated
  `15 min - 1 hour` (still failed by most systems, but completable), prefer
  multi-file gold patches (e.g. django-12406, django-16631) so the cross-file
  localization mechanism is actually exercised, and keep the gold patch's file
  set as the localization yardstick. Reserve `1-4 hours` instances for a
  longer-budget run, not the smoke.

- **Minor harness TODO:** the timeout return path doesn't tally
  `repowise_tools_called` into the meta record (the stream has them); parse from
  `_raw_stream_lines` on timeout so timed-out runs still report repowise usage.

What IS proven: the end-to-end apparatus (agent -> diff -> Docker scorer ->
resolved) works, the C1 repowise wiring works after the profile fix, and the
failure-mining surfaces genuinely hard tasks. The mechanism question is
unanswered only because the first chosen instance was too hard to complete.
