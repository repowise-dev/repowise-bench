#!/usr/bin/env python3
"""agent_szz_induction.py — SZZ bug-inducing walk over the agent-era corpus.

For every repo in the main analysis pool, blame the lines each window fix
commit deleted/modified back to the commits that last wrote them (B-SZZ), with
the AG-SZZ refinements (skip blank/comment/punctuation lines; drop inducers
that are themselves fixes). The result maps every fix to its inducing commits,
so the induction analysis can ask: which *window* commits induced a defect —
by authorship tier, under raw vs gated fix sets.

Requires blobs (run ``git backfill`` on the blobless clones first). Blame is
run at the fix's parent without ``-C`` (copy detection is 5-10x slower and
standard SZZ does not require it). Fixes touching more than ``MAX_FIX_FILES``
code files are skipped as bulk refactors (counted in stats).

Inputs: the clone dirs + per-commit label records (``agent_defect_labels.py``).
Output: ``<out-dir>/<name>.json`` — per-fix inducing sets (B and AG) plus
per-commit line churn (added/deleted over code files) for the Kamei controls.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_szz_induction.py \
        --repos-dir <data>/agent-repos --labels-dir <data>/agent-repos/_labels \
        --out-dir <data>/agent-repos/_szz [--only name1,name2] [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.szz import _ext, _is_cosmetic_line  # noqa: E402

WINDOW_START = "2025-06-01"
MAX_FIX_FILES = 50  # a "fix" touching more code files is a bulk refactor

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
_SHA_LINE_RE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)")

# default pool = the PASS repos (corpus memo lock + backfill addenda)
PASS_POOL = ["omi", "dyad", "prefect", "novu", "Umbraco-CMS", "mattermost",
             "grafana", "airbyte", "homebrew-core", "metabase", "strapi",
             "shiki", "nethermind", "dart"]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}... failed: {r.stderr[-200:]}")
    return r.stdout


def _deleted_ranges(repo: Path, parent: str, sha: str, file: str) -> list[tuple[int, int]]:
    out = _git(["diff", "--unified=0", "--no-color", parent, sha, "--", file], repo)
    ranges = []
    for line in out.split("\n"):
        m = _HUNK_RE.match(line)
        if m:
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count > 0:
                ranges.append((int(m.group(1)), count))
    return ranges


def _blame_ranges(repo: Path, parent: str, file: str,
                  ranges: list[tuple[int, int]], n_lines: int) -> list[tuple[int, str]]:
    """(parent_lineno, blamed_sha) for all ranges in ONE blame call."""
    args = ["blame", "-w", "--line-porcelain"]
    for start, count in ranges:
        end = min(start + count - 1, n_lines) if n_lines else start + count - 1
        if end >= start:
            args += ["-L", f"{start},{end}"]
    if "-L" not in args:
        return []
    args += [parent, "--", file]
    try:
        out = _git(args, repo)
    except RuntimeError:
        return []
    pairs = []
    for line in out.split("\n"):
        m = _SHA_LINE_RE.match(line)
        if m:
            pairs.append((int(m.group(3)), m.group(1)))
    return pairs


def churn_walk(repo: Path, code_exts: set[str]) -> dict[str, dict]:
    """sha -> {la, ld} over code files, one numstat pass (needs blobs)."""
    out = _git(["log", f"--since={WINDOW_START}", "--no-merges", "--numstat",
                "--format=%x01%H"], repo)
    churn: dict[str, dict] = {}
    cur = None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            cur = churn.setdefault(line[1:].strip(), {"la": 0, "ld": 0})
        elif cur is not None and "\t" in line:
            a, d, f = line.split("\t", 2)
            if _ext(f) in code_exts and a != "-":
                cur["la"] += int(a)
                cur["ld"] += int(d)
    return churn


def walk_repo(name: str, repos_dir: Path, labels_dir: Path, out_dir: Path) -> dict:
    repo = repos_dir / name
    data = json.loads((labels_dir / f"{name}.json").read_text(encoding="utf-8"))
    commits = data["commits"]
    fix_shas = {c["sha"] for c in commits if c["is_fix"]}
    code_exts = {_ext(f) for c in commits for f in c["files"]}

    t0 = time.time()
    stats = {"n_fixes": len(fix_shas), "n_blamed": 0, "n_skipped_large": 0,
             "n_blame_calls": 0}
    inducing: dict[str, dict] = {}
    for c in commits:
        if not c["is_fix"]:
            continue
        if len(c["files"]) > MAX_FIX_FILES:
            stats["n_skipped_large"] += 1
            continue
        sha = c["sha"]
        try:
            parent = _git(["rev-parse", "--verify", f"{sha}^"], repo).strip()
        except RuntimeError:
            continue
        b_set: set[str] = set()
        ag_set: set[str] = set()
        for file in c["files"]:
            ranges = _deleted_ranges(repo, parent, sha, file)
            if not ranges:
                continue
            try:
                parent_lines = _git(["show", f"{parent}:{file}"], repo).split("\n")
            except RuntimeError:
                parent_lines = []
            ext = _ext(file)
            stats["n_blame_calls"] += 1
            for lineno, blamed in _blame_ranges(repo, parent, file, ranges,
                                                len(parent_lines)):
                b_set.add(blamed)
                content = parent_lines[lineno - 1] if 1 <= lineno <= len(parent_lines) else ""
                if _is_cosmetic_line(content, ext):
                    continue
                if blamed in fix_shas:
                    continue  # fix-of-a-fix is not the origin
                ag_set.add(blamed)
        if b_set:
            stats["n_blamed"] += 1
            inducing[sha] = {"b": sorted(b_set), "ag": sorted(ag_set)}

    churn = churn_walk(repo, code_exts)
    stats["seconds"] = round(time.time() - t0, 1)
    result = {"repo": data["summary"]["repo"], "dir": name,
              "window_start": WINDOW_START, "max_fix_files": MAX_FIX_FILES,
              "stats": stats, "inducing": inducing, "churn": churn}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(result), encoding="utf-8")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", type=Path, required=True)
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    names = [s.strip() for s in args.only.split(",") if s.strip()] or PASS_POOL
    todo = []
    for name in names:
        out_path = args.out_dir / f"{name}.json"
        if out_path.exists() and not args.force:
            log(f"{name}: exists, skipping")
            continue
        todo.append(name)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(walk_repo, n, args.repos_dir, args.labels_dir,
                          args.out_dir): n for n in todo}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                s = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"{name}: ERROR {e}")
                continue
            log(f"{name}: fixes {s['n_fixes']} blamed {s['n_blamed']} "
                f"(skipped-large {s['n_skipped_large']}, "
                f"{s['n_blame_calls']} blame calls) {s['seconds']}s")
    log("done")


if __name__ == "__main__":
    main()
