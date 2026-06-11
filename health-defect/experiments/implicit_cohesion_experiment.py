"""EXPERIMENT: does implicit-receiver field detection give the new JVM/C-family
languages more LCOM4 (low_cohesion) signal?

Current walker counts a method's instance-member refs only via explicit
self/this receivers (self.x / this.x / this->x). Kotlin/Java/C++/C# idiomatically
write bare `field` — so LCOM4 falls through to the "no signal" valve (lcom4=1)
and low_cohesion never fires. This prototype additionally counts bare identifiers
that match a class's *declared field names*, recomputes LCOM4, and reports how
many classes would now trip the low_cohesion gate (lcom4>=2 AND method_count>=5).

Offline only: walks the cloned repos, no re-index. If the firing lift is real,
the next step is a proper re-index + AUC measurement on 1-2 repos.
"""

from __future__ import annotations

import os as _os
import sys
from collections import defaultdict
from pathlib import Path

_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(Path(__file__).resolve().parents[3])))
sys.path.insert(0, str(_oss / "packages/core/src"))

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.complexity.walker import (
    _collect_class_methods,
    _collect_class_nodes,
    _collect_self_members,
    _find_name,
    _IDENTIFIER_SUFFIX,
)
from repowise.core.ingestion.parser import _get_language
from tree_sitter import Parser

REPOS = Path(__file__).resolve().parents[2] / "repos"
# (repo_dir_glob_root, language, extensions, field_decl_kinds)
TARGETS = [
    ("caffeine/caffeine/src/main/java", "java", (".java",), {"field_declaration"}),
    ("mockito/mockito-core/src/main/java", "java", (".java",), {"field_declaration"}),
    ("detekt", "kotlin", (".kt",), {"property_declaration"}),
    ("coroutines/kotlinx-coroutines-core", "kotlin", (".kt",), {"property_declaration"}),
    ("spdlog/include", "cpp", (".h", ".hpp"), {"field_declaration"}),
    ("fmt/include", "cpp", (".h",), {"field_declaration"}),
    ("npgsql/src", "csharp", (".cs",), {"field_declaration", "property_declaration"}),
    ("quartznet/src", "csharp", (".cs",), {"field_declaration", "property_declaration"}),
]

_TYPE_KINDS = {"type_identifier", "primitive_type"}
_BODY_KINDS = {"block", "function_body", "compound_statement", "accessor_list"}


def decl_field_names(class_node, field_decl_kinds: set[str]) -> set[str]:
    names: set[str] = set()
    stack = list(class_node.children)
    while stack:
        n = stack.pop()
        if n.type in field_decl_kinds:
            inner = list(n.children)
            while inner:
                m = inner.pop()
                if m.type in _BODY_KINDS:
                    continue  # skip accessor/method bodies inside a property
                if m.type.endswith(_IDENTIFIER_SUFFIX) and m.type not in _TYPE_KINDS and m.text:
                    names.add(m.text.decode("utf-8", "replace"))
                inner.extend(m.children)
        for c in n.children:
            stack.append(c)
    return names


def bare_members(method_node, field_set: set[str], lmap) -> set[str]:
    """Explicit self-members PLUS bare identifiers matching declared fields."""
    members = set(_collect_self_members(method_node, lmap))
    stack = list(method_node.children)
    while stack:
        n = stack.pop()
        if n.type in lmap.class_kinds:
            continue
        if n.type.endswith(_IDENTIFIER_SUFFIX) and n.text:
            t = n.text.decode("utf-8", "replace")
            if t in field_set:
                members.add(t)
        for c in n.children:
            stack.append(c)
    return members


def lcom4(method_names: list[str], members_per_method: list[set[str]]) -> int:
    n = len(method_names)
    if n == 0:
        return 1
    if sum(len(m) for m in members_per_method) == 0:
        return 1
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    name_to_idx = {nm: i for i, nm in enumerate(method_names)}
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, mem in enumerate(members_per_method):
        for m in mem:
            buckets[m].append(i)
    for m, idxs in buckets.items():
        grp = list(idxs)
        if m in name_to_idx:
            grp.append(name_to_idx[m])
        for o in grp[1:]:
            ra, rb = find(grp[0]), find(o)
            if ra != rb:
                parent[ra] = rb
    return len({find(i) for i in range(n)})


def fires(lc: int, mc: int) -> bool:
    return lc >= 2 and mc >= 5


totals = defaultdict(lambda: [0, 0, 0])  # lang -> [classes>=5methods, old_fire, new_fire]
for root, lang, exts, fdk in TARGETS:
    lmap = get_language_map(lang)
    grammar = _get_language(lang)
    base = REPOS / root
    files = [p for e in exts for p in base.rglob(f"*{e}")] if base.exists() else []
    files = [p for p in files if "/test" not in p.as_posix().lower() and "test/" not in p.as_posix().lower()]
    o_fire = n_fire = big = 0
    for fp in files:
        try:
            src = fp.read_bytes()
            tree = Parser(grammar).parse(src)
        except Exception:
            continue
        for cnode in _collect_class_nodes(tree.root_node, lmap):
            mnodes = _collect_class_methods(cnode, lmap)
            if len(mnodes) < 5:
                continue
            big += 1
            mnames = [_find_name(m) for m in mnodes]
            old = [_collect_self_members(m, lmap) for m in mnodes]
            fset = decl_field_names(cnode, fdk)
            new = [bare_members(m, fset, lmap) for m in mnodes]
            if fires(lcom4(mnames, old), len(mnodes)):
                o_fire += 1
            if fires(lcom4(mnames, new), len(mnodes)):
                n_fire += 1
    repo = root.split("/")[0]
    print(f"{repo:12} {lang:8} classes>=5m={big:5}  low_cohesion fires: old={o_fire:4} new={n_fire:4}")
    t = totals[lang]
    t[0] += big; t[1] += o_fire; t[2] += n_fire

print("\n=== per language ===")
print(f"{'lang':10}{'classes>=5m':>12}{'old_fire':>10}{'new_fire':>10}{'lift':>7}")
for lang in sorted(totals):
    big, o, n = totals[lang]
    print(f"{lang:10}{big:12}{o:10}{n:10}{n-o:+7}")
