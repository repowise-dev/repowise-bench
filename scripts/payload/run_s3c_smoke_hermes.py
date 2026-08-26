"""Session C SMOKE: two arms x three tasks x one rep, on cell B (hermes-agent).

WHAT THIS CANNOT DO, STATED BEFORE IT RUNS
------------------------------------------
**This smoke cannot measure cost.** n=1 per arm against a measured 23.3% CV
(07_SESSION_C_DESIGN.md section 1.3) resolves nothing about dollars, and
reporting a cost delta from it would be dead detector #13 in an arc that already
has twelve. Any number this produces goes in the results file as a per-task
PRICE (gate (d)) for budgeting, never as a bare-vs-treated comparison.

WHAT IT IS FOR, AND ALL IT IS FOR
---------------------------------
1. Plumbing. Does the cell-B index serve, does the MCP server come up, do both
   arms pass their gates, does the runner write rows.
2. Behaviour. Does the agent CALL the tools at all, and does `get_context` now
   resolve `Class.method` targets rather than returning `Target not found` --
   that is #1435 (f82ebb0d), which `MCP_BEHAVIOUR_FINDINGS.md` section 2a
   measured failing 42% of `get_context` calls on cell A.
3. Completion on B03, whose oracle is proved in both directions.
4. Gate (d): what one task costs on a 15.8x repo. This is still OWED from
   Session C's design and it rescales section 6 of `07_SESSION_C_DESIGN.md`.

CONDITION IS `unenforced` FOR BOTH ARMS
---------------------------------------
A bare arm has no tool to be nudged toward, so `enforced` would not be a shared
condition, and standing rule E14 forbids pooling enforced and unenforced arms.
It is also the condition that makes question 2 answerable: whether the agent
reaches for the tools when nothing makes it.

THE TREE-NAME LAND MINE, FIXED BEFORE THIS FILE EXISTED
-------------------------------------------------------
`session_arms.tree_for` used to hardcode `rich` into every tree name, and
`se-c0-bare-r1-rich` / `se-c0-short-r{1..6}-rich` are already on disk from cell
A. A cell-B arm would have silently resolved to a cell-A tree, `prepare` would
have skipped the `worktree add` because the path existed, and this smoke would
have measured Textualize/rich under hermes labels. `tree_slug` now parameterises
it and `scripts/payload/prove_tree_slug.py` proves it in both directions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
PY = Path(r"C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe")
ARMS = BENCH / "harness" / "session_arms.py"
CONFIG = BENCH / "configs" / "session_cost_eval_cellB_hermes.yaml"
OUT = (BENCH / "results" / "bakeoff_2026_08" / "session-cost-eval"
       / "sessions_s3c_smoke_hermes.jsonl")

# The binary under test. It must CONTAIN #1435 (f82ebb0d), which is the whole
# reason question 2 above is worth asking, and the commit is recorded in the
# results file rather than assumed from a version string -- `repowise --version`
# reads 0.41.0 for builds either side of the fix.
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
BINARY_PYTHON = REPOWISE_ROOT / ".venv" / "Scripts" / "python.exe"
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"

ARM_NAMES = ["c0-bare", "rw-full"]
CONDITION = "unenforced"
LIMIT_TASKS = 3


def _env() -> dict:
    env = os.environ.copy()
    env["REPOWISE_EXE"] = str(REPOWISE_EXE)
    env["DO_NOT_TRACK"] = "1"
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # The treated arm's server needs a live embedder to answer at all, and the
    # index it queries was written at 1536 dims. A server that cannot resolve an
    # embedder returns `degraded`, which `MCP_BEHAVIOUR_FINDINGS.md` section 5
    # found is what 20 of 22 stated tool abandonments blame.
    for line in (REPOWISE_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            if k.strip() in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _arms(arm: str, *extra: str) -> subprocess.CompletedProcess:
    cmd = [str(PY), str(ARMS), "--config", str(CONFIG), "--arm", arm,
           "--condition", CONDITION, "--binary-python", str(BINARY_PYTHON),
           *extra]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=str(BENCH), env=_env())


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for arm in ARM_NAMES:
        r = _arms(arm, "--prepare", "--gate", "--phase", "pre",
                  "--emit-cmd", "--out", str(OUT))
        if r.returncode != 0:
            print(f"!! {arm} FAILED ITS PRE GATE. Stopping rather than grading "
                  f"an arm that is not what it claims to be.\n"
                  f"{r.stdout[-2500:]}\n{r.stderr[-1500:]}", file=sys.stderr)
            return 1
        cmd = json.loads(r.stdout)["cmd"]
        cmd += ["--limit-tasks", str(LIMIT_TASKS)]

        print(f"\n{'=' * 70}\n=== {arm}  ({time.strftime('%H:%M:%S')})  "
              f"{LIMIT_TASKS} tasks  cellB-hermes\n{'=' * 70}", flush=True)
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(BENCH), env=_env())
        print(f"=== {arm} exited {p.returncode} after {time.time() - t0:.0f}s",
              flush=True)
        if p.returncode != 0:
            print(f"!! {arm} exited non-zero. Stopping rather than running the "
                  f"rest on top of a broken arm.", file=sys.stderr)
            return p.returncode

        g = _arms(arm, "--gate", "--phase", "post")
        (OUT.parent / f"gate_post__{arm}__cellB.json").write_text(
            g.stdout, encoding="utf-8")
        print(f"--- {arm} POST GATE rc={g.returncode}")

    print(f"\nsmoke complete: {len(ARM_NAMES)} arms -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
