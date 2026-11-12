"""Call `get_context(include=["references"])` for the 30 rename symbols and save the rows.

    python editset/run_editset.py --repo <repowise checkout at ddf30359d with its index> --out refs.jsonl
    python editset/score.py refs.jsonl

Keyless server environment, answer cache off, one process for all calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY_ENVS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_KEY",
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY")


def payload_of(res) -> dict | str:
    text = "".join(getattr(c, "text", "") or "" for c in res.content)
    p = res.structuredContent if isinstance(res.structuredContent, dict) else None
    if p is None:
        try:
            p = json.loads(text)
        except ValueError:
            return text
    if set(p) == {"result"} and isinstance(p["result"], dict):
        p = p["result"]
    return p


async def run(a) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    calls = json.loads((HERE / "truth" / "calls.json").read_text(encoding="utf-8"))
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
    env.update(DO_NOT_TRACK="1", PYTHONIOENCODING="utf-8", REPOWISE_ANSWER_DISABLE_CACHE="1",
               REPOWISE_CONFIG_DIR=tempfile.mkdtemp(prefix="es_"))
    params = StdioServerParameters(command=a.python, args=["-m", "repowise.cli.main", "mcp", a.repo, "--no-workspace"],
                                   env=env, cwd=a.repo)
    with open(a.out, "w", encoding="utf-8") as out, open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                for i, c in enumerate(calls):
                    t = time.perf_counter()
                    res = await s.call_tool(c["tool"], c["args"])
                    text = "".join(getattr(x, "text", "") or "" for x in res.content)
                    out.write(json.dumps({"i": i, "tag": c["tag"], "tool": c["tool"], "args": c["args"],
                                          "wall_s": round(time.perf_counter() - t, 3), "text_bytes": len(text.encode()),
                                          "payload": payload_of(res)}, ensure_ascii=False) + "\n")
                    out.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--python", default=sys.executable)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
