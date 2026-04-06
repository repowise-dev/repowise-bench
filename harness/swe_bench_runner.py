"""
SWE-bench runner — production grade.

Flow per task:
1. Clone repo (shallow) + checkout base_commit
2. Optionally index with repowise
3. Run Claude Code with problem_statement as prompt
4. Capture git diff (agent's patch)
5. Save full metadata + raw output

Evaluation (test pass/fail) is done separately via the official
swebench harness or Docker — we just capture patches here.

Resilient: retries on rate limits, saves progress per task,
safe to interrupt and resume.
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
from harness.swe_qa_runner import (
    _UTF8_ENV, resolve_repo_path, generate_mcp_config,
    is_rate_limit_error, backoff_sleep, _extract_json_scores,
    SWEBENCH_PROMPT_INDEX_ONLY, SWEBENCH_PROMPT_FULL,
    TOOLS_INDEX_ONLY, TOOLS_FULL, MAX_RETRIES,
)

# ---------------------------------------------------------------------------
# SWE-bench repos (from the Verified dataset)
# ---------------------------------------------------------------------------

SWEBENCH_REPOS = [
    "django/django", "sympy/sympy", "sphinx-doc/sphinx",
    "matplotlib/matplotlib", "scikit-learn/scikit-learn",
    "astropy/astropy", "pydata/xarray", "pytest-dev/pytest",
    "pylint-dev/pylint", "psf/requests", "mwaskom/seaborn",
    "pallets/flask",
]


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_swe_bench_tasks(data_dir: str, max_tasks: Optional[int] = None,
                          repos: Optional[list] = None,
                          difficulty: Optional[list] = None) -> list:
    """Load SWE-bench tasks from pre-downloaded JSON."""
    data_path = Path(data_dir) / "swe_bench"

    local_file = data_path / "tasks.json"
    if not local_file.exists():
        raise FileNotFoundError(
            f"No SWE-bench data in {data_path}. "
            "Run: python scripts/download_benchmarks.py --benchmark swe_bench"
        )

    with open(local_file, encoding="utf-8") as f:
        tasks = json.load(f)

    if repos:
        tasks = [t for t in tasks if t["repo"] in repos]

    if difficulty:
        tasks = [t for t in tasks if t.get("difficulty", "") in difficulty]

    if max_tasks and max_tasks < len(tasks):
        tasks = tasks[:max_tasks]

    return tasks


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def clone_repo(repo_name: str, repos_dir: str) -> Path:
    """Clone repo if not present. Returns repo path."""
    repo_path = resolve_repo_path(repo_name, repos_dir)
    if repo_path.exists() and (repo_path / ".git").exists():
        return repo_path
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{repo_name}.git"
    print(f"  Cloning {repo_name}...")
    subprocess.run(
        ["git", "clone", clone_url, str(repo_path)],
        check=True, capture_output=True, timeout=600,
        env=_UTF8_ENV, encoding="utf-8", errors="replace"
    )
    return repo_path


def checkout_commit(repo_path: Path, commit: str) -> bool:
    """Checkout a specific commit. Cleans working tree but preserves .repowise/."""
    try:
        subprocess.run(
            ["git", "checkout", "-f", commit],
            cwd=str(repo_path), capture_output=True, timeout=60,
            env=_UTF8_ENV, encoding="utf-8", errors="replace"
        )
        # Clean untracked files but EXCLUDE .repowise/ (index data)
        subprocess.run(
            ["git", "clean", "-fdx", "-e", ".repowise"],
            cwd=str(repo_path), capture_output=True, timeout=60,
            env=_UTF8_ENV, encoding="utf-8", errors="replace"
        )
        return True
    except Exception as e:
        print(f"  Checkout failed: {e}")
        return False


def get_agent_patch(repo_path: Path) -> str:
    """Capture git diff of agent's changes (staged + unstaged)."""
    try:
        r = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(repo_path), capture_output=True, timeout=30,
            encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()
    except Exception:
        return ""


