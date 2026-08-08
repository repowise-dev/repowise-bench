"""
SWE-QA benchmark runner — production grade.

Handles:
- Per-repo folder structure (repos/<org>/<repo>/)
- Repowise indexing + per-repo MCP config generation
- Claude Code invocation with rate-limit retry & usage-cap backoff
- LLM-as-judge scoring
- Full metadata capture
"""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from harness.metrics import (
    RunMetrics, parse_claude_code_output, BudgetTracker,
    ResultWriter, RawOutputSaver,
)
from harness import arms as arm_registry
from harness.arms import Arm

# ---------------------------------------------------------------------------
# Local repowise checkout (no pip install — uses sibling source tree directly)
# ---------------------------------------------------------------------------
# Bench expects repowise cloned at ../repowise on branch feat/pipeline-overhaul.
# We invoke it as `python -m repowise.cli.main ...` with PYTHONPATH set to the
# three local package src dirs. The MCP server is launched the same way via
# `python -m repowise.cli.main mcp <repo> --transport stdio`.
# swe_qa_runner.py is at <repowise>/repowise-bench/harness/, so parents[2] IS
# the repowise checkout root. Override with REPOWISE_ROOT to benchmark a git
# worktree (or any other checkout) without touching the main clone.
_REPOWISE_ROOT = Path(
    os.environ.get("REPOWISE_ROOT") or Path(__file__).resolve().parents[2]
)
_REPOWISE_PKG_SRCS = [
    _REPOWISE_ROOT / "packages" / "cli" / "src",
    _REPOWISE_ROOT / "packages" / "core" / "src",
    _REPOWISE_ROOT / "packages" / "server" / "src",
]
# Provenance of the repowise checkout under test. Recorded, not gated.
#
# This used to warn when the checkout was not on `feat/pipeline-overhaul`, a
# branch that has not existed for a long time, so every run printed a warning
# nobody could act on and no run recorded what was actually measured. A branch
# name is not provenance anyway: branches move. The commit is what a result has
# to be stamped with, per standing rule 1.
REPOWISE_PROVENANCE: dict = {}


def _verify_local_repowise() -> dict:
    """Check the checkout exists and capture its exact commit for stamping."""
    if not _REPOWISE_ROOT.exists():
        raise RuntimeError(
            f"Local repowise checkout not found at {_REPOWISE_ROOT}. "
            f"Clone repowise into the parent directory of repowise-bench."
        )
    for src in _REPOWISE_PKG_SRCS:
        if not src.exists():
            raise RuntimeError(f"Expected repowise source dir missing: {src}")

    def _git(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(_REPOWISE_ROOT), *args], text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            return ""

    dirty = bool(_git("status", "--porcelain"))
    prov = {
        "repowise_root": str(_REPOWISE_ROOT),
        "repowise_commit": _git("rev-parse", "HEAD"),
        "repowise_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "repowise_describe": _git("describe", "--tags", "--always", "--dirty"),
        "repowise_dirty": dirty,
    }
    if dirty:
        # A dirty tree means the measured code is not any published commit, so
        # the result cannot be reproduced from the SHA alone. Say so once.
        print(
            f"  [warn] repowise checkout at {_REPOWISE_ROOT} has uncommitted "
            f"changes; results will not be reproducible from "
            f"{prov['repowise_commit'][:12]} alone"
        )
    return prov


REPOWISE_PROVENANCE = _verify_local_repowise()

# Force UTF-8 for all subprocesses (Windows cp1252 breaks on emoji/unicode)
# Also pin PYTHONPATH so repowise's three package src dirs resolve without pip.
_UTF8_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    # Modern Claude Code DEFERS MCP tool schemas (lazy-loaded via ToolSearch)
    # and connects MCP servers asynchronously. Give the repowise server ample
    # time to come up so its tools are resolvable on the agent's first turn
    # instead of racing the conversation start.
    "MCP_TIMEOUT": os.environ.get("MCP_TIMEOUT", "60000"),
    # Bill cache WRITES at the 5-minute rate, not the 1-hour rate.
    #
    # On a Claude subscription, Claude Code requests the 1-hour cache TTL
    # automatically, which bills cache writes at roughly 1.6x the 5-minute
    # rate in exchange for surviving long gaps. A benchmark cell is a fresh,
    # short, single-question session: it writes its prefix once, reads it a
    # handful of times within its own turns, and is never resumed. The extra
    # TTL buys nothing and the premium is paid on every cell.
    #
    # It is not a neutral premium either. Measured on the rung 6 pilot, cache
    # writes are 86-115% of the entire cost gap between the repowise arms and
    # the bare control, so the arm that writes most pays most for a TTL no arm
    # uses: -10.6% for c0-bare against -16.7% / -19.2% for the repowise arms.
    # Leaving it on charges our own arms a premium for a feature the benchmark
    # cannot use, and it flatters the arm with the smallest tool surface.
    #
    # Applies identically to every arm, and it is a price change rather than a
    # behaviour change: no token count moves, only what each token costs.
    "FORCE_PROMPT_CACHING_5M": os.environ.get("FORCE_PROMPT_CACHING_5M", "1"),
    "PYTHONPATH": os.pathsep.join(
        [str(p) for p in _REPOWISE_PKG_SRCS]
        + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
    ),
    # Put the venv's script dir (Scripts/ on Windows, bin/ on posix) on PATH
    # so the agent's Bash can resolve the `repowise` console script for
    # `repowise distill <cmd>` in the long (Bash-enabled) arm. Harmless for
    # the read-only arms.
    "PATH": os.pathsep.join(
        [str(Path(__file__).resolve().parents[1].parent / ".venv" / "Scripts"),
         str(Path(__file__).resolve().parents[1].parent / ".venv" / "bin")]
        + ([os.environ["PATH"]] if os.environ.get("PATH") else [])
    ),
}

# Provider credentials the MCP server needs in its OWN environment so that
# query-time embeddings (search_codebase) and answer synthesis (get_answer)
# work — Claude Code launches the server with the config's `env` block, so
# these must be forwarded explicitly rather than relying on inheritance.
_MCP_PASSTHROUGH_ENV = (
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
    # REPOWISE_PROVIDER must be forwarded explicitly: the server otherwise
    # falls back to the provider persisted in the index's state.json, and an
    # index built months ago under a different provider silently runs
    # get_answer in retrieval-only mode when that provider's key is absent
    # (observed: a gemini-built index + an openai-only env disabled synthesis
    # for an entire benchmark arm without a single error).
    "REPOWISE_PROVIDER", "REPOWISE_EMBEDDER", "REPOWISE_DOC_MODEL",
)

# Argv prefix for invoking the local repowise CLI.
# `python -m repowise.cli.main` is a NO-OP on branches where main.py has no
# __main__ guard (it builds the click group but never invokes it), so the
# console script is REQUIRED — falling back to -m starts a server that serves
# nothing and the arm silently degrades to bare. Both venv layouts are
# checked (Scripts/ on Windows, bin/ on posix); the Windows-only lookup
# previously sent every macOS run down the no-op path. Override with
# REPOWISE_EXE.
def _resolve_repowise_cmd() -> list:
    env_exe = os.environ.get("REPOWISE_EXE")
    if env_exe and Path(env_exe).exists():
        return [env_exe]
    venv = Path(__file__).resolve().parents[1].parent / ".venv"
    for candidate in (venv / "Scripts" / "repowise.exe", venv / "bin" / "repowise"):
        if candidate.exists():
            return [str(candidate)]
    raise RuntimeError(
        f"No repowise console script under {venv} (checked Scripts/ and bin/). "
        "Set REPOWISE_EXE. Refusing the `python -m repowise.cli.main` fallback: "
        "it is a no-op and silently degrades repowise arms to bare agents."
    )


_REPOWISE_CMD = _resolve_repowise_cmd()

# ---------------------------------------------------------------------------
# SWE-QA repo name mapping (split name -> GitHub org/repo)
# ---------------------------------------------------------------------------

def _load_repo_map() -> dict:
    """split name -> GitHub org/repo, from `configs/repos.yaml` when present.

    Declared in a file so adding a repo to the bake-off needs no Python change.
    The literal below is the fallback and the record of what the published runs
    used, so a deleted or malformed registry degrades to the old behaviour
    rather than to an empty map.
    """
    path = Path(__file__).resolve().parents[1] / "configs" / "repos.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {
        name: spec["repo"]
        for name, spec in (doc.get("repos") or {}).items()
        if isinstance(spec, dict) and spec.get("repo")
    }


SWEQA_REPO_MAP = {
    "astropy": "astropy/astropy",
    "conan": "conan-io/conan",
    "django": "django/django",
    "flask": "pallets/flask",
    "matplotlib": "matplotlib/matplotlib",
    "pylint": "pylint-dev/pylint",
    "pytest": "pytest-dev/pytest",
    "reflex": "reflex-dev/reflex",
    "requests": "psf/requests",
    "scikit_learn": "scikit-learn/scikit-learn",
    "sphinx": "sphinx-doc/sphinx",
    "sqlfluff": "sqlfluff/sqlfluff",
    "streamlink": "streamlink/streamlink",
    "sympy": "sympy/sympy",
    "xarray": "pydata/xarray",
}
SWEQA_REPO_MAP.update(_load_repo_map())

# Reverse map for lookup
REPO_TO_SPLIT = {v: k for k, v in SWEQA_REPO_MAP.items()}


# ---------------------------------------------------------------------------
# Rate-limit / usage-cap detection
# ---------------------------------------------------------------------------

RATE_LIMIT_PATTERNS = [
    r"rate.?limit",
    r"too many requests",
    r"429",
    r"overloaded",
    r"capacity",
    r"usage.?limit",
    r"exceeded.*quota",
    r"throttl",
    r"billing",
    r"try again",
    r"resource_exhausted",
]

_rl_regex = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


def is_rate_limit_error(error_text: str) -> bool:
    """Check if an error looks like a rate-limit or usage-cap."""
    return bool(_rl_regex.search(error_text))


def backoff_sleep(attempt: int, base: float = 30.0, max_wait: float = 900.0):
    """Exponential backoff: 30s, 60s, 120s, 240s, 480s, capped at 15 min."""
    wait = min(base * (2 ** attempt), max_wait)
    now = datetime.now().strftime("%H:%M:%S")
    print(f"    [{now}] Rate limited — waiting {wait:.0f}s (attempt {attempt + 1})...")
    time.sleep(wait)


def _extract_failure_reason(stdout: str, stderr: str) -> str:
    """Extract a meaningful failure reason from claude's output streams.

    When claude exits non-zero, stderr is often empty because the failure
    happened mid-stream (e.g. rate limit retries exhausted). The diagnostic
    detail lives in the stream-json events on stdout. We walk those events
    in reverse, looking for the most-recent error indicator:

      1. {"type":"system","subtype":"api_retry","error":"rate_limit",
         "error_status":529,"attempt":N}
         → "rate_limit 529 (N retries exhausted)"
      2. {"type":"system","subtype":"error","error":"..."}
         → "system error: <text>"
      3. {"type":"result","is_error":true,"result":"..."}
         → "result error: <text>"

    Falls back to stderr (truncated) when no stream events match. Always
    returns a non-empty string so the swe_qa.jsonl row carries actionable
    debug info instead of an empty `error` field.
    """
    if stdout:
        last_retry: dict | None = None
        max_attempt = 0
        for raw in stdout.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            sub = obj.get("subtype")
            if t == "system" and sub == "api_retry":
                # Track the highest-numbered retry — that's the one that
                # exhausted the budget.
                attempt_n = int(obj.get("attempt", 0))
                if attempt_n >= max_attempt:
                    max_attempt = attempt_n
                    last_retry = obj
            elif t == "system" and sub == "error":
                msg = obj.get("error") or obj.get("message") or "unknown"
                return f"system error: {str(msg)[:300]}"
            elif t == "result" and obj.get("is_error"):
                msg = obj.get("result") or obj.get("error") or "unknown"
                return f"result error: {str(msg)[:300]}"
        if last_retry is not None:
            err_kind = last_retry.get("error", "unknown")
            err_status = last_retry.get("error_status", "?")
            attempt_n = last_retry.get("attempt", "?")
            max_retries = last_retry.get("max_retries", "?")
            return (
                f"{err_kind} {err_status} "
                f"({attempt_n}/{max_retries} retries exhausted)"
            )
    if stderr and stderr.strip():
        return stderr.strip()[:500]
    return "claude exited non-zero with no diagnostic output"


# ---------------------------------------------------------------------------
# Repo helpers
# ---------------------------------------------------------------------------

def resolve_repo_path(repo_name: str, repos_dir: str) -> Path:
    """repos/<org>/<repo>/"""
    parts = repo_name.split("/")
    if len(parts) == 2:
        return Path(repos_dir) / parts[0] / parts[1]
    return Path(repos_dir) / parts[-1]


def ensure_repo_cloned(repo_name: str, repos_dir: str) -> Path:
    repo_path = resolve_repo_path(repo_name, repos_dir)
    if repo_path.exists() and (repo_path / ".git").exists():
        return repo_path
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{repo_name}.git"
    print(f"  Cloning {repo_name}...")
    subprocess.run(
        ["git", "clone", "--depth", "200", clone_url, str(repo_path)],
        check=True, capture_output=True, text=True, timeout=600,
        env=_UTF8_ENV, encoding="utf-8", errors="replace"
    )
    return repo_path


