from __future__ import annotations

import re
import subprocess
from collections import defaultdict


def _git(args: list[str], cwd: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def resolve_t0_sha(repo_dir: str, t0_date: str) -> str:
    out = _git(
        ["log", "--format=%H", f"--before={t0_date}T23:59:59", "-1"],
        cwd=repo_dir,
    )
    sha = out.strip()
    if not sha:
        raise ValueError(f"No commit found before {t0_date}")
    return sha


def _touched_source_files(
    repo_dir: str,
    sha: str,
    source_root: str,
    extensions: tuple[str, ...] = (".py",),
) -> list[str]:
    out = _git(
        ["diff-tree", "--no-commit-id", "-r", "--name-only", sha],
        cwd=repo_dir,
    )
    files = []
    for f in out.strip().split("\n"):
        f = f.strip()
        if f and f.startswith(source_root) and any(f.endswith(e) for e in extensions):
            files.append(f)
    return files


def count_defects_gitmoji(
    repo_dir: str,
    t0_sha: str,
    t1_ref: str,
    source_root: str,
    emoji: str = "\U0001F41B",
    extensions: tuple[str, ...] = (".py",),
) -> dict[str, int]:
    log = _git(
        ["log", f"{t0_sha}..{t1_ref}", "--no-merges", "--format=%H %s"],
        cwd=repo_dir,
    )

    bug_shas = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        sha = line.split()[0]
        if emoji in line:
            bug_shas.append(sha)

    defect_counts: dict[str, int] = defaultdict(int)
    for sha in bug_shas:
        for f in _touched_source_files(repo_dir, sha, source_root, extensions):
            defect_counts[f] += 1

    return dict(defect_counts)


def count_defects_prefix(
    repo_dir: str,
    t0_sha: str,
    t1_ref: str,
    source_root: str,
    prefix: str = "Fixed #",
    extensions: tuple[str, ...] = (".py",),
) -> dict[str, int]:
    log = _git(
        ["log", f"{t0_sha}..{t1_ref}", "--no-merges", "--format=%H %s"],
        cwd=repo_dir,
    )

    bug_shas = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        parts = line.split(maxsplit=1)
        sha = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        if msg.startswith(prefix):
            bug_shas.append(sha)

    defect_counts: dict[str, int] = defaultdict(int)
    for sha in bug_shas:
        for f in _touched_source_files(repo_dir, sha, source_root, extensions):
            defect_counts[f] += 1

    return dict(defect_counts)


_DEFAULT_INCLUDE = [
    re.compile(r"\bfix\b", re.IGNORECASE),
    re.compile(r"\bbug\b", re.IGNORECASE),
    re.compile(r"\bpatch\b", re.IGNORECASE),
    re.compile(r"\bresolves?\b", re.IGNORECASE),
    re.compile(r"closes?\s+#\d+", re.IGNORECASE),
    re.compile(r"fixes?\s+#\d+", re.IGNORECASE),
]

_DEFAULT_EXCLUDE = [
    re.compile(r"^Merge ", re.IGNORECASE),
    re.compile(r"\btypo\b", re.IGNORECASE),
    re.compile(r"\bbump\b", re.IGNORECASE),
    re.compile(r"\bdeps?\b", re.IGNORECASE),
    re.compile(r"\bchore\b", re.IGNORECASE),
    re.compile(r"\blint\b", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bstyle\b", re.IGNORECASE),
    re.compile(r"\bdocs?\b", re.IGNORECASE),
]


def _compile_patterns(keywords: list[str]) -> list[re.Pattern]:
    return [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords]


def count_defects_keyword(
    repo_dir: str,
    t0_sha: str,
    t1_ref: str,
    source_root: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    extensions: tuple[str, ...] = (".py",),
) -> dict[str, int]:
    include_pats = _compile_patterns(include) if include else _DEFAULT_INCLUDE
    exclude_pats = _compile_patterns(exclude) if exclude else _DEFAULT_EXCLUDE

    log = _git(
        ["log", f"{t0_sha}..{t1_ref}", "--no-merges", "--format=%H %s"],
        cwd=repo_dir,
    )

    bug_shas = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        parts = line.split(maxsplit=1)
        sha = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        if any(p.search(msg) for p in exclude_pats):
            continue
        if any(p.search(msg) for p in include_pats):
            bug_shas.append(sha)

    defect_counts: dict[str, int] = defaultdict(int)
    for sha in bug_shas:
        for f in _touched_source_files(repo_dir, sha, source_root, extensions):
            defect_counts[f] += 1

    return dict(defect_counts)
