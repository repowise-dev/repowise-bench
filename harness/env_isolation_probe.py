"""Prove a benchmark cell runs in the environment the benchmark thinks it does.

`--strict-mcp-config` isolates MCP servers and nothing else. Hooks, plugins,
skills, the global effort level and CLAUDE.md discovery all come from the
operator's own `~/.claude/settings.json` and all of them fire inside a cell.

Measured on this machine 2026-08-03, running the harness's exact C0-bare flag
set: **7 hooks fired**, and one of them injected

    [repowise] Index is behind HEAD: indexed fe102e41, now c6a5640c ...
    The MCP tools (get_answer, get_context, get_symbol, search_codebase,
    get_risk) stay reliable ...

into the context of the arm whose definition is "no repowise". That is finding
D1's shape exactly (a dead server scoring as a bad arm) pointed the other way:
a contaminated control scoring as a weak lift. It cannot be caught by reading
the harness, because the harness does not put it there.

This probe is the assertion that it is gone. It runs one trivial prompt per
mode and reports, from Claude Code's own `--include-hook-events` stream rather
than from the model's self-report:

  * how many hooks fired, and what they injected
  * the advertised tool list, slash-command count, plugin list
  * whether MCP isolation held
  * whether the run was authenticated at all

The last one is not paranoia. An unauthenticated `claude -p` exits 0 with
`{"subtype": "success", "result": "Not logged in · Please run /login",
"total_cost_usd": 0}`. The harness would record that as a completed cell with
a cheap wrong answer, for every cell, and the arm would simply look bad.

Usage:
    python harness/env_isolation_probe.py --repo repos/django/django
    python harness/env_isolation_probe.py --repo <path> --no-settings   # before/after
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[1]
BENCH_SETTINGS = BENCH_ROOT / "configs" / "bench_settings.json"
EMPTY_MCP = BENCH_ROOT / "configs" / "_empty_mcp.json"

PROMPT = "Reply with exactly the word PONG and nothing else."


def probe(repo: Path, use_settings: bool, model: str, timeout: int) -> dict:
    cmd = [
        "claude", "-p", PROMPT,
        "--output-format", "stream-json", "--verbose", "--include-hook-events",
        "--model", model,
        "--max-budget-usd", "0.30",
        "--strict-mcp-config", "--mcp-config", str(EMPTY_MCP),
        "--disallowed-tools",
        "ListMcpResourcesTool,ReadMcpResourceTool,mcp__claude_ai_*,ToolSearch,mcp__*",
        "--allowed-tools", "Read,Grep,Glob",
    ]
    if use_settings:
        cmd[2:2] = ["--settings", str(BENCH_SETTINGS)]

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with open(os.devnull) as devnull:
        p = subprocess.run(
            cmd, cwd=str(repo), stdin=devnull, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )

    out: dict = {
        "settings_pinned": use_settings,
        "returncode": p.returncode,
        "hooks_fired": [],
        "hook_injections": [],
        "tools": [],
        "n_slash_commands": None,
        "n_skills": None,
        "plugins": None,
        "mcp_servers": None,
        "authenticated": None,
        "answer": "",
        "cost_usd": None,
    }
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        sub = d.get("subtype")
        if sub == "init":
            out["tools"] = d.get("tools") or []
            out["n_slash_commands"] = len(d.get("slash_commands") or [])
            out["n_skills"] = len(d.get("skills") or [])
            out["plugins"] = d.get("plugins")
            out["mcp_servers"] = d.get("mcp_servers")
        elif sub == "hook_response":
            out["hooks_fired"].append(d.get("hook_name"))
            text = d.get("output") or ""
            if text.strip():
                out["hook_injections"].append(
                    {"hook": d.get("hook_name"), "output": text[:4000]}
                )
        elif d.get("type") == "result":
            out["answer"] = str(d.get("result") or "")
            out["cost_usd"] = d.get("total_cost_usd")
    # An unauthenticated run answers this and costs nothing. Never let that
    # shape reach a results row as a completed cell.
    out["authenticated"] = not (
        "Not logged in" in out["answer"] or "/login" in out["answer"]
    )
    return out


def verdict(r: dict) -> list[str]:
    """Fail conditions, in the order in which a silent failure hides."""
    bad = []
    if not r["authenticated"]:
        bad.append(
            "NOT AUTHENTICATED — claude exited 0 and answered "
            f"{r['answer'][:60]!r} at $0. Every cell would record as complete."
        )
    if r["hooks_fired"]:
        bad.append(f"{len(r['hooks_fired'])} hooks fired: {sorted(set(r['hooks_fired']))}")
    if r["hook_injections"]:
        bad.append(
            f"{len(r['hook_injections'])} hook(s) INJECTED CONTEXT into the cell"
        )
    if r["plugins"]:
        bad.append(f"plugins loaded: {r['plugins']}")
    if r["mcp_servers"]:
        bad.append(f"MCP servers mounted despite --strict-mcp-config: {r['mcp_servers']}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--no-settings", action="store_true",
                    help="probe WITHOUT the pinned settings, to show the delta")
    ap.add_argument("--both", action="store_true",
                    help="probe both ways and print the before/after")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    modes = [False, True] if a.both else [not a.no_settings]
    results = []
    for use_settings in modes:
        label = "WITH pinned settings" if use_settings else "WITHOUT pinned settings"
        print(f"\n=== {label} ===", flush=True)
        r = probe(repo, use_settings, a.model, a.timeout)
        results.append(r)
        print(f"  hooks fired      : {len(r['hooks_fired'])} {sorted(set(r['hooks_fired']))}")
        for inj in r["hook_injections"]:
            print(f"  INJECTED by {inj['hook']}: {inj['output'][:300]!r}")
        print(f"  tools advertised : {len(r['tools'])}")
        print(f"  slash commands   : {r['n_slash_commands']} | skills: {r['n_skills']}")
        print(f"  plugins          : {r['plugins']}")
        print(f"  mcp servers      : {r['mcp_servers']}")
        print(f"  authenticated    : {r['authenticated']}  answer={r['answer'][:40]!r}")
        for line in verdict(r):
            print(f"  !! {line}")

    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {a.out}")

    final = results[-1]
    return 0 if not verdict(final) else 1


if __name__ == "__main__":
    sys.exit(main())
