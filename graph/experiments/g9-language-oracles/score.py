"""Scoring step only: read an existing `oracle.jsonl` (from `oracle_build.py`)
and score it with G4's own `compare.py` bucket logic (matched / contradicted
/ unjudged, Wilson CIs) -- imported by path, not forked. Run this with the
interpreter that has the repowise build under test installed, so
`ours_keys()` reads that build. See `oracle_build.py` for why this is a
separate interpreter from the join step.

Usage (from this directory):
    python score.py --oracle work/gitleaks/oracle.jsonl \
        --repo path/to/gitleaks --repo-name gitleaks \
        --out results/go/gitleaks/score.json

With no `--overload-set`, this is still a pure delegation to
`compare.main()` -- unrecognised flags are peeled off first, so every
existing caller's command line behaves exactly as before.

`--overload-set --language <lang>` additionally computes a SECONDARY score
(`overload_set_metrics` below) alongside the primary one: an arm edge whose
callee is a different overload of the same (class, method name) as the
oracle's true callee for that caller counts as matched rather than
contradicted/missed. This isolates how much of the primary score's gap is
overload-identity granularity (a resolver that keeps one node per overload
set, with no arity in its symbol id, is wrong about *which* overload but not
about the class or method) versus a real misresolution. Reuses
`compare.py`'s own `load_oracle`/`in_scope`/`EXTRACT`/`line_offsets` and
`arms`/`stats` -- the only new logic is the overload grouping itself
(`callsites.declaration_identity`, also shared, not copied).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GRAPH = Path(__file__).resolve().parents[2]  # repowise-bench/graph
_G4 = _GRAPH / "experiments" / "g4-oracle-anchored"
_BENCH_LIB = _GRAPH / "lib"
_BENCH_ARMS = _GRAPH / "arms"
for p in (_HERE, _G4, _BENCH_LIB, _BENCH_ARMS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import arms as arms_lib  # noqa: E402
import compare as g4_compare  # noqa: E402
import stats  # noqa: E402

import callsites  # noqa: E402

Key = tuple[str, int, str, int]


def _decl_identity_getter():
    """Cache both parsed-file bytes and per-(file,line) identity lookups
    across the whole scoring run -- every edge sharing a callee file reuses
    one parse instead of re-tokenising it per edge."""
    ident_cache: dict[tuple[str, int], tuple[str, str] | None] = {}
    source_cache: dict[str, bytes] = {}

    def get(repo_root: Path, language: str, file: str, line: int) -> tuple[str, str] | None:
        key = (file, line)
        if key in ident_cache:
            return ident_cache[key]
        if file not in source_cache:
            try:
                source_cache[file] = (repo_root / file).read_bytes()
            except OSError:
                source_cache[file] = b""
        source = source_cache[file]
        ident = (
            callsites.declaration_identity(Path(file), source, language, line)
            if source
            else None
        )
        ident_cache[key] = ident
        return ident

    return get


def overload_set_metrics(
    *, oracle: set[Key], keys: set[Key], reachable: set[tuple[str, int]],
    repo_root: Path, language: str,
) -> dict:
    """SECONDARY score: group callees by (file, enclosing class, method
    name) instead of (file, line) and rescore matched/contradicted/recall on
    that coarser key. A callee whose `declaration_identity` cannot be
    resolved (no type context, lambda, etc.) keeps its own line as a
    singleton group, i.e. it only ever matches itself, same as the strict
    score -- this metric only ever adds matches, never removes real ones.
    """
    get_identity = _decl_identity_getter()

    def group(file: str, line: int) -> tuple[str, str, str] | None:
        ident = get_identity(repo_root, language, file, line)
        return (file, ident[0], ident[1]) if ident else None

    oracle_groups_by_caller: dict[tuple[str, int], set] = defaultdict(set)
    for cf, cl, tf, tl in oracle:
        g = group(tf, tl)
        if g is not None:
            oracle_groups_by_caller[(cf, cl)].add(g)

    keys_groups_by_caller: dict[tuple[str, int], set] = defaultdict(set)
    for cf, cl, tf, tl in keys:
        g = group(tf, tl)
        if g is not None:
            keys_groups_by_caller[(cf, cl)].add(g)

    matched_strict = keys & oracle
    outside = keys - oracle
    contradicted_strict = {k for k in outside if (k[0], k[1]) in reachable}

    rescued = 0
    for cf, cl, tf, tl in contradicted_strict:
        g = group(tf, tl)
        if g is not None and g in oracle_groups_by_caller.get((cf, cl), ()):
            rescued += 1
    matched_for_precision = len(matched_strict) + rescued
    contradicted_overload = len(contradicted_strict) - rescued
    denom = matched_for_precision + contradicted_overload
    prec = stats.wilson(matched_for_precision, denom) if denom else None

    matched_for_recall = 0
    for edge in oracle:
        if edge in matched_strict:
            matched_for_recall += 1
            continue
        cf, cl, tf, tl = edge
        g = group(tf, tl)
        if g is not None and g in keys_groups_by_caller.get((cf, cl), ()):
            matched_for_recall += 1
    rec = stats.wilson(matched_for_recall, len(oracle)) if oracle else None

    return {
        "note": (
            "SECONDARY -- callee grouped by (file, enclosing class, method name); "
            "a same-named overload of the true target counts as matched. The "
            "strict score stays primary. Shows how much of the gap is overload "
            "identity granularity (repowise keeps one node per overload set, no "
            "arity in the symbol id) versus real misresolution."
        ),
        "matched": matched_for_precision,
        "rescued_from_contradicted": rescued,
        "contradicted": contradicted_overload,
        "precision_vs_oracle": round(matched_for_precision / denom, 4) if denom else None,
        "precision_ci": [round(prec.low, 4), round(prec.high, 4)] if prec else None,
        "recall": round(matched_for_recall / len(oracle), 4) if oracle else None,
        "recall_ci": [round(rec.low, 4), round(rec.high, 4)] if rec else None,
    }


def _load_ambiguous_callers(path: str) -> set[tuple[str, int]]:
    """Ambiguous-caller correction: a callee symbol with more than one
    in-repo definition (scip-dotnet arity collapse for C#; for the Python
    jedi provider, a single call site whose `goto` genuinely returns >1
    distinct in-repo definition -- e.g. a platform-branched `if WIN: def
    f(): ... else: def f(): ...` pair) cannot be judged matched/contradicted
    against one oracle answer, so its caller is scored UNJUDGED instead.
    File format: one `file\\tline` per line (caller_decl_file/line), written
    by the provider alongside oracle.jsonl (see jedi_provider.py)."""
    out: set[tuple[str, int]] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        file, _, ln = line.rpartition("\t")
        out.add((file, int(ln)))
    return out


def main() -> int:
    local = argparse.ArgumentParser(add_help=False)
    local.add_argument("--overload-set", action="store_true")
    local.add_argument("--ambiguous-callers", default=None)
    local.add_argument(
        "--language",
        default=None,
        choices=["go", "typescript", "java", "csharp", "rust", "python", "c", "cpp"],
    )
    ours, rest = local.parse_known_args()

    if not ours.overload_set and not ours.ambiguous_callers:
        # Unchanged path: exactly compare.py's own CLI and output.
        sys.argv = [sys.argv[0], *rest]
        return g4_compare.main()

    if ours.overload_set and not ours.language:
        print("--overload-set requires --language", file=sys.stderr)
        return 2

    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--arms", default="repowise")
    ap.add_argument("--out", default=None)
    ap.add_argument("--pin", default=None)
    args = ap.parse_args(rest)

    header, oracle_all, reachable = g4_compare.load_oracle(Path(args.oracle))
    analysed = set(header["analysed_files"])
    oracle = g4_compare.in_scope(oracle_all, analysed)
    repo = Path(args.repo).resolve()
    name = args.repo_name or repo.name

    ambiguous_callers = (
        _load_ambiguous_callers(ours.ambiguous_callers) if ours.ambiguous_callers else None
    )
    if ambiguous_callers:
        before = len(oracle)
        oracle = {k for k in oracle if (k[0], k[1]) not in ambiguous_callers}
        print(
            f"ambiguous-callers correction: {len(ambiguous_callers)} caller(s) with >1 "
            f"in-repo callee definition, dropping {before - len(oracle)} oracle edge(s) "
            f"from scope (scored UNJUDGED, not matched/contradicted)\n"
        )

    print(f"oracle: {header['oracle']} algo={header['algorithm']} "
          f"roots={header['root_count']} load_errors={header['load_errors']}")
    print(f"analysed files: {len(analysed)}   oracle edges: {len(oracle_all)} "
          f"-> {len(oracle)} in scope   reachable funcs: {len(reachable)}\n")

    rows: dict[str, dict] = {}
    for nm in args.arms.split(","):
        arm = arms_lib.get_arm(nm)
        art = (
            arms_lib.build_cached(arm, repo, repo_name=name, pin=args.pin)
            if args.pin
            else arm.build(repo, repo_name=name, fresh=True)
        )
        try:
            keys = g4_compare.in_scope(g4_compare.EXTRACT[nm](art), analysed)
            if ambiguous_callers:
                keys = {k for k in keys if (k[0], k[1]) not in ambiguous_callers}
            matched = keys & oracle
            outside = keys - oracle
            contradicted = {k for k in outside if (k[0], k[1]) in reachable}
            unjudged = outside - contradicted
            denom = len(matched) + len(contradicted)
            rec = stats.wilson(len(matched), len(oracle)) if oracle else None
            prec = stats.wilson(len(matched), denom) if denom else None
            row = {
                "version": art.version,
                "edges_in_scope": len(keys),
                "oracle_edges": len(oracle),
                "matched": len(matched),
                "recall": round(len(matched) / len(oracle), 4) if oracle else None,
                "recall_ci": [round(rec.low, 4), round(rec.high, 4)] if rec else None,
                "outside_oracle": len(outside),
                "outside_share": round(len(outside) / len(keys), 4) if keys else None,
                "contradicted": len(contradicted),
                "unjudged": len(unjudged),
                "precision_vs_oracle": round(len(matched) / denom, 4) if denom else None,
                "precision_ci": [round(prec.low, 4), round(prec.high, 4)] if prec else None,
                "unjudged_share": round(len(unjudged) / len(keys), 4) if keys else None,
                "line_offsets_top": [
                    [str(k), v] for k, v in g4_compare.line_offsets(keys, oracle).most_common(6)
                ],
                "ambiguous_callers_excluded": len(ambiguous_callers) if ambiguous_callers else 0,
            }
            if ours.overload_set:
                row["overload_set"] = overload_set_metrics(
                    oracle=oracle, keys=keys, reachable=reachable,
                    repo_root=repo, language=ours.language,
                )
            rows[nm] = row
            print(f"{nm:<22} v{row['version']}")
            print(f"  STRICT       matched={row['matched']:<6} "
                  f"contradicted={row['contradicted']:<6} "
                  f"precision={row['precision_vs_oracle']} recall={row['recall']}")
            if not ours.overload_set:
                continue
            os_ = row["overload_set"]
            print(f"  OVERLOAD-SET matched={os_['matched']:<6} "
                  f"(+{os_['rescued_from_contradicted']} rescued) "
                  f"contradicted={os_['contradicted']:<6} "
                  f"precision={os_['precision_vs_oracle']} recall={os_['recall']}\n")
        finally:
            arm.close(art)

    if args.out:
        payload = {
            "oracle": {k: v for k, v in header.items() if k != "analysed_files"},
            "repo": name,
            "arms": rows,
        }
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
