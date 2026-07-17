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
_REQUIRED_REPOWISE_BRANCH = "feat/pipeline-overhaul"

def _verify_local_repowise() -> None:
    """Fail loudly if the local checkout is missing or on the wrong branch."""
    if not _REPOWISE_ROOT.exists():
        raise RuntimeError(
            f"Local repowise checkout not found at {_REPOWISE_ROOT}. "
            f"Clone repowise into the parent directory of repowise-bench."
        )
    for src in _REPOWISE_PKG_SRCS:
        if not src.exists():
            raise RuntimeError(f"Expected repowise source dir missing: {src}")
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(_REPOWISE_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
    except Exception as e:
        raise RuntimeError(f"Could not read repowise branch: {e}")
    if branch != _REQUIRED_REPOWISE_BRANCH:
        print(
            f"  [warn] local repowise is on branch '{branch}', "
            f"expected '{_REQUIRED_REPOWISE_BRANCH}'"
        )

_verify_local_repowise()

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
    "REPOWISE_EMBEDDER", "REPOWISE_DOC_MODEL",
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
                       tasks_file=None) -> list:
    """
    Load SWE-QA tasks from HuggingFace-downloaded JSON or directly from HF.

    Each task gets: id, repo (GitHub org/name), question, answer, split_name.
    ``tasks_file`` (str or list of str, relative paths resolved against the
    bench root) bypasses the swe_qa dataset convention and loads explicit
    frozen question files, concatenated in order.
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
    repo_path: Path, bench_root: Path, profile: Optional[str] = None
) -> Path:
    """Write per-repo MCP config JSON. Returns absolute path.

    ``profile`` (e.g. "lean") makes the server advertise only a curated tool
    surface via ``repowise mcp --tools <profile>``, so unused tool schemas
    never enter the agent's context. ``None`` advertises the default surface.
    The profile is baked into the config filename so full and lean arms get
    distinct server launches against the same restored index.
    """
    config_dir = bench_root / "mcp_configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    repo_abs = str(repo_path.resolve()).replace("\\", "/")
    suffix = f"_{profile}" if profile else ""
    config_name = f"{repo_path.parent.name}_{repo_path.name}{suffix}.json"
    config_path = config_dir / config_name

    server_args = _REPOWISE_CMD[1:] + ["mcp", repo_abs, "--transport", "stdio"]
    if profile:
        # Current CLI spells profiles through --tools (e.g. --tools lean);
        # the old --profile flag no longer exists and would kill the server
        # at mount, silently degrading the arm to a bare agent.
        server_args += ["--tools", profile]

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
    repo_path = resolve_repo_path(repo_name, repos_dir)
    cache_key = f"{repo_name.replace('/', '_')}_{mode}"
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

# C2 lean: the agent-lean server profile (`repowise mcp --tools lean`), which
# advertises five tools — the four the agent actually reached for on flask48
# v2 (get_symbol, get_answer, get_context, search_codebase) plus get_risk.
# ONLY those schemas are advertised, so the per-task schema "tax" the other
# tools would add never enters the agent's context. Same index as full; only
# the served surface differs.
TOOLS_LEAN = (
    ",mcp__repowise__get_answer"
    ",mcp__repowise__get_symbol"
    ",mcp__repowise__search_codebase"
    ",mcp__repowise__get_context"
    ",mcp__repowise__get_risk"
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


def run_claude_code(prompt: str, repo_path: str, condition: dict,
                    model: str, timeout: int,
                    max_budget_usd: float = 2.0,
                    mcp_config_path: Optional[str] = None,
                    benchmark: str = "swe_qa",
                    max_turns: Optional[int] = None) -> tuple:
    """
    Run Claude Code with retry on rate limits.
    Returns (output_dict, retries_used).

    benchmark: "swe_qa" or "swe_bench" — selects the right system prompt.
    """
    # SWE-QA is read-only code understanding — no Bash by default.
    # Bash lets the agent escape the repo (read arbitrary files, call repowise CLI
    # manually, access the benchmark's own data/tasks.json answer key), so it is
    # opt-in per condition via `enable_bash` (the long investigation arm), where
    # it is needed to run the test/git/grep commands distill compresses.
    if benchmark == "swe_qa":
        base_tools = "Read,Grep,Glob,Bash" if condition.get("enable_bash") else "Read,Grep,Glob"
    else:
        base_tools = "Read,Grep,Glob,Bash,Edit,Write"

    repowise_enabled = bool(condition.get("repowise_enabled"))
    # Third-party MCP arm: a hand-written static config mounted verbatim.
    # {"prefix": "serena", "config": "/abs/path/serena_flask.json"} — the
    # prefix names the server inside the config (tools appear as
    # mcp__<prefix>__*) and drives the attach-guard.
    mcp_server = condition.get("mcp_server")
    has_mcp = repowise_enabled or bool(mcp_server)

    # System prompt applied to ALL conditions — prevents repo escape.
    # Modern Claude Code DEFERS MCP tool schemas: MCP tools are not in
    # the initial tool list, they are loaded on demand via ToolSearch. So MCP
    # arms MUST be allowed to use ToolSearch or the tools are unreachable; C0
    # (no MCP) keeps it blocked. ListMcpResourcesTool / ReadMcpResourceTool are
    # the repo-escape vectors (they read arbitrary MCP resource URIs) and stay
    # blocked everywhere.
    base_system_prompt = (
        "You are answering a question about the code repository in your current directory. "
        "Only read files within the current repository. "
        "Do NOT access files outside the current directory. "
        "Do NOT read any benchmark, test-harness, or evaluation data. "
        "Do NOT use ListMcpResourcesTool or ReadMcpResourceTool. "
        + ("" if has_mcp else "Do NOT use ToolSearch. ")
        + "Answer based solely on what you find in the source code."
    )

    # ListMcpResourcesTool / ReadMcpResourceTool are top-level tools (not under
    # mcp__*) that read any MCP resource URI — block them unconditionally so the
    # mcp__* disallow can't be bypassed through the resource namespace. Personal
    # MCP servers (Notion, Gmail, …) bleed in from the user's global config;
    # --strict-mcp-config already excludes them, and mcp__claude_ai_* belt-and-
    # braces blocks the hosted ones.
    disallowed = "ListMcpResourcesTool,ReadMcpResourceTool,mcp__claude_ai_*"

    # Isolation. Any arm can run in a clean git worktree (no .repowise/,
    # .serena/, .codegraph/, CLAUDE.md, or .mcp.json from other arms' setup);
    # MCP servers keep pointing at the indexed source checkout via absolute
    # paths in their configs, so the served index is unaffected. `worktree_variant`
    # gives an arm a private worktree for arm-specific injected files.
    # If worktree creation fails we FAIL LOUDLY rather than fall back to the
    # real repo dir (that's how C0 got silently contaminated before).
    if not has_mcp or condition.get("clean_worktree"):
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

    if not has_mcp:
        # C0 — no MCP servers at all.
        disallowed += ",ToolSearch,mcp__*"
    elif not repowise_enabled:
        pass  # third-party arm: tool allow-list handled below
    else:
        mode = condition.get("repowise_mode", "full")
        # Block every repowise tool that is NOT in the allowed list for this
        # mode, so the agent never wastes a turn attempting an unavailable tool.
        if mode == "index-only":
            # Block all repowise tools not in TOOLS_INDEX_ONLY
            disallowed += (
                ",mcp__repowise__search_codebase"
                ",mcp__repowise__get_overview"
                ",mcp__repowise__get_architecture_diagram"
                ",mcp__repowise__get_dead_code"
                ",mcp__repowise__update_decision_records"
            )
        elif mode == "lean":
            # The server (launched with --profile core) advertises only the four
            # core tools, so there is nothing else to block — the unused schemas
            # never reach the client in the first place. This is the whole point
            # of the lean arm: cut the schema tax at the source, not via the
            # client allow-list.
            pass
        else:
            # C2 full — block only the genuinely useless ones
            disallowed += (
                ",mcp__repowise__get_architecture_diagram"
                ",mcp__repowise__get_dead_code"
                ",mcp__repowise__update_decision_records"
            )

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
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

    allowed_tools = base_tools
    if mcp_server and not repowise_enabled:
        # Third-party MCP arm. Mount exactly the hand-written config, allow
        # exactly that server's tools, and keep the system prompt NEUTRAL:
        # it names which tools exist and nothing else, so no arm gets
        # per-tool coaching the others lack.
        prefix = mcp_server["prefix"]
        allowed_tools += f",ToolSearch,mcp__{prefix}"
        cmd.extend(["--strict-mcp-config", "--mcp-config",
                    str(mcp_server["config"])])
        system_prompt_parts.append(NEUTRAL_MCP_PROMPT.format(prefix=prefix))
    elif condition.get("repowise_enabled"):
        # Deferred MCP tools are loaded via ToolSearch — allow it so the agent
        # can pull the repowise tool schemas into context on first use.
        allowed_tools += ",ToolSearch"
        mode = condition.get("repowise_mode", "full")
        if mode == "index-only":
            allowed_tools += TOOLS_INDEX_ONLY
            # CLAUDE.md in the repo already carries full tool docs and workflow
            # for C1. Only append a short reminder via system-prompt so the
            # agent gets the nudge even if CLAUDE.md is somehow missing.
            system_prompt = (SWEBENCH_PROMPT_INDEX_ONLY if benchmark == "swe_bench"
                             else "Use the repowise tools listed in CLAUDE.md before reading source.")
        elif mode == "lean":
            # Same four-tool workflow as full (the SWE-QA full prompt only ever
            # references get_answer / get_symbol / get_context / search_codebase),
            # but the server advertises just those four — so we get full-arm
            # behaviour at a fraction of the schema cost.
            allowed_tools += TOOLS_LEAN
            system_prompt = (SWEBENCH_PROMPT_FULL if benchmark == "swe_bench"
                             else SWEQA_PROMPT_FULL)
        else:
            allowed_tools += TOOLS_FULL
            system_prompt = (SWEBENCH_PROMPT_FULL if benchmark == "swe_bench"
                             else SWEQA_PROMPT_FULL)
        if mcp_config_path:
            # --strict-mcp-config: ignore user-global / project-level servers
            # (Figma/Notion/Apollo/Gmail/... from ~/.claude.json) and only
            # mount the repowise server from our config.
            cmd.extend(["--strict-mcp-config", "--mcp-config", mcp_config_path])
        # Neutral-comparison mode: the repowise arm gets the SAME single
        # sentence as every competitor arm instead of the repowise-specific
        # workflow prompt, so no arm is coached more than another.
        if condition.get("neutral_prompt"):
            system_prompt = NEUTRAL_MCP_PROMPT.format(prefix="repowise")
        system_prompt_parts.append(system_prompt)
    else:
        # C0 — mount NO MCP servers at all. An empty strict config suppresses
        # both the user's global servers and any project-level .mcp.json that
        # repowise itself may have written into the repo.
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

    for attempt in range(MAX_RETRIES):
        try:
            result = subprocess.run(
                cmd, cwd=repo_path, capture_output=True, text=True,
                timeout=timeout, env=_UTF8_ENV, encoding="utf-8", errors="replace"
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
                    "server_tools_called": parsed.get("server_tools_called", {}),
                    "token_source": parsed.get("token_source", ""),
                    # Keep raw lines for saving
                    "_raw_stream_lines": lines,
                }

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
                return {
                    "error": err,
                    "returncode": result.returncode,
                    "_raw_stream_lines": raw_lines,
                }, attempt

        except subprocess.TimeoutExpired:
            return {"error": "timeout", "timed_out": True}, attempt
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


# Per-category additions to the judge rubric. history-why exists because a
# generic rubric rewards fluent invention: a confident, plausible, WRONG
# rationale reads as a good answer unless the judge is told groundedness in
# the actual historical reason is the thing being scored.
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


def build_judge_prompt(question: str, gold_answer: str, agent_answer: str,
                       category: Optional[str] = None) -> str:
    """Blind judge prompt; the judge never sees condition labels."""
    rubric = CATEGORY_RUBRICS.get(category or "", "")
    return f"""You are evaluating an AI agent's answer to a repository-level code question.

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


