"""Build step only: SCIP index -> oracle.jsonl.

Run with an interpreter that has `protobuf`, `tree_sitter` and the grammar
packages `callsites.py` imports, plus `scip_pb2` (see `scip_reader.py`). The
scoring step (`score.py`) runs where `repowise` is installed instead. The two
steps are one implementation run from two interpreters only because a
`protobuf` runtime new enough for the generated `scip_pb2` may not match the
one the repowise build under test pins.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from join import build_oracle, write_oracle_jsonl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--scip", required=True)
    ap.add_argument(
        "--language",
        required=True,
        choices=["go", "typescript", "java", "csharp", "rust", "python", "c", "cpp"],
    )
    ap.add_argument("--scip-subdir", default="")
    ap.add_argument("--out-oracle", required=True)
    ap.add_argument("--exclude-tests", action="store_true")
    args = ap.parse_args()

    repo_root = Path(args.repo).resolve()
    result = build_oracle(
        repo_root=repo_root,
        scip_index_path=Path(args.scip),
        language=args.language,
        scip_subdir=args.scip_subdir,
        file_glob="*",
        exclude_tests=args.exclude_tests,
    )
    out_oracle = Path(args.out_oracle)
    out_oracle.parent.mkdir(parents=True, exist_ok=True)
    write_oracle_jsonl(result, out_oracle, oracle_name=f"scip-{args.language}")
    print(
        f"SCIP oracle: {len(result.analysed_files)} files, "
        f"{len(result.edges)} in-repo edges, "
        f"{len(result.judgeable_callers)} judgeable callers -> {out_oracle}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
