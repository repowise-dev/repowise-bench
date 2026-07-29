"""Derive the runner-format task file from the frozen drift question set.

The drift runs reuse the ordinary experiment runner, which judges each
answer against the task's `answer` field: that must be gold_post (the truth
at the worktree commit), so correctness-vs-current comes free from the run.
The second judgment (vs gold_pre) and the staleness classification are
applied post-hoc by harness/drift_bench.py against the same frozen set.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "context_bench"


def main() -> None:
    drift = json.loads((BENCH / "questions_drift.json").read_text(encoding="utf-8"))
    tasks = [{
        "id": q["id"],
        "repo": "pallets/flask",
        "split_name": "drift15",
        "question": q["question"],
        "answer": q["gold_post"],
        "category": f"drift-{q['drift_kind']}",
    } for q in drift["questions"]]
    out = BENCH / "questions_drift_run.json"
    out.write_text(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}: {len(tasks)} tasks (gold_post as reference answer)")


if __name__ == "__main__":
    main()
