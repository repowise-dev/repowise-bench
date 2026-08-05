"""Build the frozen PR-localization dataset from real merged PRs.

Ground truth mined from GitHub via the gh CLI: a PR's actually-changed files
are an objective localization target, free of question-author bias. The
sample is deterministic (fixed seed over a sorted candidate list) so the
dataset reproduces from this script alone.

Filters (documented in the dataset header):
- merged within the last 18 months of the --as-of date
- description at least 200 characters (the agent needs something to work from)
- 2 to 8 changed non-test files (single-file PRs are trivial; huge PRs are
  unscoreable sprawl)
- not docs-only

Usage:
    python harness/build_prloc_dataset.py --repo pallets/flask \
        --out data/context_bench/prloc_flask.json [--sample 40]
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42
WINDOW_DAYS = 548  # 18 months
MIN_BODY_CHARS = 200
MIN_FILES, MAX_FILES = 2, 8

_DOC_SUFFIXES = (".md", ".rst", ".txt")


def _gh_json(args: list) -> object:
    out = subprocess.run(["gh"] + args, check=True, capture_output=True,
                         text=True).stdout
    return json.loads(out)


def is_test_path(path: str) -> bool:
    p = path.lower()
    parts = p.split("/")
    return ("tests" in parts or "test" in parts
            or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
            or ".test." in parts[-1] or parts[-1].endswith(".test.js"))


def is_doc_path(path: str) -> bool:
    p = path.lower()
    return p.startswith("docs/") or p.endswith(_DOC_SUFFIXES)


def eligible_files(files: list) -> list:
    """Changed non-test files that make up the localization target."""
    return [f for f in files if not is_test_path(f)]


def build(repo: str, sample_size: int, as_of: datetime,
          window_days: int = WINDOW_DAYS) -> dict:
    since = as_of - timedelta(days=window_days)
    listed = _gh_json(["pr", "list", "--repo", repo, "--state", "merged",
                       "--limit", "400",
                       "--json", "number,title,body,mergedAt"])
    candidates = [p for p in listed
                  if len(p.get("body") or "") >= MIN_BODY_CHARS
                  and p.get("mergedAt")
                  and datetime.fromisoformat(
                      p["mergedAt"].replace("Z", "+00:00")) >= since]
    # Deterministic order before the seeded shuffle: gh's ordering is not
    # guaranteed stable across invocations.
    candidates.sort(key=lambda p: p["number"])
    random.Random(SEED).shuffle(candidates)

    picked = []
    inspected = 0
    for pr in candidates:
        if len(picked) >= sample_size:
            break
        inspected += 1
        detail = _gh_json(["pr", "view", str(pr["number"]), "--repo", repo,
                           "--json", "files,baseRefOid,mergedAt"])
        changed = [f["path"] for f in detail.get("files", [])]
        target = eligible_files(changed)
        if not (MIN_FILES <= len(target) <= MAX_FILES):
            continue
        if all(is_doc_path(f) for f in target):
            continue
        picked.append({
            "pr_number": pr["number"],
            "repo": repo,
            "title": pr["title"],
            "body": pr["body"],
            "base_sha": detail.get("baseRefOid", ""),
            "merged_at": detail.get("mergedAt", ""),
            "changed_files": sorted(target),
            "changed_files_all": sorted(changed),
        })
        print(f"  picked #{pr['number']} ({len(target)} target files)"
              f" [{len(picked)}/{sample_size}]")

    return {
        "repo": repo,
        "built_as_of": as_of.isoformat(),
        "seed": SEED,
        "filters": {"window_days": window_days, "min_body_chars": MIN_BODY_CHARS,
                    "files_range": [MIN_FILES, MAX_FILES],
                    "excludes": "test files from targets; docs-only PRs"},
        "candidates_inspected": inspected,
        "prs": picked,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--as-of", default=None,
                    help="ISO date anchoring the merge window (for repro)")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                    help="merge window; widen for low-activity repos and "
                         "record the difference in the results write-up")
    args = ap.parse_args()
    as_of = (datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
             if args.as_of else datetime.now(timezone.utc))
    data = build(args.repo, args.sample, as_of, args.window_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out}: {len(data['prs'])} PRs "
          f"({data['candidates_inspected']} inspected)")


if __name__ == "__main__":
    main()
