"""Per-model savings bench driver: the same question, bare and with Repowise, on the subscription.

    python driver.py --model haiku --out ../runs/haiku [--only FOR-01,FOR-02] [--reps 3] [--jobs 2]

One run = headless `claude -p` (stream-json) on one question in one arm. Resumable: a run with a
meta.json is never repeated; a failed run is kept as failed.json and retried next time.

Arms
  bare  clean clone (no .repowise), no MCP server, the plain prompt.
  rw    indexed clone, Repowise core tools only (get_answer, get_context, search_codebase,
        get_symbol), keyless, `mcp.always_load: true` in the repo's .repowise/config.yaml,
        and the steered prompt (the plain prompt plus one tool sentence).

Both arms: model default effort, user settings, hooks, plugins and skills off
(--setting-sources project, --disable-slash-commands), no web tools, read-only permissions.
Metrics reuse the paired harness parser (run_paired.parse_stream, h2h_run.extra_metrics).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from h2h_run import extra_metrics  # noqa: E402
from run_paired import CORE_TOOLS, KEY_ENVS, claude_exe, parse_stream  # noqa: E402

BENCH = HERE.parent
# The Python that runs the Repowise MCP server, and the repowise checkout it imports (packages/*/src).
# The published grid used repowise at 061b2bc79. Set REPOWISE_SRC (or --repowise-src) to a checkout of
# the sha under test; empty means the repowise installed in REPOWISE_PY.
PY = os.environ.get("REPOWISE_PY", sys.executable)
SRC = os.environ.get("REPOWISE_SRC", "")


def pypath(src: str) -> str:
    return os.pathsep.join(str(Path(src) / "packages" / p / "src") for p in ("core", "server", "cli"))


def src_sha(src: str) -> str | None:
    if not src:
        return None
    p = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True)
    return p.stdout.strip() or None
REPOS = ["foreman", "okf-agent-memory", "leviathan"]
DESC = {"foreman": "foreman (Python)", "okf-agent-memory": "okf-agent-memory (Go)", "leviathan": "leviathan (Rust)"}
ALLOW = ("Read Grep Glob LS TodoWrite Agent Task Bash(git:*) Bash(ls:*) Bash(grep:*) Bash(rg:*) Bash(cat:*) "
         "Bash(sed:*) Bash(head:*) Bash(tail:*) Bash(wc:*) Bash(find:*) Bash(cd:*) Bash(awk:*) Bash(nl:*)")
STEER = ("Use the repowise tools (get_answer, get_context, search_codebase, get_symbol) to find the code, "
         "and do not re-read code a tool result already delivered.")
PROMPT = """You are a coding agent helping a developer on the {repo} repository (cwd). Do not modify any files.
{steer}
TASK
{task}

