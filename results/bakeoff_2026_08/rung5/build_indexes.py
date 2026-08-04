"""Rung 5: build one index per arm, each in its OWN copy of the tree.

Why not reuse rung 4's `smoke_matrix.py`: that script clears **every** arm's
artifact directory before each cell, which is the correct isolation for a timed
build (finding E3) but destroys the indexes an earlier cell produced. Rung 5
needs all four indexes to exist at once, so the two requirements are in direct
tension and the resolution is one worktree per arm rather than one shared tree.

That is also strictly better for retrieval fairness: no arm can surface another
arm's artifact files as results, which a shared tree would allow.

Not timed. Rung 5 scores recall and MRR, so these builds may run under
contention without invalidating anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TREES = Path(r"C:\Users\ragha\Desktop\bakeoff")
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"
NPM_BIN = Path(os.environ["APPDATA"]) / "npm"
UV_BIN = Path.home() / ".local" / "bin"
OUT = Path(__file__).resolve().parent
LOGS = OUT / "logs"

BUILDS = {
    "repowise": lambda t: [
        str(REPOWISE_EXE), "init", "--no-prose", "--embedder", "openai",
        "--max-file-pages", "0", "--no-workspace", "--no-editor-setup", "--yes",
    ],
    "codegraph": lambda t: [str(NPM_BIN / "codegraph.cmd"), "init", str(t)],
    "crg": lambda t: [
        str(UV_BIN / "code-review-graph.exe"), "build", "--repo", str(t)
    ],
    "graphify": lambda t: [str(UV_BIN / "graphify.exe"), "update", str(t)],
}


def main() -> int:
    arms = sys.argv[1:] or list(BUILDS)
    env = dict(os.environ)
    env.update(
        {
            "DO_NOT_TRACK": "1",
            "REPOWISE_SKIP_EDITOR_SETUP": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    LOGS.mkdir(parents=True, exist_ok=True)
    rows = []
    out = OUT / "index_builds.json"
    if out.exists():
        rows = json.loads(out.read_text())

    for arm in arms:
        tree = TREES / f"r5-{arm}"
        argv = BUILDS[arm](tree)
        print(f"[{arm}] {' '.join(argv)}", flush=True)
        t0 = time.time()
        p = subprocess.run(
            argv, cwd=str(tree), env=env, capture_output=True,
            text=True, errors="replace", timeout=3 * 60 * 60,
        )
        el = round(time.time() - t0, 1)
        (LOGS / f"{arm}.log").write_text(
            f"$ {' '.join(argv)}\n\n--- stdout ---\n{p.stdout}\n"
            f"--- stderr ---\n{p.stderr}\n",
            encoding="utf-8",
        )
        row = {"arm": arm, "tree": str(tree), "rc": p.returncode, "seconds": el}
        rows = [r for r in rows if r["arm"] != arm]
        rows.append(row)
        out.write_text(json.dumps(rows, indent=2))
        print(f"    -> rc={p.returncode} {el}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
