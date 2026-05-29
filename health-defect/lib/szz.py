"""SZZ defect attribution (Śliwerski–Zimmermann–Zeller).

Replaces the coarse "a fix commit touched this file" label with a label that is
attributed to the file that actually *contained* the bug at T0:

    A source file is **defective-at-T0** iff at least one *bug-inducing* commit
    — found by ``git blame``-ing the lines a post-T0 fix changed, back to the
    commit(s) that last wrote them — existed in the history at T0 (is an ancestor
    of the T0 commit) and touched that file.

This is exactly the quantity ``health-at-T0`` is supposed to predict, with the
leakage of "the fix itself manufactured the signal" removed (the inducing commit
must predate T0).

Two variants are computed in one blame pass and cached side by side:

* **B-SZZ** — blame every changed/deleted line; every blamed ancestor is
  bug-inducing.
* **AG-SZZ** (default) — the "annotation-graph" refinement: ignore blank,
  comment-only and pure-punctuation lines before blaming, and drop blamed
  commits that are themselves fixes (a fix-of-a-fix is not the origin). This is
  the field-recommended default; B-SZZ is kept for the label-quality comparison.

Pure-Python over ``git`` subprocesses (no new dependency, deterministic, cached)
— same house style as ``defect_counter.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .defect_counter import _git, _touched_source_files

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
_SHA_LINE_RE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)")

# Per-extension single-line comment prefixes. Block-comment continuation lines
# (`/* … */`, leading `*`) and pure punctuation are handled generically below.
_LINE_COMMENT = {
    ".py": ("#",),
    ".rs": ("//",),
    ".go": ("//",),
    ".js": ("//",),
    ".jsx": ("//",),
    ".ts": ("//",),
    ".tsx": ("//",),
    ".mts": ("//",),
    ".cts": ("//",),
    ".java": ("//",),
    ".kt": ("//",),
    ".c": ("//",),
    ".cc": ("//",),
    ".cpp": ("//",),
    ".h": ("//",),
    ".hpp": ("//",),
    ".cs": ("//",),
}
_PUNCT_ONLY = set("{}()[];,")


def _ext(path: str) -> str:
    i = path.rfind(".")
    return path[i:] if i >= 0 else ""


def _is_cosmetic_line(content: str, ext: str) -> bool:
    """True for blank, comment-only or pure-punctuation lines (AG-SZZ skips them
    so a whitespace/brace/comment tweak is never blamed as bug-inducing)."""
    s = content.strip()
    if not s:
        return True
    if set(s) <= _PUNCT_ONLY:
        return True
    for prefix in _LINE_COMMENT.get(ext, ()):
        if s.startswith(prefix):
            return True
    # Block-comment markers (best effort, language-agnostic).
    return s.startswith(("/*", "*", "*/", '"""', "'''"))


def _parent(repo_dir: str, sha: str) -> str | None:
    try:
        out = _git(["rev-parse", "--verify", f"{sha}^"], cwd=repo_dir)
    except RuntimeError:
        return None  # root commit — nothing to blame against
    return out.strip() or None


def _deleted_line_ranges(
    repo_dir: str, parent: str, sha: str, file: str
) -> list[tuple[int, int]]:
    """Parent-side ``(start, count)`` ranges the fix deleted or modified."""
    out = _git(
        ["diff", "--unified=0", "--no-color", parent, sha, "--", file],
        cwd=repo_dir,
    )
    ranges: list[tuple[int, int]] = []
    for line in out.split("\n"):
        m = _HUNK_RE.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count > 0:
            ranges.append((start, count))
    return ranges


def _file_lines(repo_dir: str, parent: str, file: str) -> list[str]:
    try:
        out = _git(["show", f"{parent}:{file}"], cwd=repo_dir)
    except RuntimeError:
        return []
    return out.split("\n")


def _blame_range(
    repo_dir: str, parent: str, file: str, start: int, end: int
) -> list[tuple[int, str]]:
    """Return ``(final_lineno, blamed_sha)`` for parent lines ``start..end``."""
    try:
        out = _git(
            [
                "blame", "-w", "-C", "--line-porcelain",
                "-L", f"{start},{end}", parent, "--", file,
            ],
            cwd=repo_dir,
        )
    except RuntimeError:
        return []
    pairs: list[tuple[int, str]] = []
    for line in out.split("\n"):
        m = _SHA_LINE_RE.match(line)
        if m:
            pairs.append((int(m.group(3)), m.group(1)))
    return pairs


