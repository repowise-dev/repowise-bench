"""Materialise the corpus from `corpus.lock` at exactly its recorded pins.

This is the script that makes the benchmark reproducible by somebody who is not
us. Before it existed, `test-repos/` was ninety-two directories on one laptop
and no reader could obtain the tree any published number was measured over.

    python graph/corpus/fetch.py --repos gitleaks,zod        # named
    python graph/corpus/fetch.py --kinds library,application # the pinned 30
    python graph/corpus/fetch.py --check                     # verify, clone nothing

Three rules, and they are the whole point:

* A repository is checked out at its **pin**, never at a branch. A branch moves
  and a moved branch is a different measurement wearing the same name.
* An existing directory at the wrong commit is **refused, never fixed**. The
  frozen CodeGraph indexes under `test-repos/<repo>/.codegraph/` are bytes that
  published baselines reconcile against, and a helpful `git checkout` would
  destroy the reconciliation silently. Refusing costs a message; repairing
  costs a re-audit nobody knows is needed.
* A repository the lock marks `usable: false` is not fetched into place over
  the existing one either. Fix it deliberately with `--force-reclone`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
LOCK = Path(__file__).parent / "corpus.lock"
TEST_REPOS = BENCH.parent / "test-repos"


def run(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    out = subprocess.run(
        args, capture_output=True, text=True, cwd=str(cwd) if cwd else None,
        timeout=1800, check=False,
    )
    return out.returncode, (out.stdout + out.stderr).strip()


def head_of(path: Path) -> str | None:
    code, out = run("git", "-C", str(path), "rev-parse", "HEAD")
    return out if code == 0 else None


def clone_at_pin(url: str, pin: str, dest: Path) -> tuple[bool, str]:
    """Clone and hard-pin. Full history, because a shallow clone cannot reach
    an arbitrary older commit and every pin here is arbitrary and older."""
    code, out = run("git", "clone", "--quiet", url, str(dest))
    if code != 0:
        return False, f"clone failed: {out[:200]}"
    code, out = run("git", "-C", str(dest), "checkout", "--quiet", pin)
    if code != 0:
        return False, f"pin {pin[:8]} not reachable: {out[:200]}"
    got = head_of(dest)
    if got != pin:
        return False, f"checked out {got} but lock says {pin}"
    return True, f"cloned at {pin[:8]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", default=str(LOCK))
    ap.add_argument("--test-repos", default=str(TEST_REPOS))
    ap.add_argument("--repos", default="", help="comma-separated names")
    ap.add_argument("--kinds", default="", help="library,application,framework")
    ap.add_argument("--languages", default="", help="comma-separated")
    ap.add_argument("--check", action="store_true", help="report only, clone nothing")
    ap.add_argument(
        "--force-reclone", action="store_true",
        help="delete and re-clone a directory sitting at the wrong commit. "
             "Destroys any frozen peer index under it.",
    )
    args = ap.parse_args()

    doc = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    rows = doc["repos"]
    if args.repos:
        want = set(args.repos.split(","))
        rows = [r for r in rows if r["name"] in want]
    if args.kinds:
        want = set(args.kinds.split(","))
        rows = [r for r in rows if r.get("kind") in want]
    if args.languages:
        want = set(args.languages.split(","))
        rows = [r for r in rows if r.get("language") in want]
    if not rows:
        print("no lock entries match those filters", file=sys.stderr)
        return 2

    root = Path(args.test_repos).resolve()
    root.mkdir(parents=True, exist_ok=True)
    tally = {"ok": 0, "cloned": 0, "refused": 0, "failed": 0, "missing_url": 0}

    for row in sorted(rows, key=lambda r: r["name"].lower()):
        name, pin, url = row["name"], row["pin"], row.get("url")
        dest = root / name
        if dest.exists():
            got = head_of(dest)
            if got == pin:
                flag = "" if row.get("usable", True) else "  (lock says usable: false)"
                print(f"  ok       {name:24s} {pin[:8]}{flag}")
                tally["ok"] += 1
                continue
            if not args.force_reclone:
                print(
                    f"  REFUSED  {name:24s} on {(got or 'unknown')[:8]}, "
                    f"lock says {pin[:8]}. Not touching it; pass --force-reclone "
                    f"if you mean to discard what is there."
                )
                tally["refused"] += 1
                continue
            if args.check:
                print(f"  would reclone {name}")
                continue
            shutil.rmtree(dest, ignore_errors=True)
        if args.check:
            print(f"  missing  {name:24s} would clone at {pin[:8]}")
            continue
        if not url:
            print(f"  NO URL   {name:24s} lock has no remote; cannot fetch")
            tally["missing_url"] += 1
            continue
        ok, msg = clone_at_pin(url, pin, dest)
        print(f"  {'cloned ' if ok else 'FAILED '} {name:24s} {msg}")
        tally["cloned" if ok else "failed"] += 1

    print("\n" + "  ".join(f"{k}={v}" for k, v in tally.items()))
    return 1 if tally["failed"] or tally["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
