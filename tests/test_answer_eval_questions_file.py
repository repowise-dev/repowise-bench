"""The shipped retrieval question set itself.

A question set is data, and data rots quietly. These check the properties that
would otherwise be found only by reading a suspicious number in a report: an
expectation written in the wrong id space, an expectation nothing could ever
match, or a question that drifted into duplicating another.

What is deliberately not checked here is whether each expected path exists in
the corpus - that needs the 31 MB snapshot and a network. The runner's own
output answers it: a question whose expected path is not a real page scores
zero on every run and never recovers, which shows up as a permanent floor
rather than noise.
"""

import re

import pytest

from answer_eval.question_set import load_retrieval_questions

QUESTIONS_PATH = "answer_eval/questions/retrieval.jsonl"

#: `retrieval[].path` is a repo-relative file path, optionally with a `::symbol`
#: suffix. Anything with a page-type prefix (`file_page:`, `symbol_spotlight:`)
#: is written in the page-id space instead, which the tool never returns.
PAGE_ID_PREFIX = re.compile(r"^[a-z_]+:(?!:)")


@pytest.fixture(scope="module")
def questions():
    return load_retrieval_questions(QUESTIONS_PATH)


def test_the_set_loads(questions):
    assert len(questions) == 99


def test_every_id_is_unique(questions):
    assert len({q.id for q in questions}) == len(questions)


def test_no_two_questions_are_the_same_text(questions):
    """Two identical questions would weight one page twice for no extra signal."""
    texts = [q.question.lower().strip() for q in questions]
    assert len(set(texts)) == len(texts)


def test_every_expectation_is_a_path_not_a_page_id(questions):
    """The whole point of the field: expectations live in the space the tool returns.

    A `file_page:` or `symbol_spotlight:` prefix here would match nothing, and
    the resulting zeros would read as a retrieval failure rather than a set
    written against the wrong ids.
    """
    offenders = [
        (q.id, path)
        for q in questions
        for path in q.expected_paths
        if PAGE_ID_PREFIX.match(path)
    ]
    assert offenders == []


def test_every_expectation_looks_like_a_repo_path(questions):
    """A bare word is almost always a typo'd path, and scores zero forever."""
    offenders = [
        (q.id, path)
        for q in questions
        for path in q.expected_paths
        if "/" not in path
    ]
    assert offenders == []


def test_every_question_reads_as_a_question(questions):
    assert [q.id for q in questions if not q.question.endswith("?")] == []


def test_the_set_spans_more_than_one_question_shape(questions):
    """A set that is all "where is X" measures one retrieval path, not the tool.

    The catastrophic-quality tail sits on "where" questions, and mechanism
    questions are where synthesis is exercised, so both have to be present.
    """
    shapes = {tag for q in questions for tag in q.tags} & {"how", "why", "where", "what"}
    assert shapes == {"how", "why", "where", "what"}


#: Questions reworded after reading the tool's answer to the original by hand,
#: with the reason. Both originals scored zero while the tool was arguably or
#: plainly right, which makes the set - not the tool - the thing that was
#: wrong. Pinned here so a later edit cannot quietly restore either one; the
#: reason is carried next to the text so the pin can be argued with.
HAND_AUDITED = {
    "q002": (
        "Where is the walk option that skips any non-root directory containing "
        "its own .git entry?",
        "The original asked where 'the repo scan' stops descending into nested "
        "git repositories. Two different scans do that - the file walk, and the "
        "workspace scanner that discovers repos - so the question had two "
        "correct answers and one expected path. It now names the walk option, "
        "which only one of them has.",
    ),
    "q015": (
        "Where does get_answer apply its domain penalty and intersection boost "
        "to the candidate hits?",
        "The original asked where get_answer runs its retrieval, and expected "
        "the re-ranking module - whose own docstring says it operates on hits "
        "'after the hybrid-retrieval stages in _answer_pipeline'. The tool "
        "answered with the pipeline and was marked wrong for being right. It "
        "now asks for what the expected module actually does.",
    ),
}


@pytest.mark.parametrize("qid", sorted(HAND_AUDITED))
def test_a_hand_audited_question_keeps_its_corrected_wording(questions, qid):
    expected_text, _reason = HAND_AUDITED[qid]
    by_id = {q.id: q for q in questions}
    assert by_id[qid].question == expected_text


def test_no_single_page_dominates_the_set(questions):
    """One page answering many questions would make the score mostly about it."""
    from collections import Counter

    counts = Counter(path for q in questions for path in q.expected_paths)
    assert counts.most_common(1)[0][1] <= 3
