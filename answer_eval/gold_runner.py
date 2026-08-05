"""Running the judged gold set and writing its blob.

The retrieval runner and this one deliberately stay apart. A retrieval run is
free and can be repeated at will; a judged run costs two model calls per
question and is the only thing that can say whether an answer was *right*.
Merging them would make the cheap measurement expensive.

What a judged run adds over the retrieval one:

- **correctness**, per claim, from :mod:`answer_eval.judge`
- **contradictions** - answers making a claim the gold set names as wrong,
  counted separately from the mean because a confident wrong mechanism and a
  thin honest answer are different problems
- **tool-side cost per question**, metered from the provider rather than
  inferred

That last figure is the tool's own synthesis spend and is *not* the
cache-neutral dollars-per-question a caller pays, which is an agent-side
quantity a different harness measures. It is named for what it is so the two
cannot be read as the same number.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from answer_eval.gold_set import GoldQuestion
from answer_eval.index import IndexBuildReport
from answer_eval.judge import AnswerVerdict, JudgeModel, JudgedScores, aggregate, judge_answer
from answer_eval.runner import RunnerError, answered_paths, cited_paths, retrieved_paths
from answer_eval.server_session import EmbedderConfig, SynthesisModel
from answer_eval.token_meter import TokenMeter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoldResult:
    """One gold question, answered and judged."""

    id: str
    question: str
    answer_text: str
    confidence: str
    answered: bool
    cited_paths: list[str]
    expected_paths: list[str]
    found_expected_path: bool
    """Whether any expected page reached the caller.

    Secondary here - a right answer citing an unexpected page is still right -
    but it is what separates "retrieval missed it" from "retrieval found it
    and synthesis got it wrong", which are opposite fixes."""
    verdict: dict[str, Any]
    elapsed_seconds: float
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GoldRunReport:
    """One judged run, whole."""

    snapshot_short_id: str
    scores: JudgedScores
    confidence_counts: dict[str, int]
    abstention_rate: float
    correctness_by_confidence: dict[str, float]
    """Mean correctness within each confidence bucket.

    The single most important row in the blob. The tool's confidence is a
    promise to the caller that verification is unnecessary; if `high` is not
    the most correct bucket, the promise is not being kept."""
    n_contradicted_at_high: int
    """Confident answers stating a mechanism the gold set names as wrong.

    The worst outcome the tool can produce, and it must never be averaged into
    a correctness figure that also contains honest low-confidence answers."""
    tool_side_usd_per_question: float | None
    tool_side_tokens: dict[str, Any]
    judge: JudgeModel
    judge_tokens: dict[str, int]
    index: IndexBuildReport
    embedder: EmbedderConfig
    synthesis: SynthesisModel
    questions: list[GoldResult]
    elapsed_seconds: float
    warnings: list[str] = field(default_factory=list)

    def as_blob(self) -> dict[str, Any]:
        return {
            "snapshot_short_id": self.snapshot_short_id,
            "correctness": self.scores.correctness,
            "claim_coverage": self.scores.claim_coverage,
            "n_questions": self.scores.n_questions,
            "n_fully_correct": self.scores.n_fully_correct,
            "n_contradicted": self.scores.n_contradicted,
            "n_contradicted_at_high": self.n_contradicted_at_high,
            "correctness_by_confidence": self.correctness_by_confidence,
            "confidence_counts": self.confidence_counts,
            "abstention_rate": self.abstention_rate,
            "tool_side_usd_per_question": self.tool_side_usd_per_question,
            "tool_side_tokens": self.tool_side_tokens,
            "judge": asdict(self.judge),
            "judge_tokens": self.judge_tokens,
            "embedder": asdict(self.embedder),
            "synthesis": asdict(self.synthesis),
            "index": asdict(self.index),
            "elapsed_seconds": self.elapsed_seconds,
            "warnings": self.warnings,
            "questions": [asdict(q) for q in self.questions],
        }


