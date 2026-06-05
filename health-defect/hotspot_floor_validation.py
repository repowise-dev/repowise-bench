"""Validate the issue-#361 hotspot absolute floors against the 21-repo corpus.

The floors (enrich.meets_hotspot_floors) are designed to bite ONLY on quiet
repos — on the active benchmark corpus they should strip (almost) no hotspot,
which means every benchmark metric (AUC / Popt / partial-rho) is provably
unchanged: findings depend on biomarker gates, and the only gates touched are
the hotspot bit + its severity escalations.

For each corpus repo this script:

1. Runs the product GitIndexer (ESSENTIAL tier — blame/co-change irrelevant
   to the hotspot signal) with windows anchored to the repo's own HEAD.
2. Classifies hotspots under the OLD rule (pct >= 0.75 and c90 > 0) and the
   NEW rule (pct >= 0.75 and absolute floors).
3. Reports the stripped set, its activity profile, and whether any stripped
   file was defect-bearing under the keyword label (results cache).
4. Reports the repo's active-contributor count (the Layer-3 gate input) —
   corpus repos should all be > 3, i.e. the small-team severity cap is also
   a no-op on the corpus.

Run:  ../../.venv/Scripts/python.exe hotspot_floor_validation.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["REPOWISE_GIT_WINDOW_ANCHOR"] = "head"

BENCH = Path(__file__).resolve().parent
REPOS = BENCH.parent / "repos"
RESULTS = BENCH.parent / "results"

CORPUS = [
    "pydantic", "rich", "litestar",          # Python
    "hono", "zod",                            # TypeScript
    "axios", "fastify",                       # JavaScript
    "clap", "fd", "bat",                      # Rust
    "gin", "chi", "fiber",                    # Go
    "caffeine", "mockito",                    # Java
    "detekt", "coroutines",                   # Kotlin
    "spdlog", "fmt",                          # C++
    "quartznet", "npgsql",                    # C#
]


def _defect_files(repo: str) -> set[str]:
    p = RESULTS / f"health_defect_{repo}" / "defect_counts_keyword.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(data, dict):
        return {k for k, v in data.items() if v}
    return set()


async def _index(repo_path: Path) -> list[dict]:
    from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier

    indexer = GitIndexer(repo_path, tier=GitIndexTier.ESSENTIAL)
    _summary, metas = await indexer.index_repo("floor-validation")
    return metas


def main() -> None:
    from repowise.core.ingestion.git_indexer.enrich import (
        count_active_contributors,
        meets_hotspot_floors,
    )

    grand_old = grand_new = grand_files = 0
    stripped_defect_files: list[str] = []
    rows: list[tuple[str, int, int, int, int, str, int | None]] = []

    for repo in CORPUS:
        repo_path = REPOS / repo
        if not repo_path.exists():
            print(f"  !! {repo}: clone missing, skipped", file=sys.stderr)
            continue
        metas = asyncio.run(_index(repo_path))
        if not metas:
            print(f"  !! {repo}: no git metadata, skipped", file=sys.stderr)
            continue

        old_hot = {
            m["file_path"]
            for m in metas
            if (m.get("churn_percentile") or 0.0) >= 0.75
            and (m.get("commit_count_90d") or 0) > 0
        }
        new_hot = {
            m["file_path"]
            for m in metas
            if (m.get("churn_percentile") or 0.0) >= 0.75 and meets_hotspot_floors(m)
        }
        stripped = old_hot - new_hot
        assert not (new_hot - old_hot), "floors must only ever narrow the set"

        defects = _defect_files(repo)
        stripped_defective = sorted(stripped & defects)
        stripped_defect_files += [f"{repo}:{f}" for f in stripped_defective]

        team = count_active_contributors(metas)
        meta_by_path = {m["file_path"]: m for m in metas}
        profile = ", ".join(
            f"{f} (c90={meta_by_path[f].get('commit_count_90d')}, "
            f"t={meta_by_path[f].get('temporal_hotspot_score', 0.0):.2f})"
            for f in sorted(stripped)[:4]
        )

        rows.append(
            (repo, len(metas), len(old_hot), len(new_hot), len(stripped), profile, team)
        )
        grand_files += len(metas)
        grand_old += len(old_hot)
        grand_new += len(new_hot)

    print(f"{'repo':<12} {'files':>6} {'old_hot':>8} {'new_hot':>8} {'stripped':>9} {'team90d':>8}")
    for repo, n, old, new, strip, profile, team in rows:
        print(f"{repo:<12} {n:>6} {old:>8} {new:>8} {strip:>9} {team if team is not None else '?':>8}")
        if profile:
            print(f"             stripped: {profile}")
    pct = 100.0 * (grand_old - grand_new) / max(grand_old, 1)
    print(
        f"\nTOTAL: {grand_files} files, {grand_old} -> {grand_new} hotspots "
        f"({grand_old - grand_new} stripped, {pct:.1f}%)"
    )
    if stripped_defect_files:
        print(f"stripped DEFECT-BEARING files ({len(stripped_defect_files)}):")
        for f in stripped_defect_files:
            print(f"  - {f}")
    else:
        print("stripped defect-bearing files: NONE")
    small_teams = [r[0] for r in rows if r[6] is not None and r[6] <= 3]
    print(f"corpus repos with small-team gate active: {small_teams or 'NONE'}")


if __name__ == "__main__":
    main()
