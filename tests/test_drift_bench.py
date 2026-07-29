"""Drift classifier on synthetic evidence. No agents, no judges, no network.

The classifier decides what the flagship staleness chart reports, so each
mapping is pinned: an old-truth echo with no hedge must land in
STALE-CONFIDENT, any surfaced staleness must land in FLAGGED, and judge ties
on drifted questions must land in WRONG-OTHER — never silently in CORRECT.
"""

from __future__ import annotations

import json

from harness.drift_bench import (
    answer_hedges,
    classify,
    transcript_flags_staleness,
)


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

def test_old_truth_no_hedge_is_stale_confident():
    assert classify(3, 9, flagged=False, hedged=False,
                    drift_kind="renamed") == "STALE-CONFIDENT"


def test_old_truth_with_tool_flag_is_flagged():
    assert classify(3, 9, flagged=True, hedged=False,
                    drift_kind="renamed") == "FLAGGED"


def test_old_truth_with_answer_hedge_is_flagged():
    assert classify(3, 9, flagged=False, hedged=True,
                    drift_kind="moved") == "FLAGGED"


def test_new_truth_is_correct():
    assert classify(9, 2, flagged=False, hedged=False,
                    drift_kind="signature-changed") == "CORRECT"


def test_judge_tie_on_drifted_question_never_correct():
    assert classify(8, 8, flagged=False, hedged=False,
                    drift_kind="behavior-changed") == "WRONG-OTHER"
    # Even a surfaced tie is not credited as correct.
    assert classify(8, 8, flagged=True, hedged=False,
                    drift_kind="behavior-changed") == "FLAGGED"


def test_matches_neither_gold():
    assert classify(2, 3, flagged=False, hedged=False,
                    drift_kind="deleted") == "WRONG-OTHER"
    assert classify(2, 3, flagged=True, hedged=False,
                    drift_kind="deleted") == "FLAGGED"


def test_control_question_scores_against_post_only():
    # drift_kind "none": both golds coincide, a tie is simply correct.
    assert classify(9, 9, flagged=False, hedged=False, drift_kind="none") == "CORRECT"
    assert classify(3, 3, flagged=False, hedged=False, drift_kind="none") == "WRONG-OTHER"


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def _stream_with_tool_result(content: str) -> list:
    return [json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": content}]}})]


def test_stale_warning_in_tool_result_flags():
    lines = _stream_with_tool_result(
        '{"result": {...}, "_meta": {"stale_warning": "file changed after indexing"}}')
    assert transcript_flags_staleness(lines)


def test_tombstone_in_tool_result_flags():
    lines = _stream_with_tool_result('{"error": "tombstone", "successor_paths": ["b.py"]}')
    assert transcript_flags_staleness(lines)


def test_clean_tool_results_do_not_flag():
    lines = _stream_with_tool_result('{"result": {"answer": "all good"}}')
    assert not transcript_flags_staleness(lines)
    # Markers in ASSISTANT text (not tool results) must not flag: only the
    # tool surfacing staleness counts as the tool's behavior.
    assistant = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "maybe there is a stale_warning somewhere"}]}})]
    assert not transcript_flags_staleness(assistant)


def test_hedge_lexicon():
    assert answer_hedges("The Blueprint class may have changed since indexing.")
    assert answer_hedges("I cannot determine the current signature.")
    assert not answer_hedges("register_blueprint attaches the blueprint to the app.")
    assert not answer_hedges("")
