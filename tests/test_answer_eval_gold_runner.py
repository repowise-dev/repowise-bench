"""Running the judged gold set.

Both the answer tool and the judge are stubbed. What these assert is the
bookkeeping a correctness figure rests on: that a contradiction at high
confidence is counted where it can be seen, that correctness is broken down by
the confidence the tool claimed, and that a cost figure is either real or
absent.
"""

import pytest

from answer_eval.gold_runner import run_gold_set, write_gold_blob
from answer_eval.gold_set import GoldQuestion
from answer_eval.index import IndexBuildReport
from answer_eval.judge import JudgeModel
from answer_eval.runner import RunnerError
from answer_eval.server_session import EmbedderConfig, SynthesisModel
from answer_eval.token_meter import TokenMeter

EMBEDDER = EmbedderConfig(name="gemini", model="gemini-embedding-001", dims=768)
SYNTHESIS = SynthesisModel(provider="gemini", model="gemini-3.1-flash-lite-preview")
JUDGE = JudgeModel(provider="gemini", model="gemini-3.1-flash-lite-preview")
INDEX = IndexBuildReport(
    pages_written=3,
    vectors_written=3,
    embed_failures=0,
    embedder="gemini",
    embed_recipe="content",
    repo_dir="/tmp/eval",
    symbols={"symbols_written": 10},
)


def gold(qid="g001", must=("claim one", "claim two"), must_not=("wrong claim",), paths=("a.py",)):
    return GoldQuestion(
        id=qid,
        question=f"How does {qid} work?",
        gold_answer="Like this.",
        must_include=tuple(must),
        must_not_claim=tuple(must_not),
        expected_paths=frozenset(paths),
    )


def payload(answer="Because of X.", confidence="high", cites=("a.py",)):
    return {
        "answer": answer,
        "confidence": confidence,
        "citations": list(cites),
        "retrieval": [],
    }


def tool_returning(*payloads):
    remaining = list(payloads)

    async def answer_tool(question_text, *args, **kwargs):
        return remaining.pop(0)

    return answer_tool


def judge_saying(*verdicts):
    """A judge whose reply per question is (required_made, forbidden_made)."""
    import json

    remaining = list(verdicts)

    class Provider:
        async def generate(self, **kwargs):
            required, forbidden = remaining.pop(0)

            class Result:
                content = json.dumps(
                    {
                        "required": [
                            {"n": n, "made": m, "quote": ""} for n, m in enumerate(required, 1)
                        ],
                        "forbidden": [
                            {"n": n, "made": m, "quote": ""} for n, m in enumerate(forbidden, 1)
                        ],
                    }
                )
                input_tokens = 50
                output_tokens = 10

            return Result()

    return Provider()


async def run(questions, payloads, verdicts, **kwargs):
    return await run_gold_set(
        tool_returning(*payloads),
        judge_saying(*verdicts),
        questions,
        snapshot_short_id="45ce57f52457",
        index=kwargs.pop("index", INDEX),
        embedder=EMBEDDER,
        synthesis=SYNTHESIS,
        judge=kwargs.pop("judge", JUDGE),
        **kwargs,
    )


class TestCorrectness:
    async def test_the_headline_is_the_mean_of_the_question_scores(self):
        report = await run(
            [gold("g001"), gold("g002")],
            [payload(), payload()],
            [([True, True], [False]), ([True, False], [False])],
        )
        assert report.scores.correctness == pytest.approx(0.75)
        assert report.scores.n_fully_correct == 1

    async def test_the_answer_text_is_kept_next_to_its_verdict(self):
        report = await run([gold()], [payload(answer="It works via Y.")], [([True, True], [False])])
        question = report.as_blob()["questions"][0]
        assert question["answer_text"] == "It works via Y."
        assert question["verdict"]["n_covered"] == 2

    async def test_an_abstention_is_recorded_as_such(self):
        report = await run([gold()], [payload(answer="  ")], [([False, False], [False])])
        assert report.abstention_rate == 1.0
        assert report.as_blob()["questions"][0]["answered"] is False


