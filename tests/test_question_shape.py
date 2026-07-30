"""Splitting the question set by whether its wording reaches the answer's path."""

from __future__ import annotations

import json
from pathlib import Path

from answer_eval.question_shape import (
    QuestionShape,
    classify_question_set,
    common_path_terms,
    overlapping_terms,
    shape_counts,
    terms,
)


def test_terms_drops_stop_words_and_single_characters() -> None:
    assert terms("How is the a b walk resolved?") == {"walk", "resolved"}


def test_terms_splits_a_path_on_its_separators() -> None:
    assert terms("packages/core/src/fs_walk.py") == {
        "packages",
        "core",
        "src",
        "fs_walk",
        "py",
    }


def test_a_word_shared_with_the_path_is_path_shaped() -> None:
    """The overlap rule on its own, with nothing treated as widespread."""
    shapes = classify_question_set(
        {
            "q1": ("How are dbt references in SQL files resolved?", ["a/resolvers/dbt.py"]),
            "q2": ("Why does the scan stop descending?", ["a/resolvers/dbt.py"]),
        },
        ceiling=1.0,
    )
    assert shapes == {"q1": QuestionShape.PATH, "q2": QuestionShape.CONCEPT}


def test_a_term_most_paths_carry_does_not_make_a_question_path_shaped() -> None:
    """``repowise`` sits in every path of a Python monorepo.

    An overlap on it says nothing about whether the asker named the file, so
    a question whose only shared word is that term stays concept-shaped.
    """
    questions = {
        f"q{i}": ("What does repowise do about caching?", [f"src/repowise/mod{i}.py"])
        for i in range(10)
    }
    shapes = classify_question_set(questions)
    assert set(shapes.values()) == {QuestionShape.CONCEPT}


def test_common_path_terms_finds_the_widespread_ones() -> None:
    """Terms in most paths are common; a term in one of ten is not."""
    paths = [[f"src/repowise/mod{i}.py"] for i in range(9)] + [["other/d.py"]]
    common = common_path_terms(paths)
    assert {"src", "repowise", "py"} <= common
    assert "other" not in common


def test_common_path_terms_on_an_empty_set_is_empty() -> None:
    assert common_path_terms([]) == set()


def test_overlapping_terms_names_the_word_that_decided_it() -> None:
    assert overlapping_terms("Where is the collector?", ["a/telemetry/collector.py"], set()) == {
        "collector"
    }


def test_shape_counts_reports_both_buckets_even_when_one_is_empty() -> None:
    assert shape_counts([QuestionShape.PATH]) == {
        QuestionShape.PATH: 1,
        QuestionShape.CONCEPT: 0,
    }


def test_the_shipped_question_set_splits_into_two_usable_subsets() -> None:
    """A guard on the split, not on the tool.

    If a later edit to the question set empties one side, or shrinks it to a
    handful, the per-subset recall numbers quietly stop meaning anything. Ten
    questions is not a good subset — it is the floor below which the number
    should not be quoted at all.
    """
    path = Path(__file__).resolve().parent.parent / "answer_eval" / "questions" / "retrieval.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    questions = {r["id"]: (r["question"], r["expected_paths"]) for r in rows}

    counts = shape_counts(classify_question_set(questions).values())

    assert sum(counts.values()) == len(rows)
    assert counts[QuestionShape.PATH] >= 10
    assert counts[QuestionShape.CONCEPT] >= 10
