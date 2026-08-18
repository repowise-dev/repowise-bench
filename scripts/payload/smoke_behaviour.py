"""Read the cell-B smoke's transcripts for the four questions it can answer.

Deliberately NOT the went-around classifier from `MCP_BEHAVIOUR_FINDINGS.md`.
That instrument needs a corpus; this smoke is n=1 per arm and a went-around RATE
off two sessions would be a number with no denominator behind it. What is
answerable at n=1 is presence/absence and resolution success, which are counts
of concrete events, so that is all this reports:

  1. did the MCP server come up and get called at all, and with what
  2. did `get_context` resolve `Class.method` targets, or return
     `Target not found` -- this is #1435 (f82ebb0d), which
     `MCP_BEHAVIOUR_FINDINGS.md` 2a measured failing 11 of 26 (42%) of
     `get_context` calls on cell A, EVERY failure a `Class.method` target and
     every success a plain file target
  3. was any payload `degraded` / empty-answer -- 20 of 22 stated tool
     abandonments on cell A named that
  4. per-task cost, for gate (d)

SELF-TEST: `--self-test` proves each classifier fires in BOTH directions on
hand-built records of the real shape, because every one of the twelve dead
detectors in this arc returned a plausible number while measuring something
else, and every one made the answer look better.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
RESULTS = BENCH / "results" / "bakeoff_2026_08" / "session-cost-eval"

# `Target not found` is the exact string `tool_context/targets.py` emitted
# before #1435 (targets.py:319, :322). Matched case-insensitively and without
# anchoring so a reworded-but-still-failing payload is not silently scored green.
#
# !! NOT SUFFICIENT ON ITS OWN AGAINST A POST-#1435 SERVER !!
# #1435 also made a miss "degrade to the file-level card rather than a bare
# Target not found". So on the binary under test a MISS RETURNS NO ERROR STRING:
# a bogus method comes back as a healthy ~6 KB `"type": "file"` payload. Counting
# not-found strings therefore reads a clean 0 whether the symbol resolved or
# every call silently fell back to a file card -- a plausible number that
# flatters the fix. Verified directly, see RESULT_S3C_SMOKE.md section 6.1.
#
# `DEGRADED_TO_FILE` is the real discriminator and is reported alongside.
NOT_FOUND = re.compile(r"target not found", re.I)
# A get_context payload that answered with a file card rather than a symbol card.
RESOLVED_AS_SYMBOL = re.compile(r'"type"\s*:\s*"symbol"')
RESOLVED_AS_FILE = re.compile(r'"type"\s*:\s*"file"')
DEGRADED = re.compile(r"\bdegraded\b|no-llm-provider|no synthesized answer", re.I)
# A `Class.method` target: a path, `::`, then a dotted name. This is the shape
# that failed on cell A; a plain file target is not evidence either way.
DOTTED_TARGET = re.compile(r"::\s*\w+\s*\.\s*\w+")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(_text(c) for c in content)
    if isinstance(content, dict):
        return _text(content.get("text") or content.get("content") or "")
    return ""


def read_task(path: Path) -> dict:
    """One task's transcript -> tool calls, and how each retrieval call ended."""
    calls: list[dict] = []
    pending: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "assistant":
            for c in d.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    rec = {"name": c.get("name") or "", "input": c.get("input") or {},
                           "result": None}
                    calls.append(rec)
                    if c.get("id"):
                        pending[c["id"]] = rec
        elif d.get("type") == "user":
            for c in (d.get("message", {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    rec = pending.pop(c.get("tool_use_id"), None)
                    if rec is not None:
                        rec["result"] = _text(c.get("content"))
    return {"task": path.stem.replace("_stream", ""), "calls": calls}


def summarise(tasks: list[dict]) -> dict:
    mcp = [c for t in tasks for c in t["calls"] if c["name"].startswith("mcp__")]
    ctx = [c for c in mcp if c["name"].endswith("get_context")]
    dotted = [c for c in ctx if DOTTED_TARGET.search(json.dumps(c["input"]))]
    by_tool: dict[str, int] = {}
    for c in mcp:
        by_tool[c["name"].split("__")[-1]] = by_tool.get(c["name"].split("__")[-1], 0) + 1
    return {
        "tool_calls_total": sum(len(t["calls"]) for t in tasks),
        "mcp_calls": len(mcp),
        "mcp_by_tool": dict(sorted(by_tool.items())),
        "get_context_calls": len(ctx),
        # Kept because it is the cell-A comparator, but read section 6.1 of
        # RESULT_S3C_SMOKE.md before quoting it: on a post-#1435 server this
        # goes to 0 whether or not anything resolved.
        "get_context_not_found": sum(
            1 for c in ctx if c["result"] and NOT_FOUND.search(c["result"])),
        "get_context_dotted_targets": len(dotted),
        "get_context_dotted_not_found": sum(
            1 for c in dotted if c["result"] and NOT_FOUND.search(c["result"])),
        # THE REAL DISCRIMINATOR. A dotted target answered with a file card is a
        # miss that the not-found counter above cannot see.
        "get_context_dotted_as_symbol": sum(
            1 for c in dotted
            if c["result"] and RESOLVED_AS_SYMBOL.search(c["result"])),
        "get_context_dotted_as_file": sum(
            1 for c in dotted
            if c["result"] and not RESOLVED_AS_SYMBOL.search(c["result"])
            and RESOLVED_AS_FILE.search(c["result"])),
        "degraded_payloads": sum(
            1 for c in mcp if c["result"] and DEGRADED.search(c["result"])),
    }


def self_test() -> int:
    """Every classifier, both directions, on the real record shape."""
    fails = []

    def ck(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name} {detail}")
        if not ok:
            fails.append(name)

    ck("NOT_FOUND fires", bool(NOT_FOUND.search(
        "{\"error\": \"Target not found: 'a.py::C.m'\"}")))
    ck("NOT_FOUND silent on a good payload", not NOT_FOUND.search(
        "| Type | file |\n| Summary | resolves fine |"))
    ck("DOTTED fires on Class.method", bool(DOTTED_TARGET.search(
        '{"targets": ["tools/todo_tool.py::TodoStore._dedupe_by_id"]}')))
    ck("DOTTED silent on a plain file target", not DOTTED_TARGET.search(
        '{"targets": ["tools/todo_tool.py"]}'))
    ck("DOTTED silent on Class::method", not DOTTED_TARGET.search(
        '{"targets": ["a.py::C::m"]}'))
    ck("DEGRADED fires", bool(DEGRADED.search('{"degraded": "no-llm-provider"}')))
    ck("DEGRADED silent on a real answer", not DEGRADED.search(
        '{"answer": "The list is built in gateway/x.py"}'))

    # The pairing walk, both directions: a result must attach to ITS call and a
    # call with no result must stay None. Pairing by position instead of by
    # tool_use_id is how a reader credits one tool's payload to another.
    rows = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "mcp__repowise__get_context",
             "input": {"targets": ["x.py::C.m"]}},
            {"type": "tool_use", "id": "b", "name": "Grep", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": "Target not found: 'x.py::C.m'"}]}},
    ]
    p = Path(__file__).with_name("_selftest_stream.jsonl")
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    got = read_task(p)
    s = summarise([got])
    p.unlink()
    ck("pairs result to its own call", got["calls"][0]["result"] is not None
       and got["calls"][1]["result"] is None)
    ck("counts a dotted not-found", s["get_context_dotted_not_found"] == 1, s)
    ck("does not count Grep as mcp", s["mcp_calls"] == 1, s)

    # And the negative direction of the headline count: a RESOLVING dotted call
    # must read zero, or the metric would report the bug whatever happened.
    rows[1]["message"]["content"][0]["content"] = '{"targets": {"x.py::C.m": {"type": "symbol"}}}'
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    s2 = summarise([read_task(p)])
    p.unlink()
    ck("a resolving dotted call reads zero not-found",
       s2["get_context_dotted_not_found"] == 0 and s2["get_context_dotted_targets"] == 1, s2)
    ck("a resolving dotted call counts as symbol",
       s2["get_context_dotted_as_symbol"] == 1 and s2["get_context_dotted_as_file"] == 0, s2)

    # THE POST-#1435 TRAP, held in both directions: a silent degrade to the file
    # card carries NO error string, so the not-found counter reads a clean zero
    # while the call in fact missed. The `as_file` counter must catch it.
    rows[1]["message"]["content"][0]["content"] = (
        '{"targets": {"x.py::C.m": {"type": "file", "docs": {"title": "File: x.py"}}}}')
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    s3 = summarise([read_task(p)])
    p.unlink()
    ck("silent file-card degrade reads zero not-found (the trap)",
       s3["get_context_dotted_not_found"] == 0, s3)
    ck("...and IS caught as a file-card miss",
       s3["get_context_dotted_as_file"] == 1 and s3["get_context_dotted_as_symbol"] == 0, s3)

    print(f"\nself-test {15 - len(fails)}/15")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--cell", default="cellB-hermes")
    ap.add_argument("--condition", default="unenforced")
    ap.add_argument("--arms", default="c0-bare,rw-full")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    out = {}
    for arm in a.arms.split(","):
        d = RESULTS / f"_status_{a.cell}_{arm}_{a.condition}"
        if not d.is_dir():
            out[arm] = {"error": f"no status dir at {d}"}
            continue
        tasks = [read_task(p) for p in sorted(d.glob("*_stream.jsonl"))]
        out[arm] = {"tasks": [t["task"] for t in tasks], **summarise(tasks)}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
