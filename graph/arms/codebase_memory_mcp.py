"""codebase-memory-mcp (DeusData) 0.10.8, on the arms protocol.

A single native executable -- no language runtime -- that indexes a repository
into SQLite. `cli index_repository --repo-path <path>` writes
`${CBM_CACHE_DIR}/<project>.db`, where the project name is derived from the
path with separators folded to dashes.

Every normalisation decision this adapter makes is recorded in
`codebase-memory-mcp.md` beside this file, per `arms.py`'s contract. The two
that change its numbers most are summarised here.

## Resolved versus unresolved: this tool separates them itself

The trap code-review-graph set -- unresolved callees stored in the same table
as resolved ones, worth roughly 2,000 edges it did not earn -- does not apply
here, and the reason is structural rather than a favour. `edges.source_id` and
`edges.target_id` are `INTEGER REFERENCES nodes(id)`, so an edge cannot exist
without both endpoints being real nodes. There is no bare-identifier row to
filter out.

Their own vocabulary draws the line in a second place, and this adapter follows
it: `CALLS` is a resolved invocation, `CALL_REFERENCE` is a callable used as a
value that resolves to one exact target, and **`USAGE` is where ambiguous
values are retained**. So `USAGE` is their unresolved bucket and is excluded
from every set here.

`CALL_REFERENCE` is excluded from `call_edges` for a different reason: it is
resolved, but it is not an invocation. Passing a function as an argument is not
a call, and no other arm in this benchmark counts one. It is included in the
arm's own dependency vocabulary (`kinds=None`), where each tool is allowed its
own reading, and its size is reported in `extra` so the choice is auditable.

## Paths

`file_hashes.rel_path` is a real record of the walk -- one row per file the
indexer hashed, whether or not it produced a node. That makes `files_seen`
honest here in a way it is not for code-review-graph, which has no files table
and under-counts by however many files parsed to nothing. Paths still go
through `arms.norm_path`, because a repository indexed on Windows stores
backslashes and a single one makes a cross-arm intersection silently empty.

`nodes.file_path` on an external or stdlib node is empty. Those rows are
dropped from the file-level sets rather than normalised into the repository
root, where they would collide with a real file.

## Isolation

Each build gets its own `CBM_CACHE_DIR`, so the database is the only one in the
directory and the project name never has to be re-derived from the path. That
also keeps a build from joining an index another run left behind, which for an
incremental indexer would mean measuring a graph this run did not produce.

## Precondition, and why this arm may be absent

The release binary validates the DACL of its coordination directory's ancestor
chain under `%LOCALAPPDATA%` and refuses to run if any ancestor grants mutation
rights to another account. On a profile where `C:/Users/<user>/AppData` carries
such an ACE it exits before doing any work, and the runtime-parent override is
compiled out of release builds (`CBM_ENABLE_TEST_SEAMS`). The arm registers
only when the executable is both present and able to run; see `UNAVAILABLE`
below and the smoke check that reports it, so an absent arm reads as a stated
precondition rather than as a row nobody noticed was missing.
"""

from __future__ import annotations

import os
import re
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

# Their node vocabulary. `File`, `Folder`, `Package` and `Project` are
# containment scaffolding, the same role our file nodes play, and a file that
# yields only those has declared no symbol.
SYMBOL_LABELS = frozenset(
    {"Class", "Function", "Method", "Interface", "Enum", "Type", "Route", "Resource", "Module"}
)

# Their edge vocabulary, minus the structural and the unresolved. CONTAINS_*
# and DEFINES* are containment. USAGE is where they retain callable values they
# could not resolve. FILE_CHANGES_WITH is git co-change, excluded for the same
# reason we exclude our own `co_changes`. TESTS is a coverage relation.
DEPENDENCY_TYPES = frozenset(
    {"IMPORTS", "CALLS", "CALL_REFERENCE", "ASYNC_CALLS", "HTTP_CALLS",
     "IMPLEMENTS", "USES_TYPE", "HANDLES", "CONFIGURES", "WRITES"}
)
_STRUCTURAL = frozenset(
    {"CONTAINS_PACKAGE", "CONTAINS_FOLDER", "CONTAINS_FILE", "DEFINES",
     "DEFINES_METHOD", "MEMBER_OF", "TESTS", "FILE_CHANGES_WITH", "USAGE"}
)

# Only `calls` is portable across arms. `ASYNC_CALLS` is an invocation that
# happens to be awaited, so it joins CALLS; CALL_REFERENCE deliberately does
# not -- see the module docstring.
_KIND_ALIASES = {
    "calls": ("CALLS", "ASYNC_CALLS"),
    "imports": ("IMPORTS",),
    "type_use": ("USES_TYPE",),
    "method_implements": ("IMPLEMENTS",),
}

