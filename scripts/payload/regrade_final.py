"""Final-state re-grading of every session-cost arm — the debt RESULT.md owes.

Section 5 defect 4: session-shaped grading samples a MOVING tree. An arm that
broke tests mid-session and repaired them later was graded on the broken window
and scored 1 of 6 instead of 5 of 6. Only `rw-cli` was ever re-graded on final
state. This closes that for every arm, at zero agent spend, by running the six
oracles against the tree each arm left behind.

T10's "nothing else changed" assertion needs a pre-task `git status` snapshot
that final-state grading cannot reconstruct, so it is run WITHOUT one and its
verdict is reported as `no-baseline` rather than quietly graded against the pin.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(r"C:\Users\ragha\Desktop\repowise\repowise-bench")
ORACLES = BENCH / "scripts" / "oracles"
TREES = Path(r"C:\Users\ragha\Desktop\bakeoff")
PY = Path(r"C:\Users\ragha\Desktop\bakeoff\se-venv\Scripts\python.exe")

ARMS = ["c0-bare", "rw-block", "rw-mcp", "rw-hooks", "rw-full", "rw-full-unenf",
        "codegraph", "rw-lean3", "rw-noanswer", "rw-lean-nosym", "rw-answeronly",
        "rw-cli"]
TREE_ALIAS = {"c0-bare": "se-c0bare-rich"}

ORACLE_FILES = sorted(p for p in ORACLES.glob("t*.py"))


def tree_for(arm: str) -> Path:
    return TREES / TREE_ALIAS.get(arm, f"se-{arm}-rich")


def main() -> None:
    results = {}
    for arm in ARMS:
        tree = tree_for(arm)
        if not tree.is_dir():
            results[arm] = {"error": "tree missing"}
            print(f"{arm:<16} TREE MISSING {tree}", flush=True)
            continue
        per = {}
        for oracle in ORACLE_FILES:
            cmd = [str(PY), str(oracle), "--tree", str(tree)]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
            line = (r.stdout or "").strip().splitlines()
            line = line[-1] if line else (r.stderr or "").strip()[-200:]
            passed = r.returncode == 0
            if oracle.stem.startswith("t10"):
                per[oracle.stem] = {"passed": passed, "note": "no-baseline",
                                    "detail": line}
            else:
                per[oracle.stem] = {"passed": passed, "detail": line}
        n_pass = sum(1 for v in per.values() if v["passed"])
        results[arm] = {"tree": str(tree), "passed": n_pass,
                        "of": len(per), "oracles": per}
        flags = " ".join(("+" if v["passed"] else "-") + k[:3]
                         for k, v in per.items())
        print(f"{arm:<16} {n_pass}/{len(per)}   {flags}", flush=True)

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("regrade_final.json")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
