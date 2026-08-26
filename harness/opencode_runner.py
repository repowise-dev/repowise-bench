"""opencode harness adapter — run a SWE-QA task through opencode + a local Ollama model.

Local-model counterpart to ``run_claude_code`` in ``swe_qa_runner.py``. Same
conditions (C0 = no repowise, C2 = repowise MCP), but an opencode agent driving
an Ollama-served model instead of Claude Code driving Anthropic.

TRANSPORT — the JSON CLI, since 1.18.15:
  ``opencode run --format json`` emitted ZERO bytes on the 1.15.13 Windows build
  (verified streaming for 300s), and a persistent ``opencode serve`` REST
  singleton existed for that one reason. **Re-measured on 1.18.15 (2026-08-09):
  the CLI emits NDJSON, including the tool-call events.** The singleton is
  therefore deleted rather than maintained — it was workaround scaffolding, and
  keeping it would mean carrying shared cross-cell server state for nothing.

  One process per cell:
    opencode run --format json --dir <repo> --model <provider/model>
                 --agent <BENCH_AGENT> "<prompt>"

  Each stdout line is one event: {type, timestamp, sessionID, part:{...}}. The
  ``part`` objects carry the SAME ``type`` values the REST route returned
  (tool / text / step-finish), which is why ``parse_session_messages`` is reused
  verbatim instead of a second aggregator being written.

  Events observed on a tool-using turn:
    step_start  -> part.type "step-start"
    tool_use    -> part.type "tool",        part.tool "repowise_search_codebase"
    text        -> part.type "text",        part.text "<the answer>"
    step_finish -> part.type "step-finish", part.tokens {...}, part.cost 0

BINARY — never the .CMD shim:
  ``shutil.which("opencode")`` resolves to the npm ``opencode.CMD`` batch shim.
  A NEWLINE in a positional argument truncates a batch shim's command line and
  silently drops every flag after it — this workstream already lost time to
  exactly that with ``codex.cmd``, where ``--json`` and ``--cd`` vanished with no
  error. Our prompt is always multi-line, so the real ``opencode.exe`` under
  node_modules is resolved and the shim is a last resort.

SYSTEM PROMPT — via the config, because the CLI has no --system:
  ``opencode run`` exposes no system-prompt flag. The config schema does:
  ``agent.<name>.prompt``. So the per-condition system prompt is written into
  opencode.json as a custom agent and selected with ``--agent``.

MODEL PRECONDITIONS (learned the hard way — see harness/check_tool_calling.py):
  - The Ollama model MUST emit *structured* tool_calls. qwen2.5-coder:7b emits
    them as plain text and is unusable; qwen3/qwen3.5 work; llama3.2:3b is flaky.
  - Ollama's default 4096 context truncates the tool definitions -> create a
    larger-context variant (e.g. ``qwen3.5:4b-16k`` via a Modelfile num_ctx 16384).
  - qwen3-family thinking balloons latency; append ``/no_think`` to the prompt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_BENCH_ROOT = Path(__file__).resolve().parents[1]

from harness.swe_qa_runner import _UTF8_ENV  # noqa: E402

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# The custom agent that carries our system prompt. `opencode run` has no
# --system flag, so the prompt travels in opencode.json under this name.
BENCH_AGENT = "bench"


def _resolve_opencode_bin() -> str:
    """The real opencode.exe, never the npm .CMD shim if it can be avoided.

    `shutil.which("opencode")` returns `opencode.CMD`. Passing a MULTI-LINE
    positional argument through a batch shim truncates the command line at the
    newline and silently drops every flag after it, which is how `codex.cmd`
    swallowed `--json` and `--cd` with rc=0 and no error. Our prompt always
    contains newlines, so resolve the executable the shim wraps.
    """
    env_bin = os.environ.get("OPENCODE_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    shim = shutil.which("opencode")
    if shim:
        # npm layout: <prefix>/opencode.CMD wraps
        # <prefix>/node_modules/opencode-ai/bin/opencode.exe
        real = (Path(shim).parent / "node_modules" / "opencode-ai" / "bin"
                / "opencode.exe")
        if real.exists():
            return str(real)
    return shim or "opencode"


_OPENCODE_BIN = _resolve_opencode_bin()


def _resolve_repowise_exe() -> str:
    """Locate the repowise console script.

    ``python -m repowise.cli.main`` is a no-op on this branch (main.py builds the
    click group but has no __main__ guard), so the MCP server must be launched
    via the entry-point exe. The editable install makes ``repowise`` importable
    without PYTHONPATH, so no extra env is needed.
    """
    env_exe = os.environ.get("REPOWISE_EXE")
    if env_exe and Path(env_exe).exists():
        return env_exe
    candidate = _BENCH_ROOT.parent / ".venv" / "Scripts" / "repowise.exe"
    if candidate.exists():
        return str(candidate)
    return "repowise"


_REPOWISE_EXE = _resolve_repowise_exe()


# ---------------------------------------------------------------------------
# opencode.json generation (per-repo, in the repo cwd)
# ---------------------------------------------------------------------------

# Every tool the repowise MCP server serves in single-repo mode, read off a live
# server on 2026-08-09. Named explicitly so a condition's allowlist can DENY the
# complement: a tool the server gains later would otherwise default to enabled
# and quietly re-enter a condition that is defined by its absence.
REPOWISE_SERVED_TOOLS = (
    "get_answer", "get_change_risk", "get_context", "get_dead_code",
    "get_health", "get_overview", "get_risk", "get_symbol", "get_why",
    "list_repos", "search_codebase",
)

# Large, low-QA-value payloads. A 16k window cannot afford them and the answer
# does not need them. Denied in every repowise condition.
_VERBOSE_TOOLS = ("get_overview", "get_health", "get_dead_code")


def build_opencode_config(*, model: str, repowise_enabled: bool, repo_path: Path,
                          allowed_tools: Optional[list] = None,
                          system_prompt: Optional[str] = None) -> dict:
    """opencode.json for one condition.

    provider  -> local Ollama OpenAI-compatible endpoint.
    permission-> read-only (deny edit/write/bash) so SWE-QA cannot escape or
                 mutate the repo, mirroring the Claude arm's tool restriction.
    snapshot  -> false; opencode's git-snapshot crawl hangs on large repos and is
                 useless for read-only QA.
    mcp       -> repowise server, present only for repowise conditions.
    agent     -> carries the system prompt; `opencode run` has no --system flag.
    tools     -> the per-condition allowlist.

    ``allowed_tools`` is the condition's repowise surface, UNPREFIXED
    (``["search_codebase", "get_context", "get_symbol"]``). It is what makes
    `C2_repowise_local` a different arm from `C2_repowise` rather than a
    relabelling of it: `get_answer` synthesizes through a FRONTIER model, so a
    row that reaches it is measuring gemini and not the local 8b. This function
    previously ignored the field entirely, which would have published a
    pure-local row with a frontier model in the loop and nothing in the output
    to show it. None = the full surface minus the verbose three.
    """
    provider_model = model.split("/", 1)[-1]  # "ollama/qwen3.5:4b-16k" -> "qwen3.5:4b-16k"
    config: dict = {
        "$schema": "https://opencode.ai/config.json",
        "snapshot": False,
        "model": f"ollama/{provider_model}",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (local)",
                "options": {"baseURL": OLLAMA_BASE_URL},
                "models": {provider_model: {"name": provider_model, "tools": True}},
            }
        },
        "permission": {
            "edit": "deny", "write": "deny", "bash": "deny", "webfetch": "deny",
            "read": "allow", "grep": "allow", "glob": "allow", "list": "allow",
        },
    }
    if system_prompt:
        config["agent"] = {
            BENCH_AGENT: {
                "description": "repowise-bench SWE-QA condition",
                "mode": "primary",
                "prompt": system_prompt,
            }
        }
    if repowise_enabled:
        # Deny-by-default over the whole served surface, then re-enable exactly
        # this condition's tools. Expressed on BOTH the `tools` map and the
        # `permission` map: `tools` is the documented enable/disable surface and
        # `permission` is what older builds honoured, and a tool that slips
        # through one is the silent kind of failure this row cannot survive.
        surface = (list(allowed_tools) if allowed_tools is not None
                   else [t for t in REPOWISE_SERVED_TOOLS if t not in _VERBOSE_TOOLS])
        tools_map: dict = {"repowise_*": False}
        for t in REPOWISE_SERVED_TOOLS:
            tools_map[f"repowise_{t}"] = t in surface
        config["tools"] = tools_map
        for t in REPOWISE_SERVED_TOOLS:
            if t not in surface:
                config["permission"][f"repowise_{t}"] = "deny"
        repo_abs = str(repo_path.resolve())
        # Full mode's get_answer (LLM synthesis) and semantic search (gemini
        # embedder) need a provider key at QUERY time — forward whatever is in
        # the parent env so the spawned MCP server can reach the LLM/embedder.
        mcp_env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        for _k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            if os.environ.get(_k):
                mcp_env[_k] = os.environ[_k]
        config["mcp"] = {
            "repowise": {
                "type": "local",
                "command": [_REPOWISE_EXE, "mcp", repo_abs, "--transport", "stdio"],
                "environment": mcp_env,
                "enabled": True,
                "timeout": 60000,
            }
        }
    return config


def write_opencode_config(repo_path: Path, config: dict) -> Path:
    """Write opencode.json into the repo cwd (untracked -> absent from C0 worktree)."""
    cfg_path = repo_path / "opencode.json"
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# System prompts — opencode tool names (server_tool, i.e. repowise_<tool>)
# ---------------------------------------------------------------------------

# C0 / no-repowise: plain read-only exploration.
_OPENCODE_PROMPT_BARE = """You are answering a question about the code repository in your current working directory.
Use the read, grep, glob, and list tools to explore the source. Only read files inside this repository.
Answer concisely and reference real file paths and function/class names. Do not invent files you have not opened."""

# C2 full mode: wiki docs + semantic search + get_answer are populated.
# Tuned for a small (4B/16k-ctx) model + repowise 0.15.2 tool shapes. The rules
# target observed qwen3.5:4b failure modes: printing a tool call as plain text,
# stopping after a tool call without writing an answer, dumping a generic tool
# summary, and inventing class/file names not present in the tool output.
_OPENCODE_PROMPT_FULL = """You answer a question about the code repository in your current directory using repowise tools (an accurate indexed wiki of THIS repo).

