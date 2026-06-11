#!/usr/bin/env python3
"""build_openclaw_dataset.py — index openclaw at T0 + keyword labels, no SZZ.

THROWAWAY (local-stash). Produces the standard benchmark artifacts for openclaw
(`results/health_defect_openclaw/{health_scores,defect_counts_keyword,joined_data}.json`)
using the FAST keyword-touch label only — SZZ would blame 11.8k fix hunks (hours);
keyword tracks it closely enough for this probe. Also (re)writes a 22-repo cohort
config (`local-stash/config_cohort.yaml` = the 21 baseline repos + openclaw) for the
gate scripts.

Run (venv python, from repowise-bench/)::

    ../.venv/Scripts/python.exe local-stash/build_openclaw_dataset.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import yaml

_BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BENCH))

from lib.defect_counter import count_defects_keyword, resolve_t0_sha  # noqa: E402
from lib.health_runner import run_health_at_commit  # noqa: E402
from run_benchmark import join_and_filter, normalize_path  # noqa: E402

RESULTS = Path(__file__).resolve().parents[2] / "results"
REPOS = Path(__file__).resolve().parents[2] / "repos"

OPENCLAW = {
    "name": "openclaw",
    "repo_url": "https://github.com/openclaw/openclaw.git",
    "language": "typescript",
    "source_root": "",
    "extensions": [".ts"],
    "t0_date": "2026-04-03",
    "defect_strategy": "keyword",
    "bug_keywords": ["fix", "bug", "patch", "resolve"],
    "exclude_keywords": ["typo", "docs", "style", "lint", "bump", "chore",
                         "format", "deps", "refactor", "test", "feat", "ci",
                         "perf", "build", "merge"],
    # Drop vendored / generated / docs trees so the scored universe is source.
    # NOTE: repowise -x takes gitignore DIR patterns only — glob file patterns
    # like **/*.test.ts blow up the CLI ("unexpected extra arguments"). Test
    # files (.test.ts/.spec.ts) are already dropped by repowise's own is_test
    # detection + join_and_filter(exclude_tests=True); .d.ts stubs are filtered
    # in the join below.
    "exclude": ["vendor/", "docs/", "assets/", "test-fixtures/", "test/"],
}


def write_cohort_config() -> Path:
    base = yaml.safe_load((_BENCH / "config.yaml").read_text())
    names = {r["name"] for r in base["repos"]}
    if "openclaw" not in names:
        base["repos"].append(OPENCLAW)
    out = Path(__file__).resolve().parent / "config_cohort.yaml"
    out.write_text(yaml.safe_dump(base, sort_keys=False))
    return out


def main() -> None:
    cohort_cfg = write_cohort_config()
    print(f"Wrote cohort config: {cohort_cfg}")

    repo_dir = str((REPOS / "openclaw").resolve())
    t0_sha = resolve_t0_sha(repo_dir, OPENCLAW["t0_date"])
    out_dir = RESULTS / "health_defect_openclaw"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"openclaw T0 {t0_sha[:12]} ({OPENCLAW['t0_date']})")

    health_path = out_dir / "health_scores.json"
    if health_path.exists():
        print("  reusing existing health_scores.json")
        health = json.loads(health_path.read_text())
    else:
        print("  indexing + scoring at T0 (this is the long step)...")
        health = run_health_at_commit(
            repo_dir, t0_sha, timeout=7200, exclude_patterns=OPENCLAW["exclude"],
        )
        health["_scored_at"] = {"mode": "t0", "sha": t0_sha}
        health_path.write_text(json.dumps(health, indent=2))
    print(f"  -> {len(health.get('metrics', []))} files scored, "
          f"{len(health.get('findings', []))} findings")

    print("  keyword defect labeling over (T0, HEAD]...")
    counts = count_defects_keyword(
        repo_dir, t0_sha, "HEAD", OPENCLAW["source_root"],
        include=OPENCLAW["bug_keywords"], exclude=OPENCLAW["exclude_keywords"],
        extensions=tuple(OPENCLAW["extensions"]),
    )
    counts = {normalize_path(k): v for k, v in counts.items()}
    (out_dir / "defect_counts_keyword.json").write_text(json.dumps(counts, indent=2))
    print(f"  -> {sum(counts.values())} fix-touches over {len(counts)} files")

    joined = join_and_filter(health, counts, exclude_patterns=OPENCLAW["exclude"])
    # Drop generated TS type-declaration stubs (not real source).
    joined = [d for d in joined if not d["file_path"].endswith(".d.ts")]
    # finding_count per file (parity with the other repos' joined_data).
    fcount: Counter = Counter(normalize_path(f.get("file_path", "")) for f in health.get("findings", []))
    for d in joined:
        d["finding_count"] = int(fcount.get(d["file_path"], 0))
    (out_dir / "joined_data.json").write_text(json.dumps(joined, indent=2))

    npos = sum(1 for d in joined if d["defect_count"] > 0)
    nsmall = sum(1 for d in joined if d["nloc"] <= 48)
    nsmall_pos = sum(1 for d in joined if d["nloc"] <= 48 and d["defect_count"] > 0)
    print(f"\n  joined_data: {len(joined)} files | {npos} positives ({npos/max(len(joined),1):.0%})")
    print(f"  small (<=48 LOC): {nsmall} files | {nsmall_pos} positives")
    print(f"\nWrote {out_dir}/joined_data.json")


if __name__ == "__main__":
    main()
