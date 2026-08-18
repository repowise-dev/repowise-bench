"""Verify a session-eval task's test oracle in BOTH directions.

Standing rule 17: prove the detector before recording anything. An oracle that
passes at the pin cannot tell a completed task from an untouched one, and an
oracle that fails even with the gold patch applied marks every arm incomplete.
Both failure modes produce a full-looking results table.

For each candidate fix commit this applies the TEST half of the commit to the
pinned tree and runs it (must FAIL), then applies the SOURCE half and runs again
(must PASS), then reverts. A candidate that does not do both is rejected and the
rejection is printed, never silently dropped.

Usage:
    python verify_oracles.py --tree <path> --python <exe> <sha> [<sha> ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TEST_PREFIX = "tests/"


def _git(tree: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(tree), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def _files(tree: Path, sha: str) -> tuple[list[str], list[str]]:
    """(test paths, source paths) touched by this commit."""
    names = [p for p in _git(tree, "show", "--name-only", "--format=", sha).splitlines() if p.strip()]
    tests = [p for p in names if p.startswith(TEST_PREFIX)]
    src = [p for p in names if not p.startswith(TEST_PREFIX)]
    return tests, src


def _apply_half(tree: Path, sha: str, paths: list[str]) -> bool:
    if not paths:
        return False
    diff = subprocess.run(["git", "-C", str(tree), "show", sha, "--", *paths],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout
    if not diff.strip():
        return False
    r = subprocess.run(["git", "-C", str(tree), "apply", "-"],
                       input=diff, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode == 0


def _pytest(tree: Path, python: str, targets: list[str]) -> tuple[bool, str]:
    """(all passed, last line of output)."""
    r = subprocess.run([python, "-m", "pytest", *targets, "-q", "--no-header"],
                       cwd=str(tree), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    return r.returncode == 0, (tail[-1] if tail else (r.stderr or "")[-200:])


def _reset(tree: Path) -> None:
    _git(tree, "checkout", "--", ".")


def verify(tree: Path, python: str, sha: str) -> dict:
    out: dict = {"sha": sha, "subject": _git(tree, "log", "-1", "--format=%s", sha).strip()}
    _reset(tree)
    tests, src = _files(tree, sha)
    out["test_files"] = tests
    out["source_files"] = src
    if not tests or not src:
        out["verdict"] = "REJECT"
        out["reason"] = "commit has no test half or no source half"
        return out

    if not _apply_half(tree, sha, tests):
        _reset(tree)
        out["verdict"] = "REJECT"
        out["reason"] = "test half does not apply onto the pinned tree"
        return out

    passed_before, tail_before = _pytest(tree, python, tests)
    out["before"] = tail_before
    if passed_before:
        _reset(tree)
        out["verdict"] = "REJECT"
        out["reason"] = ("NEGATIVE CONTROL FAILED: the test passes at the pin, so it "
                         "cannot discriminate a completed task from an untouched one")
        return out

    if not _apply_half(tree, sha, src):
        _reset(tree)
        out["verdict"] = "REJECT"
        out["reason"] = "source half does not apply onto the pinned tree"
        return out

    passed_after, tail_after = _pytest(tree, python, tests)
    out["after"] = tail_after
    _reset(tree)
    if not passed_after:
        out["verdict"] = "REJECT"
        out["reason"] = ("POSITIVE CONTROL FAILED: the test still fails with the gold "
                         "patch applied, so every arm would score incomplete")
        return out

    out["verdict"] = "ACCEPT"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("shas", nargs="+")
    a = ap.parse_args()

    tree = Path(a.tree)
    results = []
    for sha in a.shas:
        r = verify(tree, a.python, sha)
        results.append(r)
        mark = "ACCEPT" if r["verdict"] == "ACCEPT" else "REJECT"
        print(f"[{mark}] {sha} {r['subject'][:70]}")
        if r["verdict"] == "ACCEPT":
            print(f"         before: {r['before']}")
            print(f"         after:  {r['after']}")
        else:
            print(f"         {r['reason']}")
        sys.stdout.flush()

    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    accepted = sum(1 for r in results if r["verdict"] == "ACCEPT")
    print(f"\n{accepted} of {len(results)} candidates verified in both directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
