"""Tree-sitter call-expression callee spans, one file at a time.

Reuses repowise's own per-language call queries verbatim
(`packages/core/src/repowise/core/ingestion/queries/<lang>.scm`, found in the
installed `repowise` package, or in `$REPOWISE_QUERY_DIR` when set) instead of
re-deriving which node shapes are a call
in each grammar -- exactly the queries `CallResolver` is built from, so a
callee span found here is a callee span repowise itself would have tried to
resolve. Only the `@call.target` capture is read; `@call.receiver`,
`@call.arguments`, `@reference.*` captures fire from the same query file (it
also covers non-call references) and are ignored here.

Per-language table (`LANG_TABLE` below): grammar module, query filename, and
which node types count as a caller's own function body versus a nested
function-literal whose calls still attribute to the enclosing named function
(G4's caller-keying rule, TYPESCRIPT.md / README.md "The method").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Language, Parser, Query, QueryCursor

def _find_query_dir() -> Path:
    """The `.scm` queries of the repowise build under test. Point
    `REPOWISE_QUERY_DIR` at a checkout's `.../core/ingestion/queries` when
    this interpreter does not have that build installed."""
    import os
    from importlib.util import find_spec

    env = os.environ.get("REPOWISE_QUERY_DIR")
    if env:
        return Path(env)
    spec = find_spec("repowise.core")
    if spec and spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations))) / "ingestion" / "queries"
    raise RuntimeError("set REPOWISE_QUERY_DIR to repowise's ingestion/queries directory")


_QUERY_DIR = _find_query_dir()


@dataclass(frozen=True, slots=True)
class LangEntry:
    grammar: object  # tree_sitter.Language
    query_path: Path
    # Node types that introduce a *named* function scope, i.e. the caller key
    # G4 uses. A call inside any *other* function-shaped node (func literal,
    # arrow function, closure) attributes to the nearest ancestor of one of
    # these types instead.
    named_function_types: frozenset[str]
    # Node types that introduce a function scope at all, named or not --
    # walking past one of these (that isn't in named_function_types) means
    # "still inside the same caller", not "found the caller".
    any_function_types: frozenset[str]
    # Type-declaration node types (class/struct/record/interface...) whose
    # SCIP Definition a `new X(...)` call site's occurrence commonly resolves
    # to -- see join.py's `_reanchor_decl_line`: scip-dotnet (and plausibly
    # scip-java) points the type-name token in an object-creation expression
    # at the TYPE symbol, not the selected constructor overload, even when
    # the class declares an explicit constructor repowise's own csharp.scm
    # query already treats as the call target. Empty for languages with no
    # such ambiguity (Go has no constructors; TS's class constructor is a
    # single unambiguous `constructor(...)` member already covered by the
    # method path).
    type_decl_types: frozenset[str] = field(default_factory=frozenset)
    # The node type of a constructor declaration within one of the types
    # above, used to find it among that type's direct members.
    constructor_node_type: str = ""


def _go_table() -> LangEntry:
    import tree_sitter_go as tsgo

    return LangEntry(
        grammar=Language(tsgo.language()),
        query_path=_QUERY_DIR / "go.scm",
        named_function_types=frozenset({"function_declaration", "method_declaration"}),
        any_function_types=frozenset(
            {"function_declaration", "method_declaration", "func_literal"}
        ),
    )


def _typescript_table() -> LangEntry:
    import tree_sitter_typescript as tsts

    return LangEntry(
        grammar=Language(tsts.language_typescript()),
        query_path=_QUERY_DIR / "typescript.scm",
        named_function_types=frozenset(
            {"function_declaration", "method_definition", "function_signature"}
        ),
        any_function_types=frozenset(
            {
                "function_declaration",
                "method_definition",
                "function_signature",
                "arrow_function",
                "function_expression",
                "generator_function",
                "generator_function_declaration",
            }
        ),
    )


def _java_table() -> LangEntry:
    import tree_sitter_java as tsjava

    # java.scm anchors `@symbol.def` on method_declaration and
    # constructor_declaration only (see the Symbols section at the top of
    # java.scm) -- no anchor for lambda_expression, and none for a method
    # declared inside an anonymous class body either, because that method is
    # itself a method_declaration and already matches the same pattern
    # wherever it is nested. So no separate anonymous-class-body boundary is
    # needed the way TS needed `_named_arrow_boundary`: a call inside an
    # anonymous class's method already keys to that method_declaration by the
    # normal ancestor walk, and a call inside a lambda with no enclosing
    # method_declaration of its own folds up to the nearest named method,
    # per G4's rule for anonymous closures.
    return LangEntry(
        grammar=Language(tsjava.language()),
        query_path=_QUERY_DIR / "java.scm",
        named_function_types=frozenset({"method_declaration", "constructor_declaration"}),
        any_function_types=frozenset(
            {"method_declaration", "constructor_declaration", "lambda_expression"}
        ),
        type_decl_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
        ),
        constructor_node_type="constructor_declaration",
    )


def _csharp_table() -> LangEntry:
    import tree_sitter_c_sharp as tscs

    # csharp.scm anchors `@symbol.def` on method_declaration,
    # constructor_declaration and property_declaration only (the whole
    # property, not each get/set accessor -- csharp.scm has no separate
    # accessor capture). It has no anchor for operator_declaration,
    # indexer_declaration, conversion_operator_declaration or
    # destructor_declaration either, so those are left out of
    # named_function_types here too: a call site inside one has no repowise
    # caller key to compare against, so it is dropped on both sides rather
    # than mis-attributed to the enclosing class. Lambdas, anonymous methods
    # and local functions are not their own symbol, so calls inside them
    # fold up to the nearest true named ancestor, per G4's rule for
    # anonymous closures.
    return LangEntry(
        grammar=Language(tscs.language()),
        query_path=_QUERY_DIR / "csharp.scm",
        named_function_types=frozenset(
            {"method_declaration", "constructor_declaration", "property_declaration"}
        ),
        any_function_types=frozenset(
            {
                "method_declaration",
                "constructor_declaration",
                "property_declaration",
                "local_function_statement",
                "lambda_expression",
                "anonymous_method_expression",
                "operator_declaration",
                "indexer_declaration",
                "conversion_operator_declaration",
                "destructor_declaration",
            }
        ),
        type_decl_types=frozenset(
            {"class_declaration", "struct_declaration", "record_declaration", "interface_declaration"}
        ),
        constructor_node_type="constructor_declaration",
    )


def _rust_table() -> LangEntry:
    import tree_sitter_rust as tsrust

    # rust.scm anchors `@symbol.def` for a callable on `function_item` (with
    # or without a leading `(visibility_modifier)` child) only -- the same
    # node type whether it sits at the top level, inside an `impl` block, or
    # inside a `trait` block (tree-sitter-rust does not give impl/trait
    # methods a distinct node type from free functions; the type is
    # `function_item` in all three positions, disambiguated only by the
    # parent chain). `function_signature_item` (a trait's undefaulted
    # `fn foo();` or an `extern "C"` declaration) has no body, so it can
    # never contain a call site and is deliberately left out of
    # `named_function_types` -- the brief's "function_item in impl and trait
    # blocks and at the top level" names exactly one node type, not two.
    #
    # No `_named_arrow_boundary`-style helper is needed the way TS needed
    # one: Rust closures (`closure_expression`) and async blocks
    # (`async_block`) are expressions, never their own `@symbol.def`, so a
    # call inside one simply is not in `named_function_types` and the
    # existing ancestor walk in `_enclosing_named_function_line` keeps
    # climbing past it to the nearest enclosing `function_item` on its own,
    # per G4's closure-attribution rule -- confirmed in identity.md.
    return LangEntry(
        grammar=Language(tsrust.language()),
        query_path=_QUERY_DIR / "rust.scm",
        named_function_types=frozenset({"function_item"}),
        any_function_types=frozenset(
            {"function_item", "closure_expression", "async_block"}
        ),
    )


def _python_table() -> LangEntry:
    import tree_sitter_python as tspy

    # python.scm anchors `@symbol.def` on `function_definition` for every
    # def -- module-level function, class method, or a def nested inside
    # another def; tree-sitter-python >= 0.23 shares one node type for `def`
    # and `async def`, and the query has no nesting guard, so a nested def
    # gets its own `@symbol.def` match exactly like a top-level one. That
    # means repowise anchors nested defs *separately* (own symbol, own
    # start_line), so per G4's rule a call inside a nested def attributes to
    # that nested def, not folded up into its enclosing function -- unlike a
    # lambda, which gets no `@symbol.def` match at all (no query pattern
    # covers `lambda`) and so folds up to the nearest named ancestor `def`,
    # same as every other language's anonymous-closure case here.
    #
    # Decorators do NOT need `_reanchor_decl_line`-style handling the way
    # Java/C# annotations/modifiers do: python.scm's `decorated_definition`
    # pattern captures `@symbol.def` on the *inner* `function_definition`/
    # `class_definition` node itself, not on the wrapping
    # `decorated_definition`, so repowise's `start_line` for a decorated
    # function is already the `def` keyword's own line, decorators or not.
    # Confirmed in identity.md: no `(0, N)` decorator-offset bucket.
    #
    # No `type_decl_types`/`constructor_node_type`: unlike `new X(...)` in
    # Java/C#, Python has no separate constructor node type to disambiguate
    # (`__init__` is just another `function_definition` inside the class
    # body), so the generic single-constructor re-key this table drives for
    # other languages does not apply here without misfiring on every other
    # method in the class. Left empty; `MyClass(args)` call sites are
    # checked directly in the identity hand-check instead.
    return LangEntry(
        grammar=Language(tspy.language()),
        query_path=_QUERY_DIR / "python.scm",
        named_function_types=frozenset({"function_definition"}),
        any_function_types=frozenset({"function_definition", "lambda"}),
    )


def _c_table() -> LangEntry:
    import tree_sitter_cpp as tscpp

    # c.py's LanguageSpec sets shares_grammar_with="cpp": .c files parse with
    # the tree_sitter_cpp grammar (C is a subset of it for our purposes) but
    # load c.scm, not cpp.scm -- c.scm has no qualified_identifier/
    # class_specifier/lambda patterns at all, just plain function_definition
    # (identifier declarator), forward declarations and macros. A forward
    # declaration (`declaration` node) has no body, so it can never contain a
    # call site and is deliberately left out of named_function_types --
    # matches the C++ table's same omission for the same reason.
    return LangEntry(
        grammar=Language(tscpp.language()),
        query_path=_QUERY_DIR / "c.scm",
        named_function_types=frozenset({"function_definition"}),
        any_function_types=frozenset({"function_definition"}),
    )


def _cpp_table() -> LangEntry:
    import tree_sitter_cpp as tscpp

    # cpp.scm's @symbol.def for a callable is function_definition in every
    # shape it matches: a free function, an inline in-class method
    # (declarator: field_identifier), an out-of-line Foo::bar (declarator:
    # qualified_identifier, one or two levels), a destructor definition/
    # out-of-line (destructor_name), and an out-of-line operator overload
    # (operator_name) -- all still function_definition, so one node type
    # covers the whole "methods defined out of line" pitfall the brief
    # flags: repowise already anchors Foo::bar at its own function_definition
    # regardless of where Foo is declared, same place scip-clang's Definition
    # occurrence for Foo::bar lands (the body), not the in-class or header
    # declaration (a `field_declaration`/`declaration` with no body, which
    # cpp.scm gives its own @symbol.def for a pure-virtual/abstract case --
    # deliberately NOT in named_function_types here, matching C#/Java's
    # declaration-vs-definition handling: a declaration-only site never
    # contains a call, so it cannot be a caller key, and scip_reader.py's
    # `definitions` already prefers a Definition-role occurrence over a
    # Declaration-role one for the same symbol when both exist).
    # lambda_expression is a function-shaped node with no @symbol.def anchor
    # of its own in cpp.scm, so it is only in any_function_types: a call
    # inside a lambda folds up to the nearest enclosing function_definition,
    # per G4's anonymous-closure rule (same treatment as every other
    # language's closures in this table).
    #
    # No type_decl_types/constructor_node_type: unlike Java/C#'s `new X(...)`,
    # C++ constructors are plain function_definitions (declarator is the
    # class name itself, e.g. `Foo::Foo(...)` or an in-class `Foo(...)`), the
    # same node type as every other method -- there is no separate
    # constructor node type to collect without also collecting every other
    # inline method, so the generic single-constructor re-key this table
    # drives for Java/C# would misfire here. Left empty; `new X(...)`/
    # `X(...)` construction call sites are checked directly in the identity
    # hand-check instead, same as Python's __init__.
    return LangEntry(
        grammar=Language(tscpp.language()),
        query_path=_QUERY_DIR / "cpp.scm",
        named_function_types=frozenset({"function_definition"}),
        any_function_types=frozenset({"function_definition", "lambda_expression"}),
    )


LANG_TABLE: dict[str, LangEntry] = {
    "go": _go_table(),
    "typescript": _typescript_table(),
    "java": _java_table(),
    "csharp": _csharp_table(),
    "rust": _rust_table(),
    "python": _python_table(),
    "c": _c_table(),
    "cpp": _cpp_table(),
}


@dataclass(frozen=True, slots=True)
class CallSite:
    caller_decl_line: int  # 1-indexed, outermost named function, G4 rule
    callee_line0: int  # 0-indexed, tree-sitter point, for the SCIP lookup
    callee_col0: int


def _named_arrow_boundary(n) -> object | None:
    """TS/JS-only: an arrow function or function expression that repowise's
    own `typescript.scm` mints a *named* symbol for, because it is the value
    of a `const`/`let` declarator (`const foo = () => {}`) or a class field
    (`foo = () => {}`). repowise keys that declaration at the wrapping
    `lexical_declaration`/`variable_declaration` (for the const/let form) or
    at the `public_field_definition` itself (for the class-field form) -- see
    `typescript.scm`'s `@symbol.def` anchors, the same anchors `ours.py`'s
    graph nodes carry as `start_line`. An arrow function that is *not* one of
    these two shapes (an inline callback, an object-literal property value)
    gets no symbol there either, so it is not a caller-key boundary: a call
    inside it still folds up to the nearest true named ancestor, per G4's
    rule for anonymous closures.
    """
    if n.type not in ("arrow_function", "function_expression"):
        return None
    parent = n.parent
    if parent is not None and parent.type == "variable_declarator":
        grandparent = parent.parent
        if grandparent is not None and grandparent.type in (
            "lexical_declaration",
            "variable_declaration",
        ):
            return grandparent
    if parent is not None and parent.type in ("public_field_definition", "field_definition"):
        return parent
    return None


def sole_direct_constructor_line(
    type_node, constructor_node_type: str, boundary_types: frozenset[str]
) -> int | None:
    """The start line of `type_node`'s one explicit direct-member constructor,
    or None.

    Walks `type_node`'s subtree collecting `constructor_node_type` nodes, but
    does not recurse past a nested node whose own type is in
    `boundary_types` (another class/struct/interface/record declaration), so
    a nested type's own constructor is never mistaken for the outer type's.
    Returns None rather than guessing when there are zero (implicit default
    constructor -- nothing for repowise to have anchored on either) or more
    than one (can't disambiguate which overload a `new X(...)` site bound to
    without argument types, which this join has no access to) explicit
    constructors, so the caller can fall back to the type's own declaration
    line in either case.
    """
    found: list[int] = []

    def walk(n):
        for child in n.children:
            if child.type == constructor_node_type:
                found.append(child.start_point[0] + 1)
            elif child.type in boundary_types:
                continue  # nested type: its members are not this type's
            else:
                walk(child)

    walk(type_node)
    return found[0] if len(found) == 1 else None


def _enclosing_named_function_line(node, named: frozenset[str]) -> int | None:
    n = node.parent
    while n is not None:
        if n.type in named:
            # Declaration line: the function node's own start, matching G4's
            # "outermost named function" rule and repowise's own start_line
            # for the same symbol (validated in identity.md for offset (0,0)).
            return n.start_point[0] + 1
        boundary = _named_arrow_boundary(n)
        if boundary is not None:
            return boundary.start_point[0] + 1
        n = n.parent
    return None


def call_sites(file_path: Path, source: bytes, language: str) -> list[CallSite]:
    """Every call-expression callee span in one file, keyed to its caller.

    A call whose enclosing function cannot be found (top-level call, e.g. in
    an `init()`-less package block or a top-level script statement) is
    dropped: G4's own oracle has no caller key for those either, so there is
    nothing on the other side of this join to compare it against.
    """
    entry = LANG_TABLE[language]
    parser = Parser(entry.grammar)
    tree = parser.parse(source)
    query_text = entry.query_path.read_text(encoding="utf-8")
    query = Query(entry.grammar, query_text)
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)
    targets = captures.get("call.target", [])

    out: list[CallSite] = []
    for node in targets:
        caller_line = _enclosing_named_function_line(node, entry.named_function_types)
        if caller_line is None:
            continue
        sl, sc = node.start_point
        out.append(CallSite(caller_decl_line=caller_line, callee_line0=sl, callee_col0=sc))
    return out


def all_named_functions(file_path: Path, source: bytes, language: str) -> list[int]:
    """Declaration lines (1-indexed) of every named function/method in the file.

    Used to build the judgeable-caller set: a function that declares zero
    call sites is trivially judgeable (there is nothing in it to fail to
    resolve), and without this pass it would never appear in `join.py`'s
    per-caller resolution tally at all.
    """
    entry = LANG_TABLE[language]
    parser = Parser(entry.grammar)
    tree = parser.parse(source)
    out: list[int] = []

    def walk(n):
        if n.type in entry.named_function_types:
            out.append(n.start_point[0] + 1)
        boundary = _named_arrow_boundary(n)
        if boundary is not None:
            out.append(boundary.start_point[0] + 1)
        for child in n.children:
            walk(child)

    walk(tree.root_node)
    return out


def declaration_identity(
    file_path: Path, source: bytes, language: str, decl_line: int
) -> tuple[str, str] | None:
    """(enclosing type name, method name) for the named-function declaration
    starting at 1-indexed `decl_line`, or `None` if the declaration cannot be
    found or the language has no type context (`type_decl_types` empty).

    Used only by the overload-set secondary score (`score.py`): plain line
    keying treats every overload as a distinct declaration, but a resolver
    that collapses overloads to one node per (class, method name) is wrong
    about *which* overload, not about the class or the method -- this gives
    the coarser key needed to tell those two failure modes apart.
    """
    entry = LANG_TABLE[language]
    if not entry.type_decl_types:
        return None
    parser = Parser(entry.grammar)
    tree = parser.parse(source)

    target = None

    def walk(n):
        nonlocal target
        if target is not None:
            return
        if n.type in entry.named_function_types and n.start_point[0] + 1 == decl_line:
            target = n
            return
        for child in n.children:
            walk(child)

    walk(tree.root_node)
    if target is None:
        return None
    name_node = target.child_by_field_name("name")
    if name_node is None:
        return None
    method_name = source[name_node.start_byte : name_node.end_byte].decode("utf-8", "replace")

    n = target.parent
    type_name = None
    while n is not None:
        if n.type in entry.type_decl_types:
            tn = n.child_by_field_name("name")
            if tn is not None:
                type_name = source[tn.start_byte : tn.end_byte].decode("utf-8", "replace")
            break
        n = n.parent
    if type_name is None:
        return None
    return (type_name, method_name)
