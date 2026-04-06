#!/usr/bin/env python3
"""
Download benchmark datasets from HuggingFace and save as local JSON.

Usage:
    python scripts/download_benchmarks.py --benchmark swe_qa
    python scripts/download_benchmarks.py --benchmark swe_qa --repos flask,requests,django
"""

import argparse
import json
from pathlib import Path

SWEQA_REPO_MAP = {
    "astropy": "astropy/astropy",
    "conan": "conan-io/conan",
    "django": "django/django",
    "flask": "pallets/flask",
    "matplotlib": "matplotlib/matplotlib",
    "pylint": "pylint-dev/pylint",
    "pytest": "pytest-dev/pytest",
    "reflex": "reflex-dev/reflex",
    "requests": "psf/requests",
    "scikit_learn": "scikit-learn/scikit-learn",
    "sphinx": "sphinx-doc/sphinx",
    "sqlfluff": "sqlfluff/sqlfluff",
    "streamlink": "streamlink/streamlink",
    "sympy": "sympy/sympy",
    "xarray": "pydata/xarray",
}


def download_swe_qa(out_dir: Path, repos: list = None):
    from datasets import load_dataset

    all_tasks = []
    splits = list(SWEQA_REPO_MAP.keys())

    if repos:
        # Map GitHub names back to split names
        repo_to_split = {v: k for k, v in SWEQA_REPO_MAP.items()}
        splits = []
        for r in repos:
            if r in repo_to_split:
                splits.append(repo_to_split[r])
            elif r in SWEQA_REPO_MAP:
                splits.append(r)
            else:
                print(f"  Warning: unknown repo/split '{r}', skipping")

    for split_name in splits:
        github_repo = SWEQA_REPO_MAP[split_name]
        print(f"  Loading {split_name} ({github_repo})...")
        ds = load_dataset("SWE-QA/SWE-QA", split=split_name)

        for i, row in enumerate(ds):
            task = {
                "id": f"{split_name}_{i:03d}",
                "repo": github_repo,
                "split_name": split_name,
                "question": row["question"],
                "answer": row["answer"],
            }
            all_tasks.append(task)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "tasks.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_tasks, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(all_tasks)} tasks to {out_file}")

    # Print summary
    from collections import Counter
    repo_counts = Counter(t["repo"] for t in all_tasks)
    for repo, count in sorted(repo_counts.items()):
        print(f"  {repo}: {count} tasks")


def download_swe_bench(out_dir: Path, repos: list = None):
    from datasets import load_dataset
    from collections import Counter

    print("Loading SWE-bench Verified from HuggingFace...")
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    print(f"  Loaded {len(ds)} tasks")

    tasks = []
    for row in ds:
        task = {
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "problem_statement": row["problem_statement"],
            "hints_text": row.get("hints_text", ""),
            "patch": row["patch"],
            "test_patch": row["test_patch"],
            "FAIL_TO_PASS": row["FAIL_TO_PASS"],
            "PASS_TO_PASS": row["PASS_TO_PASS"],
            "version": row.get("version", ""),
            "environment_setup_commit": row.get("environment_setup_commit", ""),
            "created_at": row.get("created_at", ""),
            "difficulty": row.get("difficulty", ""),
        }
        tasks.append(task)

    if repos:
        tasks = [t for t in tasks if t["repo"] in repos]

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "tasks.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(tasks)} tasks to {out_file}")
    repo_counts = Counter(t["repo"] for t in tasks)
    for repo, count in repo_counts.most_common():
        print(f"  {repo}: {count} tasks")

    diff_counts = Counter(t.get("difficulty", "unknown") for t in tasks)
    print("\nDifficulty distribution:")
    for diff, count in diff_counts.most_common():
        print(f"  {diff}: {count}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True, choices=["swe_qa", "swe_bench"])
    parser.add_argument("--repos", type=str, default=None,
                        help="Comma-separated repo or split names to download")
    parser.add_argument("--data-dir", type=str, default="./data")
    args = parser.parse_args()

    repos = args.repos.split(",") if args.repos else None

    if args.benchmark == "swe_qa":
        download_swe_qa(Path(args.data_dir) / "swe_qa", repos)
    elif args.benchmark == "swe_bench":
        download_swe_bench(Path(args.data_dir) / "swe_bench", repos)


if __name__ == "__main__":
    main()
