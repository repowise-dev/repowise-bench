"""Calibration analysis fixtures: hedge detection, confidence extraction,
empty-slice handling. No agents, no network."""

from __future__ import annotations

import json

from analysis.calibration import (
    analyze,
    answer_expresses_uncertainty,
    extract_tool_confidences,
)


def test_hedged_answers_detected():
    assert answer_expresses_uncertainty("I'm not sure, but it may be in app.py.")
    assert answer_expresses_uncertainty("The exact mechanism is unclear from the code.")
    assert answer_expresses_uncertainty("I cannot determine the rationale.")


def test_confident_answers_not_flagged():
    assert not answer_expresses_uncertainty(
        "register_blueprint() defers setup via a list of closures in "
        "Blueprint.deferred_functions and runs them on registration.")
    assert not answer_expresses_uncertainty("")


def test_confidence_extraction_from_tool_result():
    envelope = json.dumps({"result": {"answer": "...", "confidence": "high"},
                           "_meta": {"verified": True}})
    lines = [json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": envelope}]}})]
    assert extract_tool_confidences(lines) == ["high"]


def test_confidence_extraction_handles_multiple_and_none():
    def result_line(conf):
        return json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t",
             "content": json.dumps({"confidence": conf})}]}})
    lines = [result_line("high"), result_line("low")]
    assert extract_tool_confidences(lines) == ["high", "low"]
    plain = [json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "plain text"}]}})]
    assert extract_tool_confidences(plain) == []


def test_analyze_confidently_wrong_and_empty_slices(tmp_path):
    rows = [
        # wrong and confident
        {"condition": "C0_bare", "judge_scores": {"correctness": 2},
         "answer": "It is definitely handled in wsgi.py."},
        # wrong but hedged
        {"condition": "C0_bare", "judge_scores": {"correctness": 3},
         "answer": "I cannot determine this from the code."},
        # right
        {"condition": "C0_bare", "judge_scores": {"correctness": 9},
         "answer": "Handled in app.py."},
        # errored row: skipped entirely
        {"condition": "C0_bare", "error": "timeout"},
        # unparseable judge: skipped
        {"condition": "C0_bare", "judge_scores": {"error": "parse_failed"},
         "answer": "x"},
    ]
    f = tmp_path / "rows.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows))
    out = analyze([str(f)])
    stats = out["conditions"]["C0_bare"]
    assert stats["scored"] == 3
    assert stats["wrong"] == 2
    assert stats["confidently_wrong"] == 1
    assert stats["confidently_wrong_rate"] == round(1 / 3, 4)
    # No transcripts on disk -> no confidence slices, and that is reported
    # as absence, not an error.
    assert out["confidence_slices"] == {}
