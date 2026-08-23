"""CodeGraph, `@colbymchenry/codegraph@1.5.0`, on the arms protocol.

Wraps `lib/peer_codegraph.py`, which already knew how to read the SQLite index.
Nothing about the queries changed; what this adds is the protocol, so an
experiment can ask for `files_seen` and `symbol_files` without knowing there is
a database behind them.

Two things worth knowing before reading a number off this arm.

**The frozen indexes are baselines and are opened, not rebuilt.** The six under
`test-repos/<repo>/.codegraph/codegraph.db` were written by the 1.5.0 binary
and every published number reconciles against those exact bytes (METHODOLOGY
rule 10). `build(fresh=False)` opens the frozen file read-only and reports
`seconds=None`, because nothing was timed. `build(fresh=True)` copies the repo
to scratch and runs the real binary there, which is what G6 and the determinism
gate need.

**`nodes.language` is the caller's language, `files.language` is the file's.**
caffeine's index carries kotlin, python and c callers. `file_languages` below
reads `files`, so language filtering in an experiment is filtering by what the
file is, not by what called into it.

Version 1.5.0 is their current tagged release. Their `main` is 104 commits
ahead of it and is not tracked here: a moving comparator cannot be reconciled
against a frozen index.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import arms  # noqa: E402
import peer_codegraph as peer  # noqa: E402
import procmeter  # noqa: E402

# Where the frozen indexes live, relative to the repowise checkout root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FROZEN = _REPO_ROOT / "test-repos"


class CodeGraphArm:
    name = "codegraph"

    def __init__(self) -> None:
        self._version: str | None = None

    def version(self) -> str:
        if self._version is None:
            # An npm bin on Windows resolves only as `codegraph.cmd`, so this
            # needs shell=True. Without it the version stamped null and every
            # result carried an unversioned competitor, which session 1's smoke
            # suite caught only because it asserted on the value.
            res = procmeter.run_measured(["codegraph", "--version"], shell=True, timeout=120)
            out = (res.stdout or "").strip().splitlines()
            self._version = out[-1].strip() if out else "unknown"
        return self._version

    def frozen_index(self, repo_name: str) -> Path:
        return _FROZEN / repo_name / ".codegraph" / "codegraph.db"

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> arms.Artifact:
        if not fresh:
            db = self.frozen_index(repo_name)
            if db.is_file():
                return arms.Artifact(
                    arm=self.name,
                    version=self.version(),
                    repo_name=repo_name,
                    repo_path=Path(repo).resolve(),
                    handle=peer.connect(db),
                    seconds=None,  # frozen: not built this run, so not timed
                    peak_rss_mb=None,
                    index_size_mb=db.stat().st_size / (1024 * 1024),
                    extra={"frozen": True, "db_path": str(db)},
                )
            # Falling through to a real build is correct for the four
            # peer_published repos, which have no frozen index.

        with arms.scratch_copy(repo) as work:
            # `init`, not `index`. Their own help calls the two "the same
            # result as a fresh init", but `index` refuses on a tree with no
            # `.codegraph/` and exits 1 with "Run codegraph init first" -- and
            # `scratch_copy` excludes `.codegraph/` by design, so every scratch
            # tree is exactly that case. The frozen baselines were written by
            # `init` too, which is what makes this the reconcilable command.
            res = procmeter.run_measured(
                ["codegraph", "init", "."], cwd=work, shell=True, timeout=7200
            )
            db = work / ".codegraph" / "codegraph.db"
            if not db.is_file():
                raise RuntimeError(
                    f"codegraph index wrote no database in {work}\n"
                    f"exit {res.returncode}\nstdout: {res.stdout[-2000:]}\n"
                    f"stderr: {res.stderr[-2000:]}"
                )
            size = db.stat().st_size / (1024 * 1024)
            # The scratch tree is deleted on exiting the context manager, so
            # the database is copied out first -- to its own temp directory,
            # NOT to a sibling path inside the scratch tree, which would be
            # deleted along with it and leave `connect` opening nothing.
            kept = Path(tempfile.mkdtemp(prefix=f"gq-db-{repo_name}-")) / "codegraph.db"
            shutil.copy2(db, kept)

        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle=peer.connect(kept),
            seconds=res.seconds,
            peak_rss_mb=res.peak_rss_mb,
            index_size_mb=size,
            extra={"frozen": False, "db_path": str(kept), "returncode": res.returncode},
        )

    def cache_payload(self, art: arms.Artifact) -> Path | None:
        """The database is the whole artifact, so the cache stores that file."""
        p = art.extra.get("db_path")
        return Path(p) if p and not art.extra.get("frozen") else None

    def open_cached(
        self, payload: Path, repo: Path, repo_name: str, meta: dict
    ) -> arms.Artifact:
        cost = meta.get("cost", {})
        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle=peer.connect(payload),
            seconds=cost.get("seconds"),
            peak_rss_mb=cost.get("peak_rss_mb"),
            index_size_mb=cost.get("index_size_mb"),
            extra={"frozen": False, "db_path": str(payload)},
        )

    def close(self, art: arms.Artifact) -> None:
        if isinstance(art.handle, sqlite3.Connection):
            art.handle.close()
            art.handle = None
        # A fresh build leaves a copied-out database behind. A frozen baseline
        # and a cached artifact are both files somebody else owns, and deleting
        # either would destroy the thing that makes a rerun cheap or a
        # published number reconcilable.
        if art.extra.get("frozen") or art.extra.get("from_cache"):
            return
        if art.extra.get("db_path"):
            shutil.rmtree(Path(art.extra["db_path"]).parent, ignore_errors=True)

    def files_seen(self, art: arms.Artifact) -> set[str]:
        """The `files` table: everything the walker recorded.

        This is broader than `symbol_files` by construction -- gitleaks' 226
        rows include yaml and markdown -- and the gap between the two is the
        measurement that sized the caffeine 128-file finding.
        """
        return {
            arms.norm_path(r[0]) for r in art.handle.execute("SELECT path FROM files")
        }

    def symbol_files(self, art: arms.Artifact) -> set[str]:
        return {arms.norm_path(p) for p in peer.symbol_bearing_files(art.handle)}

    def call_edges(self, art: arms.Artifact) -> set[tuple[str, int, str]]:
        return {
            (arms.norm_path(f), line, target)
            for f, line, target in peer.resolved_call_edges(art.handle)
        }

    def call_pairs(self, art: arms.Artifact) -> set[tuple[str, str]]:
        return {
            (arms.norm_path(f), target)
            for f, target in peer.resolved_call_pairs(art.handle)
        }

    def cross_file_edges(
        self, art: arms.Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        kinds = sorted(peer.DEPENDENCY_KINDS if kinds is None else kinds)
        sql = f"""
            SELECT DISTINCT src.file_path, tgt.file_path
            FROM edges e
            JOIN nodes src ON src.id = e.source
            JOIN nodes tgt ON tgt.id = e.target
            WHERE e.kind IN ({",".join("?" * len(kinds))})
              AND src.file_path <> tgt.file_path
        """
        return {
            (arms.norm_path(s), arms.norm_path(t))
            for s, t in art.handle.execute(sql, kinds)
            if s and t
        }

    def file_languages(self, art: arms.Artifact) -> dict[str, str]:
        return {
            arms.norm_path(p): lang
            for p, lang in art.handle.execute("SELECT path, language FROM files")
        }


arms.register(CodeGraphArm())
