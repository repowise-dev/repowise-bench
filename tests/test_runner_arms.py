"""Runner behavior for multi-server benchmark arms, on synthetic streams.

Everything here runs against fabricated stream-JSONL and a stubbed
subprocess: no agent, no network, no keys. The three properties pinned are
the ones whose silent failure fabricates results: the attach-guard (an arm
that never called its server is not evidence about the server), token
accounting (result.usage under-reports on current Claude Code; modelUsage is
authoritative), and per-arm command construction (a mis-mounted config turns
a competitor arm into a bare agent).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.metrics import parse_claude_stream_output
from harness import swe_qa_runner


# ---------------------------------------------------------------------------
# Synthetic stream builders
# ---------------------------------------------------------------------------

def _tool_use(tool_id: str, name: str, inp: dict | None = None) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": inp or {}}]}})


def _tool_result(tool_id: str, is_error: bool = False) -> str:
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error}]}})


def _result_line(usage: dict | None = None, model_usage: dict | None = None,
                 cost: float = 0.1) -> str:
    d = {"type": "result", "result": "the answer", "num_turns": 3,
         "total_cost_usd": cost, "usage": usage or {}}
    if model_usage is not None:
        d["modelUsage"] = model_usage
    return json.dumps(d)


# ---------------------------------------------------------------------------
# Attach-guard input: server_tools_called
# ---------------------------------------------------------------------------

def test_server_tools_called_keyed_by_prefix():
    lines = [
        _tool_use("t1", "mcp__serena__find_symbol"),
        _tool_result("t1"),
        _tool_use("t2", "mcp__serena__get_symbols_overview"),
        _tool_result("t2"),
        _tool_use("t3", "Read", {"file_path": "src/app.py"}),
        _tool_result("t3"),
        _result_line(),
    ]
    parsed = parse_claude_stream_output(lines)
    assert parsed["server_tools_called"] == {
        "serena": ["mcp__serena__find_symbol", "mcp__serena__get_symbols_overview"]
    }
    assert parsed["repowise_tools_called"] == []
    assert parsed["num_tool_calls"] == 3


def test_errored_mcp_call_is_not_an_attach_signal():
    lines = [
        _tool_use("t1", "mcp__codegraph__codegraph_explore"),
        _tool_result("t1", is_error=True),
        _result_line(),
    ]
    parsed = parse_claude_stream_output(lines)
    assert parsed["server_tools_called"] == {}


def test_results_matched_by_tool_use_id_not_order():
    # Two parallel MCP calls whose results come back out of order: the
    # errored one must be attributed to the right call.
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "mcp__repowise__get_answer", "input": {}},
            {"type": "tool_use", "id": "b", "name": "mcp__repowise__get_context", "input": {}},
        ]}}),
        _tool_result("b", is_error=False),
        _tool_result("a", is_error=True),
        _result_line(),
    ]
    parsed = parse_claude_stream_output(lines)
    assert parsed["server_tools_called"] == {"repowise": ["mcp__repowise__get_context"]}
    assert parsed["repowise_tools_called"] == ["mcp__repowise__get_context"]


def test_bare_run_has_no_server_calls():
    lines = [
        _tool_use("t1", "Read", {"file_path": "a.py"}),
        _tool_result("t1"),
        _result_line(),
    ]
    assert parse_claude_stream_output(lines)["server_tools_called"] == {}


# ---------------------------------------------------------------------------
# Token accounting: modelUsage is authoritative
# ---------------------------------------------------------------------------

def test_model_usage_preferred_over_underreporting_result_usage():
    # Figures from a real transcript where top-level usage under-reported:
    # cache_creation 46340 vs a true 98485, output 183 vs 274.
    lines = [_result_line(
        usage={"input_tokens": 10, "output_tokens": 183,
               "cache_creation_input_tokens": 46340,
               "cache_read_input_tokens": 1000},
        model_usage={"claude-sonnet-5": {
            "inputTokens": 12, "outputTokens": 274,
            "cacheCreationInputTokens": 98485,
            "cacheReadInputTokens": 2000, "costUSD": 0.42}},
        cost=0.42,
    )]
    parsed = parse_claude_stream_output(lines)
    assert parsed["token_source"] == "modelUsage"
    assert parsed["output_tokens"] == 274
    assert parsed["cache_write_tokens"] == 98485
    assert parsed["cache_read_tokens"] == 2000
    assert parsed["total_cost_usd"] == pytest.approx(0.42)


def test_model_usage_sums_across_models():
    lines = [_result_line(model_usage={
        "claude-sonnet-5": {"inputTokens": 100, "outputTokens": 50, "costUSD": 0.2},
        "claude-haiku-4-5": {"inputTokens": 30, "outputTokens": 7, "costUSD": 0.01},
    })]
    parsed = parse_claude_stream_output(lines)
    assert parsed["input_tokens"] == 130
    assert parsed["output_tokens"] == 57


def test_result_usage_fallback_when_no_model_usage():
    lines = [_result_line(usage={"input_tokens": 11, "output_tokens": 22,
                                 "cache_creation_input_tokens": 33,
                                 "cache_read_input_tokens": 44})]
    parsed = parse_claude_stream_output(lines)
    assert parsed["token_source"] == "result_usage"
    assert (parsed["input_tokens"], parsed["output_tokens"]) == (11, 22)


# ---------------------------------------------------------------------------
# Command construction per arm
# ---------------------------------------------------------------------------

@pytest.fixture()
def captured_run(monkeypatch, tmp_path):
    """Stub subprocess.run inside the runner; capture the claude command."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        stdout = "\n".join([_result_line(
            model_usage={"claude-sonnet-5": {
                "inputTokens": 1, "outputTokens": 1, "costUSD": 0.001}})])
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(swe_qa_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(swe_qa_runner, "get_c0_worktree",
                        lambda repo, variant="": tmp_path / "wt")
    (tmp_path / "wt").mkdir()
    return calls


def _flag(cmd: list, name: str) -> str:
    return cmd[cmd.index(name) + 1]


def test_third_party_arm_mounts_static_config(captured_run, tmp_path):
    cfg = tmp_path / "serena_flask.json"
    cfg.write_text("{}")
    condition = {"name": "C3_serena",
                 "mcp_server": {"prefix": "serena", "config": str(cfg)}}
    output, _ = swe_qa_runner.run_claude_code(
        "q?", str(tmp_path / "repo"), condition, "sonnet", timeout=10)
    cmd = captured_run[0]["cmd"]
    assert "--strict-mcp-config" in cmd
    assert _flag(cmd, "--mcp-config") == str(cfg)
    allowed = _flag(cmd, "--allowed-tools")
    assert "mcp__serena" in allowed and "ToolSearch" in allowed
    disallowed = _flag(cmd, "--disallowed-tools")
    assert "mcp__*" not in disallowed
    prompts = " ".join(cmd[i + 1] for i, a in enumerate(cmd)
                       if a == "--append-system-prompt")
    assert "serena" in prompts
    assert output["result"] == "the answer"


def test_c0_arm_blocks_every_mcp_surface(captured_run, tmp_path):
    output, _ = swe_qa_runner.run_claude_code(
        "q?", str(tmp_path / "repo"), {"name": "C0_bare"}, "sonnet", timeout=10)
    cmd = captured_run[0]["cmd"]
    disallowed = _flag(cmd, "--disallowed-tools")
    assert "mcp__*" in disallowed and "ToolSearch" in disallowed
    assert "ToolSearch" not in _flag(cmd, "--allowed-tools")
    # C0 runs in the clean worktree, not the source checkout.
    assert captured_run[0]["kwargs"]["cwd"].endswith("wt")


def test_worktree_files_injected_for_packed_arm(captured_run, tmp_path):
    pack = tmp_path / "default.xml"
    pack.write_text("<repo/>")
    condition = {"name": "C4_repomix",
                 "worktree_files": {"repomix-output.xml": str(pack)},
                 "system_note": "The file repomix-output.xml in the repository "
                                "root contains a packed representation of the "
                                "whole repository."}
    swe_qa_runner.run_claude_code(
        "q?", str(tmp_path / "repo"), condition, "sonnet", timeout=10)
    assert (tmp_path / "wt" / "repomix-output.xml").read_text() == "<repo/>"
    cmd = captured_run[0]["cmd"]
    prompts = " ".join(cmd[i + 1] for i, a in enumerate(cmd)
                       if a == "--append-system-prompt")
    assert "repomix-output.xml" in prompts
