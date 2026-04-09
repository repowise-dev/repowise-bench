"""Token-efficiency benchmark.

For each test commit in a repo, compute the tokens a reviewer/agent would
need to load under three strategies:

  1. naive    — full contents of every changed (non-excluded) file
  2. diff     — `git diff <parent>..<sha>` (non-excluded files only)
  3. repowise — result of repowise's MCP tool `get_context(targets=files)`

`tiktoken` (cl100k_base) is used as the token counter for all three strategies
so the ratios are directly comparable.

Fairness rules baked into the runner:
  * Generated/lockfile/vendored paths are excluded from ALL three strategies
    so the comparison is on real source code, not on `package-lock.json`.
  * Commits whose surviving file set is empty after exclusion are dropped.
  * Commits where repowise returns trivially few tokens (< --min-repowise-tokens)
    are dropped — those are wiki-misses, not real wins, and would otherwise
    inflate the ratio dishonestly.
  * The same file list is used for naive and repowise. Neither side gets to
    quietly skip files.
  * Multiple repos can be passed; per-repo and overall stats are reported.

Output: a CSV + summary JSON per run, plus a summary printed to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# No path exclusions: every changed file counts for every strategy.
def is_excluded(path: str) -> bool:
    return False

import tiktoken
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPO_ROOT = Path(__file__).resolve().parents[1]
REPOWISE_ROOT = REPO_ROOT.parent  # /Users/swatiahuja/Desktop/repowise
REPOWISE_SRC = [
    REPOWISE_ROOT / "packages" / "cli" / "src",
    REPOWISE_ROOT / "packages" / "core" / "src",
    REPOWISE_ROOT / "packages" / "server" / "src",
]

ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(ENC.encode(text, disallowed_special=()))


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), text=True, errors="replace"
    )


def changed_files(repo: Path, sha: str) -> list[str]:
    out = git(repo, "diff", "--name-only", f"{sha}^", sha)
    return [f for f in out.splitlines() if f.strip() and not is_excluded(f)]


def naive_tokens(repo: Path, sha: str, files: list[str]) -> int:
    total = 0
    for f in files:
        try:
            blob = git(repo, "show", f"{sha}:{f}")
        except subprocess.CalledProcessError:
            continue  # file deleted in this commit
        total += count_tokens(blob)
    return total


def diff_tokens(repo: Path, sha: str, files: list[str]) -> int:
    if not files:
        return 0
    # Restrict diff to the same fair file set we're using everywhere else.
    out = git(repo, "diff", f"{sha}^", sha, "--", *files)
    return count_tokens(out)


@dataclass
class CommitResult:
    repo: str
    sha: str
    subject: str
    files_changed: int
    naive_tokens: int
    diff_tokens: int
    repowise_tokens: int
    repowise_vs_naive: float
    repowise_vs_diff: float
    skipped_reason: str = ""


def server_params(repo: Path) -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in REPOWISE_SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "repowise.cli.main", "mcp", str(repo.resolve()),
            "--transport", "stdio",
        ],
        env=env,
    )


async def repowise_context_tokens(
    session: ClientSession, files: list[str]
) -> int:
    if not files:
        return 0
    result = await session.call_tool("get_context", {"targets": files})
    text_parts = []
    for c in result.content:
        # mcp content blocks are TextContent / ImageContent / etc.
        text_parts.append(getattr(c, "text", "") or "")
    return count_tokens("\n".join(text_parts))


async def run_bench_one_repo(
    repo: Path, shas: list[str], min_rw: int,
) -> list[CommitResult]:
    results: list[CommitResult] = []
    async with stdio_client(server_params(repo)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for sha in shas:
                subject = git(repo, "log", "-1", "--format=%s", sha).strip()
                files = changed_files(repo, sha)
                base = CommitResult(
                    repo=repo.name, sha=sha[:12], subject=subject[:80],
                    files_changed=len(files), naive_tokens=0, diff_tokens=0,
                    repowise_tokens=0, repowise_vs_naive=0.0,
                    repowise_vs_diff=0.0,
                )
                if not files:
                    base.skipped_reason = "all-files-excluded"
                    results.append(base)
                    print(f"  {sha[:8]} SKIP (no non-excluded files)")
                    continue

                naive = naive_tokens(repo, sha, files)
                diff = diff_tokens(repo, sha, files)
                try:
                    rw = await repowise_context_tokens(session, files)
                except Exception as e:
                    print(f"  [warn] {sha[:8]} repowise call failed: {e}",
                          file=sys.stderr)
                    rw = 0

                base.naive_tokens = naive
                base.diff_tokens = diff
                base.repowise_tokens = rw

                if rw < min_rw:
                    base.skipped_reason = f"repowise-undersized(<{min_rw})"
                    results.append(base)
                    print(f"  {sha[:8]} SKIP wiki-miss "
                          f"(rw={rw} < {min_rw})")
                    continue

                base.repowise_vs_naive = naive / rw
                base.repowise_vs_diff = diff / rw if diff else 0.0
                results.append(base)
                print(f"  {sha[:8]} files={len(files):3d} "
                      f"naive={naive:7d} diff={diff:6d} "
                      f"repowise={rw:6d} ratio_naive={base.repowise_vs_naive:5.1f}x")
    return results


async def run_bench(
    repos: list[Path], per_repo_last: int, min_rw: int, out_csv: Path,
) -> list[CommitResult]:
    all_results: list[CommitResult] = []
    for repo in repos:
        log = git(repo, "log", "--no-merges", "--pretty=%H", f"-{per_repo_last}")
        shas = [s for s in log.splitlines() if s.strip()]
        print(f"\n--- {repo.name}: {len(shas)} commits ---")
        all_results.extend(await run_bench_one_repo(repo, shas, min_rw))

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(all_results[0]).keys()))
        w.writeheader()
        for r in all_results:
            w.writerow(asdict(r))
    return all_results


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarise(results: list[CommitResult]) -> dict:
    n_total = len(results)
    valid = [r for r in results if not r.skipped_reason and r.repowise_tokens > 0]
    skipped = [r for r in results if r.skipped_reason]
    sum_naive = sum(r.naive_tokens for r in valid)
    sum_diff = sum(r.diff_tokens for r in valid)
    sum_rw = sum(r.repowise_tokens for r in valid)
    ratios_naive = [r.repowise_vs_naive for r in valid]
    ratios_diff = [r.repowise_vs_diff for r in valid if r.repowise_vs_diff > 0]

    skip_reasons: dict[str, int] = {}
    for r in skipped:
        skip_reasons[r.skipped_reason] = skip_reasons.get(r.skipped_reason, 0) + 1

    return {
        "commits_total": n_total,
        "commits_valid": len(valid),
        "commits_skipped": len(skipped),
        "skip_reasons": skip_reasons,
        "total_naive_tokens": sum_naive,
        "total_diff_tokens": sum_diff,
        "total_repowise_tokens": sum_rw,
        "naive_per_commit": sum_naive // max(len(valid), 1),
        "diff_per_commit": sum_diff // max(len(valid), 1),
        "repowise_per_commit": sum_rw // max(len(valid), 1),
        # pooled (sum/sum): conservative, dominated by big commits
        "pooled_naive_vs_repowise": (sum_naive / sum_rw) if sum_rw else 0.0,
        "pooled_diff_vs_repowise": (sum_diff / sum_rw) if sum_rw else 0.0,
        # mean-of-ratios: standard "average reduction" framing
        "mean_naive_vs_repowise": sum(ratios_naive) / max(len(valid), 1),
        "mean_diff_vs_repowise": sum(ratios_diff) / max(len(ratios_diff), 1),
        "median_naive_vs_repowise": _median(ratios_naive),
        "median_diff_vs_repowise": _median(ratios_diff),
        "max_naive_vs_repowise": max(ratios_naive) if ratios_naive else 0.0,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", nargs="+", required=True,
                   help="One or more paths to git repos with built repowise indexes")
    p.add_argument("--last", type=int, default=30,
                   help="Use last N non-merge commits per repo (default: 30)")
    p.add_argument("--min-repowise-tokens", type=int, default=200,
                   help="Drop commits where repowise returned fewer than this "
                        "many tokens (likely wiki-misses). Default: 200")
    p.add_argument("--out", default="results/token_efficiency/results.csv")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repos = [Path(r).resolve() for r in args.repo]

    print(f"Benchmarking {len(repos)} repo(s), last {args.last} commits each, "
          f"min_repowise_tokens={args.min_repowise_tokens}")
    out_csv = REPO_ROOT / args.out
    results = asyncio.run(
        run_bench(repos, args.last, args.min_repowise_tokens, out_csv)
    )

    print("\n=== Overall summary ===")
    overall = summarise(results)
    print(json.dumps(overall, indent=2))

    per_repo = {}
    for repo in repos:
        sub = [r for r in results if r.repo == repo.name]
        per_repo[repo.name] = summarise(sub)
        print(f"\n=== {repo.name} ===")
        print(json.dumps(per_repo[repo.name], indent=2))

    summary_path = out_csv.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(
        {"overall": overall, "per_repo": per_repo}, indent=2
    ))
    print(f"\nWrote {out_csv}")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
