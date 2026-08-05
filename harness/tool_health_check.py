"""Pre-flight health check for any MCP server used as a benchmark condition.

Silent server misconfiguration (wrong index, missing key, dead language
server) can invalidate a whole result set: the affected arm degrades to a
bare-agent run and fakes parity without a single error surfacing in the run.
This script speaks the MCP protocol to a server exactly the way a host would,
exercises every exposed tool with a benign query, and classifies each response
ok / empty / error, so a broken arm fails loudly BEFORE any tokens are spent.

Modes:
    python harness/tool_health_check.py <repo-path>
        legacy: spawn `repowise mcp <repo> --transport stdio` for that repo,
        using per-tool sample args tuned for pallets/flask
    python harness/tool_health_check.py --mcp-config <json> --server <name> [--cwd <dir>]
        spawn/connect the server exactly as the benchmark arm configures it.
        Supports stdio configs ({"command": ..., "args": [...], "env": {...}})
        and remote configs ({"type": "http", "url": ...}). Unknown tools get
        arguments synthesized from their input schema (--query fills string
        params); --sample-args <json> overrides per tool.

Exit code is non-zero when any tool errors, so runners can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Claude Code rejects MCP tool results over MAX_MCP_OUTPUT_TOKENS with an
# isError, which teaches the agent to abandon the server; a pre-flight must
# surface any tool that would trip it on a realistic query.
HOST_OUTPUT_CAP_TOKENS = 25_000

# A single stalled tool call (embedding endpoint, cold language server) must
# not hang the whole pre-flight.
CALL_TIMEOUT_S = float(os.environ.get("REPOWISE_BENCH_CALL_TIMEOUT", "90"))

DEFAULT_QUERY = "What does this repository do and what are its main components?"

# A pre-flight must never write: some servers (e.g. Serena) expose
# replace/rename/delete tools, and calling those with synthesized arguments
# mutates the target repo. Tools are skipped when their name looks mutating,
# unless the server explicitly annotates them read-only.
_MUTATING_NAME = re.compile(
    r"(^|_)(replace|insert|rename|delete|write|edit|create|remove|move|update|"
    r"apply|execute|run|set)(_|$)")


def is_mutating(tool) -> bool:
    ann = getattr(tool, "annotations", None)
    read_only = getattr(ann, "readOnlyHint", None) if ann else None
    if read_only is True:
        return False
    if read_only is False:
        return True
    return bool(_MUTATING_NAME.search(tool.name))

# Per-tool sample arguments tuned for the pallets/flask repo (legacy repowise mode).
SAMPLE_ARGS = {
    "get_overview": {},
    "get_answer": {"question": "What does the Blueprint class do and how are blueprints registered on a Flask app?"},
    "get_context": {"targets": ["src/flask/blueprints.py", "src/flask/app.py"]},
    "search_codebase": {"query": "blueprint registration and deferred functions"},
    "get_symbol": {"symbol_id": "Blueprint"},  # bare name (no path): tests repo-wide fallback
    "get_risk": {"targets": ["src/flask/app.py"]},
    "get_why": {"query": "src/flask/blueprints.py"},
    "get_dependency_path": {"source": "src/flask/app.py", "target": "src/flask/blueprints.py"},
    "get_dead_code": {},
    "get_health": {},
    "get_callers_callees": {"symbol_id": "src/flask/blueprints.py::Blueprint"},
    "get_community": {},
    "get_execution_flows": {},
    "get_graph_metrics": {},
    "get_architecture_diagram": {"level": "container"},
}


@dataclass
class ToolHealth:
    name: str
    status: str  # OK | EMPTY | ERROR | EXC | SKIP
    chars: int
    approx_tokens: int
    over_cap: bool
    snippet: str


def _sample_value(spec: dict, query: str):
    if "default" in spec:
        return spec["default"]
    if "enum" in spec and spec["enum"]:
        return spec["enum"][0]
    t = spec.get("type")
    if isinstance(t, list):
        t = t[0] if t else None
    if t in ("integer", "number"):
        return 1
    if t == "boolean":
        return False
    if t == "array":
        return [_sample_value(spec.get("items", {}) or {}, query)]
    if t == "object":
        return {}
    return query


def synthesize_args(schema: dict | None, query: str) -> dict:
    """Minimal argument set satisfying a tool's input schema.

    Only required params are filled: optional params keep server defaults, so
    the probe exercises the same call shape a lazy agent would make.
    """
    schema = schema or {}
    props = schema.get("properties", {}) or {}
    return {name: _sample_value(props.get(name, {}) or {}, query)
            for name in schema.get("required", []) or []}


def _classify(text: str) -> tuple[str, str]:
    low = text.lower()
    for marker in ('"error"', "not found", "no data", "unsupported", "not available",
                   "no such", "failed", "traceback", "invalid"):
        if marker in low[:400]:
            return "ERROR?", text[:200]
    if len(text.strip()) < 25:
        return "EMPTY", text[:200]
    return "OK", text[:160].replace("\n", " ")


async def check_session(session: ClientSession, sample_args: dict[str, dict],
                        query: str, synthesize: bool) -> tuple[list[ToolHealth], dict]:
    """Call every listed tool once and classify the responses."""
    listed = await session.list_tools()
    rows: list[ToolHealth] = []
    dump: dict[str, str] = {}
    for tool in listed.tools:
        if is_mutating(tool) and tool.name not in sample_args:
            rows.append(ToolHealth(tool.name, "SKIP", 0, 0, False,
                                   "(mutating tool, not probed)"))
            continue
        if tool.name in sample_args:
            # A null entry documents a deliberate exclusion (e.g. a tool that
            # errors on a benign probe for environmental reasons, like an
            # empty memory store) without weakening the exit-code gate.
            if sample_args[tool.name] is None:
                rows.append(ToolHealth(tool.name, "SKIP", 0, 0, False,
                                       "(excluded by sample args)"))
                continue
            args = sample_args[tool.name]
        elif synthesize:
            args = synthesize_args(getattr(tool, "inputSchema", None), query)
        else:
            rows.append(ToolHealth(tool.name, "SKIP", 0, 0, False, "(no sample args)"))
            continue
        try:
            res = await asyncio.wait_for(session.call_tool(tool.name, args),
                                         timeout=CALL_TIMEOUT_S)
            payload = "\n".join((getattr(c, "text", "") or "") for c in (res.content or []))
            dump[tool.name] = payload
            chars = len(payload)
            tok = chars // 4  # rough estimate, same basis as the host cap check
            over = tok > HOST_OUTPUT_CAP_TOKENS
            if getattr(res, "isError", False):
                rows.append(ToolHealth(tool.name, "ERROR", chars, tok, over, payload[:160]))
            else:
                status, snip = _classify(payload)
                rows.append(ToolHealth(tool.name, status, chars, tok, over, snip))
        except asyncio.TimeoutError:
            rows.append(ToolHealth(tool.name, "EXC", 0, 0, False,
                                   f"timed out after {CALL_TIMEOUT_S:.0f}s"))
        except Exception as e:  # noqa: BLE001 - any transport failure is a finding
            rows.append(ToolHealth(tool.name, "EXC", 0, 0, False, str(e)[:160]))
    return rows, dump


def load_server_config(config_path: Path, server: str | None) -> tuple[str, dict]:
    """Return (server_name, server_config) from a Claude-Code-style mcp config."""
    data = json.loads(config_path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers", data)
    if not isinstance(servers, dict) or not servers:
        raise SystemExit(f"no mcpServers found in {config_path}")
    if server is None:
        if len(servers) == 1:
            server = next(iter(servers))
        else:
            raise SystemExit(f"multiple servers in {config_path}; pass --server "
                             f"(one of {sorted(servers)})")
    if server not in servers:
        raise SystemExit(f"server '{server}' not in {config_path} "
                         f"(has {sorted(servers)})")
    return server, servers[server]


def stdio_params_from_config(cfg: dict, cwd: str | None) -> StdioServerParameters:
    command = cfg["command"]
    resolved = shutil.which(command) or command
    return StdioServerParameters(
        command=resolved,
        args=list(cfg.get("args", [])),
        # Config env layers on top of the caller's env, same as a real host.
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(cfg.get("env") or {})},
        cwd=cwd or cfg.get("cwd"),
    )


async def check_stdio(params: StdioServerParameters, sample_args: dict[str, dict],
                      query: str, synthesize: bool) -> tuple[list[ToolHealth], dict]:
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await check_session(session, sample_args, query, synthesize)


async def check_http(url: str, sample_args: dict[str, dict], query: str,
                     synthesize: bool) -> tuple[list[ToolHealth], dict]:
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await check_session(session, sample_args, query, synthesize)


def render(rows: list[ToolHealth], dump_path: Path | None, dump: dict) -> int:
    print(f"{'tool':34}{'status':8}{'chars':>9}{'~tok':>8}  snippet")
    print("-" * 110)
    for r in sorted(rows, key=lambda r: -r.approx_tokens):
        cap = " OVER-25K-CAP" if r.over_cap else ""
        print(f"{r.name:34}{r.status:8}{r.chars:>9}{r.approx_tokens:>8}"
              f"{cap}  {r.snippet[:60]}")
    bad = [r for r in rows if r.status in ("ERROR", "EXC")]
    over = [r for r in rows if r.over_cap]
    print(f"\n{len(rows)} tools called | {len(bad)} errored | "
          f"{len(over)} over the {HOST_OUTPUT_CAP_TOKENS} token host cap"
          f"{': ' + str([r.name for r in over]) if over else ''}")
    if dump_path is not None:
        dump_path.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
        print(f"full outputs -> {dump_path}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?", help="repo path (legacy repowise mode)")
    ap.add_argument("--mcp-config", type=Path,
                    help="Claude-Code-style mcp config JSON for the arm under test")
    ap.add_argument("--server", help="server name inside the config (default: sole entry)")
    ap.add_argument("--cwd", help="working directory for a stdio server")
    ap.add_argument("--query", default=DEFAULT_QUERY,
                    help="benign query used to fill string params")
    ap.add_argument("--sample-args", type=Path,
                    help="JSON file of per-tool argument overrides")
    ap.add_argument("--dump", type=Path, default=None,
                    help="where to write full tool outputs (default: tool_health_dump.json)")
    args = ap.parse_args()

    overrides = {}
    if args.sample_args:
        overrides = json.loads(args.sample_args.read_text(encoding="utf-8"))

    if args.mcp_config:
        name, cfg = load_server_config(args.mcp_config, args.server)
        print(f"pre-flight: server '{name}' from {args.mcp_config}")
        if cfg.get("type") in ("http", "sse") or "url" in cfg:
            rows, dump = asyncio.run(check_http(cfg["url"], overrides, args.query, True))
        else:
            params = stdio_params_from_config(cfg, args.cwd)
            rows, dump = asyncio.run(check_stdio(params, overrides, args.query, True))
    elif args.repo:
        repo = str(Path(args.repo).resolve())
        exe = os.environ.get("REPOWISE_EXE") or shutil.which("repowise") or "repowise"
        # cwd MUST be the repo: repowise resolves its index by walking up from
        # cwd, so launching from elsewhere serves the wrong repo's .repowise.
        params = StdioServerParameters(
            command=exe, args=["mcp", repo, "--transport", "stdio"],
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=repo,
        )
        rows, dump = asyncio.run(check_stdio(params, {**SAMPLE_ARGS, **overrides},
                                             args.query, False))
    else:
        ap.error("pass a repo path or --mcp-config")
        return 2

    dump_path = args.dump or Path(__file__).resolve().parents[1] / "tool_health_dump.json"
    return render(rows, dump_path, dump)


if __name__ == "__main__":
    sys.exit(main())
