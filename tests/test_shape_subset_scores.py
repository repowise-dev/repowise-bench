"""Recall reported per question shape, never folded into one number.

An index change that widens what can be matched lifts the questions whose
wording reaches the answer's path and, at best, leaves the rest alone. One
combined recall figure averages those two populations together and reports a
small rise, which reads the same as a small rise everywhere. Reported apart,
the same run says which half moved — and whether the other half paid for it.

Each subset carries its own ``n``. A recall figure over a handful of questions
cannot resolve a small change and must never be quoted without it.
"""

from __future__ import annotations

import pytest

from answer_eval.question_shape import QuestionShape
from answer_eval.shape_scores import ShapeSubsetScores, score_by_shape


class _Result:
    """The three fields :func:`score_by_shape` reads off a QuestionResult."""

    def __init__(self, rid: str, expected: list[str], retrieved: list[str]) -> None:
        self.id = rid
        self.expected_paths = expected
        self.retrieved_paths = retrieved


def test_splits_the_run_into_two_scored_subsets() -> None:
    results = [
        # Shares "collector" with its path — path-shaped.
        _Result("q1", ["a/telemetry/collector.py"], ["a/telemetry/collector.py"]),
        # Shares nothing — concept-shaped, and missed.
        _Result("q2", ["a/telemetry/emit.py"], ["a/other/thing.py"]),
    ]
    texts = {"q1": "Where is the collector?", "q2": "Why does it stop?"}

    by_shape = score_by_shape(results, texts, k=5, ceiling=1.0)

    assert by_shape[QuestionShape.PATH] == ShapeSubsetScores(
        n_questions=2 - 1, recall_at_k=1.0, mrr=1.0
    )
    assert by_shape[QuestionShape.CONCEPT].n_questions == 1
    assert by_shape[QuestionShape.CONCEPT].recall_at_k == 0.0


def test_an_empty_subset_reports_zero_questions_and_no_score() -> None:
    """A subset with nothing in it must not report 0.0 recall.

    Zero recall and no data look identical in a table and mean opposite
    things, so the score is ``None`` and the count says why.
    """
    results = [_Result("q1", ["a/telemetry/collector.py"], ["a/telemetry/collector.py"])]
    texts = {"q1": "Where is the collector?"}

    by_shape = score_by_shape(results, texts, k=5, ceiling=1.0)

    assert by_shape[QuestionShape.CONCEPT].n_questions == 0
    assert by_shape[QuestionShape.CONCEPT].recall_at_k is None
    assert by_shape[QuestionShape.CONCEPT].mrr is None


def test_both_subsets_are_always_present() -> None:
    by_shape = score_by_shape([], {}, k=5)
    assert set(by_shape) == {QuestionShape.PATH, QuestionShape.CONCEPT}


def test_a_result_with_no_question_text_is_an_error() -> None:
    """Silently dropping it would shrink a subset with no sign anything went wrong."""
    results = [_Result("q1", ["a/b.py"], [])]
    with pytest.raises(KeyError, match="q1"):
        score_by_shape(results, {}, k=5)


def test_subset_counts_add_up_to_the_run() -> None:
    results = [
        _Result("q1", ["a/telemetry/collector.py"], []),
        _Result("q2", ["a/telemetry/emit.py"], []),
        _Result("q3", ["a/telemetry/flush.py"], []),
    ]
    texts = {"q1": "Where is the collector?", "q2": "Why stop?", "q3": "What emit does?"}

    by_shape = score_by_shape(results, texts, k=5, ceiling=1.0)

    assert sum(s.n_questions for s in by_shape.values()) == 3
