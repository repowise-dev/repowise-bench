"""Read-only accessors for a CodeGraph index (`.codegraph/codegraph.db`).

Every function here opens the database through a `file:...?mode=ro` URI. That is
not politeness. The peer indexes under `test-repos/*/.codegraph/` are frozen
baselines that published numbers reconcile against, and SQLite will happily
create a `-wal` beside a database opened read-write, which is enough to make a
later run disagree with an earlier one for reasons nobody can reconstruct.

Schema, as of CodeGraph v1.5.0 (extraction version 24):

    nodes(id, kind, name, qualified_name, file_path, language,
          start_line, end_line, ..., return_type, updated_at)
    edges(id, source, target, kind, metadata, line, col, provenance)
    files(path, content_hash, language, size, ..., node_count, errors)
    unresolved_refs(id, from_node_id, reference_name, reference_kind,
                    line, col, candidates, file_path, language, status, name_tail)

`edges.kind` observed across the six frozen indexes: calls, contains, imports,
instantiates, references, implements, extends.

`unresolved_refs.reference_kind` carries the same vocabulary, so anything that
treats that table as calls-only overstates the miss set by roughly 5x. Filter it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

# Node kinds that declare a symbol. `file` is the synthetic per-file node and
# `import` is a reference to something declared elsewhere; neither is a symbol
# this file owns.
DECLARATION_KINDS = frozenset(
    {
        "function",
        "method",
        "class",
        "struct",
        "interface",
        "enum",
        "constant",
        "variable",
        "type_alias",
        "union",
        "trait",
        "module",
        "namespace",
        "property",
        "field",
    }
)

# Edge kinds that mean "the source depends on the target".
DEPENDENCY_KINDS = frozenset(
    {"calls", "references", "imports", "instantiates", "extends", "implements"}
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a CodeGraph index read-only. Raises if the file is not there."""
    p = Path(db_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"no CodeGraph index at {p}")
    uri = "file:" + p.as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True)
class IndexStats:
    repo: str
    db_path: str
    codegraph_version: str
    extraction_version: str
    files: int
    nodes: int
    edges: int
    calls_raw: int
    calls_distinct: int
    unresolved_calls: int


def stats(conn: sqlite3.Connection, repo: str, db_path: str) -> IndexStats:
    """Headline counts for one index, on both the raw and the distinct basis.

    `calls_raw` and `calls_distinct` are reported side by side deliberately.
    Mixing the two bases is the single easiest way to produce a comparison that
    is off by a few percent in whichever direction the author wanted.
    """
    meta = {
        r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM project_metadata")
    }
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return IndexStats(
        repo=repo,
        db_path=db_path,
        codegraph_version=meta.get("indexed_with_version", "unknown"),
        extraction_version=meta.get("indexed_with_extraction_version", "unknown"),
        files=one("SELECT count(*) FROM files"),
        nodes=one("SELECT count(*) FROM nodes"),
        edges=one("SELECT count(*) FROM edges"),
        calls_raw=one("SELECT count(*) FROM edges WHERE kind = 'calls'"),
        calls_distinct=one(
            "SELECT count(*) FROM (SELECT DISTINCT source, target, line"
            "                      FROM edges WHERE kind = 'calls')"
        ),
        unresolved_calls=one(
            "SELECT count(*) FROM unresolved_refs WHERE reference_kind = 'calls'"
        ),
    )


def _kind_list(kinds) -> str:
    return ", ".join("'" + k.replace("'", "''") + "'" for k in sorted(kinds))


def symbol_bearing_files(conn: sqlite3.Connection, languages=None) -> set[str]:
    """Source files that declare at least one symbol.

    Restricting by language matters: caffeine's index carries kotlin and python
    files alongside java, and gitleaks carries yaml and xml. A coverage figure
    quoted as a language's is wrong unless the denominator is that language's
    files.
    """
    sql = f"""
        SELECT DISTINCT n.file_path
        FROM nodes n
        JOIN files f ON f.path = n.file_path
        WHERE n.kind IN ({_kind_list(DECLARATION_KINDS)})
    """
    params: list[str] = []
    if languages:
        sql += f"  AND f.language IN ({','.join('?' * len(languages))})"
        params = list(languages)
    return {r[0] for r in conn.execute(sql, params)}


def files_with_cross_file_dependents(
    conn: sqlite3.Connection,
    edge_kinds=DEPENDENCY_KINDS,
    languages=None,
    direction: str = "incoming",
) -> set[str]:
    """Files connected to another file by at least one edge.

    `direction` picks which end of the edge the file sits on:

      incoming  something in another file depends on this file. This is what
                CodeGraph's published wording ("has at least one resolved
                cross-file dependent") literally describes.
      outgoing  this file depends on something in another file. A file that
                merely imports anything satisfies this.
      either    union of the two. This is the reading that reproduces their
                published table, and it is a materially easier bar than the
                wording implies. See the G2 README for the reproduction.

    The join hits `nodes` twice because `edges` stores node ids, not paths, and
    the cross-file test is `src.file_path <> tgt.file_path`.
    """
    if direction not in ("incoming", "outgoing", "either"):
        raise ValueError(f"direction must be incoming|outgoing|either, got {direction!r}")

    def _side(col: str) -> set[str]:
        sql = f"""
            SELECT DISTINCT {col}.file_path
            FROM edges e
            JOIN nodes src ON src.id = e.source
            JOIN nodes tgt ON tgt.id = e.target
            JOIN files f    ON f.path = {col}.file_path
            WHERE e.kind IN ({_kind_list(edge_kinds)})
              AND src.file_path <> tgt.file_path
        """
        params: list[str] = []
        if languages:
            sql += f"  AND f.language IN ({','.join('?' * len(languages))})"
            params = list(languages)
        return {r[0] for r in conn.execute(sql, params)}

    if direction == "incoming":
        return _side("tgt")
    if direction == "outgoing":
        return _side("src")
    return _side("tgt") | _side("src")


def language_histogram(conn: sqlite3.Connection) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute("SELECT language, count(*) FROM files GROUP BY 1")}


def resolved_call_edges(conn: sqlite3.Connection) -> set[tuple[str, int, str]]:
    """Distinct resolved call edges as `(caller_file, line, target_qualified_name)`.

    Folded to distinct because CodeGraph, like us, can emit the same edge more
    than once for one call expression. Comparing a folded set against an unfolded
    one is how a head-to-head gets a few percent it did not earn.
    """
    sql = """
        SELECT DISTINCT src.file_path, ifnull(e.line, -1), tgt.qualified_name
        FROM edges e
        JOIN nodes src ON src.id = e.source
        JOIN nodes tgt ON tgt.id = e.target
        WHERE e.kind = 'calls'
    """
    return {(r[0], r[1], r[2]) for r in conn.execute(sql)}
