"""Judging an answer against a gold question.

The provider is a stub. What these assert is everything around the model
call: that the arithmetic is done here rather than by the judge, that a
forbidden claim is not diluted by covered ones, and that a judge response
which cannot be trusted stops the run instead of scoring zero.
"""

import pytest

from answer_eval.gold_set import GoldQuestion
from answer_eval.judge import (
    AnswerVerdict,
    ClaimVerdict,
    JudgeError,
    aggregate,
    build_prompt,
    judge_answer,
    parse_verdict,
)


def gold(qid="g001", must=("claim one", "claim two"), must_not=("wrong claim",)):
    return GoldQuestion(
        id=qid,
        question="How does it work?",
        gold_answer="Like this.",
        must_include=tuple(must),
        must_not_claim=tuple(must_not),
    )


def response(required, forbidden):
    import json

    return json.dumps(
        {
            "required": [{"n": n, "made": m, "quote": "q"} for n, m in enumerate(required, 1)],
            "forbidden": [{"n": n, "made": m, "quote": "q"} for n, m in enumerate(forbidden, 1)],
        }
    )


class StubProvider:
    """Returns each canned body in turn, recording what it was asked."""

    def __init__(self, *bodies, input_tokens=100, output_tokens=20):
        self.bodies = list(bodies)
        self.calls: list[dict] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    async def generate(self, **kwargs):
        self.calls.append(kwargs)

        class Result:
            content = self.bodies.pop(0)
            input_tokens = self.input_tokens
            output_tokens = self.output_tokens

        return Result()


def verdict(scores, forbidden_made=False, qid="g001"):
    return AnswerVerdict(
        question_id=qid,
        required=[ClaimVerdict(claim=f"c{n}", made=m, quote="") for n, m in enumerate(scores, 1)],
        forbidden=[ClaimVerdict(claim="w", made=forbidden_made, quote="")],
    )


class TestThePrompt:
    def test_claims_are_numbered_so_the_reply_can_be_lined_up(self):
        text = build_prompt(gold(), "an answer")
        assert "1. claim one" in text
        assert "2. claim two" in text
        assert "1. wrong claim" in text

    def test_the_answer_under_test_is_included(self):
        assert "an answer" in build_prompt(gold(), "an answer")

    def test_an_empty_answer_is_labelled_rather_than_left_blank(self):
        """A blank section reads as a formatting bug; the abstention is the point."""
        assert "no answer" in build_prompt(gold(), "   ")

    def test_a_question_with_no_forbidden_claims_says_so_explicitly(self):
        text = build_prompt(gold(must_not=()), "an answer")
        assert "empty forbidden list" in text


class TestScoringHappensInCodeNotInTheJudge:
    def test_score_is_the_fraction_of_required_claims_covered(self):
        assert parse_verdict(gold(), response([True, False], [False])).score == 0.5

    def test_a_forbidden_claim_zeroes_a_question_that_covered_everything(self):
        """The failure the set exists for: confident, fluent, and wrong.

        Letting four covered points dilute one wrong mechanism would score the
        worst answers as passable.
        """
        result = parse_verdict(gold(), response([True, True], [True]))
        assert result.score == 0.0
        assert result.contradicted is True
        assert result.n_covered == 2

    def test_covering_nothing_scores_zero_without_being_contradicted(self):
        result = parse_verdict(gold(), response([False, False], [False]))
        assert result.score == 0.0
        assert result.contradicted is False