def reset_repo(repo_path: Path, commit: str):
    """Reset repo back to base commit after agent run. Preserves .repowise/."""
    try:
        subprocess.run(
            ["git", "checkout", "-f", commit],
            cwd=str(repo_path), capture_output=True, timeout=60,
            env=_UTF8_ENV, encoding="utf-8", errors="replace"
        )
        subprocess.run(
            ["git", "clean", "-fdx", "-e", ".repowise"],
            cwd=str(repo_path), capture_output=True, timeout=60,
            env=_UTF8_ENV, encoding="utf-8", errors="replace"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Repowise indexing for SWE-bench (per commit)
# ---------------------------------------------------------------------------

def index_at_commit(repo_name: str, repo_path: Path, commit: str,
                    index_dir: str, mode: str, repowise_bin: str) -> tuple:
    """
    Index a repo at a specific commit. Caches by repo+commit+mode.
    Returns (success, time_seconds).
    """
    short_commit = commit[:8]
    cache_key = f"{repo_name.replace('/', '_')}_{short_commit}_{mode}"
    cache_dir = Path(index_dir) / cache_key

    # Restore from cache
    if cache_dir.exists():
        cached_idx = cache_dir / ".repowise"
        dest_idx = repo_path / ".repowise"
        if cached_idx.exists():
            if dest_idx.exists():
                shutil.rmtree(str(dest_idx))
            shutil.copytree(str(cached_idx), str(dest_idx))
        return True, 0.0

    start = time.time()
    cmd = [repowise_bin, "init", "-y", "--no-claude-md"]
    if mode == "index-only":
        cmd.append("--index-only")

    # Force DB to repo-local .repowise/wiki.db so the MCP server can find it
    rw_dir = repo_path.resolve() / ".repowise"
    rw_dir.mkdir(parents=True, exist_ok=True)
    local_db = (rw_dir / "wiki.db").as_posix()
    env = {**_UTF8_ENV, "REPOWISE_DB_URL": f"sqlite+aiosqlite:///{local_db}"}

    print(f"  Indexing {repo_name}@{short_commit} (mode={mode})...")
    result = subprocess.run(
        cmd, cwd=str(repo_path), capture_output=True, timeout=900,
        env=env, encoding="utf-8", errors="replace"
    )
    elapsed = time.time() - start

    if result.returncode == 0:
        cache_dir.mkdir(parents=True, exist_ok=True)
        src = repo_path / ".repowise"
        if src.exists():
            shutil.copytree(str(src), str(cache_dir / ".repowise"))
        print(f"  Indexed in {elapsed:.0f}s")
        return True, elapsed
    else:
        print(f"  Indexing failed: {result.stderr[:300]}")
        return False, elapsed


# ---------------------------------------------------------------------------
# Claude Code invocation (reuses rate-limit logic from swe_qa_runner)
# ---------------------------------------------------------------------------

SWE_BENCH_PROMPT_TEMPLATE = """You are a software engineer tasked with fixing a bug in a repository.

Here is the issue/bug report:

{problem_statement}

{hints}

Your task:
1. Understand the issue by exploring the relevant code
2. Identify the root cause
3. Make the minimal code changes needed to fix the bug
4. Do NOT add tests — only fix the source code

Important:
- Make minimal, targeted changes. Do not refactor unrelated code.
- Edit the actual source files using the Edit tool.
- Focus on the root cause, not symptoms.
"""


def run_claude_code_swebench(prompt: str, repo_path: str, condition: dict,
                              model: str, timeout: int,
                              max_budget_usd: float = 3.0,
                              mcp_config_path: Optional[str] = None) -> tuple:
    """Run Claude Code for SWE-bench task. Returns (output_dict, retries)."""
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--max-budget-usd", str(max_budget_usd),
    ]

    # SWE-bench needs Edit + Write for making fixes
    allowed_tools = "Read,Grep,Glob,Bash,Edit,Write"
    if condition.get("repowise_enabled"):
        mode = condition.get("repowise_mode", "full")
        if mode == "index-only":
            allowed_tools += TOOLS_INDEX_ONLY
            system_prompt = SWEBENCH_PROMPT_INDEX_ONLY
        else:
            allowed_tools += TOOLS_FULL
            system_prompt = SWEBENCH_PROMPT_FULL
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
                from harness.metrics import parse_claude_stream_output
                lines = result.stdout.strip().split("\n")
                parsed = parse_claude_stream_output(lines)

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
                    "num_tool_calls": parsed["num_tool_calls"],
                    "files_explored": parsed["files_explored"],
                    "files_edited": parsed["files_edited"],
                    "repowise_tools_called": parsed["repowise_tools_called"],
                    "_raw_stream_lines": lines,
                }

                if not output["result"] and result.stderr:
                    if is_rate_limit_error(result.stderr):
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
# Single task runner
# ---------------------------------------------------------------------------

