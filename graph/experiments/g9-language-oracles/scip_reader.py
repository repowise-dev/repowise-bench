"""Read a SCIP protobuf index once, index its occurrences by file and range.

One reader for every language SCIP covers. Per-language differences (how a
document's `relative_path` roots relative to the repo, which position
encoding a given indexer emits) live in `LANG_TABLE` in `join.py`, not here.

Needs `scip_pb2`, compiled from `scip.proto` v0.10.0
(`github.com/sourcegraph/scip`) with `python -m grpc_tools.protoc`. Put the
directory holding it on `SCIP_PB2_DIR`, or anywhere on `sys.path`. The
README has the command.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_PROTO_OUT = os.environ.get("SCIP_PB2_DIR")
if _PROTO_OUT and _PROTO_OUT not in sys.path:
    sys.path.insert(0, _PROTO_OUT)

import scip_pb2  # noqa: E402

DEFINITION = 0x1  # scip_pb2.SymbolRole.Definition


@dataclass(frozen=True, slots=True)
class Occ:
    start_line: int  # 0-indexed, as SCIP stores it
    start_col: int
    end_line: int
    end_col: int
    symbol: str
    roles: int

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & DEFINITION)


class SCIPIndex:
    """In-memory view of one `index.scip`.

    - `definitions[symbol]` -> (relative_path, decl_line_1indexed,
      decl_col_0indexed), first Definition occurrence found for that symbol.
      A symbol with no Definition occurrence anywhere in the index is
      external (stdlib or a dependency) as far as this reader is concerned.
      The column travels alongside the line because SCIP's definition
      occurrence lands on the name token (e.g. a Java method's identifier),
      not on the declaration node repowise itself anchors on (which starts at
      any leading modifiers/annotations) -- `join.py`'s reanchoring step needs
      a point, not just a line, to find the enclosing declaration via
      tree-sitter.
    - `occurrences[relative_path]` -> list[Occ], every occurrence in that
      file, for spatial lookup by `symbol_at`.
    - `files` -> the set of `relative_path` the index actually covers (used
      to build the oracle header's `analysed_files`).
    """

    def __init__(self) -> None:
        self.definitions: dict[str, tuple[str, int, int]] = {}
        self.occurrences: dict[str, list[Occ]] = {}
        self.files: set[str] = set()

    @classmethod
    def load(cls, scip_path: str | Path) -> "SCIPIndex":
        idx = scip_pb2.Index()
        with open(scip_path, "rb") as fh:
            idx.ParseFromString(fh.read())
        self = cls()
        for doc in idx.documents:
            path = doc.relative_path.replace("\\", "/")
            self.files.add(path)
            occs: list[Occ] = []
            for o in doc.occurrences:
                rng = _range_of(o)
                if rng is None or not o.symbol:
                    continue
                sl, sc, el, ec = rng
                occ = Occ(sl, sc, el, ec, o.symbol, o.symbol_roles)
                occs.append(occ)
                if occ.is_definition and o.symbol not in self.definitions:
                    self.definitions[o.symbol] = (path, sl + 1, sc)
            self.occurrences[path] = occs
        return self

    def symbol_at(self, path: str, line0: int, col0: int) -> str | None:
        """The symbol occupying (line0, col0) in `path`, tree-sitter's point.

        Ties (several occurrences covering the same point, e.g. a definition
        occurrence and a nested reference) resolve to the smallest/innermost
        range, which is the one whose bounds most tightly wrap the queried
        point -- the same rule an editor's "go to definition" uses.
        """
        best: Occ | None = None
        for occ in self.occurrences.get(path, ()):
            if _contains(occ, line0, col0):
                if best is None or _narrower(occ, best):
                    best = occ
        return best.symbol if best else None


def _range_of(o) -> tuple[int, int, int, int] | None:
    which = o.WhichOneof("typed_range")
    if which == "single_line_range":
        r = o.single_line_range
        return r.line, r.start_character, r.line, r.end_character
    if which == "multi_line_range":
        r = o.multi_line_range
        return r.start_line, r.start_character, r.end_line, r.end_character
    # deprecated `range` repeated-int32 fallback
    if len(o.range) == 3:
        line, sc, ec = o.range
        return line, sc, line, ec
    if len(o.range) == 4:
        sl, sc, el, ec = o.range
        return sl, sc, el, ec
    return None


def _contains(occ: Occ, line0: int, col0: int) -> bool:
    if (line0, col0) < (occ.start_line, occ.start_col):
        return False
    if (line0, col0) > (occ.end_line, occ.end_col):
        return False
    return True


def _narrower(a: Occ, b: Occ) -> bool:
    a_span = (a.end_line - a.start_line, a.end_col - a.start_col)
    b_span = (b.end_line - b.start_line, b.end_col - b.start_col)
    return a_span < b_span