def get_repo_commit(repo_path: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path), capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()[:12]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_swe_qa_tasks(data_dir: str, max_tasks: Optional[int] = None,
                       repos: Optional[list] = None,
                       skip_tasks: int = 0,
                       exclude_indices: Optional[list] = None,
                       include_indices: Optional[list] = None,
                       task_ids: Optional[list] = None,
                       tasks_file=None) -> list:
    """
    Load SWE-QA tasks from HuggingFace-downloaded JSON or directly from HF.

    Each task gets: id, repo (GitHub org/name), question, answer, split_name.

    ``tasks_file`` (str or list of str, relative paths resolved against the
    bench root) bypasses the swe_qa dataset convention and loads explicit
    frozen question files, concatenated in order.

    `task_ids` selects by NAME rather than by position, and it exists because
    every Layer B run so far selected by position and that turned out not to be
    a sample. The django rows are laid out by interrogative — What, then How,
    then Why, then Where — so `max_tasks: 10` in file order is the `What` block
    and nothing else. A stratified draw has to name its questions; see
    `harness/question_shapes.py`, which commits the classification and the
    seed. Applied before skip/max so a named draw is never silently truncated
    by a stale `max_tasks` left in a config.
    """
    if tasks_file:
        files = tasks_file if isinstance(tasks_file, list) else [tasks_file]
        tasks = []
        for f in files:
            p = Path(f).expanduser()
            if not p.is_absolute():
                p = _BENCH_ROOT / p
            tasks.extend(json.loads(p.read_text(encoding="utf-8")))
    else:
        tasks = _load_swe_qa_dataset(data_dir)

    # Filter by repo
    if repos:
        tasks = [t for t in tasks if t.get("repo", "") in repos]

    # Select by task id. Wins over every positional selector below it, and
    # raises on an id that does not exist rather than quietly running a
    # smaller set than the draw specified — a stratified draw missing a slice
    # is not a stratified draw, and n going from 15 to 14 is not visible in any
    # summary line.
    if task_ids:
        wanted = list(dict.fromkeys(task_ids))
        found = {t.get("id"): t for t in tasks}
        missing = [tid for tid in wanted if tid not in found]
        if missing:
            raise KeyError(
                f"task_ids not present in the loaded set: {missing}. "
                f"Check the repo filter: {repos}."
            )
        return [found[tid] for tid in wanted]

    # Include specific per-repo indices (computed AFTER repo filter). Used by
    # targeted re-runs (e.g., re-running only the failing tasks after a fix).
    # Mutually informative with exclude_indices; if both set, include wins.
    if include_indices:
        incl = set(include_indices)
        tasks = [t for i, t in enumerate(tasks) if i in incl]
    # Exclude specific per-repo indices (computed AFTER repo filter, BEFORE
    # skip/max). Lets a re-run skip a subset of tasks that were already
    # completed in a prior run.
    elif exclude_indices:
        excl = set(exclude_indices)
        tasks = [t for i, t in enumerate(tasks) if i not in excl]
    # Skip + limit
    if skip_tasks:
        tasks = tasks[skip_tasks:]
    if max_tasks and max_tasks < len(tasks):
        tasks = tasks[:max_tasks]

    return tasks


def _load_swe_qa_dataset(data_dir: str) -> list:
    data_path = Path(data_dir) / "swe_qa"

    # Check for pre-downloaded data
    local_file = data_path / "tasks.json"
    if local_file.exists():
        with open(local_file, encoding="utf-8") as f:
            tasks = json.load(f)
    else:
        # Also try test.json (from earlier mini datasets)
        for fname in ["test.json", "data.json"]:
            fp = data_path / fname
            if fp.exists():
                with open(fp, encoding="utf-8") as f:
                    tasks = json.load(f)
                break
        else:
            raise FileNotFoundError(
                f"No SWE-QA data in {data_path}. "
                "Run: python scripts/download_benchmarks.py --benchmark swe_qa"
            )
    return tasks


# ---------------------------------------------------------------------------
# Repowise indexing + MCP config
# ---------------------------------------------------------------------------

def generate_mcp_config(
    repo_path: Path, bench_root: Path, tools: Optional[str] = None
) -> Path:
    """Write per-repo MCP config JSON. Returns absolute path.

    ``tools`` is passed straight through to ``repowise mcp --tools``: a
    comma-separated allowlist, or the literal ``"lean"`` for the shipped
    six-tool profile, so unused tool schemas never enter the agent's context.
    ``None`` advertises the default full surface. The tool
    surface is baked into the config filename so full and lean arms get
    distinct server launches against the same restored index.

    **This used to emit ``--profile``, which the CLI does not accept**
    (`packages/cli/src/repowise/cli/commands/mcp_cmd.py:86` defines `--tools`
    and nothing else). Click rejected the unknown option, the server exited
    before the handshake, and the lean arm silently degraded to a bare agent
    that scored as if the MCP surface were free. Every lean result predating
    this fix, including `BENCHMARK_REPORT_FLASK_V3.md`, is void.
    """
    config_dir = bench_root / "mcp_configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    repo_abs = str(repo_path.resolve()).replace("\\", "/")
    suffix = ("_" + re.sub(r"[^A-Za-z0-9]+", "-", tools)[:40]) if tools else ""
    config_name = f"{repo_path.parent.name}_{repo_path.name}{suffix}.json"
    config_path = config_dir / config_name

    server_args = _REPOWISE_CMD[1:] + ["mcp", repo_abs, "--transport", "stdio"]
    if tools:
        # Current CLI spells profiles through --tools (e.g. --tools lean);
        # the old --profile flag no longer exists and would kill the server
        # at mount, silently degrading the arm to a bare agent.
        server_args += ["--tools", tools]

    # The server's own env: PYTHONPATH for the local checkout, UTF-8, plus any
    # provider credentials present so embeddings + get_answer synthesis work
    # (Claude Code launches the server with exactly this env block).
    server_env = {
        "PYTHONPATH": _UTF8_ENV["PYTHONPATH"],
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    for key in _MCP_PASSTHROUGH_ENV:
        val = os.environ.get(key)
        if val:
            server_env[key] = val

    # NOTE: `python -m repowise.cli.main` is a no-op (main.py has no __main__
    # guard), so the server must be launched through the resolved CLI command —
    # the console-script exe, or REPOWISE_EXE. PYTHONPATH still points at the
    # local checkout's src dirs, which shadow the venv's editable install.
    mcp_config = {
        "mcpServers": {
            "repowise": {
                "command": _REPOWISE_CMD[0],
                "args": server_args,
                "env": server_env,
                # Load tool schemas eagerly. Current Claude Code defers every
                # MCP tool behind ToolSearch by default, and agents answer
                # from Read/Grep without ever loading a deferred schema —
                # observed as zero MCP adoption across all benchmark arms.
                # Eager schemas also match the regime under which all prior
                # published numbers were measured (the schema tax is part of
                # what the lean profile exists to reduce).
                "alwaysLoad": True,
            }
        }
    }
    with open(config_path, "w") as f:
        json.dump(mcp_config, f, indent=2)
    return config_path.resolve()


def _safe_rmtree(path: Path, retries: int = 3) -> None:
    """Robust rmtree — Windows sometimes holds file handles briefly after a crash."""
    for i in range(retries):
        if not path.exists():
            return
        try:
            shutil.rmtree(str(path))
            return
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(0.5 * (i + 1))


def _restore_index_from_cache(cached_idx: Path, dest_idx: Path) -> None:
    """Restore a cached .repowise/ tree into the repo, robust to Windows path collisions."""
    _safe_rmtree(dest_idx)
    # On Windows, copytree fails with WinError 183 if dest re-appears between
    # rmtree and copytree (rare but observed). Retry once.
    for attempt in range(2):
        try:
            shutil.copytree(str(cached_idx), str(dest_idx))
            return
        except FileExistsError:
            _safe_rmtree(dest_idx)
            if attempt == 1:
                raise


_BENCH_ROOT = Path(__file__).resolve().parents[1]
_C0_WORKTREES_ROOT = _BENCH_ROOT / "scratch_c0"


def get_c0_worktree(repo_path: Path, variant: str = "") -> Path:
    """Return a git worktree path for runs that must not see tool artifacts.

    Git worktrees share the repo's object store (fast, no full copy) but have
    their own working directory — untracked files like `.repowise/` are NOT
    present.  This means an agent running in cwd=worktree physically cannot
    access artifacts left in the source checkout by any tool's indexing
    (.repowise/, .serena/, .codegraph/, CLAUDE.md, .mcp.json).

    ``variant`` gives an arm its own worktree, so arm-specific files injected
    into one arm's working directory (e.g. a packed-repo file) can never be
    seen by another arm running concurrently against the same repo.

    The worktree is created once per (repo, variant) and reused across tasks.
    If the repo's HEAD has moved (e.g. after a `git pull`), the existing
    worktree is torn down and recreated.
    """
    org = repo_path.parent.name
    name = f"{repo_path.name}__{variant}" if variant else repo_path.name
    wt_path = _C0_WORKTREES_ROOT / org / name

    # Parallel workers race on create/remove of the same worktree (git
    # worktree add fails with 128 when another worker is mid-prune) —
    # serialize the check-and-create.
    with _WORKTREE_LOCK:
        return _ensure_worktree(repo_path, wt_path)


_WORKTREE_LOCK = threading.Lock()


def _ensure_worktree(repo_path: Path, wt_path: Path) -> Path:
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if the worktree is healthy and on the same HEAD as the source repo.
    needs_create = True
    if wt_path.exists():
        try:
            src_head = subprocess.check_output(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            wt_head = subprocess.check_output(
                ["git", "-C", str(wt_path), "rev-parse", "HEAD"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            needs_create = (src_head != wt_head)
        except Exception:
            needs_create = True

    if needs_create:
        # Remove any stale worktree entry first — git tracks worktrees
        # independently of the filesystem, so a dir that was `rm -rf`'d
        # still appears as "prunable" and blocks `worktree add`.
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(wt_path)],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "prune"],
            capture_output=True,
        )
        if wt_path.exists():
            _safe_rmtree(wt_path)
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "add", "--detach", str(wt_path), "HEAD"],
            check=True, capture_output=True,
        )

    # Belt-and-braces: a worktree shouldn't have these untracked artifacts,
    # but if anything ever leaks in (e.g. a stray CLAUDE.md write), kill it
    # before handing the path to the agent.
    for leak in (".repowise", ".mcp.json", "CLAUDE.md", ".serena", ".codegraph",
                 ".claude"):
        p = wt_path / leak
        if p.exists():
            if p.is_dir():
                _safe_rmtree(p)
            else:
                p.unlink()

    return wt_path


def index_repo(repo_name: str, repos_dir: str, index_dir: str,
               mode: str, repowise_bin: str, doc_model: str,
               provider: Optional[str] = None,
               embedder: Optional[str] = None) -> tuple:
    """Run repowise init from the local checkout. Returns (success, time_seconds).

    Uses --resume so a previous partial run continues instead of restarting.
    Caps git history at 200 commits and LLM concurrency at 3 (full mode only).
    """
    del repowise_bin  # ignored — we always use the local checkout via _REPOWISE_CMD
    return index_repo_at(
        resolve_repo_path(repo_name, repos_dir), repo_name, index_dir, mode,
        doc_model, provider=provider, embedder=embedder,
    )


def index_repo_at(repo_path: Path, repo_name: str, index_dir: str, mode: str,
                  doc_model: str, provider: Optional[str] = None,
                  embedder: Optional[str] = None) -> tuple:
    """`index_repo` against an explicit checkout, so an arm can index its own
    worktree instead of the shared `repos/<org>/<repo>` every arm sees.

    Same command, same flags, same cache semantics — only the path moves. The
    cache key carries the tree name so two arms sharing a repo but not a tree
    do not restore each other's index over their own.
    """
    cache_key = f"{repo_name.replace('/', '_')}_{repo_path.name}_{mode}"
    cache_dir = Path(index_dir) / cache_key

    # Restore from cache (mode-specific). Idempotent: if the repo already has a
    # restored wiki.db, SKIP the rmtree+copytree. A long-lived MCP server from a
    # prior C2 task in the same repo holds wiki.db / lancedb open, so re-wiping
    # .repowise under it fails with WinError 32 (file in use). The cache content
    # is identical once restored, so skipping is correct, not just a workaround.
    if cache_dir.exists():
        cached_idx = cache_dir / ".repowise"
        dest_idx = repo_path / ".repowise"
        if cached_idx.exists() and not (dest_idx / "wiki.db").exists():
            _restore_index_from_cache(cached_idx, dest_idx)
        return True, 0.0

    start = time.time()
    cmd = list(_REPOWISE_CMD) + [
        "init", "-y",
        "--resume",                  # pick up partial pipeline-overhaul checkpoints
        "--commit-limit", "200",     # 500 default → 200 keeps ~85% of git signal, much faster
    ]
    if mode == "index-only":
        cmd.append("--index-only")
    else:
        # Cap LLM concurrency to avoid rate-limit thrash and improve prompt-cache reuse.
        cmd.extend(["--concurrency", "3"])
        # `init` does NOT read REPOWISE_DOC_MODEL — provider/model must be
        # passed explicitly or it falls back to API-key autodetection.
        if provider:
            cmd.extend(["--provider", provider])
        if doc_model:
            cmd.extend(["--model", doc_model])
        if embedder:
            cmd.extend(["--embedder", embedder])

    # Force DB to repo-local .repowise/wiki.db so the MCP server can find it.
    # NOTE: this places repowise artifacts inside the working tree, so the
    # bench MUST call cleanup_repowise_dir() before launching any C0 agent.
    rw_dir = repo_path.resolve() / ".repowise"
    rw_dir.mkdir(parents=True, exist_ok=True)
    local_db = (rw_dir / "wiki.db").as_posix()
    env = {
        **_UTF8_ENV,
        "REPOWISE_DOC_MODEL": doc_model,
        "REPOWISE_DB_URL": f"sqlite+aiosqlite:///{local_db}",
    }

    # Full mode on large repos (django, sympy, astropy) needs serious time for
    # LLM doc generation. With --resume a timeout is recoverable on the next run.
    index_timeout = 5400 if mode == "full" else 1200  # 90 min full, 20 min index-only
    print(f"  Indexing {repo_name} (mode={mode}) via local repowise checkout...")
    result = subprocess.run(
        cmd, cwd=str(repo_path), capture_output=True, text=True,
        env=env, timeout=index_timeout, encoding="utf-8", errors="replace"
    )
    elapsed = time.time() - start

    if result.returncode == 0:
        cache_dir.mkdir(parents=True, exist_ok=True)
        src = repo_path / ".repowise"
        if src.exists():
            dest = cache_dir / ".repowise"
            _safe_rmtree(dest)
            shutil.copytree(str(src), str(dest))
        print(f"  Indexed {repo_name} in {elapsed:.0f}s")
        return True, elapsed
    else:
        print(f"  Indexing failed for {repo_name}: {result.stderr[:300]}")
        return False, elapsed


