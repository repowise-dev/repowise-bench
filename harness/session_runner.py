"""Session-shaped runner for the session-cost eval.

The whole point of this file is the one design change the brief asks for:
**measure sessions, not questions.** Every other runner in this harness spends a
cell on one question, so the resident costs (the CLAUDE.md block, the MCP
schemas) are paid once and the hooks fire twice. A real user pays those on every
API call of a long multi-task session, and that is the shape the 3.28x complaint
came from.

Mechanism, verified before this file was written (see
`50-results/session-cost-eval/RESULT.md` section 3):

  * `claude -p --session-id <uuid>` starts the session, `claude -p --resume
    <uuid>` continues it. Context carries; the session id echoes unchanged.
  * Each invocation's usage is ITS OWN, not cumulative, so summing across the
    task list is correct. Turn 2 of the probe reported cache_read 52,169 against
    cache_creation 254.
  * Tokens come from `result.modelUsage` per invocation. NOT from the per
    assistant-message `usage` blocks the brief suggested: on this build a turn
    whose result event reported 9 output tokens had a single assistant message
    reporting 1, so summing those under-reports output. Standing rule 3 wants
    modelUsage anyway.

Oracles run OUT OF BAND between turns, so their wall clock is recorded
separately and never lands in the agent's column.

Resumable on (cell_id, arm, condition, task_id): a killed run costs only the
tasks it had not finished, and the session id is carried on every row so the
resumed process rejoins the same conversation rather than starting a new one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("pyyaml required", file=sys.stderr)
    raise

BENCH_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = BENCH_ROOT / "scripts" / "oracles"
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

# `--settings` MERGES, it does not replace, so it cannot remove a hook the
# operator's own settings define. Measured 2026-08-03 by env_isolation_probe.py:
# an unpinned cell fires 8 hooks and receives two injected context blocks, one
# of which advertises repowise's MCP tools, INTO THE BARE CONTROL. That is
# finding D16 and it is the reason a bare arm here is not bare by default.
# CLAUDE_CONFIG_DIR replaces the whole config root and does remove them.
from harness.arms import prepare_claude_home  # noqa: E402

# An unauthenticated CLI exits 0 with subtype "success" and answers "Not logged
# in", so every cell of every arm would record as complete, at $0, with a wrong
# answer. The bench credential hard link has silently become a stale copy once
# already. Never let that become a data row.
_UNAUTH_MARKERS = (
    "not logged in", "failed to authenticate", "invalid api key",
    "please run /login", "authentication_error",
)


def looks_unauthenticated(answer: str) -> bool:
    low = (answer or "").strip().lower()
    return bool(low) and any(m in low for m in _UNAUTH_MARKERS)

# Every arm gets the same tools and the same operating instructions. An arm that
# had to discover the test command for itself would differ from one that did not
# by something other than its treatment.
BASE_TOOLS = "Read,Grep,Glob,Bash,Edit,Write"

SYSTEM_PROMPT_HEAD = (
    "You are working in the code repository in your current directory, task by "
    "task, in one continuous session. "
    "Only read and modify files within the current repository. "
    "Do NOT access files outside the current directory. "
    "Do NOT read any benchmark, test-harness, or evaluation data, and do not "
    "look for a reference solution; there is none inside this repository. "
    "Do NOT use ListMcpResourcesTool or ReadMcpResourceTool. "
)

# Emitted ONLY for tasks that actually need to run tests.
#
# This used to be one command interpolated for the WHOLE session. On a small
# repo that was harmless (`pytest -q` names nothing). On a repo whose suite is
# too large to run whole, every affordable command names test FILES — and
# naming the module of a later edit task in the system prompt hands the agent
# the location of that answer before the session's first task has started.
#
# The hint is treatment-independent, so it inflates every arm equally rather
# than favouring one, but it corrupts the COMPLETION signal specifically, which
# is the signal a large repo is supposed to pay in. Scoping it per task means a
# task leaks only the module it is already being pointed at, and a task that
# changes no code leaks nothing at all.
SYSTEM_PROMPT_TEST = (
    "Run the test suite with this exact command from the repository root: "
    "{test_cmd} "
)

SYSTEM_PROMPT_TAIL = (
    "When a task says not to change code, answer it and change nothing. "
    "When a task asks for a fix, make the smallest change that fixes it and "
    "keep the whole test suite passing."
)


def system_prompt_for(task: dict, cfg: dict) -> str:
    """The system prompt for ONE task, with the test clause scoped to it.

    Resolution order: the task's own ``test_command``, else the cell-wide
    ``test_env.command_display``, else the clause is omitted entirely. A task
    that declares ``test_command: none`` gets no clause even when the cell
    defines one — that is how read-only tasks (retrieval, architecture) avoid
    carrying another task's module name.
    """
    per_task = task.get("test_command")
    if per_task is None:
        cmd = (cfg.get("test_env") or {}).get("command_display")
    elif str(per_task).strip().lower() in ("none", "false", ""):
        cmd = None
    else:
        cmd = per_task
    middle = SYSTEM_PROMPT_TEST.format(test_cmd=cmd) if cmd else ""
    return SYSTEM_PROMPT_HEAD + middle + SYSTEM_PROMPT_TAIL

# Blocked everywhere. The first two read arbitrary MCP resource URIs, which
# would bypass the mcp__* disallow through the resource namespace; the hosted
# claude_ai_* servers bleed in from the operator's own config.
DISALLOWED_BASE = "ListMcpResourcesTool,ReadMcpResourceTool,mcp__claude_ai_*"


def _utf8_env(claude_home: Path | None = None,
              pinned_bin: Path | None = None,
              task_id: str | None = None) -> dict:
    env = dict(os.environ)
    if task_id:
        # The enforcement hook caps its nudges per unit of work. Without this the
        # unit is the whole 11-task session, and the enforced condition quietly
        # degrades to 2 injections across 154 firings. See
        # `force_tool_use.py::_nudges_used`.
        env["BENCH_TASK_ID"] = task_id
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["DO_NOT_TRACK"] = "1"
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    if claude_home:
        env["CLAUDE_CONFIG_DIR"] = str(claude_home)
    if pinned_bin and pinned_bin.is_dir():
        # Pin the binary for the whole cell, not just for the MCP launch.
        # A declared hook's shipped command is `command -v repowise-augment`,
        # which takes whatever PATH offers; measured 2026-08-08, PATH offered an
        # editable install of a checkout with uncommitted changes, and that
        # would have been published as the pinned binary.
        env["PATH"] = str(pinned_bin) + os.pathsep + env.get("PATH", "")
    return env


def git_status(tree: Path) -> str:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=str(tree),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout or ""


def parse_stream(lines: list) -> dict:
    """Read one invocation's stream-json.

    Tokens come from the result event's `modelUsage`. Tool calls, MCP calls and
    hook events are counted from the assistant/user events, because the result
    event does not carry them.
    """
    out = {
        "answer": "", "num_turns": 0, "cost_usd": 0.0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "token_source": "", "models_used": [],
        "tool_calls": 0, "tool_names": {},
        "mcp_calls": 0, "mcp_tools": {}, "mcp_isError": 0,
        # The CLI arm's adoption column. Without it a CLI arm reports
        # `mcp_calls: 0` and nothing else, so "did the agent use the tool" is
        # unanswerable for half of step 2's arms and prediction S4 has no
        # measurement at all. Counted from the same tool_use blocks as
        # `mcp_calls`, so the two columns are the same kind of number.
        "cli_calls": 0, "cli_cmds": {},
        "hook_events": 0, "hook_injections": 0, "hook_names": {},
        "stop_reason": "", "session_id": "", "is_error": False,
        "cache_ttl_1h": 0, "cache_ttl_5m": 0,
    }
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        etype = ev.get("type")

        if etype == "assistant":
            for block in ((ev.get("message") or {}).get("content") or []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or "?"
                    out["tool_calls"] += 1
                    out["tool_names"][name] = out["tool_names"].get(name, 0) + 1
                    if name.startswith("mcp__"):
                        out["mcp_calls"] += 1
                        out["mcp_tools"][name] = out["mcp_tools"].get(name, 0) + 1
                    elif name == "Bash":
                        sub = _repowise_subcommand(
                            (block.get("input") or {}).get("command"))
                        if sub:
                            out["cli_calls"] += 1
                            out["cli_cmds"][sub] = out["cli_cmds"].get(sub, 0) + 1

        elif etype == "user":
            for block in ((ev.get("message") or {}).get("content") or []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if block.get("is_error"):
                        out["mcp_isError"] += 1

        elif etype == "system" and str(ev.get("subtype", "")).startswith("hook_"):
            # Hook events arrive as `type: "system"` with `subtype:
            # "hook_started"` / `"hook_response"`. The first version of this
            # counter tested `"hook" in etype`, which is never true for
            # "system", so it read ZERO on every row of every arm including one
            # whose hooks demonstrably fired 154 times. A dead counter reporting
            # a clean zero is this workstream's house speciality, and here it
            # was one edit away from being published as "the control is
            # provably bare".
            if ev.get("subtype") == "hook_started":
                out["hook_events"] += 1
                name = ev.get("hook_name") or "?"
                out["hook_names"][name] = out["hook_names"].get(name, 0) + 1
            elif ev.get("subtype") == "hook_response":
                # A hook that fires and emits nothing has cost wall clock and
                # changed nothing. Counting firings alone hides that, and the
                # gap between the two is the whole story of the enforced arm.
                if (ev.get("output") or "").strip() or (ev.get("stdout") or "").strip():
                    out["hook_injections"] += 1

        elif etype == "result":
            out["answer"] = ev.get("result") or ""
            out["num_turns"] = int(ev.get("num_turns") or 0)
            out["cost_usd"] = float(ev.get("total_cost_usd") or 0.0)
            out["session_id"] = ev.get("session_id") or ""
            out["stop_reason"] = ev.get("stop_reason") or ev.get("subtype") or ""
            out["is_error"] = bool(ev.get("is_error"))

            mu = ev.get("modelUsage") or {}
            if mu:
                out["token_source"] = "modelUsage"
                out["models_used"] = sorted(mu)
                for per in mu.values():
                    if not isinstance(per, dict):
                        continue
                    out["input_tokens"] += int(per.get("inputTokens") or 0)
                    out["output_tokens"] += int(per.get("outputTokens") or 0)
                    out["cache_read_tokens"] += int(per.get("cacheReadInputTokens") or 0)
                    out["cache_creation_tokens"] += int(per.get("cacheCreationInputTokens") or 0)
            else:
                # Recorded, never silently substituted: a row whose tokens came
                # from the top-level usage under-reports when a subagent runs on
                # another model, and standing rule 3 exists because that was
                # published once already.
                u = ev.get("usage") or {}
                out["token_source"] = "usage(fallback)"
                out["input_tokens"] = int(u.get("input_tokens") or 0)
                out["output_tokens"] = int(u.get("output_tokens") or 0)
                out["cache_read_tokens"] = int(u.get("cache_read_input_tokens") or 0)
                out["cache_creation_tokens"] = int(u.get("cache_creation_input_tokens") or 0)

            cc = (ev.get("usage") or {}).get("cache_creation") or {}
            out["cache_ttl_1h"] = int(cc.get("ephemeral_1h_input_tokens") or 0)
            out["cache_ttl_5m"] = int(cc.get("ephemeral_5m_input_tokens") or 0)

    return out


# The subcommands that count as using the repowise CLI surface. Same six the
# step-2 arms advertise, and the same list `force_tool_use.py --surface cli`
# nudges toward, so the counter and the enforcement cannot disagree about what
# adoption is.
_CLI_SUBCOMMANDS = ("ask", "context", "symbol", "why", "search", "risk")


def _repowise_subcommand(command: str | None) -> str | None:
    """Which of the six this Bash command invoked, if any.

    Tight on purpose. `grep -rn "repowise" .` and `repowise --version` are NOT
    adoption, and a counter that merely looked for the word would score both as
    a tool call. Proved in both directions before use — see
    `scratchpad/prove_cli_detector.py`, which holds the enforcement hook to the
    same cases.
    """
    if not command:
        return None
    for raw in re.split(r"&&|\|\||[;|\n]", command):
        parts = raw.strip().split()
        # Skip a leading env assignment or interpreter, e.g. `cd x && repowise ...`
        while parts and ("=" in parts[0] and not parts[0].startswith("-")):
            parts = parts[1:]
        if len(parts) >= 2 and Path(parts[0]).stem.lower() == "repowise":
            if parts[1] in _CLI_SUBCOMMANDS:
                return parts[1]
    return None


def build_cmd(task: dict, session_id: str, first: bool, arm: dict,
              cfg: dict, mcp_config: str | None, settings: str | None,
              model: str, max_turns: int, max_budget: float) -> list:
    uses_mcp = bool(arm.get("uses_mcp"))
    disallowed = DISALLOWED_BASE
    allowed = BASE_TOOLS
    if uses_mcp:
        allowed += ",ToolSearch"
        for t in arm.get("client_tools") or []:
            allowed += "," + t
    else:
        disallowed += ",ToolSearch,mcp__*"

    cmd = [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        "--include-hook-events",
        "--model", model,
        "--max-budget-usd", str(max_budget),
        "--max-turns", str(max_turns),
        "--disallowed-tools", disallowed,
        "--allowed-tools", allowed,
    ]
    cmd += ["--session-id", session_id] if first else ["--resume", session_id]

    if settings:
        cmd += ["--settings", settings]

    if uses_mcp and mcp_config:
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
    else:
        empty = BENCH_ROOT / "configs" / "_empty_mcp.json"
        if not empty.exists():
            empty.parent.mkdir(parents=True, exist_ok=True)
            empty.write_text('{"mcpServers": {}}', encoding="utf-8")
        cmd += ["--strict-mcp-config", "--mcp-config", str(empty)]

    parts = [system_prompt_for(task, cfg)]
    if arm.get("coaching"):
        parts.append(arm["coaching"])
    cmd += ["--append-system-prompt", "\n\n".join(parts)]

    cmd.append(task["prompt"])
    return cmd


def run_oracle(task: dict, tree: Path, baseline_status: Path | None) -> dict:
    oracle = task.get("oracle")
    if not oracle or oracle == "none" or not isinstance(oracle, dict):
        return {"oracle": None}
    probe = oracle.get("probe")
    if probe:
        script = BENCH_ROOT / probe
    else:
        # kind-only oracles map onto the same per-task script by id
        script = ORACLE_DIR / f"{task['id'].lower()}_*.py"
        matches = sorted(ORACLE_DIR.glob(f"{task['id'].lower()}_*.py"))
        if not matches:
            return {"oracle": "missing", "passed": None}
        script = matches[0]

    if not script.exists():
        matches = sorted(ORACLE_DIR.glob(f"{task['id'].lower()}_*.py"))
        if not matches:
            return {"oracle": "missing", "passed": None}
        script = matches[0]

    env = _utf8_env()
    env["PYTHONPATH"] = str(ORACLE_DIR)
    cmd = [sys.executable, str(script), "--tree", str(tree)]
    if baseline_status:
        cmd += ["--baseline-status", str(baseline_status)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=1800)
    return {
        "oracle": script.name,
        "passed": r.returncode == 0,
        "detail": (r.stdout or "").strip().splitlines()[-1] if r.stdout else (r.stderr or "")[-300:],
        "wall_seconds": round(time.time() - t0, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="cell task-set yaml")
    ap.add_argument("--arm", required=True)
    ap.add_argument("--condition", default="enforced",
                    choices=["enforced", "unenforced"])
    ap.add_argument("--tree", required=True, help="this arm's worktree")
    ap.add_argument("--out", required=True, help="jsonl output path")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--max-budget-usd", type=float, default=3.0)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--mcp-config")
    ap.add_argument("--settings")
    ap.add_argument("--uses-mcp", action="store_true")
    ap.add_argument("--client-tools", default="")
    ap.add_argument("--coaching", default="")
    # Coaching that spans lines or contains quotes cannot survive a shell
    # argument list. PowerShell split a multi-line CLI-arm coaching on its
    # embedded double quotes and argparse rejected the remainder as unknown
    # arguments; had it split somewhere less lucky, the arm would have run with
    # SILENTLY TRUNCATED coaching and been reported as a low-adoption result.
    # Read it from a file instead, so the bytes the agent receives are the bytes
    # on disk.
    ap.add_argument("--coaching-file", default=None)
    ap.add_argument("--limit-tasks", type=int, default=0,
                    help="stop after N tasks (costing runs only)")
    # A cell whose tasks are split across several SESSIONS needs to run one
    # session's slice per invocation, and `--limit-tasks` can only take a
    # prefix. Ids are explicit rather than a range so the selection is visible
    # in the command line that gets recorded with the run.
    ap.add_argument("--task-ids", default="",
                    help="comma-separated task ids to run, in config order "
                         "(default: all). Unknown ids are a hard error rather "
                         "than a silent empty run.")
    ap.add_argument("--pinned-bin",
                    default=r"C:\Users\ragha\Desktop\repowise-sessioneval\.venv\Scripts",
                    help="Scripts dir of the repowise build under test")
    args = ap.parse_args()

    # Resolve every path the CLI receives. `claude` runs with cwd set to the
    # arm's tree, so a relative --settings resolves against the TREE, not
    # against the bench root, and it then exits 1 with "Settings file not
    # found" while the runner records a clean zero row. That is the plausible
    # zero again, and it cost this run one smoke cell to find.
    for attr in ("settings", "mcp_config"):
        val = getattr(args, attr, None)
        if val:
            setattr(args, attr, str(Path(val).resolve()))
            if not Path(getattr(args, attr)).is_file():
                print(f"--{attr.replace('_', '-')} not found: "
                      f"{getattr(args, attr)}", file=sys.stderr)
                return 2

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    tasks = cfg["tasks"]
    if args.task_ids:
        wanted = [t.strip() for t in args.task_ids.split(",") if t.strip()]
        known = {t["id"] for t in tasks}
        missing = [w for w in wanted if w not in known]
        if missing:
            # A typo'd id must not silently select nothing: an empty task list
            # runs no work, writes a clean summary row and reads as a completed
            # session. Fail loudly instead.
            print(f"--task-ids names unknown task(s): {missing}. "
                  f"Known: {sorted(known)}", file=sys.stderr)
            return 2
        # Config order, not argument order, so a session's task sequence stays
        # frozen regardless of how the ids were typed.
        tasks = [t for t in tasks if t["id"] in set(wanted)]
    if args.limit_tasks:
        tasks = tasks[: args.limit_tasks]

    tree = Path(args.tree).resolve()
    if not tree.is_dir():
        print(f"tree missing: {tree}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume on (cell_id, arm, condition, task_id).
    done, session_id = {}, None
    if out_path.exists():
        for ln in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if (row.get("cell_id") == cfg["cell_id"] and row.get("arm") == args.arm
                    and row.get("condition") == args.condition):
                done[row.get("task_id")] = row
                session_id = row.get("session_id") or session_id
    first = session_id is None
    if session_id is None:
        session_id = str(uuid.uuid4())

    arm = {
        "name": args.arm,
        "uses_mcp": args.uses_mcp,
        "client_tools": [t for t in args.client_tools.split(",") if t],
        "coaching": (Path(args.coaching_file).read_text(encoding="utf-8")
                     if args.coaching_file else (args.coaching or "")),
    }

    claude_home = prepare_claude_home()
    if claude_home == Path.home() / ".claude":
        print("!! ISOLATION FAILED: could not hard-link bench credentials, so "
              "this cell would run inside the operator's own config, with the "
              "operator's hooks and plugins firing into it (D16). Refusing.",
              file=sys.stderr)
        return 3
    run_env = _utf8_env(claude_home, Path(args.pinned_bin))

    print(f"cell={cfg['cell_id']} arm={args.arm} condition={args.condition}")
    print(f"claude_home={claude_home}")
    print(f"session_id={session_id} (resuming={not first}) tasks={len(tasks)}")
    print(f"tree={tree}")

    # Keyed on session_id, NOT just cell/arm/condition. The old name was stable
    # across runs, so re-running an arm silently OVERWROTE the previous run's
    # transcripts — and the transcripts are the only record of what the agent
    # actually did. That cost a real comparison: a post-fix re-run destroyed the
    # pre-fix run's payloads, and the question "did the same mechanism cause
    # both failures" became unanswerable.
    #
    # session_id is the right key rather than a timestamp: it is generated fresh
    # per run (uuid4) so two runs can never collide, and it is READ BACK from the
    # out file on resume, so a resumed session keeps writing to its own
    # directory instead of starting a second one.
    scratch = (out_path.parent /
               f"_status_{cfg['cell_id']}_{args.arm}_{args.condition}__{session_id[:8]}")
    scratch.mkdir(parents=True, exist_ok=True)

    totals = {k: 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                             "cache_creation_tokens", "tool_calls", "mcp_calls",
                             "cli_calls",
                             "num_turns", "hook_events", "hook_injections")}
    total_cost, agent_wall, oracle_wall = 0.0, 0.0, 0.0

    for task in tasks:
        tid = task["id"]
        if tid in done:
            row = done[tid]
            print(f"  {tid} SKIP (already recorded)")
            for k in totals:
                totals[k] += int(row.get(k) or 0)
            total_cost += float(row.get("cost_usd") or 0.0)
            agent_wall += float(row.get("agent_wall_seconds") or 0.0)
            oracle_wall += float(row.get("oracle", {}).get("wall_seconds") or 0.0) if isinstance(row.get("oracle"), dict) else 0.0
            continue

        # Snapshot BEFORE the task so a "nothing else changed" oracle is
        # relative to this task and not to the pin. Written without a BOM.
        baseline = scratch / f"{tid}_pre.txt"
        baseline.write_text(git_status(tree), encoding="utf-8")

        cmd = build_cmd(task, session_id, first, arm, cfg,
                        args.mcp_config, args.settings, args.model,
                        args.max_turns, args.max_budget_usd)
        log_path = scratch / f"{tid}_stream.jsonl"

        t0 = time.time()
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                proc = subprocess.Popen(cmd, cwd=str(tree), stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace",
                                        env={**run_env, "BENCH_TASK_ID": tid})
                lines = []
                for ln in proc.stdout:
                    fh.write(ln)
                    lines.append(ln)
                proc.wait(timeout=args.timeout)
                stderr = proc.stderr.read()
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            lines, stderr, timed_out = lines, "timeout", True
        elapsed = round(time.time() - t0, 2)
        agent_wall += elapsed

        parsed = parse_stream(lines)

        # A cell with no result event is a DEAD cell, not a cheap one. Writing
        # a row of zeroes here is how a broken flag, a missing settings file or
        # an unauthenticated CLI becomes a data point that reads as "this arm
        # used nothing and cost nothing". Refuse, loudly, and write nothing.
        if not parsed["session_id"] and not parsed["num_turns"]:
            print(f"\n!! NO RESULT EVENT on {tid} after {elapsed}s. "
                  f"stream lines={len(lines)}. stderr: {(stderr or '')[-400:]!r}\n"
                  f"   Refusing to write a zero row.", file=sys.stderr)
            return 5

        if looks_unauthenticated(parsed["answer"]):
            print(f"\n!! NOT AUTHENTICATED on {tid}: the CLI exited 0 and "
                  f"answered {parsed['answer'][:120]!r}. Every cell of every "
                  f"arm would record as complete at $0 with a wrong answer. "
                  f"Refusing to write a row.", file=sys.stderr)
            return 4
        first = False
        if parsed["session_id"] and parsed["session_id"] != session_id:
            print(f"  !! session id drifted: {parsed['session_id']} != {session_id}")

        oracle_result = run_oracle(task, tree, baseline)
        if isinstance(oracle_result, dict):
            oracle_wall += float(oracle_result.get("wall_seconds") or 0.0)

        row = {
            "cell_id": cfg["cell_id"], "arm": args.arm,
            "condition": args.condition, "task_id": tid,
            "task_kind": task["kind"], "source_issue": task.get("source_issue"),
            "session_id": session_id, "model": args.model,
            "agent_wall_seconds": elapsed, "timed_out": timed_out,
            "cost_usd": parsed["cost_usd"],
            "oracle": oracle_result,
            "answer_chars": len(parsed["answer"]),
            "stderr_tail": (stderr or "")[-300:],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_creation_tokens", "tool_calls", "mcp_calls",
                  "cli_calls", "cli_cmds",
                  "num_turns", "hook_events", "hook_injections", "hook_names",
                  "mcp_isError", "token_source", "models_used",
                  "tool_names", "mcp_tools", "stop_reason", "is_error",
                  "cache_ttl_1h", "cache_ttl_5m"):
            row[k] = parsed[k]

        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

        for k in totals:
            totals[k] += int(row.get(k) or 0)
        total_cost += row["cost_usd"]

        oc = oracle_result.get("passed") if isinstance(oracle_result, dict) else None
        oc_s = "-" if oc is None else ("PASS" if oc else "FAIL")
        print(f"  {tid} {task['kind']:<12} turns={parsed['num_turns']:>3} "
              f"tools={parsed['tool_calls']:>3} mcp={parsed['mcp_calls']:>2} "
              f"cli={parsed['cli_calls']:>2} "
              f"out={parsed['output_tokens']:>6} cr={parsed['cache_read_tokens']:>8} "
              f"${parsed['cost_usd']:.4f} {elapsed:>7.1f}s oracle={oc_s}")

    billed = (totals["input_tokens"] + totals["output_tokens"]
              + totals["cache_read_tokens"] + totals["cache_creation_tokens"])
    summary = {
        "type": "session_summary",
        "cell_id": cfg["cell_id"], "arm": args.arm, "condition": args.condition,
        "session_id": session_id, "model": args.model,
        "tasks": len(tasks), "total_billed_tokens": billed,
        "total_cost_usd": round(total_cost, 6),
        "agent_wall_seconds": round(agent_wall, 1),
        "oracle_wall_seconds": round(oracle_wall, 1),
        **totals,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(out_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")

    print("\n=== SESSION SUMMARY ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
