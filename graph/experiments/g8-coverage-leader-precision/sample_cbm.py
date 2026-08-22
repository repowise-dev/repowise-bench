"""Draw the hand-grading sample from a frozen codebase-memory-mcp index.

Read-only against the artifact cache at `repowise-bench/artifacts/
codebase-memory-mcp-0.10.8/<repo>-<pin8>/payload/*.db`. Nothing is rebuilt, so
the arm's Windows ACL precondition never has to be cleared to run this.

Method is G1's, transplanted onto this arm's fields:

* **Denominator** distinct `(caller_file, call_line, target_qualified_name)` on
  `type IN ('CALLS','ASYNC_CALLS')`, matching the arm page's edge vocabulary.
  `CALL_REFERENCE` and `USAGE` are excluded there and are excluded here.
* **Language scope** the caller's file extension through the arm's own
  extension table, the one `arms/codebase_memory_mcp.py` mirrors from the tool.
* **Strata** `properties.strategy`, this arm's equivalent of CodeGraph's
  `metadata.resolvedBy`, in proportion to population.
* **Sample** seed 2026, largest-remainder allocation, seeded draw per stratum.

`stratified` is a verbatim copy of the function G1 drew with, vendored beside
this script as `stratified.py`, so the allocation is the same code rather than a
re-implementation of its description.

Usage:
  python sample_cbm.py <repo> --lang cpp --n 10 --out rows/cpp-<repo>.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "arms"))

from codebase_memory_mcp import _THEIR_EXT_LANG, _suffix  # noqa: E402
from stratified import stratified  # noqa: E402

ARTIFACTS = BENCH / "artifacts" / "codebase-memory-mcp-0.10.8"

# `run_corpus.norm_lang`, the fold every arm's language names go through.
_NORM = {"c++": "cpp", "c#": "csharp", "javascript": "javascript"}


def _norm_lang(name: str) -> str:
    low = name.lower()
    return _NORM.get(low, low)


def find_db(repo: str) -> tuple[Path, dict]:
    hits = sorted(ARTIFACTS.glob(f"{repo}-*"))
    if not hits:
        raise SystemExit(f"no cached artifact for {repo} under {ARTIFACTS}")
    if len(hits) > 1:
        raise SystemExit(f"{repo} has {len(hits)} cached pins; disambiguate by hand")
    meta = json.loads((hits[0] / "meta.json").read_text(encoding="utf-8"))
    return hits[0] / "payload" / meta["payload_name"], meta


def _rel(path: str, build_root: str) -> str:
    """Repo-relative, forward-slashed. `nodes.file_path` is absolute into the
    scratch tree the index was built in; the root is read off the artifact and
    never re-derived, which is the failure the arm page documents."""
    p = path.replace("\\", "/")
    root = build_root.replace("\\", "/").rstrip("/")
    if root and p.lower().startswith(root.lower() + "/"):
        p = p[len(root) + 1 :]
    return p


def run(repo: str, lang: str, n: int, seed: int) -> dict:
    db, meta = find_db(repo)
    build_root = meta["extra"]["build_root"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    # laravel-framework stores a mojibaked argument snippet inside an edge's
    # `properties`, so the default text factory raises on the whole column.
    con.text_factory = lambda b: b.decode("utf-8", "replace")

    q = """
        SELECT e.properties, e.type,
               sn.file_path, sn.name, sn.qualified_name, sn.start_line, sn.end_line, sn.label,
               tn.file_path, tn.name, tn.qualified_name, tn.start_line, tn.end_line, tn.label
        FROM edges e
        JOIN nodes sn ON sn.id = e.source_id
        JOIN nodes tn ON tn.id = e.target_id
        WHERE e.type IN ('CALLS', 'ASYNC_CALLS')
    """
    raw = 0
    no_line = 0
    seen: set[tuple] = set()
    rows: list[dict] = []
    for (props, etype, sfile, sname, sqn, sline, send, slabel,
         tfile, tname, tqn, tline, tend, tlabel) in con.execute(q):
        raw += 1
        try:
            m = json.loads(props) if props else {}
        except (ValueError, TypeError):
            m = {}
        # Rule 5 on the arm page: external / stdlib nodes carry an empty path
        # and are dropped rather than rooted, on both endpoints.
        if not sfile or sfile.startswith("<"):
            continue
        caller = _rel(sfile, build_root)
        # A row with no call-site line cannot be graded at a site, so it is
        # excluded from the denominator and counted instead.
        if m.get("line") is None:
            no_line += 1
            continue
        key = (caller, m.get("line"), tqn)
        if key in seen:
            continue
        seen.add(key)
        their_lang = _THEIR_EXT_LANG.get(_suffix(caller))
        if not their_lang or _norm_lang(their_lang) != lang:
            continue
        rows.append(
            {
                # `file`/`line`/`target` are the keys `stratified` sorts on.
                "file": caller,
                "line": m.get("line"),
                "target": tname,
                "origin": m.get("strategy") or "<none>",
                "edge_type": etype,
                "confidence": m.get("confidence"),
                "candidates": m.get("candidates"),
                "callee_ref": m.get("callee"),
                "caller": sname,
                "caller_qualified": sqn,
                "caller_label": slabel,
                "caller_decl_line": sline,
                "caller_end_line": send,
                "resolved_to": tqn,
                "target_file": _rel(tfile, build_root) if tfile else None,
                "target_line": tline,
                "target_end_line": tend,
                "target_label": tlabel,
                "repo": repo,
                "verdict": None,
                "reason": None,
            }
        )
    con.close()

    mix = collections.Counter(r["origin"] for r in rows)
    return {
        "arm": "codebase-memory-mcp 0.10.8",
        "repo": repo,
        "pin": meta["pin"],
        "language": lang,
        "seed": seed,
        "n": n,
        "raw_call_rows": raw,
        "distinct_call_edges": len(seen),
        "rows_without_line": no_line,
        "distinct_in_language": len(rows),
        "origin_mix": dict(mix.most_common()),
        "sample": stratified(rows, "origin", n, seed),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--lang", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out")
    ap.add_argument("--census", action="store_true", help="population only, draw nothing")
    args = ap.parse_args()

    result = run(args.repo, args.lang, args.n, args.seed)
    if args.census:
        result.pop("sample")
    elif args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "sample"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
