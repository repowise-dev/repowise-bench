"""Running a question set against the real answer tool and scoring the result.

One run is: fetch a named snapshot, build an index from it, boot the server,
ask every question, and write one JSON blob. The blob is the unit of
comparison between runs, so it carries the corpus and the embedding parameters
next to every number - a recall figure with no record of what it was measured
against cannot be compared to anything.

**What is scored, and what is only counted.** Recall@k and MRR are computed
over ``retrieval[].path``, which is what the tool actually returns. Some
answers arrive with an empty ``retrieval`` list and a non-retrieval grounding -
an exact symbol match, or a shape read straight off the data - and those score
zero on a retrieval metric while being perfectly good answers. They are counted
and reported separately rather than folded in, because a rise in that count
looks identical to a retrieval regression once it is averaged away.

Cost is deliberately absent. Retrieval scoring needs no judge, so a run is
near-free; the cost figure belongs with the judged gold set, where it is a real
quantity rather than a rounding error.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from answer_eval.index import IndexBuildReport
from answer_eval.question_set import RetrievalQuestion
from answer_eval.scoring import RetrievalScores, score_retrieval
from answer_eval.server_session import EmbedderConfig

logger = logging.getLogger(__name__)

DEFAULT_K = 5

#: Confidence values the tool is documented to return. Anything else is a
#: change in the tool that the eval must not average into a distribution as if
#: it were expected.
KNOWN_CONFIDENCES = ("high", "medium", "low")


class RunnerError(RuntimeError):
    """A run produced something that cannot be scored honestly."""


@dataclass(frozen=True)
class QuestionResult:
    """Everything one question produced, kept so a number can be traced back."""

    id: str
    question: str
    expected_paths: list[str]
    retrieved_paths: list[str]
    confidence: str
    grounding: str
    answered: bool
    """False when the tool returned no answer text - the abstention signal."""
    recall_at_k: float
    reciprocal_rank: float
    elapsed_seconds: float
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunReport:
    """One run, whole. Serialised to the result blob."""

    snapshot_short_id: str
    k: int
    scores: RetrievalScores
    confidence_counts: dict[str, int]
    abstention_rate: float
    recall_at_k_when_high: float | None
    """Recall among the questions answered at high confidence.

    Not the same thing as judged correctness. It says whether a confident
    answer at least had an expected page in front of it, which is the part
    that can be measured without a model.
    """
    non_retrieval_grounding_counts: dict[str, int]
    """Answers that came back with no retrieval hits, by grounding. These score
    zero on recall while often being correct - counted, never folded in."""
    index: IndexBuildReport
    embedder: EmbedderConfig
    questions: list[QuestionResult]
    elapsed_seconds: float

    def as_blob(self) -> dict[str, Any]:
        return {
            "snapshot_short_id": self.snapshot_short_id,
            "k": self.k,
            "recall_at_k": self.scores.recall_at_k,
            "mrr": self.scores.mrr,
            "n_questions": self.scores.n_questions,
            "n_empty_results": self.scores.n_empty_results,
            "confidence_counts": self.confidence_counts,
            "abstention_rate": self.abstention_rate,
            "recall_at_k_when_high": self.recall_at_k_when_high,
            "non_retrieval_grounding_counts": self.non_retrieval_grounding_counts,
            "embedder": asdict(self.embedder),
            "index": asdict(self.index),
            "elapsed_seconds": self.elapsed_seconds,
            "questions": [asdict(q) for q in self.questions],
        }


def retrieved_paths(payload: dict) -> list[str]:
    """Pull the ordered target paths out of an answer payload.

    ``retrieval[].path`` is ``target_path``, which the tool reads off a hit
    with ``.get`` and so can be absent. A hit with no path cannot be matched
    against an expectation; it is dropped from the ranking and logged, because
    silently keeping it as an empty string would occupy a slot in the top-k
    window and depress recall for a reason unrelated to retrieval.
    """
    hits = payload.get("retrieval") or []
    paths: list[str] = []
    for position, hit in enumerate(hits):
        path = (hit or {}).get("path")
        if isinstance(path, str) and path.strip():
            paths.append(path.strip())
        else:
            logger.warning("retrieval hit at position %d has no path: %r", position, hit)
    return paths


async def run_question(answer_tool, question: RetrievalQuestion, k: int) -> QuestionResult:
    """Ask one question and record what came back, scored but not judged."""
    from answer_eval.scoring import recall_at_k, reciprocal_rank

    started = time.perf_counter()
    payload = await answer_tool(question.question)
    elapsed = time.perf_counter() - started

    if not isinstance(payload, dict):
        raise RunnerError(
            f"question {question.id}: answer tool returned {type(payload).__name__}, not a dict"
        )
    if error := payload.get("error"):
        logger.warning("question %s: answer tool reported %r", question.id, error)

    confidence = payload.get("confidence") or "missing"
    if confidence not in KNOWN_CONFIDENCES:
        logger.warning(
            "question %s: unrecognised confidence %r - the tool's contract may have changed",
            question.id,
            confidence,
        )

    paths = retrieved_paths(payload)
    expected = set(question.expected_paths)

    return QuestionResult(
        id=question.id,
        question=question.question,
        expected_paths=sorted(expected),
        retrieved_paths=paths,
        confidence=confidence,
        grounding=payload.get("grounding") or "retrieval",
        answered=bool((payload.get("answer") or "").strip()),
        recall_at_k=recall_at_k(paths, expected, k=k),
        reciprocal_rank=reciprocal_rank(paths, expected),
        elapsed_seconds=elapsed,
        tags=sorted(question.tags),
    )


async def run_question_set(
    answer_tool,
    questions: Sequence[RetrievalQuestion],
    *,
    snapshot_short_id: str,
    index: IndexBuildReport,
    embedder: EmbedderConfig,
    k: int = DEFAULT_K,
) -> RunReport:
    """Ask every question in order and aggregate into one report."""
    if not questions:
        raise RunnerError("cannot run an empty question set")

    started = time.perf_counter()
    results: list[QuestionResult] = []
    for position, question in enumerate(questions, start=1):
        result = await run_question(answer_tool, question, k=k)
        logger.info(
            "%d/%d %s confidence=%s recall@%d=%.2f",
            position,
            len(questions),
            question.id,
            result.confidence,
            k,
            result.recall_at_k,
        )
        results.append(result)

    scores = score_retrieval(
        {r.id: (r.retrieved_paths, set(r.expected_paths)) for r in results}, k=k
    )

    high = [r for r in results if r.confidence == "high"]
    no_hits = [r for r in results if not r.retrieved_paths]

    return RunReport(
        snapshot_short_id=snapshot_short_id,
        k=k,
        scores=scores,
        confidence_counts=dict(Counter(r.confidence for r in results)),
        abstention_rate=sum(1 for r in results if not r.answered) / len(results),
        recall_at_k_when_high=(
            sum(r.recall_at_k for r in high) / len(high) if high else None
        ),
        non_retrieval_grounding_counts=dict(Counter(r.grounding for r in no_hits)),
        index=index,
        embedder=embedder,
        questions=results,
        elapsed_seconds=time.perf_counter() - started,
    )


def write_blob(report: RunReport, path: str | Path) -> Path:
    """Write the result blob, creating the directory if it does not exist."""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_blob(), indent=2, sort_keys=True), encoding="utf-8")
    logger.info("wrote result blob to %s", path)
    return path
