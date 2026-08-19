"""G4: recall and the outside-oracle bucket, against a graph neither tool produced.

Reports two numbers per arm and never one. Precision against a static oracle is
not precision: RTA over-approximates, so an edge outside its set is *probably*
wrong but not certainly, and folding recall and outside-rate into an F1 would
launder that uncertainty into a number that looks decisive. The outside bucket
is reported at full size so a reader can see how much of each tool's output the
oracle simply cannot speak to.

Key: `(caller_decl_file, caller_decl_line) -> (callee_decl_file, callee_decl_line)`,
function granularity, locations only. Names are never compared -- a name-matched
join is the failure mode this experiment exists to remove, and it would also
silently favour whichever tool spells identifiers most like the oracle.

Call-site granularity is deliberately NOT used: codebase-memory-mcp records no
line for a call site, so a site-keyed join would zero that arm out for a reason
about its storage rather than about its resolver.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "lib"))
sys.path.insert(0, str(BENCH / "graph" / "arms"))

import arms as arms_lib  # noqa: E402
import stats  # noqa: E402

Key = tuple[str, int, str, int]


def load_oracle(path: Path) -> tuple[dict, set[Key], set[tuple[str, int]]]:
    header: dict = {}
    edges: set[Key] = set()
    reachable: set[tuple[str, int]] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("_header"):
                header = o
                continue
            if o.get("_reachable"):
                reachable = {(f, l) for f, l in o["funcs"]}
                continue
            # An edge whose caller or callee has no declaration position is a
            # synthetic SSA function -- a wrapper or a thunk. It is dropped
            # rather than keyed on a zero, which would collide every such edge
            # onto one bucket and inflate whichever arm happened to emit there.
            if not o["caller_decl_file"] or not o["callee_file"]:
                continue
            edges.add(
                (o["caller_decl_file"], o["caller_decl_line"],
                 o["callee_file"], o["callee_line"])
            )
    return header, edges, reachable


def ours_keys(art) -> set[Key]:
    g = art.handle.graph
    out: set[Key] = set()
    for u, v, d in g.edges(data=True):
        if d.get("edge_type") != "calls":
            continue
        a, b = g.nodes[u], g.nodes[v]
        if a.get("node_type") != "symbol" or b.get("node_type") != "symbol":
            continue
        if a.get("file_path") is None or b.get("file_path") is None:
            continue
        out.add(
            (arms_lib.norm_path(a["file_path"]), int(a.get("start_line") or 0),
             arms_lib.norm_path(b["file_path"]), int(b.get("start_line") or 0))
        )
    return out


def _sql_keys(conn: sqlite3.Connection, sql: str, root: str | None) -> set[Key]:
    out: set[Key] = set()
    for sf, sl, tf, tl in conn.execute(sql):
        if not sf or not tf:
            continue
        out.add(
            (arms_lib.norm_path(sf, root), int(sl or 0),
             arms_lib.norm_path(tf, root), int(tl or 0))
        )
    return out


def codegraph_keys(art) -> set[Key]:
    return _sql_keys(
        art.handle,
        """
        SELECT DISTINCT src.file_path, src.start_line, tgt.file_path, tgt.start_line
        FROM edges e
        JOIN nodes src ON src.id = e.source
        JOIN nodes tgt ON tgt.id = e.target
        WHERE e.kind = 'calls'
        """,
        None,
    )


def cbm_keys(art) -> set[Key]:
    # CALLS and ASYNC_CALLS only, matching this arm's `calls` reading in the
    # adapter. CALL_REFERENCE is excluded there and is excluded here.
    return _sql_keys(
        art.handle["conn"],
        """
        SELECT DISTINCT src.file_path, src.start_line, tgt.file_path, tgt.start_line
        FROM edges e
        JOIN nodes src ON src.id = e.source_id
        JOIN nodes tgt ON tgt.id = e.target_id
        WHERE e.type IN ('CALLS','ASYNC_CALLS')
          AND src.file_path <> '' AND tgt.file_path <> ''
        """,
        art.handle["root"],
    )


def _graphify_line(loc) -> int:
    """`"L58"` -> 58, matching this arm's adapter. Malformed becomes -1 there
    and here, so a lineless edge folds the same way on both readers."""
    m = re.match(r"^L(\d+)$", str(loc or "").strip())
    return int(m.group(1)) if m else -1


def graphify_keys(art) -> set[Key]:
    """Both endpoints keyed at their own node's declaration position.

    A graphify call link carries a `source_file` / `source_location` of its own,
    but that is the call site rather than a declaration, so keying on it would
    compare this arm's call sites against every other arm's declarations. The
    node each endpoint points at is the declaration, which is the key the rest
    of this experiment uses.

    No confidence filter. 93% of this arm's call edges are `INFERRED` by its own
    tagging, and dropping those would score the tool on a tenth of its output
    while every other arm is scored on all of its own.
    """
    doc = art.handle["doc"]
    nodes = {n["id"]: n for n in doc.get("nodes", []) if "id" in n}
    out: set[Key] = set()
    for e in doc.get("links", []):
        if e.get("relation") != "calls":
            continue
        src, tgt = nodes.get(str(e.get("source"))), nodes.get(str(e.get("target")))
        if not src or not tgt:
            continue
        sf, tf = src.get("source_file"), tgt.get("source_file")
        if not sf or not tf:
            continue
        out.add(
            (arms_lib.norm_path(sf), _graphify_line(src.get("source_location")),
             arms_lib.norm_path(tf), _graphify_line(tgt.get("source_location")))
        )
    return out


def crg_keys(art) -> set[Key]:
    """Resolved `CALLS` only, both endpoints joined through `nodes`.

    The join is what enforces "resolved", exactly as this arm's adapter does it:
    an unresolved callee is stored as a bare identifier matching no
    `qualified_name`. Scoring the unresolved rows would charge the tool for
    edges it never claimed to have drawn.

    Its qualified names carry the absolute scratch directory it indexed, so
    every path goes through the same normaliser the adapter uses.
    """
    return _sql_keys(
        art.handle["conn"],
        """
        SELECT DISTINCT src.file_path, src.line_start, tgt.file_path, tgt.line_start
        FROM edges e
        JOIN nodes src ON src.qualified_name = e.source_qualified
        JOIN nodes tgt ON tgt.qualified_name = e.target_qualified
        WHERE e.kind = 'CALLS'
          AND src.file_path IS NOT NULL AND tgt.file_path IS NOT NULL
        """,
        art.handle["root"],
    )


EXTRACT = {
    "repowise": ours_keys,
    "codegraph": codegraph_keys,
    "codebase-memory-mcp": cbm_keys,
    "graphify": graphify_keys,
    "code-review-graph": crg_keys,
}


def in_scope(keys: set[Key], analysed: set[str]) -> set[Key]:
    """Both endpoints inside the file set the oracle actually type-checked."""
    return {k for k in keys if k[0] in analysed and k[2] in analysed}


def line_offsets(arm_keys: set[Key], oracle: set[Key]) -> Counter:
    """How far each arm's declaration lines sit from the oracle's.

    This is the validation the preregistration demands before any rate is
    quoted. If the modal offset is not (0, 0) then the two sides are keying
    declarations differently -- a doc comment counted in or out, a receiver on
    its own line -- and every rate below is meaningless rather than merely low.
    """
    by_files: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for cf, cl, tf, tl in oracle:
        by_files.setdefault((cf, tf), []).append((cl, tl))
    off: Counter = Counter()
    for cf, cl, tf, tl in arm_keys:
        cands = by_files.get((cf, tf))
        if not cands:
            off["no_file_pair"] += 1
            continue
        best = min(cands, key=lambda c: abs(c[0] - cl) + abs(c[1] - tl))
        off[(best[0] - cl, best[1] - tl)] += 1
    return off


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--arms",
                    default="repowise,codegraph,codebase-memory-mcp,graphify,code-review-graph")
    ap.add_argument("--out", default=None)
    ap.add_argument("--pin", default=None,
                    help="restore competitor artifacts from the cache for this pin "
                         "instead of rebuilding. No cost number comes out of this "
                         "experiment, and a restored artifact is byte-identical for "
                         "the edge sets it reads, so this is free of the honesty rule "
                         "that keeps the cache away from G6.")
    args = ap.parse_args()

    header, oracle_all, reachable = load_oracle(Path(args.oracle))
    analysed = set(header["analysed_files"])
    oracle = in_scope(oracle_all, analysed)
    repo = Path(args.repo).resolve()
    name = args.repo_name or repo.name

    print(f"oracle: {header['oracle']} algo={header['algorithm']} "
          f"roots={header['root_count']} load_errors={header['load_errors']}")
    print(f"analysed files: {len(analysed)}   oracle edges: {len(oracle_all)} "
          f"-> {len(oracle)} in scope   reachable funcs: {len(reachable)}\n")

    rows: dict[str, dict] = {}
    for nm in args.arms.split(","):
        arm = arms_lib.get_arm(nm)
        if args.pin:
            art = arms_lib.build_cached(arm, repo, repo_name=name, pin=args.pin)
        else:
            art = arm.build(repo, repo_name=name, fresh=True)
        try:
            keys = in_scope(EXTRACT[nm](art), analysed)
            matched = keys & oracle
            outside = keys - oracle
            # The split that makes precision automatable. A caller RTA reached
            # had every one of its call sites analysed, so an unmatched edge
            # from it is the oracle positively denying that call. A caller it
            # never reached is one the oracle cannot speak about, and those
            # edges are reported at full size rather than charged to anyone.
            contradicted = {k for k in outside if (k[0], k[1]) in reachable}
            unjudged = outside - contradicted
            denom = len(matched) + len(contradicted)
            rec = stats.wilson(len(matched), len(oracle)) if oracle else None
            prec = stats.wilson(len(matched), denom) if denom else None
            rows[nm] = {
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
                    [str(k), v] for k, v in line_offsets(keys, oracle).most_common(6)
                ],
            }
            r = rows[nm]
            print(f"{nm:<22} v{r['version']}")
            print(f"  edges in scope      {r['edges_in_scope']:>7}")
            print(f"  matched oracle      {r['matched']:>7}")
            print(f"  RECALL              {r['recall']:>7}  CI{r['recall_ci']}")
            print(f"  contradicted        {r['contradicted']:>7}  "
                  f"(caller analysed, oracle denies the call)")
            print(f"  PRECISION           {r['precision_vs_oracle']:>7}  CI{r['precision_ci']}")
            print(f"  unjudged            {r['unjudged']:>7}  "
                  f"({r['unjudged_share']} of its edges) -- oracle never reached the caller")
            print(f"  line offsets        {r['line_offsets_top'][:3]}\n")
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
