"""Cell B SCORED run: 3 sessions x 2 arms x N reps on hermes-agent, post-#1443.

Design deltas from `07_SESSION_C_DESIGN.md`, stated rather than buried
--------------------------------------------------------------------
* **10 tasks, not 12.** Three of the design's edit slots were rejected with
  evidence (#83651 no assertable property + TypeScript; #83845 POSIX-only, does
  not reproduce on this host; #83792 ships a characterisation test asserting the
  CURRENT behaviour and its correct fix is under-specified). #84289 replaces one
  of them. Three further candidates were attempted and rejected this pass
  (#83797 environmental, #83122 and #83455 both under-specified fixes), which
  keeps the running conversion rate near the 3-of-5 already recorded. The
  remaining two slots are dropped rather than filled with a task invented off
  the tracker, which would break the first non-negotiable rule.
  Resulting mix: 3 retrieval, 2 architecture, 4 edit, 1 mechanical.
* **n=4, not 6.** Session B measured that the cost endpoint needs n~14-25 per
  cell; neither 4 nor 6 reaches it, so paying for 6 buys precision on a question
  that stays unresolvable. What n=4 buys is what Session B said it buys:
  direction, the one-sided "we do not reach a saving" claim, and a real sigma.
  COMPLETION is the primary endpoint here and it is task-level: 5 oracle-backed
  tasks x 4 reps = 20 graded instances per arm.

**ARM ORDER IS COUNTERBALANCED**, which no previous run in this arc did. Odd
reps run bare first, even reps run treated first. With n=4 that is 2 of each per
session, so any order effect (API load drift, machine state, cache warming
across adjacent runs) falls equally on both arms instead of loading onto one.

Machine safety: strictly sequential, one agent at a time. No indexing happens
here -- treated trees are stamped from an already-built index by
`clone_hermes_index.py`, so the 13.3 GB `init` peak is not re-triggered.
"""

from __future__ import annotations

import argparse
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
       / "sessions_cellB_scored.jsonl")

REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
BINARY_PYTHON = REPOWISE_ROOT / ".venv" / "Scripts" / "python.exe"
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"

# Frozen. Order within a session is part of the design.
SESSIONS = {
    1: ["B01", "B02", "B03", "B04"],
    2: ["B05", "B06", "B07"],
    3: ["B08", "B09", "B10"],
}
REPS = 4
CONDITION = "unenforced"

# Hard ceiling. The arc's standing budget is $350 and ~$172 is spent; this run
# is priced at ~$54 from measured per-task figures. If it runs away, stop.
BUDGET_CEILING_USD = 90.0


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


def spent_so_far() -> float:
    if not OUT.exists():
        return 0.0
    total = 0.0
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("task_id"):
            total += float(row.get("cost_usd") or 0.0)
    return total


def arm_order(rep: int) -> list[str]:
    """Counterbalanced: odd reps bare-first, even reps treated-first."""
    return ["bare", "rw"] if rep % 2 == 1 else ["rw", "bare"]


def plan() -> list[tuple]:
    out = []
    for sess in sorted(SESSIONS):
        for rep in range(1, REPS + 1):
            for kind in arm_order(rep):
                out.append((sess, rep, kind, f"cb-{kind}-s{sess}r{rep}",
                            SESSIONS[sess]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the schedule and exit without spending")
    ap.add_argument("--only-session", type=int, default=0)
    args = ap.parse_args()

    schedule = plan()
    if args.only_session:
        schedule = [s for s in schedule if s[0] == args.only_session]

    print(f"cell B scored: {len(SESSIONS)} sessions x 2 arms x {REPS} reps "
          f"= {len(schedule)} arm-runs")
    print(f"condition={CONDITION}  out={OUT.name}")
    print()
    for sess, rep, kind, arm, ids in schedule:
        first = " (bare first)" if arm_order(rep)[0] == "bare" else " (treated first)"
        print(f"  s{sess} r{rep} {kind:4s}  {arm:18s}  {','.join(ids)}"
              f"{first if kind == arm_order(rep)[0] else ''}")
    if args.dry_run:
        print("\ndry run: nothing spent")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    for i, (sess, rep, kind, arm, ids) in enumerate(schedule, 1):
        spent = spent_so_far()
        if spent >= BUDGET_CEILING_USD:
            print(f"\n!! BUDGET CEILING reached (${spent:.2f} >= "
                  f"${BUDGET_CEILING_USD:.2f}). Stopping before {arm}.",
                  file=sys.stderr)
            return 4

        r = _arms(arm, "--prepare", "--gate", "--phase", "pre",
                  "--emit-cmd", "--out", str(OUT))
        if r.returncode != 0:
            print(f"!! {arm} FAILED ITS PRE GATE. Stopping rather than grading "
                  f"an arm that is not what it claims to be.\n"
                  f"{r.stdout[-2500:]}\n{r.stderr[-1500:]}", file=sys.stderr)
            return 1
        cmd = json.loads(r.stdout)["cmd"] + ["--task-ids", ",".join(ids)]

        el = (time.time() - t_start) / 60.0
        print(f"\n{'=' * 70}\n=== [{i}/{len(schedule)}] {arm}  s{sess} r{rep}  "
              f"{len(ids)} tasks  ({time.strftime('%H:%M:%S')}, "
              f"{el:.0f} min in, ${spent:.2f} spent)\n{'=' * 70}", flush=True)

        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(BENCH), env=_env())
        print(f"=== {arm} exited {p.returncode} after {time.time() - t0:.0f}s",
              flush=True)
        if p.returncode != 0:
            print(f"!! {arm} exited non-zero. Stopping rather than running the "
                  f"rest on top of a broken arm.", file=sys.stderr)
            return p.returncode

        g = _arms(arm, "--gate", "--phase", "post")
        (OUT.parent / f"gate_post__{arm}__cellB_scored.json").write_text(
            g.stdout, encoding="utf-8")
        print(f"--- {arm} POST GATE rc={g.returncode}")

    mins = (time.time() - t_start) / 60.0
    print(f"\nscored cell complete: {len(schedule)} arm-runs, "
          f"{mins:.0f} min, ${spent_so_far():.2f} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
