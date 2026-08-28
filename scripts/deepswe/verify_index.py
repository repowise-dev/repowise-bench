"""Assert a DeepSWE arm index is real before any agent cell is paid for.

The failure this exists to catch: a keyless or mis-keyed repowise run falls back
to 8-dimensional MOCK vectors, writes a complete-looking index, and EXITS 0.
Every health field reads clean. On 2026-08-09 that happened at the harness layer
(Codex sanitizes the environment of its MCP children, so the key set on the
container never reached the server) and the arm would have queried a real
1536-dim index with mock vectors while looking healthy.

So this checks the bytes, not the exit code:

  * the embedding column exists and is 1536-wide, not 8
  * the store is non-empty
  * the index's recorded sync commit matches the repo checkout, which is what
    makes the index commit-matched to the /app tree baked into the arm image
  * the repowise build that wrote it is recorded, so a result can name it

Exit status is 0 only if every assertion holds for every path given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXPECTED_DIM = 1536


def check(repo: Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    index = repo / ".repowise"
    if not index.is_dir():
        return False, [f"FAIL no .repowise at {index}"]

    # 1. provenance: which build wrote it, and against which commit
    state_path = index / "state.json"
    if not state_path.is_file():
        ok = False
        lines.append("FAIL no state.json")
    else:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        written_by = state.get("written_by_version")
        synced = state.get("last_sync_commit")
        lines.append(f"  written_by_version : {written_by}")
        lines.append(f"  last_sync_commit   : {synced}")

        head_path = repo / ".git" / "HEAD"
        head = None
        if head_path.is_file():
            raw = head_path.read_text(encoding="utf-8").strip()
            if raw.startswith("ref: "):
                ref = repo / ".git" / raw[5:]
                head = ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
            else:
                head = raw
        lines.append(f"  repo HEAD          : {head}")
        if head and synced and head != synced:
            ok = False
            lines.append("FAIL index sync commit does not match repo HEAD")

    # 2. the assertion that actually catches a mock-vector run
    lancedb_dir = index / "lancedb"
    if not lancedb_dir.is_dir():
        ok = False
        lines.append("FAIL no lancedb store")
        return ok, lines

    try:
        import lancedb
    except ImportError:
        return False, lines + ["FAIL lancedb not importable; run with the pinned repowise venv"]

    db = lancedb.connect(str(lancedb_dir))
    # list_tables() returns a ListTablesResponse whose iteration yields
    # (field, value) pairs, not names; the names live on .tables.
    listing = db.list_tables()
    tables = list(getattr(listing, "tables", listing))
    if not tables:
        return False, lines + ["FAIL lancedb store has no tables"]

    for name in tables:
        table = db.open_table(name)
        rows = table.count_rows()
        dim = None
        for field in table.schema:
            # the embedding column is the only fixed-size list of floats
            text = str(field.type)
            if text.startswith("fixed_size_list") and "float" in text:
                dim = int(text.rsplit("[", 1)[1].rstrip("]"))
                break
        lines.append(f"  table {name!r}: rows={rows} vector_dim={dim}")
        if rows == 0:
            ok = False
            lines.append(f"FAIL table {name!r} is empty")
        if dim != EXPECTED_DIM:
            ok = False
            lines.append(
                f"FAIL table {name!r} vector dim is {dim}, expected {EXPECTED_DIM}. "
                "A dim of 8 means the run fell back to MOCK vectors and the arm is dead."
            )

    return ok, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="+", type=Path, help="repo checkouts holding a .repowise index")
    args = ap.parse_args()

    all_ok = True
    for repo in args.repos:
        ok, lines = check(repo)
        print(f"{'PASS' if ok else 'FAIL'}  {repo}")
        for line in lines:
            print(line)
        all_ok = all_ok and ok
    print()
    print("ALL INDEXES VALID" if all_ok else "AT LEAST ONE INDEX IS INVALID -- do not run a cell")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
