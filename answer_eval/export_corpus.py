"""Command line entry point: a generated wiki becomes a corpus the runner indexes.

    python -m answer_eval.export_corpus <wiki.db> --id local-<name>

Writes into the snapshot cache, which ``fetch_snapshot`` reads before it
downloads anything. The runner then indexes pages this machine rendered:

    python -m answer_eval --index local-<name> --rebuild-index \\
        --checkout <src> --commit <sha> --out results/answer-eval/blobs/<x>.json

Pass ``--compare <other.json>`` when exporting the second side of a before/after
pair. Two corpora whose page-type composition differs are not a pair, and the
difference lands in recall as if it were the change under test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from answer_eval.local_corpus import describe_corpus, export_local_corpus
from answer_eval.snapshot import cache_path_for

DEFAULT_WORK_DIR = Path("results/answer-eval")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", help="path to a generated .repowise/wiki.db")
    parser.add_argument(
        "--id",
        required=True,
        help="corpus id to cache under; prefix with 'local-' so a blob's corpus "
        "is identifiable as generated rather than hosted",
    )
    parser.add_argument(
        "--work-dir",
        default=str(DEFAULT_WORK_DIR),
        help=f"where snapshots live (default: {DEFAULT_WORK_DIR})",
    )
    parser.add_argument(
        "--compare",
        help="path to the other side's exported json; reports composition "
        "differences, which make a before/after pair incomparable",
    )
    args = parser.parse_args(argv)

    out_path = cache_path_for(args.id, Path(args.work_dir) / "snapshots")
    described = export_local_corpus(args.db, out_path)

    print(f"wrote {described.total_pages} pages -> {out_path}")
    for page_type, count in sorted(described.pages_by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {page_type:24s} {count}")

    if args.compare:
        other_pages = json.loads(Path(args.compare).read_text(encoding="utf-8"))["pages"]
        differences = described.differences(describe_corpus(other_pages))
        print()
        if differences:
            print(f"NOT COMPARABLE with {args.compare}:")
            for line in differences:
                print(f"  {line}")
            print(
                "\nRegenerate both sides with --force after removing .repowise. "
                "Preserved pages from an earlier run land in one side only, and "
                "the difference reads as an effect of the change under test."
            )
            return 1
        print(f"composition matches {args.compare} — the pair is comparable")

    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    sys.exit(main())
