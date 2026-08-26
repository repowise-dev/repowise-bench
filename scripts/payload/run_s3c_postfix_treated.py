"""Post-#1443 cell-B baseline: the TREATED arm only, three tasks, one rep.

WHY ONLY THE TREATED ARM
------------------------
OSS PR #1443 (`a7de4b9e`, 2026-08-12) stopped the traversal size cap dropping
every file over 500 KB, which had silently removed hermes's six biggest modules
(103,664 lines, 2,617 symbols: the CLI, gateway, web server and state layer).
Every hermes index built before that date is stale.

But `ARM_SHAPE["c0-bare"]` is `(no mcp, no hooks, no block)` and the bare tree
carries no `.repowise/` at all -- RESULT_S3C_SMOKE.md section 5 records this as
the design, not an omission. An index that does not exist cannot have been
invalidated by an indexer fix, so the smoke's bare arm ($2.8626 over B01-B03,
2026-08-11) still stands and is reused as the reference. Only the treated arm
queried the stale index, so only the treated arm is re-run.

WHAT THIS STILL CANNOT DO
-------------------------
**It cannot measure cost.** n=1 against a measured 23.3% CV resolves nothing
about dollars, and this run is additionally cross-day against its bare
reference. The arc measured cross-run drift as small (-5.1% billed / +1.9% cost)
but that is a reason to tolerate the comparison, not to report a delta from it.
Numbers here are a per-task PRICE (gate (d)) and a behaviour count. Twelve dead
detectors in this arc; every one produced a plausible number.

Arm order is still NOT counterbalanced -- this runs treated alone, so the
question does not arise here, and it remains open for the scored cell.

TREES
-----
`tree_slug: hermes-pf`, so this resolves to `se-rw-full-hermes-pf`, whose index
was copied from the verified golden build (pages 8587, symbols 88016,
index_vector_dim 1536). The old `hermes` slug still resolves to the STALE
pre-fix trees, and `prepare` skips `worktree add` when a path exists, so the
slug bump is what keeps this run off the stale index.
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
       / "sessions_s3c_postfix_hermes.jsonl")

REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
BINARY_PYTHON = REPOWISE_ROOT / ".venv" / "Scripts" / "python.exe"
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"

ARM_NAMES = ["rw-full"]
CONDITION = "unenforced"
LIMIT_TASKS = 3


def _env() -> dict:
    env = os.environ.copy()
    env["REPOWISE_EXE"] = str(REPOWISE_EXE)
    env["DO_NOT_TRACK"] = "1"
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
              f"{LIMIT_TASKS} tasks  cellB-hermes POST-FIX\n{'=' * 70}",
              flush=True)
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(BENCH), env=_env())
        print(f"=== {arm} exited {p.returncode} after {time.time() - t0:.0f}s",
              flush=True)
        if p.returncode != 0:
            print(f"!! {arm} exited non-zero.", file=sys.stderr)
            return p.returncode

        g = _arms(arm, "--gate", "--phase", "post")
        (OUT.parent / f"gate_post__{arm}__cellB_postfix.json").write_text(
            g.stdout, encoding="utf-8")
        print(f"--- {arm} POST GATE rc={g.returncode}")

    print(f"\npost-fix treated run complete -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
