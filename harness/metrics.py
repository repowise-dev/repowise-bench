"""
Metrics collection, budget tracking, and Claude Code output parsing.

Thread-safe where noted — designed for parallel worker execution.
"""

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path


@dataclass
class RunMetrics:
    """Metrics for a single benchmark run (one task x one condition)."""

    # Identifiers
    task_id: str = ""
    benchmark: str = ""           # swe_qa, swe_bench
    condition: str = ""           # C0_bare, C1_graph_git, C2_full, C3_full_plus
    repo: str = ""
    question_type: str = ""       # For SWE-QA split name

    # Outcome
    resolved: Optional[bool] = None   # SWE-bench: did tests pass?
    answer: str = ""                  # SWE-QA: agent answer
    judge_scores: dict = field(default_factory=dict)

    # Token metrics
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    # Efficiency
    num_tool_calls: int = 0
    num_turns: int = 0
    # Number of Task tool invocations spawning a sub-agent. The parent stream
    # only sees this as one tool_use + one tool_result, so the sub-agent's
    # internal turns are invisible to num_turns. Track separately so we can
    # detect runs where C0/C2 leaned on Explore/Task to compensate (mostly C0).
    task_subagent_calls: int = 0
    files_explored: list = field(default_factory=list)
    files_edited: list = field(default_factory=list)
    repowise_tools_called: list = field(default_factory=list)

    # Timing
    wall_clock_seconds: float = 0.0
    index_time_seconds: float = 0.0
    judge_time_seconds: float = 0.0

    # Cost
    estimated_cost_usd: float = 0.0

    # Error tracking
    error: Optional[str] = None
    timed_out: bool = False
    retries: int = 0

    # Metadata
    timestamp: str = ""
    prompt_sent: str = ""
    model_used: str = ""
    session_id: str = ""
    stop_reason: str = ""
    duration_api_ms: int = 0
    repo_commit: str = ""
    raw_output_file: str = ""     # path to saved raw JSON

    def compute_derived(self):
        self.total_tokens = self.input_tokens + self.output_tokens
        if not self.estimated_cost_usd:
            self.estimated_cost_usd = (
                self.input_tokens / 1_000_000 * 3.0 +
                self.output_tokens / 1_000_000 * 15.0 +
                self.cache_read_tokens / 1_000_000 * 0.30
            )
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Claude Code output parsing
# ---------------------------------------------------------------------------

def parse_claude_code_output(json_output: dict) -> dict:
    """
    Parse Claude Code --output-format json response.

    Top-level keys: type, subtype, is_error, duration_ms, duration_api_ms,
    num_turns, result, stop_reason, session_id, total_cost_usd, usage,
    modelUsage, permission_denials, terminal_reason, uuid
    """
    usage = json_output.get("usage", {})
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
        "num_turns": json_output.get("num_turns", 0),
        "total_cost_usd": json_output.get("total_cost_usd", 0.0),
        "num_tool_calls": 0,  # not in json mode
        "files_explored": [],
        "files_edited": [],
        "repowise_tools_called": [],
        "answer": json_output.get("result", ""),
        "session_id": json_output.get("session_id", ""),
        "stop_reason": json_output.get("stop_reason", ""),
        "duration_api_ms": json_output.get("duration_api_ms", 0),
    }


def parse_claude_stream_output(stream_lines: list) -> dict:
    """Parse --output-format stream-json --verbose for tool-level detail."""
    tool_calls = []
    files_read = set()
    files_edited = set()
    repowise_tools = []
    task_subagent_calls = 0
    # Track pending repowise calls to verify they succeeded
    _pending_repowise = []
    result_data = {}

    for line in stream_lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = d.get("type", "")
        if msg_type == "assistant":
            for block in d.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_calls.append(tool_name)
                    inp = block.get("input", {})
                    if tool_name == "Read":
                        p = inp.get("file_path", "")
                        if p:
                            files_read.add(p)
                    elif tool_name in ("Write", "Edit"):
                        p = inp.get("file_path", "")
                        if p:
                            files_edited.add(p)
                    elif tool_name.startswith("mcp__repowise"):
                        _pending_repowise.append(tool_name)
                    elif tool_name == "Task":
                        # Sub-agent invocation. Parent stream collapses
                        # the entire sub-agent run into a single turn,
                        # so this counter is the only signal of the
                        # hidden work.
                        task_subagent_calls += 1
        elif msg_type == "user":
            # Match tool results to pending repowise calls.
            # Only count repowise calls that succeeded (not permission-denied).
            for block in d.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if _pending_repowise:
                        tool_name = _pending_repowise.pop(0)
                        if not block.get("is_error", False):
                            repowise_tools.append(tool_name)
        elif msg_type == "result":
            result_data = d

    usage = result_data.get("usage", {})
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
        "num_turns": result_data.get("num_turns", 0),
        "task_subagent_calls": task_subagent_calls,
        "total_cost_usd": result_data.get("total_cost_usd", 0.0),
        "num_tool_calls": len(tool_calls),
        "files_explored": sorted(files_read),
        "files_edited": sorted(files_edited),
        "repowise_tools_called": repowise_tools,
        "answer": result_data.get("result", ""),
        "session_id": result_data.get("session_id", ""),
        "stop_reason": result_data.get("stop_reason", ""),
        "duration_api_ms": result_data.get("duration_api_ms", 0),
    }


