"""A second agent harness, so a Layer B row is a claim about repowise.

Every Layer B number this workstream has produced is a statement about **Claude
Code**: one agent harness, one model family, one tool-loading mechanism. Claude
Code defers MCP schemas behind `ToolSearch`, which is exactly the mechanism
finding A30 is about — under a neutral prompt a sonnet agent never issued
`ToolSearch` at all and answered a django question with nine Greps. If our lift
(or our loss) is an artifact of that, no amount of n fixes it, and it is the
first objection a reviewer makes. A second harness is the cheapest robustness
check available and nobody in this field publishes one.

`config["agent"]["harness"] = "codex"` routes here. Codex CLI 0.145.0.

---------------------------------------------------------------------------
What is NOT the same as the Claude runner, and must travel with any number
---------------------------------------------------------------------------

1. **Codex reports tokens, not dollars.** Claude Code's stream carries
   `total_cost_usd` computed by the CLI. Codex's `turn.completed` carries
   `usage` and nothing else, so cost here is COMPUTED from token counts at
   list rates (`CODEX_PRICES`). A Claude cost and a Codex cost are therefore
   not the same kind of number and a table putting them in one column is
   wrong. Within-harness arm-to-arm comparison is what this runner is for.

2. **The tool surfaces are not the same shape.** The Claude cells run
   `Read,Grep,Glob` with no Bash, because Bash lets an agent leave the repo and
   read the benchmark's own answer key. Codex's core tool IS a sandboxed shell;
   there is no `--allowed-tools` and no way to take it away. `-s read-only`
   plus a working root pinned to the arm's worktree is the nearest equivalent,
   and it is nearer than it sounds — the sandbox refuses writes and refuses
   commands outside policy — but it is not the same restriction. A Codex cell
   can run `grep` where a Claude cell had to call the Grep tool. Report it.

3. **The judge flips family.** `DEFAULT_JUDGE_MODEL` is a GPT model, chosen
   when every arm was Claude-family. A Codex arm on a GPT model graded by it is
   same-family self-grading, D3/D8 from the other side. `_resolve_judge_model`
   now compares families and picks the default from the agent's family, so a
   Codex run grades with Claude. That means a cross-HARNESS table has two
   different judges, which is a real confound; the mitigation PLAN.md already
   specifies for luna-vs-terra applies — grade a stratified subset with both
   and publish the agreement.

---------------------------------------------------------------------------
Isolation, and the flag that does not do what its name says
---------------------------------------------------------------------------

Finding D16 is that the operator's own config fires inside every cell, and
`--strict-mcp-config` covered MCP servers and nothing else. Codex has the same
shape and the obvious lever is `--ignore-user-config`. **Measured 2026-08-03,
it does not do the job**, and this is the whole reason the probe exists rather
than the flag:

  | CODEX_HOME | --ignore-user-config | sentinel PreToolUse hook |
  |---|---|---|
  | throwaway home with a hooks.json | no  | **fired 5x** |
  | throwaway home with a hooks.json | yes | **fired 3x** |
  | bench home, no hooks.json        | yes | did not fire |

`hooks.json` is a separate file from `config.toml`, and `--ignore-user-config`
says it ignores `config.toml`. It means it. On this machine that file carried a
`repowise-rewrite --agent codex` PreToolUse hook on the shell — the distill
treatment Layer C exists to isolate, applied ambiently to every arm including
the control — and a run trusting the flag would have inherited it.

So the mechanism is a **bench-owned `CODEX_HOME`**, exactly as the Claude side
uses a bench-owned `CLAUDE_CONFIG_DIR`, with `auth.json` HARD-LINKED rather
than copied so there is no second copy of a credential on disk.
`--ignore-user-config` is kept as belt and braces, not as the mechanism.

Two consequences of an empty home that cost a cell each to find:

  * **Codex's sandbox refuses shell commands in an untrusted directory.** With
    no `projects.<dir>.trust_level` the router answers `rejected: blocked by
    policy` to nearly everything and the agent flails. Trust for the arm's
    worktree is passed with `-c` on the command line, which is an override
    rather than user config, so it survives `--ignore-user-config`.
  * **`CODEX_HOME` under a path containing `tmp` makes Codex refuse to create
    its helper binaries.** The bench home lives in the checkout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from harness.arms import Arm

BENCH_ROOT = Path(__file__).resolve().parents[1]
BENCH_CODEX_HOME = BENCH_ROOT / ".codex_bench_home"

CODEX_EXE = os.environ.get("CODEX_EXE") or str(
    Path(os.environ.get("APPDATA", str(Path.home()))) / "npm" / "codex.cmd"
)

# List rates, USD per million tokens. Codex emits token counts and no cost, so
# a Codex cost is a figure this file computes and not one the CLI reported.
# Anything published from it says so.
CODEX_PRICES: dict[str, dict[str, float]] = {
    # model prefix -> {input, cached_input, output} per 1M tokens
    "gpt-5.6": {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5.4": {"input": 1.25, "cached_input": 0.125, "output": 10.00},
}
_DEFAULT_PRICE = {"input": 1.25, "cached_input": 0.125, "output": 10.00}


def _price_for(model: str) -> dict:
    for prefix, price in CODEX_PRICES.items():
        if model.startswith(prefix):
            return price
    return _DEFAULT_PRICE


def compute_cost_usd(usage: dict, model: str) -> float:
    """Cost from token counts, because Codex does not report one.

    `input_tokens` in Codex's usage block is the TOTAL prompt, with
    `cached_input_tokens` a subset of it — not a sibling as in Anthropic's
    accounting. Subtracting is the difference between a plausible number and a
    number roughly 8x too large on a cached turn, which is every turn after the
    first.
    """
    p = _price_for(model)
    total_in = int(usage.get("input_tokens") or 0)
    cached_in = int(usage.get("cached_input_tokens") or 0)
    fresh_in = max(0, total_in - cached_in)
    out = int(usage.get("output_tokens") or 0)
    return (
        fresh_in * p["input"] / 1e6
        + cached_in * p["cached_input"] / 1e6
        + out * p["output"] / 1e6
    )


# ---------------------------------------------------------------------------
# The bench-owned CODEX_HOME
# ---------------------------------------------------------------------------

def prepare_codex_home(trees_root: Optional[Path] = None) -> Path:
    """A config root the bench owns, holding a hard link to the credential.

    `hooks.json` is deleted rather than merely not-written: a stale one from a
    probe run is the whole failure mode this directory exists to prevent.
    `config.toml` is REWRITTEN each run from the template below, so its
    contents are a property of this file and not of whatever was there before.

    It is not empty, and it took two failed probe designs to work out why it
    must not be. Codex refuses shell commands in an untrusted project — the
    router answers `rejected: blocked by policy` and the agent spends its turns
    retrying — so the arm's worktree has to be trusted somewhere. The obvious
    route, a `-c projects.<path>.trust_level` override on the command line, is
    a trap on Windows: the key is a TOML dotted path and `"C:\\Users\\..."` is a
    *basic* TOML string in which `\\U` is an escape sequence. It does not error.
    It resolves to a different key, the trust silently does not apply, and the
    only symptom is an agent that cannot run anything.

    So trust is declared here, in a file this file writes, and the trees root
    is trusted once rather than each worktree separately.

    `--ignore-user-config` is consequently NOT the mechanism and the runner no
    longer depends on it. `CODEX_HOME` replaces the config root outright, which
    is what actually removes the operator's `hooks.json`, `rules/`, `plugins/`
    and MCP servers — and it is what the probe measures. See
    `codex_isolation_probe.py`: the flag leaves `$CODEX_HOME/hooks.json` firing.
    """
    BENCH_CODEX_HOME.mkdir(parents=True, exist_ok=True)
    stale_hooks = BENCH_CODEX_HOME / "hooks.json"
    if stale_hooks.exists():
        stale_hooks.unlink()

    root = trees_root or Path(
        os.environ.get("BAKEOFF_TREES") or (Path.home() / "Desktop" / "bakeoff"))
    # TOML LITERAL string for the key (single quotes). A basic string would
    # re-introduce the backslash-escape bug above inside the file itself.
    (BENCH_CODEX_HOME / "config.toml").write_text(
        "# Written by harness/codex_runner.py::prepare_codex_home on every run.\n"
        "# Deliberately minimal: no mcp_servers, no hooks, no profiles. The only\n"
        "# entry is trust for the bake-off's worktree root, without which Codex\n"
        "# rejects every shell command as blocked by policy.\n"
        f"[projects.'{root}']\n"
        'trust_level = "trusted"\n',
        encoding="utf-8",
    )

    src = Path(os.environ.get("CODEX_AUTH") or Path.home() / ".codex" / "auth.json")
    dst = BENCH_CODEX_HOME / "auth.json"
    if src.exists():
        relink = True
        if dst.exists():
            try:
                relink = dst.stat().st_ino != src.stat().st_ino
            except OSError:
                relink = True
        if relink:
            if dst.exists():
                dst.unlink()
            try:
                os.link(str(src), str(dst))
            except OSError:
                import shutil
                shutil.copyfile(str(src), str(dst))
    return BENCH_CODEX_HOME


def trust_toml(root: Path | str) -> str:
    """The minimal `config.toml` a bench-owned CODEX_HOME carries.

    Shared with the isolation probe so its sentinel home and the real bench
    home differ in exactly one file, `hooks.json`, and a difference in outcome
    between them is therefore attributable.
    """
    return (
        f"[projects.'{root}']\n"
        'trust_level = "trusted"\n'
    )


# ---------------------------------------------------------------------------
# MCP servers, per arm
# ---------------------------------------------------------------------------

def _toml(value) -> str:
    """A TOML literal for a `-c key=value` override.

    `-c` parses its value as TOML and falls back to "the raw string" when that
    fails, so a malformed value does not always error — it sometimes becomes a
    string where an array was meant, which is worse. JSON is not TOML: a JSON
    object is not a TOML inline table, and a JSON string with a Windows path in
    it is only accidentally right (both treat `\\\\` as an escaped backslash).
    Encode deliberately rather than lean on that.

    Note that anything passed this way is visible in the process command line,
    including an `OPENAI_API_KEY` forwarded to the server. The Claude runner
    puts the same value in a file instead. Accepted here because `-c` is the
    only channel Codex offers for a per-invocation server, and because the key
    is already in `provider_config.json` on the same machine; it is recorded so
    that a CI port of this does not inherit the decision silently.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_toml(v) for v in value) + "]"
    s = str(value)
    escaped = (s.replace("\\", "\\\\").replace('"', '\\"')
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{escaped}"'


def mcp_overrides(arm: Arm, mcp_config_path: Optional[str]) -> list[str]:
    """Mount this arm's server through `-c mcp_servers.<name>.…`.

    Reads the SAME `.mcp.json` the Claude runner mounts, rather than
    re-deriving the launch from the arm record, so the two harnesses cannot
    drift into starting the server differently — which would make a
    cross-harness difference a fact about the harness's config writer.
    """
    if not arm.uses_mcp or not mcp_config_path:
        return []
    cfg = json.loads(Path(mcp_config_path).read_text(encoding="utf-8"))
    spec = cfg["mcpServers"][arm.server_name]
    name = arm.server_name
    out = ["-c", f"mcp_servers.{name}.command={_toml(spec['command'])}"]
    out += ["-c", f"mcp_servers.{name}.args={_toml(spec.get('args') or [])}"]
    # One override per env key rather than one inline table. `-c foo={"A":"b"}`
    # is JSON, not TOML, and Codex answers `Error loading config.toml: invalid
    # type: str` — an error naming a file the run never wrote, for a value that
    # came off the command line.
    for key, value in (spec.get("env") or {}).items():
        out += ["-c", f"mcp_servers.{name}.env.{key}={_toml(value)}"]
    # repowise's first call after a server start can hang (finding A8); the
    # client giving up is what unblocks it. Give the server room to start and
    # the tool a bound, rather than letting one call eat the cell's timeout.
    out += ["-c", f"mcp_servers.{name}.startup_timeout_sec=60"]
    out += ["-c", f"mcp_servers.{name}.tool_timeout_sec=180"]

    # Auto-approve every tool the arm allowlists, per tool, because Codex's
    # non-interactive approval policy is DENY and not ALLOW.
    #
    # This is the Codex spelling of finding D1, and it is the most expensive
    # possible failure mode dressed as the cheapest-looking row. The first
    # repowise cell run here came back `arm_exercised: true`, one
    # `get_answer` issued, `error: null` on the cell, a judge score of 9.0/10
    # and a plausible cost — and the call itself had returned
    # `{"error": {"message": "user cancelled MCP tool call"}}` in a run with no
    # user in it. The agent asked its server a question, was silently refused,
    # fell back to the shell, answered well, and every summary field said the
    # arm had been used. Only `mcp_isError_count` disagreed.
    #
    # An arm allowlists what it allowlists; auto-approving exactly that set and
    # nothing else keeps the Codex surface equal to the Claude one, where
    # `--allowedTools` grants the same thing without an approval step.
    for tool in arm.client_tools:
        short = tool.split("__")[-1]
        out += ["-c", f'mcp_servers.{name}.tools.{short}.approval_mode="approve"']
    return out


def configured_mcp_servers(codex_home: str, extra: Optional[list[str]] = None) -> list[dict]:
    """What `codex mcp list --json` reports under these exact flags.

    A config-level view, not a stream-level one: Codex's `--json` event stream
    has no init event and never names its MCP servers, so "zero servers
    mounted" is not directly readable from a cell the way it is on the Claude
    side. This is the honest substitute and its limit is stated where it is
    used.
    """
    cmd = [CODEX_EXE, "mcp", "list", "--json"] + list(extra or [])
    env = {**os.environ, "CODEX_HOME": codex_home,
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with open(os.devnull) as devnull:
        p = subprocess.run(cmd, capture_output=True, text=True, stdin=devnull,
                           encoding="utf-8", errors="replace", timeout=120, env=env)
    try:
        return json.loads(p.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------

_ARG_CLIP = 400


def _clip_args(args) -> dict:
    """One MCP call's arguments, with long string values clipped.

    Values stay whole where they are short, which is the case that matters:
    `refresh_index: false` and a shell command survive verbatim, while a
    `get_answer` question is cut at 400 characters so this ledger cannot
    dominate a result row.
    """
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > _ARG_CLIP:
            out[k] = v[:_ARG_CLIP] + f"...[+{len(v) - _ARG_CLIP} chars]"
        else:
            out[k] = v
    return out


def parse_codex_stream(lines: list[str], model: str) -> dict:
    """Codex's JSONL events into the same shape the Claude parser returns.

    Event vocabulary observed on 0.145.0::

        {"type":"thread.started","thread_id":...}
        {"type":"turn.started"}
        {"type":"item.started"  ,"item":{"id":...,"type":"command_execution",...}}
        {"type":"item.completed","item":{"id":...,"type":"agent_message","text":...}}
        {"type":"item.completed","item":{"id":...,"type":"mcp_tool_call",...}}
        {"type":"turn.completed","usage":{...}}
        {"type":"error","message":...}

    Note what is NOT here: no init event, so no advertised tool list and no
    mounted-server list; and no hook events, so a hook that fires inside a
    Codex cell is invisible to this stream. Claude Code's
    `--include-hook-events` is what made finding D16 visible at all. On the
    Codex side the equivalent evidence has to come from
    `codex_isolation_probe.py`, which detects a hook by its side effect
    instead. `hook_events` is therefore reported as `None` — unknown — and
    never as `[]`, which would read as a measurement that a hook did not fire.
    """
    answer = ""
    usage: dict = {}
    num_tool_calls = 0
    mcp_tools_issued: list[str] = []
    mcp_call_args: list[dict] = []
    mcp_per_server: dict = {}
    mcp_is_error = 0
    files_explored: list[str] = []
    commands: list[str] = []
    errors: list[str] = []
    turns = 0

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t == "turn.completed":
            usage = d.get("usage") or {}
            turns += 1
        elif t == "error":
            errors.append(str(d.get("message") or d)[:400])
        elif t == "item.completed":
            item = d.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message":
                answer = item.get("text") or answer
            elif itype == "command_execution":
                num_tool_calls += 1
                cmd = item.get("command") or ""
                commands.append(cmd)
                files_explored.extend(_paths_in(cmd))
            elif itype == "file_change":
                num_tool_calls += 1
            elif itype in ("mcp_tool_call", "mcp_tool_call_output"):
                if itype != "mcp_tool_call":
                    continue
                num_tool_calls += 1
                server = item.get("server") or item.get("server_name") or "?"
                tool = item.get("tool") or item.get("tool_name") or "?"
                mcp_tools_issued.append(f"mcp__{server}__{tool}")
                # The ARGUMENTS the agent actually sent, not only the tool
                # name. Two things need them and neither can be answered from
                # a name: cocoindex's `refresh_index` defaults TRUE and Layer B
                # cannot pin an agent's arguments, so the only honest record is
                # what was passed; and serena's `execute_shell_command` runs
                # outside codex's sandbox, so `answer_leak_audit.py` has to see
                # the command to check it never reached the benchmark's own
                # answer key. Truncated per call: a get_answer question is
                # prose and this ledger sits on every row.
                mcp_call_args.append({
                    "tool": f"mcp__{server}__{tool}",
                    "arguments": _clip_args(item.get("arguments")),
                })
                bucket = mcp_per_server.setdefault(server, {"ok": 0, "error": 0})
                failed = bool(item.get("isError") or item.get("is_error")
                              or item.get("error")
                              or item.get("status") == "failed")
                bucket["error" if failed else "ok"] += 1
                mcp_is_error += int(failed)
            elif itype in ("reasoning", "todo_list", "web_search"):
                pass

    return {
        "answer": answer,
        "num_turns": turns,
        "total_cost_usd": compute_cost_usd(usage, model),
        "input_tokens": int(usage.get("input_tokens") or 0)
        - int(usage.get("cached_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cached_input_tokens") or 0),
        "cache_write_tokens": int(usage.get("cache_write_input_tokens") or 0),
        "num_tool_calls": num_tool_calls,
        "files_explored": sorted(set(files_explored)),
        "files_edited": [],
        "repowise_tools_called": [t for t in mcp_tools_issued if "repowise" in t],
        "mcp_tools_issued": mcp_tools_issued,
        "mcp_call_args": mcp_call_args,
        "mcp_isError_count": mcp_is_error,
        "mcp_per_server": mcp_per_server,
        "commands": commands,
        "stream_errors": errors,
        # Not [] — see the docstring. Codex's stream cannot answer this.
        "hook_events": None,
        "hook_injections": None,
        "models_used": [model],
        "token_source": "codex:turn.completed.usage",
        "cost_source": "computed_from_tokens",
    }


_PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|txt|md|cfg|toml|json|yaml|yml|html|js|ts)")


def _paths_in(command: str) -> list[str]:
    """Files a shell command plausibly touched.

    Necessarily weaker than the Claude side, where `files_explored` comes from
    the Read tool's own argument. A `grep -r foo .` reads hundreds of files and
    names none of them. So a Codex `files_read` is a LOWER BOUND and is not
    comparable to a Claude one; it is comparable between Codex arms.
    """
    return [m.group(0) for m in _PATH_RE.finditer(command or "")]


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def build_codex_system_prompt(base_system_prompt: str, coaching: str) -> str:
    """Codex has no `--append-system-prompt`; instructions ride the prompt.

    Both harnesses therefore deliver the same text and deliver it differently,
    which is a difference between them and not between the arms inside either.
    """
    parts = [p.strip() for p in (base_system_prompt, coaching) if p and p.strip()]
    return "\n\n".join(parts)


def run_codex(prompt: str, repo_path: str, condition: dict, model: str,
              timeout: int, arm: Arm,
              mcp_config_path: Optional[str] = None,
              system_prompt: str = "",
              stream_log_path: Optional[str] = None,
              codex_home: Optional[str] = None) -> tuple[dict, int]:
    """One Codex cell. Returns (output_dict, retries_used), like run_claude_code."""
    home = codex_home or str(prepare_codex_home())
    full_prompt = "\n\n".join(p for p in (system_prompt, prompt) if p)

    # The prompt goes on STDIN, not on the command line, and this is not a
    # style choice. `CODEX_EXE` is `codex.cmd`, a batch shim, so every argument
    # crosses cmd.exe — and a NEWLINE inside an argument terminates the command
    # line there. Passing the prompt positionally truncated it at its first
    # blank line and silently ate every flag after it: the agent received only
    # the first paragraph of its system prompt, `--json` never took effect so
    # the stream was human-readable text, and `--cd` never took effect so the
    # agent ran in repowise-bench instead of the arm's worktree. The cell
    # errored rather than scoring, which is the only lucky part: an agent
    # answering a question it was never asked, from the wrong directory, is
    # otherwise a complete-looking row.
    #
    # `codex exec -` reads the instructions from stdin. It also removes the
    # `Reading additional input from stdin...` warning, which was Codex saying
    # it had appended whatever the parent's stdin held to the agent's context.
    cmd = [
        CODEX_EXE, "exec", "-",
        "--json",
        "--model", model,
        "--cd", repo_path,
        "--sandbox", "read-only",
        # NOT --ignore-user-config. CODEX_HOME already replaces the config
        # root, which is the thing that actually removes the operator's hooks,
        # rules and servers; the flag additionally suppresses the bench's OWN
        # config.toml, which is where the worktree's trust entry lives, and
        # without that Codex rejects every shell command as blocked by policy.
        # The flag is measured in codex_isolation_probe.py and does not
        # suppress $CODEX_HOME/hooks.json anyway, so it was never the mechanism.
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
    ]
    cmd += mcp_overrides(arm, mcp_config_path)

    env = {**os.environ, "CODEX_HOME": str(Path(home)).replace("\\", "/"),
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "DO_NOT_TRACK": "1"}

    log = Path(stream_log_path) if stream_log_path else None
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, input=full_prompt,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as e:
        partial = e.output if isinstance(e.output, str) else ""
        if log:
            log.write_text(partial or "", encoding="utf-8")
        return {"error": f"timeout after {timeout}s",
                "timed_out": True,
                "_raw_stream_lines": (partial or "").splitlines()}, 0

    lines = (p.stdout or "").splitlines()
    if log:
        log.write_text(p.stdout or "", encoding="utf-8")
        Path(str(log) + ".err").write_text(p.stderr or "", encoding="utf-8")

    if p.returncode != 0 and not lines:
        return {"error": f"codex exited {p.returncode}: {(p.stderr or '')[:400]}",
                "returncode": p.returncode,
                "_raw_stream_lines": lines}, 0

    # `--json` not taking effect is a DIFFERENT failure from the agent saying
    # nothing, and the two are indistinguishable downstream: both arrive as an
    # empty answer. It happened, from a batch-shim command line that swallowed
    # the flag, and the row it produced said `codex produced no agent_message`
    # while the agent had in fact answered fine in a human-readable stream, in
    # the wrong directory, to a truncated question. Separate them here.
    if lines and not any(ln.lstrip().startswith("{") for ln in lines[:5]):
        return {"error": "codex stream is not JSONL — `--json` did not take "
                         "effect, so this cell's flags were not applied as "
                         f"written; first line: {lines[0][:200]!r}",
                "_raw_stream_lines": lines}, 0

    parsed = parse_codex_stream(lines, model)
    if not parsed["answer"]:
        return {"error": "codex produced no agent_message; "
                         f"stderr={(p.stderr or '')[:300]}",
                "_raw_stream_lines": lines}, 0

    out = {
        "result": parsed["answer"],
        "num_turns": parsed["num_turns"],
        "task_subagent_calls": 0,
        "total_cost_usd": parsed["total_cost_usd"],
        "usage": {
            "input_tokens": parsed["input_tokens"],
            "output_tokens": parsed["output_tokens"],
            "cache_read_input_tokens": parsed["cache_read_tokens"],
            "cache_creation_input_tokens": parsed["cache_write_tokens"],
        },
        "session_id": "",
        "stop_reason": "",
        "duration_api_ms": int((time.time() - t0) * 1000),
        "num_tool_calls": parsed["num_tool_calls"],
        "files_explored": parsed["files_explored"],
        "files_edited": [],
        "repowise_tools_called": parsed["repowise_tools_called"],
        "mcp_tools_issued": parsed["mcp_tools_issued"],
        "mcp_call_args": parsed["mcp_call_args"],
        "mcp_isError_count": parsed["mcp_isError_count"],
        "mcp_per_server": parsed["mcp_per_server"],
        "hook_events": parsed["hook_events"],
        "hook_injections": parsed["hook_injections"],
        "models_used": parsed["models_used"],
        "token_source": parsed["token_source"],
        "_raw_stream_lines": lines,
        "_codex_commands": parsed["commands"],
        "_codex_stream_errors": parsed["stream_errors"],
    }
    return out, 0
