"""code-review-graph 2.3.7, on the arms protocol.

`code-review-graph build` writes SQLite to `<repo>/.code-review-graph/graph.db`.

## Two things that decide whether its numbers are comparable at all

**Its `CALLS` rows are not all resolved edges.** When the tool cannot resolve a
callee it stores the bare identifier in `target_qualified` rather than dropping
the row: `make`, `Notify`, `AddConfigPath`. Both other arms emit an edge only
when they bound it to a declaration they found, so counting all 4,367 `CALLS`
rows on gitleaks against our 2,286 resolved edges would compare a resolved set
against a resolved-plus-unresolved one and hand this arm roughly 2,000 edges it
did not earn.

`call_edges` therefore returns only rows whose `target_qualified` matches a real
`nodes.qualified_name`, which is the same bar the other two arms are held to.
The unresolved count is reported separately in `extra` because it is a genuine
and interesting number -- it is this tool's own recall gap, visible in its own
database -- not because it belongs in the edge set.

**Every path is absolute, with Windows backslashes**, baked in from wherever
`build` ran: `C:\\Users\\...\\gitleaks\\main.go`. Nothing else in this benchmark
stores paths that way. `arms.norm_path` is given the build root so every path
comes back repo-relative and forward-slashed, and the root is recorded on the
artifact because the database cannot be read correctly without it. Two DBs
built on different machines share no path strings at all.

## What its published accuracy number is, stated fairly

Their README reports 0.714 average F1 and 0.578 average precision across 13
commits, with recall 1.000. That recall is circular by construction, and **they
say so themselves**, by name, in both the shipped README and the docstring of
`eval/benchmarks/impact_accuracy.py`: the ground truth is "changed files plus
files with CALLS/IMPORTS_FROM edges into them", derived from the same graph the
predictor traverses. Their own words are "circular by construction" and "an
upper bound, not independent evidence".

They also ship an honest non-circular mode -- grading against files a human
actually co-changed in the same commit -- and decline to quote its numbers
before measuring them.

So the fair statement is not that they published a misleading number. It is
that the headline is a self-consistency measurement, that self-consistency is a
real property and not accuracy, and that this is disclosed by the authors
rather than discovered by us. Note also that 0.69/0.546, the figures this
benchmark's notes carried, do not appear anywhere in 2.3.7; the current numbers
are 0.714/0.578 and the older pair should not be quoted.

Community detection falls back to directory grouping unless `igraph` is
installed. Irrelevant to the edge sets read here, but it means `communities`
rows are not what that column implies.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import arms  # noqa: E402
import procmeter  # noqa: E402

# Their vocabulary. CONTAINS is file -> symbol and always same-file, so it is
# structural in exactly the way our `defines` and the peer's `contains` are.
# TESTED_BY is a test-coverage relation rather than a code dependency, and it
# is excluded for the same reason we exclude `co_changes`.
DEPENDENCY_KINDS = frozenset({"CALLS", "IMPORTS_FROM", "INHERITS", "REFERENCES"})
_STRUCTURAL = frozenset({"CONTAINS", "TESTED_BY"})

# The protocol's portable kind name is lowercase `calls`; theirs is `CALLS`.
# Mapped rather than special-cased at every call site.
_KIND_ALIASES = {"calls": "CALLS", "references": "REFERENCES", "imports": "IMPORTS_FROM",
                 "extends": "INHERITS"}


class CodeReviewGraphArm:
    name = "code-review-graph"

    def __init__(self) -> None:
        self._version: str | None = None
        self._exe: str | None = None

    def _executable(self) -> str:
        """Prefer a venv the survey built, else whatever is on PATH."""
        if self._exe is None:
            self._exe = "code-review-graph"
        return self._exe

    def version(self) -> str:
        if self._version is None:
            res = procmeter.run_measured(
                [self._executable(), "--version"], shell=True, timeout=180
            )
            text = ((res.stdout or "") + " " + (res.stderr or "")).strip()
            import re

            m = re.search(r"(\d+\.\d+\.\d+)", text)
            self._version = m.group(1) if m else "unknown"
        return self._version

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> arms.Artifact:
        """`fresh` is ignored: nothing is frozen for this arm."""
        with arms.scratch_copy(repo) as work:
            res = procmeter.run_measured(
                [self._executable(), "build", "--repo", str(work)],
                cwd=work, shell=True, timeout=7200,
            )
            db = work / ".code-review-graph" / "graph.db"
            if not db.is_file():
                raise RuntimeError(
                    f"code-review-graph wrote no graph.db in {work}\nexit {res.returncode}\n"
                    f"stdout: {res.stdout[-2000:]}\nstderr: {res.stderr[-2000:]}"
                )
            size = db.stat().st_size / (1024 * 1024)
            # Copied out to its own temp dir before the scratch tree is
            # removed. The paths inside still point at the scratch tree, which
            # is why `build_root` is recorded rather than recomputed.
            kept = Path(tempfile.mkdtemp(prefix=f"gq-crg-{repo_name}-")) / "graph.db"
            shutil.copy2(db, kept)
            build_root = str(work)

        conn = sqlite3.connect("file:" + kept.as_posix() + "?mode=ro", uri=True)
        counts = dict(conn.execute("SELECT kind, count(*) FROM edges GROUP BY kind").fetchall())
        unresolved = conn.execute(
            "SELECT count(*) FROM edges e WHERE e.kind = 'CALLS' AND NOT EXISTS "
            "(SELECT 1 FROM nodes n WHERE n.qualified_name = e.target_qualified)"
        ).fetchone()[0]

        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle={"conn": conn, "root": build_root},
            seconds=res.seconds,
            peak_rss_mb=res.peak_rss_mb,
            index_size_mb=size,
            extra={
                "build_root": build_root,
                "db_path": str(kept),
                "edge_kind_counts": counts,
                "calls_rows_total": counts.get("CALLS", 0),
                # Their own recall gap, read off their own database. Reported,
                # never folded into the edge set.
                "calls_rows_unresolved": unresolved,
                "returncode": res.returncode,
            },
        )

    def close(self, art: arms.Artifact) -> None:
        if isinstance(art.handle, dict) and isinstance(art.handle.get("conn"), sqlite3.Connection):
            art.handle["conn"].close()
        art.handle = None
        if art.extra.get("db_path"):
            shutil.rmtree(Path(art.extra["db_path"]).parent, ignore_errors=True)

    def _n(self, art: arms.Artifact, p: str) -> str:
        return arms.norm_path(p, art.handle["root"])

    def files_seen(self, art: arms.Artifact) -> set[str]:
        """Distinct `nodes.file_path`, which under-counts the real walk.

        There is no files table. A file that parses to zero nodes has no row of
        any kind, so on gitleaks 216 files appear against 224 the build log says
        it parsed. The missing 8 are invisible in the database, so this is a
        lossy proxy and is labelled one; a shared-denominator comparison must
        intersect against an arm that records its walk properly.
        """
        return {
            self._n(art, r[0])
            for r in art.handle["conn"].execute("SELECT DISTINCT file_path FROM nodes")
            if r[0]
        }

    def symbol_files(self, art: arms.Artifact) -> set[str]:
        """Files with at least one node that is not the File node itself."""
        return {
            self._n(art, r[0])
            for r in art.handle["conn"].execute(
                "SELECT DISTINCT file_path FROM nodes WHERE kind <> 'File'"
            )
            if r[0]
        }

    def call_edges(self, art: arms.Artifact) -> set[tuple[str, int, str]]:
        """Resolved `CALLS` only -- see the module docstring.

        The join to `nodes` is what enforces "resolved": an unresolved callee
        is stored as a bare identifier that matches no `qualified_name`.
        """
        sql = """
            SELECT DISTINCT e.file_path, e.line, e.target_qualified
            FROM edges e
            JOIN nodes n ON n.qualified_name = e.target_qualified
            WHERE e.kind = 'CALLS'
        """
        return {
            (self._n(art, f), int(line if line is not None else -1), self._n(art, target))
            for f, line, target in art.handle["conn"].execute(sql)
        }

    def cross_file_edges(
        self, art: arms.Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        """`(source_file, target_file)`, joining both endpoints through `nodes`.

        `IMPORTS_FROM` targets a module path string rather than a node (e.g.
        `github.com/spf13/cobra`), so those rows resolve to no target file and
        drop out here. That is correct: an import of something outside the
        repository is not a cross-file edge inside it, and both other arms
        exclude the same thing.
        """
        if kinds is None:
            wanted = DEPENDENCY_KINDS
        else:
            wanted = {_KIND_ALIASES.get(k, k.upper()) for k in kinds} - _STRUCTURAL
        wanted = sorted(wanted)
        if not wanted:
            return set()
        sql = f"""
            SELECT DISTINCT src.file_path, tgt.file_path
            FROM edges e
            JOIN nodes src ON src.qualified_name = e.source_qualified
            JOIN nodes tgt ON tgt.qualified_name = e.target_qualified
            WHERE e.kind IN ({",".join("?" * len(wanted))})
              AND src.file_path <> tgt.file_path
        """
        return {
            (self._n(art, s), self._n(art, t))
            for s, t in art.handle["conn"].execute(sql, wanted)
            if s and t
        }

    def file_languages(self, art: arms.Artifact) -> dict[str, str]:
        """`nodes.language`, which is populated inconsistently.

        A Class row was observed with a NULL language in a file whose Function
        rows all said `go`, so this takes any non-null language recorded for
        the file rather than trusting one row.
        """
        out: dict[str, str] = {}
        for path, lang in art.handle["conn"].execute(
            "SELECT file_path, language FROM nodes WHERE language IS NOT NULL"
        ):
            if path:
                out.setdefault(self._n(art, path), lang)
        return out


arms.register(CodeReviewGraphArm())
