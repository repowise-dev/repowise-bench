"""Exercise every repowise MCP tool against an indexed repo and report health.

Speaks the MCP stdio protocol to ``repowise.exe mcp <repo> --transport stdio``
via the official ``mcp`` client, lists the tools the server actually exposes,
then calls each with flask-appropriate args and classifies the result as
ok / empty / error. Run with the SAME python whose venv has ``repowise==0.15.2``
installed (it ships the ``mcp`` client lib).

Usage:
    .venv-rw0152/Scripts/python.exe harness/tool_health_check.py <abs-repo-path>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _exe() -> str:
    env = os.environ.get("REPOWISE_EXE")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve().parents[1]
    cand = here / ".venv-rw0152" / "Scripts" / "repowise.exe"
    return str(cand) if cand.exists() else "repowise"


# Per-tool sample arguments tuned for the pallets/flask repo.
SAMPLE_ARGS = {
    "get_overview": {},
    "get_answer": {"question": "What does the Blueprint class do and how are blueprints registered on a Flask app?"},
    "get_context": {"targets": ["src/flask/blueprints.py", "src/flask/app.py"]},
    "search_codebase": {"query": "blueprint registration and deferred functions"},
    "get_symbol": {"symbol_id": "Blueprint"},  # bare name (no path) — tests repo-wide fallback
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


def _classify(payload) -> tuple[str, str]:
    """Return (status, snippet)."""
    if payload is None:
        return "EMPTY", ""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    low = text.lower()
    # heuristics for an error/empty envelope
    for marker in ('"error"', "not found", "no data", "unsupported", "not available",
                   "no such", "failed", "traceback"):
        if marker in low[:400]:
            return "ERROR?", text[:200]
    if len(text.strip()) < 25:
        return "EMPTY", text[:200]
    return "OK", text[:160].replace("\n", " ")


async def main(repo: str) -> int:
    # cwd MUST be the repo: repowise resolves its index by walking up from cwd,
    # so launching from elsewhere serves the wrong repo's .repowise. (opencode
    # sets directory=repo for the same reason.)
    params = StdioServerParameters(
        command=_exe(),
        args=["mcp", repo, "--transport", "stdio"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        cwd=repo,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [t.name for t in listed.tools]
            print(f"server exposes {len(names)} tools: {sorted(names)}\n")

            rows = []
            dump = {}
            for name in names:
                args = SAMPLE_ARGS.get(name)
                if args is None:
                    rows.append((name, "SKIP", 0, 0, "(no sample args)"))
                    continue
                try:
                    res = await session.call_tool(name, args)
                    parts = []
                    for c in (res.content or []):
                        parts.append(getattr(c, "text", "") or "")
                    payload = "\n".join(parts)
                    dump[name] = payload
                    chars = len(payload)
                    approx_tok = chars // 4  # rough GPT-ish estimate
                    if getattr(res, "isError", False):
                        rows.append((name, "ERROR", chars, approx_tok, payload[:160]))
                    else:
                        status, snip = _classify(payload)
                        rows.append((name, status, chars, approx_tok, snip))
                except Exception as e:  # noqa: BLE001
                    rows.append((name, "EXC", 0, 0, str(e)[:160]))

            CTX = 16384  # qwen3.5:4b-16k window
            print(f"{'tool':26}{'status':8}{'chars':>8}{'~tok':>8}{'%ctx':>6}  snippet")
            print("-" * 110)
            for name, status, chars, tok, snip in sorted(rows, key=lambda r: -r[3]):
                pct = f"{100*tok/CTX:.0f}%" if tok else "-"
                flag = " ⚠" if tok > CTX * 0.4 else ""
                print(f"{name:26}{status:8}{chars:>8}{tok:>8}{pct:>6}{flag}  {snip[:60].replace(chr(10),' ')}")

            total_tok = sum(r[3] for r in rows)
            print(f"\nIf an agent called ALL tools once: ~{total_tok} tok "
                  f"(= {total_tok/CTX:.1f}x the 16k window)")
            bad = [r for r in rows if r[1] in ("ERROR", "EXC")]
            verbose = [r for r in rows if r[3] > CTX * 0.4]
            print(f"{len(rows)} tools called | {len(bad)} errored | "
                  f"{len(verbose)} exceed 40% of context: {[r[0] for r in verbose]}")

            # full outputs for manual inspection
            out = Path(__file__).resolve().parents[1] / "tool_health_dump.json"
            out.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
            print(f"full outputs -> {out}")
            return 1 if bad else 0


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parents[1] / "repos" / "pallets" / "flask"
    )
    sys.exit(asyncio.run(main(str(Path(repo).resolve()))))
