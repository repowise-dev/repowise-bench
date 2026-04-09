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
# the repowise checkout root.
_REPOWISE_ROOT = Path(__file__).resolve().parents[2]
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
    "PYTHONPATH": os.pathsep.join(
        [str(p) for p in _REPOWISE_PKG_SRCS]
        + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
    ),
}

# Argv prefix for invoking the local repowise CLI.
_REPOWISE_CMD = [sys.executable, "-m", "repowise.cli.main"]

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
                       include_indices: Optional[list] = None) -> list:
    """
    Load SWE-QA tasks from HuggingFace-downloaded JSON or directly from HF.

    Each task gets: id, repo (GitHub org/name), question, answer, split_name.
    """
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


# ---------------------------------------------------------------------------
# Repowise indexing + MCP config
# ---------------------------------------------------------------------------

def generate_mcp_config(repo_path: Path, bench_root: Path) -> Path:
    """Write per-repo MCP config JSON. Returns absolute path."""
    config_dir = bench_root / "mcp_configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    repo_abs = str(repo_path.resolve()).replace("\\", "/")
    config_name = f"{repo_path.parent.name}_{repo_path.name}.json"
    config_path = config_dir / config_name

    mcp_config = {
        "mcpServers": {
            "repowise": {
                "command": sys.executable,
                "args": [
                    "-m", "repowise.cli.main",
                    "mcp", repo_abs, "--transport", "stdio",
                ],
                "env": {
                    "PYTHONPATH": _UTF8_ENV["PYTHONPATH"],
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
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


def get_c0_worktree(repo_path: Path) -> Path:
    """Return a git worktree path for C0 runs.

    Git worktrees share the repo's object store (fast, no full copy) but have
    their own working directory — untracked files like `.repowise/` are NOT
    present.  This means a C0 agent running in cwd=worktree physically cannot
    access any repowise artifacts left by a prior C1/C2 run, without any
    delete-and-restore dance.

    The worktree is created once per repo and reused across tasks.  If the
    repo's HEAD has moved (e.g. after a `git pull`), the existing worktree is
    torn down and recreated.
    """
    org = repo_path.parent.name
    wt_path = _C0_WORKTREES_ROOT / org / repo_path.name
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
    # before handing the path to a C0 agent.
    for leak in (".repowise", ".mcp.json", "CLAUDE.md"):
        p = wt_path / leak
        if p.exists():
            if p.is_dir():
                _safe_rmtree(p)
            else:
                p.unlink()

    return wt_path


def index_repo(repo_name: str, repos_dir: str, index_dir: str,
               mode: str, repowise_bin: str, doc_model: str) -> tuple:
    """Run repowise init from the local checkout. Returns (success, time_seconds).

    Uses --resume so a previous partial run continues instead of restarting.
    Caps git history at 200 commits and LLM concurrency at 3 (full mode only).
    """
    del repowise_bin  # ignored — we always use the local checkout via _REPOWISE_CMD
    repo_path = resolve_repo_path(repo_name, repos_dir)
    cache_key = f"{repo_name.replace('/', '_')}_{mode}"
    cache_dir = Path(index_dir) / cache_key

    # Restore from cache (mode-specific)
    if cache_dir.exists():
        cached_idx = cache_dir / ".repowise"
        dest_idx = repo_path / ".repowise"
        if cached_idx.exists():
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

MAX_RETRIES = 6


def run_claude_code(prompt: str, repo_path: str, condition: dict,
                    model: str, timeout: int,
                    max_budget_usd: float = 2.0,
                    mcp_config_path: Optional[str] = None,
                    benchmark: str = "swe_qa") -> tuple:
    """
    Run Claude Code with retry on rate limits.
    Returns (output_dict, retries_used).

    benchmark: "swe_qa" or "swe_bench" — selects the right system prompt.
    """
    # SWE-QA is read-only code understanding — no Bash needed.
    # Bash lets the agent escape the repo (read arbitrary files, call repowise CLI
    # manually, access the benchmark's own data/tasks.json answer key).
    base_tools = "Read,Grep,Glob" if benchmark == "swe_qa" else "Read,Grep,Glob,Bash,Edit,Write"

    # System prompt applied to ALL conditions — prevents repo escape
    base_system_prompt = (
        "You are answering a question about the code repository in your current directory. "
        "Only read files within the current repository. "
        "Do NOT access files outside the current directory. "
        "Do NOT use ToolSearch or ListMcpResourcesTool. "
        "Answer based solely on what you find in the source code."
    )

    # Block everything that isn't explicitly needed for the condition.
    # Personal MCP servers (Notion, Gmail, Canva, Invideo…) bleed in from the
    # user's global Claude Code config — block them unconditionally so no
    # condition can reach outside the repo.
    # ReadMcpResourceTool is a *top-level* tool (not under mcp__*) that lets
    # the agent read any MCP resource URI (e.g. `repowise://context?...`).
    # Block it unconditionally so `mcp__*` disallow can't be bypassed through
    # the resource namespace. Same for ListMcpResourcesTool/ToolSearch.
    disallowed = (
        "ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,"
        "mcp__claude_ai_*"
    )

    if not condition.get("repowise_enabled"):
        # C0 — no MCP servers at all. Run in a git worktree so .repowise/
        # and .mcp.json from prior runs are physically absent.  If worktree
        # creation fails we FAIL LOUDLY rather than fall back to the real
        # repo dir (that's how C0 got silently contaminated before).
        disallowed += ",mcp__*"
        repo_path = str(get_c0_worktree(Path(repo_path)))
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
        "--append-system-prompt", base_system_prompt,
        "--disallowed-tools", disallowed,
    ]

    allowed_tools = base_tools
    if condition.get("repowise_enabled"):
        mode = condition.get("repowise_mode", "full")
        if mode == "index-only":
            allowed_tools += TOOLS_INDEX_ONLY
            # CLAUDE.md in the repo already carries full tool docs and workflow
            # for C1. Only append a short reminder via system-prompt so the
            # agent gets the nudge even if CLAUDE.md is somehow missing.
            system_prompt = (SWEBENCH_PROMPT_INDEX_ONLY if benchmark == "swe_bench"
                             else "Use the repowise tools listed in CLAUDE.md before reading source.")
        else:
            allowed_tools += TOOLS_FULL
            system_prompt = (SWEBENCH_PROMPT_FULL if benchmark == "swe_bench"
                             else SWEQA_PROMPT_FULL)
        if mcp_config_path:
            # --strict-mcp-config: ignore user-global / project-level servers
            # (Figma/Notion/Apollo/Gmail/... from ~/.claude.json) and only
            # mount the repowise server from our config.
            cmd.extend(["--strict-mcp-config", "--mcp-config", mcp_config_path])
        cmd.extend(["--append-system-prompt", system_prompt])
    else:
        # C0 — mount NO MCP servers at all. An empty strict config suppresses
        # both the user's global servers and any project-level .mcp.json that
        # repowise itself may have written into the repo.
        empty_cfg_path = _BENCH_ROOT / "configs" / "_empty_mcp.json"
        if not empty_cfg_path.exists():
            empty_cfg_path.parent.mkdir(parents=True, exist_ok=True)
            empty_cfg_path.write_text('{"mcpServers": {}}')
        cmd.extend(["--strict-mcp-config", "--mcp-config", str(empty_cfg_path)])

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


def judge_answer(question: str, gold_answer: str, agent_answer: str,
                 judge_model: str) -> dict:
    """Score agent answer via LLM judge. Retries on rate limits."""
    judge_prompt = f"""You are evaluating an AI agent's answer to a repository-level code question.

QUESTION:
{question}

REFERENCE ANSWER:
{gold_answer}

AGENT ANSWER:
{agent_answer}

Score the agent's answer on each dimension (1-10 scale):
- Correctness: Is the answer factually accurate?
- Completeness: Does it address all aspects of the question?
- Relevance: Does it directly answer what was asked?
- Clarity: Is it clear and easy to understand?
- Reasoning: Does it show logical coherence and proper code reasoning?

Respond with ONLY a JSON object like:
{{"correctness": 7, "completeness": 6, "relevance": 8, "clarity": 9, "reasoning": 7}}
"""

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
                 "--model", judge_model, "--max-budget-usd", "0.15"],
                capture_output=True, text=True, timeout=90,
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
    repo_path = resolve_repo_path(repo_name, repos_dir)

    # Clone if needed
    if not repo_path.exists():
        try:
            ensure_repo_cloned(repo_name, repos_dir)
        except Exception as e:
            metrics.error = f"clone_failed: {e}"
            return metrics

    metrics.repo_commit = get_repo_commit(repo_path)

    # Index + MCP config for repowise conditions
    mcp_config_path = None
    if condition.get("repowise_enabled"):
        mode = condition.get("repowise_mode", "full")
        try:
            ok, idx_time = index_repo(
                repo_name, repos_dir,
                config["repowise"]["index_dir"],
                mode,
                config["repowise"]["binary"],
                config["repowise"]["doc_model"],
            )
            metrics.index_time_seconds = idx_time
            if not ok:
                metrics.error = "indexing_failed"
                return metrics
        except Exception as e:
            metrics.error = f"indexing_error: {e}"
            return metrics

        bench_root = Path(__file__).resolve().parent.parent
        mcp_cfg = generate_mcp_config(repo_path, bench_root)
        mcp_config_path = str(mcp_cfg)

        # Write CLAUDE.md into the repo so Claude Code loads it as project
        # context before the agent prompt. Untracked → absent from C0 worktree.
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

    # Run agent
    per_task_budget = config.get("budget", {}).get("max_per_task_usd", 2.0)
    start = time.time()
    output, retries = run_claude_code(
        prompt=prompt,
        repo_path=str(repo_path),
        condition=condition,
        model=config["agent"]["model"],
        timeout=config["agent"]["timeout_seconds"],
        max_budget_usd=per_task_budget,
        mcp_config_path=mcp_config_path,
        benchmark="swe_qa",
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
        )
        metrics.judge_time_seconds = time.time() - judge_start

    budget.record(metrics.estimated_cost_usd, task_id)
    return metrics
