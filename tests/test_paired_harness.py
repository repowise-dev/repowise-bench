"""Per-run metrics of the paired harness and the Table 1 grep baseline."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "paired"))
sys.path.insert(0, str(ROOT / "table1"))

from run_paired import build_cmd, parse_stream, steer_text  # noqa: E402
from run_table1 import grep_files  # noqa: E402


def _assistant(mid, tools, usage):
    return {"type": "assistant", "message": {"id": mid, "usage": usage,
            "content": [{"type": "tool_use", "name": n, "input": i} for n, i in tools]}}


def test_parse_stream_counts_tools_and_skips_toolsearch_for_first_tool(tmp_path):
    u = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100, "output_tokens": 5}
    events = [
        {"type": "system", "subtype": "init", "claude_code_version": "x", "model": "m", "tools": []},
        _assistant("m1", [("ToolSearch", {})], u),
        _assistant("m2", [("mcp__repowise__get_answer", {"question": "q"})], u),
        _assistant("m2", [("Read", {"file_path": "a\\b.py"})], u),  # same message id: counted once
        {"type": "result", "total_cost_usd": 0.01, "num_turns": 3, "duration_ms": 2000, "result": "ANSWER: x",
         "modelUsage": {"m": {"inputTokens": 1, "cacheCreationInputTokens": 2, "cacheReadInputTokens": 3, "outputTokens": 4}}},
    ]
    p = tmp_path / "stream.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    m = parse_stream(p)
    assert m["first_tool_rw"] is True
    assert m["rw_calls"] == 1 and m["rw_tools"] == ["get_answer"]
    assert m["tool_calls"] == 3
    assert m["files_read"] == ["a/b.py"]
    assert m["total_tokens"] == 10
    assert m["total_tokens_by_message"] == 2 * 115
    assert m["first_ctx"] == 110


def test_steer_text_is_the_published_block():
    assert steer_text().startswith("For any question about this codebase, call a Repowise tool first")


def test_build_cmd_arms_differ_only_in_mcp_permission(tmp_path):
    t = {"kind": "read"}
    bare = build_cmd(t, "bare", tmp_path / "m.json", "minimal", "sonnet", None)
    rw = build_cmd(t, "rw", tmp_path / "m.json", "minimal", "sonnet", None)
    assert [a.replace(" mcp__repowise", "") for a in rw] == bare and rw != bare
    assert "Edit" not in " ".join(bare)
    edit = build_cmd({"kind": "edit"}, "bare", tmp_path / "m.json", "minimal", "sonnet", "sid")
    assert "Edit" in " ".join(edit) and edit[-2:] == ["sid", "--fork-session"]


def test_grep_files_intersects_terms_and_falls_back_to_first(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("session cookie\n")
    (tmp_path / "b.py").write_text("session only\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert grep_files(tmp_path, ["session", "cookie"]) == ["a.py"]
    assert grep_files(tmp_path, ["session", "absent"]) == ["a.py", "b.py"]