async def run_gold_set(
    answer_tool,
    judge_provider,
    questions: Sequence[GoldQuestion],
    *,
    snapshot_short_id: str,
    index: IndexBuildReport,
    embedder: EmbedderConfig,
    synthesis: SynthesisModel,
    judge: JudgeModel,
    meter: TokenMeter | None = None,
) -> GoldRunReport:
    """Ask every gold question, judge each answer, and aggregate."""
    if not questions:
        raise RunnerError("cannot run an empty gold set")

    warnings: list[str] = []
    if not index.symbols:
        warnings.append(
            "index has no symbols: every high-confidence answer is demoted by the "
            "citation-source gate, so the confidence breakdown in this blob is a "
            "property of the index rather than the tool"
        )
        logger.warning(warnings[-1])
    if judge.same_family_as_synthesis:
        warnings.append(
            f"the judge and the model that wrote the answers are both {judge.provider}: "
            "correctness here is partly a model grading its own family's output"
        )
        logger.warning(warnings[-1])

    started = time.perf_counter()
    results: list[GoldResult] = []
    verdicts: list[AnswerVerdict] = []

    for position, question in enumerate(questions, start=1):
        began = time.perf_counter()
        payload = await answer_tool(question.question)
        elapsed = time.perf_counter() - began

        if not isinstance(payload, dict):
            raise RunnerError(
                f"question {question.id}: answer tool returned "
                f"{type(payload).__name__}, not a dict"
            )

        answer_text = (payload.get("answer") or "").strip()
        paths = answered_paths(payload)
        verdict = await judge_answer(judge_provider, question, answer_text)
        verdicts.append(verdict)

        results.append(
            GoldResult(
                id=question.id,
                question=question.question,
                answer_text=answer_text,
                confidence=payload.get("confidence") or "missing",
                answered=bool(answer_text),
                cited_paths=cited_paths(payload),
                expected_paths=sorted(question.expected_paths),
                found_expected_path=bool(set(paths) & question.expected_paths),
                verdict=verdict.as_dict(),
                elapsed_seconds=elapsed,
                tags=sorted(question.tags),
            )
        )
        logger.info(
            "%d/%d %s confidence=%s score=%.2f%s",
            position,
            len(questions),
            question.id,
            results[-1].confidence,
            verdict.score,
            " CONTRADICTED" if verdict.contradicted else "",
        )
        if verdict.contradicted:
            logger.warning(
                "question %s answered at %s while claiming something the gold set "
                "names as wrong",
                question.id,
                results[-1].confidence,
            )

    scores = aggregate(verdicts)
    by_id = {v.question_id: v for v in verdicts}

    usd_per_question: float | None = None
    if meter is not None and meter.calls:
        usd_per_question = meter.usd(synthesis.model, synthesis.provider) / len(results)

    return GoldRunReport(
        snapshot_short_id=snapshot_short_id,
        scores=scores,
        confidence_counts=dict(Counter(r.confidence for r in results)),
        abstention_rate=sum(1 for r in results if not r.answered) / len(results),
        correctness_by_confidence=_by_confidence(results, by_id),
        n_contradicted_at_high=sum(
            1 for r in results if r.confidence == "high" and by_id[r.id].contradicted
        ),
        tool_side_usd_per_question=usd_per_question,
        tool_side_tokens=meter.as_dict() if meter is not None else {},
        judge=judge,
        judge_tokens={
            "input_tokens": sum(v.input_tokens for v in verdicts),
            "output_tokens": sum(v.output_tokens for v in verdicts),
        },
        index=index,
        embedder=embedder,
        synthesis=synthesis,
        questions=results,
        elapsed_seconds=time.perf_counter() - started,
        warnings=warnings,
    )


def _by_confidence(
    results: Sequence[GoldResult], by_id: dict[str, AnswerVerdict]
) -> dict[str, float]:
    """Mean correctness within each confidence bucket."""
    buckets: dict[str, list[float]] = {}
    for result in results:
        buckets.setdefault(result.confidence, []).append(by_id[result.id].score)
    return {name: sum(scores) / len(scores) for name, scores in sorted(buckets.items())}


def write_gold_blob(report: GoldRunReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_blob(), indent=2, sort_keys=True), encoding="utf-8")
    logger.info("wrote judged result blob to %s", path)
    return path


__all__ = [
    "GoldResult",
    "GoldRunReport",
    "run_gold_set",
    "write_gold_blob",
    "retrieved_paths",
]
