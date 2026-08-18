#!/usr/bin/env python
"""Generate semantics-known mutations of a real repository, into a scratch copy.

Tool-agnostic by construction: this script only edits source files on disk and
emits a manifest describing what it did. It never imports `repowise`, never
reads a `.repowise/` or `.codegraph/` index, and never touches `--src` — it
copies the tree to `--dst` first and mutates the copy. See
`PREREGISTRATION.md` in this directory for what each mutation is for and what
a correct resolver versus a name-matcher is predicted to do with it.

Mutations implemented here (Go only, via tree-sitter):

  M1  decoy twin       — real.        A same-named, unreachable declaration.
  M2  consistent rename — real.       A symbol renamed everywhere, isomorphically.
  M3  shadowing        — real.        A local shadows an imported package inside
                                       one call's scope.
  M4  overload split    — stub.       Go has no overloading; there is nothing to
                                       split. NotImplementedError with a comment.
  M5  vendored twin     — stub.       Needs an import-graph rewrite to be a
                                       faithful copy-without-import-change on Go's
                                       package-path-is-identity model; out of scope
                                       for the Go-first pass. NotImplementedError.

Determinism contract: same `--seed` + same `--src` tree => byte-identical
`--dst` tree and byte-identical manifest. That means the manifest must never
carry a wall-clock timestamp or an absolute path (two runs into two different
`--dst` directories must diff empty). Every collection that a selection is
drawn from is sorted before a `random.Random(seed)` touches it, so a rerun on
an unrelated machine or a different `--dst` path can't reorder a choice.

Language ceiling: Go only, on purpose. The pre-registration asks for one
language done properly rather than five done shallowly, and Go is the
suggested first target (simple syntax, no overloading, real receiver typing
already landed in our resolver). Extending to another language means adding
another `_collect_*` pass with that language's tree-sitter grammar, not
touching the mutation logic below it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from random import Random

from tree_sitter import Language, Node, Parser
import tree_sitter_go

_GO_LANGUAGE = Language(tree_sitter_go.language())


def _go_parser() -> Parser:
    return Parser(_GO_LANGUAGE)


# ---------------------------------------------------------------------------
# Tree-sitter symbol/call collection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Declaration:
    """One `func`/method declaration site."""

    name: str
    file: str  # POSIX-relative to the mutated tree root
    line: int  # 1-based
    is_method: bool
    params_text: str
    result_text: str  # "" if the function returns nothing


@dataclass(frozen=True)
class CallSite:
    """One call expression, keyed by the bare name it invokes.

    For `pkg.Func(...)` or `recv.Method(...)` the name is the field
    (`Func`/`Method`); for `foo(...)` it is the identifier itself. This is
    deliberately a name match, not a resolved edge — it is the denominator
    the pre-registration asks for ("how many call sites in the repo name that
    symbol"), independent of whether any tool under test can resolve it.
    """

    name: str
    file: str
    line: int
    operand: str | None  # package/receiver identifier for `x.Name(...)`, else None


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_go_files(root: Path) -> list[Path]:
    """All `.go` files under root, sorted for determinism.

    No vendor/testdata exclusion: gitleaks (the first target repo) vendors
    nothing (plain go.mod, no `vendor/`), and its `testdata/` holds fixture
    text files, not `.go` sources, so there is nothing to filter out here yet.
    A repo with a vendor tree would need that added.
    """
    return sorted(root.rglob("*.go"))


def _node_text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8")


def _collect_declarations(root: Path) -> dict[str, list[Declaration]]:
    """Map symbol name -> every `func`/method declaration of that name."""
    out: dict[str, list[Declaration]] = {}
    parser = _go_parser()
    for path in _iter_go_files(root):
        src = path.read_bytes()
        tree = parser.parse(src)
        for node in _walk(tree.root_node):
            if node.type not in ("function_declaration", "method_declaration"):
                continue
            is_method = node.type == "method_declaration"
            name_type = "field_identifier" if is_method else "identifier"
            name_node = next((c for c in node.children if c.type == name_type), None)
            if name_node is None:
                continue
            param_lists = [c for c in node.children if c.type == "parameter_list"]
            # function_declaration: [params, result?]
            # method_declaration:   [receiver, params, result?]
            params_node = param_lists[1] if is_method else param_lists[0]
            result_node = None
            for c in node.children:
                if c.start_byte > params_node.end_byte and c.type in (
                    "parameter_list",
                    "type_identifier",
                    "pointer_type",
                    "qualified_type",
                    "generic_type",
                    "array_type",
                    "slice_type",
                    "map_type",
                    "interface_type",
                    "struct_type",
                    "function_type",
                ):
                    result_node = c
                    break
            decl = Declaration(
                name=_node_text(name_node, src),
                file=_rel(path, root),
                line=name_node.start_point[0] + 1,
                is_method=is_method,
                params_text=_node_text(params_node, src),
                result_text=_node_text(result_node, src) if result_node else "",
            )
            out.setdefault(decl.name, []).append(decl)
    for name in out:
        out[name].sort(key=lambda d: (d.file, d.line))
    return out


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _collect_call_sites(root: Path) -> dict[str, list[CallSite]]:
    """Map invoked name -> every call expression naming it, across the repo."""
    out: dict[str, list[CallSite]] = {}
    parser = _go_parser()
    for path in _iter_go_files(root):
        src = path.read_bytes()
        tree = parser.parse(src)
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            fn = node.children[0]
            if fn.type == "identifier":
                name, operand = _node_text(fn, src), None
            elif fn.type == "selector_expression":
                field_node = next((c for c in fn.children if c.type == "field_identifier"), None)
                operand_node = fn.children[0]
                if field_node is None:
                    continue
                name, operand = _node_text(field_node, src), _node_text(operand_node, src)
            else:
                continue
            site = CallSite(
                name=name,
                file=_rel(path, root),
                line=fn.start_point[0] + 1,
                operand=operand,
            )
            out.setdefault(name, []).append(site)
    for name in out:
        out[name].sort(key=lambda s: (s.file, s.line))
    return out


def _grep_count(root: Path, token: str) -> int:
    """Word-boundary occurrence count of `token` across every file in the tree.

    Used two ways: to pick an M2 replacement name that is provably absent
    before it is chosen, and to report the pre-rename count the verification
    step asks for.
    """
    pattern = re.compile(r"\b" + re.escape(token) + r"\b")
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_dir() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        count += len(pattern.findall(text))
    return count


# ---------------------------------------------------------------------------
# Selection (seeded, deterministic)
# ---------------------------------------------------------------------------


def _pick_with_seed(candidates: list[str], rng: Random) -> str:
    """Pick one name from an already-sorted candidate list.

    `random.Random(seed)` still governs the pick (via `shuffle`) so the seed
    has real effect on ties, but the input is sorted first so the shuffle
    itself is reproducible instead of depending on dict/set iteration order.
    """
    pool = sorted(candidates)
    rng.shuffle(pool)
    return pool[0]


def _call_count(name: str, calls: dict[str, list[CallSite]]) -> int:
    return len(calls.get(name, []))


def choose_m1_target(
    decls: dict[str, list[Declaration]], calls: dict[str, list[CallSite]], rng: Random
) -> str:
    """Heaviest-called function/method name, tie-broken by the seed."""
    ranked = sorted(decls, key=lambda n: (-_call_count(n, calls), n))
    if not ranked:
        raise ValueError("no Go function/method declarations found")
    top_count = _call_count(ranked[0], calls)
    tied = [n for n in ranked if _call_count(n, calls) == top_count]
    return _pick_with_seed(tied, rng)


def choose_m2_target(
    decls: dict[str, list[Declaration]],
    calls: dict[str, list[CallSite]],
    rng: Random,
    exclude: set[str],
) -> str:
    """A uniquely-declared, non-method, well-called symbol.

    Non-method and singly-declared so a repo-wide textual rename cannot
    conflate it with an unrelated same-named method on a different receiver
    type (Go permits `func (A) Foo()` and `func (B) Foo()` to coexist; a
    plain-text rename of "Foo" would wrongly merge them). Top-level functions
    declared exactly once don't have that ambiguity.
    """
    eligible = sorted(
        n
        for n, ds in decls.items()
        if len(ds) == 1 and not ds[0].is_method and n not in exclude and _call_count(n, calls) > 0
    )
    if not eligible:
        raise ValueError("no eligible M2 rename target (unique top-level, called function)")
    ranked = sorted(eligible, key=lambda n: (-_call_count(n, calls), n))
    top_count = _call_count(ranked[0], calls)
    tied = [n for n in ranked if _call_count(n, calls) == top_count]
    return _pick_with_seed(tied, rng)


def _fresh_name(old: str, root: Path, rng: Random) -> str:
    """A name that appears nowhere in the tree, verified by grep, not assumed."""
    salt_pool = sorted(f"{i:04d}" for i in range(1, 10000))
    for salt in salt_pool:
        candidate = f"{old}G5Mut{salt}"
        if _grep_count(root, candidate) == 0:
            return candidate
    raise RuntimeError("exhausted candidate salts without finding a free name")  # pragma: no cover


# ---------------------------------------------------------------------------
# M1: decoy twin
# ---------------------------------------------------------------------------

_DECOY_DIR = "zz_g5_decoy_unreachable"
_DECOY_PACKAGE = "g5decoymutation"


def apply_m1(root: Path, seed: int, rng: Random) -> dict:
    decls = _collect_declarations(root)
    calls = _collect_call_sites(root)
    name = choose_m1_target(decls, calls, rng)
    original = decls[name][0]  # first declaration site, deterministic (sorted)

    decoy_dir = root / _DECOY_DIR
    decoy_dir.mkdir(exist_ok=True)
    decoy_path = decoy_dir / "decoy.go"

    result_clause = f" {original.result_text}" if original.result_text else ""
    body = (
        f"package {_DECOY_PACKAGE}\n\n"
        f"// Package {_DECOY_PACKAGE} is G5 mutation 1 (decoy twin). It declares a\n"
        f'// function named "{name}" — the same name as {original.file}:{original.line} —\n'
        "// in a directory nothing imports. No call site in the repository can\n"
        "// reach this declaration: it exists only to see whether a resolver\n"
        "// binds a call by bare name rather than by scope. A signature is copied\n"
        "// from the original for plausibility; it is not guaranteed to type-check\n"
        "// against this package's (empty) imports, only to parse, which is all\n"
        "// this mutation's syntax-validity check requires.\n"
        f"func {name}{original.params_text}{result_clause} {{\n"
        '\tpanic("g5-mutation: decoy twin, unreachable")\n'
        "}\n"
    )
    decoy_path.write_text(body, encoding="utf-8", newline="\n")

    call_sites = calls.get(name, [])
    return {
        "id": "m1",
        "kind": "decoy_twin",
        "seed": seed,
        "symbol": name,
        "original_declaration": {
            "file": original.file,
            "line": original.line,
            "is_method": original.is_method,
        },
        "decoy_file": _rel(decoy_path, root),
        "decoy_package": _DECOY_PACKAGE,
        "files_touched": [_rel(decoy_path, root)],
        "call_sites_naming_symbol": len(call_sites),
        "call_sites": [
            {"file": c.file, "line": c.line, "operand": c.operand} for c in call_sites
        ],
    }


# ---------------------------------------------------------------------------
# M2: consistent rename
# ---------------------------------------------------------------------------


def apply_m2(root: Path, seed: int, rng: Random, exclude: set[str]) -> dict:
    decls = _collect_declarations(root)
    calls = _collect_call_sites(root)
    old_name = choose_m2_target(decls, calls, rng, exclude)

    pre_rename_new_name_count = None  # filled in per-candidate inside _fresh_name's grep
    new_name = _fresh_name(old_name, root, rng)
    # Report the exact grep count that justified picking `new_name`: zero, by
    # construction of _fresh_name, but re-checked here rather than assumed so
    # the manifest states a measured number and not a promise.
    pre_rename_new_name_count = _grep_count(root, new_name)

    pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
    touched: list[str] = []
    for path in _iter_go_files(root):
        text = path.read_text(encoding="utf-8")
        new_text, n = pattern.subn(new_name, text)
        if n:
            path.write_text(new_text, encoding="utf-8", newline="\n")
            touched.append(_rel(path, root))
    touched.sort()

    call_sites = calls.get(old_name, [])
    return {
        "id": "m2",
        "kind": "consistent_rename",
        "seed": seed,
        "symbol": old_name,
        "old_name": old_name,
        "new_name": new_name,
        "rename_map": {old_name: new_name},
        "pre_rename_new_name_grep_count": pre_rename_new_name_count,
        "files_touched": touched,
        "call_sites_naming_symbol": len(call_sites),
    }


# ---------------------------------------------------------------------------
# M3: shadowing
# ---------------------------------------------------------------------------


def _collect_imports(path: Path, src: bytes, tree_root: Node) -> dict[str, str]:
    """Map local package identifier -> import path, for one file."""
    out: dict[str, str] = {}
    for node in _walk(tree_root):
        if node.type != "import_spec":
            continue
        alias_node = next((c for c in node.children if c.type == "package_identifier"), None)
        path_node = next(
            (c for c in node.children if c.type in ("interpreted_string_literal", "raw_string_literal")),
            None,
        )
        if path_node is None:
            continue
        import_path = _node_text(path_node, src).strip('"').strip("`")
        local = _node_text(alias_node, src) if alias_node else import_path.rsplit("/", 1)[-1]
        out[local] = import_path
    return out


@dataclass(frozen=True)
class ShadowCandidate:
    file: str
    func_name: str
    pkg_ident: str
    import_path: str
    call_line: int
    block_start_byte: int
    block_end_byte: int


def _enclosing_block_and_func(node: Node, src: bytes) -> tuple[Node | None, str | None]:
    """Innermost `block` containing `node`, and the name of the function or
    method that block ultimately belongs to (walking past any nested
    if/for/switch blocks to find the declaration itself).
    """
    innermost = None
    cur = node.parent
    while cur is not None:
        if cur.type == "block" and innermost is None:
            innermost = cur
        if cur.type in ("function_declaration", "method_declaration"):
            name_type = "field_identifier" if cur.type == "method_declaration" else "identifier"
            name_node = next((c for c in cur.children if c.type == name_type), None)
            func_name = _node_text(name_node, src) if name_node else None
            return innermost, func_name
        cur = cur.parent
    return innermost, None


def _find_shadow_candidates(root: Path) -> list[ShadowCandidate]:
    parser = _go_parser()
    candidates: list[ShadowCandidate] = []
    for path in _iter_go_files(root):
        src = path.read_bytes()
        tree = parser.parse(src)
        imports = _collect_imports(path, src, tree.root_node)
        if not imports:
            continue
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            fn = node.children[0]
            if fn.type != "selector_expression":
                continue
            operand_node = fn.children[0]
            if operand_node.type != "identifier":
                continue
            operand = _node_text(operand_node, src)
            if operand not in imports:
                continue  # not a package-qualified call (e.g. a receiver var)
            block, func_name = _enclosing_block_and_func(node, src)
            if block is None or func_name is None:
                continue
            candidates.append(
                ShadowCandidate(
                    file=_rel(path, root),
                    func_name=func_name,
                    pkg_ident=operand,
                    import_path=imports[operand],
                    call_line=fn.start_point[0] + 1,
                    block_start_byte=block.start_byte,
                    block_end_byte=block.end_byte,
                )
            )
    candidates.sort(key=lambda c: (c.file, c.call_line, c.pkg_ident))
    return candidates


def apply_m3(root: Path, seed: int, rng: Random) -> dict:
    candidates = _find_shadow_candidates(root)
    if not candidates:
        raise ValueError("no package-qualified call sites found for M3")
    chosen = _pick_with_seed_obj(candidates, rng)

    path = root / chosen.file
    # BYTES, not text. `block_start_byte` was computed by tree-sitter over
    # `path.read_bytes()`, so it indexes the file as it sits on disk, CRLF and
    # all. Reading it back with `read_text` collapses every CRLF to one LF, so
    # the same logical position sits one byte earlier per preceding line and
    # the offset lands that many bytes too far along -- 37 lines in, 37 bytes
    # into the wrong statement.
    #
    # Not hypothetical: this split the string literal in jfrog.go's
    # `[]string{"jfrog", "artifactory", ...}` into `"art` + the inserted
    # statement + `ifactory"`, and `gofmt -e` rejected the file. It went
    # unnoticed because the combined m1,m2,m3 run picks a different candidate,
    # one where the drift happened to land somewhere still parseable -- so the
    # syntax gate passed while the shadow statement sat in the wrong scope.
    src = path.read_bytes()
    # Insert immediately after the enclosing block's opening `{`, as a new
    # first statement, so every call to `pkg_ident.X(...)` later in that
    # block sees the local instead of the import. `_ = ident` keeps the
    # inserted identifier itself from tripping "declared and not used" so the
    # only thing that breaks is the intended shadowing, not an unrelated
    # compile error the resolver would never see in real code.
    insert_at = chosen.block_start_byte + 1  # just past the '{'
    shadow_stmt = (
        f"\n\t{chosen.pkg_ident} := 0 "
        f'// g5-mutation: shadows import "{chosen.import_path}"\n'
        f"\t_ = {chosen.pkg_ident}"
    )
    # The inserted statement takes the line ending the file already uses, so a
    # CRLF file stays CRLF and the mutation is a pure insertion. Writing bytes
    # back also leaves every unrelated line untouched, which is what makes
    # "the only difference is the mutation" true rather than approximately so.
    eol = b"\r\n" if src.count(b"\r\n") * 2 > src.count(b"\n") else b"\n"
    payload = shadow_stmt.encode("utf-8").replace(b"\n", eol)
    new_src = src[:insert_at] + payload + src[insert_at:]
    path.write_bytes(new_src)

    return {
        "id": "m3",
        "kind": "shadowing",
        "seed": seed,
        "symbol": chosen.pkg_ident,
        "shadowed_import": chosen.import_path,
        "function": chosen.func_name,
        "call_site": {"file": chosen.file, "line": chosen.call_line},
        "files_touched": [chosen.file],
    }


def _pick_with_seed_obj(candidates: list, rng: Random):
    pool = list(candidates)  # already sorted by caller
    rng.shuffle(pool)
    return pool[0]


# ---------------------------------------------------------------------------
# M4 / M5: stubs
# ---------------------------------------------------------------------------


def apply_m4(root: Path, seed: int, rng: Random) -> dict:
    # Go has no function/method overloading — there is no arity-different
    # sibling to add that the compiler would accept under the same name.
    # A faithful M4 needs a language with overloading (C#, Java); the Go-first
    # pass stops here rather than fake an overload Go cannot express.
    raise NotImplementedError("M4 (overload split) needs an overloaded language; not implemented for Go")


def apply_m5(root: Path, seed: int, rng: Random) -> dict:
    # A faithful vendored twin copies a called module to a second import path
    # without changing any importer. In Go, package identity IS the import
    # path, so a byte-copy under a new path is invisible to every existing
    # `import` line and calls nothing new — the mutation would be a no-op
    # unless we also rewrite an import to point at the copy, which is a
    # different mutation (M2-shaped) wearing this name. Needs real design
    # before it is worth building; not implemented for Go.
    raise NotImplementedError("M5 (vendored twin) needs import-graph rewrite design; not implemented for Go")


# ---------------------------------------------------------------------------
# Syntax validity
# ---------------------------------------------------------------------------


def check_syntax(root: Path, files: list[str]) -> dict:
    """Verify every touched file is still syntactically valid Go.

    Prefers `gofmt -e` (the real compiler's own formatter, invoked with
    `-e` to report all syntax errors rather than stopping at the first) when
    `gofmt` is on PATH. Falls back to a tree-sitter parse check
    (`root_node.has_error`) otherwise, since tree-sitter grammars are
    error-tolerant and can't be trusted alone when a stricter tool is
    available.
    """
    gofmt = shutil.which("gofmt")
    if gofmt:
        errors = []
        for rel in files:
            proc = subprocess.run(
                [gofmt, "-e", str(root / rel)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                errors.append({"file": rel, "stderr": proc.stderr.strip()})
        return {"method": "gofmt -e", "files_checked": len(files), "errors": errors, "ok": not errors}

    parser = _go_parser()
    errors = []
    for rel in files:
        src = (root / rel).read_bytes()
        tree = parser.parse(src)
        if tree.root_node.has_error:
            errors.append({"file": rel, "stderr": "tree-sitter: parse error in tree"})
    return {
        "method": "tree-sitter has_error",
        "files_checked": len(files),
        "errors": errors,
        "ok": not errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_MUTATIONS = {
    "m1": apply_m1,
    "m2": apply_m2,
    "m3": apply_m3,
    "m4": apply_m4,
    "m5": apply_m5,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, type=Path, help="read-only source repository")
    ap.add_argument("--dst", required=True, type=Path, help="scratch destination for the mutated copy")
    ap.add_argument(
        "--mutations",
        default="m1,m2",
        help="comma-separated subset of m1,m2,m3,m4,m5 (default: m1,m2)",
    )
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--force", action="store_true", help="overwrite a non-empty --dst")
    ap.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="manifest output path (default: <dst>/G5_MANIFEST.json)",
    )
    args = ap.parse_args(argv)

    src: Path = args.src.resolve()
    dst: Path = args.dst.resolve()
    if not src.is_dir():
        ap.error(f"--src {src} is not a directory")
    if dst.exists() and any(dst.iterdir()) and not args.force:
        ap.error(f"--dst {dst} exists and is non-empty; pass --force to overwrite")

    requested = [m.strip() for m in args.mutations.split(",") if m.strip()]
    unknown = [m for m in requested if m not in _MUTATIONS]
    if unknown:
        ap.error(f"unknown mutation(s): {unknown}; choose from {sorted(_MUTATIONS)}")

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"))

    rng = Random(args.seed)
    used_symbols: set[str] = set()
    results = []
    for mutation_id in requested:
        if mutation_id == "m1":
            r = apply_m1(dst, args.seed, rng)
        elif mutation_id == "m2":
            r = apply_m2(dst, args.seed, rng, exclude=used_symbols)
        elif mutation_id == "m3":
            r = apply_m3(dst, args.seed, rng)
        elif mutation_id == "m4":
            r = apply_m4(dst, args.seed, rng)
        else:
            r = apply_m5(dst, args.seed, rng)
        used_symbols.add(r.get("symbol", ""))
        results.append(r)

    all_touched = sorted({f for r in results for f in r.get("files_touched", [])})
    syntax_report = check_syntax(dst, all_touched)

    manifest = {
        # No absolute paths and no timestamp: two runs with the same seed
        # into two different --dst directories must produce byte-identical
        # manifests, and both of those would make the diff non-empty.
        "repo": src.name,
        "seed": args.seed,
        "mutations_requested": requested,
        "mutations": results,
        "syntax_check": syntax_report,
    }
    manifest_path = args.manifest or (dst / "G5_MANIFEST.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8", newline="\n"
    )

    print(f"wrote {manifest_path}")
    print(json.dumps(manifest, indent=2, sort_keys=False))
    return 0 if syntax_report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
