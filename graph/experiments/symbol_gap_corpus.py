"""Size and bucket the symbol-extraction gap across the whole pinned corpus.

`probe_symbol_gap.py` answers this for one repository and one language, and
its classifier is Java-shaped (`package-info`, `@interface`). This is the
general form: every repository in `corpus.lock`, every language, and buckets
by *why* a file produces no symbol for us rather than by what it declares.

The three buckets the session brief asks for, and how each is decided:

* **extension never parsed** -- the file is not in our `files_seen` at all and
  its extension is not in the language registry. No grammar, no parse, no
  symbol. Decided from `REGISTRY.all_code_extensions`, not from a guess.
* **excluded by the traverser** -- not in `files_seen`, but the extension *is*
  registered. So a grammar exists and something else dropped the file: size
  cap, exclude pattern, nested repo, blocked directory.
* **parsed, yields no symbol** -- in `files_seen`, but not in `symbol_files`.
  The parse ran and produced nothing. caffeine's `package-info.java` and its
  `public @interface` files are both here.

The peer arm supplies the denominator: a file counts as gap only if the peer
calls it symbol-bearing, so "a file nobody extracts from" is never charged to
us. That is the same fairness rule the caffeine handover established.

Coverage upper bound, and it is an upper bound rather than a projection: if
every gap file in the primary language became symbol-bearing *and* something
already resolved into it, G2 would read

    (covered + gap) / (symbol_bearing + gap)

Nothing here claims that edge would exist. It is the ceiling the lever can
reach, which is exactly the number that decides whether the lever is worth
building.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BENCH / "graph" / "lib"))

import arms as arms_lib  # noqa: E402
import corpus as corpus_lib  # noqa: E402


def registered_extensions() -> set[str]:
    from repowise.core.ingestion.languages.registry import REGISTRY

    return {e.lower() for e in REGISTRY.all_code_extensions()}


def bucket(rel: str, seen: set[str], exts: set[str]) -> str:
    if rel in seen:
        return "parsed, yields no symbol"
    ext = Path(rel).suffix.lower()
    if ext and ext in exts:
        return "excluded by the traverser"
    return "extension never parsed"


def probe(repo_row: dict, test_repos: Path, peers: list[str], exts: set[str],
          g2: dict) -> dict:
    name = repo_row["name"]
    repo_path = test_repos / name
    language = repo_row["language"]

    ours = arms_lib.get_arm("repowise")
    a = ours.build(repo_path, repo_name=name, fresh=True)
    try:
        our_seen = ours.files_seen(a)
        our_symbol = ours.symbol_files(a)
        our_langs = {p: (l or "").lower() for p, l in ours.file_languages(a).items()}
    finally:
        ours.close(a)

    peer_symbol: dict[str, set[str]] = {}
    peer_in_lang: dict[str, set[str]] = {}
    for pname in peers:
        arm = arms_lib.get_arm(pname)
        art = arms_lib.build_cached(arm, repo_path, repo_name=name, pin=repo_row["pin"])
        try:
            langs = {p: (l or "").lower() for p, l in arm.file_languages(art).items()}
            peer_symbol[pname] = arm.symbol_files(art)
            peer_in_lang[pname] = {p for p, l in langs.items() if l == language}
        finally:
            arm.close(art)

    head = peers[0]
    gap = sorted(peer_symbol[head] - our_symbol)
    gap_in_lang = sorted((peer_symbol[head] & peer_in_lang[head]) - our_symbol)
    union_symbol = set().union(*peer_symbol.values())
    union_in_lang = set().union(*peer_in_lang.values())
    gap_union_in_lang = sorted((union_symbol & union_in_lang) - our_symbol)

    buckets: Counter[str] = Counter()
    by_bucket_ext: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    for rel in gap:
        bk = bucket(rel, our_seen, exts)
        buckets[bk] += 1
        by_bucket_ext[bk][Path(rel).suffix.lower() or "<no ext>"] += 1
        examples[bk].append(rel)

    def bucket_set(rels: list[str]) -> tuple[dict, dict, dict]:
        b: Counter[str] = Counter()
        ex: dict[str, list[str]] = defaultdict(list)
        be: dict[str, Counter[str]] = defaultdict(Counter)
        for rel in rels:
            bk = bucket(rel, our_seen, exts)
            b[bk] += 1
            be[bk][Path(rel).suffix.lower() or "<no ext>"] += 1
            ex[bk].append(rel)
        return (dict(b.most_common()),
                {k: dict(v.most_common()) for k, v in be.items()},
                {k: v[:20] for k, v in ex.items()})

    lang_buckets, lang_ext, lang_examples = bucket_set(gap_in_lang)
    # The union gap is the one the zod/hono lead lives in: graphify calls files
    # symbol-bearing that CodeGraph does not, so a codegraph-only denominator
    # understates the ceiling on exactly the repositories that motivated this.
    union_buckets, union_ext, union_examples = bucket_set(gap_union_in_lang)

    # The published G2 cell, read off the corpus result rather than recomputed,
    # so the ceiling below cannot disagree with the table it is a ceiling on.
    cov = g2["covered"]
    sym = g2["symbol_bearing"]
    rate = cov / sym if sym else 0.0

    def ceiling(g: int) -> dict:
        c = (cov + g) / (sym + g) if (sym + g) else 0.0
        return {"gap": g, "rate_ceiling": round(c, 4), "delta_upper_bound": round(c - rate, 4)}

    return {
        "repo": name,
        "language": language,
        "kind": repo_row.get("kind"),
        "pin": repo_row["pin"],
        "peers": peers,
        "counts": {
            "our_files_seen": len(our_seen),
            "our_symbol_files": len(our_symbol),
            "peer_symbol_files": {k: len(v) for k, v in peer_symbol.items()},
            "our_symbol_in_language_published": sym,
            "our_covered_in_language_published": cov,
            "gap_all_languages": len(gap),
            "gap_in_primary_language": len(gap_in_lang),
            "gap_in_primary_language_union_of_peers": len(gap_union_in_lang),
        },
        "buckets": dict(buckets.most_common()),
        "buckets_in_primary_language": lang_buckets,
        "extensions_by_bucket_in_primary_language": lang_ext,
        "buckets_in_primary_language_union_of_peers": union_buckets,
        "extensions_by_bucket_union_of_peers": union_ext,
        "examples_union_of_peers": union_examples,
        "full_gap_list_union_in_primary_language": gap_union_in_lang,
        "peer_symbol_only_in": {
            k: len((v & peer_in_lang[k]) - our_symbol) for k, v in peer_symbol.items()
        },
        "extensions_by_bucket": {k: dict(v.most_common()) for k, v in by_bucket_ext.items()},
        "g2": {
            "rate_now": round(rate, 4),
            "vs_head_peer": ceiling(len(gap_in_lang)),
            "vs_peer_union": ceiling(len(gap_union_in_lang)),
        },
        "examples": {k: v[:20] for k, v in examples.items()},
        "examples_in_primary_language": lang_examples,
        "full_gap_list": gap,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--peers", default="codegraph,graphify,code-review-graph")
    ap.add_argument("--g2-result", default=str(BENCH / "results" / "graph" / "g2g6"
                                              / "2026-08-18-58576af0" / "result.json"))
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--only", help="comma-separated repo names")
    ap.add_argument("--max-files", type=int, default=corpus_lib.DEFAULT_MAX_FILES)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sel = corpus_lib.select(max_files=args.max_files)
    rows = sel.rows
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if r["name"] in want]

    peers = [p.strip() for p in args.peers.split(",")]
    g2res = json.loads(Path(args.g2_result).read_text(encoding="utf-8"))["repos"]
    exts = registered_extensions()
    test_repos = Path(args.test_repos).resolve()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    if out_path.is_file():
        results = json.loads(out_path.read_text(encoding="utf-8")).get("repos", [])
    done = {r["repo"] for r in results}

    for row in rows:
        if row["name"] in done:
            print(f"skip {row['name']} (already in {out_path.name})", flush=True)
            continue
        print(f"=== {row['name']} ({row['language']}, {row['files']} files)", flush=True)
        try:
            cell = g2res[row["name"]]["arms"]["repowise"]["coverage"][
                "primary_language__calls_only__incoming"
            ]
            res = probe(row, test_repos, peers, exts, cell)
        except Exception:
            traceback.print_exc()
            res = {"repo": row["name"], "language": row["language"], "error": True}
        results.append(res)
        out_path.write_text(
            json.dumps({"peers": peers, "repos": results}, indent=2), encoding="utf-8"
        )
        c = res.get("counts", {})
        print(
            f"    gap {c.get('gap_all_languages')} "
            f"(in-lang {c.get('gap_in_primary_language')})  "
            f"buckets {res.get('buckets')}  g2 {res.get('g2')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