# ---------------------------------------------------------------------------
# Claude Code invocation with retry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# System prompts — tuned per (benchmark x mode) to avoid wasted tool calls.
#
# Principles (v2 — learned from SWE-QA overnight run):
#   - C1 index-only: get_overview returns empty, search_codebase is blocked
#     → make tools supplementary, not mandatory-first
#     → focus on get_risk + get_context (the tools that return real data)
#     → explicitly list ONLY available tools to prevent denied-call waste
#   - C2 full: overview + search_codebase are populated
#     → can lead with search_codebase for navigation
#     → get_context returns rich docs, get_overview returns real summary
#     → still skip dead_code/architecture_diagram (irrelevant for Q&A/bugfix)
# ---------------------------------------------------------------------------

# -- SWE-bench (bug fixing) --

SWEBENCH_PROMPT_INDEX_ONLY = """You have access to repowise codebase intelligence tools alongside standard tools.
Use them to LOCATE the suspect file(s) and find SILENT co-change partners that likely
need parallel edits — that's where graph + git intelligence pays off on bug fixes.

Available repowise tools (use ONLY these — no others exist):
- mcp__repowise__get_context(targets=["a.py","b.py"]) — for each file: symbols, imports,
  dependents. Batch multiple files in ONE call.
- mcp__repowise__get_risk(targets=["a.py","b.py"]) — top-5 co-change partners, hotspot
  score, ownership. Co-change partners are the files that have historically been edited
  together with the target — pay attention to ones with has_import_link=false (silent
  couplings the import graph misses).
- mcp__repowise__get_dependency_path(source="a.py", target="b.py") — import chain.
- mcp__repowise__get_why(query="path/to/file.py") — past significant commits.

Strict workflow:
  1. Read the issue, Glob/Grep to find the most likely file.
  2. ONE batched get_risk call on that file → note co-change partners.
  3. ONE batched get_context call covering the target file + its top co-change partners.
  4. Read code, make the minimal fix, edit each file the bug actually touches.

Budget: 2 repowise calls total. Never call get_overview, search_codebase, dead_code,
or architecture_diagram in this mode.
"""

SWEBENCH_PROMPT_FULL = """You have access to repowise codebase intelligence tools alongside standard tools.
Repowise gives you LLM-generated wiki docs, semantic search, graph + git intelligence.
TRUST the doc layer — it usually localises the bug faster than reading source from scratch.

Available repowise tools:
- mcp__repowise__search_codebase(query="...") — semantic search. ALWAYS the first call.
  Use the issue title/description as the query.
- mcp__repowise__get_context(targets=["a.py","b.py"]) — wiki docs + symbols + dependents.
  Batch the top 2–4 search results in ONE call.
- mcp__repowise__get_risk(targets=["a.py"]) — co-change partners (top-5). Use to find
  silent couplings the bug fix may also need to touch.
- mcp__repowise__get_why(query="path/to/file.py") — design rationale from past commits.
  Use only when the wiki doc doesn't explain WHY the existing logic is the way it is.
- mcp__repowise__get_dependency_path(source="a.py", target="b.py") — import chain.
- mcp__repowise__get_overview() — at most ONCE, only if you need overall architecture.

Strict workflow:
  1. ONE search_codebase call.
  2. ONE batched get_context on the top results.
  3. ONE get_risk on the file you intend to edit, and patch its co-change partners too if
     they look causally related.
  4. Read code, make the minimal fix, edit all files the bug actually touches.

Budget: 3 repowise calls total. Never call dead_code or architecture_diagram.
"""

# -- SWE-QA (code understanding) --

SWEQA_PROMPT_INDEX_ONLY = """You have access to repowise codebase intelligence tools alongside standard tools.
Use them for structural and git context when answering the question.

Available repowise tools (use ONLY these — no others exist):
- mcp__repowise__get_context(targets=["path/a.py","path/b.py"]) — for each file: symbols
  (top-level functions/classes), imports, and dependents (who imports it). BATCH multiple
  files in ONE call. This is your primary navigation tool.
- mcp__repowise__get_risk(targets=["path/to/file.py"]) — top-5 co-change partners, ownership,
  hotspot score, churn. Use ONLY when the question is about history/coupling/ownership.
- mcp__repowise__get_dependency_path(source="a.py", target="b.py") — import chain. Use ONLY
  when the question is literally "how does X depend on Y".
- mcp__repowise__get_why(query="path/to/file.py") — past significant commits. Use ONLY for
  "why was this changed" questions.

Strict workflow (do not deviate):
  1. Glob/Grep to find 1–3 candidate files (one search, not many).
  2. ONE batched get_context call on all candidates at once.
  3. Read the relevant code from those files.
  4. Answer.

Budget: at most ONE repowise call for most questions, TWO if the first surfaces a new file
worth inspecting. Never call get_overview, search_codebase, dead_code, or architecture_diagram
— they are not available in this mode and waste a turn.
"""

SWEQA_PROMPT_FULL = """You have repowise MCP tools. They are accurate. Trust them.

These tools are loaded on demand: if a repowise tool is not immediately
available, call ToolSearch with the tool name first (e.g. ToolSearch
"mcp__repowise__get_answer") to load it, then call it. Do this silently.

STRICT WORKFLOW:
  1. mcp__repowise__get_answer(question) — ALWAYS your first call.
  2. If response.confidence == "high" AND response.answer names concrete file paths
     or symbol names (not phrases like "the provided excerpts do not contain", "you
     should inspect", "consult the source"): CITE THE ANSWER DIRECTLY. Do NOT call
     Grep, Read, get_context, or get_symbol to verify. Emit your final answer.
  3. If the question names a specific class/function/method, call
     mcp__repowise__get_symbol(symbol_id="path::Name") for it. Trust the returned
     source body. Do NOT re-Read the file.
  4. ONLY fall back to Grep/Read/get_context/search_codebase if (a) get_answer was
     confidence=="low", (b) get_answer's text was hedged/vague, or (c) get_symbol
     returned not-found.

BUDGET: 1–2 MCP calls + 0 verification reads on clean high-confidence answers.
        4 calls maximum on hard questions. Never call dead_code or architecture_diagram.

When the tool reports high confidence, verification reads are wasted cost: the
confidence signal is the gate. Trust it on high; verify on low.
"""

# -- Tool lists per mode --

# C1 index-only: graph + git layer only. No wiki docs, no semantic search.
# These four are the only tools that return real data in this mode.
TOOLS_INDEX_ONLY = (
    ",mcp__repowise__get_context"
    ",mcp__repowise__get_risk"
    ",mcp__repowise__get_why"
    ",mcp__repowise__get_dependency_path"
)

# C2 full: all useful tools including semantic search, wiki docs, get_answer, get_symbol.
TOOLS_FULL = (
    ",mcp__repowise__get_answer"
    ",mcp__repowise__get_symbol"
    ",mcp__repowise__search_codebase"
    ",mcp__repowise__get_context"
    ",mcp__repowise__get_risk"
    ",mcp__repowise__get_why"
    ",mcp__repowise__get_dependency_path"
    ",mcp__repowise__get_overview"
)

# -- Server-side allowlists (passed verbatim to `repowise mcp --tools`) --
#
# BOTH arms pin their served surface explicitly. Relying on the server default
# was not deterministic:
#   1. **Workspace contamination.** `find_workspace_root` walks up from the repo
#      path (`workspace/config.py:360-373`), so a `.repowise-workspace.yaml`
#      anywhere above the bench checkout flips every server into workspace mode
#      and adds tools. Measured 2026-08-01: the flask arm served **13** tools,
#      including `list_repos`, because the developer's own workspace file sits at
#      the repowise checkout root. That is environment-dependent, so it would not
#      reproduce on a clean clone, which is the worst property a benchmark arm
#      can have.
#   2. **Version drift.** The default set has changed across releases. A pinned
#      allowlist means an arm measured today is the same arm a year from now.
#   3. **Client/server disagreement.** TOOLS_FULL below allowlists
#      `get_dependency_path`, which is opt-in and is NOT in the server's default
#      set, so the full arm was allowlisting a tool the server never advertised.
SERVED_TOOLS_FULL = (
    "get_answer,get_symbol,search_codebase,get_context,"
    "get_risk,get_why,get_dependency_path,get_overview"
)

# C1 index-only: graph + git layer only, no wiki and no semantic search. These
# four are the only tools that return real data in that mode.
SERVED_TOOLS_INDEX_ONLY = "get_context,get_risk,get_why,get_dependency_path"

# The server-side allowlist for the lean arm, passed verbatim to
# `repowise mcp --tools`. These are the four tools the agent actually reached
# for on flask48 v2, so ONLY these four schemas are advertised and the per-task
# schema "tax" the other five would add never enters the agent's context. Same
# index as full; only the served surface differs.
#
# Deliberately an explicit allowlist rather than the CLI's `lean` keyword: that
# keyword selects the shipped SIX-tool profile, which is a different arm. If you
# want the shipped profile, pass the string "lean" and rename the arm.
SERVED_TOOLS_LEAN = "get_answer,get_symbol,search_codebase,get_context"

# Client-side allowlist for the same four (Claude Code `--allowedTools` form).
TOOLS_LEAN = (
    ",mcp__repowise__get_answer"
    ",mcp__repowise__get_symbol"
    ",mcp__repowise__search_codebase"
    ",mcp__repowise__get_context"
    ",mcp__repowise__get_risk"
    # Newer lean profiles also serve get_why; allowing it is harmless when
    # the server under test predates that (an unserved tool cannot be called).
    ",mcp__repowise__get_why"
)

# CLAUDE.md written into repo root for C1 runs.
# Claude Code auto-loads CLAUDE.md from cwd — this surfaces tool signatures and
# a strong call-to-action before the agent sees the question. It is an untracked
# file so it will NOT appear in the C0 git worktree.
_CLAUDE_MD_INDEX_ONLY = """\
# Repowise Codebase Intelligence (index-only mode)

You have four repowise tools available. **Use them — they are faster and more
accurate than grepping from scratch.** Call them BEFORE reading any source file.

---

## Tools — call signatures and what they return

### 1. `mcp__repowise__get_context`
```
mcp__repowise__get_context(targets=["path/to/a.py", "path/to/b.py"])
```
Returns for each file:
- **summary** — 1–3 sentence purpose blurb. Always present. In index-only mode
  this is auto-synthesized from class/function names.
- **symbols** — every top-level class/function/method with `signature` (full
  typed signature including return type), `start_line`/`end_line`, and a
  per-symbol `docstring` (truncated to 400 chars)
- **structure** — `{classes, functions, symbol_count, total_loc, avg_complexity}`
  for a quick scan of what the file contains
- **imported_by** — files that import this one (dependents)

Batch multiple files in a single call. This is your **primary navigation tool** —
call it on any file you suspect is relevant before reading its source.

**Interpreting the response — do not over-trust thin results:**
- Empty per-symbol `docstring`s + tiny `symbol_count` (e.g. 1–6 symbols, all
  classes with no methods) usually means the file is a **test fixture or stub**,
  not the real implementation. Do NOT answer from it. Follow `imported_by` to
  find the real caller, or Grep for the concept.
- Rich per-symbol docstrings are high-signal — you can often answer directly
  without Reading the source.
- Signatures include type annotations. Use them to pick the right function
  before Reading line ranges.

### 2. `mcp__repowise__get_risk`
```
mcp__repowise__get_risk(targets=["path/to/file.py"])
```
Returns: **hotspot_score** (0–1, churn percentile), **top-5 co-change partners**
(files historically edited together — the `has_import_link: false` ones are
*silent* couplings the import graph misses), **primary_owner**, **risk_type**
(`stable` / `churn-heavy` / `high-coupling` / `bus-factor-risk`).
Use when the question is about ownership, history, or coupling.

### 3. `mcp__repowise__get_why`
```
mcp__repowise__get_why(query="path/to/file.py")
```
Returns the most significant past commits for the file — commit messages,
authors, dates. Use for "why was this designed this way" questions.

### 4. `mcp__repowise__get_dependency_path`
```
mcp__repowise__get_dependency_path(source="path/a.py", target="path/b.py")
```
Returns the import chain between two files. Use when the question is about
how one module depends on another.

---

## Workflow

1. **Glob or Grep once** to identify 2–4 candidate files. Include both
   implementation paths (e.g. `django/db/...`) and any test/fixture paths
   you see — you want to compare them, not pick the first hit.
2. **`get_context` on all candidates in one batched call** — inspect the
   `structure` block and per-symbol docstrings to tell fixtures from real code.
3. If results look thin (see "Interpreting the response" above), broaden
   the Grep or follow `imported_by` — do not answer from a stub file.
4. **Read only the specific line ranges** (use `start_line`/`end_line`
   from the symbol list) from the real implementation file.
5. Answer. For ownership/history questions, add a `get_risk` or `get_why`
   call after step 2.

**Budget: up to 3 repowise calls per question. Never call `search_codebase`,
`get_overview`, `dead_code`, or `architecture_diagram` — they are not available.**
"""


def write_repo_claude_md(repo_path: Path, mode: str) -> None:
    """Write a CLAUDE.md into the repo root for the given condition mode.

    Claude Code auto-loads CLAUDE.md from cwd before the agent prompt, so this
    surfaces exact tool signatures and a strong call-to-action without consuming
    a system-prompt slot. Being an untracked file it is absent from the C0
    git worktree, keeping conditions cleanly separated.
    """
    if mode == "index-only":
        content = _CLAUDE_MD_INDEX_ONLY
    else:
        return  # C2 and others handled separately when needed
    claude_md = repo_path / "CLAUDE.md"
    claude_md.write_text(content, encoding="utf-8")

