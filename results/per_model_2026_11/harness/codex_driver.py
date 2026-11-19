"""Codex arm of the per-model bench: `codex exec --json` on the ChatGPT subscription, isolated home.

    python codex_driver.py --out ../runs/codex-gpt-5.6-terra [--only ...] [--reps 3]

Isolation: HOME/USERPROFILE and CODEX_HOME point at bench/codex_user, which holds only auth.json and a
config.toml naming the model, so the operator's hooks, rules, skills and MCP servers never load.
The repowise arm adds the same keyless core-four server as driver.py through -c overrides.
meta.json uses driver.py's layout (plus files_opened) so analyze.py reads it unchanged.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import re
import subprocess
import threading
import time
from pathlib import Path

from analyze import SHELL_READ, norm
from driver import BENCH, DESC, LIMIT, PROMPT, PY, PYPATH, REPOS, STEER, STOP, CORE_TOOLS, KEY_ENVS, child_env

HOME = BENCH / "codex_user"
MODEL = "gpt-5.6-terra"


def toml_str(s: str) -> str:
    return json.dumps(s)


def build_cmd(arm: str, repo: Path, cfg: Path) -> list[str]:
    cmd = [shutil.which("codex") or "codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-m", MODEL]
    if arm == "rw":
        args = ["-m", "repowise.cli.main", "mcp", str(repo), "--transport", "stdio", "--no-workspace", "--tools", CORE_TOOLS]
        env = {**{k: "" for k in KEY_ENVS}, "REPOWISE_CONFIG_DIR": str(cfg), "PYTHONPATH": PYPATH,
               "REPOWISE_ANSWER_DISABLE_CACHE": "1", "DO_NOT_TRACK": "1", "REPOWISE_TELEMETRY_DISABLED": "1"}
        cmd += ["-c", f"mcp_servers.repowise.command={toml_str(PY)}",
                "-c", "mcp_servers.repowise.args=[" + ",".join(toml_str(a) for a in args) + "]",
                "-c", "mcp_servers.repowise.env={" + ",".join(f"{k}={toml_str(v)}" for k, v in env.items()) + "}",
                "-c", "mcp_servers.repowise.startup_timeout_sec=60", "-c", "mcp_servers.repowise.required=true"]
    return cmd + ["-"]


def parse(stream: Path) -> dict:
    tok = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    tools, rw, files, answer, hooks, errors = 0, [], set(), "", 0, []
    for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type", "")
        if "hook" in line.lower() and "repowise-rewrite" in line:
            hooks += 1
        if t == "turn.completed":
            u = ev.get("usage") or {}
            tok["input"] += u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)
            tok["cache_read"] += u.get("cached_input_tokens", 0)
            tok["cache_write"] += u.get("cache_write_input_tokens", 0)
            tok["output"] += u.get("output_tokens", 0)
            tok["reasoning"] += u.get("reasoning_output_tokens", 0)
        elif t == "turn.failed" or t == "error":
            errors.append(str(ev)[:500])
        elif t == "item.completed":
            it = ev.get("item") or {}
            k = it.get("type")
            if k == "agent_message":
                answer = it.get("text", "")
            elif k == "command_execution":
                tools += 1
                cmd = it.get("command", "")
                inner = re.sub(r'^"?[^"]*powershell\.exe"?\s+-Command\s+', "", cmd)
                inner = inner.strip().strip("'\"")
                for part in re.split(r"&&|;|\|\|", inner):
                    seg = part.split("|")[0].strip()
                    if SHELL_READ.match(seg):
                        for tkn in re.findall(r"""'([^']+)'|"([^"]+)"|(\S+)""", seg)[1:]:
                            p = next(x for x in tkn if x)
                            if not p.startswith("-") and re.search(r"\.[A-Za-z0-9]{1,6}$", p):
                                files.add(norm(p))
            elif k == "mcp_tool_call":
                tools += 1
                if it.get("server") == "repowise":
                    rw.append(it.get("tool"))
            elif k in ("file_change", "web_search"):
                tools += 1
    return {"tokens": {"input": tok["input"], "cache_write": tok["cache_write"], "cache_read": tok["cache_read"],
                       "output": tok["output"]}, "reasoning_tokens": tok["reasoning"],
            "total_tokens": tok["input"] + tok["cache_read"] + tok["output"], "tool_calls": tools,
            "rw_calls": len(rw), "rw_tools": rw, "files_opened": len(files), "answer": answer,
            "hook_events": hooks, "errors": errors, "cost_usd": 0, "api_calls": None, "model": MODEL}