# Their extension-to-language table, reproduced verbatim from the one the tool
# carries in its store layer, including its casing and its choices.
#
# This exists because the tool stores no language on a node -- it derives one
# from the extension at read time, and this is the table it derives it with. The
# table backs the `languages` breakdown its own `get_architecture` output
# prints, so reproducing it is reading their index the way they read it rather
# than imposing a scheme of ours. See `file_languages` for the two places this
# adapter deliberately departs from their query.
#
# Two of their choices to notice before quoting a number scoped by this:
#   * `.h` is C, not C++. Every C++ repository in the corpus with headers is
#     therefore split between `c` and `cpp` by their rule, not ours. `graphify`,
#     the other arm with no language field, happens to map `.h` the same way.
#   * `.sh`/`.bash` are "Bash" where `graphify` says "shell". Neither is a
#     corpus primary language, so nothing published turns on it.
#
# An extension absent from this table yields no entry at all, which is their
# behaviour: their lookup returns NULL and their loop skips the row. That is
# what keeps `.md`, `.json`, `.txt` and the rest out of a language denominator.
_THEIR_EXT_LANG = {
    ".py": "Python", ".go": "Go", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".rs": "Rust", ".java": "Java",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".c": "C",
    ".h": "C", ".cs": "C#", ".php": "PHP", ".lua": "Lua",
    ".scala": "Scala", ".kt": "Kotlin", ".rb": "Ruby", ".sh": "Bash",
    ".bash": "Bash", ".zig": "Zig", ".ex": "Elixir", ".exs": "Elixir",
    ".hs": "Haskell", ".ml": "OCaml", ".mli": "OCaml", ".html": "HTML",
    ".css": "CSS", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".hcl": "HCL", ".tf": "HCL", ".sql": "SQL", ".erl": "Erlang",
    ".swift": "Swift", ".dart": "Dart", ".groovy": "Groovy", ".pl": "Perl",
    ".r": "R", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
}


def _suffix(path: str) -> str:
    """The lowercased final extension, matching how they take one.

    They scan for the last `.` in the whole path and lowercase what follows, so
    a dotless path yields nothing and `.TS` and `.ts` agree. `Path.suffix` gives
    the same answer for every path in this corpus and is used instead of a
    hand-rolled `rfind`, with one difference that cannot bite: theirs would read
    an extension out of a dotless filename inside a dotted directory, and both
    then miss the table and yield no language either way.
    """
    return Path(path).suffix.lower()


# Where a bench machine keeps the extracted release. Kept off %LOCALAPPDATA%
# deliberately; see the precondition note in the module docstring.
_DEFAULT_HOME = Path("C:/Users/ragha/Desktop/bench-worktrees/cbm")


# The release the coverage and precision reads are taken on. Pinned in code
# rather than left to whatever is on PATH: this tool ships often -- three
# releases in the nine days around this measurement -- and v0.10.8 is a graph
# correctness release, so a number read off a neighbouring build is a different
# number. The G6 cost and memory tables were measured on 0.10.6 and stay there;
# they are labelled with it, and a re-sweep for a patch release is 158 minutes
# to move a figure that is about the process rather than the graph.
_PINNED_VERSION = "0.10.8"


