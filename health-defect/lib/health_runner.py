from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPOWISE_ROOT = Path(__file__).resolve().parents[3]
# Windows lays the venv interpreter under Scripts/python.exe; POSIX under bin/python.
_REPOWISE_PYTHON = (
    _REPOWISE_ROOT / ".venv" / "Scripts" / "python.exe"
    if (_REPOWISE_ROOT / ".venv" / "Scripts" / "python.exe").exists()
    else _REPOWISE_ROOT / ".venv" / "bin" / "python"
)
_REPOWISE_PKG_SRCS = [
    _REPOWISE_ROOT / "packages" / "cli" / "src",
    _REPOWISE_ROOT / "packages" / "core" / "src",
    _REPOWISE_ROOT / "packages" / "server" / "src",
]


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Indexing transient worktrees must NOT mutate the developer's global editor
    # config (the single global "repowise" MCP entry would otherwise be repointed
    # at a temp worktree that is then deleted). Honored by repowise>=this build.
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    # Anchor recency windows (90d/30d, churn, change-entropy, co-change decay) to
    # the repo's HEAD commit, not wall-clock now. Essential for T0 scoring: the
    # worktree's tip is ~6 months old, so a now()-anchored window is empty and
    # every windowed process biomarker silently never fires. Honored by
    # repowise>=this build (GitIndexer._resolve_as_of_ts).
    env["REPOWISE_GIT_WINDOW_ANCHOR"] = "head"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(p) for p in _REPOWISE_PKG_SRCS]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    return env


def _repowise_cmd() -> list[str]:
    repowise_bin = shutil.which("repowise")
    if repowise_bin:
        return [repowise_bin]
    if _REPOWISE_PYTHON.exists():
        return [str(_REPOWISE_PYTHON), "-c", "from repowise.cli.main import cli; cli()"]
    return [sys.executable, "-c", "from repowise.cli.main import cli; cli()"]


def run_health(repo_dir: str, timeout: int = 1800) -> dict:
    env = _build_env()
    result = subprocess.run(
        [*_repowise_cmd(), "health", "--format", "json"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"repowise health failed:\n{result.stderr}")
    return _extract_json(result.stdout)


def _extract_json(raw: str) -> dict:
    idx = raw.find('{\n  "kpis"')
    if idx == -1:
        idx = raw.find('{"kpis"')
    if idx == -1:
        raise ValueError("No JSON health output found in command output")
    return json.loads(raw[idx:])


def run_health_at_commit(
    repo_dir: str,
    sha: str,
    timeout: int = 1800,
    exclude_patterns: list[str] | None = None,
) -> dict:
    """Score a repo at a historical commit (the §7.2 T0-scoring fix).

    Adds a detached worktree at ``sha``, indexes it (``init --index-only``),
    runs ``health``, then removes the worktree. ``exclude_patterns`` are passed
    as repeatable ``-x`` gitignore-style patterns to skip docs/website/example
    trees during indexing (keeps the file walk — and the score — focused on
    source while cutting index time). Labels are therefore strictly *after*
    measurement: the worktree's history stops at T0.
    """
    worktree_path = os.path.join(tempfile.gettempdir(), f"repowise-bench-{sha[:12]}")
    # A stale worktree from an interrupted run would make `worktree add` fail.
    subprocess.run(
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=repo_dir, capture_output=True, text=True,
    )
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", worktree_path, sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        env = _build_env()
        exclude_args: list[str] = []
        for pat in exclude_patterns or []:
            exclude_args += ["-x", pat]
        result = subprocess.run(
            [*_repowise_cmd(), "init", "-y", "--index-only", *exclude_args],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            # Decline the post-commit-hook prompt non-interactively (the `-y`
            # flag covers config prompts; the hook prompt reads stdin).
            input="n\n",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"repowise init at {sha[:12]} failed:\n{result.stderr[-2000:]}"
            )
        return run_health(worktree_path, timeout=timeout)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
