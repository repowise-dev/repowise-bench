"""Session B, stage 2: one arm on the pre-change build, one on the post-change.

WHAT THIS CAN AND CANNOT ANSWER, fixed before spending.

Stage 1 measured the instrument's own spread at CV 31.9% on cost across three
identical bare sessions ($5.00, $8.22, $4.82). At n=1 per side, THIS RUN CANNOT
RESOLVE COST. Any cost difference it produces, in either direction, is inside
the noise and must not be claimed.

What it can answer is the question #1427 actually targets, and that question is
a COUNT rather than a cost: with the product no longer advertising get_symbol as
a place to start, does the agent still reach for it? Run 1 measured 0 calls when
the tool was served but unnamed against 27-33 when named, monotonic across four
arms, and reproduced on both transports in run 2. An effect of that size is
visible in a single session even through this much noise.

It also produces two transcripts on the current build, which is what the
field-usage and cross-call redundancy analyses run on.

WHY THE `rw-full` SHAPE AND NOT `s2-mcp`. #1427 changed five surfaces that
advertise get_symbol. Under the s2 shape three of them cannot fire (the block is
scrubbed, no repowise hooks so _CORE_TOOLS never emits, get_overview not served)
while the harness itself names get_symbol in both the coaching and the nudge. So
that shape would have measured the payload cuts plus two docstrings against a
prompt that re-advertises the tool. Full reasoning in
`local-stash/competitive-proof/session-cost-eval/04_SESSION_B_DESIGN.md` 4a.

Three separate knobs point at a binary here -- REPOWISE_EXE (the server the
agent calls), --binary-python (where the hooks are read from) and --block-source
(the CLAUDE.md block that build generated). All three are derived from the arm
name below and the gate cross-checks them, because setting two of three leaves
an arm mixing builds with nothing in the output saying so.
"""

from __future__ import annotations

import json
import os
import re
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

BIN_TREE = {
    "rw-pre-r1": Path(r"C:\Users\ragha\Desktop\repowise-s3pre"),
    "rw-post-r1": Path(r"C:\Users\ragha\Desktop\repowise-s3post"),
}
ORDER = ["rw-pre-r1", "rw-post-r1"]
CONDITION = "enforced"


def _key() -> str:
    """The embedder key, from the repo .env.

    The harness refuses to run without it on purpose: without a key `init`
    silently writes an 8-dimension mock index and the server answers a 1536-dim
    index on full-text alone while reporting itself healthy. Read here rather
    than exported into the session, so that refusal keeps working for anyone who
    runs an arm by hand.
    """
    env = Path(r"C:\Users\ragha\Desktop\repowise\.env")
    m = re.search(r"^OPENAI_API_KEY=(.+)$", env.read_text(encoding="utf-8"),
                  re.M)
    return m.group(1).strip().strip('"') if m else ""


def env_for(arm: str) -> dict:
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = _key()
    env["REPOWISE_EXE"] = str(BIN_TREE[arm] / ".venv" / "Scripts" / "repowise.exe")
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "DO_NOT_TRACK": "1"})
    return env


def main() -> int:
    if not _key():
        print("!! no OPENAI_API_KEY in .env. Refusing: indexing and the server "
              "would both degrade silently.", file=sys.stderr)
        return 2

    for arm in ORDER:
        bt = BIN_TREE[arm]
        args = [str(PY), str(ARMS), "--config", str(CONFIG), "--arm", arm,
                "--condition", CONDITION,
                "--binary-python", str(bt / ".venv" / "Scripts" / "python.exe"),
                "--block-source", str(bt / "block_generated.md")]
        r = subprocess.run(args + ["--prepare", "--gate", "--emit-cmd",
                                   "--out", str(OUT)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(BENCH), env=env_for(arm))
        if r.returncode != 0:
            print(f"!! {arm} FAILED ITS GATE. Stopping.\n{r.stdout[-2500:]}",
                  file=sys.stderr)
            return 1
        cmd = json.loads(r.stdout)["cmd"]

        print(f"\n{'=' * 70}\n=== {arm}  ({time.strftime('%H:%M:%S')})\n{'=' * 70}",
              flush=True)
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(BENCH), env=env_for(arm))
        print(f"=== {arm} exited {p.returncode} after {time.time() - t0:.0f}s",
              flush=True)
        if p.returncode != 0:
            print(f"!! {arm} exited non-zero. Stopping.", file=sys.stderr)
            return p.returncode

    print(f"\nstage 2 complete: {len(ORDER)} arms -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
