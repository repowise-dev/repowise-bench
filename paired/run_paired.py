"""Paired agent sessions: the same task, bare and with Repowise, back to back.

    python paired/run_paired.py --tasks paired/tasks/<set>.json --out OUT \
        [--arms bare,rw] [--reps 1] [--prefix realistic|minimal] [--model sonnet] \
        [--only T1,T2] [--cap-usd 5] [--seed 20261113]

What one run is: `claude -p` on one task in one arm, stream-json captured to
`OUT/runs/<task>__<arm>__r<rep>/stream.jsonl` with `meta.json` beside it.
Resumable: a run with a `meta.json` is never repeated.

Arms
  bare   no MCP server, no CLAUDE.md.
  rw     the Repowise core tools only (get_answer, get_context, search_codebase,
         get_symbol) plus the steer in paired/STEER.md as the arm's CLAUDE.md.
  rw-ns  the same server without the steer (adoption tracking; not published).

Prefix
  realistic  all built-in tools, plus a seeded conversation history per repo
             (`--resume <seed> --fork-session`), so the first turn carries
             about 60k tokens as in a working session.
  minimal    `--tools Read,Grep,Glob,Bash` and no history (the first paired pilot's shape).

Fairness
  Each arm's prefix is warmed once before its first measured run, and the arms
  of a task run back to back in an order drawn from --seed, so every measured
  run reads a warm prompt cache. Each arm has its own CLAUDE_CONFIG_DIR, which
  removes the operator's hooks, plugins, skills and user CLAUDE.md.

Edit tasks (`"kind": "edit"`) run in a fresh git worktree at the task's commit,
may use Edit/Write, and keep `git diff` as `diff.patch` for the judge. The MCP
server keeps serving the indexed clone at the same commit.

Other harnesses: `--harness opencode` runs the same task set through opencode
(small open-weight models via OpenRouter; config in paired/opencode.json).
`--harness codex` is reserved for the Codex CLI and not wired yet.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE_TOOLS = "get_answer,get_context,search_codebase,get_symbol"
READ_ALLOW = ("Read Grep Glob Bash(git:*) Bash(ls:*) Bash(grep:*) Bash(rg:*) Bash(cat:*) "
              "Bash(sed:*) Bash(head:*) Bash(tail:*) Bash(wc:*) Bash(find:*) Bash(cd:*)")
EDIT_ALLOW = READ_ALLOW + " Edit Write MultiEdit Bash(python:*) Bash(go:*) Bash(npm:*) Bash(npx:*)"
KEY_ENVS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_KEY",
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY")
PROMPT = """You are a coding agent helping a developer on the {repo} repository (cwd).
{mode_line}

TASK
{task}

Final reply, exactly these sections:
ANSWER: <your answer{answer_hint}>
CONFIDENCE: high|medium|low
"""


def repo_path(repos_dir: str, name: str) -> Path:
    """A task's repository: a full clone with its index at <repos-dir>/<name>, at the commit the task file pins."""
    return (Path(repos_dir) / name).resolve()


def steer_text() -> str:
    m = re.search(r"```\n(.+?)\n```", (HERE / "STEER.md").read_text(encoding="utf-8"), re.S)
    return m.group(1).strip()


def claude_exe() -> str:
    # The npm .cmd shim truncates multi-line arguments; use the real binary.
    shim = shutil.which("claude")
    if shim:
        base = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code"
        for cand in (base / "bin" / "claude.exe", base / "claude.exe"):
            if cand.exists():
                return str(cand)
    return shim or "claude"