class TestParsingAJudgeResponse:
    def test_a_code_fence_is_tolerated(self):
        """Providers add one despite being told not to; it is not a judge failure."""
        raw = "```json\n" + response([True, True], [False]) + "\n```"
        assert parse_verdict(gold(), raw).score == 1.0

    def test_the_quote_the_judge_decided_from_is_kept(self):
        raw = '{"required": [{"n": 1, "made": true, "quote": "  because X  "}], "forbidden": []}'
        result = parse_verdict(gold(must=("claim one",), must_not=()), raw)
        assert result.required[0].quote == "because X"

    def test_claims_are_paired_with_the_gold_set_text_not_the_judge_numbering(self):
        result = parse_verdict(gold(), response([True, False], [False]))
        assert [v.claim for v in result.required] == ["claim one", "claim two"]

    def test_a_non_json_response_raises(self):
        with pytest.raises(JudgeError, match="not JSON"):
            parse_verdict(gold(), "The answer looks pretty good to me.")

    def test_too_few_claims_raises_rather_than_reading_as_uncovered(self):
        """A short list would score as missed claims - a regression that never happened."""
        with pytest.raises(JudgeError, match="was asked about 2"):
            parse_verdict(gold(), response([True], [False]))

    def test_too_many_claims_raises(self):
        with pytest.raises(JudgeError, match="was asked about 2"):
            parse_verdict(gold(), response([True, True, True], [False]))

    def test_a_missing_forbidden_list_raises(self):
        with pytest.raises(JudgeError, match="'forbidden'"):
            parse_verdict(
                gold(), '{"required": [{"n": 1, "made": true}, {"n": 2, "made": true}]}'
            )

    def test_a_non_boolean_made_raises(self):
        raw = '{"required": [{"n": 1, "made": "yes"}, {"n": 2, "made": true}], "forbidden": []}'
        with pytest.raises(JudgeError, match="boolean"):
            parse_verdict(gold(must_not=()), raw)


class TestCallingTheJudge:
    async def test_a_clean_response_is_judged_in_one_call(self):
        provider = StubProvider(response([True, True], [False]))
        result = await judge_answer(provider, gold(), "an answer")
        assert result.score == 1.0
        assert len(provider.calls) == 1

    async def test_token_usage_is_recorded(self):
        """The judge is the eval's own spend, and it has to be attributable."""
        provider = StubProvider(response([True, True], [False]))
        result = await judge_answer(provider, gold(), "an answer")
        assert (result.input_tokens, result.output_tokens) == (100, 20)

    async def test_a_malformed_response_is_retried_once(self):
        provider = StubProvider("not json at all", response([True, False], [False]))
        result = await judge_answer(provider, gold(), "an answer")
        assert result.score == 0.5
        assert len(provider.calls) == 2

    async def test_two_failures_raise_rather_than_scoring_zero(self):
        """An API blip must not be reported as the tool getting everything wrong."""
        provider = StubProvider("nope", "still nope")
        with pytest.raises(JudgeError, match="failed twice"):
            await judge_answer(provider, gold(), "an answer")
        assert len(provider.calls) == 2

    async def test_the_judge_runs_at_a_fixed_temperature(self):
        """Two runs of the same answers must not disagree because of sampling."""
        provider = StubProvider(response([True, True], [False]))
        await judge_answer(provider, gold(), "an answer")
        assert provider.calls[0]["temperature"] == 0.0


class TestAggregate:
    def test_correctness_is_the_mean_of_the_question_scores(self):
        scores = aggregate([verdict([True, True], qid="a"), verdict([True, False], qid="b")])
        assert scores.correctness == pytest.approx(0.75)
        assert scores.n_questions == 2

    def test_fully_correct_counts_only_questions_with_nothing_missing(self):
        scores = aggregate([verdict([True, True], qid="a"), verdict([True, False], qid="b")])
        assert scores.n_fully_correct == 1

    def test_contradicted_questions_are_counted_separately_from_the_mean(self):
        """A confident wrong mechanism and a thin honest answer both score badly.

        Only this count separates them, and they are not the same problem.
        """
        scores = aggregate(
            [verdict([True, True], forbidden_made=True, qid="a"), verdict([False, False], qid="b")]
        )
        assert scores.correctness == 0.0
        assert scores.n_contradicted == 1

    def test_claim_coverage_shows_what_the_forbidden_penalty_cost(self):
        """Both answers covered every claim; one is zeroed. Coverage still says so."""
        scores = aggregate(
            [verdict([True, True], forbidden_made=True, qid="a"), verdict([True, True], qid="b")]
        )
        assert scores.correctness == pytest.approx(0.5)
        assert scores.claim_coverage == pytest.approx(1.0)

    def test_aggregating_nothing_raises(self):
        with pytest.raises(JudgeError, match="empty set of verdicts"):
            aggregate([])
