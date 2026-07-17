"""Answer-calibration analysis over benchmark result rows.

Two questions, both post-hoc over existing JSONL (no new agent runs):

1. Confidently-wrong rate per condition: of answers the blind judge scored
   correctness <= 4, how many expressed no uncertainty at all? A wrong
   answer that hedges is recoverable; a wrong answer delivered with full
   confidence is the dangerous one.
2. For conditions whose tool emits a `confidence` field in its results
   (repowise's get_answer/search envelopes), a calibration slice: judge
   correctness grouped by the tool-reported confidence mined from the raw
   transcript. Tools that emit no confidence signal simply have no curve —
   that absence is reported, not imputed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WRONG_THRESHOLD = 4

# General uncertainty lexicon for final answers. Distinct from the drift
# bench's staleness-specific hedges: this asks "did the answer express any
# uncertainty", not "did it surface index staleness".
_HEDGE_PATTERNS = [
    r"\bI (?:am|'m) not (?:sure|certain)\b",
    r"\bnot (?:entirely|fully|completely) (?:sure|certain|clear)\b",
    r"\bcannot (?:determine|verify|confirm|find)\b",
    r"\bcould not (?:determine|verify|confirm|find)\b",
    r"\bunclear\b",
    r"\buncertain\b",
    r"\b(?:appears|seems) to\b",
    r"\bmay (?:be|have|not)\b",
    r"\bmight (?:be|have|not)\b",
    r"\bpossibly\b",
    r"\bI (?:don't|do not) know\b",
    r"\bbased on (?:limited|partial|the available)\b",
]
_HEDGE_RE = re.compile("|".join(_HEDGE_PATTERNS), re.IGNORECASE)

_CONFIDENCE_RE = re.compile(r'\\?"confidence\\?"\s*:\s*\\?"(high|medium|low)\\?"')


def answer_expresses_uncertainty(answer: str) -> bool:
    return bool(_HEDGE_RE.search(answer or ""))


def extract_tool_confidences(stream_lines: list) -> list:
    """Tool-reported confidence values mined from tool_result blocks."""
    found = []
    for line in stream_lines:
        try:
            d = json.loads(line) if isinstance(line, str) else line
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("type") != "user":
            continue
        for block in d.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = json.dumps(block.get("content", ""), default=str)
                found.extend(m.group(1) for m in _CONFIDENCE_RE.finditer(content))
    return found


def _correctness(scores: dict) -> int | None:
    try:
        value = scores.get("correctness")
        return int(value) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def analyze(results_files: list) -> dict:
    per_condition = defaultdict(lambda: {"scored": 0, "wrong": 0,
                                         "confidently_wrong": 0})
    slices = defaultdict(lambda: defaultdict(list))  # condition -> conf -> scores

    for results_file in results_files:
        for line in Path(results_file).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("error"):
                continue
            correctness = _correctness(row.get("judge_scores", {}))
            if correctness is None:
                continue
            cond = row["condition"]
            stats = per_condition[cond]
            stats["scored"] += 1
            if correctness <= WRONG_THRESHOLD:
                stats["wrong"] += 1
                if not answer_expresses_uncertainty(row.get("answer", "")):
                    stats["confidently_wrong"] += 1

            raw_file = row.get("raw_output_file")
            if raw_file and Path(raw_file).exists():
                raw = json.loads(Path(raw_file).read_text(encoding="utf-8"))
                confidences = extract_tool_confidences(
                    raw.get("_raw_stream_lines", []))
                if confidences:
                    # Attribute the run to its weakest served confidence: one
                    # low-confidence retrieval taints the whole answer's basis.
                    order = {"low": 0, "medium": 1, "high": 2}
                    weakest = min(confidences, key=lambda c: order[c])
                    slices[cond][weakest].append(correctness)

    out = {"conditions": {}, "confidence_slices": {}}
    for cond, s in sorted(per_condition.items()):
        out["conditions"][cond] = {
            **s,
            "confidently_wrong_rate": (round(s["confidently_wrong"] / s["scored"], 4)
                                       if s["scored"] else None),
        }
    for cond, by_conf in sorted(slices.items()):
        out["confidence_slices"][cond] = {
            conf: {"n": len(scores),
                   "mean_correctness": round(sum(scores) / len(scores), 2),
                   "wrong_rate": round(sum(1 for x in scores
                                           if x <= WRONG_THRESHOLD) / len(scores), 4)}
            for conf, scores in sorted(by_conf.items())
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_files", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    summary = analyze(args.results_files)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
