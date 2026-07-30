"""Split a question set by whether its wording reaches the answer's file path.

Two kinds of question reach retrieval and they fail differently.

A **path-shaped** question uses a word that also appears in the path of the
page that answers it — "How are dbt references resolved?" against
``.../resolvers/dbt.py``. The asker's vocabulary and the file's name meet, so a
lexical index can find the page from the path alone.

A **concept-shaped** question describes a behaviour and shares nothing with the
path — "Why does the walk stop descending?" against ``fs_walk.py`` shares only
``walk``, or against ``scanner.py`` nothing at all. Retrieval has to work from
the prose on the page.

Widening what the index covers moves these two subsets by different amounts,
and one combined recall number hides that. Reporting them apart is the only way
to see whether an index change helped the questions it was meant to help — and,
more importantly, whether it cost anything on the questions it cannot help.

**How the split is drawn.** A question is path-shaped when it shares at least
one *discriminating* term with one of its expected paths. Terms are the same
tokens the product's own full-text query builder keeps: alphanumeric runs,
lowercased, minus English stop words and single characters. Discriminating
means the term is not carried by more than :data:`_COMMON_TERM_CEILING` of the
set's expected paths — ``repowise``, ``packages``, ``src``, ``py`` sit in
almost every path in a Python monorepo, so an overlap on one of them says
nothing about the question. That mirrors the document-frequency ceiling the
query builder applies for the same reason.

**This is a heuristic and the question set does not label it.** It is drawn
from the data rather than from a guess about English, so it moves if the
question set does; the counts belong beside any number reported per subset.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum

# Terms carried by more than this share of the set's expected paths are
# ignored when looking for an overlap. Matching the query builder's own
# ceiling: a term most of the corpus carries cannot discriminate between pages,
# so a question sharing only that term has not really named the path.
_COMMON_TERM_CEILING = 0.20

# The tokens the full-text query builder keeps, minus its stop words. Kept as a
# literal copy rather than imported from repowise: the eval measures whichever
# build is installed, and a split that changed shape when the product's stop
# list changed would silently redraw the subsets between two runs.
_TOKEN = re.compile(r"[a-zA-Z0-9_]+")

_STOP_WORDS = frozenset(
    """a an the is are was were be been being have has had do does did will would
    shall should may might must can could am to of in for on with at by from as
    into about it its this that these those i we you he she they me him her us
    them my your his our their what which who whom how when where why not no so
    if or and but all each very just also than too only""".split()
)


class QuestionShape(str, Enum):
    """Which retrieval problem a question poses."""

    PATH = "path"
    """Shares a discriminating word with the path of the page that answers it."""

    CONCEPT = "concept"
    """Shares nothing with the path; only the page's prose can match."""


def terms(text: str) -> set[str]:
    """Searchable terms of *text* — lowercased, stop words and singles dropped."""
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP_WORDS and len(t) > 1}


def _path_terms(expected_paths: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for path in expected_paths:
        found |= terms(path)
    return found


def common_path_terms(
    expected_paths_per_question: Sequence[Iterable[str]],
    ceiling: float = _COMMON_TERM_CEILING,
) -> set[str]:
    """Terms too widespread across the set's expected paths to mean anything.

    Computed over the question set rather than the corpus because the set is
    what a reader of these numbers has in front of them, and because the two
    agree on the terms that matter here — a directory in most expected paths is
    a directory in most of the corpus.
    """
    if not expected_paths_per_question:
        return set()
    counts: Counter[str] = Counter()
    for paths in expected_paths_per_question:
        counts.update(_path_terms(paths))
    limit = ceiling * len(expected_paths_per_question)
    return {term for term, count in counts.items() if count > limit}


def overlapping_terms(
    question: str, expected_paths: Iterable[str], common: set[str]
) -> set[str]:
    """Discriminating terms the question and one of its paths both carry.

    Returned rather than kept private so a classification can be argued with: a
    reviewer who disagrees with a question's bucket can see the exact word that
    put it there.
    """
    return (terms(question) & _path_terms(expected_paths)) - common


def classify_question_set(
    questions: Mapping[str, tuple[str, Sequence[str]]],
    ceiling: float = _COMMON_TERM_CEILING,
) -> dict[str, QuestionShape]:
    """Bucket every question. Input maps id to ``(question text, expected paths)``.

    A set-level call rather than a per-question one because the common-term
    ceiling is a property of the whole set: classifying one question in
    isolation would have to guess which terms are widespread. For the same
    reason the ceiling is meaningless on a handful of questions — every term
    one path carries is then "widespread" — so *ceiling* is adjustable for
    tests that want the overlap rule on its own.
    """
    common = common_path_terms([paths for _text, paths in questions.values()], ceiling=ceiling)
    return {
        question_id: (
            QuestionShape.PATH
            if overlapping_terms(text, paths, common)
            else QuestionShape.CONCEPT
        )
        for question_id, (text, paths) in questions.items()
    }


def shape_counts(shapes: Iterable[QuestionShape]) -> dict[QuestionShape, int]:
    """How many questions fall in each bucket. Both keys always present."""
    counts = Counter(shapes)
    return {shape: counts.get(shape, 0) for shape in QuestionShape}