# Appended for the long (Bash-enabled) arm when distill is on. Teaches the
# agent to route noisy command output through `repowise distill`, which
# compresses it (errors-first, reversible) before it lands in context — and to
# expand a marker instead of re-running. Voluntary-prefix path (no Claude Code
# hook needed); the distilled output the agent reads is identical to what the
# rewrite hook would have produced.
DISTILL_PROMPT = """\

When you run a shell command whose output is long or noisy — test runs
(pytest), `git log`, `git diff`, `git show`, or wide `grep`/search floods —
prefix it with `repowise distill`, e.g. `repowise distill pytest tests/test_json.py`
or `repowise distill git log -n 50 -- src/flask/json`. It runs the command
unchanged (same exit code) and prints a compact, errors-first rendering, so you
spend far fewer tokens reading output. If you see a marker like
`[repowise#<ref>: N lines omitted ...]`, run `repowise expand <ref>` to restore
the omitted lines instead of re-running the command.
"""

# ---------------------------------------------------------------------------
# Register the legacy prompts with the arm registry.
#
# `configs/arms.yaml` refers to these as `builtin:<NAME>` rather than restating
# them inline, so the repowise arms are coached with the exact bytes the
# published flask48 numbers were produced with. A YAML copy would be one
# invisible whitespace change away from making those runs incomparable.
#
# They are also the reason `prompt_style: neutral` exists. Read
# SWEQA_PROMPT_FULL as a competitor would: it names which tool to call first,
# tells the agent when to trust a confidence signal, and instructs it NOT to
# verify. No competitor arm was ever offered coaching of that quality, and a
# cross-tool run using it measures our prompt engineering alongside our tool.
# Layer B's competitive tables use `neutral`; the repowise-only comparisons that
# have to line up with flask48 use `arm`. Any published row must say which.
# ---------------------------------------------------------------------------
arm_registry.register_builtin_coaching("SWEQA_PROMPT_FULL", SWEQA_PROMPT_FULL)
arm_registry.register_builtin_coaching("SWEQA_PROMPT_INDEX_ONLY", SWEQA_PROMPT_INDEX_ONLY)
arm_registry.register_builtin_coaching("SWEBENCH_PROMPT_FULL", SWEBENCH_PROMPT_FULL)
arm_registry.register_builtin_coaching("SWEBENCH_PROMPT_INDEX_ONLY", SWEBENCH_PROMPT_INDEX_ONLY)


# ---------------------------------------------------------------------------
# Condition -> arm
# ---------------------------------------------------------------------------

# The old boolean, kept working. `repowise_enabled` / `repowise_mode` were the
# whole dispatch; they are now one way of naming an arm among others, and they
# resolve to arms whose definitions reproduce the old behaviour exactly.
_LEGACY_ARM_FOR_MODE = {
    "full": "repowise-full",
    "lean": "repowise-lean",
    "index-only": "repowise-index-only",
}


def arm_name_for_condition(condition: dict) -> str:
    """Which arm this condition names.

    Prefer `arm: <name>`. A config that still says `repowise_enabled: true` with
    a `repowise_mode` keeps working and resolves to the same arm it always ran.
    """
    if condition.get("arm"):
        return str(condition["arm"])
    if not condition.get("repowise_enabled"):
        return "c0-bare"
    mode = condition.get("repowise_mode") or "full"
    if mode not in _LEGACY_ARM_FOR_MODE:
        raise ValueError(
            f"condition {condition.get('name')!r} has repowise_mode={mode!r}, "
            f"which maps to no arm. Either use `arm: <name>` or one of "
            f"{sorted(_LEGACY_ARM_FOR_MODE)}."
        )
    return _LEGACY_ARM_FOR_MODE[mode]


def _arm_from_mcp_server(condition: dict, bench_root: Path) -> tuple:
    """Synthesize an `Arm` for a condition carrying `mcp_server`.

    `mcp_server: {prefix, config}` is master's way of mounting a hand-written,
    pre-flighted MCP config verbatim, used by every `configs/context_bench_*`
    arm. The arm registry launches servers from a command instead, so a config
    written that way has no entry in `arms.yaml` and would otherwise fail
    resolution.

    Rather than keep a second code path for it, build the record the registry
    would have produced: the prefix names the server (its tools appear as
    ``mcp__<prefix>__*``, which is what the attach guard checks), the config is
    mounted as-is, and `index=None` marks it externally provisioned so
    `ensure_arm_index` skips the build instead of trying to run one.

    Returns `(arm, mcp_config_path)`. Raises if the config is missing, because
    a third-party arm whose config never loaded is an arm that answers with no
    tools and scores as a bad tool rather than as a broken setup.
    """
    server = dict(condition["mcp_server"])
    prefix = server["prefix"]
    cfg_path = Path(server["config"]).expanduser()
    if not cfg_path.is_absolute():
        cfg_path = bench_root / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"mcp_config_missing: {cfg_path}")
    arm = Arm(
        name=prefix,
        description=f"third-party MCP server, static config {cfg_path.name}",
        mcp={"server_name": prefix, "static_config": str(cfg_path)},
        client_tools=[f"mcp__{prefix}"],
        index=None,
        raw=dict(condition),
    )
    return arm, str(cfg_path)


# An unauthenticated `claude -p` exits 0, reports `subtype: success`, costs
# $0.00 and answers this. Nothing about the shape of that row says "failure",
# so a run whose credentials expired mid-flight records a full set of cheap
# wrong answers and the arms simply look bad. Detected as a hard error.
_NOT_LOGGED_IN = ("Not logged in", "Please run /login", "Invalid API key")


def _looks_unauthenticated(answer: str) -> bool:
    return any(marker in (answer or "") for marker in _NOT_LOGGED_IN)


MAX_RETRIES = 6

# The standing instruction every augmented arm gets in neutral-comparison
# mode: the SAME template for every arm with only the server name
# substituted, and no tool-specific workflow. It assigns the server the
# primary-interface role because anything weaker does not produce adoption:
# with eagerly-loaded schemas and a one-sentence "use the X tools" nudge,
# arms called their server in under 20% of runs (Sonnet defaults hard to
# Grep/Read), and a comparison in which no arm exercises its tool measures
# prompt bias, not tools. Adoption rate stays a reported metric either way.
# Requires eagerly-loaded schemas (alwaysLoad in the server config): under
# deferred loading the tools are absent from the model's callable set and
# no phrasing produces any adoption at all.
NEUTRAL_MCP_PROMPT = (
    "Your primary interface for exploring this repository is the '{prefix}' "
    "MCP tool set. For every question, query the '{prefix}' tools first and "
    "base your answer on what they return; use direct file reads only to "
    "verify details or fill gaps those tools leave."
)


