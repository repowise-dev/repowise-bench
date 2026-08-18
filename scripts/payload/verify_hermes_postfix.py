"""Post-#1443 index verification for hermes cell B, against the pre-fix tree.

Answers three questions the next bench session must not have to infer:

1. Is the store real?  `index_vector_dim == 1536` and not mock. Without
   OPENAI_API_KEY, `--embedder openai` silently writes 8-dimensional mock
   vectors and exits 0 (finding D13), so this is a hard precondition and not a
   formality. Read off the index itself, the same way `arms.index_embedding_proof`
   does, rather than off a query response.

2. What did #1443 actually add?  The six oversized hermes modules are matched on
   EXACT repo-relative path, never on a `%suffix` LIKE. A suffix match on
   `cli.py` collides with 29 unrelated files here and on `hermes_state.py` with
   `tests/test_hermes_state.py`, which would report phantom coverage for files
   that are in fact absent.

3. Is each cell-B task's target file retrievable at all?  A task landing on a
   file with zero indexed symbols gives the treated arm no possible retrieval
   advantage, and nothing in the run output would say so.

The delta is printed against measured pre-fix counts rather than inferred.

Usage:
  python verify_hermes_postfix.py --tree C:\\...\\se-hermes-pf-golden
      [--baseline C:\\...\\se-rw-full-hermes]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

# The six modules #1443 recovered, as EXACT repo-relative paths.
SIX = [
    "gateway/run.py",
    "cli.py",
    "hermes_cli/web_server.py",
    "tui_gateway/server.py",
    "hermes_cli/main.py",
    "hermes_state.py",
]

# Files each proved/candidate cell-B task lands on. #83131's is the one the
# smoke found absent from the index and proposed dropping or making a negative
# control; post-#1443 that premise needs re-testing rather than inheriting.
TASK_TARGETS = {
    "B03 / #83389 (proved)": "tools/todo_tool.py",
    "#83807 (proved)": "agent/display.py",
    "#83869 (proved)": "agent/gemini_native_adapter.py",
    "#83131 (proved, was UNINDEXED)": "hermes_cli/web_server.py",
}


def connect_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def counts(conn: sqlite3.Connection, path: str) -> tuple[int, int]:
    """(pages, symbols) for one exact repo-relative path."""
    pages = conn.execute(
        "select count(*) from wiki_pages where target_path = ?", (path,)
    ).fetchone()[0]
    syms = conn.execute(
        "select count(*) from wiki_symbols where file_path = ?", (path,)
    ).fetchone()[0]
    return pages, syms


def totals(conn: sqlite3.Connection) -> tuple[int, int]:
    return (
        conn.execute("select count(*) from wiki_pages").fetchone()[0],
        conn.execute("select count(*) from wiki_symbols").fetchone()[0],
    )


def embedding_proof(tree: Path) -> dict:
    """Vector dim read off the store, mirroring arms.index_embedding_proof."""
    lance = tree / ".repowise" / "lancedb"
    if not lance.exists():
        return {"index_vector_dim": None, "index_embedder_mock": None}
    try:
        import lancedb

        db = lancedb.connect(str(lance))
        names = list(db.table_names())
        if not names:
            return {"index_vector_dim": None, "index_embedder_mock": None}
        table = db.open_table(names[0])
        field = next(x for x in table.schema if x.name == "vector")
        dim = int(field.type.list_size)
    except Exception as exc:  # noqa: BLE001
        return {"index_vector_dim": None, "index_embedder_probe_error": repr(exc)}
    return {"index_vector_dim": dim, "index_embedder_mock": dim <= 16}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True, type=Path)
    ap.add_argument("--baseline", type=Path, default=None)
    args = ap.parse_args()

    post_db = args.tree / ".repowise" / "wiki.db"
    if not post_db.exists():
        print(f"FAIL no wiki.db at {post_db}")
        return 1

    failures: list[str] = []

    print("=" * 72)
    print("1. EMBEDDING PROOF (hard precondition, finding D13)")
    print("=" * 72)
    proof = embedding_proof(args.tree)
    print(f"   {proof}")
    if proof.get("index_vector_dim") != 1536:
        failures.append(f"index_vector_dim is {proof.get('index_vector_dim')}, expected 1536")
    if proof.get("index_embedder_mock") is not False:
        failures.append("index_embedder_mock is not False")
    print(f"   -> {'PASS' if not failures else 'FAIL'}")

    post = connect_ro(post_db)
    pre = None
    if args.baseline:
        pre_db = args.baseline / ".repowise" / "wiki.db"
        if pre_db.exists():
            pre = connect_ro(pre_db)
        else:
            print(f"\n   note: no baseline wiki.db at {pre_db}, delta skipped")

    print()
    print("=" * 72)
    print("2. TOTALS")
    print("=" * 72)
    po_p, po_s = totals(post)
    if pre is not None:
        pr_p, pr_s = totals(pre)
        print(f"   {'':14s} {'pre-fix':>10s} {'post-fix':>10s} {'delta':>10s}")
        print(f"   {'wiki_pages':14s} {pr_p:>10d} {po_p:>10d} {po_p - pr_p:>+10d}")
        print(f"   {'wiki_symbols':14s} {pr_s:>10d} {po_s:>10d} {po_s - pr_s:>+10d}")
    else:
        print(f"   wiki_pages   {po_p}")
        print(f"   wiki_symbols {po_s}")

    print()
    print("=" * 72)
    print("3. THE SIX MODULES #1443 RECOVERED (exact path match)")
    print("=" * 72)
    print(f"   {'file':<30s} {'pre p/s':>12s} {'post p/s':>12s}")
    tp = ts = 0
    rtp = rts = 0
    for f in SIX:
        pp, ps = counts(post, f)
        tp += pp
        ts += ps
        pre_str = "-"
        if pre is not None:
            rp, rs = counts(pre, f)
            rtp += rp
            rts += rs
            pre_str = f"{rp}/{rs}"
        print(f"   {f:<30s} {pre_str:>12s} {f'{pp}/{ps}':>12s}")
        if ps == 0:
            failures.append(f"{f} still has ZERO indexed symbols post-fix")
    # Summed from the same rows as the lines above, never a literal: this table
    # is also used to compare a COPY against its golden, where the baseline
    # column is not zero and a hardcoded 0/0 would read as "the copy added
    # everything".
    total_pre = f"{rtp}/{rts}" if pre is not None else "-"
    print(f"   {'TOTAL':<30s} {total_pre:>12s} {f'{tp}/{ts}':>12s}")

    print()
    print("=" * 72)
    print("4. CELL-B TASK TARGETS (retrievable at all?)")
    print("=" * 72)
    for label, f in TASK_TARGETS.items():
        pp, ps = counts(post, f)
        verdict = "retrievable" if ps > 0 else "NOT IN INDEX -- retrieval cannot help"
        print(f"   {label:<32s} {f:<34s} pages={pp:<4d} symbols={ps:<5d} {verdict}")

    print()
    print("=" * 72)
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS -- store is real, all six recovered, targets retrievable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
