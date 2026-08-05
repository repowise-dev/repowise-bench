"""PR-localization scoring, parsing, and dataset filters. No network."""

from __future__ import annotations

import pytest

from harness.build_prloc_dataset import eligible_files, is_doc_path, is_test_path
from harness.prloc_bench import extract_paths, normalize_path, score_prediction


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

def test_normalize_strips_leading_dot_slash_and_case():
    assert normalize_path("./src/flask/app.py") == "src/flask/app.py"
    assert normalize_path("Src/Flask/App.py") == "src/flask/app.py"
    assert normalize_path("src\\flask\\app.py") == "src/flask/app.py"
    assert normalize_path("/src/flask/app.py") == "src/flask/app.py"
    assert normalize_path("  lib/reply.js  ") == "lib/reply.js"


def test_score_ignores_rename_style_duplicates():
    scores = score_prediction(["./src/a.py", "src/A.py"], ["src/a.py"])
    assert scores["f1"] == 1.0  # both normalize to the same path


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_perfect_prediction():
    s = score_prediction(["src/a.py", "src/b.py"], ["src/b.py", "src/a.py"])
    assert (s["precision"], s["recall"], s["f1"]) == (1.0, 1.0, 1.0)


def test_empty_prediction_scores_zero_not_error():
    s = score_prediction([], ["src/a.py"])
    assert (s["precision"], s["recall"], s["f1"]) == (0.0, 0.0, 0.0)


def test_superset_prediction_hurts_precision_only():
    s = score_prediction(["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
                         ["src/a.py", "src/b.py"])
    assert s["recall"] == 1.0
    assert s["precision"] == 0.5
    assert s["f1"] == pytest.approx(2 * 0.5 / 1.5, abs=1e-3)


def test_disjoint_prediction():
    s = score_prediction(["src/x.py"], ["src/a.py"])
    assert s["f1"] == 0.0


# ---------------------------------------------------------------------------
# JSON extraction from messy agent output
# ---------------------------------------------------------------------------

def test_extracts_clean_array():
    assert extract_paths('["src/a.py", "src/b.py"]') == ["src/a.py", "src/b.py"]


def test_extracts_last_array_after_reasoning():
    text = ('First I looked at ["src/wrong.py"] as a candidate...\n'
            'Final answer:\n```json\n["src/a.py", "src/b.py"]\n```')
    assert extract_paths(text) == ["src/a.py", "src/b.py"]


def test_non_string_arrays_ignored():
    assert extract_paths("scores were [1, 2, 3] overall") == []


def test_no_array_yields_empty_prediction():
    assert extract_paths("The files are src/a.py and src/b.py.") == []
    assert extract_paths("") == []


# ---------------------------------------------------------------------------
# Dataset filters
# ---------------------------------------------------------------------------

def test_test_paths_excluded_from_targets():
    files = ["src/flask/app.py", "tests/test_app.py", "src/test_utils.py",
             "lib/reply.test.js", "test/hooks.test.js"]
    assert eligible_files(files) == ["src/flask/app.py"]


def test_doc_detection():
    assert is_doc_path("docs/index.rst")
    assert is_doc_path("README.md")
    assert not is_doc_path("src/flask/app.py")


def test_test_detection_edge_cases():
    assert is_test_path("tests/conftest.py")
    assert not is_test_path("src/contest_manager.py")  # 'test' substring only
