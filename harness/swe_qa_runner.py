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
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from harness.metrics import (
    RunMetrics, parse_claude_code_output, BudgetTracker,
    ResultWriter, RawOutputSaver,
)

# Force UTF-8 for all subprocesses (Windows cp1252 breaks on emoji/unicode)
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

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
                       repos: Optional[list] = None) -> list:
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

    # Limit
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
                "command": "repowise",
                "args": ["mcp", repo_abs, "--transport", "stdio"]
            }
        }
    }
    with open(config_path, "w") as f:
        json.dump(mcp_config, f, indent=2)
    return config_path.resolve()


def index_repo(repo_name: str, repos_dir: str, index_dir: str,
               mode: str, repowise_bin: str, doc_model: str) -> tuple:
    """Run repowise init. Returns (success, time_seconds)."""
    repo_path = resolve_repo_path(repo_name, repos_dir)
    cache_key = f"{repo_name.replace('/', '_')}_{mode}"
    cache_dir = Path(index_dir) / cache_key

    # Restore from cache (mode-specific)
    if cache_dir.exists():
        cached_idx = cache_dir / ".repowise"
        dest_idx = repo_path / ".repowise"
        if cached_idx.exists():
            # Replace any existing index with the correct mode's cache
            if dest_idx.exists():
                shutil.rmtree(str(dest_idx))
            shutil.copytree(str(cached_idx), str(dest_idx))
        return True, 0.0

    start = time.time()
    cmd = [repowise_bin, "init", "-y"]
    if mode == "index-only":
        cmd.append("--index-only")

    # Force DB to repo-local .repowise/wiki.db so the MCP server can find it
    rw_dir = repo_path.resolve() / ".repowise"
    rw_dir.mkdir(parents=True, exist_ok=True)
    local_db = (rw_dir / "wiki.db").as_posix()
    env = {
        **_UTF8_ENV,
        "REPOWISE_DOC_MODEL": doc_model,
        "REPOWISE_DB_URL": f"sqlite+aiosqlite:///{local_db}",
    }

    # Full mode on large repos (django, sympy) needs more time for LLM doc generation
    index_timeout = 3600 if mode == "full" else 900  # 60 min full, 15 min index-only
    print(f"  Indexing {repo_name} (mode={mode})...")
    result = subprocess.run(
        cmd, cwd=str(repo_path), capture_output=True, text=True,
        env=env, timeout=index_timeout, encoding="utf-8", errors="replace"
    )
    elapsed = time.time() - start

    if result.returncode == 0:
        cache_dir.mkdir(parents=True, exist_ok=True)
        src = repo_path / ".repowise"
        if src.exists():
            shutil.copytree(str(src), str(cache_dir / ".repowise"))
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
Use them to get structural and git context — but do NOT spend turns on tools that aren't listed below.

Available repowise tools (use ONLY these — no others exist):
- mcp__repowise__get_risk(targets=["path/to/file.py"]) — hotspot score, co-change partners,
  ownership, churn. Co-change partners reveal files that usually change together — check them.
- mcp__repowise__get_context(targets=["path/to/file.py"]) — symbols, imports, dependents.
- mcp__repowise__get_dependency_path(source="path/a.py", target="path/b.py") — import chain.
- mcp__repowise__get_why(query="path/to/file.py") — significant past commits for a file.
- mcp__repowise__get_overview() — entry points (may be sparse in this mode).

Workflow: Read the issue → identify suspect file(s) → call get_risk to find co-change partners →
use Read/Grep to examine code → make your fix. Keep it efficient — 1-2 repowise calls max.
"""

SWEBENCH_PROMPT_FULL = """You have access to repowise codebase intelligence tools alongside standard tools.
These provide documentation, semantic search, graph intelligence, and git history.

Available repowise tools:
- mcp__repowise__search_codebase(query="...") — semantic search across the codebase. Start here.
- mcp__repowise__get_context(targets=["path/to/file.py"]) — documentation, symbols, imports, dependents.
- mcp__repowise__get_risk(targets=["path/to/file.py"]) — hotspot score, co-change partners, churn.
- mcp__repowise__get_overview() — project summary, entry points, architecture.
- mcp__repowise__get_dependency_path(source="a.py", target="b.py") — import chain between modules.
- mcp__repowise__get_why(query="path/to/file.py") — past commits and design rationale.