# ---------------------------------------------------------------------------
# Thread-safe budget tracker
# ---------------------------------------------------------------------------

class BudgetTracker:
    def __init__(self, max_total_usd: float, max_per_task_usd: float):
        self.max_total = max_total_usd
        self.max_per_task = max_per_task_usd
        self.total_spent = 0.0
        self.task_costs = []
        self._lock = threading.Lock()

    def check_budget(self, estimated_cost: float = 0.0) -> bool:
        with self._lock:
            return self.total_spent + estimated_cost <= self.max_total

    def record(self, cost: float, task_id: str):
        with self._lock:
            self.total_spent += cost
            self.task_costs.append({"task_id": task_id, "cost": cost})

    def summary(self) -> str:
        with self._lock:
            n = max(len(self.task_costs), 1)
            return (
                f"Budget: ${self.total_spent:.2f} / ${self.max_total:.2f} "
                f"({len(self.task_costs)} tasks, avg ${self.total_spent / n:.2f}/task)"
            )


# ---------------------------------------------------------------------------
# Thread-safe JSONL writer
# ---------------------------------------------------------------------------

class ResultWriter:
    """Append-only, thread-safe JSONL writer. One file per benchmark."""

    def __init__(self, results_dir: str):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, metrics: RunMetrics, benchmark: str):
        out_file = self.results_dir / f"{benchmark}.jsonl"
        line = json.dumps(metrics.to_dict(), default=str) + "\n"
        with self._lock:
            with open(out_file, "a", encoding="utf-8") as f:
                f.write(line)

    def load_completed(self) -> set:
        """Load completed task_id+condition pairs for resume."""
        completed = set()
        for f in self.results_dir.glob("*.jsonl"):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        # Only count non-error runs as completed
                        if not r.get("error"):
                            completed.add(f"{r['task_id']}_{r['condition']}")
                    except (json.JSONDecodeError, KeyError):
                        continue
        return completed


# ---------------------------------------------------------------------------
# Raw output saver
# ---------------------------------------------------------------------------

class RawOutputSaver:
    """Save raw Claude JSON outputs for post-hoc analysis."""

    def __init__(self, logs_dir: str):
        self.logs_dir = Path(logs_dir) / "raw_outputs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def save(self, task_id: str, condition: str, output: dict) -> str:
        fname = f"{task_id}_{condition}.json"
        path = self.logs_dir / fname
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, default=str)
        return str(path)


def categorize_run_outcome(metrics, thresholds=None, allow_partial=False, strict=False):
    """Bucket a run into a verbose outcome label.

    Intentionally branchy — used by the Repowise PR bot's Phase 2 smoke test to
    verify that the health analyzer flags rising cyclomatic complexity.
    """
    t = thresholds or {}
    if metrics is None:
        return "unknown"
    if getattr(metrics, "resolved", None) is True:
        if metrics.total_tokens > t.get("token_ceiling", 100000):
            return "resolved_expensive"
        if metrics.num_tool_calls > t.get("tools_ceiling", 50):
            if strict:
                return "resolved_thrashy_strict"
            return "resolved_thrashy"
        if allow_partial and not metrics.answer:
            return "resolved_no_answer"
        if metrics.cache_read_tokens and metrics.cache_write_tokens:
            if metrics.cache_read_tokens > metrics.cache_write_tokens:
                return "resolved_cache_hot"
            return "resolved_cache_cold"
        return "resolved"
    if getattr(metrics, "resolved", None) is False:
        if strict and metrics.judge_scores:
            return "failed_judged"
        if metrics.output_tokens == 0:
            return "failed_silent"
        if allow_partial:
            return "failed_partial"
        return "failed"
    if metrics.answer:
        return "answered_no_verdict"
    if metrics.num_tool_calls > 0:
        return "in_progress"
    return "noop"