class _StreamResult:
    """Minimal subprocess-result stand-in for the streaming path."""
    def __init__(self, returncode, stdout, stderr, timed_out):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def _run_streamed(cmd, cwd, timeout, log_path, env=None):
    """Run cmd, tee stdout to log_path live, enforce timeout, return result.

    Unlike subprocess.run(capture_output, timeout), this preserves everything
    the process emitted up to a timeout kill, so long agent runs stay
    debuggable. stderr is captured to a sibling .err file and read back.
    """
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    err_path = str(log_path) + ".err"
    with open(log_path, "w", encoding="utf-8") as out_f, \
         open(err_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=out_f, stderr=err_f, env=env or _UTF8_ENV,
            text=True, encoding="utf-8", errors="replace",
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
    stdout = Path(log_path).read_text(encoding="utf-8", errors="replace")
    stderr = Path(err_path).read_text(encoding="utf-8", errors="replace")
    return _StreamResult(proc.returncode, stdout, stderr, timed_out)


def run_claude_code(prompt: str, repo_path: str, condition: dict,
                    model: str, timeout: int,
                    max_budget_usd: float = 2.0,
                    mcp_config_path: Optional[str] = None,
                    benchmark: str = "swe_qa",
                    manage_c0_worktree: bool = True,
                    stream_log_path: Optional[str] = None,
                    arm: Optional[Arm] = None,
                    settings_path: Optional[str] = None,
                    prompt_style: str = "arm",
                    tool_descriptions: Optional[dict] = None,
                    claude_home: Optional[str] = None,
                    max_turns: Optional[int] = None) -> tuple:
    """
    Run Claude Code with retry on rate limits.
    Returns (output_dict, retries_used).

    benchmark: "swe_qa" or "swe_bench" — selects the right system prompt.
    arm: the resolved arm record. When omitted it is resolved from `condition`,
        so callers that still pass `repowise_enabled` keep working unchanged.
    manage_c0_worktree: when True (default), an arm with no MCP server is
        relocated into a fresh worktree scrubbed of every other arm's artifacts.
        The SWE-bench runner sets this False because it already supplies an
        isolated worktree checked out at the instance's base_commit (a HEAD
        worktree would be the wrong code) and captures the diff from that path.
    """
    if arm is None:
        arm = arm_registry.resolve_arm(
            arm_name_for_condition(condition),
            tree=Path(repo_path), repo_path=Path(repo_path), repo_name="",
        )

    # SWE-QA is read-only code understanding — no Bash by default.
    # Bash lets the agent escape the repo (read arbitrary files, call repowise CLI
    # manually, access the benchmark's own data/tasks.json answer key), so it is
    # opt-in per condition via `enable_bash` (the long investigation arm), where
    # it is needed to run the test/git/grep commands distill compresses.
    if benchmark == "swe_qa":
        base_tools = "Read,Grep,Glob,Bash" if condition.get("enable_bash") else "Read,Grep,Glob"
    else:
        base_tools = "Read,Grep,Glob,Bash,Edit,Write"

    uses_mcp = arm.uses_mcp

    # System prompt applied to ALL conditions — prevents repo escape.
    # Modern Claude Code DEFERS MCP tool schemas: an arm's tools are not in the
    # initial tool list, they are loaded on demand via ToolSearch. So any MCP arm
    # MUST be allowed to use ToolSearch or its tools are unreachable and it
    # degrades into a bare agent wearing the arm's name; a no-MCP arm keeps it
    # blocked. ListMcpResourcesTool / ReadMcpResourceTool are the repo-escape
    # vectors (they read arbitrary MCP resource URIs) and stay blocked everywhere.
    #
    # A condition carrying master's `mcp_server: {prefix, config}` (a
    # hand-written static config mounted verbatim) resolves to a synthesized
    # third-party arm, so `arm.uses_mcp` is already true for it here.
    base_system_prompt = (
        "You are answering a question about the code repository in your current directory. "
        "Only read files within the current repository. "
        "Do NOT access files outside the current directory. "
        "Do NOT read any benchmark, test-harness, or evaluation data. "
        "Do NOT use ListMcpResourcesTool or ReadMcpResourceTool. "
        + ("" if uses_mcp else "Do NOT use ToolSearch. ")
        + "Answer based solely on what you find in the source code."
    )

    # ListMcpResourcesTool / ReadMcpResourceTool are top-level tools (not under
    # mcp__*) that read any MCP resource URI — block them unconditionally so the
    # mcp__* disallow can't be bypassed through the resource namespace. Personal
    # MCP servers (Notion, Gmail, …) bleed in from the user's global config;
    # --strict-mcp-config already excludes them, and mcp__claude_ai_* belt-and-
    # braces blocks the hosted ones.
    disallowed = "ListMcpResourcesTool,ReadMcpResourceTool,mcp__claude_ai_*"

    # Isolation. A no-MCP arm ALWAYS runs in a clean worktree so .repowise/,
    # .codegraph/, graphify-out/ and .mcp.json from other arms are physically
    # absent rather than merely disallowed — `--disallowed-tools mcp__*` stops
    # the agent CALLING a server, it does not stop it Reading a previous arm's
    # generated wiki off disk. An MCP arm can opt in via `clean_worktree`; its
    # server keeps pointing at the indexed source checkout through absolute
    # paths in its config, so the served index is unaffected. If worktree
    # creation fails we FAIL LOUDLY rather than fall back to the real repo dir
    # (that's how C0 got silently contaminated before).
    if (not uses_mcp and manage_c0_worktree) or condition.get("clean_worktree"):
        variant = condition.get("worktree_variant", "")
        repo_path = str(get_c0_worktree(Path(repo_path), variant=variant))
        # Arm-specific files dropped into the private worktree (e.g. a packed
        # repo file the agent is told exists). {dest_name: source_path},
        # relative sources resolved against the bench root.
        for dest, src in (condition.get("worktree_files") or {}).items():
            src_path = Path(src).expanduser()
            if not src_path.is_absolute():
                src_path = _BENCH_ROOT / src_path
            shutil.copy2(src_path, Path(repo_path) / dest)

    if not uses_mcp:
        disallowed += ",ToolSearch,mcp__*"

    # Master's per-mode client blocklists are deliberately NOT carried over.
    # They were replaced by the allowlist rule in configs/arms.yaml, which
    # pins a served surface per arm and applies the same rule to us as to
    # every vendor. Blocking client-side on top of that would re-handicap
    # arms whose surface is already pinned.

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--include-hook-events",
        "--model", model,
        "--max-budget-usd", str(max_budget_usd),
        "--disallowed-tools", disallowed,
    ]
    if max_turns:
        cmd.extend(["--max-turns", str(max_turns)])

    # The CLI honors only the LAST --append-system-prompt flag (verified
    # empirically: two flags, only the second surfaced). Every prompt part
    # is therefore collected here and emitted as ONE flag at the end —
    # passing them as separate flags silently dropped the repo-escape rules
    # or the arm's tool sentence depending on ordering.
    system_prompt_parts = [base_system_prompt]

    # Pin the settings for this arm: hooks only as declared, no plugins, no
    # inherited MCP servers. See harness/arms.py::generate_settings and finding
    # D16 — an unpinned cell fires the operator's own hooks, two of which are
    # repowise's, into every arm including the bare control.
    if settings_path:
        cmd.extend(["--settings", settings_path])

    allowed_tools = base_tools
    # A condition using master's `mcp_server: {prefix, config}` arrives here as
    # a synthesized arm (see `_arm_from_mcp_server`): `client_tools` is
    # `mcp__<prefix>` and `coaching` is the neutral one-sentence prompt, so the
    # third-party branch master kept separate is this same branch.
    if uses_mcp:
        # Deferred MCP tools are loaded via ToolSearch — allow it so the agent
        # can pull this arm's tool schemas into context on first use.
        allowed_tools += ",ToolSearch"
        if arm.client_tools:
            allowed_tools += "," + ",".join(arm.client_tools)
        if mcp_config_path:
            # --strict-mcp-config: ignore user-global / project-level servers
            # (Figma/Notion/Apollo/Gmail/... from ~/.claude.json) and mount only
            # this arm's server from our config.
            cmd.extend(["--strict-mcp-config", "--mcp-config", mcp_config_path])
        # Neutral-comparison mode: master spelled this `neutral_prompt: true`
        # on the condition, we spell it `prompt_style: neutral` on the run.
        # Both mean "give this arm the SAME single sentence as every other
        # arm instead of its own workflow prompt", so no arm is coached more
        # than another. Map the config spelling onto the run spelling.
        style = prompt_style
        if condition.get("neutral_prompt") and style == "arm":
            style = "neutral"
        # A statically-mounted third-party arm has no arms.yaml coaching to
        # resolve, and master always gave these the neutral one-liner. Without
        # this they would silently get NO tool prompt at all while every
        # registry arm got one, which is the coaching asymmetry the neutral
        # mode exists to remove.
        if (arm.mcp or {}).get("static_config") and style == "arm":
            style = "neutral"
        coaching = arm.resolved_coaching(style, tool_descriptions)
        if coaching:
            cmd.extend(["--append-system-prompt", coaching])
    else:
        # No MCP servers mounted at all. An empty strict config suppresses both
        # the operator's global servers and any project-level .mcp.json that a
        # tool may have written into the repo.
        empty_cfg_path = _BENCH_ROOT / "configs" / "_empty_mcp.json"
        if not empty_cfg_path.exists():
            empty_cfg_path.parent.mkdir(parents=True, exist_ok=True)
            empty_cfg_path.write_text('{"mcpServers": {}}')
        cmd.extend(["--strict-mcp-config", "--mcp-config", str(empty_cfg_path)])

    # Distill instruction (long arm): teach the agent to route noisy command
    # output through `repowise distill`. Independent of the MCP surface.
    if condition.get("distill"):
        system_prompt_parts.append(DISTILL_PROMPT)

    # Arm-specific factual note, e.g. naming a file injected into the
    # worktree. One neutral sentence owned by the config, never coaching.
    if condition.get("system_note"):
        system_prompt_parts.append(condition["system_note"])

    cmd.extend(["--append-system-prompt", "\n\n".join(system_prompt_parts)])
    cmd.extend(["--allowed-tools", allowed_tools])

    run_env = dict(_UTF8_ENV)
    if claude_home:
        run_env["CLAUDE_CONFIG_DIR"] = claude_home

    # PIN THE BINARY FOR THE WHOLE CELL, not just for the MCP launch command.
    #
    # `repowise_exe()` already pins what the harness spawns. It does not pin
    # what the cell spawns, and a declared hook does exactly that: the shipped
    # command is `if command -v repowise-augment ...; then exec ...; fi`, so it
    # takes whatever PATH offers. Measured 2026-08-08: PATH offered
    # `Desktop/repowise/.venv/Scripts/repowise-augment`, an editable install of
    # a checkout with uncommitted changes, which is the wrong binary for a
    # pinned run and would have been published as the pinned one. Prepending
    # the pinned Scripts dir makes `command -v` resolve there (verified under
    # the git-bash `sh` Claude Code runs hook commands with on Windows).
    #
    # Applied to every arm, not just the hooks arm: an arm that declares no
    # hooks spawns no repowise binary at all, so this is a no-op for it, and a
    # PATH that differs per arm would be a second difference between the pair.
    _pinned_bin = Path(arm_registry.repowise_exe()).parent
    if _pinned_bin.is_dir():
        run_env["PATH"] = str(_pinned_bin) + os.pathsep + run_env.get("PATH", "")

    for attempt in range(MAX_RETRIES):
        try:
            if stream_log_path:
                # Stream stdout to a file in real time so a timeout (or any
                # mid-run death) still leaves the full stream-json trail for
                # diagnosis — subprocess.run(capture_output) discards it on
                # TimeoutExpired, which left long agent runs un-debuggable.
                result = _run_streamed(cmd, repo_path, timeout, stream_log_path,
                                       env=run_env)
                if result.timed_out:
                    raise subprocess.TimeoutExpired(cmd, timeout,
                                                    output=result.stdout)
            else:
                result = subprocess.run(
                    cmd, cwd=repo_path, capture_output=True, text=True,
                    timeout=timeout, env=run_env, encoding="utf-8",
                    errors="replace"
                )

            if result.returncode == 0 and result.stdout.strip():
                # stream-json: parse all lines, extract the result line + tool calls
                from harness.metrics import parse_claude_stream_output
                lines = result.stdout.strip().split("\n")
                parsed = parse_claude_stream_output(lines)

                # Build a combined output dict (compatible with json mode)
                output = {
                    "result": parsed["answer"],
                    "num_turns": parsed["num_turns"],
                    "task_subagent_calls": parsed.get("task_subagent_calls", 0),
                    "total_cost_usd": parsed["total_cost_usd"],
                    "usage": {
                        "input_tokens": parsed["input_tokens"],
                        "output_tokens": parsed["output_tokens"],
                        "cache_read_input_tokens": parsed["cache_read_tokens"],
                        "cache_creation_input_tokens": parsed["cache_write_tokens"],
                    },
                    "session_id": parsed.get("session_id", ""),
                    "stop_reason": parsed.get("stop_reason", ""),
                    "duration_api_ms": parsed.get("duration_api_ms", 0),
                    # Tool call details (not available in json mode)
                    "num_tool_calls": parsed["num_tool_calls"],
                    "files_explored": parsed["files_explored"],
                    "files_edited": parsed["files_edited"],
                    "repowise_tools_called": parsed["repowise_tools_called"],
                    # Proof of life, carried per cell.
                    "mcp_tools_issued": parsed.get("mcp_tools_issued", []),
                    "mcp_isError_count": parsed.get("mcp_isError_count", 0),
                    "mcp_per_server": parsed.get("mcp_per_server", {}),
                    "server_tools_called": parsed.get("server_tools_called", {}),
                    "hook_events": parsed.get("hook_events", []),
                    "hook_injections": parsed.get("hook_injections", []),
                    "models_used": parsed.get("models_used", []),
                    "token_source": parsed.get("token_source", ""),
                    # Keep raw lines for saving
                    "_raw_stream_lines": lines,
                }

                # An unauthenticated CLI exits 0 with subtype "success" and
                # answers "Not logged in". Never let that become a data row.
                if _looks_unauthenticated(output["result"]):
                    return {
                        "error": "not_authenticated: claude exited 0 but the "
                                 "session is not logged in; every cell would "
                                 "record as complete with a $0 wrong answer",
                        "_raw_stream_lines": lines,
                    }, attempt

                # Check for rate-limit error
                if not output["result"] and result.stderr:
                    err_text = result.stderr
                    if is_rate_limit_error(err_text):
                        backoff_sleep(attempt)
                        continue

                return output, attempt

            else:
                # Non-zero exit. stderr is often empty when claude itself
                # exits cleanly after exhausting retries — the diagnostic
                # detail is in the stream-json events on stdout. Extract it.
                err = _extract_failure_reason(result.stdout, result.stderr)
                # Always preserve the raw stream so post-mortem inspection
                # can see the api_retry chain even on hard failures.
                raw_lines = result.stdout.strip().split("\n") if result.stdout else []
                if is_rate_limit_error(err):
                    backoff_sleep(attempt)
                    continue
                # A hard exit (notably error_max_turns) still burned real tokens
                # and the result event carries the usage. Parse it so the row's
                # cost is recorded honestly — otherwise an arm that thrashes to
                # the turn cap looks CHEAPER than one that answers, biasing the
                # very cost comparison this benchmark exists to make. The row
                # stays an error (empty answer, not judged); only its accounting
                # is filled in.
                err_out = {
                    "error": err,
                    "returncode": result.returncode,
                    "_raw_stream_lines": raw_lines,
                }
                try:
                    from harness.metrics import parse_claude_stream_output
                    p = parse_claude_stream_output(raw_lines)
                    err_out["total_cost_usd"] = p.get("total_cost_usd", 0.0)
                    err_out["num_turns"] = p.get("num_turns", 0)
                    err_out["usage"] = {
                        "input_tokens": p.get("input_tokens", 0),
                        "output_tokens": p.get("output_tokens", 0),
                        "cache_read_input_tokens": p.get("cache_read_tokens", 0),
                        "cache_creation_input_tokens": p.get("cache_write_tokens", 0),
                    }
                    err_out["token_source"] = p.get("token_source", "")
                except Exception:
                    pass  # accounting is best-effort; never mask the real error
                return err_out, attempt

        except subprocess.TimeoutExpired as e:
            # Preserve whatever the agent streamed before the kill so a timeout
            # is diagnosable (which/how many tool calls, last action).
            partial = e.output if isinstance(e.output, str) else ""
            raw_lines = partial.strip().split("\n") if partial.strip() else []
            tool_calls = sum(1 for ln in raw_lines if '"type":"assistant"' in ln
                             and '"tool_use"' in ln)
            return {
                "error": f"timeout (streamed {len(raw_lines)} events, "
                         f"~{tool_calls} tool calls before kill)",
                "timed_out": True,
                "_raw_stream_lines": raw_lines,
            }, attempt
        except Exception as e:
            err = str(e)
            if is_rate_limit_error(err):
                backoff_sleep(attempt)
                continue
            return {"error": err}, attempt

    return {"error": "max_retries_exhausted"}, MAX_RETRIES


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------

def _extract_json_scores(text: str) -> dict:
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[^{}]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"error": f"parse_failed: {text[:200]}"}


# The judge must never be the model it is grading, and "the model" means the
# FAMILY, not the string. Self-preference bias is a property of a model lineage
# and its training data, not of a version number: `gpt-5.6-luna` grading
# `gpt-5.6-sol` is the same bug as `sonnet` grading `sonnet`, arrived at one
# character later.
DEFAULT_JUDGE_MODEL = "gpt-5.6-luna"

# Which judge to reach for given the family the AGENT belongs to. This became
# load-bearing the moment a second harness existed: `DEFAULT_JUDGE_MODEL` was
# chosen when every arm was Claude-family, and a Codex run on a GPT model
# inherits it and self-grades. There is no single right default here because
# the right default depends on the arm.
DEFAULT_JUDGE_BY_AGENT_FAMILY = {
    "anthropic": "gpt-5.6-luna",
    "openai": "claude-sonnet-5",
}


def _resolve_judge_model(config: dict) -> str:
    """Judge model for this run, refusing to grade a family with itself.

    Three bugs are guarded here.

    1. **The default was the agent model** (2026-08-01). `judge_model` fell
       back to `config["agent"]["model"]`, so an unconfigured run graded the
       agent with itself.
    2. **The configs made it explicit anyway** (2026-08-01). Every shipped
       config set `judge_model: "sonnet"` under a comment reading "neutral
       judge across all cells, never the agent model", while `model:` was also
       `"sonnet"`. Not merely same-family: byte-identical. Every published
       SWE-QA quality score before that date was self-graded.
    3. **The check was byte equality** (2026-08-03, this session). Which is to
       say it caught case 2 and would not have caught the thing it exists to
       prevent. `DEFAULT_JUDGE_MODEL` is a GPT model, chosen because every arm
       was Claude-family; a Codex arm runs a GPT agent and would have been
       graded by a GPT judge, silently, with the config unchanged and every
       cell reporting a `judge_model` that looks cross-family until you know
       what the agent was. D3/D8 arriving from the other side.

    So the comparison is on `_judge_family`, and the default is chosen from
    the agent's family rather than fixed.

    The cost of getting this right is a real confound and it is not hidden: a
    cross-HARNESS table then has two different judges. PLAN.md already
    specifies the mitigation for luna-vs-terra and the same one applies here —
    grade a stratified subset with both judges and publish the agreement.
    """
    agent_model = str(config.get("agent", {}).get("model", "")).strip()
    agent_family = _judge_family(agent_model) if agent_model else "unknown"
    configured = config.get("evaluation", {}).get("judge_model")

    if configured:
        judge = str(configured).strip()
    else:
        judge = DEFAULT_JUDGE_BY_AGENT_FAMILY.get(agent_family, DEFAULT_JUDGE_MODEL)

    judge_family = _judge_family(judge)

    if agent_model and judge == agent_model:
        raise ValueError(
            f"judge_model ({judge!r}) is the same model as the agent "
            f"({agent_model!r}). A model may not grade itself: self-preference "
            f"bias makes the quality column meaningless."
        )
    if agent_family != "unknown" and judge_family == agent_family:
        raise ValueError(
            f"judge_model ({judge!r}) is in the same family as the agent "
            f"({agent_model!r}): both resolve to {judge_family!r}. "
            f"Self-preference bias is a property of the lineage, not of the "
            f"version string, so a same-family judge invalidates the quality "
            f"column exactly as a self-grading one does. For a "
            f"{agent_family!r} agent use "
            f"{DEFAULT_JUDGE_BY_AGENT_FAMILY.get('openai' if agent_family == 'anthropic' else 'anthropic')!r}."
        )
    if agent_family == "unknown" and agent_model:
        # An agent this harness cannot place cannot be proven cross-family
        # against anything. Refuse rather than record a `judge_model` field
        # that reads as a guarantee it is not.
        raise ValueError(
            f"agent model {agent_model!r} matches no family this harness "
            f"knows, so the judge cannot be proven cross-family. Add its "
            f"prefix to _judge_family before running it."
        )
    return judge