class TestConfidenceIsWhatThisMeasures:
    async def test_correctness_is_broken_down_by_the_confidence_the_tool_claimed(self):
        """The tool's confidence is a promise that verification is unnecessary.

        A single correctness number cannot say whether that promise is kept;
        only the breakdown can.
        """
        report = await run(
            [gold("g001"), gold("g002")],
            [payload(confidence="high"), payload(confidence="low")],
            [([True, False], [False]), ([True, True], [False])],
        )
        assert report.correctness_by_confidence == {"high": 0.5, "low": 1.0}

    async def test_a_contradiction_at_high_confidence_is_counted_on_its_own(self):
        """The worst thing the tool can do, and it must not be averaged away."""
        report = await run(
            [gold("g001"), gold("g002")],
            [payload(confidence="high"), payload(confidence="low")],
            [([True, True], [True]), ([False, False], [True])],
        )
        assert report.scores.n_contradicted == 2
        assert report.n_contradicted_at_high == 1

    async def test_a_contradiction_is_warned_about_as_it_happens(self, caplog):
        with caplog.at_level("WARNING"):
            await run([gold()], [payload()], [([True, True], [True])])
        assert "names as wrong" in caplog.text


class TestRetrievalIsRecordedButNotScored:
    async def test_whether_an_expected_page_reached_the_caller_is_kept(self):
        """Separates 'retrieval missed it' from 'synthesis got it wrong'."""
        report = await run(
            [gold(paths=("a.py",))], [payload(cites=("z.py",))], [([True, True], [False])]
        )
        assert report.as_blob()["questions"][0]["found_expected_path"] is False

    async def test_a_right_answer_citing_an_unexpected_page_still_scores_full(self):
        report = await run(
            [gold(paths=("a.py",))], [payload(cites=("z.py",))], [([True, True], [False])]
        )
        assert report.scores.correctness == 1.0


class TestCost:
    async def test_a_metered_run_reports_cost_per_question(self):
        meter = TokenMeter(calls=2, input_tokens=2000, output_tokens=1000)
        report = await run(
            [gold("g001"), gold("g002")],
            [payload(), payload()],
            [([True, True], [False]), ([True, True], [False])],
            meter=meter,
        )
        # gemini-3.1-flash-lite-preview: $0.00025/1k in, $0.0015/1k out.
        assert report.tool_side_usd_per_question == pytest.approx((0.0005 + 0.0015) / 2)

    async def test_an_unmetered_run_reports_no_cost_rather_than_zero(self):
        """A zero would read as a free tool. Absent reads as not measured."""
        report = await run([gold()], [payload()], [([True, True], [False])])
        assert report.tool_side_usd_per_question is None

    async def test_the_judges_own_tokens_are_reported_separately(self):
        report = await run([gold()], [payload()], [([True, True], [False])])
        assert report.judge_tokens == {"input_tokens": 50, "output_tokens": 10}


class TestARunThatCannotBeReadHonestlyIsLabelled:
    async def test_an_empty_gold_set_raises(self):
        with pytest.raises(RunnerError, match="empty gold set"):
            await run([], [], [])

    async def test_a_non_dict_payload_raises(self):
        with pytest.raises(RunnerError, match="not a dict"):
            await run([gold()], ["a string"], [])

    async def test_a_symbol_less_index_carries_a_warning(self):
        import dataclasses

        report = await run(
            [gold()],
            [payload()],
            [([True, True], [False])],
            index=dataclasses.replace(INDEX, symbols={}),
        )
        assert any("no symbols" in w for w in report.warnings)

    async def test_a_judge_from_the_same_family_as_the_answerer_is_flagged(self):
        """Not fatal, and not fixable with one key on the box - but a confound.

        A correctness figure that does not carry the caveat gets read without
        it.
        """
        report = await run(
            [gold()],
            [payload()],
            [([True, True], [False])],
            judge=JudgeModel(provider="gemini", model="m", same_family_as_synthesis=True),
        )
        assert any("grading its own family" in w for w in report.warnings)


class TestBlob:
    async def test_it_names_both_models_and_the_corpus(self, tmp_path):
        report = await run([gold()], [payload()], [([True, True], [False])])
        path = write_gold_blob(report, tmp_path / "gold.json")

        import json

        blob = json.loads(path.read_text())
        assert blob["synthesis"]["model"] == "gemini-3.1-flash-lite-preview"
        assert blob["judge"]["provider"] == "gemini"
        assert blob["snapshot_short_id"] == "45ce57f52457"
