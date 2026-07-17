"""Health-check classification against a fake stdio MCP server.

The fixture server (fake_mcp_server.py) is synthetic and local: no network,
no API calls, no keys. Each of its tools exists to pin one classification the
pre-flight must get right, because a misclassified pre-flight either blocks a
healthy arm or (worse) green-lights a broken one.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters

from harness.tool_health_check import (
    HOST_OUTPUT_CAP_TOKENS,
    check_stdio,
    load_server_config,
    stdio_params_from_config,
    synthesize_args,
)

FAKE_SERVER = Path(__file__).resolve().parent / "fake_mcp_server.py"


@pytest.fixture(scope="module")
def fake_rows():
    params = StdioServerParameters(command=sys.executable, args=[str(FAKE_SERVER)])
    rows, dump = asyncio.run(check_stdio(params, {}, "benign question", True))
    return {r.name: r for r in rows}, dump


def test_healthy_tool_is_ok(fake_rows):
    rows, dump = fake_rows
    assert rows["healthy"].status == "OK"
    assert "benign question" in dump["healthy"]


def test_empty_tool_is_empty(fake_rows):
    rows, _ = fake_rows
    assert rows["empty"].status == "EMPTY"


def test_raising_tool_is_error(fake_rows):
    rows, _ = fake_rows
    assert rows["boom"].status == "ERROR"


def test_oversized_tool_flagged_against_host_cap(fake_rows):
    rows, _ = fake_rows
    assert rows["oversized"].over_cap
    assert rows["oversized"].approx_tokens > HOST_OUTPUT_CAP_TOKENS
    # An oversized result is not itself a protocol error; only the flag is set.
    assert rows["oversized"].status == "OK"


def test_synthesize_args_fills_only_required():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "verbose": {"type": "boolean"},
            "targets": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
        },
        "required": ["query", "limit", "verbose", "targets", "mode"],
    }
    args = synthesize_args(schema, "hello")
    assert args == {"query": "hello", "limit": 1, "verbose": False,
                    "targets": ["hello"], "mode": "fast"}
    assert synthesize_args({"properties": {"q": {"type": "string"}}}, "x") == {}
    assert synthesize_args(None, "x") == {}


def test_load_server_config_and_env_layering(tmp_path):
    cfg_path = tmp_path / "arm.json"
    cfg_path.write_text(json.dumps({
        "mcpServers": {
            "fake": {"command": sys.executable, "args": [str(FAKE_SERVER)],
                     "env": {"ARM_SPECIFIC": "1"}},
        }
    }))
    name, cfg = load_server_config(cfg_path, None)
    assert name == "fake"
    params = stdio_params_from_config(cfg, cwd=str(tmp_path))
    assert params.env["ARM_SPECIFIC"] == "1"
    assert "PATH" in params.env  # caller env is inherited, config layers on top
    assert params.cwd == str(tmp_path)


def test_load_server_config_rejects_unknown_name(tmp_path):
    cfg_path = tmp_path / "arm.json"
    cfg_path.write_text(json.dumps({"mcpServers": {"a": {}, "b": {}}}))
    with pytest.raises(SystemExit):
        load_server_config(cfg_path, None)  # ambiguous without --server
    with pytest.raises(SystemExit):
        load_server_config(cfg_path, "missing")
