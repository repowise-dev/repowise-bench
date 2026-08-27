"""Measure how the MCP tools are actually used in uncoached Codex sessions.

The bench arms coach the agent: a system block advertises the tools, and a
force_tool_use cap shapes the mix. Codex rollout transcripts have neither, so
they are the only uncontaminated read on real usage.

Counting rule, and the three ways a naive probe gets it wrong:

1. A transcript that *mentions* a tool has not called it. Every rollout ships
   the tool schema, so raw name matching overcounts by roughly 3x.
2. An MCP call is not a ``function_call`` record. Codex routes almost all of
   them through ``custom_tool_call``; reading only ``function_call`` finds
   about a dozen calls in a corpus with hundreds.
3. Most calls arrive inside a ``custom_tool_call`` named ``exec`` whose input
   is JavaScript batching several MCP calls in one ``Promise.all``. One tool
   per record undercounts and mis-attributes to whichever name appears first.

So: count ``mcp__repowise__<tool>(`` occurrences inside the call input, and
exclude ``ALL_TOOLS.filter(...)`` listings, which name tools without calling
them.

Usage:
    python analysis/codex_tool_usage.py --sessions ~/.codex/sessions --out out.json
    python analysis/codex_tool_usage.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOL_CALL_RE = re.compile(r"mcp__repowise__([a-z_]+)\s*\(")
# ALL_TOOLS.filter(...) enumerates tool names without calling any of them.
LISTING_RE = re.compile(r"ALL_TOOLS\s*\.\s*(?:filter|map|forEach|find|includes)\s*\(")
MENTION_RE = re.compile(r"mcp__repowise__([a-z_]+)")

# A shell read of a file the agent could have got from a tool instead.
READ_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)(cat|head|tail|sed|type|Get-Content|less|more)\b", re.IGNORECASE
)
# Paths inside a JS argument object or a shell command.
PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|yaml|yml|json|toml|rs|go|java|cs)\b")
SYMBOL_ID_RE = re.compile(r"([\w./\\-]+\.\w+)::")


def _iter_records(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _call_input(payload: dict) -> str:
    """The text an MCP call would appear in, for either record shape."""
    if payload.get("type") == "custom_tool_call":
        return payload.get("input") or ""
    if payload.get("type") == "function_call":
        return payload.get("arguments") or ""
    return ""


def count_calls(text: str) -> list[str]:
    """Tool names actually invoked in one call input, in source order."""
    if not text:
        return []
    # Blank out listing expressions so their tool names do not count as calls.
    scrubbed = LISTING_RE.sub("__LISTING__(", text)
    return TOOL_CALL_RE.findall(scrubbed)


def _norm(p: str) -> str:
    return p.replace("\\", "/").lstrip("./").lower()


def _targets(text: str) -> set[str]:
    """Files a call or command names, normalised for comparison."""
    out = {_norm(m) for m in PATH_RE.findall(text)}
    out |= {_norm(m) for m in SYMBOL_ID_RE.findall(text)}
    return {t for t in out if "/" in t}


def _shell_command(payload: dict) -> str:
    """The shell text of an exec_command / shell_command, if this is one."""
    text = _call_input(payload)
    if not text:
        return ""
    if "exec_command" in text or "shell_command" in text or payload.get("name") in (
        "shell_command",
        "exec_command",
    ):
        return text
    return ""


def mentions_a_tool(path: Path) -> bool:
    """Trap 1: every rollout ships the tool schema, so a name match anywhere in
    the transcript proves nothing. Measured so the overcount is visible."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        return any("mcp__repowise__" in chunk for chunk in iter(lambda: fh.read(1 << 20), ""))


def analyse_session(path: Path) -> dict:
    """One transcript reduced to its call sequence and the reads around it."""
    events: list[dict] = []  # ordered: {kind, tools, targets, text}
    mentions: set[str] = set()

    for rec in _iter_records(path):
        payload = rec.get("payload") or {}
        ptype = payload.get("type")
        if ptype not in ("custom_tool_call", "function_call"):
            continue
        text = _call_input(payload)
        name = payload.get("name") or ""

        # A bare function_call named after a tool is itself a call. There are
        # only a dozen of these in the corpus, but trap 2 says they exist.
        direct = [name] if ptype == "function_call" and name in KNOWN_TOOLS else []
        tools = count_calls(text) + direct
        mentions |= set(MENTION_RE.findall(text))

        if tools:
            events.append(
                {
                    "kind": "mcp",
                    "tools": tools,
                    "targets": sorted(_targets(text)),
                    "batch": len(tools),
                }
            )
        shell = _shell_command(payload)
        if shell and READ_CMD_RE.search(shell):
            events.append(
                {"kind": "read", "tools": [], "targets": sorted(_targets(shell)), "batch": 0}
            )
        elif shell:
            events.append({"kind": "shell", "tools": [], "targets": [], "batch": 0})

    calls = [t for e in events if e["kind"] == "mcp" for t in e["tools"]]
    return {
        "path": str(path),
        "events": events,
        "calls": calls,
        "n_calls": len(calls),
        "mentions": sorted(mentions),
        "called": bool(calls),
    }