def project_dir_name(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def anthropic_env(cfg_dir: Path, key_file: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS and not k.startswith("CLAUDE")}
    for line in key_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            env["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    env.update(CLAUDE_CONFIG_DIR=str(cfg_dir), DISABLE_AUTOUPDATER="1", DO_NOT_TRACK="1",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def mcp_config(cell: Path, arm: str, repo: Path, py: str) -> Path:
    cell.mkdir(parents=True, exist_ok=True)
    servers = {}
    if arm != "bare":
        cfg = cell / "rw_cfg"
        cfg.mkdir(parents=True, exist_ok=True)
        servers["repowise"] = {
            "command": py,
            "args": ["-m", "repowise.cli.main", "mcp", str(repo), "--transport", "stdio",
                     "--no-workspace", "--tools", CORE_TOOLS],
            # Keyless: no provider key reaches the server; cache off so every call computes.
            "env": {**{k: "" for k in KEY_ENVS}, "REPOWISE_CONFIG_DIR": str(cfg),
                    "REPOWISE_ANSWER_DISABLE_CACHE": "1", "DO_NOT_TRACK": "1"},
        }
    p = cell / "mcp.json"
    p.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return p


def arm_config_dir(root: Path, arm: str) -> Path:
    d = root / "cfg" / arm
    d.mkdir(parents=True, exist_ok=True)
    md = d / "CLAUDE.md"
    if arm == "rw":
        md.write_text(steer_text() + "\n", encoding="utf-8")
    elif md.exists():
        md.unlink()
    # Skip first-run prompts in a fresh config root.
    (d / "settings.json").write_text(json.dumps({"includeCoAuthoredBy": False}), encoding="utf-8")
    return d


def place_seed(seed: dict | None, cfg_dir: Path, cwd: Path) -> str | None:
    if not seed:
        return None
    dst = cfg_dir / "projects" / project_dir_name(cwd)
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed["jsonl"], dst / Path(seed["jsonl"]).name)
    return seed["session_id"]


def build_cmd(task: dict, arm: str, mcp_json: Path, prefix: str, model: str, resume: str | None,
              run_budget: float = 4.0) -> list[str]:
    edit = task.get("kind") == "edit"
    cmd = [claude_exe(), "-p", "--model", model, "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(mcp_json), "--strict-mcp-config",
           "--allowedTools", (EDIT_ALLOW if edit else READ_ALLOW) + (" mcp__repowise" if arm != "bare" else ""),
           "--permission-mode", "dontAsk", "--max-turns", "60", "--max-budget-usd", str(run_budget)]
    # Keeps per-session sections out of the system prompt so forks of one seed share the prompt cache.
    cmd.append("--exclude-dynamic-system-prompt-sections")
    if prefix == "minimal":
        cmd += ["--tools", "Read,Grep,Glob,Bash" + (",Edit,Write" if edit else "")]
    if resume:
        cmd += ["--resume", resume, "--fork-session"]
    return cmd


def prompt_for(task: dict, repo_desc: str) -> str:
    edit = task.get("kind") == "edit"
    return PROMPT.format(
        repo=repo_desc, task=task["prompt"],
        mode_line=("Make the change in the working tree. Do not commit." if edit else "Do not modify any files."),
        answer_hint=("; summarise the change you made" if edit else ""))


def parse_stream(path: Path) -> dict:
    """Per-run metrics from a stream-json file."""
    tools, rw, files_read, first_tool = [], [], set(), None
    result, init, first_ctx = {}, {}, None
    by_msg: dict[str, int] = {}  # cross-check: per-message usage, de-duplicated by message id
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            init = ev
        elif t == "assistant":
            msg = ev.get("message", {})
            u = msg.get("usage") or {}
            if u and msg.get("id"):
                by_msg[msg["id"]] = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                                     + u.get("cache_read_input_tokens", 0) + u.get("output_tokens", 0))
            if first_ctx is None and u:
                first_ctx = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                             + u.get("cache_read_input_tokens", 0))
            for c in msg.get("content", []) or []:
                if c.get("type") == "tool_use":
                    name = c.get("name", "")
                    tools.append(name)
                    if name != "ToolSearch":  # loading deferred tool schemas is not a choice of tool
                        first_tool = first_tool or name
                    if name.startswith("mcp__repowise"):
                        rw.append(name.split("__")[-1])
                    if name == "Read":
                        files_read.add(str((c.get("input") or {}).get("file_path", "")).replace("\\", "/"))
        elif t == "result":
            result = ev
    mu = result.get("modelUsage") or {}
    tok = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    for v in mu.values():
        tok["input"] += v.get("inputTokens", 0)
        tok["cache_write"] += v.get("cacheCreationInputTokens", 0)
        tok["cache_read"] += v.get("cacheReadInputTokens", 0)
        tok["output"] += v.get("outputTokens", 0)
    return {
        "cost_usd": result.get("total_cost_usd"), "api_calls": result.get("num_turns"),
        "wall_s": round((result.get("duration_ms") or 0) / 1000, 1), "is_error": result.get("is_error"),
        "tokens": tok, "total_tokens": sum(tok.values()), "first_ctx": first_ctx,
        "total_tokens_by_message": sum(by_msg.values()),
        "tool_calls": len(tools), "rw_calls": len(rw), "rw_tools": rw, "used_rw": bool(rw),
        "first_tool_rw": bool(first_tool and first_tool.startswith("mcp__repowise")),
        "files_read": sorted(files_read), "n_files_read": len(files_read),
        "answer": result.get("result", ""), "cc_version": init.get("claude_code_version"),
        "model": init.get("model"), "init_tools": init.get("tools"), "mcp_servers": init.get("mcp_servers"),
    }


