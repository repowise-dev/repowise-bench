#!/usr/bin/env python3
"""clone_agent_corpus.py — bounded clones of the agent-era corpus.

Clones every corpus repo with a bound chosen by age:
  * all repos:           --filter=blob:none   (blobless partial clone)
  * created before 2023: --shallow-since=2023-01-01  (agent era + >=18 months
                          of human baseline)
  * created 2023+:       no shallow bound (history is already short)

Measures per repo: wall time, on-disk .git size, total commits reachable, and
commits in the screen window — the bounded-history prototype measurements.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/clone_agent_corpus.py \
        --dest <data-dir> [--workers 3] [--only repo1,repo2]

Writes <dest>/clone_report.json (re-runs skip existing clones but still
re-measure them).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

SHALLOW_SINCE = "2023-01-01"
WINDOW_START = "2025-06-01"  # corpus-gate measurement window

# (owner/repo, created_year, cohort)
CORPUS: list[tuple[str, int, str]] = [
    # agent-heavy
    ("github/gh-aw", 2025, "agent_heavy"),
    ("windmill-labs/windmill", 2022, "agent_heavy"),
    ("BasedHardware/omi", 2024, "agent_heavy"),
    ("koala73/worldmonitor", 2026, "agent_heavy"),
    ("dyad-sh/dyad", 2025, "agent_heavy"),
    ("PrimeIntellect-ai/verifiers", 2025, "agent_heavy"),
    # mixed (mature human base + dateable agent era)
    ("grafana/grafana", 2013, "mixed"),
    ("PrefectHQ/prefect", 2018, "mixed"),
    ("airbytehq/airbyte", 2020, "mixed"),
    ("apache/camel", 2009, "mixed"),
    ("mattermost/mattermost", 2015, "mixed"),
    ("metabase/metabase", 2015, "mixed"),
    ("umbraco/Umbraco-CMS", 2013, "mixed"),
    ("formatjs/formatjs", 2014, "mixed"),
    ("novuhq/novu", 2021, "mixed"),
    ("Homebrew/homebrew-core", 2016, "mixed"),
    # controls
    ("pytorch/pytorch", 2016, "control"),
    ("strapi/strapi", 2015, "control"),
    ("NVIDIA-NeMo/NeMo", 2019, "control"),
    ("bluesky-social/atproto", 2021, "control"),
    ("shikijs/shiki", 2018, "control"),
]


def dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return round(total / (1024 * 1024), 1)


def git_out(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def clone_one(repo: str, created_year: int, cohort: str, dest: Path) -> dict:
    name = repo.split("/")[1]
    target = dest / name
    bound = ["--filter=blob:none"]
    if created_year < 2023:
        bound.append(f"--shallow-since={SHALLOW_SINCE}")
    rec: dict = {"repo": repo, "cohort": cohort, "dir": name,
                 "bound": " ".join(bound), "created_year": created_year}
    if target.exists():
        rec["status"] = "exists"
        rec["clone_seconds"] = None
    else:
        t0 = time.time()
        # core.longpaths: several corpus repos (umbraco, airbyte, camel, ...)
        # have paths past the Windows 260-char limit; checkout fails without it.
        r = subprocess.run(
            ["git", "-c", "core.longpaths=true", "clone", *bound,
             f"https://github.com/{repo}.git", str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        rec["clone_seconds"] = round(time.time() - t0, 1)
        if r.returncode != 0:
            rec["status"] = "FAILED"
            rec["error"] = (r.stderr or "")[-400:]
            return rec
        rec["status"] = "cloned"
        # persist for future checkouts in this clone
        subprocess.run(["git", "config", "core.longpaths", "true"], cwd=str(target),
                       capture_output=True)
    rec["git_dir_mb"] = dir_size_mb(target / ".git")
    rec["head"] = git_out(["rev-parse", "HEAD"], target)
    rec["commits_reachable"] = int(git_out(["rev-list", "--count", "HEAD"], target) or 0)
    rec["commits_window"] = int(git_out(
        ["rev-list", "--count", f"--since={WINDOW_START}", "HEAD"], target) or 0)
    rec["shallow"] = (target / ".git" / "shallow").exists()
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", default="", help="comma list of owner/repo to restrict to")
    args = ap.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)

    todo = CORPUS
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        todo = [c for c in CORPUS if c[0] in keep]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(clone_one, r, y, c, args.dest): r for r, y, c in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {rec['repo']:35s} "
                  f"{rec['status']:7s} {rec.get('clone_seconds')}s "
                  f"{rec.get('git_dir_mb')}MB commits={rec.get('commits_reachable')}",
                  flush=True)

    # merge with a previous report so --only runs don't clobber it
    report_path = args.dest / "clone_report.json"
    if report_path.exists():
        prev = json.loads(report_path.read_text(encoding="utf-8"))
        done = {r["repo"] for r in results}
        results.extend(r for r in prev.get("repos", []) if r["repo"] not in done)

    results.sort(key=lambda r: r["repo"])
    report = {"generated": datetime.now(timezone.utc).isoformat(),
              "shallow_since": SHALLOW_SINCE, "window_start": WINDOW_START,
              "repos": results}
    (args.dest / "clone_report.json").write_text(json.dumps(report, indent=2),
                                                 encoding="utf-8")
    failed = [r["repo"] for r in results if r["status"] == "FAILED"]
    print(f"\ndone: {len(results)} repos, {len(failed)} failed {failed}")


if __name__ == "__main__":
    main()