KNOWN_TOOLS = {
    "get_answer",
    "get_context",
    "get_symbol",
    "search_codebase",
    "get_why",
    "get_risk",
    "get_health",
    "get_overview",
    "get_dead_code",
    "get_change_risk",
    "list_repos",
    "get_security",
    "get_architecture",
    "get_blast_radius",
}


def read_displacement(sessions: list[dict], window: int = 3) -> dict:
    """Does a tool call replace a raw read of the same file, or add to it?

    For every MCP call naming at least one file, look ahead `window` events
    for a shell read of one of those same files. A hit means the tool did not
    displace the read; the agent paid for both.
    """
    per_tool = defaultdict(lambda: {"targeted": 0, "followed_by_read": 0})
    for sess in sessions:
        events = sess["events"]
        for i, ev in enumerate(events):
            if ev["kind"] != "mcp" or not ev["targets"]:
                continue
            targets = set(ev["targets"])
            follow = False
            for nxt in events[i + 1 : i + 1 + window]:
                if nxt["kind"] == "read" and targets & set(nxt["targets"]):
                    follow = True
                    break
            for tool in set(ev["tools"]):
                per_tool[tool]["targeted"] += 1
                if follow:
                    per_tool[tool]["followed_by_read"] += 1
    return {
        tool: {
            **v,
            "read_after_pct": round(100.0 * v["followed_by_read"] / v["targeted"], 1)
            if v["targeted"]
            else None,
        }
        for tool, v in sorted(per_tool.items())
    }


def successors(sessions: list[dict], of: str) -> dict:
    """What the agent calls next, after `of` — within a batch and after it."""
    same_batch = Counter()
    next_call = Counter()
    next_event = Counter()
    for sess in sessions:
        events = sess["events"]
        mcp_idx = [i for i, e in enumerate(events) if e["kind"] == "mcp"]
        for i in mcp_idx:
            tools = events[i]["tools"]
            if of not in tools:
                continue
            for t in tools:
                if t != of:
                    same_batch[t] += 1
            after = [j for j in mcp_idx if j > i]
            if after:
                for t in events[after[0]]["tools"]:
                    next_call[t] += 1
            else:
                next_call["<none: end of session>"] += 1
            if i + 1 < len(events):
                next_event[events[i + 1]["kind"]] += 1
            else:
                next_event["<end>"] += 1
    return {
        "same_batch": dict(same_batch.most_common()),
        "next_mcp_call": dict(next_call.most_common()),
        "next_event_kind": dict(next_event.most_common()),
    }


def walks(sessions: list[dict], tool: str = "get_symbol") -> dict:
    """Runs of consecutive `tool` calls, and whether a run stays in one file.

    A run of length 1 is a lookup. A long run is the walk the response
    contract is meant to remove — and a run that never leaves one file is the
    case the guidance explicitly says to do with a plain read instead.
    """
    lengths: list[int] = []
    single_file = 0
    run: list[list[str]] = []

    def close():
        nonlocal single_file, run
        if run:
            lengths.append(len(run))
            files = {t for step in run for t in step}
            if len(files) == 1 and len(run) > 1:
                single_file += 1
        run = []

    for sess in sessions:
        for ev in sess["events"]:
            if ev["kind"] != "mcp":
                continue
            for name in ev["tools"]:
                if name == tool:
                    run.append(ev["targets"])
                else:
                    close()
        close()

    multi = [n for n in lengths if n > 1]
    return {
        "runs": len(lengths),
        "calls_in_runs": sum(lengths),
        "longest_run": max(lengths) if lengths else 0,
        "mean_run": round(statistics.mean(lengths), 2) if lengths else 0,
        "runs_longer_than_one": len(multi),
        "calls_inside_multi_runs": sum(multi),
        "share_of_calls_in_a_walk_pct": round(100.0 * sum(multi) / sum(lengths), 1)
        if lengths
        else 0,
        "multi_runs_confined_to_one_file": single_file,
    }


def co_occurrence(sessions: list[dict]) -> dict:
    """Tool pairs that show up in the same session."""
    pairs = Counter()
    for sess in sessions:
        present = sorted(set(sess["calls"]))
        for a_i, a in enumerate(present):
            for b in present[a_i + 1 :]:
                pairs[f"{a}+{b}"] += 1
    return dict(pairs.most_common(20))


