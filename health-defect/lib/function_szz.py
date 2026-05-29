"""Function/symbol-level SZZ defect attribution (builds on ``lib/szz.py``).

File-level SZZ (``szz.py``) answers *which file* contained a bug at T0. This
module refines that to *which function/symbol* contained it, by retaining the
**bug-inducing commit set per file** and then, at scoring time (see
``build_function_dataset.py``), blaming each defective file **at T0** and
mapping every T0 line whose blamed commit is bug-inducing onto the enclosing
function (via the walker's symbol line-spans at T0).

The leakage guard is inherited and reinforced: a bug-inducing commit only
counts if it is an ancestor of the T0 commit (the bug already existed at T0),
and blaming *at T0* can only ever surface ancestors of T0 — so a function is
labeled defective strictly on pre-T0 evidence, exactly mirroring file-level.

Granularity caveat (documented, same spirit as file-level SZZ): blaming at T0
attributes *every* T0 line written by a bug-inducing commit to its function,
not only the specific lines the later fix changed (those lines have shifted
between the fix's parent and T0). This is the standard, pragmatic
line→function mapping and is strictly finer than the file-level label.

Pure-Python over ``git`` subprocesses — no new dependency, deterministic.
"""
from __future__ import annotations

from .defect_counter import _touched_source_files
from .szz import (
    _blame_range,
    _deleted_line_ranges,
    _ext,
    _file_lines,
    _is_cosmetic_line,
    _parent,
    _reachable_at_t0,
)


def _norm_line(s: str) -> str:
    """Whitespace-insensitive line fingerprint (blame runs with ``-w``)."""
    return " ".join(s.split())


def inducing_lines_by_file(
    repo_dir: str,
    t0_sha: str,
    fixes: list[tuple[str, str]],
    *,
    source_root: str,
    extensions: tuple[str, ...],
    fix_sha_set: set[str] | None = None,
    variant: str = "ag",
) -> dict[str, dict[tuple[str, str], set[str]]]:
    """Map ``file -> {(inducing_sha, line_fingerprint) -> set(fix_sha)}``.

    Mirrors ``szz.compute_szz`` but keeps the *specific bug-inducing line* — its
    inducing commit plus a whitespace-normalised content fingerprint — so the
    caller can localise the bug to a function precisely. This is the plan's
    "map each bug-inducing line to the enclosing function at T0": a line traced
    by ``git blame`` to an inducing commit (an ancestor of T0) was, by
    definition, unchanged from that commit through T0 up to the fix's parent —
    so the identical ``(sha, content)`` line is present at T0 and can be matched
    against a T0 blame. Matching the *line*, not just its commit, avoids the
    over-attribution of "any line the inducing commit ever wrote" (an inducing
    commit also authors plenty of non-buggy lines in the same file).

    ``variant`` selects B-SZZ (every blamed ancestor line) or AG-SZZ (skip
    cosmetic lines + fix-of-fix inducers) — AG is the default, matching the
    file-level label that ships.
    """
    fix_sha_set = fix_sha_set or {s for s, _ in fixes}
    reachable = _reachable_at_t0(repo_dir, t0_sha)
    out: dict[str, dict[tuple[str, str], set[str]]] = {}

    for fix_sha, _msg in fixes:
        parent = _parent(repo_dir, fix_sha)
        if parent is None:
            continue
        for file in _touched_source_files(repo_dir, fix_sha, source_root, extensions):
            ranges = _deleted_line_ranges(repo_dir, parent, fix_sha, file)
            if not ranges:
                continue
            parent_lines = _file_lines(repo_dir, parent, file)
            n_parent = len(parent_lines)
            ext = _ext(file)
            for start, count in ranges:
                end = min(start + count - 1, n_parent) if n_parent else start + count - 1
                if end < start:
                    continue
                for lineno, blamed in _blame_range(repo_dir, parent, file, start, end):
                    if blamed not in reachable:
                        continue  # bug introduced after T0 → not defective at T0
                    content = parent_lines[lineno - 1] if 1 <= lineno <= n_parent else ""
                    if variant == "ag":
                        if _is_cosmetic_line(content, ext):
                            continue
                        if blamed in fix_sha_set:
                            continue  # fix-of-fix is not the origin
                    fp = _norm_line(content)
                    if not fp:
                        continue
                    out.setdefault(file, {}).setdefault((blamed, fp), set()).add(fix_sha)
    return out
