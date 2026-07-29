"""Judging one answer against one gold question.

The judge is asked a set of small closed questions, not an open one. For each
claim the gold set says a correct answer must make, it answers covered or not
and quotes the span it read that from; for each claim the gold set says a
correct answer must not make, it answers claimed or not, likewise with a quote.
It never produces a score - the arithmetic happens here, in code, where it can
be read.

That split is deliberate. A model asked "grade this answer out of ten" grades
fluency, drifts between runs, and cannot be argued with afterwards. A model
asked "does this text claim X" is doing something it is reliable at, and the
quote it returns is what lets a human overturn the verdict without re-running
anything.

**Scoring.** A question's score is the fraction of required claims covered,
unless the answer makes a forbidden claim, in which case it is zero however
much else it covered. A confidently stated wrong mechanism is worse for a
caller than an incomplete answer - it is the failure this whole set exists to
catch - and letting a forbidden claim be diluted by four covered points would
score the worst answers as passable.

**Failure is loud.** A judge response that does not parse, or that answers a
different number of claims than it was asked about, is retried once and then
raises. It is never scored as zero coverage: an eval that reads an API blip as
"the tool got everything wrong" reports a regression that did not happen.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from answer_eval.gold_set import GoldQuestion

logger = logging.getLogger(__name__)

#: Low, not zero. Providers differ in whether 0.0 is accepted, and the task is
#: near-deterministic anyway; recorded in the blob either way.
JUDGE_TEMPERATURE = 0.0

JUDGE_MAX_TOKENS = 2048

SYSTEM_PROMPT = """\
You check whether an answer about a software codebase makes specific claims.

You are given a question, a reference answer known to be correct, the answer
under test, and two numbered lists of claims. For every claim in both lists,
decide whether the answer under test makes that claim.

Rules:
- Judge only the answer under test. The reference answer is context for what
  the claim means, never evidence that the claim was made.
- A claim counts as made if the answer states it in its own words. Wording
  need not match. A claim does not count if the answer merely mentions the
  same identifiers, or hedges so far that it asserts nothing.
- Quote the exact span of the answer under test you decided from. If a claim
  is not made, leave the quote empty.
- Never explain, never grade, never comment on the answer's quality.

Reply with JSON only, no prose and no code fence:

{"required": [{"n": 1, "made": true, "quote": "..."}, ...],
 "forbidden": [{"n": 1, "made": false, "quote": ""}, ...]}

