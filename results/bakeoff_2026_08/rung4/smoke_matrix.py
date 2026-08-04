"""Rung 4 install + index smoke matrix.

Runs each indexing arm against each pinned repo, serially, and records wall
clock, exit code, artifact size and a per-arm stat line. A failed arm is a
result: it is recorded and the matrix continues.

Serial by design. Finding E1: concurrent repowise process pools inflate every
timed build, so a parallel matrix would not reproduce. `--preflight` refuses to
start while another repowise pool is alive.

Usage:
    python smoke_matrix.py [--arms a,b] [--repos django,svelte,cli] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TREES = Path(r"C:\Users\ragha\Desktop\bakeoff")
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"
OUT = Path(__file__).resolve().parent
LOGS = OUT / "logs"

# On Windows, npm installs a .cmd shim and uv installs a real .exe. subprocess
# with shell=False cannot exec a bare `codegraph`, so name the wrapper.
NPM_BIN = Path(os.environ["APPDATA"]) / "npm"
UV_BIN = Path.home() / ".local" / "bin"

# Every pin is a real ContextBench base_commit, so each index is Layer A
# reusable. Rule: newest base_commit per repo, except django, which keeps
# 838e432e for comparability with the existing cost basis.
# svelte is retained only as evidence for finding A6 (no .svelte grammar); it is
# dropped from the bake-off and must not appear in a comparison table.
REPOS = {
    "django": "838e432e3e5519c5383d12018e6c78f8ec7833c1",
    "cli": "8288011149e71d5658b80ebef393522ba2d0e7cc",
    "mui": "b8a28e13dc0821314700f1aefc9f8e1134cf9034",
    "svelte": "a1f371e78656f951ad75945493a798716ecc92c4",
    # rung 5 target: repowise indexing itself, staged as a worktree so no
    # .repowise-workspace.yaml sits above it (finding D1b).
    "repowise-self": "234b27980a0421f7e7553372da1ed1ca036f400b",
}

# The mandated flag set. --no-workspace defeats the workspace-hijack trap;
# --no-editor-setup plus REPOWISE_SKIP_EDITOR_SETUP=1 stops an unguarded init
# repointing the one global repowise MCP key at a bench repo.
REPOWISE_FLAGS = [
    "--no-prose",
    "--embedder",
    "openai",
    "--max-file-pages",
    "0",
    "--no-workspace",
    "--no-editor-setup",
    "--yes",
]

ARMS = {
    # arm -> (argv builder, artifact dir relative to the repo tree)
    "repowise-noprose": (
        lambda repo: [str(REPOWISE_EXE), "init", *REPOWISE_FLAGS],
        ".repowise",
    ),
    "codegraph": (
        lambda repo: [str(NPM_BIN / "codegraph.cmd"), "init", str(TREES / repo)],
        ".codegraph",
    ),
    "code-review-graph": (
        lambda repo: [
            str(UV_BIN / "code-review-graph.exe"),
            "build",
            "--repo",
            str(TREES / repo),
        ],
        ".code-review-graph",
    ),
    # `update` is graphify's explicitly no-LLM code path ("re-extract code files
    # and update the graph (no LLM needed)"). `extract` would route docs through
    # a paid semantic pass, which rung 4 has no budget for.
    "graphify": (
        lambda repo: [str(UV_BIN / "graphify.exe"), "update", str(TREES / repo)],
        "graphify-out",
    ),
}


def dir_size_mb(p: Path) -> float | None:
    if not p.exists():
        return None
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return round(total / 1024 / 1024, 2)


def preflight() -> dict:
    """Finding E1: refuse to start a timed build under repowise contention.

    Matching on the string 'repowise' alone is useless here: every process this
    session launches carries the repo path in its command line, so a naive
    match reported 19 "live pools" when none existed. The contaminating thing
    E1 actually described is a multiprocessing worker pool, whose children run
    `from multiprocessing.spawn import spawn_main`. Count those, and report
    overall CPU load alongside so a contended build is visible after the fact
    rather than only preventable before it.
    """
    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            # Name-gate on python.exe: the query string itself contains
            # 'spawn_main', so an unfiltered CommandLine match finds this very
            # PowerShell process and the guard refuses to run against itself.
            "$w = @(Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -like 'python*.exe' -and "
            "$_.CommandLine -like '*spawn_main*' }); "
            "$c = (Get-CimInstance Win32_Processor "
            "| Measure-Object -Property LoadPercentage -Average).Average; "
            "[pscustomobject]@{workers=$w.Count; load=$c} | ConvertTo-Json",
        ],
        capture_output=True,
        text=True,
    )
    try:
        info = json.loads(ps.stdout)
    except (ValueError, TypeError):
        info = {"workers": None, "load": None, "raw": ps.stdout[-400:]}
    return info


def run_cell(arm: str, repo: str, dry: bool) -> dict:
    build, artifact_rel = ARMS[arm]
    tree = TREES / repo
    artifact = tree / artifact_rel
    argv = build(repo)

    # Every cell starts from a tree carrying NO arm's artifacts, not merely
    # none of its own. Removing only the current arm's output silently lets a
    # later arm index an earlier arm's index: graphify parsed 6,809 svelte files
    # against codegraph's 6,285 because it had walked into `.repowise/jobs/`.
    # That biases toward whichever arm runs first, which was ours, so the fix
    # costs our own arm its advantage.
    if not dry:
        for _, other_rel in ARMS.values():
            shutil.rmtree(tree / other_rel, ignore_errors=True)

    env = dict(os.environ)
    env["DO_NOT_TRACK"] = "1"
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{arm}__{repo}.log"

    row = {
        "arm": arm,
        "repo": repo,
        "commit": REPOS[repo],
        "argv": argv,
        "cwd": str(tree),
        "log": str(log_path),
    }
    if dry:
        row["status"] = "dry-run"
        return row

    print(f"[{arm} / {repo}] {' '.join(argv)}", flush=True)
    t0 = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(tree),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=3 * 60 * 60,
            shell=False,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as e:
        rc, out, err, timed_out = None, (e.stdout or ""), (e.stderr or ""), True
    except FileNotFoundError as e:
        rc, out, err, timed_out = None, "", f"FileNotFoundError: {e}", False
    elapsed = round(time.time() - t0, 1)

    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    log_path.write_text(
        f"$ {' '.join(argv)}\n(cwd={tree})\n\n--- stdout ---\n{out}\n"
        f"--- stderr ---\n{err}\n",
        encoding="utf-8",
    )

    row.update(
        {
            "returncode": rc,
            "timed_out": timed_out,
            "seconds": elapsed,
            "artifact": str(artifact),
            "artifact_mb": dir_size_mb(artifact),
            "artifact_exists": artifact.exists(),
            "other_arm_artifacts_present": sorted(
                rel
                for _, rel in ARMS.values()
                if rel != artifact_rel and (tree / rel).exists()
            ),
            "status": "ok" if rc == 0 and artifact.exists() else "FAIL",
            "stdout_tail": out[-2000:],
            "stderr_tail": err[-2000:],
        }
    )
    print(
        f"    -> {row['status']} rc={rc} {elapsed}s artifact={row['artifact_mb']}MB",
        flush=True,
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--repos", default=",".join(REPOS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--out", default=str(OUT / "smoke_matrix.json"))
    a = ap.parse_args()

    pre = None
    if not a.skip_preflight and not a.dry_run:
        pre = preflight()
        print(
            f"preflight: {pre.get('workers')} multiprocessing workers alive, "
            f"cpu load {pre.get('load')}%"
        )
        if pre.get("workers"):
            print(
                "REFUSING: a worker pool is alive; timings would be contended "
                "(finding E1). Pass --skip-preflight to override.",
                file=sys.stderr,
            )
            return 2

    rows = []
    out_path = Path(a.out)
    if out_path.exists():
        rows = json.loads(out_path.read_text())

    for repo in [r for r in a.repos.split(",") if r]:
        for arm in [x for x in a.arms.split(",") if x]:
            row = run_cell(arm, repo, a.dry_run)
            if not a.dry_run:
                row["preflight_at_start"] = pre
                row["preflight_at_end"] = preflight()
            rows = [r for r in rows if not (r["arm"] == arm and r["repo"] == repo)]
            rows.append(row)
            out_path.write_text(json.dumps(rows, indent=2))

    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
