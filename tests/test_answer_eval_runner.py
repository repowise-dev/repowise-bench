"""Running a question set and aggregating the result.

The answer tool is stubbed here. What these assert is the bookkeeping between
the tool and the blob: that the paths scored are the ones the tool returned,
that a shape the tool can legitimately produce - a confident answer with no
retrieval at all - is counted rather than averaged away, and that a payload the
tool should never produce stops the run instead of scoring.
"""

import pytest

from answer_eval.index import IndexBuildReport
from answer_eval.question_set import RetrievalQuestion
from answer_eval.runner import RunnerError, retrieved_paths, run_question_set
from answer_eval.server_session import EmbedderConfig, SynthesisModel

EMBEDDER = EmbedderConfig(name="gemini", model="gemini-embedding-001", dims=768)
SYNTHESIS = SynthesisModel(provider="gemini", model="gemini-3.1-flash-lite-preview")
INDEX = IndexBuildReport(
    pages_written=3,
    vectors_written=3,
    embed_failures=0,
    embedder="gemini",
    embed_recipe="content",
    repo_dir="/tmp/eval",
)


def question(qid="q001", expected=("a.py",), text="Where?"):
    return RetrievalQuestion(
        id=qid, question=text, expected_paths=frozenset(expected), tags=frozenset()
    )


def payload(paths=("a.py",), confidence="high", answer="Because of X.", **kw):
    body = {
        "answer": answer,
        "confidence": confidence,
        "retrieval": [{"path": p} for p in paths],
    }
    body.update(kw)
    return body


def tool_returning(*payloads):
    """An answer tool that returns each payload in turn."""
    remaining = list(payloads)

    async def answer_tool(question_text, *args, **kwargs):
        return remaining.pop(0)

    return answer_tool


async def run(questions, payloads, k=5):
    return await run_question_set(
        tool_returning(*payloads),
        questions,
        snapshot_short_id="45ce57f52457",
        index=INDEX,
        embedder=EMBEDDER,
        synthesis=SYNTHESIS,
        k=k,
    )


class TestRetrievedPaths:
    def test_keeps_order(self):
        assert retrieved_paths(payload(paths=("b.py", "a.py"))) == ["b.py", "a.py"]

    def test_missing_retrieval_key_is_no_paths_not_a_crash(self):
        assert retrieved_paths({"answer": "x", "confidence": "high"}) == []

    def test_a_hit_with_no_path_is_dropped_rather_than_kept_as_blank(self, caplog):
        """A pathless hit cannot match an expectation, and must not eat a top-k slot.

        `retrieval[].path` is read off the hit with `.get`, so it can be absent.
        Keeping it as "" would fill a rank position and depress recall for a
        reason that has nothing to do with retrieval quality.
        """
        payload_with_gap = {"retrieval": [{"path": None}, {"path": "a.py"}, {}]}
        with caplog.at_level("WARNING"):
            assert retrieved_paths(payload_with_gap) == ["a.py"]
        assert "no path" in caplog.text


class TestScoresWhatTheToolReturned:
    async def test_recall_and_mrr_come_from_retrieval_paths(self):
        report = await run(
            [question(expected=("a.py",))],
            [payload(paths=("z.py", "a.py"))],
        )
        assert report.scores.recall_at_k == 1.0
        assert report.scores.mrr == pytest.approx(0.5)

    async def test_a_miss_scores_zero_without_raising(self):
        report = await run([question(expected=("a.py",))], [payload(paths=("z.py",))])
        assert report.scores.recall_at_k == 0.0
        assert report.scores.mrr == 0.0

    async def test_confidence_distribution_counts_every_question(self):
        report = await run(
            [question("q1"), question("q2"), question("q3")],
            [payload(confidence="high"), payload(confidence="low"), payload(confidence="high")],
        )
        assert report.confidence_counts == {"high": 2, "low": 1}

    async def test_abstention_is_an_empty_answer_not_a_low_confidence(self):
        """Low confidence with a real answer is a hedge; no text at all is a punt.

        The two are worth different amounts to a caller and must not be one
        number - a tool that got more cautious and a tool that stopped
        answering look the same once they are merged.
        """
        report = await run(
            [question("q1"), question("q2")],
            [payload(confidence="low", answer="Probably X."), payload(answer="   ")],
        )
        assert report.abstention_rate == pytest.approx(0.5)

    async def test_recall_when_high_covers_only_the_confident_answers(self):
        report = await run(
            [question("q1"), question("q2")],
            [
                payload(confidence="high", paths=("a.py",)),
                payload(confidence="low", paths=("z.py",)),
            ],
        )
        assert report.recall_at_k_when_high == 1.0
        assert report.scores.recall_at_k == pytest.approx(0.5)

    async def test_recall_when_high_is_none_when_nothing_reached_high(self):
        """None, not 0.0 - "no confident answers" is not "confident and wrong"."""
        report = await run([question()], [payload(confidence="low")])
        assert report.recall_at_k_when_high is None


