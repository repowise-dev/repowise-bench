#!/usr/bin/env python3
"""agent_line_survival.py — do agent-written lines survive like human lines?

Sample-based BlameIndex-style survival: sample code files touched by window
commits, blame each sampled file ONCE at HEAD, attribute every surviving line
to the commit that wrote it, and join with per-(commit,file) added-line counts
from one numstat pass. Survival of a commit's contribution to a file =
HEAD-blame lines attributed to it / lines it added.

Design guards:
  * exposure: only commits from WINDOW_START to EXPOSURE_CUTOFF (>= ~6 months
    before the data pull) enter contrasts — young lines survive trivially;
  * line-weighted group rates (sum survived / sum added) per repo;
    deltas are within-repo (tier - human), cluster-bootstrap CI over repos;
  * file sampling is uniform over the touched-file universe and identical for
    every tier (sampling cannot favour a group);
  * renames break attribution (blame follows content within a file path only)
    — survival is *path-stable* survival; stated in the report.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_line_survival.py \
        --repos-dir <data>/agent-repos --labels-dir <data>/agent-repos/_labels \
        --out-dir <data>/agent-repos/_survival [--files-per-repo 400]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

WINDOW_START = "2025-06-01"
EXPOSURE_CUTOFF = "2025-12-01"  # commits after this are too young to score
MIN_LINES = 200                 # a group needs this many added lines per repo
_SHA_LINE_RE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)")

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


def per_file_added(repo: Path, files: set[str]) -> dict[tuple[str, str], int]:
    """(sha, file) -> lines added, window numstat pass restricted to files."""
    out = _git(["log", f"--since={WINDOW_START}", "--no-merges", "--numstat",
                "--format=%x01%H"], repo)
    added: dict[tuple[str, str], int] = {}
    sha = None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            sha = line[1:].strip()
        elif sha and "\t" in line:
            a, _d, f = line.split("\t", 2)
            if f in files and a != "-" and a != "0":
                added[(sha, f)] = added.get((sha, f), 0) + int(a)
    return added


def blame_file_at_head(repo: Path, file: str) -> Counter:
    """sha -> surviving line count for one file at HEAD."""
    try:
        out = _git(["blame", "-w", "--line-porcelain", "HEAD", "--", file], repo)
    except RuntimeError:
        return Counter()
    counts: Counter = Counter()
    for line in out.split("\n"):
        m = _SHA_LINE_RE.match(line)
        if m:
            counts[m.group(1)] += 1
    return counts


def survey_repo(name: str, repos_dir: Path, labels_dir: Path,
                n_files: int, rng: random.Random) -> dict:
    repo = repos_dir / name
    data = json.loads((labels_dir / f"{name}.json").read_text(encoding="utf-8"))
    commits = data["commits"]
    cutoff_ts = int(datetime.fromisoformat(EXPOSURE_CUTOFF + "T00:00:00+00:00")
                    .timestamp())
    eligible = {c["sha"]: c for c in commits if c["ts"] < cutoff_ts and c["n_files"]}

    universe = sorted({f for c in eligible.values() for f in c["files"]})
    sample = universe if len(universe) <= n_files else rng.sample(universe, n_files)
    sample_set = set(sample)

    added = per_file_added(repo, sample_set)
    t0 = time.time()
    surv: dict[tuple[str, str], int] = {}
    n_blamed = 0
    for f in sample:
        counts = blame_file_at_head(repo, f)
        if not counts:
            continue  # file deleted/renamed at HEAD — its lines count as dead
        n_blamed += 1
        for sha, n in counts.items():
            if sha in eligible:
                surv[(sha, f)] = n

    # per-commit totals over sampled files
    per_commit: dict[str, dict] = {}
    for (sha, f), la in added.items():
        if sha not in eligible:
            continue
        d = per_commit.setdefault(sha, {"la": 0, "surv": 0})
        d["la"] += la
        d["surv"] += min(surv.get((sha, f), 0), la)  # context lines can inflate

    groups: dict[str, dict] = defaultdict(lambda: {"la": 0, "surv": 0, "n_commits": 0})
    rows = []
    for sha, d in per_commit.items():
        c = eligible[sha]
        g = f"t{c['tier']}" if c["agent"] else "human"
        for gg in (g, f"agent:{c['agent']}" if c["agent"] else None):
            if gg:
                groups[gg]["la"] += d["la"]
                groups[gg]["surv"] += d["surv"]
                groups[gg]["n_commits"] += 1
        rows.append({"sha": sha, "group": g, "agent": c["agent"],
                     "month": datetime.fromtimestamp(c["ts"], tz=timezone.utc)
                     .strftime("%Y-%m"), "la": d["la"], "surv": d["surv"]})

    return {"repo": data["summary"]["repo"], "dir": name,
            "n_files_universe": len(universe), "n_files_sampled": len(sample),
            "n_files_blamed": n_blamed, "blame_seconds": round(time.time() - t0, 1),
            "groups": {g: {**v, "survival": round(v["surv"] / v["la"], 4)}
                       for g, v in groups.items() if v["la"] >= MIN_LINES},
            "commits": rows}


def pooled_delta(repo_results: list[dict], group: str,
                 rng: random.Random, n_boot: int = 2000) -> dict | None:
    """Within-repo line-weighted survival delta, cluster-bootstrap over repos.
    Per-repo bootstrap resamples commits (the survival unit) within cells."""
    deltas = []
    for r in repo_results:
        g, h = r["groups"].get(group), r["groups"].get("human")
        if not g or not h:
            continue
        deltas.append((r["repo"], g["survival"] - h["survival"],
                       g["survival"], h["survival"]))
    if len(deltas) < 3:
        return None
    vals = [d[1] for d in deltas]
    boots = sorted(
        sum(s := [rng.choice(vals) for _ in vals]) / len(s) for _ in range(n_boot))
    return {"mean_delta": round(sum(vals) / len(vals), 4),
            "ci95": [round(boots[int(0.025 * n_boot)], 4),
                     round(boots[int(0.975 * n_boot)], 4)],
            "n_repos": len(deltas),
            "per_repo": [{"repo": r, "delta": round(d, 4), "group": g,
                          "human": h} for r, d, g, h in deltas]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", type=Path, required=True)
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--files-per-repo", type=int, default=400)
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=20260604)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    names = [s.strip() for s in args.only.split(",") if s.strip()] or PASS_POOL
    results = []
    for name in names:
        cache = args.out_dir / f"{name}.json"
        if cache.exists():
            results.append(json.loads(cache.read_text(encoding="utf-8")))
            log(f"{name}: cached")
            continue
        try:
            r = survey_repo(name, args.repos_dir, args.labels_dir,
                            args.files_per_repo, rng)
        except Exception as e:  # noqa: BLE001
            log(f"{name}: ERROR {e}")
            continue
        cache.write_text(json.dumps(r), encoding="utf-8")
        results.append(r)
        log(f"{name}: {r['n_files_sampled']} files ({r['n_files_blamed']} blamed, "
            f"{r['blame_seconds']}s) groups: "
            + ", ".join(f"{g}={v['survival']}" for g, v in sorted(r["groups"].items())
                        if not g.startswith("agent:")))

    contrasts = {g: pooled_delta(results, g, rng) for g in ("t1", "t2", "t3")}
    summary = {"generated": datetime.now(timezone.utc).isoformat(),
               "window": [WINDOW_START, EXPOSURE_CUTOFF],
               "files_per_repo": args.files_per_repo, "min_lines": MIN_LINES,
               "repos": [{k: r[k] for k in ("repo", "n_files_sampled",
                                            "n_files_blamed", "groups")}
                         for r in results],
               "contrasts": contrasts}
    (args.out_dir / "line_survival.json").write_text(
        json.dumps(summary, indent=1), encoding="utf-8")

    lines = ["# Line survival to HEAD (path-stable, sampled files)",
             f"\nGenerated {summary['generated']} · commits {WINDOW_START} → "
             f"{EXPOSURE_CUTOFF} (≥6 mo exposure) · ≤{args.files_per_repo} sampled "
             f"files/repo · line-weighted survival = surviving lines / added lines "
             "· Δ = tier − human, same repo · cluster-bootstrap 95% CI · renames "
             "count as death (path-stable).\n",
             "| tier | mean Δ | 95% CI | n repos |", "|---|--:|---|--:|"]
    for g in ("t1", "t2", "t3"):
        c = contrasts[g]
        if not c:
            continue
        lo, hi = c["ci95"]
        star = " **\\***" if lo > 0 or hi < 0 else ""
        lines.append(f"| {g} | {c['mean_delta']:+.4f}{star} | "
                     f"[{lo:+.4f}, {hi:+.4f}] | {c['n_repos']} |")
    lines += ["", "## Per-repo line-weighted survival\n",
              "| repo | group | added lines | survival |", "|---|---|--:|--:|"]
    for r in results:
        for g in ("human", "t1", "t2", "t3"):
            v = r["groups"].get(g)
            if v:
                lines.append(f"| {r['repo']} | {g} | {v['la']} | {v['survival']:.3f} |")
    (args.out_dir / "LINE_SURVIVAL.md").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
    log(f"wrote {args.out_dir / 'LINE_SURVIVAL.md'}")


if __name__ == "__main__":
    main()
