"""The shipped gold set itself.

The retrieval set's file test guards expectations written in the wrong id
space. This one guards the properties that decide whether a correctness
figure means anything: that the questions test mechanisms rather than
vocabulary, that the required points are specific enough for a judge to
disagree with, and that the set is not secretly about three pages.
"""

import pytest

from answer_eval.gold_set import load_gold_questions

GOLD_PATH = "answer_eval/questions/gold.jsonl"

#: Points shorter than this are things like "it is fast" - a judge will mark
#: them covered against almost any fluent answer, which quietly turns
#: correctness into a fluency score.
MIN_POINT_CHARS = 25


@pytest.fixture(scope="module")
def questions():
    return load_gold_questions(GOLD_PATH)


def test_the_set_loads(questions):
    assert len(questions) == 22


def test_no_two_questions_are_the_same_text(questions):
    texts = [q.question.lower().strip() for q in questions]
    assert len(set(texts)) == len(texts)


def test_every_question_reads_as_a_question(questions):
    assert [q.id for q in questions if not q.question.endswith("?")] == []


def test_every_question_has_more_than_one_thing_to_get_right(questions):
    """A single-point question is pass/fail, which is the holistic grade again.

    The whole reason for point-by-point judging is that an answer getting
    three mechanisms of four is a different thing from one getting none.
    """
    assert [q.id for q in questions if len(q.must_include) < 3] == []


def test_no_required_point_is_too_vague_to_disagree_with(questions):
    offenders = [
        (q.id, point)
        for q in questions
        for point in q.must_include
        if len(point) < MIN_POINT_CHARS
    ]
    assert offenders == []


def test_most_questions_name_a_plausible_wrong_answer(questions):
    """`must_not_claim` is what catches the confident, fluent, adjacent answer.

    Not every question has a tempting wrong answer, so this is a floor rather
    than a requirement on each one.
    """
    with_guard = [q for q in questions if q.must_not_claim]
    assert len(with_guard) >= len(questions) // 2


def test_the_set_spans_more_than_one_question_shape(questions):
    shapes = {tag for q in questions for tag in q.tags} & {"how", "why", "what", "where"}
    assert shapes == {"how", "why", "what", "where"}


def test_no_single_page_dominates_the_set(questions):
    """A set concentrated on a few pages measures those pages, not the tool."""
    from collections import Counter

    counts = Counter(path for q in questions for path in q.expected_paths)
    assert counts.most_common(1)[0][1] <= 2


def test_every_question_says_where_its_answer_lives(questions):
    """Optional in the schema, required here.

    A correctness drop and a retrieval drop are different findings, and
    telling them apart afterwards needs the expected page recorded now.
    """
    assert [q.id for q in questions if not q.expected_paths] == []