Include exactly one entry per numbered claim in each list, in order."""


class JudgeError(RuntimeError):
    """A judge response that cannot be turned into a verdict honestly."""


@dataclass(frozen=True)
class ClaimVerdict:
    """One claim, and whether the answer under test made it."""

    claim: str
    made: bool
    quote: str
    """The span the judge decided from. Empty when the claim was not made.

    Kept so a verdict can be overturned by reading, rather than re-run."""


@dataclass(frozen=True)
class AnswerVerdict:
    """One answer, judged."""

    question_id: str
    required: list[ClaimVerdict]
    forbidden: list[ClaimVerdict]
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def contradicted(self) -> bool:
        return any(verdict.made for verdict in self.forbidden)

    @property
    def n_covered(self) -> int:
        return sum(1 for verdict in self.required if verdict.made)

    @property
    def score(self) -> float:
        """Fraction of required claims covered - zero if a forbidden one was made."""
        if self.contradicted:
            return 0.0
        return self.n_covered / len(self.required)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "score": self.score,
            "n_covered": self.n_covered,
            "n_required": len(self.required),
            "contradicted": self.contradicted,
            "required": [vars(v) for v in self.required],
            "forbidden": [vars(v) for v in self.forbidden],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class JudgeModel:
    """Which model judged a run.

    Recorded next to every correctness figure. Two runs judged by different
    models are not comparable, and a correctness number with no record of its
    judge is not a measurement.
    """

    provider: str
    model: str
    temperature: float = JUDGE_TEMPERATURE
    same_family_as_synthesis: bool = False
    """True when the judge and the model that wrote the answers come from the
    same provider.

    Not fatal, and not correctable with one API key on the box - but a judge
    grading its own family's output is a real confound, and a correctness
    figure that does not carry the caveat will be read without it."""


def _numbered(claims: tuple[str, ...]) -> str:
    return "\n".join(f"{n}. {claim}" for n, claim in enumerate(claims, start=1))


def build_prompt(question: GoldQuestion, answer: str) -> str:
    """The user half of the judge call. Pure, so it can be asserted on."""
    forbidden = (
        _numbered(question.must_not_claim)
        if question.must_not_claim
        else "(none - return an empty forbidden list)"
    )
    return (
        f"QUESTION\n{question.question}\n\n"
        f"REFERENCE ANSWER (correct, for context only)\n{question.gold_answer}\n\n"
        f"ANSWER UNDER TEST\n{answer.strip() or '(the tool returned no answer)'}\n\n"
        f"REQUIRED CLAIMS\n{_numbered(question.must_include)}\n\n"
        f"FORBIDDEN CLAIMS\n{forbidden}\n"
    )


def _strip_fence(text: str) -> str:
    """Providers add a ```json fence despite being told not to. Tolerate it."""
    fenced = re.match(r"\s*```(?:json)?\s*(.*?)\s*```\s*\Z", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def parse_verdict(question: GoldQuestion, raw: str) -> AnswerVerdict:
    """Turn a judge response into a verdict, or raise.

    Every rejection here is a case that would otherwise become a plausible
    number: a short list reads as uncovered claims, a long one as claims the
    gold set never made, and both look exactly like a worse answer.
    """
    try:
        payload = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        raise JudgeError(
            f"question {question.id}: judge response is not JSON ({exc.msg}): {raw[:400]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise JudgeError(f"question {question.id}: judge response is not an object: {raw[:200]!r}")

    def claims_of(key: str, expected: tuple[str, ...]) -> list[ClaimVerdict]:
        entries = payload.get(key)
        if not isinstance(entries, list):
            raise JudgeError(f"question {question.id}: judge response has no {key!r} list")
        if len(entries) != len(expected):
            raise JudgeError(
                f"question {question.id}: judge answered {len(entries)} {key} claims "
                f"but was asked about {len(expected)}; scoring it would invent a number"
            )
        verdicts: list[ClaimVerdict] = []
        for claim, entry in zip(expected, entries, strict=True):
            if not isinstance(entry, dict) or not isinstance(entry.get("made"), bool):
                raise JudgeError(
                    f"question {question.id}: {key} entry for {claim!r} has no boolean "
                    f"'made': {entry!r}"
                )
            quote = entry.get("quote")
            verdicts.append(
                ClaimVerdict(
                    claim=claim,
                    made=entry["made"],
                    quote=quote.strip() if isinstance(quote, str) else "",
                )
            )
        return verdicts

    return AnswerVerdict(
        question_id=question.id,
        required=claims_of("required", question.must_include),
        forbidden=claims_of("forbidden", question.must_not_claim),
    )


async def judge_answer(provider, question: GoldQuestion, answer: str) -> AnswerVerdict:
    """Ask the judge about one answer. Retries once, then raises.

    One retry because a malformed response is usually a formatting slip a
    second sample does not repeat. No second retry, because past that it is
    the prompt or the model, and quietly burning calls to hide that is how a
    broken judge ships a full set of numbers.
    """
    prompt = build_prompt(question, answer)
    last: Exception | None = None

    for attempt in (1, 2):
        response = await provider.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=JUDGE_MAX_TOKENS,
            temperature=JUDGE_TEMPERATURE,
        )
        try:
            verdict = parse_verdict(question, response.content or "")
        except JudgeError as exc:
            last = exc
            logger.warning("judge attempt %d for %s failed: %s", attempt, question.id, exc)
            continue
        return AnswerVerdict(
            question_id=verdict.question_id,
            required=verdict.required,
            forbidden=verdict.forbidden,
            input_tokens=getattr(response, "input_tokens", 0) or 0,
            output_tokens=getattr(response, "output_tokens", 0) or 0,
        )

    raise JudgeError(f"judge failed twice on {question.id}") from last


@dataclass(frozen=True)
class JudgedScores:
    """Correctness over a whole gold set."""

    correctness: float
    """Mean per-question score. The headline number."""
    n_questions: int
    n_fully_correct: int
    """Questions covering every required claim and making no forbidden one."""
    n_contradicted: int
    """Questions whose answer made a claim the gold set names as wrong.

    Reported separately and never folded into the mean. A confident wrong
    mechanism and a thin-but-honest answer both score badly; only this
    separates them, and they are not the same problem."""
    claim_coverage: float
    """Required claims covered over required claims asked, ignoring the
    forbidden-claim penalty. The mean without the zeroing, so the effect of
    the penalty on the headline is visible rather than baked in."""
    verdicts: list[AnswerVerdict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "correctness": self.correctness,
            "n_questions": self.n_questions,
            "n_fully_correct": self.n_fully_correct,
            "n_contradicted": self.n_contradicted,
            "claim_coverage": self.claim_coverage,
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def aggregate(verdicts: list[AnswerVerdict]) -> JudgedScores:
    """Roll verdicts into one correctness figure, plus what it hides."""
    if not verdicts:
        raise JudgeError("cannot aggregate an empty set of verdicts")

    covered = sum(v.n_covered for v in verdicts)
    asked = sum(len(v.required) for v in verdicts)
    return JudgedScores(
        correctness=sum(v.score for v in verdicts) / len(verdicts),
        n_questions=len(verdicts),
        n_fully_correct=sum(1 for v in verdicts if v.score == 1.0),
        n_contradicted=sum(1 for v in verdicts if v.contradicted),
        claim_coverage=covered / asked,
        verdicts=verdicts,
    )