Final reply, exactly these sections:
ANSWER: <your answer>
CONFIDENCE: high|medium|low
"""


def child_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in KEY_ENVS and not k.startswith("CLAUDE") and k not in ("AI_AGENT", "claude")}
    env.update(DISABLE_AUTOUPDATER="1", DO_NOT_TRACK="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def mcp_json(cell: Path, arm: str, repo: Path) -> Path:
    servers = {}
    if arm == "rw":
        cfg = cell / "rw_cfg"
        cfg.mkdir(parents=True, exist_ok=True)
        servers["repowise"] = {
            "command": PY,
            "args": ["-m", "repowise.cli.main", "mcp", str(repo), "--transport", "stdio",
                     "--no-workspace", "--tools", CORE_TOOLS],
            "env": {**{k: "" for k in KEY_ENVS}, "REPOWISE_CONFIG_DIR": str(cfg),
                    **({"PYTHONPATH": pypath(SRC)} if SRC else {}), "REPOWISE_ANSWER_DISABLE_CACHE": "1", "DO_NOT_TRACK": "1", "REPOWISE_TELEMETRY_DISABLED": "1"},
        }
    p = cell / "mcp.json"
    p.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return p


def build_cmd(model: str, arm: str, mcp: Path) -> list[str]:
    allow = ALLOW + (" mcp__repowise" if arm == "rw" else "")
    return [claude_exe(), "-p", "--model", model, "--output-format", "stream-json", "--verbose",
            "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence",
            "--strict-mcp-config", "--mcp-config", str(mcp),
            "--disallowedTools", "WebFetch WebSearch",
            "--allowedTools", allow, "--permission-mode", "dontAsk", "--max-turns", "60"]


STOP = BENCH / "runs" / "STOP"
LIMIT = re.compile(r"usage limit|rate limit|rate_limit|limit reached|resets? at|hit your limit|429|overloaded", re.I)


def run_cell(q: dict, repo: str, arm: str, rep: int, model: str, out: Path, lock: threading.Lock) -> None:
    rid = f"{q['id']}__{arm}__r{rep}"
    cell = out / "runs" / rid
    if (cell / "meta.json").exists() or STOP.exists():
        return
    cell.mkdir(parents=True, exist_ok=True)
    cwd = (BENCH / ("repos" if arm == "rw" else "bare") / repo).resolve()
    mcp = mcp_json(cell, arm, (BENCH / "repos" / repo).resolve())
    prompt = PROMPT.format(repo=DESC[repo], steer=(STEER + "\n") if arm == "rw" else "", task=q["question"])
    t0 = time.time()
    rc = None
    with open(cell / "stream.jsonl", "w", encoding="utf-8") as so, open(cell / "stderr.log", "w", encoding="utf-8") as se:
        try:
            p = subprocess.run(build_cmd(model, arm, mcp), input=prompt, text=True, encoding="utf-8",
                               cwd=str(cwd), env=child_env(), stdout=so, stderr=se, timeout=900)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    m = parse_stream(cell / "stream.jsonl")
    m.update(extra_metrics(cell / "stream.jsonl"))
    m.update(rc=rc, harness_wall_s=round(time.time() - t0, 1), task=q["id"], repo=repo, arm=arm, rep=rep,
             kind=q["kind"], model_arg=model, prompt=prompt, repowise_sha=src_sha(SRC))
    servers = {s.get("name"): s.get("status") for s in (m.get("mcp_servers") or [])}
    failure = ("timeout" if rc == "timeout" else f"exit code {rc}" if rc else
               "result is_error" if m["is_error"] else
               f"repowise server {servers.get('repowise')}" if arm == "rw" and servers.get("repowise") != "connected"
               else None)
    with lock:
        if failure:
            blob = (cell / "stderr.log").read_text(encoding="utf-8", errors="replace") + str(m.get("answer", ""))
            if LIMIT.search(blob):
                failure = "LIMIT: " + failure
                STOP.write_text(f"{model} {rid} {time.ctime()}\n{blob[-2000:]}", encoding="utf-8")
            (cell / "failed.json").write_text(json.dumps({**m, "failure": failure}, indent=2), encoding="utf-8")
            print(f"{model} {rid}: FAILED ({failure})", flush=True)
            return
        (cell / "failed.json").unlink(missing_ok=True)
        (cell / "meta.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        print(f"{model} {rid}: out={m['tokens']['output']} tok={m['total_tokens']} calls={m['tool_calls']} "
              f"rw={m['rw_calls']} reads={m['n_file_reads']} wall={m['wall_s']}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--arms", default="bare,rw")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20261118)
    ap.add_argument("--qset", default=str(BENCH / "questions" / "grid_set.json"), help="pre-registered ids; '' = all")
    ap.add_argument("--python", default=None, help="Python for the Repowise MCP server (default: REPOWISE_PY, else this one)")
    ap.add_argument("--repowise-src", default=None, help="repowise checkout the server imports (default: REPOWISE_SRC)")
    a = ap.parse_args()
    global PY, SRC
    PY = a.python or PY
    SRC = a.repowise_src if a.repowise_src is not None else SRC
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    qs = []
    for repo in REPOS:
        for q in json.loads((BENCH / "questions" / f"{repo}.json").read_text(encoding="utf-8"))["questions"]:
            keep = not a.qset or q["id"] in json.loads(Path(a.qset).read_text(encoding="utf-8"))[repo]
            if keep and (not a.only or q["id"] in a.only.split(",")):
                qs.append((q, repo))
    rng = random.Random(a.seed)
    plan = []
    for rep in range(1, a.reps + 1):
        for q, repo in qs:
            arms = a.arms.split(",")
            rng.shuffle(arms)
            plan += [(q, repo, arm, rep) for arm in arms]
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for f in [ex.submit(run_cell, q, repo, arm, rep, a.model, out, lock) for q, repo, arm, rep in plan]:
            f.result()


if __name__ == "__main__":
    main()
