"""Tool-abandonment survival analysis over benchmark transcripts.

One or two error responses early in a session can end an agent's use of an
MCP server for the whole run; error handling quality is therefore measurable
and (as far as published comparisons go) unreported. This walks raw
stream-JSONL transcripts and, per (condition, server), builds a survival
curve: after the server's first error, what fraction of runs call that
server again within the next 1, 2, 3, ... opportunities, where an
opportunity is an assistant turn (after the error) in which ANY tool was
called. Runs whose errors leave no subsequent opportunities are excluded
from the denominators rather than divided by zero.

Also reported per server: protocol error rate, first-error turn index, and
host output-cap rejections (a response over the 25k-token MCP cap reads as
isError to the agent and is its own abandonment trigger).

Input: results dirs written by run_experiment / prloc_bench (raw_outputs/
{task_id}_{condition}.json carrying _raw_stream_lines).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.metrics import _mcp_server_prefix  # noqa: E402

MAX_HORIZON = 6

# Substrings Claude Code emits when it rejects an oversized MCP result.
_CAP_MARKERS = ("exceeds maximum allowed tokens", "MCP tool response too large",
                "exceeds the maximum")


def turn_events(stream_lines: list) -> list:
    """Collapse a stream into assistant turns of tool events.

    Returns a list of turns; each turn is a list of
    {"tool", "server", "is_error", "cap_rejected"} dicts. Tool results are
    matched to calls by tool_use_id.
    """
    turns = []
    pending = {}  # tool_use_id -> event dict
    for line in stream_lines:
        try:
            d = json.loads(line) if isinstance(line, str) else line
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") == "assistant":
            events = []
            for block in d.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    ev = {"tool": block.get("name", ""),
                          "server": _mcp_server_prefix(block.get("name", "")),
                          "is_error": False, "cap_rejected": False}
                    pending[block.get("id", "")] = ev
                    events.append(ev)
            if events:
                turns.append(events)
        elif d.get("type") == "user":
            for block in d.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    ev = pending.pop(block.get("tool_use_id", ""), None)
                    if ev is None:
                        continue
                    if block.get("is_error", False):
                        ev["is_error"] = True
                        content = json.dumps(block.get("content", ""), default=str)
                        ev["cap_rejected"] = any(m in content for m in _CAP_MARKERS)
    return turns


def analyze_run(turns: list, server: str) -> dict:
    """Per-run abandonment facts for one server."""
    server_calls = sum(1 for turn in turns for ev in turn if ev["server"] == server)
    errors = sum(1 for turn in turns for ev in turn
                 if ev["server"] == server and ev["is_error"])
    cap_rejections = sum(1 for turn in turns for ev in turn
                         if ev["server"] == server and ev["cap_rejected"])
    first_error_turn = None
    for i, turn in enumerate(turns):
        if any(ev["server"] == server and ev["is_error"] for ev in turn):
            first_error_turn = i
            break

    resumed_within = None  # opportunities until the server was used again
    opportunities = 0
    if first_error_turn is not None:
        for turn in turns[first_error_turn + 1:]:
            if not turn:
                continue
            opportunities += 1
            if any(ev["server"] == server for ev in turn):
                resumed_within = opportunities
                break

    return {"server_calls": server_calls, "errors": errors,
            "cap_rejections": cap_rejections,
            "first_error_turn": first_error_turn,
            "opportunities_after_error": opportunities,
            "resumed_within": resumed_within}


def survival_curve(run_facts: list, max_horizon: int = MAX_HORIZON) -> dict:
    """Fraction of errored runs still using the server within h opportunities.

    Denominator at horizon h counts only runs with at least h opportunities
    (or that resumed earlier) — a run whose error fell on the final turn has
    nothing to survive and is excluded, never divided by zero.
    """
    errored = [r for r in run_facts if r["first_error_turn"] is not None]
    curve = {}
    for h in range(1, max_horizon + 1):
        eligible = [r for r in errored
                    if r["opportunities_after_error"] >= h
                    or (r["resumed_within"] is not None and r["resumed_within"] <= h)]
        if not eligible:
            curve[h] = None
            continue
        resumed = sum(1 for r in eligible
                      if r["resumed_within"] is not None and r["resumed_within"] <= h)
        curve[h] = round(resumed / len(eligible), 4)
    return curve


def analyze_results_dirs(dirs: list, max_horizon: int = MAX_HORIZON) -> dict:
    facts = defaultdict(list)  # (condition, server) -> [run facts]
    for results_dir in dirs:
        for raw_file in sorted(Path(results_dir).glob("**/raw_outputs/*.json")):
            condition = raw_file.stem.split("_")[-1]
            try:
                raw = json.loads(raw_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            turns = turn_events(raw.get("_raw_stream_lines", []))
            servers = {ev["server"] for turn in turns for ev in turn if ev["server"]}
            for server in servers:
                facts[(condition, server)].append(analyze_run(turns, server))

    out = {}
    for (condition, server), runs in sorted(facts.items()):
        errored = [r for r in runs if r["first_error_turn"] is not None]
        total_calls = sum(r["server_calls"] for r in runs)
        total_errors = sum(r["errors"] for r in runs)
        out[f"{condition}/{server}"] = {
            "runs": len(runs),
            "runs_with_error": len(errored),
            "error_rate_per_call": round(total_errors / total_calls, 4) if total_calls else 0.0,
            "cap_rejections": sum(r["cap_rejections"] for r in runs),
            "median_first_error_turn": (sorted(r["first_error_turn"] for r in errored)
                                        [len(errored) // 2] if errored else None),
            "survival": survival_curve(runs, max_horizon),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_dirs", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    summary = analyze_results_dirs(args.results_dirs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for key, s in summary.items():
        print(f"{key:28} runs={s['runs']} errored={s['runs_with_error']} "
              f"err/call={s['error_rate_per_call']} survival={s['survival']}")


if __name__ == "__main__":
    main()
