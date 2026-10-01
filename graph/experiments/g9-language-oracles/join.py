"""Site -> decl keys: the SCIP oracle's join step.

For every tree-sitter call site (`callsites.py`) in a file the SCIP index
covers, look up the symbol at the callee span (`scip_reader.py`). Three
outcomes, matching the preregistered judgeable-caller definition:

  * symbol found AND it has an in-repo Definition occurrence -> an oracle
    edge `(caller_decl_file, caller_decl_line) -> (callee_decl_file,
    callee_decl_line)`.
  * symbol found but its only Definition (if any) is outside every analysed
    file (stdlib / a dependency) -> resolved-external. Counts toward "this
    call site was resolved" for the judgeable-caller test, contributes no
    edge.
  * no occurrence at all at that span -> unresolved. The caller that contains
    it is not judgeable: SCIP found nothing to confirm or deny there, same
    reason RTA withholds a function it never reached.

Per-language table lives in `callsites.py` (`LANG_TABLE`); this module is the
one join implementation for every language SCIP covers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Parser

_C_CPP_EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")


def _c_cpp_test_stem(scip_path: str) -> bool:
    """GoogleTest/Unity filename convention (repowise's own c.py/cpp.py
    LanguageSpec: test_stem_prefixes=("test_",),
    test_stem_suffixes=("_test", "_unittest")), for a C/C++ file whose test
    status isn't already caught by a test/tests/ directory check -- e.g. a
    *-test.cc sibling living next to non-test sources in the same directory,
    which fmt's test/ layout happens not to need (caught by the directory
    check already) but a future repo's flatter layout might."""
    if not scip_path.lower().endswith(_C_CPP_EXTS):
        return False
    stem = Path(scip_path).stem
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith("_unittest")
        or stem.endswith("-test")
    )

from callsites import LANG_TABLE, all_named_functions, call_sites, sole_direct_constructor_line
from scip_reader import SCIPIndex


@dataclass(frozen=True, slots=True)
class JoinResult:
    analysed_files: list[str]
    edges: list[tuple[str, int, str, int]]
    judgeable_callers: set[tuple[str, int]]


def _reanchor_decl_line(
    *,
    tree_cache: dict[str, tuple | None],
    repo_root: Path,
    scip_subdir: str,
    callee_scip_path: str,
    callee_line: int,
    callee_col: int,
    language: str,
) -> int:
    """Re-key a SCIP definition's line to the declaration node repowise
    itself anchors a symbol's `start_line` on.

    SCIP's Definition occurrence for a method lands on the name identifier
    token (e.g. Java: the token after a leading `@Override` annotation or
    `public`/`static` modifiers), while repowise's own `start_line` for the
    same symbol is the *declaration node's* start, which includes any
    leading modifiers/annotations as children of that node
    (`packages/core/.../ingestion/parser.py`:
    `start_line = def_node.start_point[0] + 1`, where `def_node` is the whole
    `method_declaration`/`constructor_declaration` node the `@symbol.def`
    query capture matched). Left unreconciled, a method preceded by even one
    single-line annotation scores a spurious (0, 1) (or worse) line offset on
    every edge that targets it, contaminating both the identity check and
    every downstream precision/recall number.

    Walking up from SCIP's point to the nearest ancestor whose type is in
    `LANG_TABLE[language].named_function_types` -- the same table
    `callsites.py` already uses to key the *caller* side, which the identity
    check already validated at offset (0, 0) -- puts both sides of every
    edge on the same line convention repowise itself uses. This is generic
    over every language `LANG_TABLE` covers, not a Java-specific patch; a
    language whose grammar has no such leading-modifier gap (nothing to walk
    past) simply finds the same node SCIP already pointed at and returns the
    same line unchanged.

    Falls back to SCIP's own line when no such ancestor is found (e.g. a
    field or class definition reached through some other call-like syntax)
    rather than dropping the edge.

    One more case, hand-check-confirmed on FluentValidation (see
    `results/csharp/ORACLE_CHECK.md`): scip-dotnet's occurrence for the
    type-name token in a `new X(...)` object-creation expression resolves to
    the TYPE symbol, not the selected constructor overload -- even when `X`
    declares an explicit constructor that repowise's own csharp.scm query
    (`object_creation_expression` -> `@call.target`) already treats as the
    call's target. Left alone, every such call site scores as a repowise
    "contradicted" edge against an oracle that quietly points one node too
    high. When the ancestor walk above lands on one of
    `LANG_TABLE[language].type_decl_types` without finding a named-function
    ancestor first, and that type declares exactly one direct constructor,
    re-key to the constructor instead of the type. Two or zero constructors
    (can't disambiguate an overload without argument types, or there is
    nothing explicit to point at) falls back to the type's own line, same as
    the no-ancestor case above.
    """
    entry = LANG_TABLE[language]
    cached = tree_cache.get(callee_scip_path)
    if cached is None and callee_scip_path not in tree_cache:
        abs_path = (
            repo_root / scip_subdir / callee_scip_path
            if scip_subdir
            else repo_root / callee_scip_path
        )
        try:
            source = abs_path.read_bytes()
        except OSError:
            tree_cache[callee_scip_path] = None
            cached = None
        else:
            parser = Parser(entry.grammar)
            cached = (parser.parse(source),)
            tree_cache[callee_scip_path] = cached
    if cached is None:
        return callee_line
    (tree,) = cached
    point = (callee_line - 1, callee_col)
    node = tree.root_node.descendant_for_point_range(point, point)
    if node is None:
        return callee_line
    # The type-name-token case is checked first and only at this exact
    # point, deliberately *not* folded into the ancestor walk below: every
    # node in a class eventually has that class_declaration as some ancestor
    # (a field, a property, a nested statement), so testing
    # `n.type in type_decl_types` while walking up would fire for any member
    # reference inside a single-constructor class, not just for a SCIP
    # position that is actually the type's own name token (the `new X(...)`
    # case this exists for). Restricting it to "is this node's immediate
    # parent the type declaration" -- true only for the `identifier` child
    # that names the type itself -- keeps it scoped to that one case.
    if node.parent is not None and node.parent.type in entry.type_decl_types and entry.constructor_node_type:
        type_node = node.parent
        ctor_line = sole_direct_constructor_line(
            type_node, entry.constructor_node_type, entry.type_decl_types
        )
        return ctor_line if ctor_line is not None else type_node.start_point[0] + 1
    n = node
    while n is not None:
        if n.type in entry.named_function_types:
            return n.start_point[0] + 1
        n = n.parent
    return callee_line