def _reachable_at_t0(repo_dir: str, t0_sha: str) -> set[str]:
    """Every commit present in the history at T0 (ancestors of, and including,
    the T0 commit). Used to keep only bug-inducing commits that predate T0."""
    out = _git(["rev-list", t0_sha], cwd=repo_dir)
    return {s for s in out.split("\n") if s}


def compute_szz(
    repo_dir: str,
    t0_sha: str,
    fixes: list[tuple[str, str]],
    *,
    source_root: str,
    extensions: tuple[str, ...],
    fix_sha_set: set[str] | None = None,
) -> dict:
    """Run SZZ over ``fixes`` (post-T0 fix commits). Returns both B-SZZ and
    AG-SZZ labels plus diagnostics. A label maps ``file_path -> #distinct fixes``
    whose bug-inducing commit (an ancestor of T0) touched that file."""
    fix_sha_set = fix_sha_set or {s for s, _ in fixes}
    reachable = _reachable_at_t0(repo_dir, t0_sha)

    # file -> set(fix_sha) that induced a (pre-T0) bug in it
    b_hits: dict[str, set[str]] = {}
    ag_hits: dict[str, set[str]] = {}
    n_fixes_attributed = 0
    bug_inducing_b: set[str] = set()
    bug_inducing_ag: set[str] = set()

    for fix_sha, _msg in fixes:
        parent = _parent(repo_dir, fix_sha)
        if parent is None:
            continue
        attributed = False
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
                    # B-SZZ: every blamed ancestor counts.
                    b_hits.setdefault(file, set()).add(fix_sha)
                    bug_inducing_b.add(blamed)
                    attributed = True
                    # AG-SZZ: skip cosmetic lines and fix-of-fix inducers.
                    content = (
                        parent_lines[lineno - 1] if 1 <= lineno <= n_parent else ""
                    )
                    if _is_cosmetic_line(content, ext):
                        continue
                    if blamed in fix_sha_set:
                        continue
                    ag_hits.setdefault(file, set()).add(fix_sha)
                    bug_inducing_ag.add(blamed)
        if attributed:
            n_fixes_attributed += 1

    def _counts(hits: dict[str, set[str]]) -> dict[str, int]:
        return {f: len(s) for f, s in sorted(hits.items())}

    return {
        "b_szz": _counts(b_hits),
        "ag_szz": _counts(ag_hits),
        "stats": {
            "n_fixes": len(fixes),
            "n_fixes_attributed": n_fixes_attributed,
            "n_bug_inducing_commits_b": len(bug_inducing_b),
            "n_bug_inducing_commits_ag": len(bug_inducing_ag),
            "n_defective_files_b": len(b_hits),
            "n_defective_files_ag": len(ag_hits),
        },
    }


def label_repo(
    repo_dir: str,
    t0_sha: str,
    fixes: list[tuple[str, str]],
    *,
    source_root: str,
    extensions: tuple[str, ...],
    cache_path: Path | None = None,
    fix_sha_set: set[str] | None = None,
) -> dict:
    """``compute_szz`` with a deterministic on-disk cache keyed by T0 + fix set."""
    cache_key = {
        "t0_sha": t0_sha,
        "fix_shas": sorted(s for s, _ in fixes),
        "source_root": source_root,
        "extensions": list(extensions),
    }
    if cache_path and cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached.get("_key") == cache_key:
            return cached
    result = compute_szz(
        repo_dir, t0_sha, fixes,
        source_root=source_root, extensions=extensions, fix_sha_set=fix_sha_set,
    )
    result["_key"] = cache_key
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2))
    return result


def labels_for_variant(szz_result: dict, variant: str = "ag") -> dict[str, int]:
    """Pick the B or AG label map from a ``compute_szz`` result."""
    return szz_result["ag_szz" if variant == "ag" else "b_szz"]
