#!/usr/bin/env python3
"""error_handling.py — per-language tree-sitter detectors for swallowed-exception
and unsafe-unwrap anti-patterns. Precision-first (a noisy query is worse than
none), so every detector targets the *unambiguous* shape and degrades to "no
signal" rather than guessing.

Detectors (each returns a count of occurrences in a file):

  * **swallowed_catch** — a ``catch``/``except`` whose body has no real handling:
    empty block, or only ``pass`` / ``...`` / a docstring / comments. Languages:
    Python (``except_clause``), JS/TS (``catch_clause``), Java/Kotlin/C#/C++
    (``catch_clause`` / ``catch_block``). This is the canonical "swallow the
    error" smell.
  * **bare_except** (Python) — ``except:`` / ``except Exception:`` /
    ``except BaseException:`` (catch-all) regardless of body. Catches everything
    incl. ``KeyboardInterrupt``; a recognised antipattern.
  * **unsafe_unwrap** (Rust) — ``.unwrap()`` / ``.expect(...)`` calls and the
    ``panic!`` / ``unreachable!`` / ``todo!`` / ``unimplemented!`` macros. Each
    is a latent panic-on-error.
  * **go_swallow** (Go) — an empty ``if err != nil { }`` block (error checked then
    ignored) and a blank-identifier discard ``_ = call()`` / ``x, _ := call()``
    of a call's return (the idiomatic way Go drops an error).

The per-file signal ``eh_count`` is the sum of all applicable detectors for that
file's language. ``run_self_test()`` validates each detector against handcrafted
positive/negative fixtures and is the gate the experiment runs FIRST.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent
_REPOWISE_ROOT = _BENCH_DIR.parents[1]
for _src in ("core", "cli", "server"):
    _p = _REPOWISE_ROOT / "packages" / _src / "src"
    if _p.exists():
        sys.path.insert(0, str(_p))

# Catch/except clause node types per language.
_CATCH_KINDS = {
    "python": {"except_clause"},
    "typescript": {"catch_clause"}, "tsx": {"catch_clause"},
    "javascript": {"catch_clause"}, "jsx": {"catch_clause"},
    "java": {"catch_clause"},
    "kotlin": {"catch_block"},
    "cpp": {"catch_clause"},
    "csharp": {"catch_clause"},
}
_BLOCK_KINDS = {"block", "statement_block", "compound_statement"}
# Statement node types that count as "no real handling" inside a catch body.
_TRIVIAL_STMT = {"comment", "pass_statement", "line_comment", "block_comment"}


def _get_parser(language: str, cache: dict):
    from tree_sitter import Parser
    if language in cache:
        return cache[language]
    parser = None
    try:
        from repowise.core.ingestion.parser import _get_language
        grammar = _get_language(language)
        parser = Parser(grammar) if grammar is not None else None
    except Exception:
        parser = None
    cache[language] = parser
    return parser


def _text(node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _named(node):
    return [c for c in node.children if c.is_named]


def _find_body_block(clause):
    """The block-like child of a catch/except clause (its handler body)."""
    for c in clause.children:
        if c.type in _BLOCK_KINDS:
            return c
    # Kotlin catch_block / some grammars nest the block one level down.
    for c in clause.children:
        for g in c.children:
            if g.type in _BLOCK_KINDS:
                return g
    return None


def _is_trivial_stmt(stmt, language: str) -> bool:
    if stmt.type in _TRIVIAL_STMT:
        return True
    if language == "python":
        if stmt.type == "expression_statement":
            inner = _named(stmt)
            if inner and inner[0].type in {"ellipsis", "string"}:
                return True
            if not inner:
                return True
    return False


def _body_is_swallowed(block, language: str) -> bool:
    real = [c for c in _named(block) if not _is_trivial_stmt(c, language)]
    return len(real) == 0


def _is_bare_except(clause) -> bool:
    """Python ``except:`` / ``except Exception:`` / ``except BaseException:``."""
    kids = [c for c in clause.children if c.type not in {"comment"}]
    # children: 'except' [ (':' immediately) | <type-expr> ... ':' ] block
    after = [c for c in kids if c.type not in {"except", ":"} and c.type not in _BLOCK_KINDS]
    if not after:
        return True  # bare `except:`
    # `except Exception:` / `except BaseException:` (single catch-all identifier)
    first = after[0]
    if first.type in {"identifier"} and _text(first) in {"Exception", "BaseException"}:
        # not an `as` binding alone, and no tuple of specific types
        return True
    return False


def _count_catches(root, language: str) -> tuple[int, int]:
    """(swallowed_catch, bare_except) over the tree."""
    catch_kinds = _CATCH_KINDS.get(language, set())
    swallowed = 0
    bare = 0
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in catch_kinds:
            block = _find_body_block(n)
            if block is not None and _body_is_swallowed(block, language):
                swallowed += 1
            if language == "python" and _is_bare_except(n):
                bare += 1
        stack.extend(n.children)
    return swallowed, bare


_RUST_UNWRAP_METHODS = {"unwrap", "expect", "unwrap_unchecked"}
_RUST_PANIC_MACROS = {"panic", "unreachable", "todo", "unimplemented"}


def _count_rust_unsafe(root) -> int:
    cnt = 0
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "field_expression":
                field = fn.child_by_field_name("field")
                if field is not None and _text(field) in _RUST_UNWRAP_METHODS:
                    cnt += 1
        elif n.type == "macro_invocation":
            mac = n.child_by_field_name("macro")
            if mac is not None and _text(mac) in _RUST_PANIC_MACROS:
                cnt += 1
        stack.extend(n.children)
    return cnt


def _cond_is_err_check(cond_text: str) -> bool:
    t = cond_text.replace(" ", "")
    return ("err!=nil" in t) or ("err==nil" in t)


def _count_go_swallow(root) -> int:
    """Empty ``if err != nil { }`` + blank-identifier discard of a call return."""
    cnt = 0
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "if_statement":
            cond = n.child_by_field_name("condition")
            cons = n.child_by_field_name("consequence")
            if cond is not None and cons is not None and _cond_is_err_check(_text(cond)):
                if len(_named(cons)) == 0:
                    cnt += 1
        elif n.type in {"short_var_declaration", "assignment_statement"}:
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            if left is not None and right is not None:
                left_kids = _named(left)
                has_blank = any(c.type == "blank_identifier" or _text(c) == "_"
                                for c in left_kids)
                right_is_call = any(c.type == "call_expression" for c in _named(right)) \
                    or right.type == "expression_list" and any(
                        c.type == "call_expression" for c in _named(right))
                # multi-return discard: ≥2 LHS, a call on the RHS, a blank present
                if has_blank and len(left_kids) >= 2 and right_is_call:
                    cnt += 1
        stack.extend(n.children)
    return cnt


def detect_file(source: bytes, language: str, parser_cache: dict) -> dict[str, int] | None:
    """Return per-detector counts for one file, or None if unparseable/unsupported."""
    parser = _get_parser(language, parser_cache)
    if parser is None:
        return None
    try:
        tree = parser.parse(source)
    except Exception:
        return None
    root = tree.root_node
    out = {"swallowed_catch": 0, "bare_except": 0, "unsafe_unwrap": 0, "go_swallow": 0}
    if language in _CATCH_KINDS:
        sw, bare = _count_catches(root, language)
        out["swallowed_catch"] = sw
        out["bare_except"] = bare
    if language == "rust":
        out["unsafe_unwrap"] = _count_rust_unsafe(root)
    if language == "go":
        out["go_swallow"] = _count_go_swallow(root)
    out["eh_count"] = (out["swallowed_catch"] + out["bare_except"]
                       + out["unsafe_unwrap"] + out["go_swallow"])
    return out


# --------------------------------------------------------------------------
# Handcrafted fixtures — precision validation (run FIRST, plan §Part C)
# --------------------------------------------------------------------------
_FIXTURES = [
    # (language, source, expected_eh_count, note)
    ("python", b"try:\n    x()\nexcept Exception:\n    pass\n", 2,
     "empty Exception catch → swallowed(1) + bare(1)"),
    ("python", b"try:\n    x()\nexcept ValueError:\n    pass\n", 1,
     "empty specific catch → swallowed(1), not bare"),
    ("python", b"try:\n    x()\nexcept:\n    ...\n", 2, "bare except + ellipsis body"),
    ("python", b"try:\n    x()\nexcept ValueError as e:\n    logger.error(e)\n    raise\n", 0,
     "handled + re-raised → clean"),
    ("python", b"try:\n    x()\nexcept ValueError:\n    return None\n", 0,
     "specific catch with real handling → clean"),
    ("python", b"def f():\n    return 1\n", 0, "no try → clean"),
    ("javascript", b"try { go(); } catch (e) {}\n", 1, "empty JS catch"),
    ("javascript", b"try { go(); } catch (e) { console.error(e); }\n", 0, "handled JS catch"),
    ("typescript", b"try { go(); } catch (e) { /* ignore */ }\n", 1, "comment-only TS catch"),
    ("java", b"class A { void m(){ try { go(); } catch (Exception e) {} } }\n", 1,
     "empty Java catch"),
    ("java", b"class A { void m(){ try { go(); } catch (Exception e) { log(e); } } }\n", 0,
     "handled Java catch"),
    ("kotlin", b"fun f(){ try { go() } catch (e: Exception) {} }\n", 1, "empty Kotlin catch"),
    ("kotlin", b"fun f(){ try { go() } catch (e: Exception) { log(e) } }\n", 0,
     "handled Kotlin catch"),
    ("csharp", b"class A{ void M(){ try { Go(); } catch (Exception e) {} } }\n", 1,
     "empty C# catch"),
    ("csharp", b"class A{ void M(){ try { Go(); } catch (Exception e) { Log(e); } } }\n", 0,
     "handled C# catch"),
    ("cpp", b"void f(){ try { go(); } catch (std::exception& e) {} }\n", 1, "empty C++ catch"),
    ("cpp", b"void f(){ try { go(); } catch (std::exception& e) { log(e); } }\n", 0,
     "handled C++ catch"),
    ("rust", b"fn f() { let x = g().unwrap(); }\n", 1, "unwrap"),
    ("rust", b"fn f() { let x = g().expect(\"boom\"); panic!(\"x\"); }\n", 2,
     "expect + panic!"),
    ("rust", b"fn f() -> Result<i32,E> { let x = g()?; Ok(x) }\n", 0,
     "? operator → clean"),
    ("go", b"func f() { v, _ := g(); _ = v }\n", 1, "blank discard of call return"),
    ("go", b"func f() { if err != nil {} }\n", 1, "empty if-err block"),
    ("go", b"func f() { if err != nil { return err } }\n", 0, "handled if-err"),
    ("go", b"func f() { x := g(); _ = x }\n", 0, "single assign, no multi-return discard"),
]


def run_self_test(verbose: bool = True) -> bool:
    cache: dict = {}
    ok = True
    for lang, src, expected, note in _FIXTURES:
        res = detect_file(src, lang, cache)
        got = res["eh_count"] if res else None
        passed = got == expected
        ok = ok and passed
        if verbose:
            mark = "PASS" if passed else "FAIL"
            detail = "" if res is None else (
                f"sw={res['swallowed_catch']} bare={res['bare_except']} "
                f"unwrap={res['unsafe_unwrap']} go={res['go_swallow']}")
            print(f"  [{mark}] {lang:11s} exp={expected} got={got}  {note}  {detail}")
    print(f"\nself-test: {'ALL PASS' if ok else 'FAILURES PRESENT'}")
    return ok


if __name__ == "__main__":
    run_self_test()