def _openai_key_candidates() -> list[Path]:
    """Where `provider_config.json` might be, most specific first.

    More than one location, because pinning `REPOWISE_ROOT` broke this and the
    failure was split across two symptoms that do not look related.

    The environment pin the whole of Layer B runs under points `REPOWISE_ROOT`
    at a DETACHED WORKTREE (`repowise-layerb`), so that nothing can switch the
    binary under a run in flight. `provider_config.json` is untracked and lives
    in the main checkout, so it is not in the worktree — and this function only
    looked in `REPOWISE_ROOT`. Two consequences, neither of which announced
    itself as the same bug:

      * the judge raised `needs an OpenAI key and none was found`, mid-run,
        after the agent spend for that cell was already gone;
      * before that, and silently, `_index_extra_env` returned `{}`, so the
        repowise MCP server was launched WITHOUT `OPENAI_API_KEY` in its
        environment. That is finding A9's exact precondition. It happened to
        survive only because the server now recovers the key from
        `~/.repowise/config.yaml` and says so in a log line nobody parses.

    So: search the pinned root, then the bench's own parent checkout, then the
    conventional user location.
    """
    return [
        _REPOWISE_ROOT / "provider_config.json",
        _BENCH_ROOT.parent / "provider_config.json",
        Path.home() / ".repowise" / "provider_config.json",
    ]


def _openai_api_key() -> Optional[str]:
    """The OpenAI key, from the environment or a provider config.

    The environment wins so CI can override without editing a file.
    """
    env = os.environ.get("OPENAI_API_KEY")
    if env:
        return env
    for cfg in _openai_key_candidates():
        if not cfg.exists():
            continue
        try:
            key = json.loads(cfg.read_text(encoding="utf-8")).get("keys", {}).get("openai")
        except (json.JSONDecodeError, OSError):
            continue
        if key:
            return str(key)
    return None


def _judge_family(judge_model: str) -> str:
    """Which client can actually reach this model.

    Existed because it did not. `DEFAULT_JUDGE_MODEL` was switched to a GPT
    model for cross-family grading (D3/D8), but `judge_answer` only ever had an
    Anthropic SDK path and a `claude` CLI path, and the SDK failure was
    swallowed by a bare `except Exception: pass`. So every judge call fell
    through to `claude --model gpt-5.6-luna`, failed, and returned an error
    dict. The quality column came back empty for every arm, after the agent
    spend was already gone. Route explicitly, and refuse what cannot be routed.
    """
    m = judge_model.lower()
    if m.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if m.startswith("claude") or m == "sonnet" or m == "opus" or m == "haiku":
        return "anthropic"
    return "unknown"


# Per-category additions to the judge rubric. history-why exists because a
# generic rubric rewards fluent invention: a confident, plausible, WRONG
# rationale reads as a good answer unless the judge is told groundedness in
# the actual historical reason is the thing being scored.
#
# Note for anyone reading a SWE-QA number: `history-why` is EMPTY in the
# django SWE-QA set (0 of 48, see harness/question_shapes.py), so this rubric
# only ever fires on question sources that actually carry the shape, such as
# data/context_bench/questions_why.json.
CATEGORY_RUBRICS = {
    "history-why": """
CATEGORY NOTE: This question asks WHY the code is the way it is, and the
reference answer cites a specific historical rationale (a commit, pull
request, or issue). Score correctness on whether the agent gives the ACTUAL
recorded reason, not a plausible-sounding invention: a confident rationale
that differs from the reference's cited reason scores correctness 3 or
lower, however fluent. An answer that says the reason cannot be determined
from the available information is honest, not wrong: score its correctness
5 and judge the other dimensions on their merits.
""",
}


def judge_answer(question: str, gold_answer: str, agent_answer: str,
                 judge_model: str, category: Optional[str] = None) -> dict:
    """Score agent answer via LLM judge. Retries on rate limits.

    Blind: the judge never sees condition labels. `category` selects an
    optional per-shape rubric addition from CATEGORY_RUBRICS.
    """
    rubric = CATEGORY_RUBRICS.get(category or "", "")
    judge_prompt = f"""You are evaluating an AI agent's answer to a repository-level code question.

QUESTION:
{question}

REFERENCE ANSWER:
{gold_answer}

AGENT ANSWER:
{agent_answer}
{rubric}
Score the agent's answer on each dimension (1-10 scale):
- Correctness: Is the answer factually accurate?
- Completeness: Does it address all aspects of the question?
- Relevance: Does it directly answer what was asked?
- Clarity: Is it clear and easy to understand?
- Reasoning: Does it show logical coherence and proper code reasoning?

Respond with ONLY a JSON object like:
{{"correctness": 7, "completeness": 6, "relevance": 8, "clarity": 9, "reasoning": 7}}
"""

    family = _judge_family(judge_model)

    if family == "unknown":
        raise ValueError(
            f"judge_model {judge_model!r} matches no client this harness has. "
            f"Supported prefixes: gpt-/o1/o3/o4 (OpenAI), claude-/sonnet/opus/"
            f"haiku (Anthropic). Refusing to run: an unroutable judge returns "
            f"an error dict per task and silently empties the quality column "
            f"after the agent spend is gone."
        )

    if family == "openai":
        key = _openai_api_key()
        if not key:
            raise ValueError(
                f"judge_model {judge_model!r} needs an OpenAI key and none was "
                f"found in OPENAI_API_KEY or any of: "
                + ", ".join(str(p) for p in _openai_key_candidates())
            )
        # `max_completion_tokens`, not `max_tokens`: the current GPT models
        # reject the older parameter outright with a 400. The budget is well
        # above the ~30 tokens the rubric JSON needs because a reasoning model
        # spends this allowance on reasoning first and returns empty content if
        # it runs out, which parses as a judge failure rather than as a score.
        # `temperature` is dropped for the same reason it was set: some models
        # accept only the default and 400 on any explicit value, so it is sent
        # once and retried without it rather than assumed either way.
        # 2000 was not enough and the failure is SILENT AND ASYMMETRIC.
        # Measured in the rung 6 pilot: two cells came back
        # `{"error": "parse_failed: "}` — empty content, not malformed content —
        # and both were `c0-bare`, because the bare arm writes the longest
        # answers (no tool summary to lean on) and a longer answer means a
        # longer rubric prompt and more reasoning before the ~30 tokens of JSON.
        # So the judge dropped cells from ONE arm, the control, and the arm's
        # mean was then computed over 8 of 10 rather than 10. A budget that
        # fails on long answers does not fail at random.
        kwargs: dict = {
            "model": judge_model,
            "max_completion_tokens": 16000,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": judge_prompt}],
        }
        for attempt in range(3):
            try:
                from openai import OpenAI
                client = OpenAI(api_key=key)
                response = client.chat.completions.create(**kwargs)
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    # Empty content means the model spent its allowance on
                    # reasoning and returned nothing. Retry with more room
                    # rather than record a parse failure, which reads as a
                    # scoring result and silently shrinks one arm's n.
                    kwargs["max_completion_tokens"] = min(
                        int(kwargs.get("max_completion_tokens", 16000)) * 2, 64000
                    )
                    continue
                return _extract_json_scores(text)
            except Exception as e:
                msg = str(e)
                if "temperature" in msg and "temperature" in kwargs:
                    kwargs.pop("temperature")
                    continue
                if is_rate_limit_error(msg):
                    backoff_sleep(attempt, base=20.0)
                    continue
                return {"error": f"judge_failed: {msg[:200]}"}
        return {"error": "judge_max_retries"}

    # Anthropic SDK first (if API key available). max_tokens is 2,000 rather
    # than the 200 this used to carry: the rubric JSON is ~30 tokens, but a
    # model that thinks before emitting it spends the allowance on thinking and
    # returns EMPTY CONTENT, which parses as a judge failure rather than as a
    # score. That failure is not random — it lands on whichever arm writes the
    # longest answers, which is the bare control — and it cost the rung 6 pilot
    # two of the control's ten cells. Same reasoning as the OpenAI branch above.
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=judge_model, max_tokens=2000, temperature=0.0,
                messages=[{"role": "user", "content": judge_prompt}]
            )
            text = "".join(getattr(b, "text", "") for b in response.content).strip()
            if text:
                return _extract_json_scores(text)
        except Exception:
            pass

    # Fall back to Claude CLI with retry.
    #
    # Pinned exactly as an agent cell is, and for the same reason (finding
    # D16). The operator's own ~/.claude/settings.json fires 8 hooks in any
    # unpinned `claude -p`, two of which inject context, and one of those is
    # repowise's own. A judge is a `claude -p` like any other: unpinned, the
    # grader for every arm's quality column runs with repowise's hook output
    # prepended to the rubric. That was never measured because the judge was
    # assumed not to be part of the experiment, which is precisely the
    # assumption D16 punished on the C0 arm.
    judge_home = str(arm_registry.prepare_claude_home())
    judge_env = dict(_UTF8_ENV)
    judge_env["CLAUDE_CONFIG_DIR"] = judge_home
    empty_cfg = _BENCH_ROOT / "configs" / "_empty_mcp.json"
    if not empty_cfg.exists():
        empty_cfg.parent.mkdir(parents=True, exist_ok=True)
        empty_cfg.write_text('{"mcpServers": {}}')

    for attempt in range(3):
        try:
            result = subprocess.run(
                # 0.15 was not enough and it failed ASYMMETRICALLY, which is
                # the third time this exact shape has cost this workstream a
                # cell. A longer agent answer means a longer rubric prompt, the
                # bare control writes the longest answers because it has no
                # tool summary to lean on, so a budget that fails on long
                # answers removes cells from one arm only. Measured on the
                # Codex n=3 run: `judge_failed` on one cell of six, and it was
                # `c0-bare` on the longest answer in the set.
                ["claude", "-p", judge_prompt, "--output-format", "json",
                 "--model", judge_model, "--max-budget-usd", "0.60",
                 "--strict-mcp-config", "--mcp-config", str(empty_cfg),
                 "--disallowed-tools",
                 "Bash,Read,Grep,Glob,Edit,Write,WebFetch,WebSearch,Task,"
                 "ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,mcp__*"],
                capture_output=True, text=True, timeout=90,
                env=judge_env, encoding="utf-8", errors="replace"
            )
            if result.returncode == 0 and result.stdout.strip():
                output = json.loads(result.stdout)
                if output.get("is_error"):
                    err = output.get("result", "")
                    if is_rate_limit_error(err):
                        backoff_sleep(attempt, base=20.0)
                        continue
                    return {"error": err[:200]}
                text = output.get("result", "")
                # An unauthenticated CLI exits 0 and answers "Not logged in".
                # As a judge that becomes a parse failure on every cell, i.e.
                # an empty quality column after the agent spend is gone
                # (finding D17, on the grader rather than on the agent).
                if _looks_unauthenticated(text):
                    return {"error": "judge_not_authenticated"}
                return _extract_json_scores(text)
            # A non-zero exit with EMPTY stderr is the normal case here, not
            # the odd one: the CLI exits cleanly after exhausting its own
            # retries and the diagnostic lives in the JSON on stdout. Reporting
            # `judge_failed: ` with nothing after the colon is how one cell
            # went unexplained for a whole session.
            err = (result.stderr or "").strip()[:300]
            if not err:
                err = _extract_failure_reason(result.stdout, result.stderr)
            if not err:
                err = (f"exit {result.returncode}, no stderr and no parseable "
                       f"stdout; stdout[:200]={(result.stdout or '')[:200]!r}")
            # Master retried whenever stderr was empty. We diagnose instead:
            # `err` is filled from the stream above, so an empty-stderr exit
            # now carries a reason rather than being retried blind.
            if is_rate_limit_error(err):
                backoff_sleep(attempt, base=20.0)
                continue
            return {"error": f"judge_failed: {err}"}
        except Exception as e:
            if is_rate_limit_error(str(e)):
                backoff_sleep(attempt, base=20.0)
                continue
            return {"error": str(e)[:200]}

    return {"error": "judge_max_retries"}


# ---------------------------------------------------------------------------
# Single task runner
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-arm staging, indexing and proof of life
# ---------------------------------------------------------------------------

# Guards the once-per-(arm, repo) work so N parallel workers do not race into
# the same worktree or run the same index build N times.
_ARM_SETUP_LOCK = threading.Lock()
_ARM_INDEX_DONE: dict = {}


def prepare_arm_tree(arm_name: str, repo_path: Path, config: dict) -> Path:
    """This arm's own checkout of the repo.

    Every arm gets one, including the bare control, because finding E3 is that
    arms contaminate each other through the tree and not through the tool
    allowlist: each writes its index into a dotdir inside the repo, so a shared
    checkout means each arm indexes its predecessors' output. The bias favours
    whoever ran first, which in every run this workstream has done was us.

    This is a change from the flask48 layout, which indexed `repos/<org>/<repo>`
    in place and gave only C0 a worktree. That was sound with one tool under
    test and is not sound with seven. The indexed CONTENT is identical — a
    worktree at the same HEAD is the same files — so the repowise arms still
    index what they always indexed; only the path moves.
    """
    trees_root = config.get("paths", {}).get("trees_dir")
    registry = arm_registry.load_registry(config.get("arms_file"),
                                          config.get("arms_dir"))
    owner = (registry.get(arm_name) or {}).get("shares_index_with") or arm_name
    with _ARM_SETUP_LOCK:
        return arm_registry.arm_tree(
            arm_name, repo_path,
            trees_root=Path(trees_root) if trees_root else None,
            owner=owner,
        )


def _index_extra_env(arm: Arm) -> dict:
    """Provider credentials an arm's build and server both need.

    FINDING D13, and it silently invalidated every repowise row rungs 5 and 8
    published. `init --embedder openai` needs the key in the BUILD environment.
    Without it the generator falls back to MockEmbedder, writes 8-dimensional
    vectors, and says so only in a decorative closing card nobody parsed. rc is
    0 and the index looks complete. The query side then resolves a REAL
    embedder, builds a 1536-dimension question vector, and every vector search
    raises `No vector column found to match with the query vector dimension`,
    which is caught and returns [] — so the arm answers on full-text alone and
    reports itself healthy.
    """
    env = {}
    if arm.name.startswith("repowise"):
        key = _openai_api_key()
        if key:
            env["OPENAI_API_KEY"] = key
        else:
            # Returning {} here is how D13 happened, and it returns {} without
            # saying so. Say so.
            print(
                f"  !! {arm.name}: no OpenAI key found in OPENAI_API_KEY or "
                + ", ".join(str(p) for p in _openai_key_candidates())
                + ". The index build will fall back to MockEmbedder (8-dim "
                  "vectors, exit 0, looks complete) and the server will be "
                  "launched without the key (finding A9). Fix before trusting "
                  "any row from this run.",
                flush=True,
            )
    return env


