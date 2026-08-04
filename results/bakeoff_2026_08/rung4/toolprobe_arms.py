"""Rung 4 tool-surface enumeration, all arms.

Generalizes rung 3's `toolprobe_surfaces.py` from one server to N. Rung 3
caveat 4: pinning our own served surface makes our arm deterministic, not our
competitors'. Every arm needs the same enumeration before its numbers count,
because a server that advertises a different surface than the client allowlists
(D1c) or that varies with the environment (D1b) silently changes what is being
measured.

Records, per arm: whether the server starts at all, the exact tool list, the
server `instructions` payload delivered at initialize (CodeGraph delivers its
agent coaching there rather than in a config file, which is itself a
comparability fact worth capturing), and any startup failure verbatim.

No agent, no LLM, no spend.

Usage:
    python toolprobe_arms.py [--arms a,b] [--repo django] [--out surfaces.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TREES = Path(r"C:\Users\ragha\Desktop\bakeoff")
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"
OUT = Path(__file__).resolve().parent

# On Windows the npm/uv shims are .cmd wrappers; stdio_client execs directly, so
# name the wrapper explicitly rather than relying on PATHEXT resolution.
NPM_BIN = Path(os.environ["APPDATA"]) / "npm"
UV_BIN = Path.home() / ".local" / "bin"


def arm_specs(repo: str) -> dict[str, dict]:
    tree = str(TREES / repo)
    return {
        # ours, pinned exactly as the harness serves it (rung 3 D1b fix)
        "repowise-full": {
            "command": str(REPOWISE_EXE),
            "args": ["mcp", tree, "--transport", "stdio"],
        },
        "codegraph": {
            "command": str(NPM_BIN / "codegraph.cmd"),
            "args": ["serve", "--mcp", "--path", tree, "--no-watch"],
        },
        "code-review-graph": {
            "command": str(UV_BIN / "code-review-graph.exe"),
            "args": ["serve", "--repo", tree],
        },
        "graphify": {
            "command": str(UV_BIN / "graphify-mcp.exe"),
            "args": [
                "--transport",
                "stdio",
                "--graph",
                str(TREES / repo / "graphify-out" / "graph.json"),
            ],
        },
        "serena": {
            "command": str(UV_BIN / "serena.exe"),
            "args": [
                "start-mcp-server",
                "--project",
                tree,
                "--transport",
                "stdio",
                "--enable-web-dashboard",
                "false",
                "--enable-gui-log-window",
                "false",
            ],
        },
    }


async def probe(label: str, spec: dict, timeout: float = 180.0) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["DO_NOT_TRACK"] = "1"
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"

    row: dict = {"arm": label, "command": spec["command"], "args": spec["args"]}
    sp = StdioServerParameters(command=spec["command"], args=spec["args"], env=env)
    try:
        async with asyncio.timeout(timeout):
            async with stdio_client(sp) as (r, w):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    listed = await s.list_tools()
                    tools = sorted(t.name for t in listed.tools)
                    row.update(
                        {
                            "status": "ok",
                            "server_name": getattr(init.serverInfo, "name", None),
                            "server_version": getattr(
                                init.serverInfo, "version", None
                            ),
                            "instructions_chars": len(init.instructions or ""),
                            "instructions": init.instructions or "",
                            "n_tools": len(tools),
                            "tools": tools,
                            "tool_schema_chars": sum(
                                len(json.dumps(t.inputSchema or {}))
                                + len(t.description or "")
                                for t in listed.tools
                            ),
                        }
                    )
    except TimeoutError:
        row.update({"status": "FAIL", "error": f"timeout after {timeout}s"})
    except Exception as e:  # noqa: BLE001 - a failed arm is the result
        row.update({"status": "FAIL", "error": f"{type(e).__name__}: {e}"})

    print(
        f"{label:20s} {row['status']:5s} "
        f"{row.get('n_tools', '-')} tools, "
        f"{row.get('instructions_chars', '-')} instr chars"
        + (f"  {row.get('error', '')}" if row["status"] != "ok" else ""),
        flush=True,
    )
    return row


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="cli")
    ap.add_argument("--arms", default="")
    ap.add_argument("--out", default=str(OUT / "tool_surfaces.json"))
    a = ap.parse_args()

    specs = arm_specs(a.repo)
    wanted = [x for x in a.arms.split(",") if x] or list(specs)

    rows = []
    out_path = Path(a.out)
    if out_path.exists():
        rows = json.loads(out_path.read_text(encoding="utf-8"))

    for arm in wanted:
        row = await probe(arm, specs[arm])
        row["repo"] = a.repo
        rows = [r for r in rows if not (r["arm"] == arm and r.get("repo") == a.repo)]
        rows.append(row)
        out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