def _find_executable() -> str | None:
    """`CBM_BIN`, else the pinned release, else whatever is on PATH.

    The pinned directory is tried before the unversioned `bin/`, which still
    holds 0.10.6 so the cost sweep stays reproducible against its own tables.
    """
    explicit = os.environ.get("CBM_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    for sub in (f"bin-{_PINNED_VERSION}", "bin"):
        local = _DEFAULT_HOME / sub / "codebase-memory-mcp.exe"
        if local.is_file():
            return str(local)
    return shutil.which("codebase-memory-mcp")


# The cache parent, which is NOT under `_DEFAULT_HOME`, and the reason is a
# precondition of the tool rather than a preference of ours.
#
# The tool validates the DACL of every ancestor of `CBM_CACHE_DIR`, separately
# from the ancestors of its coordination endpoint, and refuses any ACE granting
# mutation rights to an untrusted SID. On the measurement machine
# `C:\Users\ragha\Desktop` carries such an ACE, so a cache under `_DEFAULT_HOME`
# -- which lives on `Desktop` -- fails that check even though the binary itself
# is happy to run from there. Its own default, `C:\Users\ragha\.cache`, fails
# the same way.
#
# `%LOCALAPPDATA%` is the cleared path: the `AppData` ACE that used to block the
# coordination endpoint was removed (see the arm doc's Precondition), and nothing
# re-adds one. The repository under test may stay on `Desktop`; only the cache
# and coordination trees are validated.
_CACHE_PARENT = Path(
    os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local"
) / "cbm-bench"


def _scratch_root() -> str:
    """The parent every per-build cache and kept database is created under.

    Must sit outside `Desktop` -- see `_CACHE_PARENT`. Overridable with
    `CBM_SCRATCH_ROOT` for a machine whose clean path is somewhere else, but the
    override is only safe if that path's ancestors pass the tool's DACL check.
    """
    root = Path(os.environ.get("CBM_SCRATCH_ROOT") or _CACHE_PARENT)
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _env_with_cache(cache: Path) -> dict[str, str]:
    """This process's environment with `CBM_CACHE_DIR` pointed at *cache*.

    Passed through `run_measured(env=...)` rather than set on `os.environ`, so
    two arms measured in one session cannot inherit each other's cache. The
    per-build freshness is what stops an incremental indexer joining an index a
    previous build left behind and reporting a graph this run did not produce.
    """
    return {**os.environ, "CBM_CACHE_DIR": str(cache)}


def _open(db: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + Path(db).as_posix() + "?mode=ro", uri=True)


def _cached_handle(conn: sqlite3.Connection, root: str) -> dict:
    """The handle shape both `build` and `open_cached` must produce.

    Written once so the two cannot drift: a restored artifact that lacked
    `project_prefix` would silently stop stripping the path out of callee
    identities, and the resulting mismatch would look like a real disagreement
    between a cached and a fresh build.
    """
    return {"conn": conn, "root": root, "project_prefix": _project_prefix(conn)}


def _project_prefix(conn: sqlite3.Connection) -> str | None:
    """The `<project>.` prefix every qualified name in this index carries.

    Read from the index rather than reconstructed from the build path, because
    the tool's own path-to-project-name mangling is its business and not
    something this adapter should reimplement. An index also carries a second
    `<project>::missed` row; the base name is the shorter one and is the prefix
    qualified names actually use.
    """
    names = [r[0] for r in conn.execute("SELECT DISTINCT project FROM nodes") if r[0]]
    if not names:
        return None
    return min(names, key=len) + "."


class CodebaseMemoryMcpArm:
    name = "codebase-memory-mcp"

    def __init__(self, exe: str) -> None:
        self._exe = exe
        self._version: str | None = None

    def version(self) -> str:
        if self._version is None:
            res = procmeter.run_measured([self._exe, "--version"], timeout=180)
            text = ((res.stdout or "") + " " + (res.stderr or "")).strip()
            m = re.search(r"(\d+\.\d+\.\d+)", text)
            self._version = m.group(1) if m else "unknown"
        return self._version

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> arms.Artifact:
        """`fresh` is ignored: nothing is frozen for this arm."""
        cache = Path(tempfile.mkdtemp(prefix=f"gq-cbm-{repo_name}-", dir=_scratch_root()))
        env = _env_with_cache(cache)
        with arms.scratch_copy(repo) as work:
            res = procmeter.run_measured(
                [self._exe, "cli", "index_repository", "--repo-path", str(work)],
                cwd=work, timeout=7200, env=env,
            )
            dbs = [d for d in sorted(cache.glob("*.db")) if d.name != "_config.db"]
            if len(dbs) != 1:
                raise RuntimeError(
                    f"codebase-memory-mcp wrote {len(dbs)} databases in {cache}, expected 1\n"
                    f"exit {res.returncode}\nstdout: {res.stdout[-2000:]}\n"
                    f"stderr: {res.stderr[-2000:]}"
                )
            db = dbs[0]
            size = db.stat().st_size / (1024 * 1024)
            kept = Path(
                tempfile.mkdtemp(prefix=f"gq-cbmdb-{repo_name}-", dir=_scratch_root())
            ) / db.name
            shutil.copy2(db, kept)
            build_root = str(work)
        shutil.rmtree(cache, ignore_errors=True)

        conn = _open(kept)
        counts = dict(conn.execute("SELECT type, count(*) FROM edges GROUP BY type").fetchall())
        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle=_cached_handle(conn, build_root),
            seconds=res.seconds,
            peak_rss_mb=res.peak_rss_mb,
            index_size_mb=size,
            extra={
                "build_root": build_root,
                "db_path": str(kept),
                "edge_type_counts": counts,
                # Reported, never folded into `calls`. See the module docstring.
                "call_reference_rows": counts.get("CALL_REFERENCE", 0),
                "usage_rows_unresolved": counts.get("USAGE", 0),
                "returncode": res.returncode,
            },
        )

    def cache_payload(self, art: arms.Artifact) -> Path | None:
        p = art.extra.get("db_path")
        return Path(p) if p else None

    def open_cached(self, payload: Path, repo: Path, repo_name: str, meta: dict) -> arms.Artifact:
        """`build_root` comes out of the metadata, never recomputed.

        `file_hashes.rel_path` is already repo-relative, but `nodes.file_path`
        is absolute into the scratch tree that built the index, and that tree
        was deleted when the build finished. A guessed root leaves every node
        path absolute, which reads as an empty intersection rather than a bug.
        """
        cost = meta.get("cost", {})
        root = (meta.get("extra") or {}).get("build_root")
        if not root:
            raise RuntimeError(
                f"cached codebase-memory-mcp artifact for {repo_name} has no "
                "build_root; its node paths cannot be normalised, so it is unusable"
            )
        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle=_cached_handle(_open(Path(payload)), root),
            seconds=cost.get("seconds"),
            peak_rss_mb=cost.get("peak_rss_mb"),
            index_size_mb=cost.get("index_size_mb"),
            extra={"build_root": root, "db_path": str(payload)},
        )

    def close(self, art: arms.Artifact) -> None:
        if isinstance(art.handle, dict) and isinstance(art.handle.get("conn"), sqlite3.Connection):
            art.handle["conn"].close()
        art.handle = None
        if art.extra.get("from_cache"):
            return  # the payload belongs to the artifact cache, not to this run
        if art.extra.get("db_path"):
            shutil.rmtree(Path(art.extra["db_path"]).parent, ignore_errors=True)

    def _n(self, art: arms.Artifact, p: str) -> str:
        return arms.norm_path(p, art.handle["root"])

    @staticmethod
    def _real_file(path: str) -> bool:
        """Reject the tool's synthetic pseudo-files.

        It files builtin symbols under `<python-builtins>`, which is not a path
        and has no row in `file_hashes`. Left in, it makes `symbol_files` not a
        subset of `files_seen` -- an invariant every other arm holds and one the
        smoke suite asserts, because a denominator carrying a file that cannot
        exist is a denominator nobody can reconcile.
        """
        return bool(path) and not (path.startswith("<") and path.endswith(">"))

    def _q(self, art: arms.Artifact, qualified: str) -> str:
        """Strip the project prefix from a qualified name.

        The tool derives its project name from the **absolute path it indexed**,
        so a scratch copy yields
        `C-Users-...-Temp-gq-gitleaks-8k_mag8d-gitleaks.cmd.runVersion` and the
        random tempdir component changes on every build. Left alone, two builds
        of one repository share no callee identities at all: the determinism
        gate fails, and every cross-arm comparison against this arm intersects to
        zero -- which reads as a finding rather than as a path leaking into an
        identifier.

        Stripping the prefix restores both properties and loses nothing: the
        prefix is constant within an index, so it distinguishes no two symbols.
        """
        prefix = art.handle.get("project_prefix")
        if prefix and qualified.startswith(prefix):
            return qualified[len(prefix):]
        return qualified

    def files_seen(self, art: arms.Artifact) -> set[str]:
        """`file_hashes.rel_path`: one row per file the indexer hashed.

        A genuine walk record rather than a proxy -- a file that parsed to no
        node still has a row here, which is exactly the 8-file gap that makes
        code-review-graph's `files_seen` lossy.
        """
        return {
            self._n(art, r[0])
            for r in art.handle["conn"].execute("SELECT DISTINCT rel_path FROM file_hashes")
            if r[0]
        }

    def symbol_files(self, art: arms.Artifact) -> set[str]:
        labels = sorted(SYMBOL_LABELS)
        sql = (
            "SELECT DISTINCT file_path FROM nodes WHERE file_path <> '' "
            f"AND label IN ({','.join('?' * len(labels))})"
        )
        return {
            self._n(art, r[0])
            for r in art.handle["conn"].execute(sql, labels)
            if self._real_file(r[0])
        }

    def call_edges(self, art: arms.Artifact) -> set[tuple[str, int, str]]:
        """Distinct `(caller_file, line, callee_qualified_name)`.

        The line is the *source node's* declaration line, not the call site:
        `edges` carries no line of its own, so a caller making two calls to the
        same target folds to one row here. That under-counts relative to arms
        that record a call-site line, and it is recorded here and in the .md
        rather than papered over -- METHODOLOGY rule 2 folds on distinct
        triples, and this arm's triples are coarser than the others'.
        """
        sql = """
            SELECT DISTINCT src.file_path, src.start_line, tgt.qualified_name
            FROM edges e
            JOIN nodes src ON src.id = e.source_id
            JOIN nodes tgt ON tgt.id = e.target_id
            WHERE e.type IN ('CALLS', 'ASYNC_CALLS') AND src.file_path <> ''
        """
        return {
            (self._n(art, f), int(line if line is not None else -1), self._q(art, target))
            for f, line, target in art.handle["conn"].execute(sql)
            if self._real_file(f)
        }

    def cross_file_edges(
        self, art: arms.Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        if kinds is None:
            wanted = set(DEPENDENCY_TYPES)
        else:
            wanted = set()
            for k in kinds:
                wanted.update(_KIND_ALIASES.get(k, (k.upper(),)))
        wanted = sorted(wanted - _STRUCTURAL)
        if not wanted:
            return set()
        sql = f"""
            SELECT DISTINCT src.file_path, tgt.file_path
            FROM edges e
            JOIN nodes src ON src.id = e.source_id
            JOIN nodes tgt ON tgt.id = e.target_id
            WHERE e.type IN ({','.join('?' * len(wanted))})
              AND src.file_path <> '' AND tgt.file_path <> ''
              AND src.file_path <> tgt.file_path
        """
        return {
            (self._n(art, s), self._n(art, t))
            for s, t in art.handle["conn"].execute(sql, wanted)
        }

    def file_languages(self, art: arms.Artifact) -> dict[str, str]:
        """Their own extension table, applied the way their own query applies it.

        This tool stores no language on a node. It does not need to: it derives
        one from the file extension at read time, in `arch_languages`, which is
        what backs the `languages` table its MCP `get_architecture` surface
        prints. `_THEIR_EXT_LANG` below is that table, and an extension the
        table does not carry yields no entry -- their lookup returns NULL and
        their loop skips the row.

        So this is not a classification scheme invented here and applied to
        somebody else's index. It is the derivation the tool publishes about
        itself, reproduced so a reader can disagree with a specific 44-entry
        table rather than with a hidden choice. The precedent in this benchmark
        is `graphify`, which records no language field either and is scoped the
        same way.

        Two deliberate departures from their query, both stated because both
        move numbers:

        1. **Population.** Theirs reads `nodes WHERE label='File'`; this reads
           `files_seen`, which comes from `file_hashes` and is the protocol's
           denominator for every arm. `file_hashes` is the wider set -- it has a
           row per file the indexer hashed, including files that produced no
           node -- so scoping to their `File` nodes would silently shrink the
           denominator against every other arm.
        2. **Case.** Their names are returned verbatim (`"Go"`, `"C++"`), and
           `run_corpus.norm_lang` folds them, so this adapter stays a mirror and
           normalisation lives in the one place that already does it for all
           five arms.
        """
        return {
            path: lang
            for path in self.files_seen(art)
            for lang in (_THEIR_EXT_LANG.get(_suffix(path)),)
            if lang
        }


def _probe(exe: str) -> str | None:
    """Return None if the tool can actually run here, else why it cannot.

    `--version` alone is not enough: the binary prints its version and only
    then refuses on the coordination-directory DACL, so a version check would
    register an arm whose every build fails. This runs the cheapest command
    that goes through the coordination path.
    """
    try:
        # With no CBM_CACHE_DIR the tool falls back to `~/.cache`, whose
        # ancestors fail its own DACL check on this machine -- so a probe run
        # bare reports the arm unavailable for a reason no build would ever
        # hit. Probe through the same cache parent every build uses.
        probe_cache = Path(_scratch_root()) / "_probe"
        probe_cache.mkdir(parents=True, exist_ok=True)
        res = procmeter.run_measured(
            [exe, "cli", "list_projects"], timeout=180, env=_env_with_cache(probe_cache)
        )
    except Exception as exc:  # noqa: BLE001 - an unusable tool is not an error
        return f"{type(exc).__name__}: {exc}"
    if res.returncode == 0:
        return None
    text = ((res.stderr or "") + " " + (res.stdout or "")).strip()
    return text[:400] or f"exit {res.returncode} with no output"


# Registered only when the tool is genuinely usable. `UNAVAILABLE` carries the
# reason when it is not, and `smoke.py` prints it, so an absent arm is a stated
# precondition rather than a row that quietly never appears.
UNAVAILABLE: str | None
_exe = _find_executable()
if _exe is None:
    UNAVAILABLE = "no codebase-memory-mcp executable (set CBM_BIN, or extract a release)"
else:
    UNAVAILABLE = _probe(_exe)
    if UNAVAILABLE is None:
        arms.register(CodebaseMemoryMcpArm(_exe))