def judge_answer(question: str, gold_answer: str, agent_answer: str,
                 judge_model: str, category: Optional[str] = None) -> dict:
    """Score agent answer via LLM judge. Retries on rate limits."""
    judge_prompt = build_judge_prompt(question, gold_answer, agent_answer, category)

    # Try Anthropic SDK first (if API key available)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=judge_model, max_tokens=200, temperature=0.0,
                messages=[{"role": "user", "content": judge_prompt}]
            )
            return _extract_json_scores(response.content[0].text.strip())
        except Exception:
            pass

    # Fall back to Claude CLI with retry
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["claude", "-p", judge_prompt, "--output-format", "json",
                 "--model", judge_model, "--max-budget-usd", "0.40"],
                capture_output=True, text=True, timeout=150,
                env=_UTF8_ENV, encoding="utf-8", errors="replace"
            )
            if result.returncode == 0 and result.stdout.strip():
                output = json.loads(result.stdout)
                if output.get("is_error"):
                    err = output.get("result", "")
                    if is_rate_limit_error(err):
                        backoff_sleep(attempt, base=20.0)
                        continue
                    return {"error": err[:200]}
                return _extract_json_scores(output.get("result", ""))
            err = result.stderr[:300]
            if is_rate_limit_error(err) or not err.strip():
                # Empty stderr on a non-zero exit is a transient CLI failure
                # (observed on long judge prompts); retry rather than losing
                # the row's score.
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

    # Index + MCP config for repowise conditions
    mcp_config_path = None
    if condition.get("mcp_server"):
        # Third-party arm: the static config is hand-written and pre-flighted;
        # resolve its path against the bench root and pass it through.
        server = dict(condition["mcp_server"])
        cfg_path = Path(server["config"]).expanduser()
        if not cfg_path.is_absolute():
            cfg_path = Path(__file__).resolve().parents[1] / cfg_path
        if not cfg_path.exists():
            metrics.error = f"mcp_config_missing: {cfg_path}"
            return metrics
        condition = {**condition, "mcp_server": {**server, "config": str(cfg_path)}}
    elif condition.get("repowise_enabled"):
        mode = condition.get("repowise_mode", "full")
        # The served tool surface (full vs lean) is orthogonal to how the repo
        # is indexed. "lean" reuses the same full doc+graph index as "full" and
        # differs only in which tool schemas the MCP server advertises, so it
        # must NOT trigger a separate (re-)index.
        index_mode = "index-only" if mode == "index-only" else "full"
        served_profile = "lean" if mode == "lean" else None
        if config["repowise"].get("assume_indexed"):
            # Pinned pre-indexed checkout (repo_overrides): the index in the
            # tree IS the experiment artifact; re-indexing would move it.
            if not (repo_path / ".repowise").is_dir():
                metrics.error = f"assume_indexed_but_no_index: {repo_path}"
                return metrics
        else:
            try:
                ok, idx_time = index_repo(
                    repo_name, repos_dir,
                    config["repowise"]["index_dir"],
                    index_mode,
                    config["repowise"]["binary"],
                    config["repowise"]["doc_model"],
                    provider=config["repowise"].get("provider"),
                    embedder=config["repowise"].get("embedder"),
                )
                metrics.index_time_seconds = idx_time
                if not ok:
                    metrics.error = "indexing_failed"
                    return metrics
            except Exception as e:
                metrics.error = f"indexing_error: {e}"
                return metrics

        bench_root = Path(__file__).resolve().parent.parent
        mcp_cfg = generate_mcp_config(repo_path, bench_root, profile=served_profile)
        mcp_config_path = str(mcp_cfg)

        # Write CLAUDE.md into the repo so Claude Code loads it as project
        # context before the agent prompt. Untracked → absent from any clean
        # worktree, so arms isolated via worktrees never see it. Honor the
        # condition's claude_md flag; the historical unconditional write is
        # kept as the default for older configs.
        if condition.get("claude_md", True):
            write_repo_claude_md(repo_path, mode)

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
    if harness == "opencode":
        from harness.opencode_runner import (
            run_opencode, get_shared_server, build_opencode_system_prompt,
        )
        output, retries = run_opencode(
            prompt=prompt,
            repo_path=str(repo_path),
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
            repo_path=str(repo_path),
            condition=condition,
            model=config["agent"]["model"],
            timeout=config["agent"]["timeout_seconds"],
            max_budget_usd=per_task_budget,
            mcp_config_path=mcp_config_path,
            benchmark="swe_qa",
            max_turns=config["agent"].get("max_turns"),
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
        metrics.server_tools_called = output.get("server_tools_called", {})
        metrics.token_source = output.get("token_source", "")
        # ATTACH-GUARD: an arm that mounted a server but never successfully
        # called it degraded to a bare-agent run — the row is kept (raw data
        # is never discarded) but flagged so no aggregation can count it as
        # evidence about the server.
        attach_prefix = None
        if condition.get("mcp_server"):
            attach_prefix = condition["mcp_server"]["prefix"]
        elif condition.get("repowise_enabled"):
            attach_prefix = "repowise"
        if attach_prefix is not None:
            metrics.attach_guard_fired = not metrics.server_tools_called.get(
                attach_prefix)

    metrics.compute_derived()

    # Judge
    if metrics.answer and not metrics.error:
        gold_answer = task.get("answer", task.get("gold_answer", ""))
        judge_model = config.get("evaluation", {}).get(
            "judge_model", config["agent"]["model"]
        )
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