Workflow: search_codebase to find relevant files → get_context/get_risk on those files →
Read/Grep to examine code → make your fix. Be efficient — a few targeted repowise calls, then code.
"""

# -- SWE-QA (code understanding) --

SWEQA_PROMPT_INDEX_ONLY = """You have access to repowise codebase intelligence tools alongside standard tools.
Use them for structural and git context when answering the question.

Available repowise tools (use ONLY these — no others exist):
- mcp__repowise__get_context(targets=["path/to/file.py"]) — symbols, imports, dependents.
  Call this once you know which file(s) are relevant to the question.
- mcp__repowise__get_risk(targets=["path/to/file.py"]) — hotspot score, co-change partners, ownership.
- mcp__repowise__get_dependency_path(source="path/a.py", target="path/b.py") — import chain.
- mcp__repowise__get_why(query="path/to/file.py") — significant past commits for a file.

Workflow: Use Grep/Glob to find relevant files first → call get_context or get_risk on them →
Read source for details → answer the question. Keep it efficient — 1-2 repowise calls max.
Do NOT call tools not in the list above — they are not available.
"""

SWEQA_PROMPT_FULL = """You have access to repowise codebase intelligence tools alongside standard tools.
These provide documentation, semantic search, graph intelligence, and git history.

Available repowise tools:
- mcp__repowise__search_codebase(query="...") — semantic search across the codebase.
  Start here to find files relevant to the question.
- mcp__repowise__get_context(targets=["path/to/file.py"]) — documentation, symbols, imports, dependents.
- mcp__repowise__get_risk(targets=["path/to/file.py"]) — hotspot score, co-change partners, ownership.
- mcp__repowise__get_overview() — project summary, entry points, architecture.
- mcp__repowise__get_dependency_path(source="a.py", target="b.py") — import chain between modules.
- mcp__repowise__get_why(query="path/to/file.py") — past commits and design rationale.

Workflow: search_codebase to find relevant files → get_context on key files →
Read source for details → answer the question. Be efficient — a few targeted calls, then answer.
"""

# -- Tool lists per mode --

# Index-only (C1): only tools that return real data in this mode.
# Excludes search_codebase (needs full index), dead_code, architecture_diagram.
# Also excludes get_overview (returns empty in index-only — wastes a turn).
TOOLS_INDEX_ONLY = (
    ",mcp__repowise__get_risk"
    ",mcp__repowise__get_dependency_path"
    ",mcp__repowise__get_context"
    ",mcp__repowise__get_why"
)

# Full mode (C2): all useful tools. Still excludes dead_code and architecture_diagram
# (irrelevant for bug fixing and Q&A — wastes turns).
TOOLS_FULL = (
    ",mcp__repowise__get_risk"
    ",mcp__repowise__get_dependency_path"
    ",mcp__repowise__get_overview"
    ",mcp__repowise__get_context"
    ",mcp__repowise__get_why"
    ",mcp__repowise__search_codebase"
)

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

    # Build disallowed tools list — always block meta-tools that let agent
    # discover/invoke repowise outside the allowlist
    disallowed = "ToolSearch,ListMcpResourcesTool"
    if not condition.get("repowise_enabled"):
        disallowed += ",mcp__repowise__*"

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
            system_prompt = (SWEBENCH_PROMPT_INDEX_ONLY if benchmark == "swe_bench"
                             else SWEQA_PROMPT_INDEX_ONLY)
        else:
            allowed_tools += TOOLS_FULL
            system_prompt = (SWEBENCH_PROMPT_FULL if benchmark == "swe_bench"
                             else SWEQA_PROMPT_FULL)
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
        cmd.extend(["--append-system-prompt", system_prompt])

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
                err = result.stderr[:1000]
                if is_rate_limit_error(err):
                    backoff_sleep(attempt)
                    continue
                return {"error": err, "returncode": result.returncode}, attempt

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
