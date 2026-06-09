#!/usr/bin/env python3
"""indexability_probe.py — can the git layer walk these histories?

The openclaw lesson: a 40k+-commit history wedges a full ``git log --numstat``
-style indexing walk. For every corpus clone this probe times the two walks an
indexer needs — full history vs a windowed bound — and projects the win:

  * ``git log --name-only --no-merges``      (full reachable history)
  * ``git log --name-only --no-merges --since=<window>``

``--name-only`` (tree-level) rather than ``--numstat``: the corpus clones are
blobless, and a numstat walk would lazy-fetch every historical blob over the
network — both unmeasurable and abusive. Tree walks run near-offline
(only in-tree .gitattributes blobs are lazy-fetched, once); a real indexer's
numstat adds blob-diff cost on top, which the windowed bound shrinks
proportionally.

Output: <out>/indexability.json + a markdown table on stdout. This is the
bench-side prototype for a REPOWISE_GIT_COMMIT_WINDOW-style bound (plan
Phase 1C / Phase 3 ship 3).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

WINDOW = "2025-06-01"


def timed_log(repo: Path, *extra: str, _retry: bool = True) -> tuple[float, int, int]:
    """Returns (seconds, n_commits, n_file_touch_lines) for a name-only walk."""
    t0 = time.time()
    # Lazy fetch stays ON: name-only diffs consult in-tree .gitattributes
    # blobs (diff drivers), which a blobless clone must fetch — a few dozen
    # objects per repo, cached after the first walk. With GIT_NO_LAZY_FETCH=1
    # the walk aborts at the first attributes lookup.
    p = subprocess.Popen(["git", "log", "--name-only", "--no-merges",
                          "--format=%x01%H", *extra],
                         cwd=str(repo), stdout=subprocess.PIPE)
    n_commits = n_lines = 0
    assert p.stdout is not None
    for raw in p.stdout:
        if raw.startswith(b"\x01"):
            n_commits += 1
        elif raw.strip():
            n_lines += 1
    p.wait()
    if p.returncode != 0 and _retry:
        # a lazy fetch can fail transiently and abort the walk mid-stream;
        # fetched objects are kept, so one retry usually completes
        return timed_log(repo, *extra, _retry=False)
    if p.returncode != 0:
        return round(time.time() - t0, 1), -n_commits, n_lines  # negative = truncated
    return round(time.time() - t0, 1), n_commits, n_lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    report = json.loads((args.repos_dir / "clone_report.json").read_text(encoding="utf-8"))
    repos = [r for r in report["repos"] if r["status"] != "FAILED"]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        repos = [r for r in repos if r["dir"] in keep]

    rows = []
    for rec in sorted(repos, key=lambda r: -(r.get("commits_reachable") or 0)):
        repo = args.repos_dir / rec["dir"]
        full_s, full_c, full_l = timed_log(repo)
        win_s, win_c, win_l = timed_log(repo, f"--since={WINDOW}")
        rows.append({"repo": rec["repo"], "bound": rec["bound"],
                     "full_seconds": full_s, "full_commits": full_c,
                     "full_file_touches": full_l,
                     "window_seconds": win_s, "window_commits": win_c,
                     "window_file_touches": win_l})
        print(f"{rec['repo']:35s} full {full_c:7d} commits {full_s:7.1f}s | "
              f"window {win_c:7d} commits {win_s:6.1f}s", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"generated": datetime.now(timezone.utc).isoformat(),
         "window_since": WINDOW, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
