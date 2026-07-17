"""Abandonment survival analysis on synthetic streams. No agents, no network.

The four canonical shapes: error then continued use, error then permanent
abandonment, zero errors, and an error with no subsequent opportunities
(must be excluded from denominators, never divided by zero).
"""

from __future__ import annotations

import json

from analysis.abandonment_curves import analyze_run, survival_curve, turn_events


def _turn(*tools):
    """Assistant turn with tool_use blocks: tools = [(id, name), ...]."""
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": {}}
        for tid, name in tools]}})


def _results(*results):
    """User message with tool_result blocks: results = [(id, is_error), ...]."""
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": err}
        for tid, err in results]}})


def test_error_then_continued_use():
    lines = [
        _turn(("1", "mcp__serena__find_symbol")), _results(("1", True)),
        _turn(("2", "Read")), _results(("2", False)),
        _turn(("3", "mcp__serena__find_symbol")), _results(("3", False)),
    ]
    facts = analyze_run(turn_events(lines), "serena")
    assert facts["first_error_turn"] == 0
    assert facts["resumed_within"] == 2  # one Read turn, then serena again
    curve = survival_curve([facts])
    assert curve[1] == 0.0
    assert curve[2] == 1.0


def test_error_then_permanent_abandonment():
    lines = [
        _turn(("1", "mcp__serena__find_symbol")), _results(("1", True)),
        _turn(("2", "Read")), _results(("2", False)),
        _turn(("3", "Grep")), _results(("3", False)),
        _turn(("4", "Read")), _results(("4", False)),
    ]
    facts = analyze_run(turn_events(lines), "serena")
    assert facts["resumed_within"] is None
    assert facts["opportunities_after_error"] == 3
    curve = survival_curve([facts])
    assert curve[1] == 0.0 and curve[3] == 0.0


def test_zero_errors_never_enter_the_curve():
    lines = [
        _turn(("1", "mcp__repowise__get_answer")), _results(("1", False)),
        _turn(("2", "Read")), _results(("2", False)),
    ]
    facts = analyze_run(turn_events(lines), "repowise")
    assert facts["first_error_turn"] is None
    curve = survival_curve([facts])
    assert all(v is None for v in curve.values())  # no errored runs at all


def test_error_on_final_turn_has_no_opportunities():
    lines = [
        _turn(("1", "mcp__serena__find_symbol")), _results(("1", False)),
        _turn(("2", "mcp__serena__find_symbol")), _results(("2", True)),
    ]
    facts = analyze_run(turn_events(lines), "serena")
    assert facts["first_error_turn"] == 1
    assert facts["opportunities_after_error"] == 0
    assert facts["resumed_within"] is None
    # Sole errored run has nothing to survive: excluded, not divided by zero.
    curve = survival_curve([facts])
    assert all(v is None for v in curve.values())


def test_mixed_runs_denominator_excludes_no_opportunity_run():
    resumed = analyze_run(turn_events([
        _turn(("1", "mcp__s__a")), _results(("1", True)),
        _turn(("2", "mcp__s__a")), _results(("2", False)),
    ]), "s")
    no_opp = analyze_run(turn_events([
        _turn(("1", "mcp__s__a")), _results(("1", True)),
    ]), "s")
    curve = survival_curve([resumed, no_opp])
    assert curve[1] == 1.0  # only the eligible run counts


def test_cap_rejection_detected():
    lines = [
        _turn(("1", "mcp__deepwiki__read_wiki_contents")),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "1", "is_error": True,
             "content": "MCP tool response (184538 tokens) exceeds maximum "
                        "allowed tokens (25000)."}]}}),
    ]
    facts = analyze_run(turn_events(lines), "deepwiki")
    assert facts["cap_rejections"] == 1
    assert facts["errors"] == 1
