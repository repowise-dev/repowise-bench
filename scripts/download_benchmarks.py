#!/usr/bin/env python3
"""Download the SWE-QA task corpus and clone target repositories.

By default this fetches the full SWE-QA dataset from HuggingFace and writes
it to ``data/swe_qa/tasks.json``, then clones each target repository into
``repos/<org>/<name>``. The harness selects the per-repo subset at run time
based on the ``benchmarks.swe_qa.repos`` field of the active config.

Usage:

    python scripts/download_benchmarks.py                # all repos
    python scripts/download_benchmarks.py --repos flask  # flask only

If ``data/swe_qa/tasks.json`` already exists, the dataset download is skipped
unless ``--force`` is passed. Cloned repositories are likewise reused if they
are already present on disk.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Map of short SWE-QA split names to their canonical GitHub coordinates.
SWEQA_REPO_MAP: dict[str, str] = {
    "astropy":      "astropy/astropy",
    "conan":        "conan-io/conan",
    "django":       "django/django",
    "flask":        "pallets/flask",
    "matplotlib":   "matplotlib/matplotlib",
    "pylint":       "pylint-dev/pylint",
    "pytest":       "pytest-dev/pytest",
    "reflex":       "reflex-dev/reflex",
    "requests":     "psf/requests",
    "scikit_learn": "scikit-learn/scikit-learn",
    "sphinx":       "sphinx-doc/sphinx",
    "sqlfluff":     "sqlfluff/sqlfluff",
    "streamlink":   "streamlink/streamlink",
    "sympy":        "sympy/sympy",
    "xarray":       "pydata/xarray",
}


def download_swe_qa_tasks(out_dir: Path, splits: list[str], *, force: bool) -> Path:
    """Download SWE-QA task definitions and write them as a JSON corpus."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "tasks.json"

    if out_file.exists() and not force:
        print(f"  Reusing existing task corpus at {out_file}")
        return out_file

    try:
        from datasets import load_dataset
    except ImportError as e:
        raise SystemExit(
            "The `datasets` package is required to download SWE-QA tasks. "
            "Install it with `pip install datasets`."
        ) from e

    all_tasks: list[dict] = []
    for split_name in splits:
        github_repo = SWEQA_REPO_MAP[split_name]
        print(f"  Loading {split_name} ({github_repo})...")
        ds = load_dataset("SWE-QA/SWE-QA", split=split_name)
        for i, row in enumerate(ds):
            all_tasks.append(
                {
                    "id": f"{split_name}_{i:03d}",
                    "repo": github_repo,
                    "split_name": split_name,
                    "question": row["question"],
                    "answer": row["answer"],
                }
            )

    out_file.write_text(json.dumps(all_tasks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Saved {len(all_tasks)} tasks to {out_file}")

    repo_counts = Counter(t["repo"] for t in all_tasks)
    for repo, count in sorted(repo_counts.items()):
        print(f"    {repo}: {count} tasks")
    return out_file


def clone_repository(github_slug: str, repos_root: Path) -> Path:
    """Clone a single GitHub repository into ``repos_root/<org>/<name>``.

    No-ops if the destination already contains a git checkout.
    """
    org, _, name = github_slug.partition("/")
    if not org or not name:
        raise ValueError(f"Invalid github slug: {github_slug!r}")
    dest = repos_root / org / name
    if (dest / ".git").exists():
        print(f"  Reusing existing checkout at {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{github_slug}.git"
    print(f"  Cloning {url} → {dest}")
    subprocess.check_call(["git", "clone", "--quiet", url, str(dest)])
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repos",
        type=str,
        default=None,
        help=(
            "Comma-separated split names or GitHub slugs to download. "
            "If omitted, every repo in the SWE-QA corpus is downloaded."
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--repos-dir", type=Path, default=Path("./repos"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download the SWE-QA dataset even if it already exists locally.",
    )
    parser.add_argument(
        "--no-clone",
        action="store_true",
        help="Skip cloning target repositories (download tasks only).",
    )
    args = parser.parse_args()

    # Resolve which splits to download.
    if args.repos:
        requested = [r.strip() for r in args.repos.split(",") if r.strip()]
        slug_to_split = {v: k for k, v in SWEQA_REPO_MAP.items()}
        splits: list[str] = []
        for r in requested:
            if r in SWEQA_REPO_MAP:
                splits.append(r)
            elif r in slug_to_split:
                splits.append(slug_to_split[r])
            else:
                print(f"  Warning: unknown repo/split '{r}', skipping", file=sys.stderr)
    else:
        splits = list(SWEQA_REPO_MAP.keys())

    if not splits:
        raise SystemExit("No valid repos to download.")

    print("Downloading SWE-QA tasks...")
    download_swe_qa_tasks(args.data_dir / "swe_qa", splits, force=args.force)

    if not args.no_clone:
        print("\nCloning target repositories...")
        for split_name in splits:
            clone_repository(SWEQA_REPO_MAP[split_name], args.repos_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
