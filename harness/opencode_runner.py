"""opencode harness adapter — run a SWE-QA task through opencode + a local Ollama model.

Local-model counterpart to ``run_claude_code`` in ``swe_qa_runner.py``. Same
conditions (C0 = no repowise, C2 = repowise MCP), but an opencode agent driving
an Ollama-served model instead of Claude Code driving Anthropic.

TRANSPORT — why the HTTP server, not the CLI:
  ``opencode run --format json`` emits ZERO bytes to stdout on this Windows
  build (verified streaming for 300s; default/human format works fine). It's an
  opencode bug, not a config issue. So we drive opencode via its REST server
  instead, which is the intended programmatic interface anyway:

    1. ``opencode serve --port N``           (one persistent server per benchmark)
    2. POST /session?directory=<repo>        -> {id}
    3. POST /session/{id}/message            -> assistant SessionMessage (BLOCKS
       body: {model:{providerID,modelID},      until complete, returns full
              parts:[{type:text,text}],         message with parts)
              tools?, system?}
    NOTE: the /api/session/.../prompt "v2" endpoint returns
    "V2 session prompt is not available yet" in 1.15.x — use the v1
    /session/{id}/message route (operationId session.prompt).

SessionMessage shape we parse (from the OpenAPI spec + live probe):
  info.cost           USD (0 for local Ollama)
  info.tokens         {input, output, reasoning, cache:{read,write}}
  parts[]             step-start | reasoning | text | step-finish | tool
    - tool part:  {type:"tool", tool:"read"|"repowise_get_context"|..., state:{status,input}}
    - text part:  {type:"text", text:"..."}

MODEL PRECONDITIONS (learned the hard way — see harness/check_tool_calling.py):
  - The Ollama model MUST emit *structured* tool_calls. qwen2.5-coder:7b emits
    them as plain text and is unusable; qwen3/qwen3.5 work; llama3.2:3b is flaky.
  - Ollama's default 4096 context truncates the tool definitions -> create a
    larger-context variant (e.g. ``qwen3.5:4b-16k`` via a Modelfile num_ctx 16384).
  - qwen3-family thinking balloons latency; append ``/no_think`` to the prompt.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_BENCH_ROOT = Path(__file__).resolve().parents[1]

from harness.swe_qa_runner import _UTF8_ENV  # noqa: E402

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# On Windows the npm-global `opencode` is a .CMD shim; subprocess.Popen needs the
# resolved path (bash finds it via PATH, Python does not).
_OPENCODE_BIN = shutil.which("opencode") or "opencode"


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

def build_opencode_config(*, model: str, repowise_enabled: bool, repo_path: Path) -> dict:
    """opencode.json for one condition.

    provider  -> local Ollama OpenAI-compatible endpoint.
    permission-> read-only (deny edit/write/bash) so SWE-QA cannot escape or
                 mutate the repo, mirroring the Claude arm's tool restriction.
    snapshot  -> false; opencode's git-snapshot crawl hangs on large repos and is
                 useless for read-only QA.
    mcp       -> repowise server, present only for repowise conditions.
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
            # Deny the large, low-QA-value repowise tools so a weak model can't
            # waste its small context dumping their output instead of answering.
            "repowise_get_overview": "deny",
            "repowise_get_health": "deny",
            "repowise_get_dead_code": "deny",
        },
    }
    if repowise_enabled:
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
# opencode server lifecycle
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class OpencodeServer:
    """Start `opencode serve` once and reuse it across all tasks.

    Spawning a fresh `opencode run` per task is both slow and the route through
    the broken json CLI; one long-lived server is faster and gives structured
    responses. Use as a context manager.
    """

    def __init__(self, port: Optional[int] = None, ready_timeout: float = 30.0):
        self.port = port or _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.ready_timeout = ready_timeout
        self._proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> "OpencodeServer":
        self._proc = subprocess.Popen(
            [_OPENCODE_BIN, "serve", "--port", str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=_UTF8_ENV,
        )
        deadline = time.time() + self.ready_timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base}/doc", timeout=2):
                    return self
            except Exception:
                time.sleep(0.5)
        self.__exit__(None, None, None)
        raise RuntimeError(f"opencode server did not become ready on :{self.port}")

    def __exit__(self, *exc) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    # -- HTTP helpers --
    def _post(self, path: str, body: dict, timeout: float) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def create_session(self, directory: str, timeout: float = 30.0) -> str:
        from urllib.parse import quote
        d = quote(directory, safe="")
        resp = self._post(f"/session?directory={d}", {}, timeout)
        sid = resp.get("id")
        if not sid:
            raise RuntimeError(f"session create failed: {resp}")
        return sid

    def prompt(self, session_id: str, directory: str, model: str, text: str,
               system: Optional[str] = None, timeout: float = 600.0) -> dict:
        """POST /session/{id}/message (v1 session.prompt). Blocks until complete."""
        from urllib.parse import quote
        d = quote(directory, safe="")
        provider_id, _, model_id = model.partition("/")
        body: dict = {
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": text}],
        }
        if system:
            body["system"] = system
        return self._post(f"/session/{session_id}/message?directory={d}", body, timeout)

    def get_messages(self, session_id: str, directory: str, timeout: float = 30.0) -> list:
        """GET /session/{id}/message — all messages with their parts.

        The prompt response is only the FINAL assistant message; tool-call parts
        from earlier steps of the same turn live in prior messages. Aggregating
        across all messages is the only way to count tool calls / files read.
        """
        from urllib.parse import quote
        d = quote(directory, safe="")
        req = urllib.request.Request(
            f"{self.base}/session/{session_id}/message?directory={d}", method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        # Response may be a bare list or {messages:[...]} depending on route version.
        if isinstance(data, dict):
            return data.get("messages") or data.get("data") or []
        return data


# ---------------------------------------------------------------------------
# Shared server singleton (one `opencode serve` for the whole experiment)
# ---------------------------------------------------------------------------

_SERVER_LOCK = threading.Lock()
_SHARED_SERVER: Optional["OpencodeServer"] = None


def get_shared_server() -> "OpencodeServer":
    """Lazily start one OpencodeServer and reuse it across all tasks.

    A local GPU serves one inference at a time, so a single long-lived server is
    both sufficient and cheapest (spawning per task is slow and routes through the
    broken json CLI). Torn down at interpreter exit.
    """
    global _SHARED_SERVER
    with _SERVER_LOCK:
        if _SHARED_SERVER is None:
            _SHARED_SERVER = OpencodeServer().__enter__()
            atexit.register(lambda: _SHARED_SERVER.__exit__(None, None, None))
        return _SHARED_SERVER


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


def build_opencode_system_prompt(condition: dict, benchmark: str = "swe_qa") -> str:
    """Pick the opencode system prompt for a condition.

    Mirrors swe_qa_runner's SWEQA_PROMPT_* but uses opencode's ``repowise_<tool>``
    naming. The small local model ignores the MCP tools without this nudge.
    """
    if not condition.get("repowise_enabled"):
        return _OPENCODE_PROMPT_BARE
    mode = condition.get("repowise_mode", "full")
    return _OPENCODE_PROMPT_INDEX_ONLY if mode == "index-only" else _OPENCODE_PROMPT_FULL


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _normalize_message(m: dict) -> tuple[dict, list]:
    """A message may be {info, parts} or a flat object. Return (info, parts)."""
    if "info" in m or "parts" in m:
        return m.get("info", {}) or {}, m.get("parts", []) or []
    # Flat shape: the message IS the info, parts under "parts".
    return m, m.get("parts", []) or []


def parse_session_messages(messages: list) -> dict:
    """Aggregate ALL session messages into run_claude_code's output shape.

    Cost/tokens are summed across every step-finish (the per-message info.tokens
    is only that message's slice). Tool calls and files-read are collected across
    all assistant messages of the turn, not just the final one.
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
    repowise_tools: list[str] = []
    answer_parts: list[str] = []

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
                tinput = (p.get("state", {}) or {}).get("input", {}) or {}
                if name == "read":
                    fp = tinput.get("filePath") or tinput.get("path")
                    if fp:
                        files_explored.append(fp)
                if "repowise" in name:
                    repowise_tools.append(name)
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
                in_tok += int(tok.get("input", 0) or 0)
                out_tok += int(tok.get("output", 0) or 0)
                cache = tok.get("cache", {}) or {}
                cache_read += int(cache.get("read", 0) or 0)
                cache_write += int(cache.get("write", 0) or 0)

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
        "repowise_tools_called": repowise_tools,
        "stop_reason": "stop",
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_opencode(
    prompt: str,
    repo_path: str,
    condition: dict,
    model: str,
    timeout: int,
    server: OpencodeServer,
    benchmark: str = "swe_qa",
    system_prompt: Optional[str] = None,
    disable_thinking: bool = True,
) -> tuple[dict, int]:
    """Run one SWE-QA task through opencode's server. Returns (output_dict, retries=0).

    Mirrors run_claude_code's return so run_swe_qa_task can dispatch to either
    harness. ``server`` is a live OpencodeServer (started once for the whole run).
    Local models don't rate-limit, so there is no retry/backoff.

    disable_thinking: append qwen3 ``/no_think`` to suppress chain-of-thought
        latency. (qwen3.5 only partially honours it via the OpenAI-compat
        endpoint, but it still completes.)
    """
    repo = Path(repo_path)

    # C0 runs in a clean git worktree so prior .repowise/ + opencode.json are
    # physically absent; reuse the Claude arm's worktree helper for identical
    # isolation. NOTE: worktrees live under the bench's scratch dir; if opencode
    # bootstrap proves sensitive to monorepo nesting, point this at an external
    # work root instead.
    if not condition.get("repowise_enabled"):
        from harness.swe_qa_runner import get_c0_worktree
        repo = get_c0_worktree(repo)

    config = build_opencode_config(
        model=model,
        repowise_enabled=bool(condition.get("repowise_enabled")),
        repo_path=repo,
    )
    write_opencode_config(repo, config)

    if disable_thinking:
        prompt = prompt + "\n\n/no_think"

    directory = str(repo.resolve())
    try:
        session_id = server.create_session(directory)
        final = server.prompt(
            session_id, directory, model, prompt,
            system=system_prompt, timeout=float(timeout),
        )
        if isinstance(final, dict) and final.get("_tag"):
            return {"error": json.dumps(final)[:500]}, 0
        # Tool-call parts live across the turn's messages, not just the final
        # assistant message — aggregate them all.
        messages = server.get_messages(session_id, directory)
        if not messages:
            messages = [final]

        parsed = parse_session_messages(messages)

        # Empty-answer recovery: small models often end a turn on a tool call
        # without writing a final answer (observed on both qwen3.5:4b and
        # qwen3:8b). That yields an unscored row and lost data. Re-prompt ONCE
        # in the SAME session — the tool results are already in context, so the
        # model just has to synthesize. Cheap and materially cuts empties.
        if not (parsed.get("result") or "").strip() and not parsed.get("error"):
            server.prompt(
                session_id, directory, model,
                "You did not provide a final answer. Based on the tool results "
                "already in this conversation, write your final answer now as "
                "plain prose. Do NOT call any tools. /no_think",
                timeout=float(timeout),
            )
            messages = server.get_messages(session_id, directory) or messages
            parsed = parse_session_messages(messages)
    except urllib.error.URLError as e:
        return {"error": f"opencode server error: {e}", "timed_out": "timed out" in str(e).lower()}, 0
    except Exception as e:
        return {"error": str(e)[:500]}, 0

    if parsed.get("error") and not parsed.get("result"):
        return {"error": parsed["error"]}, 0
    return parsed, 0
