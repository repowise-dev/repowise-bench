"""Stamp every result with the state of the thing that produced it.

There is one trap this module exists for. A precision audit's first pass once
measured another session's uncommitted work and read zod 15% high, and nothing
in the output said so. The number looked exactly like a good number. Whoever
read it next had no way to tell.

So: every run records the commit it ran at and whether the tree was dirty, and
`require_clean()` refuses to produce a publishable result from a dirty tree. A
smoke test passes `--allow-dirty` and its output is stamped `publishable: false`,
which is the honest label for a number taken over someone else's half-finished
change.
"""

from __future__ import annotations

import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DirtyTreeError(RuntimeError):
    """Raised when a publishable run is attempted over uncommitted changes."""


def _git(repo: Path, *args: str, keep_leading: bool = False) -> str | None:
    """Run a read-only git command. `keep_leading` matters more than it looks.

    `git status --porcelain` encodes the index and worktree states in columns 0
    and 1, so a worktree-modified file is " M path" with a leading space. A
    plain .strip() eats that space on the first line only, which silently turns
    one path in the list into "ackages/..." and leaves every other path intact.
    That is exactly the shape of bug nobody notices in a JSON blob.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.rstrip("\r\n") if keep_leading else out.stdout.strip()


def _package_version(repo: Path) -> str | None:
    """The version a reader would `pip install`, read from the measured tree.

    A SHA reconciles against our git history; nobody outside it can act on one.
    `describe` is close but a tag can be moved and points at the last release
    rather than at what is installed. Both go in the result, and a row is
    quoted as "repowise 0.43.0 (6540c8b6)" -- never either alone.
    """
    try:
        text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"""^version\s*=\s*['"]([^'"]+)['"]""", text, re.MULTILINE)
    return m.group(1) if m else None


def git_state(repo: Path | str, *, paths: list[str] | None = None) -> dict[str, Any]:
    """HEAD, branch and dirtiness for one checkout.

    `paths` narrows the dirty check to the subtree that actually affects the
    measurement. A benchmark run does not care that a README elsewhere in the
    monorepo is edited; it cares whether the ingestion code it just imported is
    the code at HEAD. Passing `paths=["packages/core"]` is the difference
    between a useful guard and one everybody learns to pass --allow-dirty to.
    """
    repo = Path(repo)
    status_args = ["status", "--porcelain"]
    if paths:
        status_args += ["--", *paths]
    status = _git(repo, *status_args, keep_leading=True)
    return {
        "path": str(repo),
        "head": _git(repo, "rev-parse", "HEAD"),
        "head_short": _git(repo, "rev-parse", "--short", "HEAD"),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "describe": _git(repo, "describe", "--tags", "--always"),
        "dirty": bool(status) if status is not None else None,
        "dirty_paths": [ln[3:] for ln in status.splitlines()] if status else [],
        "dirty_scope": paths,
    }


def tool_versions() -> dict[str, str | None]:
    """Versions of everything whose behaviour could move a number."""

    def _capture(cmd: str) -> str | None:
        # shell=True is required on Windows: `codegraph` is an npm bin, which
        # exists on PATH only as codegraph.cmd, and CreateProcess will not
        # resolve a .cmd from a bare name. Without it this silently returns
        # None and the result carries no record of which competitor build
        # produced the peer numbers.
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60, check=False, shell=True
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return out.stdout.strip().splitlines()[0]

    return {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "codegraph": _capture("codegraph --version"),
    }


def stamp(
    experiment: str,
    *,
    repowise_repo: Path | str,
    bench_repo: Path | str,
    publishable: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The provenance block every result JSON carries at its top level."""
    block: dict[str, Any] = {
        "experiment": experiment,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "publishable": publishable,
        "repowise": {
            **git_state(repowise_repo, paths=["packages"]),
            "version": _package_version(Path(repowise_repo)),
        },
        "bench": git_state(bench_repo),
        "tools": tool_versions(),
    }
    if not publishable:
        block["not_publishable_because"] = (
            "run with --allow-dirty; the measured tree contains uncommitted "
            "changes, so this is a smoke test and not a result"
        )
    if extra:
        block.update(extra)
    return block


def require_clean(repowise_repo: Path | str, *, allow_dirty: bool) -> bool:
    """Gate a run on a clean tree. Returns whether the result is publishable.

    Raises rather than warning. A warning printed to stderr during a long run
    is a warning nobody reads, and the whole point is that a dirty-tree number
    is indistinguishable from a good one once it is written down.
    """
    state = git_state(repowise_repo, paths=["packages"])
    if not state["dirty"]:
        return True
    if allow_dirty:
        return False
    listed = "\n  ".join(state["dirty_paths"][:10])
    more = "" if len(state["dirty_paths"]) <= 10 else f"\n  ... and {len(state['dirty_paths']) - 10} more"
    raise DirtyTreeError(
        f"{state['path']} has uncommitted changes under packages/:\n  {listed}{more}\n\n"
        "Measuring here would measure whatever is half-finished in that tree. Either commit, "
        "take a detached worktree at a named commit, or pass --allow-dirty to run this as a "
        "smoke test whose output is stamped publishable: false."
    )
