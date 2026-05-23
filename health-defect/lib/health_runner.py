from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPOWISE_ROOT = Path(__file__).resolve().parents[3]
_REPOWISE_PYTHON = _REPOWISE_ROOT / ".venv" / "bin" / "python"
_REPOWISE_PKG_SRCS = [
    _REPOWISE_ROOT / "packages" / "cli" / "src",
    _REPOWISE_ROOT / "packages" / "core" / "src",
    _REPOWISE_ROOT / "packages" / "server" / "src",
]


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
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


def run_health(repo_dir: str, timeout: int = 600) -> dict:
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


def run_health_at_commit(repo_dir: str, sha: str, timeout: int = 600) -> dict:
    worktree_path = os.path.join(tempfile.gettempdir(), f"repowise-bench-{sha[:12]}")
    try:
        subprocess.run(
            ["git", "worktree", "add", worktree_path, sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        env = _build_env()
        subprocess.run(
            [*_repowise_cmd(), "init", "-y", "--index-only"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=True,
        )
        return run_health(worktree_path, timeout=timeout)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
