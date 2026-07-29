"""Loading the judged gold set.

Every check here raises rather than warns, for the reason the retrieval
loader does: a gold set that quietly drops a required point still produces a
correctness figure, and that figure is indistinguishable from a real one.
"""

import pytest

from answer_eval.gold_set import GoldSetError, load_gold_questions

VALID = {
    "id": "g001",
    "question": "What happens when a result is too big?",
    "gold_answer": "The host rejects it.",
    "must_include": ["the result is rejected"],
}


def write(tmp_path, *rows, name="gold.jsonl"):
    import json

    path = tmp_path / name
    path.write_text(
        "\n".join(row if isinstance(row, str) else json.dumps(row) for row in rows),
        encoding="utf-8",
    )
    return path


class TestAWellFormedSet:
    def test_one_question_round_trips(self, tmp_path):
        (question,) = load_gold_questions(write(tmp_path, VALID))
        assert question.id == "g001"
        assert question.must_include == ("the result is rejected",)
        assert question.must_not_claim == ()
        assert question.expected_paths == frozenset()

    def test_file_order_is_preserved(self, tmp_path):
        rows = [{**VALID, "id": f"g{n:03d}"} for n in (3, 1, 2)]
        assert [q.id for q in load_gold_questions(write(tmp_path, *rows))] == [
            "g003",
            "g001",
            "g002",
        ]

    def test_blank_and_comment_lines_are_skipped(self, tmp_path):
        path = write(tmp_path, "# a note", "", VALID)
        assert len(load_gold_questions(path)) == 1

    def test_optional_keys_are_read_when_present(self, tmp_path):
        row = {
            **VALID,
            "must_not_claim": ["it is truncated"],
            "expected_paths": ["a/b.py"],
            "tags": ["how"],
        }
        (question,) = load_gold_questions(write(tmp_path, row))
        assert question.must_not_claim == ("it is truncated",)
        assert question.expected_paths == frozenset({"a/b.py"})
        assert question.tags == frozenset({"how"})


class TestAMalformedSetStopsTheRun:
    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(GoldSetError, match="does not exist"):
            load_gold_questions(tmp_path / "nope.jsonl")

    def test_a_file_with_no_questions_raises(self, tmp_path):
        with pytest.raises(GoldSetError, match="no questions"):
            load_gold_questions(write(tmp_path, "# only a comment"))

    def test_a_line_that_is_not_json_raises_with_its_line_number(self, tmp_path):
        with pytest.raises(GoldSetError, match="line 2"):
            load_gold_questions(write(tmp_path, VALID, "{not json"))

    def test_a_missing_required_key_raises(self, tmp_path):
        row = {k: v for k, v in VALID.items() if k != "gold_answer"}
        with pytest.raises(GoldSetError, match="gold_answer"):
            load_gold_questions(write(tmp_path, row))

    def test_an_unknown_key_raises_rather_than_being_ignored(self, tmp_path):
        """A mistyped key is how a required point silently stops being checked."""
        with pytest.raises(GoldSetError, match="unknown key"):
            load_gold_questions(write(tmp_path, {**VALID, "must_includes": ["x"]}))

    def test_a_blank_field_raises(self, tmp_path):
        with pytest.raises(GoldSetError, match="gold_answer"):
            load_gold_questions(write(tmp_path, {**VALID, "gold_answer": "  "}))

    def test_a_question_with_nothing_to_check_raises(self, tmp_path):
        """An empty must_include scores 100% on every answer, including none."""
        with pytest.raises(GoldSetError, match="must_include must not be empty"):
            load_gold_questions(write(tmp_path, {**VALID, "must_include": []}))

    def test_a_duplicate_id_raises(self, tmp_path):
        with pytest.raises(GoldSetError, match="duplicate question id"):
            load_gold_questions(write(tmp_path, VALID, VALID))

    def test_a_claim_that_is_both_required_and_forbidden_raises(self, tmp_path):
        """The question could never be scored: covering the point also fails it."""
        row = {**VALID, "must_not_claim": ["the result is rejected"]}
        with pytest.raises(GoldSetError, match="both required and forbidden"):
            load_gold_questions(write(tmp_path, row))

    def test_a_non_string_required_point_raises(self, tmp_path):
        with pytest.raises(GoldSetError, match="must_include"):
            load_gold_questions(write(tmp_path, {**VALID, "must_include": [{"point": "x"}]}))
