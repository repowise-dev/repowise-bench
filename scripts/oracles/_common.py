"""Shared plumbing for the session-cost-eval task oracles.

Every oracle is a DETECTOR, so every oracle must be provable. `--self-test`
runs the check against the pinned, unfixed tree and requires it to FAIL: an
oracle that passes on unfixed code cannot distinguish a solved task from an
untouched one, and standing rule 17 says a probe asserting an absence carries a
positive control that must fire or its clean result means nothing.

Usage, from anywhere:
    python <oracle>.py --tree C:\\path\\to\\arm\\tree
    python <oracle>.py --tree ... --self-test   # requires FAIL
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

BENCH_VENV_PY = Path(
    os.environ.get(
        "SESSION_EVAL_PY",
        r"C:\Users\ragha\Desktop\bakeoff\se-venv\Scripts\python.exe",
    )
)

BASELINE_PASSED = 984
BASELINE_FAILED = 0


def parse_args(description: str) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--tree", required=True, help="arm worktree root")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="invert the verdict: require the check to FAIL (positive control)",
    )
    ap.add_argument(
        "--skip-suite",
        action="store_true",
        help="check only the behaviour probe, not the full pytest suite",
    )
    ap.add_argument(
        "--baseline-status",
        help=(
            "file holding `git status --porcelain` taken immediately BEFORE this "
            "task ran. Tasks run in one continuous session, so earlier tasks have "
            "legitimately modified files; a 'nothing else changed' assertion must "
            "be relative to this snapshot, not to the pin."
        ),
    )
    return ap.parse_args()


def baseline_paths(args) -> set:
    """Paths already modified before this task started."""
    if not args.baseline_status:
        return set()
    p = Path(args.baseline_status)
    if not p.is_file():
        return set()
    # utf-8-sig, and an explicit BOM strip on top: PowerShell's `Set-Content
    # -Encoding utf8` writes a BOM on 5.1, and a BOM is not whitespace, so
    # `strip().split()` silently yields "M path" instead of "path" and every
    # baseline entry stops matching. That is the same class of defect as the
    # UTF-16LE hook counter that read five firings as zero.
    out = set()
    for ln in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        ln = ln.lstrip("﻿").strip()
        if ln:
            out.add(ln.split(None, 1)[-1].strip().strip('"'))
    return out


def _env(tree: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(tree)
    # Never let a stray user-level pytest plugin or config change the count.
    env.pop("PYTEST_ADDOPTS", None)
    return env


def run_in_tree(tree: Path, code: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run `code` with the arm's tree first on sys.path."""
    return subprocess.run(
        [str(BENCH_VENV_PY), "-c", code],
        cwd=str(tree),
        env=_env(tree),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def run_suite(tree: Path, timeout: int = 900) -> tuple[bool, str]:
    """Full pytest suite. Returns (green, summary_line)."""
    r = subprocess.run(
        [str(BENCH_VENV_PY), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=str(tree),
        env=_env(tree),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return suite_verdict(r.returncode, r.stdout or "")


def suite_verdict(returncode: int, stdout: str) -> tuple[bool, str]:
    """The ONE definition of "the suite is green". Returns (green, summary).

    Split out of :func:`run_suite` because it was not the only place the rule
    lived. `t04` runs pytest itself (it needs the whole output to look for a
    deprecation warning, not just the summary line) and carried its own copy of
    the green check — a copy written BEFORE defect D1 and never updated with it.
    So the `>= baseline` fix landed in `run_suite` and t04 went on requiring the
    exact 984, which is the same detector that scored a working arm 1 of 6.

    Found on 2026-08-11 by final-state re-grading: three arms failed T04 with
    "warning gone but suite not green: 988 passed, 23 skipped" — a fully green
    suite with four agent-added tests. Fifth dead detector of this run, and the
    only one that was a SURVIVING copy of an already-fixed one.
    """
    tail = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else "(no pytest output)"

    # `>= baseline`, NOT `== baseline`.
    #
    # Requiring the exact count scored three genuinely-solved tasks as failures
    # in the first treatment arm: the agent added tests alongside its fix, the
    # suite read 986 and 987 passed with zero failures, and every "+ suite green"
    # oracle then failed on a green suite. It would have been recorded as the
    # arm collapsing task completion from 5 of 6 to 1 of 6, which is a dramatic
    # and false finding.
    #
    # Adding tests with a fix is legitimate and arguably good agent behaviour. An
    # oracle that punishes it measures conformity, not correctness. What must
    # never pass is a suite with failures or errors, or one that lost coverage,
    # and both are still asserted.
    m = re.search(r"(\d+) passed", summary)
    count = int(m.group(1)) if m else -1
    no_fail = " failed" not in summary and "error" not in summary.lower()
    green = returncode == 0 and no_fail and count >= BASELINE_PASSED
    if count > BASELINE_PASSED:
        summary += f" [+{count - BASELINE_PASSED} tests added by the agent]"
    elif 0 <= count < BASELINE_PASSED:
        summary += f" [LOST {BASELINE_PASSED - count} tests vs baseline]"
    return green, summary


def verdict(name: str, ok: bool, detail: str, args: argparse.Namespace) -> int:
    """Emit the machine-readable verdict line and return the exit code."""
    if args.self_test:
        # The positive control: on unfixed code the check MUST fail.
        good = not ok
        print(
            f"ORACLE {name} SELFTEST "
            f"{'PASS' if good else 'FAIL'} (check_result={'pass' if ok else 'fail'}) :: {detail}"
        )
        if not good:
            print(
                "  the oracle passes on the UNFIXED tree, so it cannot detect "
                "the task being done. Do not run any cell with it."
            )
        return 0 if good else 1

    print(f"ORACLE {name} {'PASS' if ok else 'FAIL'} :: {detail}")
    return 0 if ok else 1


def main_wrapper(name: str, description: str, check) -> None:
    args = parse_args(description)
    tree = Path(args.tree)
    if not (tree / "rich").is_dir():
        print(f"ORACLE {name} FAIL :: {tree} is not a rich checkout")
        sys.exit(1)
    ok, detail = check(tree, args)
    sys.exit(verdict(name, ok, detail, args))