def run_cell(q, repo, arm, rep, out: Path, lock):
    rid = f"{q['id']}__{arm}__r{rep}"
    cell = out / "runs" / rid
    if (cell / "meta.json").exists() or (STOP.parent / "STOP_CODEX").exists():
        return
    cell.mkdir(parents=True, exist_ok=True)
    cfg = cell / "rw_cfg"
    cfg.mkdir(exist_ok=True)
    cwd = (BENCH / ("repos" if arm == "rw" else "bare") / repo).resolve()
    prompt = PROMPT.format(repo=DESC[repo], steer=(STEER + "\n") if arm == "rw" else "", task=q["question"])
    env = child_env()
    env.update(HOME=str(HOME), USERPROFILE=str(HOME), CODEX_HOME=str(HOME / ".codex"))
    t0 = time.time()
    with open(cell / "stream.jsonl", "w", encoding="utf-8") as so, open(cell / "stderr.log", "w", encoding="utf-8") as se:
        try:
            rc = subprocess.run(build_cmd(arm, (BENCH / "repos" / repo).resolve(), cfg), input=prompt, text=True,
                                encoding="utf-8", cwd=str(cwd), env=env, stdout=so, stderr=se, timeout=900).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    m = parse(cell / "stream.jsonl")
    wall = round(time.time() - t0, 1)
    m.update(rc=rc, wall_s=wall, harness_wall_s=wall, task=q["id"], repo=repo, arm=arm, rep=rep, kind=q["kind"],
             model_arg=MODEL, prompt=prompt)
    stderr = (cell / "stderr.log").read_text(encoding="utf-8", errors="replace")
    failure = ("timeout" if rc == "timeout" else f"exit code {rc}" if rc else
               "errors" if m["errors"] or not m["answer"] else
               "repowise unused/unavailable" if arm == "rw" and "repowise" in stderr.lower() and "error" in stderr.lower() and not m["rw_calls"] else None)
    with lock:
        if failure:
            if LIMIT.search(stderr + " ".join(m["errors"])):
                failure = "LIMIT: " + failure
                (STOP.parent / "STOP_CODEX").write_text(f"{rid} {time.ctime()}\n{stderr[-2000:]}", encoding="utf-8")
            (cell / "failed.json").write_text(json.dumps({**m, "failure": failure}, indent=2), encoding="utf-8")
            print(f"codex {rid}: FAILED ({failure})", flush=True)
            return
        (cell / "failed.json").unlink(missing_ok=True)
        (cell / "meta.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        print(f"codex {rid}: out={m['tokens']['output']} tok={m['total_tokens']} calls={m['tool_calls']} rw={m['rw_calls']} "
              f"files={m['files_opened']} wall={m['wall_s']} hooks={m['hook_events']}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=20261118)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    sel = json.loads((BENCH / "questions" / "grid_set.json").read_text(encoding="utf-8"))
    qs = [(q, r) for r in REPOS for q in json.loads((BENCH / "questions" / f"{r}.json").read_text(encoding="utf-8"))["questions"]
          if q["id"] in sel[r] and (not a.only or q["id"] in a.only.split(","))]
    rng = random.Random(a.seed)
    lock = threading.Lock()
    for rep in range(1, a.reps + 1):
        for q, r in qs:
            arms = ["bare", "rw"]
            rng.shuffle(arms)
            for arm in arms:
                if (STOP.parent / "STOP_CODEX").exists():
                    print("STOP_CODEX present; halting", flush=True)
                    return
                run_cell(q, r, arm, rep, out, lock)


if __name__ == "__main__":
    main()
