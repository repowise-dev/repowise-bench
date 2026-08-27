"""How many resolvable ids does a canonical MCP response hand back, and how
many of those are pure debt -- a pointer whose target is already sitting in
the same response?

41_PHASE4_MEASUREMENT.md section 2 names the question: a canonical response
that hands back an id instead of content has bought itself a follow-up call.
This is the deterministic proxy for that follow-up: emit a response, count
the ids in it, and check how many carry no usable content alongside, and how
many resolve to something the response already contained.

**Trap this script exists to not fall into (see the section-4a note):** a
`get_context` miss returns a file card, not an error. `mcp_isError == 0` is
never evidence an id resolved -- this script never calls the id's target
tool, it only inspects what the *emitting* response already carries, so it
cannot make that mistake, but a caller reading its output has to remember
the same rule when this script's numbers are combined with anything else.

--------------------------------------------------------------------------
WHAT COUNTS AS AN ID
--------------------------------------------------------------------------

Three shapes, matched over the raw wire text (the same bytes the agent pays
for and would copy into a follow-up call):

  * ``repowise#<hex>``           -- an omission / evidence reference
  * ``path.ext::Name[::Name]``   -- a symbol id
  * ``path.ext:<line>-<line>``   -- a line-range id

A regex sweep over the raw text, not a JSON walk, so a free-text mention
inside ``note`` or ``next_action_hint`` ("call get_symbol id='...'") counts
exactly the same as one sitting in a structured ``symbol_id`` field --
both are equally an invitation to spend another call.

--------------------------------------------------------------------------
WHAT COUNTS AS DEBT
--------------------------------------------------------------------------

The response is also parsed as JSON and walked structurally to find, for
each *distinct* id:

  * whether some dict in the response carries that id (via ``symbol_id``, or
    via a ``path``+``name`` pair matching a ``path.ext::name`` id) alongside
    a non-empty ``signature`` or ``source`` field -- category (c);
  * whether that same dict's ``source`` (a full body, not just a signature)
    is already present in the response -- category (d), pure debt, because
    calling the id's target tool would return content the caller already
    has.

Category (c) counts a bare signature as "carries something", because a
signature can already answer a shape question. Category (d) requires the
full body, because a signature is not what a ``get_symbol`` call returns
that a caller doesn't already have -- the whole point of following the
pointer is the body.

Undercount, in the safe direction: a free-text id (from ``note`` /
``next_action_hint``) that is never backed by a structured dict scores 0 for
both (c) and (d) even when a human would recognise its target from prose
elsewhere in the response. This script does not read prose for content, only
for id-shaped tokens.

Usage:
    python pointer_debt.py <out.json> [--tool NAME=ARGS_JSON ...]
    python pointer_debt.py --self-test
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TREE = Path(os.environ.get("BENCH_TREE") or r"C:\Users\ragha\Desktop\bakeoff\se-payload-rich")
BIN = Path(
    os.environ.get("BENCH_BIN")
    or r"C:\Users\ragha\Desktop\repowise-sessioneval\.venv\Scripts\repowise.exe"
)

# The two calls 41_PHASE4_MEASUREMENT.md section 2 recorded a reading for.
CALLS = [
    ("get_answer", {"question": "how does Text.from_ansi decode escape sequences?"}),
    ("get_context", {"targets": ["rich/ansi.py"]}),
]

# Baseline reading, 2026-08-26, same tree, recorded in section 2.
BASELINE = {
    "get_answer": {"chars": 6450, "ids_emitted": 2, "distinct_ids": 1},
    "get_context": {"chars": 3087, "ids_emitted": 9, "distinct_ids": 6},
}

_ID_RE = re.compile(
    r"repowise#[0-9a-f]{6,}"
    r"|[A-Za-z0-9_][\w./\\-]*\.[A-Za-z]{1,6}::[A-Za-z_][\w.:]*"
    r"|[A-Za-z0-9_][\w./\\-]*\.[A-Za-z]{1,6}:\d+-\d+"
)


def extract_ids(raw_text: str) -> list[str]:
    """All id-shaped tokens in the raw wire text, in order, not deduped."""
    return _ID_RE.findall(raw_text)


def _walk_collect(node: Any, sig_bearing: set[str], body_texts: dict[str, str]) -> None:
    """Populate ``sig_bearing`` (has signature or source) and ``body_texts``
    (has a full source body) for every id-shaped key this dict carries."""
    if isinstance(node, dict):
        sid = node.get("symbol_id")
        sig = node.get("signature")
        src = node.get("source")
        if sid and (sig or src):
            sig_bearing.add(sid)
        if sid and src:
            body_texts[sid] = src
        # symbol_bodies / quotes entries key by path+name rather than a
        # symbol_id field -- reconstruct the id shape those two would form.
        path, name = node.get("path"), node.get("name")
        if src and path and name:
            alt_id = f"{path}::{name}"
            sig_bearing.add(alt_id)
            body_texts[alt_id] = src
        for v in node.values():
            _walk_collect(v, sig_bearing, body_texts)
    elif isinstance(node, list):
        for v in node:
            _walk_collect(v, sig_bearing, body_texts)


def analyze(raw_text: str) -> dict:
    """Ids emitted / distinct / carrying content / pure debt, for one raw
    tool-result text."""
    ids = extract_ids(raw_text)
    distinct = sorted(set(ids))

    sig_bearing: set[str] = set()
    body_texts: dict[str, str] = {}
    try:
        payload = json.loads(raw_text)
    except ValueError:
        payload = None
    if payload is not None:
        _walk_collect(payload, sig_bearing, body_texts)

    with_body_or_sig = [i for i in distinct if i in sig_bearing]
    redundant = [i for i in distinct if i in body_texts]

    return {
        "chars": len(raw_text),
        "ids_emitted": len(ids),
        "distinct_ids": len(distinct),
        "distinct_list": distinct,
        "with_body_or_signature": len(with_body_or_sig),
        "redundant_pure_debt": len(redundant),
        "redundant_list": redundant,
    }


async def _run(calls: list[tuple[str, dict]]) -> dict[str, str]:
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1"})
    params = StdioServerParameters(command=str(BIN), args=["mcp", str(TREE), "--transport", "stdio"],
                                   env=env, cwd=str(TREE))
    raw: dict[str, str] = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            served = {t.name for t in (await session.list_tools()).tools}
            for name, kwargs in calls:
                if name not in served:
                    raw[name] = json.dumps({"error": "not served"})
                    continue
                res = await session.call_tool(name, kwargs)
                raw[name] = "".join(c.text for c in res.content if getattr(c, "text", None))
    return raw


async def main() -> None:
    raw = await _run(CALLS)
    report = {}
    for name, _ in CALLS:
        a = analyze(raw[name])
        base = BASELINE.get(name, {})
        report[name] = {**a, "baseline": base}
        print(f"\n{name}")
        print(f"  chars              baseline {base.get('chars')!r:>8}  now {a['chars']}")
        print(f"  ids emitted        baseline {base.get('ids_emitted')!r:>8}  now {a['ids_emitted']}")
        print(f"  distinct ids       baseline {base.get('distinct_ids')!r:>8}  now {a['distinct_ids']}")
        print(f"  with body/signature (now only): {a['with_body_or_signature']}")
        print(f"  pure debt (now only): {a['redundant_pure_debt']} -> {a['redundant_list']}")

    out = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else Path("pointer_debt.json")
    out.write_text(json.dumps({"raw": raw, "report": report}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


def self_test() -> int:
    """The id-counting and debt-detection must fire in BOTH directions: a
    response with ids present, and one with none. Five dead detectors have
    shipped in this workstream already, every one of them plausible."""
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # A response with no ids at all: extraction and analysis must both
    # report zero, not error out or silently invent a match.
    empty = json.dumps({"answer": "plain prose, no pointers here", "confidence": "high"})
    a = analyze(empty)
    check("no-id response: ids_emitted", a["ids_emitted"], 0)
    check("no-id response: distinct_ids", a["distinct_ids"], 0)
    check("no-id response: pure debt", a["redundant_pure_debt"], 0)

    # A response with ids but no bodies (mirrors get_context's shape): ids
    # must be found and counted, but nothing should be flagged as debt
    # since only a signature -- no source -- rides along.
    with_sig_only = json.dumps({
        "targets": {
            "pkg/mod.py": {
                "docs": {"symbols": [
                    {"name": "Foo", "kind": "class", "signature": "class Foo",
                     "line": 1, "symbol_id": "pkg/mod.py::Foo"},
                    {"name": "bar", "kind": "function", "signature": "def bar()",
                     "line": 5, "symbol_id": "pkg/mod.py::bar"},
                ]}
            }
        }
    })
    b = analyze(with_sig_only)
    check("sig-only response: ids_emitted", b["ids_emitted"], 2)
    check("sig-only response: distinct_ids", b["distinct_ids"], 2)
    check("sig-only response: with_body_or_signature", b["with_body_or_signature"], 2)
    check("sig-only response: pure debt", b["redundant_pure_debt"], 0)

    # A response that emits an id AND already carries that id's full body:
    # this is the case that must be flagged, or the detector cannot see the
    # thing it exists to see.
    with_redundant_body = json.dumps({
        "note": "call get_symbol id='pkg/mod.py::baz' for the full body",
        "symbol_bodies": [
            {"path": "pkg/mod.py", "name": "baz", "lines": [1, 3], "source": "def baz(): ..."}
        ],
    })
    c = analyze(with_redundant_body)
    check("redundant response: ids_emitted", c["ids_emitted"], 1)
    check("redundant response: distinct_ids", c["distinct_ids"], 1)
    check("redundant response: with_body_or_signature", c["with_body_or_signature"], 1)
    check("redundant response: pure debt", c["redundant_pure_debt"], 1)
    check("redundant response: names the id", c["redundant_list"], ["pkg/mod.py::baz"])

    # An omission-reference id (repowise#<hex>) must be matched by the regex
    # even with no structured dict backing it -- it carries no body/signature
    # and is not debt, since its whole point is that the content is elsewhere.
    with_omission_ref = json.dumps({
        "omitted": {"refs": ["repowise#deadbeef1234"]},
        "omission_marker": "[repowise#deadbeef1234: 40 lines omitted]",
    })
    d = analyze(with_omission_ref)
    check("omission-ref response: ids_emitted", d["ids_emitted"], 2)
    check("omission-ref response: distinct_ids", d["distinct_ids"], 1)
    check("omission-ref response: with_body_or_signature", d["with_body_or_signature"], 0)
    check("omission-ref response: pure debt", d["redundant_pure_debt"], 0)

    if failures:
        for f in failures:
            print("FAIL", f)
        return 1
    print("self-test: id counting and debt detection fire in both directions")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    asyncio.run(main())
