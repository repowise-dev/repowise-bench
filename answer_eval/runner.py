"""Running a question set against the real answer tool and scoring the result.

One run is: fetch a named snapshot, build an index from it, boot the server,
ask every question, and write one JSON blob. The blob is the unit of
comparison between runs, so it carries the corpus and the embedding parameters
next to every number - a recall figure with no record of what it was measured
against cannot be compared to anything.

**What is scored.** The paths the tool put in front of the caller: the
``citations`` list first, then any ``retrieval[].path`` not already among them.

Scoring ``retrieval`` alone is wrong, and wrong in the direction that matters.
At high confidence the tool empties the retrieval block on purpose - the
citations and the answer are held to suffice, so carrying five enriched hits
through the conversation cache buys nothing::

    if confidence == "high":
        retrieval_view: list[dict] = []

An eval keyed on ``retrieval[].path`` therefore scores zero on every confident
answer, however good, and the harder the tool tries the worse it looks.
``citations`` is present at every confidence level, so it leads; ``retrieval``
supplements it at medium and low where it is populated.

Both raw lists are kept per question, so the split between "cited it" and "had
it in the candidate set" stays visible instead of being averaged away.

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
from answer_eval.server_session import EmbedderConfig, SynthesisModel

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
    """What was scored: citations first, then retrieval paths not already in them."""
    cited_paths: list[str]
    retrieval_paths: list[str]
    """The raw ``retrieval[].path`` list. Empty on every high-confidence answer
    by design, which is why it is not what gets scored."""
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
    """Answers that put no path in front of the caller at all, by grounding."""
    n_citations_only: int
    """Answers carrying citations but an empty retrieval block. Expected to be
    every high-confidence answer; a jump at medium or low would mean the tool
    stopped returning candidates somewhere it used to."""
    recall_at_k_by_file: float
    """Recall with the ``::symbol`` suffix stripped from both sides.

    A file has a page and its symbols have their own pages, so retrieval can
    return the right file at the wrong granularity. That is a different failure
    from returning the wrong file, and only the gap between this and
    ``recall_at_k`` separates them. Reported, never substituted for the strict
    score - collapsing granularity is how a retrieval eval flatters itself."""
    index: IndexBuildReport
    embedder: EmbedderConfig
    synthesis: SynthesisModel
    """Which model wrote the answers. Confidence is as much a property of the
    model as of the corpus, so two runs are only comparable if this matches."""
    questions: list[QuestionResult]
    elapsed_seconds: float
    warnings: list[str] = field(default_factory=list)
    """Conditions that make part of this blob unreadable as a finding.

    An index with no symbols is the motivating case: it caps every answer at
    medium, so its confidence distribution says nothing about the tool. Data
    rather than a raised error, because recall and abstention from such a run
    are still perfectly good numbers."""

    def as_blob(self) -> dict[str, Any]:
        return {
            "snapshot_short_id": self.snapshot_short_id,
            "k": self.k,
            "recall_at_k": self.scores.recall_at_k,
            "recall_at_k_by_file": self.recall_at_k_by_file,
            "mrr": self.scores.mrr,
            "n_questions": self.scores.n_questions,
            "n_empty_results": self.scores.n_empty_results,
            "confidence_counts": self.confidence_counts,
            "abstention_rate": self.abstention_rate,
            "recall_at_k_when_high": self.recall_at_k_when_high,
            "non_retrieval_grounding_counts": self.non_retrieval_grounding_counts,
            "n_citations_only": self.n_citations_only,
            "embedder": asdict(self.embedder),
            "synthesis": asdict(self.synthesis),
            "index": asdict(self.index),
            "elapsed_seconds": self.elapsed_seconds,
            "warnings": self.warnings,
            "questions": [asdict(q) for q in self.questions],
        }


def cited_paths(payload: dict) -> list[str]:
    """The ``citations`` list, cleaned. Present at every confidence level."""
    cites = payload.get("citations") or []
    paths: list[str] = []
    for position, cite in enumerate(cites):
        if isinstance(cite, str) and cite.strip():
            paths.append(cite.strip())
        else:
            logger.warning("citation at position %d is not a path: %r", position, cite)
    return paths


def answered_paths(payload: dict) -> list[str]:
    """Every path the tool put in front of the caller, best first.

    Citations lead because they are what the answer actually stands on, and
    because they are the only list populated at high confidence.
    """
    seen: set[str] = set()
    result: list[str] = []
    for path in cited_paths(payload) + retrieved_paths(payload):
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def file_of(path: str) -> str:
    """Strip a ``::symbol`` suffix, leaving the file the page belongs to."""
    return path.split("::", 1)[0]


def _file_recall(result: "QuestionResult", k: int) -> float:
    """Recall for one question with symbol granularity collapsed away."""
    expected = {file_of(p) for p in result.expected_paths}
    window = {file_of(p) for p in result.retrieved_paths[:k]}
    return len(window & expected) / len(expected)


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

    cited = cited_paths(payload)
    retrieval = retrieved_paths(payload)
    paths = answered_paths(payload)
    expected = set(question.expected_paths)

    return QuestionResult(
        id=question.id,
        question=question.question,
        expected_paths=sorted(expected),
        retrieved_paths=paths,
        cited_paths=cited,
        retrieval_paths=retrieval,
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
    synthesis: SynthesisModel,
    k: int = DEFAULT_K,
) -> RunReport:
    """Ask every question in order and aggregate into one report."""
    if not questions:
        raise RunnerError("cannot run an empty question set")

    warnings: list[str] = []
    if not index.symbols:
        warnings.append(
            "index has no symbols: the citation-source gate demotes every "
            "high-confidence answer that cannot cite symbol bodies, so the "
            "confidence distribution in this blob is a property of the index, "
            "not of the tool. Recall and abstention are unaffected."
        )
        logger.warning(warnings[-1])

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
    cite_only = sum(1 for r in results if r.cited_paths and not r.retrieval_paths)

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
        n_citations_only=cite_only,
        recall_at_k_by_file=sum(_file_recall(r, k) for r in results) / len(results),
        index=index,
        embedder=embedder,
        synthesis=synthesis,
        questions=results,
        elapsed_seconds=time.perf_counter() - started,
        warnings=warnings,
    )


def write_blob(report: RunReport, path: str | Path) -> Path:
    """Write the result blob, creating the directory if it does not exist."""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_blob(), indent=2, sort_keys=True), encoding="utf-8")
    logger.info("wrote result blob to %s", path)
    return path