class TestNonRetrievalGrounding:
    async def test_an_answer_with_no_retrieval_is_counted_by_its_grounding(self):
        """The tool answers some questions off an exact symbol match, retrieval empty.

        Those score zero on a retrieval metric while being good answers. If the
        count is not surfaced, a rise in them is indistinguishable from a
        retrieval regression.
        """
        report = await run(
            [question()],
            [payload(paths=(), confidence="high", grounding="exact_symbol")],
        )
        assert report.non_retrieval_grounding_counts == {"exact_symbol": 1}
        assert report.scores.n_empty_results == 1

    async def test_a_plain_retrieval_miss_is_labelled_as_such(self):
        report = await run([question()], [payload(paths=())])
        assert report.non_retrieval_grounding_counts == {"retrieval": 1}


class TestMalformedRunStops:
    async def test_empty_question_set_raises(self):
        with pytest.raises(RunnerError, match="empty question set"):
            await run([], [])

    async def test_a_non_dict_payload_raises_rather_than_scoring_zero(self):
        with pytest.raises(RunnerError, match="not a dict"):
            await run([question()], ["I am a string"])

    async def test_an_unexpected_confidence_is_warned_about_not_swallowed(self, caplog):
        """A new confidence value means the tool's contract moved under the eval."""
        with caplog.at_level("WARNING"):
            report = await run([question()], [payload(confidence="very-high")])
        assert "unrecognised confidence" in caplog.text
        assert report.confidence_counts == {"very-high": 1}


class TestBlob:
    async def test_carries_the_embedder_and_recipe_next_to_the_numbers(self):
        """A recall number with no record of how it was embedded is not comparable."""
        report = await run([question()], [payload()])
        blob = report.as_blob()
        assert blob["embedder"] == {
            "name": "gemini",
            "model": "gemini-embedding-001",
            "dims": 768,
        }
        assert blob["index"]["embed_recipe"] == "content"
        assert blob["snapshot_short_id"] == "45ce57f52457"

    async def test_keeps_every_question_so_a_number_can_be_traced_back(self):
        report = await run([question("q1"), question("q2")], [payload(), payload()])
        blob = report.as_blob()
        assert [q["id"] for q in blob["questions"]] == ["q1", "q2"]
        assert blob["questions"][0]["retrieved_paths"] == ["a.py"]


class TestFileGranularity:
    """A file has a page, and so does each of its symbols.

    Retrieval returning the right file at the wrong granularity is a different
    failure from returning the wrong file, and only the gap between the two
    recall figures separates them.
    """

    async def test_a_symbol_page_of_the_expected_file_misses_strictly(self):
        report = await run(
            [question(expected=("a.py",))], [payload(paths=("a.py::parse",))]
        )
        assert report.scores.recall_at_k == 0.0
        assert report.recall_at_k_by_file == 1.0

    async def test_a_genuinely_wrong_file_misses_on_both(self):
        """The looser metric must not launder a real miss into a hit."""
        report = await run(
            [question(expected=("a.py",))], [payload(paths=("z.py::parse",))]
        )
        assert report.scores.recall_at_k == 0.0
        assert report.recall_at_k_by_file == 0.0

    async def test_an_exact_hit_scores_the_same_on_both(self):
        report = await run([question(expected=("a.py",))], [payload(paths=("a.py",))])
        assert report.scores.recall_at_k == 1.0
        assert report.recall_at_k_by_file == 1.0


class TestSynthesisModelIsRecorded:
    async def test_the_blob_names_the_model_that_wrote_the_answers(self):
        """Confidence is a property of the model as much as of the corpus.

        The answer tool picks its provider from whichever API key is in the
        environment, so without this a confidence distribution records nothing
        about what produced it and two runs are not comparable.
        """
        report = await run([question()], [payload()])
        assert report.as_blob()["synthesis"] == {
            "provider": "gemini",
            "model": "gemini-3.1-flash-lite-preview",
        }
