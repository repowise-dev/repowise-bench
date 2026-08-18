"""Read-only-ish accessors for our own graph, built fresh from a repo checkout.

The peer's counterpart, `peer_codegraph.py`, opens a frozen SQLite file it
never wrote. We have no such artifact to open: our graph lives only in the
`networkx.DiGraph` `GraphBuilder.build()` returns, in-process, for the
lifetime of one Python run. So this module's "read" is "build, then read the
object in memory" — walk, parse, resolve, done. Nothing here writes to the
repo under test; the graph is held in memory and discarded when the process
exits.

Verified against a real build of `test-repos/gitleaks` (226 files) on
2026-08-18, tree dirty (see the caller's provenance stamp — this module does
not gate on that itself). What was actually found, not guessed:

    File nodes  : node_type == "file", plus symbol_count (int), language,
                  has_error, is_test, is_entry_point, docstring, local_refs.
    Symbol nodes: node_type == "symbol", plus kind (SymbolKind — function,
                  class, method, interface, enum, constant, type_alias,
                  decorator, trait, impl, struct, module, macro, variable),
                  name, qualified_name, file_path, language, parent_name, ...
                  `kind == "module"` includes one synthetic `<path>::__module__`
                  node per file that `GraphBuilder.add_file` stamps for
                  top-level calls to attach to — it is not one of
                  `parsed.symbols` and `symbol_bearing_files` below excludes it
                  by construction (it counts `parsed.symbols`, never graph
                  nodes).
    Edges       : attribute is `edge_type`. Observed on a real build of
                  `test-repos/gitleaks` (go, 296 files walked, 213 symbol-
                  bearing): `imports` 1862, `defines` 970, `calls` 1668,
                  `has_method` 76, `type_use` 48, `method_implements` 8,
                  `extends` 3 — `type_use`/`method_implements`/`extends` all
                  fired on plain Go, via C#-shaped struct-field type
                  references and Go's implicit interface satisfaction, so
                  "heritage-only" was a wrong first guess and is not repeated
                  here. Observed on `test-repos/requests` (python, 74 files,
                  32 symbol-bearing): `defines` 886, `calls` 860, `has_method`
                  475, `imports` 111, `extends` 39, `dispatches_to` 9 — no
                  `type_use` (that edge kind is currently C#-only; see
                  `type_ref_resolution.py`). Neither run produced `references`,
                  `implements`, `framework`, `framework_binds`, `reads`,
                  `dynamic_*` or `co_changes`, all of which remain in the
                  closed vocabulary (`repowise.core.ingestion.models.EdgeType`)
                  and are per-repo, not universal — `co_changes` in particular
                  is a separate git-history pass this harness never runs.

`defines` (file -> symbol) and `has_method` (class -> method) are STRUCTURAL:
both endpoints are always in the same file by construction, so they carry no
cross-file information and must be excluded from any cross-file dependency
count. `co_changes` (when present) is a git co-change correlation, not a code
dependency, and is excluded for the same reason the peer excludes `contains`.

Everything else that can connect two different files — `imports`, `calls`,
`references`, `extends`, `implements`, `method_implements`, `dispatches_to`,
`type_use`, `reads`, `framework`, `framework_binds`, `dynamic_uses`,
`dynamic_imports`, `dynamic_url_route` — is a "dependency" edge for the
purposes of `files_with_cross_file_dependents` below, mirroring the peer's
`DEPENDENCY_KINDS`.

## Why call edges are observed at the resolver, not read off the graph

`GraphBuilder._resolve_calls` (see `graph/_resolvers.py`) adds at most one
`calls` edge per `(caller_symbol, callee_symbol)` pair: "Several call sites
collapse onto one edge; the strongest wins." Two different call sites on two
different lines calling the same target from the same function become one
graph edge. Reading `(file, line, target)` off the built graph would therefore
undercount distinct call sites, in the *opposite* direction from the peer's
raw-vs-distinct trap (METHODOLOGY.md rule 2) — there the raw count is
inflated by a receiver-less twin per member call; here the graph edge count is
deflated by same-pair collapsing. So this module wraps `CallResolver.
resolve_file`, the one place every `ResolvedCall` passes through before that
collapse happens, and records each one directly.

`CallResolver.resolve_file` is also reused, unchanged, for two things that are
not calls:

  * `GraphBuilder._resolve_heritage` uses a *different* class (`HeritageResolver`)
    for `extends`/`implements`, so it never reaches this wrap.
  * `GraphBuilder._add_reference_edges` reuses the *same* `CallResolver`
    instance and calls `resolve_file(path, parsed.references)` to produce
    `references` edges — `parsed.references` is a `list[CallSite]` too (see
    `ParsedFile.references`'s docstring: "Reuses CallSite because resolving
    one is the identical problem").

Both invocations have the identical signature `resolve_file(file_path, calls)`,
so they cannot be told apart from the outside by type. They CAN be told apart
by object identity: `build_graph` parses every file itself and keeps
`parsed.calls` and `parsed.references` as two distinct list objects. The wrap
below only records an invocation whose `calls` argument `is` the `.calls` list
of that path's `ParsedFile` — the `.references` invocation, a different list
object even when its contents are also `CallSite`s, is observed but not
recorded. This is why `build_graph` must own the parse step rather than let
`GraphBuilder`/`CallResolver` parse internally: without holding the original
list objects, `calls_only` could not be told apart from `calls_or_refs`.

Distinct-call folding matters here too, independent of the raw/distinct
question above: `resolve_file` can be handed the same call site twice by an
`.scm` query that mints a receiver-less twin of a member call (see
METHODOLOGY.md rule 2 — this is our `java.scm`/`ruby.scm` behaviour, not
observed on gitleaks/Go). `resolved_call_edges` folds to distinct
`(file, line, target)` for exactly that reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from repowise.core.ingestion import call_resolver as cr
from repowise.core.ingestion.graph.builder import GraphBuilder
from repowise.core.ingestion.models import CallSite, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser

# Symbol kinds that make a file "symbol-bearing" — mirrors the peer's
# DECLARATION_KINDS, which excludes its `import` node kind (a reference to
# something declared elsewhere, not a declaration this file owns). We have no
# graph node analogous to that `import` kind; `parsed.symbols` is already
# declarations only, so every SymbolKind counts.
DECLARATION_KINDS = frozenset(
    {
        "function",
        "class",
        "method",
        "interface",
        "enum",
        "constant",
        "type_alias",
        "decorator",
        "trait",
        "impl",
        "struct",
        "module",
        "macro",
        "variable",
    }
)

# Structural edges: both endpoints are always the same file by construction
# (a file defines its own symbols; a class and its method live in the file
# that declared the class). Zero cross-file information. Excluded from every
# dependency reading below, same as the peer excludes its `contains` kind.
_STRUCTURAL_EDGE_KINDS = frozenset({"defines", "has_method"})

# co_changes is a git co-change correlation (see repowise.core.ingestion.
# cohesion), not a code dependency — excluded for the same reason.
_NON_DEPENDENCY_EDGE_KINDS = _STRUCTURAL_EDGE_KINDS | frozenset({"co_changes"})

# Every edge kind in repowise.core.ingestion.models.EdgeType that is not
# structural and not co_changes: anything left that can connect two different
# files is a "dependency" edge. Mirrors the peer's DEPENDENCY_KINDS, but wider
# — our vocabulary distinguishes calls from references from type-use where
# theirs collapses several of those into "references"/"instantiates".
DEPENDENCY_KINDS = frozenset(
    {
        "imports",
        "calls",
        "extends",
        "implements",
        "method_implements",
        "dispatches_to",
        "references",
        "type_use",
        "reads",
        "framework",
        "framework_binds",
        "dynamic_uses",
        "dynamic_imports",
        "dynamic_url_route",
    }
)


@dataclass(frozen=True, slots=True)
class BuildTimings:
    """Wall-clock seconds for each phase, measured with time.perf_counter()."""

    walk: float
    parse: float
    build: float

    @property
    def total(self) -> float:
        return self.walk + self.parse + self.build


@dataclass
class BuiltGraph:
    """Everything one `build_graph` call produces, held in memory.

    `resolved_calls` is populated by observing `CallResolver.resolve_file`
    while `build()` runs (see module docstring) — it is not derived from
    `graph` after the fact, because the graph collapses same-pair call sites
    that `resolved_calls` keeps distinct.
    """

    repo_path: Path
    graph: nx.DiGraph
    parsed_files: dict[str, ParsedFile]
    resolved_calls: list[tuple[str, int, str]]  # (file, line, target), raw — see resolved_call_edges
    parsed_count: int
    timings: BuildTimings


def _symbol_id_to_qualified_name(parsed_files: dict[str, ParsedFile]) -> dict[str, str]:
    """Map every Symbol.id to its qualified_name, across all parsed files.

    `ResolvedCall.callee_id` is a symbol node id (e.g. `"src/app.py::main"`),
    not a qualified name. The graph node for it carries `qualified_name`, but
    resolve_file runs before the corresponding graph edge exists, so this is
    built directly off the parse output instead of reading the graph back.
    """
    out: dict[str, str] = {}
    for parsed in parsed_files.values():
        for sym in parsed.symbols:
            out[sym.id] = sym.qualified_name
    return out


def build_graph(repo_path: Path | str) -> BuiltGraph:
    """Walk, parse, resolve and build the graph for one repo, timing each phase.

    Mirrors the established ~150-harness pattern: traverse -> parse -> wrap
    `CallResolver.resolve_file` to observe every resolved call -> `GraphBuilder
    .add_file` per parsed file -> `.build()`. The wrap is delegate-and-observe
    (call the original, record its input/output, return its result unchanged)
    and is always restored in `finally`, wrap failure or not.
    """
    repo_path = Path(repo_path).resolve()

    t0 = time.perf_counter()
    traverser = FileTraverser(repo_path)
    file_infos = list(traverser.traverse())
    t1 = time.perf_counter()

    parser = ASTParser()
    parsed_files: dict[str, ParsedFile] = {}
    for info in file_infos:
        try:
            source = Path(info.abs_path).read_bytes()
        except OSError:
            continue
        parsed = parser.parse_file(info, source)
        parsed_files[info.path] = parsed
    t2 = time.perf_counter()

    observed: list[tuple[str, int, str]] = []
    id_to_qname = _symbol_id_to_qualified_name(parsed_files)
    original_resolve_file = cr.CallResolver.resolve_file

    def _observing_resolve_file(
        self: cr.CallResolver, file_path: str, calls: list[CallSite]
    ) -> list[Any]:
        resolved = original_resolve_file(self, file_path, calls)
        # Only the true "calls" invocation is recorded — see module docstring
        # for why identity against parsed_files[...].calls is what tells it
        # apart from the "references" invocation, which reuses this same
        # method with an equally-typed but distinct list.
        pf = parsed_files.get(file_path)
        if pf is not None and calls is pf.calls:
            for rc in resolved:
                target = id_to_qname.get(rc.callee_id, rc.callee_id)
                observed.append((file_path, rc.line, target))
        return resolved

    cr.CallResolver.resolve_file = _observing_resolve_file  # type: ignore[method-assign]
    try:
        builder = GraphBuilder(repo_path)
        for parsed in parsed_files.values():
            builder.add_file(parsed)
        graph = builder.build()
    finally:
        cr.CallResolver.resolve_file = original_resolve_file  # type: ignore[method-assign]
    t3 = time.perf_counter()

    return BuiltGraph(
        repo_path=repo_path,
        graph=graph,
        parsed_files=parsed_files,
        resolved_calls=observed,
        parsed_count=len(parsed_files),
        timings=BuildTimings(walk=t1 - t0, parse=t2 - t1, build=t3 - t2),
    )


def resolved_call_edges(built: BuiltGraph) -> set[tuple[str, int, str]]:
    """Distinct resolved call edges as `(caller_file, line, target_qualified_name)`.

    Folded to distinct for the same reason the peer folds its `calls` edges
    (METHODOLOGY.md rule 2): a grammar can mint more than one CallSite for the
    same expression — our `java.scm`/`ruby.scm` add a receiver-less twin of
    every member call on top of the member-shaped site — and `resolve_file`
    resolves each site it is handed independently, so an unfolded count would
    not be injective on the number of real call expressions. Comparing a
    folded set against the peer's folded `calls_distinct` is the only
    apples-to-apples reading; see `resolved_calls` on `BuiltGraph` for the raw
    (possibly duplicated) sequence this folds.
    """
    return set(built.resolved_calls)


def symbol_bearing_files(built: BuiltGraph, languages: list[str] | None = None) -> set[str]:
    """Source files that declare at least one real symbol.

    Mirrors the peer's `symbol_bearing_files`: counts `parsed.symbols`
    (declarations only), never the synthetic `__module__` node `add_file`
    stamps on every file — that node exists so top-level calls have somewhere
    to attach, not because the file declares a "module" symbol, and counting
    it would make every walked file trivially symbol-bearing.
    """
    langs = set(languages) if languages else None
    out: set[str] = set()
    for path, parsed in built.parsed_files.items():
        if langs is not None and parsed.file_info.language not in langs:
            continue
        if any(sym.kind in DECLARATION_KINDS for sym in parsed.symbols):
            out.add(path)
    return out


def files_with_cross_file_dependents(
    built: BuiltGraph,
    edge_kinds: frozenset[str] = DEPENDENCY_KINDS,
    languages: list[str] | None = None,
    direction: str = "incoming",
) -> set[str]:
    """Files connected to another file by at least one edge of *edge_kinds*.

    Same semantics as the peer's function of the same name:

      incoming  something in another file depends on this file.
      outgoing  this file depends on something in another file.
      either    union of the two — the reading that reproduces CodeGraph's
                published table (see the G2 README).

    `src.file_path <> tgt.file_path` in the peer's SQL becomes, here, "the
    edge's two endpoint nodes resolve to different file paths." A file node's
    own id is already its path; a symbol node's file is `data["file_path"]`.
    Structural edges (`defines`, `has_method`) never reach this test in the
    first place — both endpoints are always same-file, so they would
    contribute nothing even if included — but they are still excluded
    up front via `DEPENDENCY_KINDS` rather than relying on that.
    """
    if direction not in ("incoming", "outgoing", "either"):
        raise ValueError(f"direction must be incoming|outgoing|either, got {direction!r}")

    langs = set(languages) if languages else None
    graph = built.graph

    def _file_of(node_id: str, data: dict[str, Any]) -> str | None:
        if data.get("node_type") == "file":
            return node_id
        if data.get("node_type") == "symbol":
            return data.get("file_path")
        return None

    def _lang_of(file_path: str) -> str | None:
        parsed = built.parsed_files.get(file_path)
        return parsed.file_info.language if parsed else None

    incoming: set[str] = set()
    outgoing: set[str] = set()
    for u, v, data in graph.edges(data=True):
        et = data.get("edge_type")
        if et not in edge_kinds:
            continue
        u_data = graph.nodes[u]
        v_data = graph.nodes[v]
        src_file = _file_of(u, u_data)
        tgt_file = _file_of(v, v_data)
        if src_file is None or tgt_file is None or src_file == tgt_file:
            continue
        if langs is not None:
            if _lang_of(src_file) not in langs and _lang_of(tgt_file) not in langs:
                continue
        if langs is None or _lang_of(tgt_file) in langs:
            incoming.add(tgt_file)
        if langs is None or _lang_of(src_file) in langs:
            outgoing.add(src_file)

    if direction == "incoming":
        return incoming
    if direction == "outgoing":
        return outgoing
    return incoming | outgoing


def language_histogram(built: BuiltGraph) -> dict[str, int]:
    """Count of parsed files per language, mirroring the peer's function."""
    out: dict[str, int] = {}
    for parsed in built.parsed_files.values():
        lang = parsed.file_info.language
        out[lang] = out.get(lang, 0) + 1
    return out


def edge_type_histogram(built: BuiltGraph) -> dict[str, int]:
    """Count of graph edges per edge_type — the discovery tool this module's
    docstring numbers came from. Not used by the coverage sweep; kept for
    verification and for the next reader who wants to re-check this repo's
    claims against a different corpus.
    """
    out: dict[str, int] = {}
    for _, _, data in built.graph.edges(data=True):
        et = data.get("edge_type", "<missing>")
        out[et] = out.get(et, 0) + 1
    return out