def run_one(cmd, prompt, cwd, env, out_dir: Path, timeout=1800) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(out_dir / "stream.jsonl", "w", encoding="utf-8") as so, open(out_dir / "stderr.log", "w", encoding="utf-8") as se:
        p = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", cwd=str(cwd), env=env,
                           stdout=so, stderr=se, timeout=timeout)
    m = parse_stream(out_dir / "stream.jsonl")
    m.update(rc=p.returncode, harness_wall_s=round(time.time() - t0, 1))
    return m


def make_worktree(repo: Path, commit: str, dest: Path) -> Path:
    if dest.exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(dest)], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(dest), commit],
                   check=True, capture_output=True)
    return dest


def seed_session(repo_name: str, repo: Path, spec: dict, root: Path, env_key: Path, model: str) -> dict:
    """One conversation per repo whose history becomes every arm's prefix."""
    sd = root / "seeds" / repo_name
    meta = sd / "seed.json"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    cfg = root / "cfg" / f"seed_{repo_name}"
    cfg.mkdir(parents=True, exist_ok=True)
    mcp = mcp_config(sd, "bare", repo, sys.executable)
    cmd = build_cmd({}, "bare", mcp, "realistic", model, None)
    m = run_one(cmd, spec["seed_prompt"], repo, anthropic_env(cfg, env_key), sd)
    sid = None
    for line in (sd / "stream.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        sid = sid or ev.get("session_id")
    hits = list((cfg / "projects").rglob(f"{sid}.jsonl"))
    if not hits:
        raise SystemExit(f"seed session file for {repo_name} not found under {cfg}")
    seed = {"session_id": sid, "jsonl": str(hits[0]), "cost_usd": m["cost_usd"], "total_tokens": m["total_tokens"]}
    meta.write_text(json.dumps(seed, indent=2), encoding="utf-8")
    return seed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", default="bare,rw")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--prefix", choices=["realistic", "minimal"], default="realistic")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--repos-dir", default="repos", help="clones with their indexes, one directory per repo name")
    ap.add_argument("--only", default="")
    ap.add_argument("--cap-usd", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--run-budget", type=float, default=4.0, help="per-run --max-budget-usd")
    ap.add_argument("--python", default=sys.executable, help="python that runs `repowise mcp`")
    ap.add_argument("--keys", default=str(Path.home() / "Desktop/repowise/backend/.env"))
    ap.add_argument("--harness", choices=["claude", "opencode", "codex"], default="claude")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.harness == "codex":
        raise SystemExit("codex harness: not wired yet (see paired/README.md, 'Second harness')")
    if a.harness == "opencode":
        from opencode_arm import main as oc_main  # noqa: PLC0415
        return oc_main(a)

    spec = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    root = Path(a.out).resolve()  # children run with cwd=repo; every path handed to them must be absolute
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
    (root / "order.json").write_text(json.dumps([[t["id"], arm, rep] for t, arm, rep in plan]), encoding="utf-8")
    if a.dry_run:
        print(json.dumps([[t["id"], arm, rep] for t, arm, rep in plan]))
        return

    key_file = Path(a.keys)
    cfgs = {arm: arm_config_dir(root, arm) for arm in arms}
    seeds = {}
    if a.prefix == "realistic":
        for name in sorted({t["repo"] for t in tasks}):
            seeds[name] = seed_session(name, repo_path(a.repos_dir, name), spec["repos"][name], root, key_file, a.model)
    spent = sum((json.loads(p.read_text())["cost_usd"] or 0) for p in root.glob("runs/*/meta.json"))
    spent += sum(s.get("cost_usd") or 0 for s in seeds.values())
    warmed: set[str] = set()
    for t, arm, rep in plan:
        rid = f"{t['id']}__{arm}__r{rep}"
        cell = root / "runs" / rid
        if (cell / "meta.json").exists():
            continue
        if spent >= a.cap_usd:
            print(f"cap reached at ${spent:.2f}; stopping before {rid}")
            break
        rinfo = spec["repos"][t["repo"]]
        repo = repo_path(a.repos_dir, t["repo"])
        cwd = repo
        if t.get("kind") == "edit":
            cwd = make_worktree(repo, rinfo["commit"], root / "wt" / rid)
        mcp = mcp_config(cell, arm, repo, a.python)
        resume = place_seed(seeds.get(t["repo"]), cfgs[arm], cwd)
        env = anthropic_env(cfgs[arm], key_file)
        cmd = build_cmd(t, arm, mcp, a.prefix, a.model, resume, a.run_budget)
        if f"{arm}|{t['repo']}" not in warmed:
            # Warm this arm's prompt-cache prefix; the run itself is not measured.
            w = run_one(cmd, "Reply with the single word: ready", cwd, env, root / "warm" / f"{arm}__{t['repo']}")
            spent += w["cost_usd"] or 0
            warmed.add(f"{arm}|{t['repo']}")
        m = run_one(cmd, prompt_for(t, rinfo.get("desc", t["repo"])), cwd, env, cell)
        if t.get("kind") == "edit":
            subprocess.run(["git", "-C", str(cwd), "add", "-N", "."], capture_output=True)  # new files show in the diff
            diff = subprocess.run(["git", "-C", str(cwd), "diff"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace").stdout
            (cell / "diff.patch").write_text(diff, encoding="utf-8")
            m["diff_lines"] = diff.count("\n")
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(cwd)], capture_output=True)
        m.update(task=t["id"], arm=arm, rep=rep, kind=t.get("kind", "read"), tier=t.get("tier"),
                 repo=t["repo"], prefix=a.prefix, model_arg=a.model, resumed=bool(resume))
        spent += m["cost_usd"] or 0
        servers = {s.get("name"): s.get("status") for s in (m.get("mcp_servers") or [])}
        failure = ("exit code %s" % m["rc"] if m["rc"] else "result is_error" if m["is_error"] else
                   "repowise server %s" % servers.get("repowise") if arm != "bare" and servers.get("repowise") != "connected" else
                   "resume failed" if resume and not m["first_ctx"] else None)
        if failure:
            # Not final: kept for inspection, retried on the next invocation, never analysed.
            (cell / "failed.json").write_text(json.dumps({**m, "failure": failure}, indent=2), encoding="utf-8")
            print(f"{rid}: FAILED ({failure}); will retry", flush=True)
            continue
        (cell / "meta.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        print(f"{rid}: ${m['cost_usd']} tok={m['total_tokens']} rw={m['rw_calls']} ctx1={m['first_ctx']} total=${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
