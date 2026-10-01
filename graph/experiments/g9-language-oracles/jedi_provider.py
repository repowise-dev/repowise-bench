"""Python definition provider for the shared oracle pipeline: jedi `goto`
in place of a SCIP index.

scip-python 0.6.6 was tried first and dropped. It left some top-level class
declarations without a Definition role, and resolved `pkg.ClassName(args)`
calls through a star-reexporting `__init__.py` to an unrelated class. Both are
inside scip-python, so this module asks jedi instead (`jedi_worker.py`).

It turns `callsites.py`'s tree-sitter call sites into jedi queries, and jedi's
per-site answers into the same `join.JoinResult` / `oracle.jsonl` shape
`scip_reader.py` + `join.py` produce, so `score.py` and the rest of the
pipeline need no change.

Same caller-keying algorithm as `join.build_oracle` (seed every named
function's own line as judgeable, downgrade a caller only when one of its
call sites is `unknown`, keep it judgeable but edge-less when a site
resolves `external`). It is duplicated here because `join.build_oracle` walks
a SCIP symbol table one call at a time, where jedi answers one batched
request for every site in the repository.

Usage (an interpreter with tree_sitter and the grammars callsites.py needs)::

    python jedi_provider.py --repo path/to/httpx         --out-oracle work/httpx/oracle_jedi.jsonl --exclude-tests
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from callsites import CallSite, all_named_functions, call_sites  # noqa: E402
from jedi_worker import python_defs, tracked_files  # noqa: E402
from join import JoinResult, write_oracle_jsonl  # noqa: E402

LANGUAGE = "python"


def _char_col(line_bytes: bytes, byte_col: int) -> int:
    """tree-sitter's column is a byte offset; jedi counts UTF-8 characters.
    Converts `callsites.py`'s sites into jedi's column space."""
    return len(line_bytes[:byte_col].decode("utf-8", "replace"))


def build_oracle_jedi(
    *, repo_root: Path, exclude_tests: bool = False
) -> tuple[JoinResult, dict]:
    """Same shape and same caller-keying rule as `join.build_oracle`, jedi in
    place of `SCIPIndex`. Returns `(result, stats)`; `stats` is diagnostic
    only (query and resolution counts), not part of the oracle shape.
    """
    repo_root = repo_root.resolve()
    all_files = tracked_files(repo_root)
    py_files = [f for f in all_files if f.endswith(".py")]
    if exclude_tests:
        py_files = [
            f
            for f in py_files
            if "/test/" not in f.lower()
            and "/tests/" not in f.lower()
            and not Path(f).name.startswith("test_")
            and not Path(f).name.endswith("_test.py")
        ]

    # Pass 1: tree-sitter call sites for every analysed file, and every
    # named-function's own line (seeds `resolved_ok`, same as join.py).
    resolved_ok: dict[tuple[str, int], bool] = {}
    sites_by_file: dict[str, list[CallSite]] = {}
    queries: list[tuple[str, int, int]] = []
    query_site: list[tuple[str, int]] = []  # (file, caller_decl_line) per query, in order
    for rel in py_files:
        abs_path = repo_root / rel
        try:
            source = abs_path.read_bytes()
        except OSError:
            continue
        for line in all_named_functions(abs_path, source, LANGUAGE):
            resolved_ok.setdefault((rel, line), True)
        lines = source.split(b"\n")
        sites = call_sites(abs_path, source, LANGUAGE)
        sites_by_file[rel] = sites
        for s in sites:
            col = _char_col(lines[s.callee_line0], s.callee_col0)
            queries.append((rel, s.callee_line0 + 1, col))  # jedi: 1-indexed line
            query_site.append((rel, s.caller_decl_line))

    # Pass 2: one batched jedi request for every site in the repo.
    resolved = python_defs(repo_root, all_files, queries) if queries else []

    edges: list[tuple[str, int, str, int]] = []
    ambiguous_callers: set[tuple[str, int]] = set()
    counts = {"defs": 0, "external": 0, "unknown": 0}
    for (rel, caller_line), answer in zip(query_site, resolved, strict=True):
        caller_key = (rel, caller_line)
        status = answer["s"]
        counts[status] = counts.get(status, 0) + 1
        if status == "unknown":
            resolved_ok[caller_key] = False
            continue
        resolved_ok.setdefault(caller_key, True)
        if status == "defs":
            # Ambiguous-caller correction: a call site whose `goto`
            # genuinely returns more than one distinct in-repo definition
            # (e.g. a platform-branched `if WIN: def f(): ... else: def
            # f(): ...` pair) has no single answer the oracle can hold this
            # caller to -- record it as ambiguous (see score.py
            # `--ambiguous-callers`) in addition to emitting all candidate
            # edges as before, so existing callers see no behaviour change
            # unless they opt in.
            if len({(d[0], d[1]) for d in answer["d"]}) > 1:
                ambiguous_callers.add(caller_key)
            for def_file, def_line, _name in answer["d"]:
                edges.append((rel, caller_line, def_file, int(def_line)))
        # "external": resolved, no in-repo definition -- no edge, caller stays judgeable.

    judgeable = {k for k, ok in resolved_ok.items() if ok}
    result = JoinResult(
        analysed_files=sorted(py_files), edges=edges, judgeable_callers=judgeable
    )
    stats = {
        "files": len(py_files),
        "queries": len(queries),
        **counts,
        "edges": len(edges),
        "judgeable_callers": len(judgeable),
        "ambiguous_callers": sorted(ambiguous_callers),
    }
    return result, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out-oracle", required=True)
    ap.add_argument("--exclude-tests", action="store_true")
    args = ap.parse_args()

    repo_root = Path(args.repo).resolve()
    result, stats = build_oracle_jedi(repo_root=repo_root, exclude_tests=args.exclude_tests)
    out_oracle = Path(args.out_oracle)
    out_oracle.parent.mkdir(parents=True, exist_ok=True)
    write_oracle_jsonl(result, out_oracle, oracle_name="jedi-python")
    ambiguous_path = out_oracle.with_suffix(out_oracle.suffix + ".ambiguous.txt")
    ambiguous_path.write_text(
        "".join(f"{f}\t{ln}\n" for f, ln in stats["ambiguous_callers"]), encoding="utf-8"
    )
    print(
        f"jedi oracle: {stats['files']} files, {stats['queries']} queries "
        f"(defs={stats['defs']} external={stats['external']} unknown={stats['unknown']}), "
        f"{len(result.edges)} in-repo edges, "
        f"{len(result.judgeable_callers)} judgeable callers, "
        f"{len(stats['ambiguous_callers'])} ambiguous callers (>1 in-repo def at one call "
        f"site) -> {out_oracle}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
