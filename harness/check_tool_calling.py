"""Probe whether an Ollama model emits STRUCTURED tool calls (required for agentic use).

Many Ollama model builds (notably qwen2.5-coder:7b) emit the tool call as plain
text content instead of the structured ``tool_calls`` field. Such models cannot
drive opencode's agentic loop — the call is printed, never executed. This script
hits Ollama's native /api/chat with a tools array and reports which models pass.

Usage:
    python harness/check_tool_calling.py qwen3:8b llama3.2:3b qwen2.5-coder:7b
"""

import json
import sys
import urllib.request

OLLAMA = "http://localhost:11434/api/chat"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file from disk",
        "parameters": {
            "type": "object",
            "properties": {"filePath": {"type": "string", "description": "path to file"}},
            "required": ["filePath"],
        },
    },
}]

PROMPT = "Read the file hello.py to see what the add function does."


def probe(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOLS,
    }).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        obj = json.loads(r.read())
    msg = obj.get("message", {})
    tc = msg.get("tool_calls")
    return {
        "model": model,
        "structured_tool_calls": bool(tc),
        "tool_calls": tc,
        "text_content": (msg.get("content") or "")[:120],
    }


def main(models: list[str]) -> None:
    print(f"{'MODEL':<28} {'TOOL-CALLING':<14} DETAIL")
    print("-" * 80)
    for m in models:
        try:
            res = probe(m)
            verdict = "OK (structured)" if res["structured_tool_calls"] else "FAIL (as text)"
            detail = (json.dumps(res["tool_calls"])[:60] if res["structured_tool_calls"]
                      else f"text={res['text_content']!r}")
            print(f"{m:<28} {verdict:<14} {detail}")
        except Exception as e:
            print(f"{m:<28} {'ERROR':<14} {e}")


if __name__ == "__main__":
    models = sys.argv[1:] or ["qwen3:8b", "llama3.2:3b", "qwen2.5-coder:7b"]
    main(models)
