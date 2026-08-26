"""Measure what the repowise MCP SERVER actually returns over stdio.

Not an in-process import of the tool function: the arm under test talks to a
server subprocess, and the payload the agent pays for is the serialised tool
result, including whatever the transport wraps around it. Measuring the
function's dict instead would understate it by exactly the wrapping, which is
the kind of near-miss this workstream has already paid for twice.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TREE = Path(os.environ.get("BENCH_TREE") or r"C:\Users\ragha\Desktop\bakeoff\se-payload-rich")
# Overridable so a candidate build can be measured against the baseline this
# script produced for the shipped one. Hardcoding it meant every re-measure
# silently re-measured the OLD binary.
BIN = Path(
    os.environ.get("BENCH_BIN")
    or r"C:\Users\ragha\Desktop\repowise-sessioneval\.venv\Scripts\repowise.exe"
)

CALLS = [
    ("get_answer", {"question": "how does Text.from_ansi decode escape sequences?"}),
    ("get_context", {"targets": ["rich/ansi.py"]}),
    ("get_symbol", {"symbol_id": "rich/ansi.py::AnsiDecoder"}),
    ("get_why", {"query": "why is the highlighter regex greedy?"}),
    ("search_codebase", {"query": "progress bar expand width", "limit": 5}),
    ("get_risk", {"targets": ["rich/progress.py"]}),
]


async def main() -> None:
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1"})

    params = StdioServerParameters(
        command=str(BIN),
        args=["mcp", str(TREE), "--transport", "stdio"],
        env=env,
        cwd=str(TREE),
    )

    rows = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            served = sorted(t.name for t in tools.tools)
            print(f"served tools ({len(served)}): {served}\n", flush=True)

            for name, kwargs in CALLS:
                if name not in served:
                    rows.append({"tool": name, "chars": None, "error": "not served"})
                    print(json.dumps(rows[-1]), flush=True)
                    continue
                try:
                    res = await session.call_tool(name, kwargs)
                    text = "".join(c.text for c in res.content
                                   if getattr(c, "text", None))
                    rows.append({"tool": name, "chars": len(text),
                                 "isError": bool(res.isError)})
                except Exception as exc:  # noqa: BLE001
                    rows.append({"tool": name, "chars": None,
                                 "error": repr(exc)[:200]})
                print(json.dumps(rows[-1]), flush=True)

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mcp_payload.json")
    out.write_text(json.dumps({"served": served, "rows": rows}, indent=2),
                   encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
