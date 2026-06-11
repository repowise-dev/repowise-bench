#!/usr/bin/env python3
"""review_coverage_experiment.py — code-review coverage / participation via the gate.

RESEARCH ARTIFACT (bench-only). Phase-3 Part B. McIntosh et al. (MSR'14 /
EMSE'16, "An empirical study of the impact of modern code review practices on
software quality") found that the *fraction of a component's changes that went
through review* and *whether those reviews had real discussion* both associated
with post-release defects — poorly-reviewed code was buggier. git + the ``gh``
API (the same auth ``lib/issue_links.py`` already uses). Bot-relevant (the PR
surface is exactly review participation).

Per file, in the window before T0 (leakage-free; defects are (T0, HEAD]):
  * Resolve each non-merge window commit to its PR via the squash/rebase
    ``(#N)`` subject suffix; that commit's ``--name-only`` diff gives the files
    the PR touched (no extra ``/files`` API call). A file is **covered by PR #N**.
  * For each referenced PR fetch (and cache) review metadata via ``gh api``:
    author, distinct non-author reviewers, review count, review-comment count,
    and ``reviewed`` = (>=1 review by a non-author OR >0 review comments).
  * File aggregates over the PRs that touched it:
      ``reviewed_fraction``    — fraction of its PRs that were reviewed,
      ``mean_reviewers``       — mean distinct non-author reviewers per PR,
      ``review_comment_density`` — mean review comments per PR.

**Graceful degradation (plan §5 — absent != zero).** A file is *computable* only
if at least one in-window PR touched it; files in repos / areas with poor PR
hygiene (direct pushes, no ``(#N)``) are **absent**, never scored 0. Per-repo
coverage (measured vs absent) is reported so the corpus heterogeneity is explicit.
If ``gh`` is unauthenticated the whole signal is absent and the experiment says so.

Responses cached under ``results/health_defect_<repo>/reviews/<N>.json`` → re-runs
are offline. First run is the slow one (one ``gh api`` round-trip per distinct PR).

Run (absolute venv python; point dirs at the MAIN bench checkout)::

    $env:PYTHONIOENCODING="utf-8"
    C:\\Users\\ragha\\Desktop\\repowise\\.venv\\Scripts\\python.exe review_coverage_experiment.py \\
        --results-dir C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\results \\
        --repos-dir   C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\repos
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import candidate_eval as ce
from lib.defect_counter import _git, resolve_t0_sha
from lib.filters import is_test_file, normalize_path
from lib.issue_links import gh_available, owner_repo_from_url

_HERE = Path(__file__).resolve().parent

DEFAULT_WINDOW_DAYS = 365
# Squash / "merge & rebase" workflows append the PR number: "... (#1234)".
_PR_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")
# Merge-commit workflow: "Merge pull request #1234 from ...".
_MERGE_RE = re.compile(r"Merge pull request #(\d+)\b")


def _make_exclude_matcher(patterns: list[str]):
    if not patterns:
        return lambda _p: False
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return lambda p: spec.match_file(p)


def _pr_from_subject(subject: str) -> int | None:
    m = _PR_SUFFIX_RE.search(subject)
    if m:
        return int(m.group(1))
    m = _MERGE_RE.search(subject)
    if m:
        return int(m.group(1))
    return None


def since_arg(t0_date: str, window_days: int) -> str:
    """``--since=<absolute date>`` anchored ``window_days`` before T0 (NOT the
    run-date-relative ``N days ago``)."""
    if not window_days:
        return "--max-count=100000"
    d = _dt.date.fromisoformat(t0_date) - _dt.timedelta(days=window_days)
    return f"--since={d.isoformat()}"


def window_pr_files(
    repo_dir: str, t0_sha: str, *, source_root: str, extensions: tuple[str, ...],
    is_excluded, since: str,
) -> dict[str, set[int]]:
    """{file_path: {pr_number, ...}} — for each source file, the in-window PRs
    (resolved from squash/rebase ``(#N)`` subjects) that touched it."""
    out = _git(
        ["log", t0_sha, "--no-merges", "--no-renames", since,
         "--pretty=format:\x01%s", "--name-only"],
        cwd=repo_dir,
    )
    file_prs: dict[str, set[int]] = {}
    cur_pr: int | None = None
    for raw in out.split("\n"):
        if not raw:
            continue
        if raw[0] == "\x01":
            cur_pr = _pr_from_subject(raw[1:])
            continue
        if cur_pr is None:
            continue
        f = normalize_path(raw)
        if not f or not f.startswith(source_root):
            continue
        if not any(f.endswith(e) for e in extensions):
            continue
        if is_test_file(f) or is_excluded(f):
            continue
        file_prs.setdefault(f, set()).add(cur_pr)
    return file_prs


def fetch_pr_review(owner: str, repo: str, number: int, cache_dir: Path) -> dict | None:
    """Fetch + cache one PR's review metadata. Returns dict with author /
    reviewers / n_reviews / review_comments / reviewed, ``{"_missing":True}`` for
    a 404, or None on a transport error (not cached → re-run retries)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{number}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    def _api(path: str, jq: str | None = None) -> tuple[int, str, str]:
        cmd = ["gh", "api", path]
        if jq:
            cmd += ["--jq", jq]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        except (FileNotFoundError, OSError):
            return -1, "", "no gh"
        return r.returncode, r.stdout, r.stderr

    rc, out, err = _api(
        f"repos/{owner}/{repo}/pulls/{number}",
        "{author:.user.login, review_comments, comments, merged}",
    )
    if rc != 0:
        if "Not Found" in err or "404" in err:
            data = {"_missing": True, "number": number}
            cache_path.write_text(json.dumps(data))
            return data
        return None
    pr = json.loads(out)
    author = pr.get("author")

    rc2, out2, err2 = _api(
        f"repos/{owner}/{repo}/pulls/{number}/reviews",
        "[.[] | {user:.user.login, state}]",
    )
    if rc2 != 0:
        return None
    reviews = json.loads(out2) if out2.strip() else []
    reviewers = sorted({rv["user"] for rv in reviews
                        if rv.get("user") and rv["user"] != author})
    n_reviews = len([rv for rv in reviews if rv.get("user") != author])
    review_comments = int(pr.get("review_comments") or 0)
    reviewed = bool(reviewers or review_comments > 0)
    data = {
        "number": number, "author": author, "reviewers": reviewers,
        "n_reviewers": len(reviewers), "n_reviews": n_reviews,
        "review_comments": review_comments, "reviewed": reviewed,
    }
    cache_path.write_text(json.dumps(data))
    return data


def build_repo_columns(
    repo: str, cfg: dict, repos_dir: Path, results_dir: Path, *,
    window_days: int,
) -> dict | None:
    """Compute the three per-file review columns for one repo. Returns
    {columns:{feat:{file:val}}, meta:{...}} or None if no PR data at all."""
    repo_dir = (repos_dir / repo).resolve()
    nested = repo_dir / repo
    if nested.exists() and (nested / ".git").exists():
        repo_dir = nested
    if not repo_dir.exists():
        print(f"  SKIP {repo}: clone missing")
        return None
    repo_dir = str(repo_dir)
    orr = owner_repo_from_url(cfg.get("repo_url", ""))
    if not orr:
        print(f"  SKIP {repo}: cannot parse owner/repo")
        return None
    owner, name = orr

    source_root = cfg["source_root"]
    extensions = tuple(cfg.get("extensions", [".py"]))
    is_excluded = _make_exclude_matcher(list(cfg.get("exclude") or []))
    t0_sha = resolve_t0_sha(repo_dir, cfg["t0_date"])

    file_prs = window_pr_files(
        repo_dir, t0_sha, source_root=source_root, extensions=extensions,
        is_excluded=is_excluded, since=since_arg(cfg["t0_date"], window_days),
    )
    all_prs = sorted({pr for prs in file_prs.values() for pr in prs})
    cache_dir = results_dir / f"health_defect_{repo}" / "reviews"

    t = time.time()
    pr_meta: dict[int, dict] = {}
    n_fetch = 0
    for pr in all_prs:
        cached = (cache_dir / f"{pr}.json").exists()
        d = fetch_pr_review(owner, name, pr, cache_dir)
        if d is None or d.get("_missing"):
            continue
        pr_meta[pr] = d
        if not cached:
            n_fetch += 1

    reviewed_frac: dict[str, float] = {}
    mean_reviewers: dict[str, float] = {}
    comment_density: dict[str, float] = {}
    for f, prs in file_prs.items():
        known = [pr_meta[p] for p in prs if p in pr_meta]
        if not known:
            continue  # absent — no resolvable PR review data for this file
        reviewed_frac[f] = sum(1 for d in known if d["reviewed"]) / len(known)
        mean_reviewers[f] = sum(d["n_reviewers"] for d in known) / len(known)
        comment_density[f] = sum(d["review_comments"] for d in known) / len(known)

    meta = {
        "owner_repo": f"{owner}/{name}", "t0_sha": t0_sha,
        "n_source_files_with_pr": len(file_prs),
        "n_files_with_review_data": len(reviewed_frac),
        "n_prs_referenced": len(all_prs), "n_prs_resolved": len(pr_meta),
        "n_fetched_this_run": n_fetch, "build_seconds": round(time.time() - t, 1),
        "window_days": window_days,
    }
    print(f"  {repo:12s} files_w_pr={len(file_prs):4d} files_w_review={len(reviewed_frac):4d} "
          f"prs={len(all_prs):4d} resolved={len(pr_meta):4d} fetched={n_fetch:4d} "
          f"{meta['build_seconds']:.0f}s")
    return {
        "columns": {
            "reviewed_fraction": reviewed_frac,
            "mean_reviewers": mean_reviewers,
            "review_comment_density": comment_density,
        },
        "meta": meta,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_HERE.parent / "repos")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--repo", default="")
    ap.add_argument("--build-only", action="store_true",
                    help="only fetch + cache review data (no gate scoring)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    import yaml
    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)
    out_path = args.out or (args.results_dir / "review_coverage_scorecards.json")

    if not gh_available():
        print("!! gh CLI not authenticated — review coverage is ENTIRELY ABSENT. "
              "Run `gh auth login` and retry.")
        return

    print(f"=== Review coverage: {len(repos)} repos, window={args.window_days}d ===")
    feats = ["reviewed_fraction", "mean_reviewers", "review_comment_density"]
    by_feat: dict[str, dict[str, dict[str, float]]] = {f: {} for f in feats}
    repo_meta: dict[str, dict] = {}
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo}: not in config)")
            continue
        try:
            res = build_repo_columns(repo, cfg, args.repos_dir, args.results_dir,
                                     window_days=args.window_days)
        except Exception as exc:  # noqa: BLE001
            print(f"  (skip {repo}: {exc})")
            continue
        if res is None:
            continue
        repo_meta[repo] = res["meta"]
        for f in feats:
            if res["columns"][f]:
                by_feat[f][repo] = res["columns"][f]

    # --- per-repo coverage table (measured vs absent) -----------------------
    print("\n=== Per-repo review-data coverage (n files with review data) ===")
    print(f"{'repo':12s} {'files_w_pr':>10s} {'files_w_review':>14s} {'prs':>5s} {'resolved':>8s}")
    for repo, m in repo_meta.items():
        print(f"{repo:12s} {m['n_source_files_with_pr']:>10d} "
              f"{m['n_files_with_review_data']:>14d} {m['n_prs_referenced']:>5d} "
              f"{m['n_prs_resolved']:>8d}")

    (args.results_dir / "review_coverage_columns.json").write_text(
        json.dumps({"by_feat": by_feat, "repo_meta": repo_meta}, indent=2))
    if args.build_only:
        print("\n(build-only: cached review data; skipping gate)")
        return

    # --- gate each column ---------------------------------------------------
    print(f"\n=== Gate (label={args.label}) ===")
    rows = ce.load_corpus(args.results_dir, args.config, args.label)
    cost = "git-log PR resolution + gh api per PR (cached); see per-repo coverage"
    cards = {}
    md_blocks = []
    for f in feats:
        col = by_feat.get(f)
        if not col:
            print(f"  ({f}: no data)")
            continue
        card = ce.evaluate_candidate(
            col, f, results_dir=args.results_dir, config_path=args.config,
            label=args.label, corpus_rows=rows, cost_note=cost,
        )
        cards[f] = card
        md_blocks.append(ce.scorecard_markdown(card))
        print("\n" + ce.scorecard_markdown(card))

    out_path.write_text(json.dumps(
        {"cards": cards, "repo_meta": repo_meta, "cost_note": cost,
         "window_days": args.window_days}, indent=2))
    out_path.with_suffix(".md").write_text("\n\n".join(md_blocks))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    sys.exit(main())
