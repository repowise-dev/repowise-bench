"""Does `get_context` resolve the ids `get_symbol` hands out, on the cell-B index?

This is `MCP_BEHAVIOUR_FINDINGS.md` defect 2a, re-run against hermes on a binary
containing #1435 (f82ebb0d). On cell A it failed **11 of 26 get_context calls
(42%)**, and every failure was a `Class.method` target while every success was a
plain file target.

Run against the REAL MCP SERVER over stdio, not against the tool functions
in-process, because the agent talks to the server and an in-process call skips
the resolution path the server actually uses.

THE DISCRIMINATOR IS `type`, NOT THE ABSENCE OF AN ERROR
--------------------------------------------------------
#1435 shipped TWO changes: separator normalisation, and "degrades to the
file-level card rather than a bare `Target not found`". The second one means
**a miss no longer produces an error string.** Asking a post-#1435 server for a
symbol that does not exist returns a perfectly healthy-looking payload:

    real symbol  ->  "type": "symbol", docs.name/_kind/_signature for the method
    bogus symbol ->  "type": "file",   the whole-file card, ~6 KB, no error

So "the payload did not say `Target not found`" is **worthless** as evidence of
resolution on this build, and any detector counting not-found strings will read
a clean 0 even if every single call silently fell back to a file card. That is
dead-detector shape: a plausible number that flatters the fix.

This probe therefore asserts `type == "symbol"`.

BOTH DIRECTIONS, so a green here is not a green for every input:
  * `Class.method` targets -- the shape that failed on cell A -- must be
    `type: symbol`;
  * `Class::method` targets, the shape that ALWAYS worked, as the positive
    control. If these fail too the server is broken generally and the dotted
    result says nothing about #1435;
  * a deliberately bogus method, which must come back `type: file` -- the
    DESIGNED degradation. If it came back `type: symbol` the server resolves
    everything and the dotted result would mean nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TREE = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\ragha\Desktop\bakeoff\se-rw-full-hermes"
EXE = r"C:\Users\ragha\Desktop\repowise\.venv\Scripts\repowise.exe"


def rpc(proc, mid, method, params):
    proc.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == mid:
            return msg


def call(proc, mid, name, args):
    r = rpc(proc, mid, "tools/call", {"name": name, "arguments": args})
    if r is None:
        return "<no response>"
    res = r.get("result") or {}
    if r.get("error"):
        return f"<rpc error: {r['error']}>"
    parts = res.get("content") or []
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict))


def main() -> int:
    env = {**os.environ, "DO_NOT_TRACK": "1", "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8", "REPOWISE_SKIP_EDITOR_SETUP": "1"}
    for line in Path(r"C:\Users\ragha\Desktop\repowise\.env").read_text(
            encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            if k.strip() in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
                env[k.strip()] = v.strip().strip('"').strip("'")

    proc = subprocess.Popen(
        [EXE, "mcp", TREE, "--transport", "stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
    try:
        rpc(proc, 1, "initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "probe1435", "version": "1"}})
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # Targets taken from the modules this cell's oracles actually touch, so
        # they are symbols the index certainly contains.
        dotted = [
            "tools/todo_tool.py::TodoStore._dedupe_by_id",
            "tools/todo_tool.py::TodoStore.write",
            "agent/display.py::build_tool_preview",
            "hermes_cli/web_server.py::_probe_gateway_health",
        ]
        # `Class::method`, built by replacing the dot in the SYMBOL part only.
        # An earlier version did `t.replace(".", "::", 1)`, which hit the `.py`
        # and produced `tools/todo_tool::py::TodoStore…` -- a garbage target that
        # correctly failed and would have been read as the control failing.
        def to_colons(t: str) -> str:
            path, _, sym = t.partition("::")
            return f"{path}::{sym.replace('.', '::', 1)}" if "." in sym else t
        colons = [to_colons(t) for t in dotted[:2]]
        bogus = ["tools/todo_tool.py::TodoStore.this_method_does_not_exist_xyz"]

        def kind_of(payload: str, target: str) -> str:
            try:
                return (json.loads(payload)["targets"][target].get("type")
                        or "<no type>")
            except Exception:
                return "<unparseable>"

        mid = 10
        rows = []
        for label, targets in (("DOTTED Class.method", dotted),
                               ("CONTROL Class::method", colons),
                               ("NEGATIVE bogus method", bogus)):
            for t in targets:
                mid += 1
                out = call(proc, mid, "get_context", {"targets": [t]})
                k = kind_of(out, t)
                rows.append((label, t, k))
                print(f"type={k:<14} [{label}]  {t}  ({len(out)} chars)")

        print()
        d = [r for r in rows if r[0].startswith("DOTTED")]
        c = [r for r in rows if r[0].startswith("CONTROL")]
        n = [r for r in rows if r[0].startswith("NEGATIVE")]
        d_ok = sum(1 for r in d if r[2] == "symbol")
        c_ok = sum(1 for r in c if r[2] == "symbol")
        n_ok = sum(1 for r in n if r[2] == "file")
        print(f"dotted  type==symbol {d_ok}/{len(d)}")
        print(f"control type==symbol {c_ok}/{len(c)}"
              f"   (must be all, else the server is broken generally)")
        print(f"bogus   type==file   {n_ok}/{len(n)}"
              f"   (must be all: a bogus method must DEGRADE, not resolve)")
        ok = d_ok == len(d) and c_ok == len(c) and n_ok == len(n)
        print("\nVERDICT:", "#1435 RESOLVES Class.method on cell B" if ok
              else "NOT a clean pass -- read the rows above")
        return 0 if ok else 1
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