def build_report(root: Path) -> dict:
    files = sorted(root.rglob("*.jsonl"))
    mentioning = 0
    sessions = []
    for path in files:
        if mentions_a_tool(path):
            mentioning += 1
        sess = analyse_session(path)
        if sess["called"]:
            sessions.append(sess)

    calls = Counter(t for s in sessions for t in s["calls"])
    total = sum(calls.values())
    per_session = sorted(s["n_calls"] for s in sessions)
    batches = [e["batch"] for s in sessions for e in s["events"] if e["kind"] == "mcp"]

    return {
        "corpus": {
            "transcripts": len(files),
            "mentioning_a_tool": mentioning,
            "actually_calling": len(sessions),
            "invocations": total,
        },
        "mix": {
            tool: {"calls": n, "share_pct": round(100.0 * n / total, 1)}
            for tool, n in calls.most_common()
        },
        "per_session_calls": {
            "median": statistics.median(per_session) if per_session else 0,
            "mean": round(statistics.mean(per_session), 1) if per_session else 0,
            "max": max(per_session) if per_session else 0,
            "distribution": per_session,
        },
        "batching": {
            "mcp_bearing_records": len(batches),
            "calls_per_record_mean": round(statistics.mean(batches), 2) if batches else 0,
            "calls_per_record_max": max(batches) if batches else 0,
            "batched_records_pct": round(
                100.0 * sum(1 for b in batches if b > 1) / len(batches), 1
            )
            if batches
            else 0,
        },
        "read_displacement": read_displacement(sessions),
        "walks": {t: walks(sessions, t) for t in ("get_symbol", "get_answer", "get_context")},
        "after_get_symbol": successors(sessions, "get_symbol"),
        "after_get_answer": successors(sessions, "get_answer"),
        "after_get_context": successors(sessions, "get_context"),
        "co_occurrence": co_occurrence(sessions),
        "sequences": {
            Path(s["path"]).name: s["calls"] for s in sessions if s["n_calls"] >= 5
        },
    }


def self_test() -> int:
    """Every counting rule here exists because a probe got it wrong. Fire each."""
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # Trap 1: a mention is not a call.
    check("mention only", count_calls("available: mcp__repowise__get_symbol"), [])
    check("mention only, negative control", MENTION_RE.findall("mcp__repowise__get_symbol"), ["get_symbol"])

    # Trap 3a: a batch holds several calls, all of which count.
    batch = (
        "const [a,b,c] = await Promise.all(["
        "tools.mcp__repowise__get_context({}),"
        "tools.mcp__repowise__get_health({}),"
        "tools.mcp__repowise__get_context({})]);"
    )
    check("batch of three", count_calls(batch), ["get_context", "get_health", "get_context"])

    # Trap 3b: a listing names tools without calling them.
    listing = "const names = ALL_TOOLS.filter(t => t.startsWith('mcp__repowise__get_symbol'));"
    check("listing excluded", count_calls(listing), [])
    # ...and the negative control: the same text without the listing wrapper counts.
    check(
        "listing negative control",
        count_calls("tools.mcp__repowise__get_symbol({id:'a'})"),
        ["get_symbol"],
    )

    # A local helper sharing a tool's name is not an MCP call.
    check("bare local helper", count_calls("function get_health(x){}\nget_health(1);"), [])

    # Targets are extracted from both a JS arg object and a shell command.
    check(
        "targets from js",
        _targets('tools.mcp__repowise__get_symbol({id:"pkg/mod/file.py::Thing"})'),
        {"pkg/mod/file.py"},
    )
    check("read command detected", bool(READ_CMD_RE.search("sed -n '1,80p' pkg/a.py")), True)
    check("non-read command", bool(READ_CMD_RE.search("pytest -q tests/")), False)

    # read_displacement must find a read of the same file, and must not
    # find one for a different file.
    hit = [
        {
            "events": [
                {"kind": "mcp", "tools": ["get_symbol"], "targets": ["a/b.py"], "batch": 1},
                {"kind": "read", "tools": [], "targets": ["a/b.py"], "batch": 0},
            ]
        }
    ]
    miss = [
        {
            "events": [
                {"kind": "mcp", "tools": ["get_symbol"], "targets": ["a/b.py"], "batch": 1},
                {"kind": "read", "tools": [], "targets": ["z/other.py"], "batch": 0},
            ]
        }
    ]
    check("displacement hit", read_displacement(hit)["get_symbol"]["followed_by_read"], 1)
    check("displacement miss", read_displacement(miss)["get_symbol"]["followed_by_read"], 0)

    if failures:
        for f in failures:
            print("FAIL", f)
        return 1
    print("self-test: all checks pass, in both directions")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default=os.path.expanduser("~/.codex/sessions"))
    ap.add_argument("--out")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    report = build_report(Path(args.sessions))
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        summary = {k: report[k] for k in ("corpus", "mix", "per_session_calls", "batching")}
        print(json.dumps(summary, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