STEPS:
  1. Call repowise_get_answer with {"question": "<the exact question>"} — actually invoke the tool, do NOT type the call as text.
  2. (Optional, only if you still lack a specific class/function body) call repowise_get_symbol with {"symbol_id": "Name"} — a bare name like "Blueprint" works.
  Make at most TWO tool calls total.

THEN STOP CALLING TOOLS and write your FINAL ANSWER as plain prose.

HARD RULES:
  - You MUST end with a written answer. Never end your turn on a tool call. Never print a tool call (e.g. `repowise_get_symbol(...)`) as your answer.
  - Ground EVERY statement in the tool output. Do NOT invent class names, file paths, exceptions, or "test failures" that the tools did not return. If the tools don't cover something, say only what they did show.
  - Answer the SPECIFIC question asked — do not paste a generic repository overview.
  - Be concise (3-6 sentences). Cite real file paths / function names that appear in the tool results."""

# C1 index-only mode: no wiki docs, no semantic search, no get_answer. Only the
# graph + git tools return real data.
_OPENCODE_PROMPT_INDEX_ONLY = """You are answering a question about the code repository in your current working directory.
You have repowise codebase-intelligence tools backed by the code graph and git history.
Use them for structural/ownership context, then read the relevant source to confirm.

Available repowise tools (use ONLY these — no get_answer / search in this mode):
  - repowise_get_context with {"targets": ["path/a.py","path/b.py"]} — per file: symbols,
    imports, dependents. Batch files in ONE call. Your primary navigation tool.
  - repowise_get_risk with {"targets": ["path/to/file.py"]} — co-change partners, ownership,
    hotspot/churn. Use only for history/coupling/ownership questions.
  - repowise_get_why with {"query": "path/to/file.py"} — past significant commits.

