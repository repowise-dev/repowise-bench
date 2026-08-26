"""Drive the six step-2 arms in order, each in its own process.

Python rather than PowerShell on purpose: PowerShell splitting a multi-line
argument on its embedded quotes already cost this run one arm, and the coaching
now travels as a file for the same reason. Nothing here builds a command string
that a shell has to re-parse.

Order is the pre-registration's order, so that a run cut short leaves the arms
that answer step 2's actual question on disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(r"C:\Users\ragha\Desktop\repowise\repowise-bench")
PY = Path(r"C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe")
ARMS_S2 = BENCH / "harness" / "session_arms_s2.py"
OUT = (BENCH / "results" / "bakeoff_2026_08" / "session-cost-eval"
       / "sessions_s2.jsonl")

ORDER = ["s2-cli-full", "s2-mcp", "s2-cli-trim",
         "s2-cli-unenf", "s2-mcp-unenf", "s2-c0bare"]


def emit_cmd(arm: str) -> list:
    r = subprocess.run(
        [str(PY), str(ARMS_S2), "--arm", arm, "--emit-cmd", "--out", str(OUT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(BENCH), env=os.environ.copy())
    if r.returncode != 0:
        raise RuntimeError(f"emit-cmd failed for {arm}: {r.stderr[-400:]}")
    return json.loads((r.stdout or "").strip().splitlines()[-1])


def gate(arm: str) -> bool:
    r = subprocess.run(
        [str(PY), str(ARMS_S2), "--arm", arm, "--gate"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(BENCH), env=os.environ.copy())
    return r.returncode == 0


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("!! OPENAI_API_KEY not set. The MCP server would query a vector "
              "index on full-text alone while reporting itself healthy (A9), "
              "and the CLI arm's `ask` would fail. Refusing.", file=sys.stderr)
        return 2

    for arm in ORDER:
        # Re-gated immediately before spending, not once at build time: an arm
        # that fails its gate is rebuilt, not graded, and the cheapest moment to
        # learn that is before the session rather than after it.
        if not gate(arm):
            print(f"!! {arm} FAILED ITS GATE. Stopping.", file=sys.stderr)
            return 1
        cmd = emit_cmd(arm)
        print(f"\n{'=' * 70}\n=== {arm}  ({time.strftime('%H:%M:%S')})\n{'=' * 70}",
              flush=True)
        t0 = time.time()
        r = subprocess.run(cmd, cwd=str(BENCH), env=os.environ.copy())
        print(f"=== {arm} exited {r.returncode} after "
              f"{time.time() - t0:.0f}s", flush=True)
        if r.returncode != 0:
            print(f"!! {arm} exited non-zero. Stopping rather than running the "
                  f"rest on top of a broken arm.", file=sys.stderr)
            return r.returncode

    print("\nall six arms complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
