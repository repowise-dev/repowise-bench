#!/usr/bin/env python3
"""provenance_walk.py — run the agent-provenance classifier over the corpus.

For every clone under --repos-dir (driven by clone_report.json): fetch/load the
merged-PR index (skipped for the control cohort — their agent volume is
near-zero and local channels suffice), classify every commit, and write
``<out-dir>/<name>.json`` with per-commit rows plus a monthly rollup
(commit counts by autonomy tier and agent).

PR-index bounds: created >= 2024-01-01 for the Devin-era repos
(airbyte, prefect, novu), >= 2025-01-01 elsewhere (Claude Code / Codex /
Copilot-agent / Cursor all launched 2025) — agent-attributed PRs cannot
predate their agent.

Run (venv python; gh authenticated)::

    .venv/Scripts/python.exe health-defect/provenance_walk.py \
        --repos-dir <data>/agent-repos --out-dir <data>/agent-repos/_provenance \
        [--only name1,name2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.agent_provenance import (  # noqa: E402
    classify_repo, fetch_pr_index, fetch_pr_index_search)

DEVIN_ERA = {"airbyte", "prefect", "novu", "fern"}  # Devin PRs begin 2024
PR_SINCE_DEFAULT = "2025-01-01"
PR_SINCE_DEVIN = "2024-01-01"
# Firehose PR volume (~100k merged PRs/yr): full pagination is prohibitive;
# use the targeted per-channel search index instead.
SEARCH_INDEX = {"homebrew-core"}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def monthly_rollup(rows: list[dict]) -> dict:
    months: dict[str, dict] = defaultdict(lambda: {"total": 0, "agent": 0,
                                                   "t1": 0, "t2": 0, "t3": 0,
                                                   "by_agent": defaultdict(int)})
    for r in rows:
        month = (r.get("date") or "")[:7]
        if not month:
            continue
        m = months[month]
        m["total"] += 1
        if r["agent"]:
            m["agent"] += 1
            m[f"t{r['autonomy_tier']}"] += 1
            m["by_agent"][r["agent"]] += 1
    return {k: {**v, "by_agent": dict(v["by_agent"])}
            for k, v in sorted(months.items())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pr_cache = args.repos_dir / "_prcache"

    report = json.loads((args.repos_dir / "clone_report.json").read_text(encoding="utf-8"))
    repos = [r for r in report["repos"] if r["status"] != "FAILED"]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        repos = [r for r in repos if r["dir"] in keep]

    for rec in repos:
        name, full = rec["dir"], rec["repo"]
        out_path = args.out_dir / f"{name}.json"
        if out_path.exists() and not args.force:
            log(f"{name}: exists, skipping")
            continue
        repo_dir = args.repos_dir / name
        pr_index = None
        if rec["cohort"] != "control":
            owner, repo = full.split("/")
            since = PR_SINCE_DEVIN if name in DEVIN_ERA else PR_SINCE_DEFAULT
            t0 = time.time()
            if name in SEARCH_INDEX:
                pr_index = fetch_pr_index_search(owner, repo,
                                                 pr_cache / f"{name}.json", log=log)
            else:
                pr_index = fetch_pr_index(owner, repo, pr_cache / f"{name}.json",
                                          pr_since=since, log=log)
            log(f"{name}: PR index {len(pr_index)} merged PRs "
                f"(since {since}, {time.time() - t0:.0f}s)")
        t0 = time.time()
        rows = classify_repo(repo_dir, pr_index=pr_index)
        n_agent = sum(1 for r in rows if r["agent"])
        log(f"{name}: {len(rows)} commits walked in {time.time() - t0:.0f}s — "
            f"{n_agent} agent-attributed ({n_agent / max(len(rows), 1):.1%})")
        out = {"repo": full, "cohort": rec["cohort"],
               "generated": datetime.now(timezone.utc).isoformat(),
               "pr_index_size": len(pr_index) if pr_index is not None else None,
               "n_commits": len(rows), "n_agent": n_agent,
               "monthly": monthly_rollup(rows), "rows": rows}
        out_path.write_text(json.dumps(out), encoding="utf-8")
    log("done")


if __name__ == "__main__":
    main()
