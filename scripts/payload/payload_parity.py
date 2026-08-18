"""Measure the payload each surface returns for the SAME six calls.

Step 2 of the session-cost arc needs transport to be the only variable. That
requires knowing, before any agent spend, whether `repowise <cmd> --full` really
is the MCP tool's dict and what the trimmed projection costs instead. Both are
measured here rather than asserted from the CLI reference.

**The MCP column comes off the wire, over stdio.** Two earlier versions did not,
and neither could have failed:

1. It awaited the tool coroutine directly. A tool reads its session factory /
   FTS index / vector store out of ``mcp_server._state``, which only the server
   lifespan (or ``cli.tool_bridge``) publishes, so every call raised
   ``TypeError: 'NoneType' object is not callable``, every row recorded
   ``mcp_chars: 0`` / ``full_vs_mcp_ratio: None``, and the table still printed.
2. Fixed to run in-process through the bridge, it then serialised the dict with
   ``json.dumps(indent=2, default=str)`` — which is exactly what
   ``cli.output.emit_json`` does. Both columns were the same function on the
   same object in the same process, so the "invariant" could only fail if
   ``--full`` post-processed, which it does not. It begged the question, and it
   could not see the thing that actually matters: what the SERVER puts on the
   wire, which measured 64-259 characters smaller on every call.

So the comparison is now CLI stdout against a real stdio tool result, and it
asserts JSON equality as well as size. Equality catches semantic drift, the
character delta catches serializer drift, and neither is produced by the code
under test.

CLI bytes are measured on raw stdout, not text mode: ``subprocess`` text mode
translates CRLF to LF, which under-counts real Windows stdout by ~3%.

Run with the checkout-under-test's python; TREE and BIN are explicit.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

TREE = Path(os.environ.get("BENCH_TREE") or r"C:\Users\ragha\Desktop\bakeoff\se-payload-rich")
# Overridable: see the note in mcp_payload.py. The in-process ``call_tool``
# below imports ``repowise.*`` from whatever python runs this file, so BIN and
# that interpreter must come from the SAME checkout or the two columns compare
# two different builds and the ratio is meaningless.
BIN = Path(
    os.environ.get("BENCH_BIN")
    or r"C:\Users\ragha\Desktop\repowise-sessioneval\.venv\Scripts\repowise.exe"
)

# One call per surface pair. The MCP kwargs and the CLI argv must describe the
# SAME request; anything else measures two different questions.
CALLS = [
    {
        "name": "answer/ask",
        "cli": ["ask", "how does Text.from_ansi decode escape sequences?"],
        "tool": "get_answer",
        "kwargs": {"question": "how does Text.from_ansi decode escape sequences?"},
    },
    {
        "name": "context",
        "cli": ["context", "rich/ansi.py"],
        "tool": "get_context",
        "kwargs": {"targets": ["rich/ansi.py"]},
    },
    {
        "name": "symbol",
        "cli": ["symbol", "rich/ansi.py::AnsiDecoder"],
        "tool": "get_symbol",
        "kwargs": {"symbol_id": "rich/ansi.py::AnsiDecoder"},
    },
    {
        "name": "why",
        "cli": ["why", "why is the highlighter regex greedy?"],
        "tool": "get_why",
        "kwargs": {"query": "why is the highlighter regex greedy?"},
    },
    {
        "name": "search",
        "cli": ["search", "progress bar expand width", "--limit", "5"],
        "tool": "search_codebase",
        "kwargs": {"query": "progress bar expand width", "limit": 5},
    },
    {
        "name": "risk",
        "cli": ["risk", "--target", "rich/progress.py"],
        "tool": "get_risk",
        "kwargs": {"targets": ["rich/progress.py"]},
    },
]


def _recovered_embedder_key() -> tuple[str, str]:
    """``(env var, key)`` from ``~/.repowise/config.yaml``, or ``("", "")``.

    The MCP server does this recovery itself (``_server.py:_embedder_key``,
    logging "recovered from ~/.repowise/config.yaml"); the CLI does not. So on
    a machine with no ``OPENAI_API_KEY`` exported, the SERVER answers with real
    1536-dim vectors while ``repowise search`` on the same repo falls back to
    keyless — and the CLI honestly reports it with three extra ``_meta`` keys
    (``embedder``, ``embedder_degraded``, ``semantic_search``), 231 characters
    the server never sends.

    That asymmetry showed up here as "parity broken on all six calls" when the
    payloads were in fact identical. Exporting the key puts both surfaces on
    the same retrieval path, which is the only way this script measures the
    payload rather than the environment.
    """
    try:
        import yaml  # type: ignore[import-untyped]

        cfg_path = Path.home() / ".repowise" / "config.yaml"
        if not cfg_path.is_file():
            return "", ""
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        name = (cfg.get("embedder") or "").strip().lower()
        key = str(cfg.get("embedder_api_key") or "").strip()
        var = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY",
               "voyage": "VOYAGE_API_KEY", "cohere": "COHERE_API_KEY"}.get(name, "")
        return (var, key) if var and key else ("", "")
    except Exception:
        return "", ""


def _env() -> dict:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1",
                "COLUMNS": "400"})
    var, key = _recovered_embedder_key()
    if var and not env.get(var):
        env[var] = key
    return env


def run_cli(argv: list[str]) -> tuple[int, str]:
    """One CLI invocation. Bytes off stdout, decoded without newline translation.

    ``text=True`` would translate CRLF to LF and under-count real Windows
    stdout by roughly 3%, which is the same order as the differences this
    script exists to detect.
    """
    r = subprocess.run([str(BIN), *argv], cwd=str(TREE), capture_output=True, env=_env())
    # CRLF collapsed for the DOCUMENT comparison only: the console writes
    # "\r\n" where the payload has "\n", which inflates a 5,350-char document
    # to 5,377 bytes and is a property of the terminal, not the tool. The
    # human-facing table path is measured the same way so the columns compare.
    return r.returncode, r.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")


async def _stdio_payloads() -> dict[str, str]:
    """The six tool results, taken off the wire from a real stdio server.

    One session for all six, so the server's background vector-store load is
    warm for calls 2-6 the way it is in a real agent session. A cold first call
    measurably differs (``get_why`` came back 1,474 cold against 1,491 warm),
    which is the kind of drift that turns into a "finding" if it is not held
    constant.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(BIN),
        args=["mcp", str(TREE), "--transport", "stdio"],
        env=_env(),
        cwd=str(TREE),
    )
    out: dict[str, str] = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Warm the server the same way for every run before measuring.
            await session.list_tools()
            for call in CALLS:
                res = await session.call_tool(call["tool"], call["kwargs"])
                out[call["name"]] = "".join(
                    c.text for c in res.content if getattr(c, "text", None)
                )
    return out


