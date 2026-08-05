"""Rung 4: DeepWiki hosted arm probe.

DeepWiki has no install and no local index: it is a hosted MCP endpoint over
public GitHub repos only. Its smoke test is therefore a different question from
every other arm's. Not "can it index this repo" but "has someone already indexed
this repo on their side, and does it answer at the pinned commit".

That second half is the finding that matters for the bake-off: a hosted wiki
serves whatever it last crawled, so it cannot be pinned to a base_commit the way
every other arm is. If so, DeepWiki structurally cannot join a leak-free Layer A
run, and saying that with evidence is worth more than a score.

Only public OSS repo names are sent. No source, no private data.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ENDPOINT = "https://mcp.deepwiki.com/mcp"
OUT = Path(__file__).resolve().parent / "deepwiki_probe.json"

REPOS = ["django/django", "sveltejs/svelte", "cli/cli"]


async def main() -> int:
    result: dict = {"endpoint": ENDPOINT}
    try:
        async with asyncio.timeout(180):
            async with streamablehttp_client(ENDPOINT) as (r, w, _):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    listed = await s.list_tools()
                    result["status"] = "ok"
                    result["server_name"] = getattr(init.serverInfo, "name", None)
                    result["server_version"] = getattr(
                        init.serverInfo, "version", None
                    )
                    result["instructions"] = init.instructions or ""
                    result["tools"] = [
                        {"name": t.name, "description": (t.description or "")[:400]}
                        for t in listed.tools
                    ]
                    print(
                        f"connected: {len(listed.tools)} tools -> "
                        f"{[t.name for t in listed.tools]}",
                        flush=True,
                    )

                    # structure probe only: does it hold this repo at all
                    result["repos"] = {}
                    for repo in REPOS:
                        try:
                            async with asyncio.timeout(120):
                                res = await s.call_tool(
                                    "read_wiki_structure", {"repoName": repo}
                                )
                            text = "\n".join(
                                getattr(c, "text", "") for c in res.content
                            )
                            result["repos"][repo] = {
                                "isError": bool(res.isError),
                                "chars": len(text),
                                "excerpt": text[:1500],
                            }
                            print(
                                f"  {repo}: isError={res.isError} {len(text)} chars",
                                flush=True,
                            )
                        except Exception as e:  # noqa: BLE001
                            result["repos"][repo] = {
                                "error": f"{type(e).__name__}: {e}"
                            }
                            print(f"  {repo}: FAILED {e}", flush=True)
    except Exception as e:  # noqa: BLE001 - a failed arm is the result
        result["status"] = "FAIL"
        result["error"] = f"{type(e).__name__}: {e}"
        print(f"FAILED {type(e).__name__}: {e}", flush=True)

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
