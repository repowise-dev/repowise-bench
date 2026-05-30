"""Score files with the CodeScene CLI at a historical commit.

The head-to-head comparison needs a *second* per-file Code-Health score for the
exact same files Repowise scored at T0, so the two tools can be compared on one
corpus with one set of defect labels. CodeScene ships a CLI (``cs``) whose
``review --output-format json`` returns a 1-10 Code-Health ``score`` for a file,
and crucially accepts a ``<commit>:./path`` spec — so it scores the file *as of*
any commit, matching our leakage-free T0 methodology without a worktree.

Auth: the CLI requires a Personal Access Token in ``CS_ACCESS_TOKEN`` (a free
codescene.io account; no server needed). The binary path comes from ``CS_BIN``
(defaults to the stashed copy). Neither the token nor the 227 MB binary is ever
committed — both live outside the tracked tree.

Per-file scoring is cached to ``results/<repo>/codescene_scores.json`` (keyed by
repo-relative POSIX path) so re-runs are offline and resumable, exactly like the
defect-count caches. A ``null`` score means "no scorable code" (CodeScene found
nothing to score — e.g. a pure-declaration file); it is recorded as ``None`` and
excluded from the paired comparison, with the count reported.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_DEFAULT_CS_BIN = (
    Path(__file__).resolve().parents[3] / "local-stash" / "cs" / "cs.exe"
)


def cs_bin() -> str:
    """Resolve the CodeScene CLI binary (``CS_BIN`` env wins; else the stash)."""
    env = os.environ.get("CS_BIN")
    if env:
        return env
    return str(_DEFAULT_CS_BIN)


def cs_available() -> tuple[bool, str]:
    """(usable, reason) — checks the binary exists and a token is present."""
    if not Path(cs_bin()).exists():
        return False, f"cs binary not found at {cs_bin()} (set CS_BIN)"
    if not os.environ.get("CS_ACCESS_TOKEN"):
        return False, "CS_ACCESS_TOKEN not set"
    return True, "ok"


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # Skip the CLI's startup version-check network call — keeps batch runs fast
    # and offline-friendly once the binary is in place.
    env.setdefault("CS_DISABLE_VERSION_CHECK", "1")
    return env


def score_file_at_commit(
    repo_dir: str, sha: str, rel_path: str, *, timeout: int = 120
) -> float | None:
    """Code-Health score for ``rel_path`` as of ``sha`` (None = no scorable code).

    Raises ``RuntimeError`` on a token/licensing/transport failure so the caller
    can stop rather than silently cache a bad result.
    """
    spec = f"{sha}:./{rel_path}"
    result = subprocess.run(
        [cs_bin(), "review", "--output-format", "json", spec],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        env=_build_env(),
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    out = (result.stdout or "").strip()
    if result.returncode != 0 or not out.startswith("{"):
        msg = (result.stderr or out or "no output").strip()
        # The licensing failure is a hard stop, not a per-file miss.
        if "Personal Access Token" in msg or "CS_ACCESS_TOKEN" in msg:
            raise RuntimeError(f"CodeScene auth failed: {msg[:200]}")
        # A file that doesn't exist at that commit / unsupported language is a
        # legitimate per-file miss — surface as None, not a crash.
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    score = data.get("score")
    return float(score) if score is not None else None


def load_cache(cache_path: Path) -> dict[str, float | None]:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def score_repo(
    repo_dir: str,
    sha: str,
    rel_paths: list[str],
    cache_path: Path,
    *,
    flush_every: int = 25,
    timeout: int = 120,
    progress: bool = True,
) -> dict[str, float | None]:
    """Score every path in ``rel_paths`` at ``sha``, resuming from the cache.

    Only paths absent from the cache are scored; the cache is flushed every
    ``flush_every`` new scores so an interrupted run loses almost nothing.
    """
    cache = load_cache(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pending = [p for p in rel_paths if p not in cache]
    if progress:
        print(
            f"    codescene: {len(rel_paths)} files, "
            f"{len(cache)} cached, {len(pending)} to score"
        )
    new = 0
    for i, rel in enumerate(pending, 1):
        cache[rel] = score_file_at_commit(repo_dir, sha, rel, timeout=timeout)
        new += 1
        if new % flush_every == 0:
            cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))
            if progress:
                print(f"      scored {i}/{len(pending)} (flushed)")
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))
    return cache