WORKFLOW:
  1. grep/glob to find 1-3 candidate files.
  2. ONE batched repowise_get_context call on those candidates.
  3. read the relevant code.
  4. Answer concisely, referencing real file paths and function/class names."""


# C2_repowise_local: the ZERO-LLM repowise surface. No get_answer, so no
# frontier model enters the loop and the row can carry a "small local model plus
# repowise" claim. The steps deliberately mirror _OPENCODE_PROMPT_FULL's shape
# (same call budget, same hard rules, same length) so the two conditions differ
# by the TOOLS THEY NAME and not by how hard each is coached.
_OPENCODE_PROMPT_LOCAL = """You answer a question about the code repository in your current directory using repowise tools (an accurate indexed wiki of THIS repo).

STEPS:
  1. Call repowise_search_codebase with {"query": "<the exact question>"} — actually invoke the tool, do NOT type the call as text.
  2. (Optional, only if you still lack a specific class/function body) call repowise_get_symbol with {"symbol_id": "Name"} — a bare name like "Blueprint" works — or repowise_get_context with {"targets": ["path/a.py"]}.
  Make at most TWO tool calls total.

THEN STOP CALLING TOOLS and write your FINAL ANSWER as plain prose.

HARD RULES:
  - You MUST end with a written answer. Never end your turn on a tool call. Never print a tool call (e.g. `repowise_get_symbol(...)`) as your answer.
  - Ground EVERY statement in the tool output. Do NOT invent class names, file paths, exceptions, or "test failures" that the tools did not return. If the tools don't cover something, say only what they did show.
  - Answer the SPECIFIC question asked — do not paste a generic repository overview.
  - Be concise (3-6 sentences). Cite real file paths / function names that appear in the tool results."""


def build_opencode_system_prompt(condition: dict, benchmark: str = "swe_qa") -> str:
    """Pick the opencode system prompt for a condition.

    Mirrors swe_qa_runner's SWEQA_PROMPT_* but uses opencode's ``repowise_<tool>``
    naming. The small local model ignores the MCP tools without this nudge.

    THE PROMPT MUST MATCH THE ALLOWLIST. `C2_repowise_local` runs `repowise_mode:
    full` with `get_answer` denied, so the full-mode prompt would open by
    instructing the model to call a tool the condition blocks. The model would
    burn its two-call budget on a refusal and the row would read as "the
    zero-LLM surface does not help" when what was actually measured is a prompt
    pointed at a closed door.
    """
    if not condition.get("repowise_enabled"):
        return _OPENCODE_PROMPT_BARE
    mode = condition.get("repowise_mode", "full")
    if mode == "index-only":
        return _OPENCODE_PROMPT_INDEX_ONLY
    allowed = condition.get("allowed_tools")
    if allowed is not None and "get_answer" not in allowed:
        return _OPENCODE_PROMPT_LOCAL
    return _OPENCODE_PROMPT_FULL


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _normalize_message(m: dict) -> tuple[dict, list]:
    """A message may be {info, parts} or a flat object. Return (info, parts)."""
    if "info" in m or "parts" in m:
        return m.get("info", {}) or {}, m.get("parts", []) or []
    # Flat shape: the message IS the info, parts under "parts".
    return m, m.get("parts", []) or []


# opencode's own tools. Everything else in a tool part came from a mounted MCP
# server, which is how an MCP call is told apart from a builtin one here:
# opencode names MCP tools `<server>_<tool>` with no `mcp__` marker, so there is
# nothing structural to match on.
_OPENCODE_BUILTIN_TOOLS = frozenset({
    "read", "grep", "glob", "list", "write", "edit", "patch", "bash",
    "webfetch", "websearch", "task", "todowrite", "todoread", "question",
    "skill", "invalid",
})


def _mcp_split(name: str) -> tuple[str, str]:
    """(server, tool) for an opencode MCP tool name, or ("", "") if builtin."""
    if not name or name in _OPENCODE_BUILTIN_TOOLS or "_" not in name:
        return "", ""
    server, _, tool = name.partition("_")
    return server, tool


def parse_session_messages(messages: list) -> dict:
    """Aggregate ALL session messages into run_claude_code's output shape.

    Cost/tokens are summed across every step-finish (the per-message info.tokens
    is only that message's slice). Tool calls and files-read are collected across
    all assistant messages of the turn, not just the final one.

    THE MCP FIELDS ARE THE ADOPTION INSTRUMENT AND THEY ARE NOT OPTIONAL.
    `run_swe_qa_task` decides `arm_exercised` from `mcp_tools_issued` and
    `mcp_per_server`, which are the Claude-shaped names; this function used to
    emit only `repowise_tools_called`, so every opencode cell arrived with both
    fields empty and the integrity guard printed "arm NOT EXERCISED, the agent
    called none of them" on cells that had demonstrably called their tools. That
    is the workstream's own trap running backwards: a detector reporting a
    plausible ZERO for a live arm. Believed, it would have published an adoption
    collapse for the local harness on the strength of a naming mismatch.
    """
    if isinstance(messages, dict) and messages.get("_tag"):
        return {"error": json.dumps(messages)[:500], "result": ""}
    if isinstance(messages, dict):
        messages = [messages]

    cost = 0.0
    in_tok = out_tok = cache_read = cache_write = 0
    steps = 0
    tool_calls = 0
    files_explored: list[str] = []
    answer_parts: list[str] = []
    mcp_issued: list[str] = []
    mcp_ok: list[str] = []
    mcp_errors: list[str] = []
    server_tools: dict = {}
    per_server: dict = {}
    step_detail: list = []

    for m in messages:
        if not isinstance(m, dict):
            continue
        info, parts = _normalize_message(m)
        role = (info or {}).get("role")
        for p in parts:
            ptype = p.get("type")
            if ptype == "tool":
                tool_calls += 1
                name = p.get("tool", "")
                state = p.get("state", {}) or {}
                tinput = state.get("input", {}) or {}
                if name == "read":
                    fp = tinput.get("filePath") or tinput.get("path")
                    if fp:
                        files_explored.append(fp)
                server, _tool = _mcp_split(name)
                if server:
                    # ISSUED is every call the agent made; OK is only the ones
                    # the server answered. The gap between them is the whole
                    # point: a call that came back an error leaves the agent
                    # with exactly what a bare agent had.
                    mcp_issued.append(name)
                    server_tools.setdefault(server, []).append(name)
                    bucket = per_server.setdefault(server, {"ok": 0, "error": 0})
                    if state.get("status") == "completed":
                        mcp_ok.append(name)
                        bucket["ok"] += 1
                    else:
                        mcp_errors.append(name)
                        bucket["error"] += 1
            elif ptype == "text":
                # ONLY the assistant's text is the answer. The GET messages route
                # returns the user message too, whose text part is our prompt — if
                # the assistant ends on a tool call with no final text, collecting
                # user text here would mis-record the prompt as the answer.
                if role != "assistant":
                    continue
                txt = (p.get("text") or "").strip()
                if txt:
                    answer_parts.append(txt)
            elif ptype == "step-finish":
                steps += 1
                cost += float(p.get("cost", 0) or 0)
                tok = p.get("tokens", {}) or {}
                step_in = int(tok.get("input", 0) or 0)
                step_out = int(tok.get("output", 0) or 0)
                in_tok += step_in
                out_tok += step_out
                cache = tok.get("cache", {}) or {}
                cache_read += int(cache.get("read", 0) or 0)
                cache_write += int(cache.get("write", 0) or 0)
                # PER STEP, not just summed. On a local model the summed input
                # count is not a context-window reading (it double-counts the
                # prompt across turns) and it cannot separate PREFILL from
                # DECODE. Those are different costs on a GPU: prefill is
                # parallel, decode is serial, so "one big payload, fewer turns"
                # and "small payloads, more turns" are distinguishable here and
                # nowhere in the sums.
                step_detail.append({
                    "input": step_in,
                    "output": step_out,
                    "reasoning": int(tok.get("reasoning", 0) or 0),
                })

    return {
        "result": answer_parts[-1] if answer_parts else "",
        "num_turns": steps,
        "total_cost_usd": cost,
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
        "num_tool_calls": tool_calls,
        "files_explored": files_explored,
        "files_edited": [],
        # Same meaning as in metrics.py: MCP tools that returned SUCCESSFULLY,
        # for whichever server the arm mounted, not narrowed to repowise.
        "repowise_tools_called": mcp_ok,
        "mcp_tools_called": mcp_ok,
        "server_tools_called": server_tools,
        "mcp_tools_issued": sorted(set(mcp_issued)),
        "mcp_tool_errors": mcp_errors,
        "mcp_isError_count": len(mcp_errors),
        "mcp_per_server": per_server,
        # Per-step prefill/decode. `max_step_input` is the closest thing to a
        # real context-window reading: the summed input token count exceeds the
        # 16k window routinely just by counting the same prompt on every turn,
        # so only the per-step maximum can say whether a payload actually
        # crowded the window and got the tool definitions truncated.
        "token_steps": step_detail,
        "max_step_input": max((s["input"] for s in step_detail), default=0),
        "stop_reason": "stop",
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def parse_cli_events(stdout: str) -> dict:
    """Aggregate `opencode run --format json` NDJSON into the output shape.

    Each line is {type, timestamp, sessionID, part:{...}} and the `part` objects
    carry the same `type` values the REST route returned, so the events are
    folded into ONE pseudo-message and handed to `parse_session_messages`. One
    aggregator, not two: the token/tool/answer accounting is the instrument this
    whole run is read through, and a second copy of it is a second thing to
    drift.

    role is forced to "assistant" because the CLI streams only the assistant's
    own events; the prompt is never echoed back as a text part, which is the
    case the role filter exists to catch.
    """
    parts: list = []
    session_id = None
    unparsed = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            unparsed += 1
            continue
        session_id = session_id or ev.get("sessionID")
        part = ev.get("part")
        if isinstance(part, dict):
            parts.append(part)
    out = parse_session_messages([{"info": {"role": "assistant"}, "parts": parts}])
    out["session_id"] = session_id
    out["n_events"] = len(parts)
    if unparsed:
        out["unparsed_lines"] = unparsed
    return out


def _opencode_cmd(*, directory: str, model: str, prompt: str,
                  use_agent: bool, session_id: Optional[str] = None) -> list:
    cmd = [_OPENCODE_BIN, "run", "--format", "json",
           "--dir", directory, "--model", model]
    if use_agent:
        cmd += ["--agent", BENCH_AGENT]
    if session_id:
        cmd += ["--session", session_id]
    cmd.append(prompt)
    return cmd


def run_opencode(
    prompt: str,
    repo_path: str,
    condition: dict,
    model: str,
    timeout: int,
    server=None,
    benchmark: str = "swe_qa",
    system_prompt: Optional[str] = None,
    disable_thinking: bool = True,
    stream_log_path: Optional[str] = None,
) -> tuple[dict, int]:
    """Run one SWE-QA task through `opencode run`. Returns (output_dict, retries=0).

    Mirrors run_claude_code's return so run_swe_qa_task can dispatch to either
    harness. One process per cell, no shared server. Local models do not rate
    limit, so there is no retry/backoff.

    ``server`` is accepted and ignored; it is the removed REST singleton's
    parameter, kept so an older caller does not TypeError.

    disable_thinking: append qwen3 ``/no_think`` to suppress chain-of-thought
        latency. (qwen3.5 only partially honours it via the OpenAI-compat
        endpoint, but it still completes.)
    """
    repo = Path(repo_path)

    # C0 runs in a clean git worktree so prior .repowise/ + opencode.json are
    # physically absent; reuse the Claude arm's worktree helper for identical
    # isolation.
    if not condition.get("repowise_enabled"):
        from harness.swe_qa_runner import get_c0_worktree
        repo = get_c0_worktree(repo)

    config = build_opencode_config(
        model=model,
        repowise_enabled=bool(condition.get("repowise_enabled")),
        repo_path=repo,
        allowed_tools=condition.get("allowed_tools"),
        system_prompt=system_prompt,
    )
    write_opencode_config(repo, config)

    if disable_thinking:
        prompt = prompt + "\n\n/no_think"

    directory = str(repo.resolve())

    def _run(text: str, session_id: Optional[str] = None) -> tuple[str, str, int]:
        proc = subprocess.run(
            _opencode_cmd(directory=directory, model=model, prompt=text,
                          use_agent=bool(system_prompt), session_id=session_id),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=float(timeout), env=_UTF8_ENV, cwd=directory,
        )
        return proc.stdout or "", proc.stderr or "", proc.returncode

    try:
        stdout, stderr, rc = _run(prompt)
        if stream_log_path:
            Path(stream_log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(stream_log_path).write_text(stdout, encoding="utf-8")
        parsed = parse_cli_events(stdout)

        # A non-zero rc with no parsed answer is a harness failure, not a bad
        # answer, and must never be graded as one.
        if rc != 0 and not (parsed.get("result") or "").strip():
            return {"error": f"opencode rc={rc}: {(stderr or stdout)[:400]}"}, 0

        # Empty-answer recovery: small models often end a turn on a tool call
        # without writing a final answer (observed on both qwen3.5:4b and
        # qwen3:8b). That yields an unscored row and lost data. Re-prompt ONCE
        # in the SAME session — the tool results are already in context, so the
        # model just has to synthesize.
        if not (parsed.get("result") or "").strip() and parsed.get("session_id"):
            stdout2, _, _ = _run(
                "You did not provide a final answer. Based on the tool results "
                "already in this conversation, write your final answer now as "
                "plain prose. Do NOT call any tools. /no_think",
                session_id=parsed["session_id"],
            )
            if stream_log_path:
                with open(stream_log_path, "a", encoding="utf-8") as fh:
                    fh.write(stdout2)
            retry = parse_cli_events(stdout2)
            if (retry.get("result") or "").strip():
                # Keep the FIRST turn's tool/token accounting and add the
                # retry's, so a recovered cell is not recorded as a zero-tool
                # one. Adoption is the headline this run reports.
                retry["repowise_tools_called"] = (
                    parsed.get("repowise_tools_called", [])
                    + retry.get("repowise_tools_called", []))
                retry["num_tool_calls"] = (parsed.get("num_tool_calls", 0)
                                           + retry.get("num_tool_calls", 0))
                retry["files_explored"] = (parsed.get("files_explored", [])
                                           + retry.get("files_explored", []))
                for k in ("input_tokens", "output_tokens"):
                    retry["usage"][k] = (parsed["usage"].get(k, 0)
                                         + retry["usage"].get(k, 0))
                retry["answer_recovered_on_retry"] = True
                parsed = retry
    except subprocess.TimeoutExpired:
        return {"error": f"opencode timed out after {timeout}s", "timed_out": True}, 0
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:400]}"}, 0

    if parsed.get("error") and not parsed.get("result"):
        return {"error": parsed["error"]}, 0
    return parsed, 0
