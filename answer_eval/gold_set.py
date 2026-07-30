"""Loading the judged gold set.

The retrieval set asks whether the right page was put in front of the caller.
This one asks whether the answer was *right*, which no path comparison can
decide: an answer can cite the correct file and still describe the wrong
mechanism, and that is the failure the confident-answer tail is made of.

Correctness is not judged as one holistic verdict. Each question carries the
claims a correct answer has to make, and each is judged on its own::

    {"id": "g001",
     "question": "What happens when a tool result exceeds the host cap?",
     "gold_answer": "The host rejects it outright ...",
     "must_include": ["the result is rejected with an isError",
                      "it is not silently truncated"],
     "must_not_claim": ["oversized results are written to a file"],
     "expected_paths": ["packages/server/.../budgeter.py"],
     "tags": ["how"]}

Point-by-point beats a single score for two reasons. A judge asked for one
number rewards fluency, and an answer that gets three of four mechanisms right
is a different thing from one that gets none - a distinction a holistic grade
throws away exactly when the tool is improving.

``must_not_claim`` is the other half. Several of the worst observed answers
were confident, well-written and about the adjacent page; listing the
plausible wrong claim is the only way an eval can mark that as wrong rather
than as merely incomplete. A question whose answer makes one of them is scored
zero however many required points it also covered - a wrong mechanism stated
confidently is worse than an incomplete answer, and averaging the two hides
it.

Every question is written from the content of the page it expects, so a
reviewer can check any expectation by reading that one page.

As with the retrieval set: JSONL so a diff shows which question moved, and
every validation raises rather than warning. A gold set that silently loses a
required point still produces a correctness figure, and that figure looks
exactly like a real one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_KEYS = frozenset({"id", "question", "gold_answer", "must_include"})
OPTIONAL_KEYS = frozenset({"must_not_claim", "expected_paths", "tags"})


class GoldSetError(ValueError):
    """A gold-set file is missing, empty, or malformed."""


@dataclass(frozen=True)
class GoldQuestion:
    """One question, the answer it should get, and how that is checked."""

    id: str
    question: str
    gold_answer: str
    """Prose a correct answer would amount to. Shown to the judge as the
    reference, and to a human as the thing to disagree with."""
    must_include: tuple[str, ...]
    """Claims a correct answer has to make, judged one at a time."""
    must_not_claim: tuple[str, ...] = ()
    """Plausible wrong claims. Any one of them scores the question zero."""
    expected_paths: frozenset[str] = field(default_factory=frozenset)
    """Where the answer lives. Optional here - this set grades the answer, and
    a right answer citing an unexpected page is still right - but recorded so
    a correctness drop can be read against a retrieval drop."""
    tags: frozenset[str] = field(default_factory=frozenset)


def _fail(path: Path, line_number: int, problem: str) -> GoldSetError:
    return GoldSetError(f"{path}, line {line_number}: {problem}")


def _read_string_list(
    path: Path, line_number: int, raw: object, key: str, *, allow_empty: bool
) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _fail(path, line_number, f"{key} must be a list of strings")
    if not raw and not allow_empty:
        raise _fail(path, line_number, f"{key} must not be empty")
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise _fail(path, line_number, f"{key} must contain only non-empty strings")
    return tuple(entry.strip() for entry in raw)


def _parse_line(path: Path, line_number: int, text: str) -> GoldQuestion:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _fail(path, line_number, f"is not valid JSON ({exc.msg})") from exc

    if not isinstance(payload, dict):
        raise _fail(path, line_number, "must be a JSON object")

    keys = set(payload)
    if missing := REQUIRED_KEYS - keys:
        raise _fail(path, line_number, f"missing required key(s): {', '.join(sorted(missing))}")
    if unknown := keys - REQUIRED_KEYS - OPTIONAL_KEYS:
        raise _fail(path, line_number, f"unknown key(s): {', '.join(sorted(unknown))}")

    for key in ("id", "question", "gold_answer"):
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise _fail(path, line_number, f"{key} must be a non-empty string")

    must_include = _read_string_list(
        path, line_number, payload["must_include"], "must_include", allow_empty=False
    )
    must_not_claim = (
        _read_string_list(
            path, line_number, payload["must_not_claim"], "must_not_claim", allow_empty=True
        )
        if "must_not_claim" in payload
        else ()
    )
    expected = (
        _read_string_list(
            path, line_number, payload["expected_paths"], "expected_paths", allow_empty=False
        )
        if "expected_paths" in payload
        else ()
    )
    tags = (
        _read_string_list(path, line_number, payload["tags"], "tags", allow_empty=False)
        if "tags" in payload
        else ()
    )

    if overlap := set(must_include) & set(must_not_claim):
        raise _fail(
            path,
            line_number,
            "the same claim is both required and forbidden, so the question "
            f"can never be scored: {', '.join(sorted(overlap))}",
        )

    return GoldQuestion(
        id=payload["id"].strip(),
        question=payload["question"].strip(),
        gold_answer=payload["gold_answer"].strip(),
        must_include=must_include,
        must_not_claim=must_not_claim,
        expected_paths=frozenset(expected),
        tags=frozenset(tags),
    )


def load_gold_questions(path: str | Path) -> list[GoldQuestion]:
    """Read and validate a JSONL gold set, preserving file order.

    Raises ``GoldSetError`` on anything that would make a correctness figure
    unreliable: a missing file, a file with no questions, a line that does not
    parse, a blank or missing field, an unknown key, a question with nothing
    to check, a claim that is both required and forbidden, or a duplicate id.
    """
    path = Path(path)
    if not path.is_file():
        raise GoldSetError(f"gold set does not exist: {path}")

    questions: list[GoldQuestion] = []
    seen_ids: set[str] = set()

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw_line.strip()
        if not text or text.startswith("#"):
            continue

        question = _parse_line(path, line_number, text)
        if question.id in seen_ids:
            raise _fail(path, line_number, f"duplicate question id: {question.id}")
        seen_ids.add(question.id)
        questions.append(question)

    if not questions:
        raise GoldSetError(f"gold set contains no questions: {path}")

    return questions
