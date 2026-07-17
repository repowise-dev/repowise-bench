"""Staleness classification for indexed context tools.

Every indexing tool serves an increasingly stale view as the repo moves under
its frozen index. The drift protocol runs each arm against a worktree
advanced to a later commit (N+k) while every index still reflects the pinned
commit N, then classifies each answer:

    CORRECT          matches the N+k truth (gold_post)
    STALE-CONFIDENT  matches the N truth (gold_pre) with no hedge or warning
                     surfaced — the failure mode this benchmark measures
    FLAGGED          the tool or the agent surfaced staleness/uncertainty
    WRONG-OTHER      everything else, including judge ties (an answer that
                     scores high against BOTH golds on a drifted question is
                     ambiguous and is never silently credited as CORRECT)

The run phase is ordinary run_experiment over the drift question file with
gold_post as the reference answer, so each result row already carries the
correctness judgment. This module adds the second blind judgment against
gold_pre, scans the raw transcript for staleness markers, and emits one
classified row per run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.swe_qa_runner import judge_answer  # noqa: E402

CLASSES = ("CORRECT", "STALE-CONFIDENT", "FLAGGED", "WRONG-OTHER")

# Judge score (correctness dimension) at or above which an answer "matches"
# a reference. 7 tracks the flask48 rubric's "factually accurate with minor
# omissions" band.
MATCH_THRESHOLD = 7

# Markers a tool can emit in its results when it detects its own staleness.
# Matched against raw tool_result content in the stream, not the answer.
_TOOL_STALENESS_MARKERS = ("stale_warning", "tombstone", '"index_behind": true')

# An agent hedging in the final answer also counts as surfacing uncertainty.
# Deliberately small and literal: over-matching hedges would launder
# stale-confident answers into FLAGGED.
_HEDGE_PATTERNS = [
    r"\bmay (?:have|be) (?:changed|outdated|stale)\b",
    r"\b(?:might|may) (?:be|no longer be) (?:current|accurate|up.to.date)\b",
    r"\bcannot determine\b",
    r"\bcould not (?:verify|confirm)\b",
    r"\bnot (?:certain|sure)\b",
    r"\bindex (?:is|appears|may be) (?:stale|outdated|behind)\b",
    r"\bas of (?:an|the) (?:older|earlier|indexed) (?:version|commit|snapshot)\b",
]
_HEDGE_RE = re.compile("|".join(_HEDGE_PATTERNS), re.IGNORECASE)


def transcript_flags_staleness(stream_lines: list) -> bool:
    """True when any tool result in the stream carries a staleness marker."""
    for line in stream_lines:
        if not isinstance(line, str):
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("type") != "user":
            continue
        for block in d.get("message", {}).get("content", []):
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            content = json.dumps(block.get("content", ""), default=str)
            if any(marker in content for marker in _TOOL_STALENESS_MARKERS):
                return True
    return False


def answer_hedges(answer: str) -> bool:
    return bool(_HEDGE_RE.search(answer or ""))


def classify(post_correct: int, pre_correct: int, flagged: bool,
             hedged: bool, drift_kind: str,
             threshold: int = MATCH_THRESHOLD) -> str:
    """Map one run's evidence to a drift class. Pure, exhaustively tested."""
    matches_post = post_correct >= threshold
    matches_pre = pre_correct >= threshold
    surfaced = flagged or hedged

    if drift_kind == "none":
        # Control question: both golds coincide, staleness cannot show.
        return "CORRECT" if matches_post else ("FLAGGED" if surfaced else "WRONG-OTHER")

    if matches_post and matches_pre:
        # Judge tie on a drifted question: the answer cannot simultaneously
        # be the old truth and the new truth — never silently CORRECT.
        return "FLAGGED" if surfaced else "WRONG-OTHER"
    if matches_post:
        return "CORRECT"
    if matches_pre:
        return "FLAGGED" if surfaced else "STALE-CONFIDENT"
    return "FLAGGED" if surfaced else "WRONG-OTHER"


def _correctness(scores: dict) -> int:
    try:
        return int(scores.get("correctness", 0))
    except (TypeError, ValueError):
        return 0


def classify_results(results_path: Path, drift_set_path: Path,
                     judge_model: str, out_path: Path) -> dict:
    drift_set = json.loads(drift_set_path.read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in drift_set["questions"]}

    counts: dict = {}
    with open(out_path, "w", encoding="utf-8") as out:
        for line in results_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            q = by_id.get(row["task_id"])
            if q is None or row.get("error"):
                continue

            post_correct = _correctness(row.get("judge_scores", {}))

            # Second blind judgment: same judge, gold_pre as reference.
            pre_scores = judge_answer(
                question=q["question"], gold_answer=q["gold_pre"],
                agent_answer=row.get("answer", ""), judge_model=judge_model)
            pre_correct = _correctness(pre_scores)

            flagged = False
            raw_file = row.get("raw_output_file")
            if raw_file and Path(raw_file).exists():
                raw = json.loads(Path(raw_file).read_text(encoding="utf-8"))
                flagged = transcript_flags_staleness(
                    raw.get("_raw_stream_lines", []))
            hedged = answer_hedges(row.get("answer", ""))

            cls = classify(post_correct, pre_correct, flagged, hedged,
                           q.get("drift_kind", ""))
            counts.setdefault(row["condition"], {}).setdefault(cls, 0)
            counts[row["condition"]][cls] += 1
            out.write(json.dumps({
                "task_id": row["task_id"], "condition": row["condition"],
                "drift_kind": q.get("drift_kind", ""),
                "class": cls,
                "judge_post_correctness": post_correct,
                "judge_pre_correctness": pre_correct,
                "tool_flagged": flagged, "answer_hedged": hedged,
            }) + "\n")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, required=True,
                    help="swe_qa.jsonl from the drift run")
    ap.add_argument("--drift-set", type=Path, required=True,
                    help="frozen questions_drift.json")
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    counts = classify_results(args.results, args.drift_set,
                              args.judge_model, args.out)
    for condition, by_class in sorted(counts.items()):
        total = sum(by_class.values())
        stale = by_class.get("STALE-CONFIDENT", 0)
        print(f"{condition:16} {by_class}  stale-confident: {stale}/{total}")


if __name__ == "__main__":
    main()
