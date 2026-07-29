"""Per-category judge rubric injection.

The why-rubric must appear for history-why and ONLY for history-why: leaking
it into other categories would penalize ordinary code answers for not citing
history, and omitting it lets fluent invention score as correct.
"""

from harness.swe_qa_runner import CATEGORY_RUBRICS, build_judge_prompt

RUBRIC_MARKER = "ACTUAL\nrecorded reason"


def test_history_why_rubric_injected():
    prompt = build_judge_prompt("why q", "gold", "agent", category="history-why")
    assert CATEGORY_RUBRICS["history-why"].strip() in prompt
    # The core scoring instructions survive alongside the rubric.
    assert "REFERENCE ANSWER" in prompt and '"correctness"' in prompt


def test_other_categories_get_no_rubric():
    for category in ("symbol-lookup", "multi-hop-flow", "architecture-why",
                     "cross-file-impact", None, ""):
        prompt = build_judge_prompt("q", "gold", "agent", category=category)
        assert CATEGORY_RUBRICS["history-why"].strip() not in prompt


def test_prompt_is_blind_to_conditions():
    prompt = build_judge_prompt("q", "gold", "agent", category="history-why")
    for label in ("repowise", "serena", "codegraph", "deepwiki", "repomix",
                  "C0_", "C1_", "condition"):
        assert label not in prompt.lower()