def run_swe_bench_task(task: dict, condition: dict, config: dict,
                       budget: BudgetTracker,
                       raw_saver: Optional[RawOutputSaver] = None) -> RunMetrics:
    """Run one SWE-bench task under one condition."""

    instance_id = task["instance_id"]
    repo_name = task["repo"]
    base_commit = task["base_commit"]

    metrics = RunMetrics(
        task_id=instance_id,
        benchmark="swe_bench",
        condition=condition["name"],
        repo=repo_name,
        question_type=task.get("difficulty", ""),
        model_used=config["agent"]["model"],
        timestamp=datetime.now(timezone.utc).isoformat(),
        repo_commit=base_commit[:12],
    )

    # Budget gate
    if not budget.check_budget(estimated_cost=2.0):
        metrics.error = "budget_exceeded"
        return metrics

    repos_dir = config["paths"]["repos_dir"]
    repo_path = resolve_repo_path(repo_name, repos_dir)

    # Clone if needed
    if not repo_path.exists():
        try:
            clone_repo(repo_name, repos_dir)
        except Exception as e:
            metrics.error = f"clone_failed: {e}"
            return metrics

    # Checkout base commit
    if not checkout_commit(repo_path, base_commit):
        metrics.error = "checkout_failed"
        return metrics

    # Remove any previous repowise index (we index per-commit)
    rw_dir = repo_path / ".repowise"
    if rw_dir.exists():
        shutil.rmtree(str(rw_dir))

    # Index if repowise condition
    mcp_config_path = None
    if condition.get("repowise_enabled"):
        mode = condition.get("repowise_mode", "index-only")
        try:
            ok, idx_time = index_at_commit(
                repo_name, repo_path, base_commit,
                config["repowise"]["index_dir"],
                mode, config["repowise"]["binary"],
            )
            metrics.index_time_seconds = idx_time
            if not ok:
                metrics.error = "indexing_failed"
                reset_repo(repo_path, base_commit)
                return metrics
        except Exception as e:
            metrics.error = f"indexing_error: {e}"
            reset_repo(repo_path, base_commit)
            return metrics

        bench_root = Path(__file__).resolve().parent.parent
        mcp_cfg = generate_mcp_config(repo_path, bench_root)
        mcp_config_path = str(mcp_cfg)

    # Build prompt
    problem = task["problem_statement"]
    hints = task.get("hints_text", "") or ""
    hints_section = f"\nHints from the maintainers:\n{hints}" if hints else ""

    prompt = SWE_BENCH_PROMPT_TEMPLATE.format(
        problem_statement=problem,
        hints=hints_section,
    )
    metrics.prompt_sent = prompt[:2000]  # truncate for storage

    # Run agent
    per_task_budget = config.get("budget", {}).get("max_per_task_usd", 3.0)
    start = time.time()
    output, retries = run_claude_code_swebench(
        prompt=prompt,
        repo_path=str(repo_path),
        condition=condition,
        model=config["agent"]["model"],
        timeout=config["agent"]["timeout_seconds"],
        max_budget_usd=per_task_budget,
        mcp_config_path=mcp_config_path,
    )
    metrics.wall_clock_seconds = time.time() - start
    metrics.retries = retries

    # Save raw output
    if raw_saver:
        metrics.raw_output_file = raw_saver.save(instance_id, condition["name"], output)

    # Parse Claude output
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
        metrics.num_tool_calls = output.get("num_tool_calls", 0)
        metrics.files_explored = output.get("files_explored", [])
        metrics.files_edited = output.get("files_edited", [])
        metrics.repowise_tools_called = output.get("repowise_tools_called", [])

    metrics.compute_derived()

    # Capture the agent's patch (git diff)
    agent_patch = get_agent_patch(repo_path)
    if agent_patch:
        metrics.answer = agent_patch  # Store patch in answer field for SWE-bench

    # Save patch to separate file for evaluation
    if agent_patch and raw_saver:
        patch_dir = Path(raw_saver.logs_dir).parent / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_file = patch_dir / f"{instance_id}_{condition['name']}.patch"
        with open(patch_file, "w", encoding="utf-8") as f:
            f.write(agent_patch)
        # Also save gold patch for reference
        gold_file = patch_dir / f"{instance_id}_gold.patch"
        if not gold_file.exists():
            with open(gold_file, "w", encoding="utf-8") as f:
                f.write(task.get("patch", ""))

    # Reset repo for next task
    reset_repo(repo_path, base_commit)

    budget.record(metrics.estimated_cost_usd, instance_id)
    return metrics
