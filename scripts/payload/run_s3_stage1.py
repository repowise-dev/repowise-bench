"""Session B, stage 1: three bare arms, to measure the instrument's own spread.

Everything this workstream has published is n=1 per arm, and the trajectory
spread it reasons against is a single observed difference between two arms
(16.5% on cost, RESULT_STEP2.md section 2). That is one draw, so it is a
terrible estimate of the thing every later power calculation divides by.

These three arms are the same cell, the same tasks, the same order, the same
model and no treatment at all. The only thing that varies between them is the
agent's trajectory, which is exactly the quantity we need. They are also the
bare cell for stage 2 rather than a throwaway measurement.

Design, stop rule and scoring rule, all fixed before spending:
`local-stash/competitive-proof/session-cost-eval/04_SESSION_B_DESIGN.md`.

Sequential on purpose. Three concurrent agent sessions would contend for CPU on
this machine and wall-clock pressure is one of the few things that could plumb
into a trajectory, which would put variance INTO the number whose whole job is
to measure variance.
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
CONFIG = BENCH / "configs" / "session_cost_eval_cellA_rich.yaml"
OUT = (BENCH / "results" / "bakeoff_2026_08" / "session-cost-eval"
       / "sessions_s3.jsonl")

ARM_NAMES = ["c0-bare-r1", "c0-bare-r2", "c0-bare-r3"]
CONDITION = "unenforced"   # a bare arm has no tool to be nudged toward


def _arms(arm: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), str(ARMS), "--config", str(CONFIG), "--arm", arm,
         "--condition", CONDITION, *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(BENCH), env=os.environ.copy())


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for arm in ARM_NAMES:
        # Gated immediately before spending rather than once up front: an arm
        # that fails its gate is rebuilt, not graded, and the cheapest moment to
        # find that out is before the session instead of after it.
        r = _arms(arm, "--prepare", "--gate", "--emit-cmd", "--out", str(OUT))
        if r.returncode != 0:
            print(f"!! {arm} FAILED ITS GATE. Stopping.\n{r.stdout[-1500:]}",
                  file=sys.stderr)
            return 1
        cmd = json.loads(r.stdout)["cmd"]

        print(f"\n{'=' * 70}\n=== {arm}  ({time.strftime('%H:%M:%S')})\n{'=' * 70}",
              flush=True)
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(BENCH), env=os.environ.copy())
        print(f"=== {arm} exited {p.returncode} after {time.time() - t0:.0f}s",
              flush=True)
        if p.returncode != 0:
            print(f"!! {arm} exited non-zero. Stopping rather than running the "
                  f"rest on top of a broken arm.", file=sys.stderr)
            return p.returncode

    print(f"\nstage 1 complete: {len(ARM_NAMES)} bare arms -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
