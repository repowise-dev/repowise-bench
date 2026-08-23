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


def git_state(
    repo: Path | str, *, paths: list[str] | None = None, untracked: bool = True
) -> dict[str, Any]:
    """HEAD, branch and dirtiness for one checkout.

    `paths` narrows the dirty check to the subtree that actually affects the
    measurement. A benchmark run does not care that a README elsewhere in the
    monorepo is edited; it cares whether the ingestion code it just imported is
    the code at HEAD. Passing `paths=["packages/core"]` is the difference
    between a useful guard and one everybody learns to pass --allow-dirty to.
    """
    repo = Path(repo)
    status_args = ["status", "--porcelain"]
    # A tree can be legitimately ahead of its upstream and carry untracked
    # build output without being a tree nobody can reproduce. Only tracked
    # edits make an instrument unreconstructable, so a gate on the bench
    # tree drops `??` lines; the product gate keeps them.
    if not untracked:
        status_args.append("--untracked-files=no")
    # A scope that matches nothing makes this guard vacuous: `status -- packages`
    # run from a directory that has no `packages/` returns empty, which reads as
    # "clean" and stamps the result publishable without having checked anything.
    # That is exactly what happened -- `run_corpus.py` passed the *packages*
    # directory as the repo root, so every run it ever produced was gated on
    # `packages/packages`. Vacuity is not a pass, so a missing scope is reported.
    missing = [p for p in (paths or []) if not (repo / p).exists()]
    if paths:
        status_args += ["--", *paths]
    status = _git(repo, *status_args, keep_leading=True)
    return {
        "dirty_scope_missing": missing or None,
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
    publishable: bool | dict[str, bool],
    reasons: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The provenance block every result JSON carries at its top level.

    `publishable` may be a mapping from experiment id to a verdict. One document
    can cover two experiments where a caveat compromises only one of them -- a
    restored artifact voids a cost row and leaves coverage untouched -- and a
    single document-wide flag makes the sound half unciteable. Pass a mapping
    and each experiment carries its own verdict and its own reason.
    """
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
    # Every false verdict states its own reason. The previous rule suppressed
    # the default line whenever the document carried any caveat at all, so a
    # reader met `publishable: false` beside a caveat about something else
    # entirely and had no way to tell what the false referred to.
    default = (
        "run with --allow-dirty; the measured tree contains uncommitted "
        "changes, so this is a smoke test and not a result"
    )
    reasons = reasons or {}
    if isinstance(publishable, dict):
        why = {k: reasons.get(k, default) for k, ok in publishable.items() if not ok}
    else:
        why = {} if publishable else {experiment: reasons.get(experiment, default)}
    if why:
        block["not_publishable_because"] = why
    if extra:
        block.update(extra)
    return block


def is_publishable(block: dict[str, Any], experiment: str | None = None) -> bool:
    """Read a verdict that may be one flag or one per experiment.

    Documents written before the stamp was split carry a single bool, so both
    shapes have to be readable. A dict is truthy, so a reader that forgot this
    would call every split document publishable, which is the failure mode this
    exists to make impossible rather than merely unlikely.
    """
    pub = block.get("publishable")
    if not isinstance(pub, dict):
        return bool(pub)
    if experiment is not None:
        return bool(pub.get(experiment, False))
    return bool(pub) and all(pub.values())


def require_clean(
    repowise_repo: Path | str, *, bench_repo: Path | str, allow_dirty: bool
) -> bool:
    """Gate a run on a clean tree. Returns whether the result is publishable.

    Raises rather than warning. A warning printed to stderr during a long run
    is a warning nobody reads, and the whole point is that a dirty-tree number
    is indistinguishable from a good one once it is written down.

    Both trees are gated. This used to see only the product repository, so
    the measuring instrument itself -- the harness deciding what counts as
    an edge -- could carry arbitrary uncommitted edits and the result was
    still stamped publishable. `bench_repo` is required rather than optional
    because a gate a caller can forget to pass is the defect this closes.
    """
    state = git_state(repowise_repo, paths=["packages"])
    if state.get("dirty_scope_missing"):
        raise DirtyTreeError(
            f"{state['path']} contains no {state['dirty_scope_missing']}, so the "
            "dirty check matched no files and would have passed whatever the tree "
            "looked like. Pass the repository root, not a subdirectory of it. "
            "--allow-dirty does not suppress this: a guard that cannot fail is "
            "not a guard, and this one silently stamped every run publishable."
        )
    bench = git_state(bench_repo, untracked=False)
    if bench["dirty"]:
        if not allow_dirty:
            listed_b = "\n  ".join(bench["dirty_paths"][:10])
            raise DirtyTreeError(
                f"the bench tree {bench['path']} has uncommitted "
                f"changes:\n  {listed_b}\n\n"
                "The harness is the instrument. A result measured by an "
                "unreconstructable instrument cannot be reproduced from "
                "any commit, so it is not a result. Commit the harness, "
                "or pass --allow-dirty to run this as a smoke test."
            )
        return False

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
