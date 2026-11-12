"""Small-model arms: the same task set through opencode, models via OpenRouter.

    python paired/run_paired.py --harness opencode --tasks ... --out OUT \
        --model openrouter/qwen/qwen3-coder-30b-a3b-instruct --arms bare,rw

Open-weight models through OpenRouter stand in for local models. The arms match
the Claude ones: `bare` (opencode built-ins only), `rw` (plus the Repowise core
tools and the steer from STEER.md as an instruction file), `rw-ns` (no steer).
Read tasks deny edit/write/bash; edit tasks run in a fresh worktree and allow
edits. The key is read from the keys file (OPENROUTER_KEY) into the opencode
process only. Metrics come from opencode's NDJSON events, parsed by the same
function the other opencode benches use.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from harness.opencode_runner import _resolve_opencode_bin, parse_cli_events  # noqa: E402
from run_paired import CORE_TOOLS, KEY_ENVS, make_worktree, prompt_for, steer_text  # noqa: E402


def opencode_config(arm: str, repo: Path, py: str, model: str, edit: bool, steer_file: Path) -> dict:
    deny = "allow" if edit else "deny"
    cfg: dict = {
        "$schema": "https://opencode.ai/config.json",
        "snapshot": False,
        "model": model,
        "permission": {"edit": deny, "write": deny, "bash": deny, "webfetch": "deny",
                       "read": "allow", "grep": "allow", "glob": "allow", "list": "allow"},
    }
    if arm != "bare":
        cfg["mcp"] = {"repowise": {
            "type": "local",
            "command": [py, "-m", "repowise.cli.main", "mcp", str(repo), "--transport", "stdio",
                        "--no-workspace", "--tools", CORE_TOOLS],
            "environment": {**{k: "" for k in KEY_ENVS}, "REPOWISE_ANSWER_DISABLE_CACHE": "1",
                            "DO_NOT_TRACK": "1", "PYTHONIOENCODING": "utf-8"},
            "enabled": True, "timeout": 120000}}
    if arm == "rw":
        cfg["instructions"] = [str(steer_file)]
    return cfg


def main(a) -> None:
    spec = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    steer_file = root / "STEER.txt"
    steer_file.write_text(steer_text() + "\n", encoding="utf-8")
    key = ""
    for line in Path(a.keys).read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_KEY=") or line.startswith("OPENROUTER_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
    oc = _resolve_opencode_bin()
    tasks = [t for t in spec["tasks"] if not a.only or t["id"] in a.only.split(",")]
    spent = 0.0
    for rep in range(1, a.reps + 1):
        for t in tasks:
            for arm in a.arms.split(","):
                rid = f"{t['id']}__{arm}__r{rep}"
                cell = root / "runs" / rid
                if (cell / "meta.json").exists():
                    continue
                if spent >= a.cap_usd:
                    print(f"cap reached at ${spent:.2f}")
                    return
                cell.mkdir(parents=True, exist_ok=True)
                rinfo = spec["repos"][t["repo"]]
                repo = Path(rinfo["path"])
                edit = t.get("kind") == "edit"
                cwd = make_worktree(repo, rinfo["commit"], root / "wt" / rid) if edit else repo
                cfg_p = cell / "opencode.json"
                cfg_p.write_text(json.dumps(opencode_config(arm, repo, a.python, a.model, edit, steer_file), indent=2),
                                 encoding="utf-8")
                env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
                env.update(OPENROUTER_API_KEY=key, OPENCODE_CONFIG=str(cfg_p),
                           XDG_DATA_HOME=tempfile.mkdtemp(prefix="oc_"), DO_NOT_TRACK="1")
                t0 = time.time()
                p = subprocess.run([oc, "run", "--format", "json", "--dir", str(cwd), "--model", a.model,
                                    prompt_for(t, rinfo.get("desc", t["repo"]))],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=env, timeout=1800)
                (cell / "stream.jsonl").write_text(p.stdout, encoding="utf-8")
                (cell / "stderr.log").write_text(p.stderr[-20000:], encoding="utf-8")
                ev = parse_cli_events(p.stdout)
                u = ev.get("usage") or {}
                issued = (ev.get("server_tools_called") or {}).get("repowise", [])
                m = {"task": t["id"], "arm": arm, "rep": rep, "kind": t.get("kind", "read"), "tier": t.get("tier"),
                     "repo": t["repo"], "harness": "opencode", "model_arg": a.model, "rc": p.returncode,
                     "cost_usd": ev.get("total_cost_usd"), "wall_s": round(time.time() - t0, 1),
                     "tokens": {"input": u.get("input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0),
                                "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)},
                     "answer": ev.get("result", ""), "api_calls": ev.get("num_turns"),
                     "tool_calls": ev.get("num_tool_calls", 0), "rw_calls": len(issued),
                     "rw_tools": [n.split("_", 1)[-1] for n in issued], "used_rw": bool(issued),
                     # opencode events do not order tool calls against text, so "first tool" is not derivable here
                     "first_tool_rw": None, "files_read": ev.get("files_explored", []),
                     "n_files_read": len(set(ev.get("files_explored", []))), "first_ctx": None}
                m["total_tokens"] = sum(m["tokens"].values())
                if edit:
                    diff = subprocess.run(["git", "-C", str(cwd), "diff"], capture_output=True, text=True,
                                          encoding="utf-8", errors="replace").stdout
                    (cell / "diff.patch").write_text(diff, encoding="utf-8")
                    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(cwd)], capture_output=True)
                (cell / "meta.json").write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
                spent += m["cost_usd"] or 0
                print(f"{rid}: ${m['cost_usd']} tok={m['total_tokens']} rw={m['rw_calls']} total=${spent:.3f}", flush=True)
