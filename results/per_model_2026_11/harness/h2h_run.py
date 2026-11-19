"""H2H runner: the published single-turn protocol, bare vs Repowise.

    python paired/h2h_run.py --tasks paired/tasks/h2h.json --repos-dir REPOS --out OUT \
        --steer-dir STEER --python PY [--reps 4] [--only H-EXC] [--cap-usd 5]

One run = `claude -p "<question>" --output-format stream-json --verbose
--permission-mode bypassPermissions --model M --effort high --max-budget-usd 4
--strict-mcp-config --mcp-config <cfg>`, cwd = the clone, the raw question as the prompt,
no seeded history, no warm-up (cold-start runs are kept and counted). Each arm has its own
CLAUDE_CONFIG_DIR; the rw arm's CLAUDE.md is STEER/<repo>.CLAUDE.md. meta.json per run uses
the run_paired layout so judge.py grades it unchanged.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_paired import anthropic_env, arm_config_dir, claude_exe, mcp_config, parse_stream, repo_path, run_one  # noqa: E402

HERE = Path(__file__).resolve().parent
SHELL_READ =re.compile(r"^\s*(cat|head|tail|sed|less|more|nl|bat|type|Get-Content|gc|awk)\b")


def extra_metrics(stream: Path) -> dict:
    """File reads (Read paths + shell reads), sub-agent tool calls, and the first Repowise call's status."""
    reads, shell_reads, sub_calls, rw_first_err, rw_ids = set(), 0, 0, None, set()
    init_status = None
    for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init_status = {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}.get("repowise")
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []) or []:
                if c.get("type") != "tool_use":
                    continue
                if ev.get("parent_tool_use_id"):
                    sub_calls += 1
                inp = c.get("input") or {}
                if c.get("name") == "Read":
                    reads.add(str(inp.get("file_path", "")).replace("\\", "/"))
                elif c.get("name") in ("Bash", "PowerShell") and SHELL_READ.match(str(inp.get("command", ""))):
                    shell_reads += 1
                elif c.get("name", "").startswith("mcp__repowise"):
                    rw_ids.add(c.get("id"))
        if ev.get("type") == "user" and rw_first_err is None:
            for c in ev.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in rw_ids:
                    rw_first_err = bool(c.get("is_error"))
    return {"n_file_reads": len(reads) + shell_reads, "shell_reads": shell_reads, "subagent_tool_calls": sub_calls,
            "rw_init_status": init_status, "rw_first_call_error": rw_first_err}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repos-dir", required=True)
    ap.add_argument("--steer-dir", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--arms", default="bare,rw")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--only", default="")
    ap.add_argument("--cap-usd", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--effort", type=int, default=1, help="1 = pass --effort high (smoke protocol)")
    ap.add_argument("--spent-extra", type=float, default=0.0, help="spend outside this root counted toward the cap")
    ap.add_argument("--est-next", type=float, default=0.4, help="floor for the next run's cost estimate")
    ap.add_argument("--keys", default=".env")
    a = ap.parse_args()
    spec = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    root = Path(a.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    arms = a.arms.split(",")
    tasks = [t for t in spec["tasks"] if not a.only or t["id"] in a.only.split(",")]
    rng = random.Random(a.seed)
    plan = []
    for rep in range(1, a.reps + 1):
        for t in tasks:
            order = arms[:]
            rng.shuffle(order)
            plan += [(t, arm, rep) for arm in order]
    cfgs = {arm: arm_config_dir(root, arm) for arm in arms}
    # Cap is global across this study's output roots plus the model probe (a.spent_extra).
    spent = a.spent_extra + sum((json.loads(p.read_text())["cost_usd"] or 0) for p in root.glob("runs/*/meta.json"))
    est_next = max([a.est_next] + [1.5 * (json.loads(p.read_text())["cost_usd"] or 0) for p in root.glob("runs/*/meta.json")])
    for t, arm, rep in plan:
        rid = f"{t['id']}__{arm}__r{rep}"
        cell = root / "runs" / rid
        if (cell / "meta.json").exists():
            continue
        if spent >= a.cap_usd:
            print(f"cap reached at ${spent:.2f}; stopping before {rid}", flush=True)
            break
        if spent + est_next > a.cap_usd:
            print(f"next run could exceed cap (spent ${spent:.2f} + est ${est_next:.2f} > ${a.cap_usd}); stopping before {rid}", flush=True)
            break
        repo = repo_path(a.repos_dir, t["repo"])
        mcp = mcp_config(cell, arm, repo, a.python)
        if arm == "rw2":
            # R2: the same server behind the always-load proxy (core tools get _meta anthropic/alwaysLoad).
            cfgj = json.loads(mcp.read_text(encoding="utf-8"))
            srv = cfgj["mcpServers"]["repowise"]
            srv["args"] = [str(HERE / "alwaysload_proxy.py"), str(cell / "proxy_tools.jsonl"),
                           "get_answer,get_context,search_codebase,get_symbol", "--", srv["command"], *srv["args"]]
            srv["command"] = a.python
            mcp.write_text(json.dumps(cfgj, indent=2), encoding="utf-8")
        if arm in ("rw", "rw2"):
            (cfgs[arm] / "CLAUDE.md").write_text(
                (Path(a.steer_dir) / f"{t['repo']}.CLAUDE.md").read_text(encoding="utf-8"), encoding="utf-8")
        env = anthropic_env(cfgs[arm], Path(a.keys))
        cmd = [claude_exe(), "-p", t["prompt"], "--output-format", "stream-json", "--verbose",
               "--permission-mode", "bypassPermissions", "--model", a.model] + (["--effort", "high"] if a.effort else []) + [
               "--max-budget-usd", "4", "--strict-mcp-config", "--mcp-config", str(mcp)]
        try:
            m = run_one(cmd, "", repo, env, cell, timeout=900)
        except Exception as e:  # a hung run is recorded, not retried
            m = parse_stream(cell / "stream.jsonl")
            m.update(rc=-9, error=str(e)[:200])
        m.update(extra_metrics(cell / "stream.jsonl"))
        m.update(task=t["id"], arm=arm, rep=rep, kind="read", repo=t["repo"], model_arg=a.model, protocol="h2h-single-turn")
        spent += m["cost_usd"] or 0
        est_next = max(est_next, 1.5 * (m["cost_usd"] or 0))
        (cell / "meta.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        print(f"{rid}: ${m['cost_usd']} tok={m['total_tokens']} tokmsg={m['total_tokens_by_message']} calls={m['tool_calls']} "
              f"rw={m['rw_calls']} reads={m['n_file_reads']} wall={m['wall_s']} total=${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
