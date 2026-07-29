"""Derive the frozen flask question set for the context-tool benchmark.

The flask split of data/swe_qa/tasks.json (48 questions) is already
battle-tested, so the context bench reuses it rather than authoring new
flask questions. Two transformations, both deterministic so the derivation
is auditable:

1. Selection: the 48 questions come in four authored buckets of 12
   (What / How / Why / Where, in id order). Take the even-indexed half of
   each bucket: 24 questions, stratified across the original intent mix
   with no cherry-picking by expected difficulty or tool fit.
2. Re-tag: each selected question gets a `category` from the context-bench
   taxonomy (symbol-lookup | multi-hop-flow | architecture-why |
   history-why | cross-file-impact), assigned by reading the question.
   flask48 was authored from the code alone, so it contains no history-why
   questions; that category is covered by the separate why-question set.

Output: data/context_bench/questions_flask.json (schema identical to
tasks.json plus `category`, split_name rewritten to flask24).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Hand-assigned from the question text; see module docstring for the taxonomy.
CATEGORY = {
    "flask_000": "symbol-lookup",
    "flask_002": "architecture-why",
    "flask_004": "multi-hop-flow",
    "flask_006": "symbol-lookup",
    "flask_008": "multi-hop-flow",
    "flask_010": "architecture-why",
    "flask_012": "symbol-lookup",
    "flask_014": "multi-hop-flow",
    "flask_016": "symbol-lookup",
    "flask_018": "multi-hop-flow",
    "flask_020": "architecture-why",
    "flask_022": "architecture-why",
    "flask_024": "architecture-why",
    "flask_026": "architecture-why",
    "flask_028": "architecture-why",
    "flask_030": "cross-file-impact",
    "flask_032": "cross-file-impact",
    "flask_034": "architecture-why",
    "flask_036": "multi-hop-flow",
    "flask_038": "multi-hop-flow",
    "flask_040": "multi-hop-flow",
    "flask_042": "cross-file-impact",
    "flask_044": "cross-file-impact",
    "flask_046": "cross-file-impact",
}


def main() -> None:
    tasks = json.loads((ROOT / "data/swe_qa/tasks.json").read_text(encoding="utf-8"))
    flask = [t for t in tasks if t["split_name"] == "flask"]
    assert len(flask) == 48, f"expected 48 flask tasks, got {len(flask)}"

    selected = []
    for task in flask:
        if task["id"] not in CATEGORY:
            continue
        selected.append({**task, "split_name": "flask24",
                         "category": CATEGORY[task["id"]]})
    assert len(selected) == len(CATEGORY) == 24

    out = ROOT / "data/context_bench/questions_flask.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(selected, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    counts: dict[str, int] = {}
    for q in selected:
        counts[q["category"]] = counts.get(q["category"], 0) + 1
    print(f"wrote {out} ({len(selected)} questions): {counts}")


if __name__ == "__main__":
    main()
