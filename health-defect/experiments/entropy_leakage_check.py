"""Temporal-leakage check for the change_entropy biomarker.

The defect benchmark scores at HEAD (its known §7.2 flaw), so change_entropy
computed over full history INCLUDES the defect-window commits (T0, HEAD]. If
those bug-fix commits are themselves wide/scattered, the biomarker's Cliff's δ
could be partly measuring the very fixes it is meant to predict.

This script recomputes the change_entropy firing set TWO ways over the same
2000-commit co-change walk, then reports Cliff's δ of post-T0 defect counts
between fired / not-fired files (restricted to the benchmark's joined universe,
exactly as lib/stats.per_biomarker_analysis does):

  * "HEAD"  — all commits up to HEAD, 90d activity & decay anchored at now.
              Should reproduce the benchmark's published δ (validates the method).
  * "preT0" — ONLY commits strictly before T0; 90d activity & decay anchored at
              T0. No defect-window information can reach the signal. This is the
              leakage test: if δ holds, the signal is genuinely predictive; if it
              collapses, the HEAD δ was inflated by leakage.

Research artifact — not shipped, not imported by packages/.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1].parent
DECAY_TAU = 180.0
MAX_FILES_ENTROPY = 30
MIN_PCT = 0.80
MIN_C90 = 3


def cliffs_delta(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dom = 0
    for x in a:
        for y in b:
            dom += (x > y) - (x < y)
    return dom / (len(a) * len(b))


def tracked_files(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    )
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def walk_commits(repo: Path, limit: int = 2000) -> list[tuple[int, list[str]]]:
    """Return [(committer_ts, [files...]), ...] mirroring the co-change walk."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", f"-{limit}", "--name-only", "--no-merges", "--format=%x00%ct"],
        capture_output=True,
        text=True,
        check=True,
    )
    commits: list[tuple[int, list[str]]] = []
    cur_ts = 0
    cur: list[str] = []
    started = False
    for line in out.stdout.splitlines():
        if line.startswith("\x00"):
            if started:
                commits.append((cur_ts, cur))
            started = True
            cur = []
            try:
                cur_ts = int(line.lstrip("\x00").strip())
            except ValueError:
                cur_ts = 0
        elif line.strip():
            cur.append(line.strip())
    if started:
        commits.append((cur_ts, cur))
    return commits


def compute(commits, all_files, ts_cutoff, now_ref):
    """Replicate compute_co_changes_and_entropy + enrich percentile + the
    activity gate, for commits with ts < ts_cutoff (None = no cutoff)."""
    entropy: dict[str, float] = {}
    c90: dict[str, int] = {}
    window_start = now_ref - 90 * 86400
    for ts, files in commits:
        if ts_cutoff is not None and ts >= ts_cutoff:
            continue
        present = [f for f in files if f in all_files]
        n = len(present)
        # activity gate population (per-file commit count in the 90d window)
        if window_start <= ts < now_ref:
            for f in present:
                c90[f] = c90.get(f, 0) + 1
        if n < 2 or n > MAX_FILES_ENTROPY:
            continue
        age_days = max((now_ref - ts) / 86400.0, 0.0)
        weight = math.exp(-age_days / DECAY_TAU)
        contribution = weight * math.log2(n) / n
        for f in present:
            entropy[f] = entropy.get(f, 0.0) + contribution
    entropy = {f: s for f, s in entropy.items() if s > 0.0}
    # percentile over nonzero-entropy files (enrich.compute_percentiles)
    nonzero = sorted(entropy.items(), key=lambda kv: kv[1])
    n_ent = len(nonzero)
    pct = {f: rank / n_ent for rank, (f, _s) in enumerate(nonzero)} if n_ent else {}
    return entropy, pct, c90


def fired(entropy, pct, c90, file_path) -> bool:
    e = entropy.get(file_path, 0.0)
    return e > 0.0 and pct.get(file_path, 0.0) >= MIN_PCT and c90.get(file_path, 0) >= MIN_C90


def run(repo_name: str, repo_dir: Path, t0_date: str):
    joined = json.loads((BENCH / "results" / f"health_defect_{repo_name}" / "joined_data.json").read_text())
    defects = {d["file_path"]: d["defect_count"] for d in joined}
    universe = set(defects)

    t0_ts = datetime.fromisoformat(t0_date).replace(tzinfo=UTC).timestamp()
    now = time.time()
    all_files = tracked_files(repo_dir)
    commits = walk_commits(repo_dir)
    n_pre = sum(1 for ts, _ in commits if ts < t0_ts)
    print(f"\n===== {repo_name} =====")
    print(f"  joined files: {len(universe)} | defect-bearing: {sum(v>0 for v in defects.values())}")
    print(f"  walk commits: {len(commits)} | strictly before T0 ({t0_date}): {n_pre}")

    for label, cutoff, ref in (("HEAD", None, now), ("preT0", t0_ts, t0_ts)):
        ent, pct, c90 = compute(commits, all_files, cutoff, ref)
        fire = {f for f in universe if fired(ent, pct, c90, f)}
        with_d = [defects[f] for f in fire]
        without_d = [defects[f] for f in universe - fire]
        delta = cliffs_delta([float(x) for x in with_d], [float(x) for x in without_d])
        dbear = sum(1 for f in fire if defects[f] > 0)
        print(
            f"  [{label:5}] fired on {len(fire):3} joined files "
            f"({dbear} defect-bearing) | Cliff's δ = {delta:+.3f}"
        )


if __name__ == "__main__":
    repos = {
        "clap": ("clap", "2025-11-23"),
        "pydantic": ("pydantic", "2025-11-23"),
    }
    for name, (dirname, t0) in repos.items():
        rd = BENCH / "repos" / dirname
        if not rd.exists():
            print(f"skip {name}: {rd} missing", file=sys.stderr)
            continue
        run(name, rd, t0)