def build_oracle(
    *,
    repo_root: Path,
    scip_index_path: Path,
    language: str,
    scip_subdir: str = "",
    file_glob: str,
    exclude_tests: bool = False,
) -> JoinResult:
    """`scip_subdir` is the prefix to add to every SCIP `relative_path` to
    reach the same repo-root-relative form `ours.py`/`arms.norm_path` use
    (empty for gitleaks, where scip-go indexed the repo root directly;
    `"packages/zod/"` for zod, where scip-typescript indexed the workspace
    member directly; installing the full pnpm workspace changed no number).
    """
    scip = SCIPIndex.load(scip_index_path)

    def to_repo_rel(scip_path: str) -> str:
        return f"{scip_subdir}{scip_path}" if scip_subdir else scip_path

    def is_test_file(scip_path: str) -> bool:
        # G4's "notests" cells are defined by RTA/tsc roots that exclude test
        # entry points, not by a file-name filter -- but the effect on file
        # scope is the same convention every test runner uses, and it is the
        # only lever a SCIP index (which has no notion of "roots") has to
        # match that scope. Go: `_test.go` suffix is the language's own test
        # convention. TypeScript: zod's own `*.test.ts`/`*.test-d.ts`.
        return (
            scip_path.endswith("_test.go")
            or ".test." in scip_path
            or scip_path.endswith(".test-d.ts")
            # Java/Maven convention: test sources live under their own
            # source root, src/test/java/..., regardless of class naming
            # (Test.java, Tests.java, TestCase.java, or no suffix at all).
            # Filtering the directory rather than the class-name suffix
            # matches how the dev repos (gson, jsoup, commons-lang) are all
            # laid out and is the same directory repowise's own test-file
            # detection excludes from the "no tests" cell.
            or "/src/test/java/" in scip_path
            # C# convention (no single source-root rule the way Maven has
            # one): the dev repos lay test projects out two ways -- a
            # top-level test/ directory (Polly: test/Polly.Core.Tests/...)
            # or a *.Tests project living beside src/ (FluentValidation:
            # src/FluentValidation.Tests/...) -- and both name every test
            # class file with a Tests.cs/Test.cs suffix (xunit/MSTest
            # convention). Check both the directory and the filename so a
            # non-.Tests-suffixed helper class inside a test project (e.g.
            # Polly.TestUtils) is still caught by the directory half.
            or "/test/" in scip_path.lower()
            or ".tests/" in scip_path.lower()
            or ".testutils/" in scip_path.lower()
            or scip_path.endswith("Tests.cs")
            or scip_path.endswith("Test.cs")
            # C/C++ convention: repowise's own LanguageSpec for both
            # (c.py/cpp.py) declares test_stem_prefixes=("test_",),
            # test_stem_suffixes=("_test", "_unittest") -- GoogleTest/Unity
            # naming. Neither dev repo's test directory matches the "/test/"
            # substring check above (fmt's is a top-level test/, no leading
            # slash in the SCIP-relative path; redis's is plural tests/), so
            # a directory check is added alongside the filename-suffix one
            # already implied by the LanguageSpec convention.
            or scip_path.startswith("test/")
            or scip_path.startswith("tests/")
            or "/test/" in scip_path.lower()
            or "/tests/" in scip_path.lower()
            or _c_cpp_test_stem(scip_path)
        )

    scip_files = scip.files
    if exclude_tests:
        scip_files = {p for p in scip_files if not is_test_file(p)}

    analysed = sorted(to_repo_rel(p) for p in scip_files)
    analysed_set = set(analysed)

    edges: list[tuple[str, int, str, int]] = []
    resolved_ok: dict[tuple[str, int], bool] = {}
    def_tree_cache: dict[str, tuple | None] = {}

    for scip_path in sorted(scip_files):
        repo_rel = to_repo_rel(scip_path)
        abs_path = repo_root / scip_path if not scip_subdir else repo_root / scip_subdir / scip_path
        try:
            source = abs_path.read_bytes()
        except OSError:
            continue

        for line in all_named_functions(abs_path, source, language):
            resolved_ok.setdefault((repo_rel, line), True)

        for site in call_sites(abs_path, source, language):
            caller_key = (repo_rel, site.caller_decl_line)
            symbol = scip.symbol_at(scip_path, site.callee_line0, site.callee_col0)
            if symbol is None:
                resolved_ok[caller_key] = False
                continue
            defn = scip.definitions.get(symbol)
            if defn is None:
                # Resolved to a real symbol with no in-repo definition:
                # external. Resolved, not an edge.
                resolved_ok.setdefault(caller_key, True)
                continue
            callee_scip_path, callee_line, callee_col = defn
            callee_repo_rel = to_repo_rel(callee_scip_path)
            callee_line = _reanchor_decl_line(
                tree_cache=def_tree_cache,
                repo_root=repo_root,
                scip_subdir=scip_subdir,
                callee_scip_path=callee_scip_path,
                callee_line=callee_line,
                callee_col=callee_col,
                language=language,
            )
            resolved_ok.setdefault(caller_key, True)
            edges.append((repo_rel, site.caller_decl_line, callee_repo_rel, callee_line))

    judgeable = {k for k, ok in resolved_ok.items() if ok}
    return JoinResult(analysed_files=analysed, edges=edges, judgeable_callers=judgeable)


def write_oracle_jsonl(result: JoinResult, out_path: Path, *, oracle_name: str) -> None:
    """G4's own `oracle.jsonl` shape (`compare.py.load_oracle`): one `_header`
    record, one edge record per line, one `_reachable` record. Reusing this
    shape -- rather than a new one -- is what lets `compare.py` score the
    SCIP oracle with zero changes to that file.
    """
    with out_path.open("w", encoding="utf-8") as fh:
        header = {
            "_header": True,
            "oracle": oracle_name,
            "algorithm": "scip-decl-join",
            "analysed_files": result.analysed_files,
            "analysed_file_count": len(result.analysed_files),
            "root_count": None,
            "load_errors": 0,
        }
        fh.write(json.dumps(header) + "\n")
        for cf, cl, tf, tl in result.edges:
            fh.write(
                json.dumps(
                    {
                        "caller_decl_file": cf,
                        "caller_decl_line": cl,
                        "callee_file": tf,
                        "callee_line": tl,
                    }
                )
                + "\n"
            )
        fh.write(
            json.dumps({"_reachable": True, "funcs": [list(k) for k in sorted(result.judgeable_callers)]})
            + "\n"
        )
