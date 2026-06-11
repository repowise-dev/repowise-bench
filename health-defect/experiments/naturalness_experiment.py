#!/usr/bin/env python3
"""naturalness_experiment.py — code naturalness through the gate + line probe.

RESEARCH ARTIFACT (bench-only). Consumes the cached outputs of
``naturalness.py`` (``results/health_defect_<repo>/naturalness{,_lines}.json``)
and runs the two Phase-2 evaluations:

**Part B.1 — file-level (the §3 promotion gate).** Builds two candidate columns —
**mean** line surprisal and **top-decile** line surprisal — and runs each through
``candidate_eval.evaluate_candidate`` (the Phase-1 harness, reused verbatim). The
headline is within-band AUC + LOO pooled OOF AUC delta + the redundancy check
against ``change_entropy``/``churn_risk`` — the make-or-break: *prove naturalness
is not just churn re-encoded* (the churn-proxy trap). Run under the keyword label
(primary, the shipped calibration label) and SZZ (robustness).

**Part B.2 — line-level localization (JITLine-style).** Of the lines a post-T0
fix actually *changed* (bug-inducing T0 lines, via ``lib.function_szz`` +
``git blame`` at T0 — the same line-resolution labels Phase 4 trains on), what
fraction fall in the top-k% most-surprising lines? Reports an effort-aware
cost-effectiveness curve (Recall@k%LOC) pooled across repos by **within-repo
surprisal percentile**, against two baselines: **random** (= k) and **rank by
line-in-the-biggest-file** (the line-level size proxy — the within-band wall at
line resolution). Bootstrap 95% CI (resample repos) on Recall@20%LOC.

Run (venv python; NOT ``uv run``)::

    cd health-defect
    ../../.venv/Scripts/python.exe naturalness_experiment.py \
        --results-dir <bench>/results --repos-dir <bench>/repos \
        [--label keyword] [--rebuild-lines]
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

import candidate_eval as ce
from lib.defect_counter import find_fix_commits, resolve_t0_sha
from lib.filters import normalize_path
from lib.function_szz import _norm_line, inducing_lines_by_file

import os as _os
_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
_REPOWISE_ROOT = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(_HERE.parents[1])))
for _src in ("core", "cli", "server"):
    _p = _REPOWISE_ROOT / "packages" / _src / "src"
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from repowise.core.ingestion.git_indexer.function_blame import _parse_porcelain  # noqa: E402


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


# --------------------------------------------------------------------------
# Part B.1 — file-level columns
# --------------------------------------------------------------------------
def load_naturalness_columns(
    results_dir: Path, repos: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """Return ``{aggregate: {repo: {file: value}}}`` for the file aggregates."""
    aggs = ["mean_line_surprisal", "top_decile_line_surprisal", "token_cross_entropy"]
    cols: dict[str, dict[str, dict[str, float]]] = {a: {} for a in aggs}
    for repo in repos:
        nat = results_dir / f"health_defect_{repo}" / "naturalness.json"
        if not nat.exists():
            continue
        data = json.loads(nat.read_text())
        for a in aggs:
            cols[a][repo] = {_norm(p): v[a] for p, v in data["files"].items()}
    return cols


# --------------------------------------------------------------------------
# Part B.2 — line-level bug-inducing labels (T0 blame match)
# --------------------------------------------------------------------------
def _repo_dir(repos_dir: Path, name: str) -> str | None:
    rd = (repos_dir / name).resolve()
    nested = rd / name
    if nested.exists() and (nested / ".git").exists():
        rd = nested
    return str(rd) if rd.exists() else None


def _blame_at_t0(repo_dir: str, t0_sha: str, path: str) -> dict[int, tuple[str, int]]:
    proc = subprocess.run(
        ["git", "blame", "-w", "-C", "--line-porcelain", t0_sha, "--", path],
        cwd=repo_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not proc.stdout:
        return {}
    lines, _authors = _parse_porcelain(proc.stdout)
    return lines


def _show_text(repo_dir: str, t0_sha: str, path: str) -> list[str]:
    proc = subprocess.run(
        ["git", "show", f"{t0_sha}:{path}"], cwd=repo_dir,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.split("\n") if proc.returncode == 0 else []


def buggy_lines_for_repo(
    repo_dir: str, cfg: dict, cache_path: Path, *, rebuild: bool
) -> dict[str, list[int]]:
    """Map ``file -> [T0 line numbers that are bug-inducing]`` (AG-SZZ).

    A T0 line is bug-inducing iff its ``(blame_sha, whitespace-normalised text)``
    matches an inducing line fingerprint from ``function_szz`` — exactly the
    line-resolution label ``build_function_dataset`` uses. Cached per repo."""
    if cache_path.exists() and not rebuild:
        return {k: v for k, v in json.loads(cache_path.read_text()).items()
                if not k.startswith("_")}

    source_root = cfg["source_root"]
    extensions = tuple(cfg.get("extensions", [".py"]))
    t0_sha = resolve_t0_sha(repo_dir, cfg["t0_date"])
    fixes = find_fix_commits(
        repo_dir, t0_sha, "HEAD",
        strategy=cfg["defect_strategy"],
        emoji=cfg.get("gitmoji_bug", "\U0001F41B"),
        prefix=cfg.get("bug_prefix", "Fixed #"),
        include=cfg.get("bug_keywords"),
        exclude=cfg.get("exclude_keywords"),
    )
    fix_sha_set = {s for s, _ in fixes}
    inducing = inducing_lines_by_file(
        repo_dir, t0_sha, fixes,
        source_root=source_root, extensions=extensions,
        fix_sha_set=fix_sha_set, variant="ag",
    )
    out: dict[str, list[int]] = {}
    for path, induce_map in inducing.items():
        p = _norm(path)
        blame = _blame_at_t0(repo_dir, t0_sha, path)
        text_lines = _show_text(repo_dir, t0_sha, path)
        buggy: list[int] = []
        for ln, entry in blame.items():
            txt = text_lines[ln - 1] if 1 <= ln <= len(text_lines) else ""
            if (entry[0], _norm_line(txt)) in induce_map:
                buggy.append(ln)
        if buggy:
            out[p] = sorted(buggy)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"_t0_sha": t0_sha, **out}, indent=2))
    return out


def line_localization(
    results_dir: Path, repos_dir: Path, repo_cfgs: dict, repos: list[str],
    *, rebuild_lines: bool,
) -> dict:
    """Pooled effort-aware cost-effectiveness curve: Recall@k%LOC for
    surprisal vs the biggest-file size proxy vs random.

    Each line carries a within-repo percentile (0=most surprising → inspected
    first). Pooling by percentile means "inspect the top-k% most surprising
    lines of every repo" — a fair, scale-free effort-aware sweep."""
    ks = [1, 2, 5, 10, 20, 30, 50]
    # Per line: (repo, surprisal_pct, bigfile_pct, is_buggy)
    records: list[tuple[str, float, float, int]] = []
    per_repo_pos: dict[str, int] = {}
    per_repo_buggy_covered: dict[str, tuple[int, int]] = {}

    for repo in repos:
        cfg = repo_cfgs.get(repo)
        rd = _repo_dir(repos_dir, repo)
        lines_path = results_dir / f"health_defect_{repo}" / "naturalness_lines.json"
        if cfg is None or rd is None or not lines_path.exists():
            continue
        per_line = json.loads(lines_path.read_text())  # {file: {line: surprisal}}
        buggy = buggy_lines_for_repo(
            rd, cfg, results_dir / f"health_defect_{repo}" / "buggy_lines.json",
            rebuild=rebuild_lines,
        )
        # Build the repo's line universe (lines with a surprisal value).
        repo_lines: list[tuple[str, int, float, int]] = []  # file, line, surp, nloc
        file_nloc = {f: len(d) for f, d in per_line.items()}
        for f, d in per_line.items():
            nf = file_nloc[f]
            for ln_s, surp in d.items():
                repo_lines.append((f, int(ln_s), float(surp), nf))
        if not repo_lines:
            continue
        buggy_set = {(f, ln) for f, lns in buggy.items() for ln in lns}
        # Coverage: how many buggy lines are scorable (in the universe).
        universe_keys = {(f, ln) for f, ln, _, _ in repo_lines}
        covered = sum(1 for k in buggy_set if k in universe_keys)
        per_repo_buggy_covered[repo] = (covered, len(buggy_set))
        if covered == 0:
            continue
        # Within-repo percentile ranks (0 = inspected first).
        n = len(repo_lines)
        surp_order = sorted(range(n), key=lambda i: -repo_lines[i][2])
        big_order = sorted(range(n), key=lambda i: (-repo_lines[i][3], repo_lines[i][1]))
        surp_pct = [0.0] * n
        big_pct = [0.0] * n
        for rank, i in enumerate(surp_order):
            surp_pct[i] = rank / n
        for rank, i in enumerate(big_order):
            big_pct[i] = rank / n
        pos = 0
        for i, (f, ln, _surp, _nf) in enumerate(repo_lines):
            isb = 1 if (f, ln) in buggy_set else 0
            pos += isb
            records.append((repo, surp_pct[i], big_pct[i], isb))
        per_repo_pos[repo] = pos

    total_pos = sum(r[3] for r in records)

    def recall_at(pct_key_idx: int, k: int) -> float:
        budget = k / 100.0
        caught = sum(r[3] for r in records if r[pct_key_idx] < budget)
        return caught / total_pos if total_pos else float("nan")

    curve = {
        "surprisal": {k: round(recall_at(1, k), 4) for k in ks},
        "biggest_file": {k: round(recall_at(2, k), 4) for k in ks},
        "random": {k: round(k / 100.0, 4) for k in ks},
    }

    # Bootstrap CI on Recall@20% (resample repos).
    rng = random.Random(20260603)
    repo_records: dict[str, list[tuple[float, float, int]]] = {}
    for repo, sp, bp, isb in records:
        repo_records.setdefault(repo, []).append((sp, bp, isb))
    repo_list = list(repo_records)

    def boot_recall(idx: int, k: int, n_boot: int = 2000) -> tuple[float, float]:
        budget = k / 100.0
        vals = []
        for _ in range(n_boot):
            chosen = [repo_list[rng.randrange(len(repo_list))] for _ in repo_list]
            caught = tot = 0
            for g in chosen:
                for sp, bp, isb in repo_records[g]:
                    key = sp if idx == 0 else bp
                    tot += isb
                    if key < budget:
                        caught += isb
            if tot:
                vals.append(caught / tot)
        vals.sort()
        if len(vals) < 20:
            return (float("nan"), float("nan"))
        return (vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))])

    ci_surp = boot_recall(0, 20)
    ci_big = boot_recall(1, 20)

    return {
        "n_lines": len(records),
        "n_buggy_lines": total_pos,
        "n_repos_with_buggy": len(per_repo_pos),
        "ks": ks,
        "curve": curve,
        "recall20_ci": {
            "surprisal": [round(ci_surp[0], 4), round(ci_surp[1], 4)],
            "biggest_file": [round(ci_big[0], 4), round(ci_big[1], 4)],
        },
        "buggy_line_coverage": {
            r: {"covered": c, "total": t} for r, (c, t) in per_repo_buggy_covered.items()
        },
    }


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_HERE.parent / "repos")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--also-szz", action="store_true",
                    help="also score the file-level columns under the SZZ label")
    ap.add_argument("--rebuild-lines", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    import yaml
    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = list(repo_cfgs)
    out_path = args.out or (args.results_dir / "naturalness_scorecards.json")

    # ---- Part B.1: file-level gate ----------------------------------------
    print("=== Part B.1 — file-level naturalness through the promotion gate ===")
    cols = load_naturalness_columns(args.results_dir, repos)
    cost = "T0 n-gram cache LM; ~9s/repo build (cached); analysis-only re-score"

    labels = [args.label] + (["szz"] if args.also_szz else [])
    all_cards: dict[str, dict] = {}
    md_blocks: list[str] = []
    for label in labels:
        rows = ce.load_corpus(args.results_dir, args.config, label)
        for agg in ["mean_line_surprisal", "top_decile_line_surprisal"]:
            name = f"naturalness_{agg}" + (f"@{label}" if label != args.label else "")
            card = ce.evaluate_candidate(
                cols[agg], name, results_dir=args.results_dir,
                config_path=args.config, label=label, cost_note=cost,
                corpus_rows=rows,
            )
            all_cards[name] = card
            md = ce.scorecard_markdown(card)
            md_blocks.append(md)
            print("\n" + md)

    # ---- Part B.2: line-level localization --------------------------------
    print("\n=== Part B.2 — line-level localization (JITLine-style) ===")
    loc = line_localization(
        args.results_dir, args.repos_dir, repo_cfgs, repos,
        rebuild_lines=args.rebuild_lines,
    )
    print(f"lines={loc['n_lines']} buggy_lines={loc['n_buggy_lines']} "
          f"repos_with_buggy={loc['n_repos_with_buggy']}")
    print(f"{'k%LOC':>6s} {'surprisal':>10s} {'biggest_file':>13s} {'random':>8s}")
    for k in loc["ks"]:
        print(f"{k:5d}% {loc['curve']['surprisal'][k]:>10.3f} "
              f"{loc['curve']['biggest_file'][k]:>13.3f} {loc['curve']['random'][k]:>8.3f}")
    print(f"Recall@20%LOC: surprisal {loc['curve']['surprisal'][20]:.3f} "
          f"CI{loc['recall20_ci']['surprisal']} · "
          f"biggest_file {loc['curve']['biggest_file'][20]:.3f} "
          f"CI{loc['recall20_ci']['biggest_file']} · random 0.200")

    out_path.write_text(json.dumps(
        {"file_level_cards": all_cards, "line_level": loc, "cost_note": cost}, indent=2))
    md_path = out_path.with_suffix(".md")
    md_path.write_text("\n\n".join(md_blocks))
    print(f"\nWrote {out_path}\nWrote {md_path}")


if __name__ == "__main__":
    main()
