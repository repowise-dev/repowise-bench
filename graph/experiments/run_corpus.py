"""G2 and G6 over every arm and every repository, from one build each.

Replaces `g2-cross-file-coverage/run_peer.py` and `run_ours.py`, which were one
script per arm and could not have been extended to four without becoming four.
An arm is now a name on the command line.

G2 and G6 share a runner because they share a build. Indexing caffeine twice to
answer two questions about one index is most of an hour for nothing.

    python graph/experiments/run_corpus.py --arms repowise,codegraph
    python graph/experiments/run_corpus.py --repos gitleaks --arms all --no-warmup

## What gets written

`results/graph/g2g6/<date>-<commit>/result.json`, one document per run, schema
`graph-result/1`. Tables are generated from these by `graph/tools/render.py`
and are never typed by hand: the retrieval bench has been bitten twice by a
table that drifted from its own raw data.

## The two denominators, and why both are reported

**Own denominator** is each arm's own symbol-bearing files. It reproduces
CodeGraph's published metric on both sides and is comparable with every G2
number measured before this script existed.

**Shared denominator** is the intersection of the two arms' `files_seen`,
further intersected with the union of their symbol-bearing files. This is the
fair reading, and on caffeine it is not a rounding difference: we walk files
the peer does not and the peer finds symbols in 128 java files where we find
none, so the own-denominator rows for caffeine and zod are **not comparable**
until that gap is explained. Both columns are emitted so a reader can see the
size of the correction rather than take our word for it.

## Warmup

One discarded build per (repo, arm), because gitleaks measured 6.92s cold
against 1.90s warm and the whole difference would otherwise land on whichever
arm ran first. `--no-warmup` halves the wall clock and is for development only;
it stamps `warmup: false` on the result, and a cost table from such a run is
not publishable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

import arms as arms_lib  # noqa: E402
import provenance  # noqa: E402
import stats  # noqa: E402

SCHEMA = "graph-result/1"

# Repositories largest first. Everything measured so far is one small Go
# repository and the ratios there will not hold on caffeine; if an arm is going
# to fall over on a big repository it is cheaper to learn that in the first ten
# minutes than in the last.
CORPUS = [
    ("dub", "typescript", 4066),
    ("caffeine", "java", 786),
    ("Ocelot", "csharp", 772),
    ("celery", "python", 436),
    ("zod", "typescript", 422),
    ("gitleaks", "go", 226),
]

# Language names differ between arms: we say `csharp`, and an arm that says
# `c_sharp` or `cs` would silently produce an empty language-filtered
# denominator, which reads as a coverage of zero rather than as a bug. Every
# arm's names are normalised through this before filtering, and an unmapped
# name passes through unchanged rather than being dropped.
_LANG_ALIASES = {
    "c#": "csharp",
    "c_sharp": "csharp",
    "cs": "csharp",
    "ts": "typescript",
    "tsx": "typescript",
    "typescriptreact": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "py": "python",
    "golang": "go",
    "kt": "kotlin",
}


def norm_lang(lang: str | None) -> str | None:
    return _LANG_ALIASES.get((lang or "").lower(), (lang or "").lower()) or None


EDGE_SETS = {
    "any_dependency": None,  # each arm's own dependency vocabulary
    "calls_only": arms_lib.CALLS,
}
DIRECTIONS = ("either", "incoming", "outgoing")


def covered_files(
    edges: set[tuple[str, str]], direction: str
) -> set[str]:
    """Files sitting at the requested end of at least one cross-file edge."""
    if direction == "incoming":
        return {t for _, t in edges}
    if direction == "outgoing":
        return {s for s, _ in edges}
    return {f for e in edges for f in e}


def measure(arm, repo_path: Path, repo_name: str, language: str, warmup: bool) -> dict:
    """Build once (after a discarded warmup) and read every protocol set off it."""
    if warmup:
        art = arm.build(repo_path, repo_name=repo_name, fresh=True)
        arm.close(art)

    started = time.perf_counter()
    art = arm.build(repo_path, repo_name=repo_name, fresh=True)
    wall = time.perf_counter() - started
    try:
        langs = {p: norm_lang(l) for p, l in arm.file_languages(art).items()}
        seen = arm.files_seen(art)
        sym = arm.symbol_files(art)
        calls = arm.call_edges(art)
        in_lang = {p for p, l in langs.items() if l == language}

        cells: dict[str, dict] = {}
        for set_name, kinds in EDGE_SETS.items():
            edges = arm.cross_file_edges(art, kinds)
            for direction in DIRECTIONS:
                cov = covered_files(edges, direction)
                for scope, denom in (
                    ("primary_language", sym & in_lang),
                    ("all_files", sym),
                ):
                    hit = cov & denom
                    iv = stats.wilson(len(hit), len(denom))
                    cells[f"{scope}__{set_name}__{direction}"] = {
                        "covered": len(hit),
                        "symbol_bearing": len(denom),
                        "rate": round(len(hit) / len(denom), 4) if denom else None,
                        "ci_low": round(iv.low, 4) if denom else None,
                        "ci_high": round(iv.high, 4) if denom else None,
                    }

        return {
            "version": art.version,
            "cost": {
                **art.cost_row(),
                "harness_wall_sec": round(wall, 3),
                "warmup_discarded": warmup,
            },
            "counts": {
                "files_seen": len(seen),
                "symbol_files": len(sym),
                "symbol_files_in_language": len(sym & in_lang),
                "files_in_language": len(in_lang),
                "call_edges_distinct": len(calls),
            },
            "coverage": cells,
            "extra": art.extra,
            # Kept for the cross-arm intersection below. Dropped before the
            # result is written: six repositories' worth of file lists is
            # megabytes of JSON nobody reads, and the intersection sizes are
            # what any comparison actually uses.
            "_sets": {"files_seen": seen, "symbol_files": sym, "in_language": in_lang},
        }
    finally:
        arm.close(art)


def shared_denominator(rows: dict[str, dict], language: str) -> dict:
    """The intersection every cross-arm comparison has to start from.

    A tool that skips `vendor/` must not be credited with perfect recall on the
    part it read, and a tool that walks more files than another must not be
    charged for the extra. This is the only place that correction is computed,
    so it cannot drift between experiments.
    """
    if len(rows) < 2:
        return {}
    seen_sets = [r["_sets"]["files_seen"] for r in rows.values()]
    shared_seen = set.intersection(*seen_sets)
    out: dict = {
        "arms": sorted(rows),
        "files_seen_intersection": len(shared_seen),
        "files_seen_per_arm": {a: len(r["_sets"]["files_seen"]) for a, r in rows.items()},
        "files_seen_only_in": {
            a: len(r["_sets"]["files_seen"] - shared_seen) for a, r in rows.items()
        },
        "symbol_files_on_shared_seen": {},
        "symbol_files_in_language_on_shared_seen": {},
    }
    for arm_name, row in rows.items():
        sym_shared = row["_sets"]["symbol_files"] & shared_seen
        out["symbol_files_on_shared_seen"][arm_name] = len(sym_shared)
        out["symbol_files_in_language_on_shared_seen"][arm_name] = len(
            sym_shared & row["_sets"]["in_language"]
        )

    # The symbol gap, per language, in both directions. This is the
    # measurement that sized the caffeine 128-file finding, and it is computed
    # here for every repository rather than by hand for one.
    lang_sym = {
        a: r["_sets"]["symbol_files"] & r["_sets"]["in_language"] & shared_seen
        for a, r in rows.items()
    }
    out["symbol_gap_in_language"] = {
        f"{a}_only": len(lang_sym[a] - set.union(*[v for b, v in lang_sym.items() if b != a]))
        for a in lang_sym
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", default="repowise,codegraph", help="comma-separated, or 'all'")
    ap.add_argument("--repos", default="all", help="comma-separated, or 'all'")
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--no-warmup", action="store_true", help="dev only; not publishable")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--out-root", default=str(BENCH / "results/graph/g2g6"))
    args = ap.parse_args()

    arm_names = arms_lib.arm_names() if args.arms == "all" else args.arms.split(",")
    selected = (
        CORPUS if args.repos == "all" else [c for c in CORPUS if c[0] in args.repos.split(",")]
    )
    test_repos = Path(args.test_repos).resolve()

    # The measured tree is whichever checkout `repowise.core` imports from --
    # the detached worktree, not this one -- so the guard has to gate on that
    # tree. Gating on the bench checkout would pass while measuring someone
    # else's half-finished ingestion change.
    import repowise.core

    measured_tree = Path(repowise.core.__file__).resolve().parents[4]
    publishable = provenance.require_clean(measured_tree, allow_dirty=args.allow_dirty)
    if args.no_warmup:
        publishable = False

    result: dict = {
        "schema": SCHEMA,
        "experiments": ["g2-cross-file-coverage", "g6-build-cost"],
        "provenance": provenance.stamp(
            "g2g6",
            repowise_repo=measured_tree,
            bench_repo=BENCH,
            publishable=publishable,
            extra={
                "warmup": not args.no_warmup,
                "arms_requested": arm_names,
                "measured_tree": str(measured_tree),
            },
        ),
        "repos": {},
    }

    for repo_name, language, _size in selected:
        repo_path = test_repos / repo_name
        if not repo_path.is_dir():
            print(f"  !! no checkout at {repo_path}, skipping", file=sys.stderr)
            continue
        print(f"\n=== {repo_name} ({language}) ===", flush=True)
        rows: dict[str, dict] = {}
        for arm_name in arm_names:
            arm = arms_lib.get_arm(arm_name)
            print(f"  {arm_name} ...", end="", flush=True)
            try:
                rows[arm_name] = measure(
                    arm, repo_path, repo_name, language, warmup=not args.no_warmup
                )
                c = rows[arm_name]
                print(
                    f" {c['cost']['seconds']}s "
                    f"{c['counts']['call_edges_distinct']} calls "
                    f"{c['counts']['symbol_files_in_language']} sym({language})",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - a failed arm is a result
                print(f" FAILED: {type(exc).__name__}: {exc}", flush=True)
                rows[arm_name] = {"error": f"{type(exc).__name__}: {exc}"}

        ok = {a: r for a, r in rows.items() if "error" not in r}
        result["repos"][repo_name] = {
            "language": language,
            "shared": shared_denominator(ok, language),
            "arms": {a: {k: v for k, v in r.items() if k != "_sets"} for a, r in rows.items()},
        }

    commit = (result["provenance"]["repowise"]["head_short"] or "nocommit")[:8]
    out_dir = Path(args.out_root) / f"{date.today().isoformat()}-{commit}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    if not publishable:
        print("STAMPED NOT PUBLISHABLE (dirty tree and/or --no-warmup)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
