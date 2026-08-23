"""G2 and G6 over every arm and every repository, from one build each.

Replaces `g2-cross-file-coverage/run_peer.py` and `run_ours.py`, which were one
script per arm and could not have been extended to four without becoming four.
An arm is now a name on the command line.

G2 and G6 share a runner because they share a build. Indexing caffeine twice to
answer two questions about one index is most of an hour for nothing.

    python graph/experiments/run_corpus.py --arms repowise,codegraph
    python graph/experiments/run_corpus.py --repos gitleaks --arms all --no-warmup
    python graph/experiments/run_corpus.py --arms all --use-cache --dry-run

## Which repositories

Read from `graph/corpus/corpus.lock` through `graph/lib/corpus.py`, which
`prebuild_artifacts.py` shares so the sweep cannot ask for a peer artifact the
prebuild never built. Every row records its pin, kind and file count at the pin,
and the result carries the selection and the per-language kind coverage, so a
language sitting at n=1 is visible in the document rather than only in a plan.

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

# One document, two experiments. A caveat rarely compromises both.
G2 = "g2-cross-file-coverage"
G6 = "g6-build-cost"
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

import arms as arms_lib  # noqa: E402
import corpus as corpus_lib  # noqa: E402
import provenance  # noqa: E402
import stats  # noqa: E402

SCHEMA = "graph-result/1"

# The corpus is read from `graph/corpus/corpus.lock` via `corpus_lib.select`,
# which `prebuild_artifacts.py` also uses so the two cannot disagree about what
# exists. It used to be a hardcoded six -- typescript x2, java x1, csharp x1,
# python x1, go x1 -- and that constant was the whole reason every per-language
# claim on the published page rested on a single repository.

# Language names differ between arms: we say `csharp`, and an arm that says
# `c_sharp` or `cs` would silently produce an empty language-filtered
# denominator, which reads as a coverage of zero rather than as a bug. Every
# arm's names are normalised through this before filtering, and an unmapped
# name passes through unchanged rather than being dropped.
_LANG_ALIASES = {
    # codebase-memory-mcp reports C++ as their own table spells it, "C++", and
    # the corpus spells it "cpp". Folded here rather than in that adapter, so
    # the adapter stays a mirror of their table and every arm is normalised in
    # one place.
    "c++": "cpp",
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


def measure(
    arm,
    repo_path: Path,
    repo_name: str,
    language: str,
    warmup: bool,
    *,
    pin: str | None = None,
    use_cache: bool = False,
) -> dict:
    """Build once (after a discarded warmup) and read every protocol set off it.

    With `use_cache`, an arm that stores an artifact on disk is restored from the
    prebuild cache rather than rebuilt. That is the only way thirty-five
    repositories times five arms fits in a session: the three competitor arms
    cost the whole budget, and their artifacts depend on `(tool, version, repo,
    pin)` and not at all on our commit.

    Coverage is unaffected -- `artifact_cache` round-trips every protocol set
    byte for byte, checked on cobra before the sweep that filled it -- but **the
    cost row then belongs to the build that filled the cache**, not to this run.
    It is passed through flagged `from_cache` and the run is stamped not
    publishable, because a G6 table must not quote a restore. Our own arms hold
    nothing on disk to cache and always build fresh.
    """
    cached = bool(use_cache and pin and hasattr(arm, "cache_payload"))

    if warmup and not cached:
        art = arm.build(repo_path, repo_name=repo_name, fresh=True)
        arm.close(art)

    started = time.perf_counter()
    if cached:
        art = arms_lib.build_cached(arm, repo_path, repo_name=repo_name, pin=pin, fresh=False)
    else:
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
                "warmup_discarded": warmup and not cached,
                # True means the seconds and RSS above were measured on an
                # earlier real build and restored here, never on this run.
                "from_cache": bool(art.extra.get("from_cache")),
                "cost_measured_at": art.extra.get("cost_measured_at"),
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
            "_sets": {
                "files_seen": seen,
                "symbol_files": sym,
                "in_language": in_lang,
                # Covered-file sets, so `shared_denominator` can score a shared
                # denominator rather than only measure its size. Sizes alone
                # were what the caffeine correction had to be done by hand from.
                "covered": {
                    f"{set_name}__{direction}": covered_files(
                        arm.cross_file_edges(art, kinds), direction
                    )
                    for set_name, kinds in EDGE_SETS.items()
                    for direction in DIRECTIONS
                },
            },
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

    out["pairwise"] = _pairwise_shared_coverage(rows, shared_seen)

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


def _pairwise_shared_coverage(rows: dict[str, dict], shared_seen: set[str]) -> dict:
    """Coverage for each arm pair on the files **both** call symbol-bearing.

    The own-denominator rate each arm reports is not a comparison: two arms
    disagree about which files can carry an edge at all, so they are answering
    the same question about different populations. On caffeine that mattered
    more than the headline -- 123 `package-info.java` files padded the peer's
    denominator, and recounting on a shared one turned a 0.608-to-0.517 win
    into a 0.640-to-0.608 loss inside overlapping intervals.

    The denominator is the **intersection**, not the union the docstring above
    once described. With five arms a union is worse than useless: graphify
    emits a node for every file it walks -- 401 of 401 on zod, 355 of 357 on
    hono -- so a union re-pads the denominator with test files and barrels that
    nothing can import, which is the very thing this correction exists to
    remove. An intersection asks only about files both tools agree declare a
    symbol, which is the population where a disagreement about edges is real.

    Reported per pair rather than as one all-arms intersection, because a
    single intersection across five arms is dominated by the most conservative
    of them and answers nobody's question.
    """
    names = sorted(rows)
    out: dict[str, dict] = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ra, rb = rows[a], rows[b]
            denom = (
                shared_seen
                & ra["_sets"]["symbol_files"] & rb["_sets"]["symbol_files"]
                & ra["_sets"]["in_language"] & rb["_sets"]["in_language"]
            )
            cell: dict = {"denominator": len(denom)}
            if denom:
                for arm_name, row in ((a, ra), (b, rb)):
                    got = {}
                    for key, cov in row["_sets"]["covered"].items():
                        hit = cov & denom
                        iv = stats.wilson(len(hit), len(denom))
                        got[key] = {
                            "covered": len(hit),
                            "rate": round(len(hit) / len(denom), 4),
                            "ci_low": round(iv.low, 4),
                            "ci_high": round(iv.high, 4),
                        }
                    cell[arm_name] = got
            out[f"{a}__vs__{b}"] = cell
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", default="repowise,codegraph", help="comma-separated, or 'all'")
    ap.add_argument("--repos", default="all", help="comma-separated, or 'all'")
    ap.add_argument("--kinds", default="", help="library,application,framework")
    ap.add_argument("--languages", default="", help="comma-separated")
    ap.add_argument("--lock", default=str(corpus_lib.LOCK))
    ap.add_argument(
        "--max-files", type=int, default=corpus_lib.DEFAULT_MAX_FILES,
        help="size cap; never drops a (language, kind) slot's only member",
    )
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument(
        "--use-cache", action="store_true",
        help="restore competitor artifacts from the prebuild cache instead of "
             "rebuilding them. Coverage is unaffected -- a restore reproduces "
             "every protocol set byte for byte -- but the cost row is then the "
             "one measured at build time, so G6 is stamped not publishable.",
    )
    ap.add_argument("--no-warmup", action="store_true", help="dev only; not publishable")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="print the selection and the per-language coverage, build nothing",
    )
    ap.add_argument("--out-root", default=str(BENCH / "results/graph/g2g6"))
    args = ap.parse_args()

    arm_names = arms_lib.arm_names() if args.arms == "all" else args.arms.split(",")
    selection = corpus_lib.select(
        lock=args.lock,
        repos=args.repos,
        kinds=args.kinds,
        languages=args.languages,
        max_files=args.max_files,
    )
    for line in selection.describe():
        print(line)
    coverage = corpus_lib.language_coverage(selection.rows)
    for lang in sorted(coverage):
        c = coverage[lang]
        flag = "" if c["three_kinds"] else "   <- not three kinds, no language claim"
        print(f"  {lang:12s} n={c['n']}  {','.join(k or '?' for k in c['kinds'])}{flag}")
    if args.dry_run:
        for r in selection.rows:
            print(f"  {r['name']:24s} {r['language'] or '?':12s} "
                  f"{r['kind'] or '?':12s} {r['files']:6d} files  {r['pin'][:8]}")
        return 0
    test_repos = Path(args.test_repos).resolve()

    # The measured tree is whichever checkout `repowise.core` imports from --
    # the detached worktree, not this one -- so the guard has to gate on that
    # tree. Gating on the bench checkout would pass while measuring someone
    # else's half-finished ingestion change.
    import repowise.core

    # Walk up to the checkout root rather than counting path segments. The
    # count was off by one -- it landed on `packages/` -- so the dirty guard
    # was scoped to `packages/packages`, matched nothing, and passed every run
    # regardless of the tree. `provenance.require_clean` now refuses a scope
    # that matches nothing, but the root has to be right for it to have
    # anything to check.
    measured_tree = Path(repowise.core.__file__).resolve()
    for parent in measured_tree.parents:
        if (parent / ".git").exists():
            measured_tree = parent
            break
    else:
        raise SystemExit(
            f"no git checkout above {repowise.core.__file__}; refusing to run "
            "a measurement whose source tree cannot be identified"
        )
    publishable = provenance.require_clean(
        measured_tree, bench_repo=BENCH, allow_dirty=args.allow_dirty
    )
    reasons: list[str] = []
    # Coverage survives a restored artifact -- every protocol set round-trips
    # byte for byte -- but the cost row does not. Stamping the document as a
    # whole made the sound half unciteable: the coverage sweep this produced was
    # read as not publishable on the strength of a caveat about cost.
    ok = {G2: publishable, G6: publishable}
    why: dict[str, str] = {}
    if not publishable:
        dirty = "--allow-dirty: the measured tree has uncommitted changes"
        reasons.append(dirty)
        why[G2] = why[G6] = dirty
    if args.no_warmup:
        ok[G6] = False
        why[G6] = ("--no-warmup: the first build on a repository is cold, and "
                   "gitleaks measured 6.92s cold against 1.90s warm")
        reasons.append(why[G6])
    cost_from_cache = args.use_cache
    if cost_from_cache:
        ok[G6] = False
        why[G6] = ("--use-cache: competitor cost rows were measured when the "
                   "artifact cache was filled, not on this run. Coverage is "
                   "unaffected; G6 needs a run without it")
        reasons.append(why[G6])

    result: dict = {
        "schema": SCHEMA,
        "experiments": [G2, G6],
        "provenance": provenance.stamp(
            "g2g6",
            repowise_repo=measured_tree,
            bench_repo=BENCH,
            publishable=ok,
            reasons=why,
            extra={
                "warmup": not args.no_warmup,
                "arms_requested": arm_names,
                "measured_tree": str(measured_tree),
                "cost_from_cache": cost_from_cache,
                "caveats": reasons,
                "corpus": {
                    "lock": str(args.lock),
                    "selected": selection.names(),
                    "skipped_oversize": [r["name"] for r in selection.oversize],
                    "kept_oversize": [r["name"] for r in selection.kept_oversize],
                    "max_files": args.max_files,
                    # Printed beside G7 so a one-repository language cannot be
                    # read with the weight of one measured three ways.
                    "language_coverage": corpus_lib.language_coverage(selection.rows),
                },
            },
        ),
        "repos": {},
    }

    for entry in selection.rows:
        repo_name = entry["name"]
        language = norm_lang(entry.get("language")) or "unknown"
        repo_path = test_repos / repo_name
        if not repo_path.is_dir():
            print(f"  !! no checkout at {repo_path}, skipping", file=sys.stderr)
            continue
        print(f"\n=== {repo_name} ({language}/{entry.get('kind')}, "
              f"{entry['files']} files) ===", flush=True)
        rows: dict[str, dict] = {}
        for arm_name in arm_names:
            arm = arms_lib.get_arm(arm_name)
            print(f"  {arm_name} ...", end="", flush=True)
            try:
                rows[arm_name] = measure(
                    arm, repo_path, repo_name, language,
                    warmup=not args.no_warmup,
                    pin=entry.get("pin"),
                    use_cache=args.use_cache,
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
            "kind": entry.get("kind"),
            "pin": entry.get("pin"),
            "files_at_pin": entry.get("files"),
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
        print("STAMPED NOT PUBLISHABLE")
        for r in reasons:
            print(f"  - {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
