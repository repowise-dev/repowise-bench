"""Retrieval scores split by question shape.

See :mod:`answer_eval.question_shape` for what the two shapes are and how the
split is drawn. This module does the arithmetic and nothing else.

The reason to keep them apart: an index change that adds a new field to match
against lifts the questions whose wording reaches that field and, at best,
leaves the rest untouched. Averaged together those two populations produce one
modest rise, indistinguishable from a modest rise everywhere — and a
regression in the untouched half can hide inside it entirely.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from answer_eval.question_shape import QuestionShape, classify_question_set
from answer_eval.question_shape import _COMMON_TERM_CEILING as DEFAULT_CEILING
from answer_eval.scoring import recall_at_k, reciprocal_rank


class _Scorable(Protocol):
    """The part of a ``QuestionResult`` this module reads."""

    id: str
    expected_paths: Sequence[str]
    retrieved_paths: Sequence[str]


@dataclass(frozen=True)
class ShapeSubsetScores:
    """Recall and MRR over one subset, with the count that qualifies them.

    ``recall_at_k`` and ``mrr`` are ``None`` on an empty subset rather than
    0.0: in a results table those two look identical and mean opposite things.
    """

    n_questions: int
    recall_at_k: float | None
    mrr: float | None

    def as_blob(self) -> dict[str, Any]:
        return {
            "n_questions": self.n_questions,
            "recall_at_k": self.recall_at_k,
            "mrr": self.mrr,
        }


def score_by_shape(
    results: Sequence[_Scorable],
    question_texts: Mapping[str, str],
    k: int,
    ceiling: float = DEFAULT_CEILING,
) -> dict[QuestionShape, ShapeSubsetScores]:
    """Score *results* separately for each question shape.

    *question_texts* maps question id to the question as asked; the shape
    cannot be worked out from a result alone. A result whose id is missing
    from it raises — dropping it would shrink a subset with nothing to show
    that it happened, which is the failure this whole split exists to prevent.

    Both shapes are always present in the returned mapping, so a caller
    rendering a table never has to decide what an absent row means.
    """
    missing = [r.id for r in results if r.id not in question_texts]
    if missing:
        raise KeyError(f"no question text for {', '.join(sorted(missing))}")

    shapes = classify_question_set(
        {r.id: (question_texts[r.id], list(r.expected_paths)) for r in results},
        ceiling=ceiling,
    )

    out: dict[QuestionShape, ShapeSubsetScores] = {}
    for shape in QuestionShape:
        subset = [r for r in results if shapes[r.id] is shape]
        if not subset:
            out[shape] = ShapeSubsetScores(n_questions=0, recall_at_k=None, mrr=None)
            continue
        recalls = [recall_at_k(r.retrieved_paths, set(r.expected_paths), k=k) for r in subset]
        ranks = [reciprocal_rank(r.retrieved_paths, set(r.expected_paths)) for r in subset]
        out[shape] = ShapeSubsetScores(
            n_questions=len(subset),
            recall_at_k=sum(recalls) / len(recalls),
            mrr=sum(ranks) / len(ranks),
        )
    return out
