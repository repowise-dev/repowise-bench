"""Rung 5 follow-up: does an LLM provider change multi-hop RETRIEVAL?

Rung 5's main run had no provider, so `get_answer` logged "running WITHOUT
synthesis". Multi-hop scored MRR 0.311 / recall@10 0.440 against 0.802 / 0.943
on single-file questions. That gap is either a real one-hop retrieval limit or
an artifact of disabling the synthesis path.

This is the paired A/B that separates them. Same questions, same index, same
server binary. The ONLY difference is whether an API key is visible to the
server process.

Scored identically to the main run, so the numbers are directly comparable.
Paired by question id, so the delta is per-question and a Wilcoxon-style sign
count is meaningful even at n=23.

Deliberately excludes the 2 questions tagged `known_wrong`: those assert
post-fix behavior and are expected to miss at baseline, so including them would
blunt the very delta this test exists to detect.

Spend: 23 get_answer calls with synthesis on. Order of cents. Recorded exactly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUT = Path(__file__).resolve().parent
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"
TREE = Path(r"C:\Users\ragha\Desktop\bakeoff\r5-repowise")
EVALS = REPOWISE_ROOT / "local-stash/agent-context/bench/eval/repowise_retrieval_v2.yaml"

sys.path.insert(0, str(OUT))
from retrieval_probe_multiarm import paths_from_repowise, score  # noqa: E402


def load_key() -> str:
    cfg = json.loads((REPOWISE_ROOT / "provider_config.json").read_text())
    return cfg["keys"]["openai"]


async def run(with_provider: bool, questions: list[dict]) -> list[dict]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "DO_NOT_TRACK": "1",
            "REPOWISE_SKIP_EDITOR_SETUP": "1",
        }
    )
    if with_provider:
        env["OPENAI_API_KEY"] = load_key()
        env["REPOWISE_PROVIDER"] = "openai"
    else:
        # make sure nothing ambient leaks in and contaminates the control arm
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "REPOWISE_PROVIDER"):
            env.pop(k, None)

    sp = StdioServerParameters(
        command=str(EXE),
        args=["mcp", str(TREE), "--transport", "stdio"],
        env=env,
    )
    rows = []
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for q in questions:
                text_q = q.get("question") or q.get("query") or ""
                tool = "get_answer" if q["tool"] == "get_answer" else "search_codebase"
                args = (
                    {"question": text_q}
                    if tool == "get_answer"
                    else {"query": text_q, "limit": 10}
                )
                row = {
                    "id": q["id"],
                    "with_provider": with_provider,
                    "tool": tool,
                    "gold": q["gold"],
                }
                try:
                    async with asyncio.timeout(300):
                        res = await s.call_tool(tool, args)
                    text = "\n".join(getattr(c, "text", "") or "" for c in res.content)
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError):
                        payload = {}
                    ranked = paths_from_repowise(payload, text)
                    row.update(
                        {
                            "status": "ok",
                            "chars": len(text),
                            "ranked": ranked[:25],
                            **score(ranked, q["gold"]),
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    row.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
                rows.append(row)
                print(
                    f"  [{'ON ' if with_provider else 'OFF'}] {q['id']:34s} "
                    f"{row.get('status')} hit@{row.get('hit_rank')}",
                    flush=True,
                )
    return rows


def agg(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("status") == "ok"]
    n = len(ok) or 1
    return {
        "n": len(ok),
        "MRR": round(sum(r["rr"] for r in ok) / n, 4),
        **{
            f"recall@{k}": round(sum(r[f"recall@{k}"] for r in ok) / n, 4)
            for k in (1, 3, 5, 10)
        },
    }


async def main() -> int:
    suite = yaml.safe_load(EVALS.read_text(encoding="utf-8"))
    qs = [
        e
        for e in suite["evals"]
        if e.get("gold")
        and e.get("category") == "multi-hop"
        and "known_wrong" not in (e.get("tags") or [])
        and any((TREE / g).exists() for g in e["gold"])
    ]
    print(f"{len(qs)} multi-hop questions (known_wrong excluded)\n")

    print("control: provider OFF (reproduces the main rung 5 run)")
    off = await run(False, qs)
    print("\ntreatment: provider ON")
    on = await run(True, qs)

    off_by = {r["id"]: r for r in off}
    paired = [
        (r["id"], off_by[r["id"]].get("hit_rank"), r.get("hit_rank"))
        for r in on
        if r["id"] in off_by
    ]
    better = sum(
        1
        for _, o, n in paired
        if (n is not None) and (o is None or n < o)
    )
    worse = sum(
        1
        for _, o, n in paired
        if (o is not None) and (n is None or n > o)
    )
    same = len(paired) - better - worse

    result = {
        "provider_off": agg(off),
        "provider_on": agg(on),
        "paired": {"better": better, "worse": worse, "same": same, "n": len(paired)},
        "rows": {"off": off, "on": on},
    }
    (OUT / "multihop_synthesis_ab.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print("\n=== multi-hop, provider OFF vs ON ===")
    print("OFF:", json.dumps(result["provider_off"]))
    print("ON :", json.dumps(result["provider_on"]))
    print(f"paired: better={better} worse={worse} same={same} of {len(paired)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