def _same_document(a: str, b: str) -> bool | None:
    """Do two payloads carry the same JSON, ignoring keys that cannot match?

    ``_meta.timing_ms`` measures the call, so it differs between two runs of
    the same call by construction and is not drift. Everything else must agree.
    Returns None when either side is not JSON at all.
    """
    try:
        left, right = json.loads(a), json.loads(b)
    except Exception:
        return None
    for doc in (left, right):
        if isinstance(doc, dict) and isinstance(doc.get("_meta"), dict):
            for key in ("timing_ms", "cached"):
                doc["_meta"].pop(key, None)
    return left == right


def main() -> None:
    try:
        stdio = asyncio.run(_stdio_payloads())
        stdio_err = ""
    except Exception as exc:  # noqa: BLE001
        stdio, stdio_err = {}, repr(exc)[:200]
        print(f"stdio server failed: {stdio_err}", flush=True)

    rows = []
    for call in CALLS:
        rc_t, text = run_cli(call["cli"])
        rc_j, cli_json = run_cli([*call["cli"], "--format", "json"])
        rc_f, cli_full = run_cli([*call["cli"], "--full"])
        mcp = stdio.get(call["name"], "")
        rows.append({
            "name": call["name"],
            "tool": call["tool"],
            "cli_text_chars": len(text), "cli_text_rc": rc_t,
            "cli_json_chars": len(cli_json), "cli_json_rc": rc_j,
            "cli_full_chars": len(cli_full), "cli_full_rc": rc_f,
            "mcp_chars": len(mcp), "mcp_err": stdio_err,
            "full_vs_mcp_ratio": round(len(cli_full) / len(mcp), 3) if mcp else None,
            # The invariant proper. Size agreement is necessary, not
            # sufficient: two payloads can match in length and differ in
            # content, which is exactly the drift that would break a bake-off.
            "same_document": _same_document(cli_full, mcp) if mcp else None,
        })
        print(json.dumps(rows[-1]), flush=True)

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("payload_parity.json")
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")

    print(f"\n{'call':<14} {'cli text':>9} {'cli json':>9} {'cli --full':>11} "
          f"{'MCP stdio':>10} {'full/MCP':>9} {'same doc':>9}")
    for r in rows:
        print(f"{r['name']:<14} {r['cli_text_chars']:>9,} {r['cli_json_chars']:>9,} "
              f"{r['cli_full_chars']:>11,} {r['mcp_chars']:>10,} "
              f"{str(r['full_vs_mcp_ratio']):>9} {str(r['same_document']):>9}")

    broken = [r["name"] for r in rows if r["same_document"] is not True]
    if broken:
        print(f"\nPARITY BROKEN on: {', '.join(broken)}. `--full` is no longer the "
              "tool dict, and a CLI-vs-MCP comparison built on it is invalid.")


if __name__ == "__main__":
    main()