def ensure_arm_index(arm: Arm, tree: Path, repo_name: str, config: dict,
                     metrics: RunMetrics) -> dict:
    """Build this arm's index once per (tree-owner, repo). Returns evidence."""
    # `repowise.assume_indexed` (master's context_bench line): the pinned
    # staged checkout already carries the index and that index IS the
    # experiment artifact, so rebuilding would move the thing being measured.
    # Verify rather than trust: an assume_indexed run against a tree with no
    # index is an arm querying nothing, which scores as a tool that cannot
    # retrieve instead of as a setup error.
    if (config.get("repowise") or {}).get("assume_indexed") and \
            arm.server_name == "repowise":
        if not (tree / ".repowise").is_dir():
            return {"arm": arm.name,
                    "failed": f"assume_indexed_but_no_index: {tree}"}
        return {"arm": arm.name, "skipped": "assume_indexed", "seconds": 0.0}

    key = (arm.tree_owner, str(tree))
    with _ARM_SETUP_LOCK:
        if key in _ARM_INDEX_DONE:
            return _ARM_INDEX_DONE[key]

        logs_dir = Path(config["paths"]["logs_dir"]) / "builds"

        # A sharing arm builds the OWNER's index, not nothing. `repowise-lean`
        # declares `index: null` because it does not build one of its own — but
        # running the lean arm alone must still produce an index, or the arm
        # queries an empty tree and scores as a tool that cannot retrieve. The
        # first version of this returned "no-index-by-design" and would have
        # published exactly that.
        build_arm = arm
        if arm.index is None and arm.shares_index_with:
            build_arm = arm_registry.resolve_arm(
                arm.shares_index_with, tree=tree, repo_path=tree,
                repo_name=repo_name,
                arms_file=config.get("arms_file"),
                arms_dir=config.get("arms_dir"),
            )
        requested_name = arm.name
        arm = build_arm
        builtin = (arm.index or {}).get("builtin")

        # A PREBUILD STAMP ON THE TREE MEANS THE BUILD ALREADY HAPPENED, AND
        # UNTIL NOW THIS FUNCTION DID NOT LOOK.
        #
        # `arm_registry.build_index` has no skip guard: it re-runs the arm's
        # index command every time a fresh process reaches it, because
        # `_ARM_INDEX_DONE` memoises within one process only. So a run whose
        # indexes were all prebuilt still rebuilt every competitor index INLINE,
        # inside the timed run. That is not hypothetical; it is what the Go
        # ContextBench run did, and its own prebuild script records the
        # consequence: "prebuild_indexes.py did not prevent inline builds, which
        # put an E11 confound in that run's cost column". It is finding E1 as
        # well, since a build running beside cells contends with them.
        #
        # The stamp is written by `scripts/prebuild_mui_indexes.py` only AFTER a
        # build exits 0. The embedding proof is re-run LIVE rather than read out
        # of the stamp, so D13 still refuses an 8-dimension index here even
        # though no build ran.
        # Both names are checked, and that is not belt-and-braces. The prebuild
        # script stamps under the arm name IT was given, which for a sharing arm
        # is the sharer (`repowise`), while this function has already resolved
        # `arm` to the OWNER (`repowise-full`). Checking one name only would
        # miss the stamp the prebuild actually wrote and rebuild a prose index
        # inline, which is the exact failure this guard exists to stop.
        stamp = next(
            (p for p in (
                tree / f".bench_prebuild__{n.replace('/', '-')}.json"
                for n in dict.fromkeys([requested_name, arm.name, key[0]]) if n
            ) if p.exists()),
            None,
        )
        if arm.index is not None and stamp is not None:
            try:
                stamped = json.loads(stamp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                stamped = {}
            evidence = {"arm": arm.name, "skipped": "prebuild stamp on tree",
                        "stamp": str(stamp),
                        "seconds": 0.0,
                        "prebuild_seconds": stamped.get("wall_seconds"),
                        "prebuild_rc": stamped.get("rc")}
            evidence.update(arm_registry.index_embedding_proof(arm, tree))
            if evidence.get("index_embedder_mock"):
                evidence["failed"] = (
                    f"index is mock-embedded (vector dim "
                    f"{evidence.get('index_vector_dim')}); the vector retrieval "
                    f"leg cannot run against it (finding D13)"
                )
            metrics.index_time_seconds = 0.0
            _ARM_INDEX_DONE[key] = evidence
            return evidence

        if arm.index is None:
            evidence = {"skipped": "no-index-by-design", "seconds": 0.0}
        elif builtin in ("repowise_legacy", "repowise_legacy_index_only"):
            # The flask48 index path, unchanged, pointed at this arm's tree.
            t0 = time.time()
            ok, idx_time = index_repo_at(
                tree, repo_name,
                config["repowise"]["index_dir"],
                "index-only" if builtin.endswith("index_only") else "full",
                config["repowise"]["doc_model"],
                provider=config["repowise"].get("provider"),
                embedder=config["repowise"].get("embedder"),
            )
            evidence = {
                "builtin": builtin,
                "rc": 0 if ok else 1,
                "seconds": round(idx_time or (time.time() - t0), 1),
            }
            evidence.update(arm_registry.index_embedding_proof(arm, tree))
            if not ok:
                evidence["failed"] = "repowise init returned non-zero"
        elif builtin:
            evidence = {"failed": f"unknown builtin index {builtin!r}"}
        else:
            evidence = arm_registry.build_index(
                arm, tree, logs_dir, extra_env=_index_extra_env(arm))
            if evidence.get("rc") not in (0, None):
                evidence["failed"] = f"build exited {evidence['rc']}"

        # D13: an 8-dimension index is not a measurement of repowise.
        if evidence.get("index_embedder_mock"):
            evidence["failed"] = (
                f"index is mock-embedded (vector dim "
                f"{evidence.get('index_vector_dim')}); the vector retrieval leg "
                f"cannot run against it (finding D13)"
            )

        metrics.index_time_seconds = float(evidence.get("seconds") or 0.0)
        _ARM_INDEX_DONE[key] = evidence
        return evidence


def probe_arm_server(arm: Arm, mcp_config_path: str, timeout: float = 120.0) -> dict:
    """Start this arm's server exactly as Claude Code will, and look at it.

    Answers, before a cent of agent spend: did the server start, what did it
    advertise, and did its activation steps succeed. `query_arm` in Layer A does
    the same thing and finding E4 exists because of it — a dead arm and a bad
    arm produce identical summary rows, and the silently-dead one is never ours,
    because ours is the only output format we already know.

    It also runs the arm's `warm` call. For repowise that call is EXPECTED to be
    abandoned: the first MCP call after a server start does not return, measured
    at 240s, 300s, 400s and 600s, and what unblocks the server is the client
    giving up — the next call answers in about 1.3s (finding A8). Note the
    limit of this: the agent launches its OWN server process, so this warm-up
    warms a process the agent does not use. It is recorded as evidence, not
    relied on as a fix.
    """
    import asyncio

    async def _probe() -> dict:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        cfg = json.loads(Path(mcp_config_path).read_text(encoding="utf-8"))
        server = cfg["mcpServers"][arm.server_name]
        env = {**os.environ, **(server.get("env") or {})}
        sp = StdioServerParameters(
            command=server["command"], args=server.get("args") or [], env=env)

        row: dict = {"arm": arm.name, "command": server["command"],
                     "args": server.get("args")}
        try:
            async with asyncio.timeout(timeout):
                cm = stdio_client(sp)
                r, w = await cm.__aenter__()
        except Exception as e:  # noqa: BLE001
            row.update({"status": "server-failed", "error": f"{type(e).__name__}: {e}"})
            return row
        try:
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                served = sorted(t.name for t in tools)
                # The server's OWN words for each tool, captured here rather
                # than written by us. `prompt_style: neutral-described` puts one
                # sentence of this in front of the agent, because under plain
                # `neutral` the agent decides whether a deferred tool is worth
                # a ToolSearch round trip from its NAME alone — which makes a
                # cross-tool table partly a ranking of how self-describing each
                # vendor's tool names happen to be.
                row.update({"status": "ok", "served_tools": served,
                            "served_count": len(served),
                            "served_tool_descriptions": {
                                t.name: (t.description or "") for t in tools}})
                for step in arm.activate:
                    if step["tool"] not in served:
                        row.setdefault("activate", {})[step["tool"]] = "tool-absent"
                        continue
                    try:
                        async with asyncio.timeout(step.get("timeout_seconds", 600)):
                            await s.call_tool(step["tool"], step.get("args") or {})
                        row.setdefault("activate", {})[step["tool"]] = "ok"
                    except Exception as e:  # noqa: BLE001
                        row.setdefault("activate", {})[step["tool"]] = f"failed: {e}"
                if arm.warm and arm.warm["tool"] in served:
                    t0 = time.time()
                    try:
                        async with asyncio.timeout(arm.warm.get("timeout_seconds", 15)):
                            await s.call_tool(arm.warm["tool"], arm.warm.get("args") or {})
                        row["warm_seconds"] = round(time.time() - t0, 1)
                    except Exception as e:  # noqa: BLE001
                        row["warm_seconds"] = round(time.time() - t0, 1)
                        row["warm_abandoned"] = type(e).__name__
        finally:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001,S110
                pass
        return row

    try:
        return asyncio.run(_probe())
    except Exception as e:  # noqa: BLE001
        return {"arm": arm.name, "status": "probe-crashed",
                "error": f"{type(e).__name__}: {e}"}


def run_swe_qa_task(task: dict, condition: dict, config: dict,
                    budget: BudgetTracker,
                    raw_saver: Optional[RawOutputSaver] = None) -> RunMetrics:
    """Run one SWE-QA task under one condition. Handles all errors gracefully."""

    task_id = task.get("id", task.get("instance_id", ""))
    repo_name = task.get("repo", "")

    metrics = RunMetrics(
        task_id=task_id,
        benchmark="swe_qa",
        condition=condition["name"],
        repo=repo_name,
        question_type=task.get("split_name", ""),
        model_used=config["agent"]["model"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Budget gate
    if not budget.check_budget(estimated_cost=1.0):
        metrics.error = "budget_exceeded"
        return metrics

    repos_dir = config["paths"]["repos_dir"]
    # Pinned out-of-tree checkouts (e.g. staged benchmark targets) take
    # precedence over the repos_dir convention; no cloning, no index step.
    override = (config["paths"].get("repo_overrides") or {}).get(repo_name)
    if override:
        repo_path = Path(override).expanduser().resolve()
        if not repo_path.exists():
            metrics.error = f"repo_override_missing: {repo_path}"
            return metrics
    else:
        repo_path = resolve_repo_path(repo_name, repos_dir)
        # Clone if needed
        if not repo_path.exists():
            try:
                ensure_repo_cloned(repo_name, repos_dir)
            except Exception as e:
                metrics.error = f"clone_failed: {e}"
                return metrics

    metrics.repo_commit = get_repo_commit(repo_path)
    metrics.category = task.get("category", "")

    # ---- resolve the arm --------------------------------------------------
    static_mcp_config = None
    try:
        if condition.get("mcp_server"):
            # Third-party arm defined inline by a static config rather than by
            # an arms.yaml entry. Its server points at the indexed source
            # checkout through absolute paths in that config, so it queries
            # `repo_path` directly and needs no arm-owned tree.
            arm, static_mcp_config = _arm_from_mcp_server(
                condition, Path(__file__).resolve().parent.parent)
            tree = repo_path
        else:
            aname = arm_name_for_condition(condition)
            tree = prepare_arm_tree(aname, repo_path, config)
            arm = arm_registry.resolve_arm(
                aname, tree=tree, repo_path=repo_path, repo_name=repo_name,
                arms_file=config.get("arms_file"),
                arms_dir=config.get("arms_dir"),
            )
    except Exception as e:
        metrics.error = f"arm_resolution_failed: {e}"
        return metrics

    metrics.arm = arm.name
    metrics.arm_provenance = arm.provenance()
    metrics.arm_provenance["tree"] = str(tree)
    metrics.prompt_style = config.get("prompt_style", "arm")

    bench_root = Path(__file__).resolve().parent.parent
    mcp_config_path = None
    # Filled from the live server below for MCP arms; stays empty for C0,
    # which has no server to describe.
    served_descriptions: dict = {}
    # E13 countermeasure, switched on per RUN and not per arm: the benchmark's
    # own Stop hook, attached identically to every MCP arm, refusing to end a
    # cell that never called that arm's server. It is stamped on the row
    # because a forced cell and an unforced cell are not the same measurement
    # and no table may mix them silently.
    # A MODE, not a flag: `stop-block` or `pre-guide`. They cost very different
    # amounts and no table may pool them, so the mode itself is what gets
    # stamped rather than a boolean that loses which one ran.
    force_tool_use = (config.get("force_tool_use")
                      or condition.get("force_tool_use") or False)
    metrics.arm_provenance["force_tool_use"] = force_tool_use

    # The coaching this cell was actually given, stamped rather than inferred.
    # `prompt_sent` is the USER prompt; coaching travels by
    # `--append-system-prompt` and left no trace on the row, so "the agent was
    # told to use the tool and did not" was an unfalsifiable claim about the
    # most interesting cells in the run. Length and head, not the whole text:
    # enough to prove which style arrived, cheap enough to carry on every row.
    try:
        _coach = arm.resolved_coaching(metrics.prompt_style)
    except Exception:
        _coach = ""
    metrics.arm_provenance["coaching_chars"] = len(_coach)
    metrics.arm_provenance["coaching_mandatory"] = "REQUIRED, and not optional" in _coach
    settings_path = str(arm_registry.generate_settings(
        arm, bench_root / "mcp_configs", force_tool_use=force_tool_use))
    claude_home = str(arm_registry.prepare_claude_home())

    if arm.uses_mcp:
        # ---- index, once per (arm-that-owns-the-tree, repo) --------------
        try:
            evidence = ensure_arm_index(arm, tree, repo_name, config, metrics)
            metrics.index_evidence = evidence
            if evidence.get("failed"):
                metrics.error = f"indexing_failed: {evidence.get('failed')}"
                return metrics
        except Exception as e:
            metrics.error = f"indexing_error: {e}"
            return metrics

        if static_mcp_config:
            # Third-party arm: mount the hand-written config verbatim rather
            # than generating one. It was pre-flighted and its absolute paths
            # already point at the indexed checkout.
            mcp_config_path = static_mcp_config
        else:
            mcp_config_path = str(arm_registry.generate_mcp_config(
                arm, bench_root / "mcp_configs",
                extra_env=_index_extra_env(arm),
            ))

        # ---- proof of life, BEFORE the agent spends anything -------------
        # Record what the server actually advertised, and run this arm's
        # activation and warm-up steps. Every one of those steps exists because
        # its absence produced a clean, plausible zero rather than an error.
        probe = probe_arm_server(arm, mcp_config_path)
        metrics.served_tools = probe.get("served_tools", [])
        # Captured off the live server so `prompt_style: neutral-described`
        # shows the agent each vendor's own words rather than ours.
        served_descriptions = probe.get("served_tool_descriptions") or {}
        metrics.served_count = probe.get("served_count")
        if probe.get("status") != "ok":
            metrics.error = f"arm_not_alive: {probe.get('status')}: {probe.get('error', '')[:300]}"
            return metrics
        # An activation step that did not succeed is a MISCONFIGURED arm, and
        # the whole reason `activate` is a field is that a missing setup step
        # scores as a bad arm rather than as an error. Serena without
        # `activate_project` answers "No active project" to everything;
        # code-review-graph without `embed_graph_tool` answers with
        # `search_mode: "none"` and retrieves nothing. Both produce a clean
        # 0.000 that looks exactly like a tool that cannot retrieve.
        #
        # This used to be recorded on the probe row and not acted on, so a
        # 1,800-second embed timeout would have let the cell run anyway and
        # billed a full agent turn to measure an unembedded graph.
        bad_activation = {k: v for k, v in (probe.get("activate") or {}).items()
                          if v != "ok"}
        if bad_activation:
            metrics.error = (
                f"arm_activation_failed: {bad_activation}. This arm's setup "
                f"did not complete, so anything it retrieves is not a "
                f"measurement of it."
            )
            return metrics

        missing = [t for t in arm.client_tools
                   if t.split("__")[-1] not in set(metrics.served_tools)]
        if missing:
            # The arm allowlists a tool its server never advertised. That is not
            # a bad arm, it is a misconfigured one, and it scores as the former.
            metrics.error = (
                f"arm_tool_mismatch: allowlisted but not served: {missing}; "
                f"served={metrics.served_tools}"
            )
            return metrics
    else:
        # No server. Scrub the worktree of every other arm's artifacts so the
        # control is a control on disk and not only in the tool allowlist.
        arm_registry.scrub_tree(tree)

    # CLAUDE.md as standing project context. Master wrote this by DEFAULT for
    # repowise arms; here it is explicit opt-in, because standing context only
    # one arm receives is exactly the coaching asymmetry the allowlist and
    # neutral-prompt work exists to remove. No config in the tree relies on
    # the old default: every config that mentions `claude_md` sets it false.
    if condition.get("claude_md"):
        write_repo_claude_md(
            Path(tree), condition.get("repowise_mode", "full"))

    # Build prompt
    question = task.get("question", "")
    prompt = (
        "Answer the following question about this code repository.\n"
        "Be specific and reference actual code files and functions.\n\n"
        f"QUESTION: {question}\n\n"
        "Think step by step. Use the available tools to explore the codebase."
    )
    metrics.prompt_sent = prompt

    # Run agent — dispatch on the configured harness.
    per_task_budget = config.get("budget", {}).get("max_per_task_usd", 2.0)
    harness = config["agent"].get("harness", "claude_code")
    start = time.time()
    if harness == "codex":
        # A second agent harness, so a Layer B row is a claim about repowise
        # rather than about Claude Code. See harness/codex_runner.py for the
        # three ways a Codex number is NOT the same kind of number as a Claude
        # one (computed cost, a shell instead of Read/Grep, a flipped judge).
        from harness.codex_runner import (
            run_codex, build_codex_system_prompt, prepare_codex_home,
        )
        base_system_prompt = (
            "You are answering a question about the code repository in your "
            "current directory. Only read files within the current repository. "
            "Do NOT access files outside the current directory. "
            "Do NOT read any benchmark, test-harness, or evaluation data. "
            "Answer based solely on what you find in the source code."
        )
        output, retries = run_codex(
            prompt=prompt,
            repo_path=str(tree),
            condition=condition,
            model=config["agent"]["model"],
            timeout=config["agent"]["timeout_seconds"],
            arm=arm,
            mcp_config_path=mcp_config_path,
            system_prompt=build_codex_system_prompt(
                base_system_prompt,
                arm.resolved_coaching(metrics.prompt_style, served_descriptions)),
            stream_log_path=str(
                Path(config["paths"]["logs_dir"]) / "streams"
                / f"{task_id}__{condition['name']}.jsonl"
            ),
            codex_home=str(prepare_codex_home()),
        )
    elif harness == "opencode":
        from harness.opencode_runner import (
            run_opencode, get_shared_server, build_opencode_system_prompt,
        )
        output, retries = run_opencode(
            prompt=prompt,
            repo_path=str(tree),
            condition=condition,
            model=config["agent"]["model"],
            timeout=config["agent"]["timeout_seconds"],
            server=get_shared_server(),
            benchmark="swe_qa",
            system_prompt=build_opencode_system_prompt(condition, "swe_qa"),
        )
    else:
        output, retries = run_claude_code(
            prompt=prompt,
            # The arm's OWN tree, never the shared checkout. This is the cwd the
            # agent sees, so it is also what makes the control a control.
            repo_path=str(tree),
            condition=condition,
            model=config["agent"]["model"],
            timeout=config["agent"]["timeout_seconds"],
            max_budget_usd=per_task_budget,
            mcp_config_path=mcp_config_path,
            benchmark="swe_qa",
            arm=arm,
            max_turns=config["agent"].get("max_turns"),
            settings_path=settings_path,
            prompt_style=metrics.prompt_style,
            tool_descriptions=served_descriptions,
            claude_home=claude_home,
            # The tree is already this arm's own and already scrubbed; a second
            # worktree under it would be a worktree of a worktree.
            manage_c0_worktree=False,
            stream_log_path=str(
                Path(config["paths"]["logs_dir"]) / "streams"
                / f"{task_id}__{condition['name']}.jsonl"
            ),
        )
    metrics.wall_clock_seconds = time.time() - start
    metrics.retries = retries

    # Save raw output
    if raw_saver:
        metrics.raw_output_file = raw_saver.save(task_id, condition["name"], output)

    # Parse
    if output.get("is_error") or "error" in output:
        metrics.error = output.get("error", output.get("result", "unknown"))
        if isinstance(metrics.error, str) and len(metrics.error) > 500:
            metrics.error = metrics.error[:500]
        metrics.timed_out = output.get("timed_out", False)
        # Honest cost accounting for hard failures (e.g. error_max_turns): the
        # tokens were spent even though there is no answer. Populate from the
        # parsed result event when present; the row stays an error and is not
        # judged, but its cost/turns count toward the budget and the totals.
        usage = output.get("usage", {})
        metrics.input_tokens = usage.get("input_tokens", 0)
        metrics.output_tokens = usage.get("output_tokens", 0)
        metrics.cache_read_tokens = usage.get("cache_read_input_tokens", 0)
        metrics.cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
        metrics.num_turns = output.get("num_turns", 0)
        metrics.estimated_cost_usd = output.get("total_cost_usd", 0.0)
        metrics.token_source = output.get("token_source", "")
    else:
        usage = output.get("usage", {})
        metrics.input_tokens = usage.get("input_tokens", 0)
        metrics.output_tokens = usage.get("output_tokens", 0)
        metrics.cache_read_tokens = usage.get("cache_read_input_tokens", 0)
        metrics.cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
        metrics.num_turns = output.get("num_turns", 0)
        metrics.task_subagent_calls = output.get("task_subagent_calls", 0)
        metrics.estimated_cost_usd = output.get("total_cost_usd", 0.0)
        metrics.answer = output.get("result", "")
        metrics.session_id = output.get("session_id", "")
        metrics.stop_reason = output.get("stop_reason", "")
        metrics.duration_api_ms = output.get("duration_api_ms", 0)
        # Tool call details (from stream-json)
        metrics.num_tool_calls = output.get("num_tool_calls", 0)
        metrics.files_explored = output.get("files_explored", [])
        metrics.files_edited = output.get("files_edited", [])
        metrics.repowise_tools_called = output.get("repowise_tools_called", [])
        metrics.mcp_tools_issued = output.get("mcp_tools_issued", [])
        metrics.server_tools_called = output.get("server_tools_called", {})
        metrics.mcp_isError_count = output.get("mcp_isError_count", 0)
        metrics.mcp_per_server = output.get("mcp_per_server", {})
        metrics.hook_events = output.get("hook_events", [])
        metrics.hook_injections = output.get("hook_injections", [])
        metrics.models_used = output.get("models_used", [])
        metrics.token_source = output.get("token_source", "")

        # Did the arm actually get used? An MCP arm that finished without ever
        # calling its own server produced a bare-agent run under the arm's name,
        # and every other field on this row reads as a healthy cell.
        if arm.uses_mcp:
            # ISSUED is not USED. A call the server never answered leaves the
            # agent with exactly what a bare agent has, and every other field
            # on the row still reads as a healthy exercised cell — measured on
            # the first Codex repowise cell, where the one `get_answer` came
            # back `user cancelled MCP tool call` in a run with no user in it,
            # the agent fell back to its shell, and the judge scored it 9.0.
            # So the bar is at least one call the server actually answered.
            answered = sum(v.get("ok", 0) for v in (metrics.mcp_per_server or {}).values())
            metrics.arm_exercised = bool(metrics.mcp_tools_issued) and answered > 0
            if metrics.mcp_tools_issued and not answered:
                print(
                    f"  !! {metrics.condition}/{task_id}: arm ISSUED "
                    f"{len(metrics.mcp_tools_issued)} call(s) and the server "
                    f"answered NONE of them ({metrics.mcp_isError_count} "
                    f"errored). This cell measures a bare agent that paid for "
                    f"a round trip.",
                    flush=True,
                )
            if not metrics.arm_exercised and not metrics.mcp_tools_issued:
                print(
                    f"  !! {metrics.condition}/{task_id}: arm NOT EXERCISED — "
                    f"the server advertised {metrics.served_count} tools and the "
                    f"agent called none of them. This cell measures a bare agent.",
                    flush=True,
                )

        # D16, and it is ARM-AWARE because an arm may legitimately declare
        # hooks.
        #
        # The original check read any injection as contamination, which was
        # right while no arm declared one: the only thing that could inject was
        # the operator's own `~/.claude/settings.json`, and it did, into the
        # bare control. On an arm whose `hooks:` block IS the treatment,
        # injection is the thing being measured, and the failure runs the other
        # way: a declared hook that injects NOTHING is the silent no-op that
        # publishes as "hooks make no difference". Both directions print, and
        # the contamination check stays exactly as loud as it was for every arm
        # that declares nothing, which is still every arm but `repowise-hooks`.
        declared_hooks = list(
            (metrics.arm_provenance or {}).get("hooks_declared") or []
        )
        # The run's own forcing hook is declared too, just by the experiment
        # rather than by the arm. Without this a forced cell would trip the
        # contamination alarm on the very mechanism the run switched on.
        _mode = (metrics.arm_provenance or {}).get("force_tool_use")
        if _mode:
            declared_hooks.append(f"force_tool_use:{_mode}")
        if metrics.hook_injections and not declared_hooks:
            print(
                f"  !! {metrics.condition}/{task_id}: "
                f"{len(metrics.hook_injections)} hook(s) INJECTED CONTEXT into "
                f"this cell. The arm declared NO hooks, so it was not run in "
                f"the pinned environment (finding D16).",
                flush=True,
            )

            # Master's ATTACH-GUARD under its own name, so analysis written
            # against either vocabulary reads the same cell the same way. It
            # is derived from `arm_exercised` rather than recomputed: ours is
            # the stricter test (it requires a call the server ANSWERED, not
            # merely one that returned without error), and two guards that
            # can disagree are worse than one.
            metrics.attach_guard_fired = not metrics.arm_exercised
        elif declared_hooks and not metrics.hook_injections:
            # Not always a fault, so it says what it saw rather than what it
            # concluded. Zero injections is the SILENT NO-OP when the hook was
            # supposed to speak (repowise's shipped command exits 0 in silence
            # when the binary is off PATH), and it is the CORRECT outcome for
            # the forcing hook on a cell that called its tool unprompted. The
            # two are told apart by whether the arm was exercised, which is on
            # the same row.
            print(
                f"  !! {metrics.condition}/{task_id}: hooks declared on "
                f"{declared_hooks}, {len(metrics.hook_events)} fired, NONE "
                f"injected. arm_exercised={metrics.arm_exercised}. Silence is "
                f"a no-op hook if the treatment was meant to speak here, and "
                f"the intended outcome if it was a guard that found nothing "
                f"to correct.",
                flush=True,
            )

    metrics.compute_derived()

    # Judge
    if metrics.answer and not metrics.error:
        gold_answer = task.get("answer", task.get("gold_answer", ""))
        judge_model = _resolve_judge_model(config)
        metrics.judge_model = judge_model
        judge_start = time.time()
        metrics.judge_scores = judge_answer(
            question=question,
            gold_answer=gold_answer,
            agent_answer=metrics.answer,
            judge_model=judge_model,
            category=task.get("category"),
        )
        metrics.judge_time_seconds = time.time() - judge_start

    budget.record(metrics.estimated_cost_usd, task_id)
    return metrics
