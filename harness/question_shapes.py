"""Question shape for the 48 django SWE-QA questions, and the stratified draw.

PLAN.md Phase 3 has said since it was written that the single most
differentiating chart in this bake-off is a slice by question type, and no run
has ever done it. The rung 6 pilot and the four-arm discriminator both used
`django_000` to `django_009` **in file order**. That is the largest
methodological gap in the Layer B result and it is free to fix.

It is worse than "unstratified". The SWE-QA django rows are laid out by
interrogative — roughly `What` at 000-011, `How` at 012-022, `Why` at 023-035,
`Where` at 036-047 — so "the first ten in file order" is not a sample of the
question set at all. It is the `What` block. Every Layer B number published so
far is a number about one interrogative class, and the control answered them in
6.2 tool calls over 2.7 files, which is what a grep-shaped question looks like.

---------------------------------------------------------------------------
The rule this file was written under
---------------------------------------------------------------------------

**Classified before any arm's per-question performance was looked at**, on
2026-08-03, from the question text alone. That ordering is the whole point: a
classification made after seeing which questions an arm won is selection on the
outcome, and it is unfalsifiable afterwards because the labels look the same
either way. The labels are committed here so that a later reader can disagree
with an individual call without being able to claim the set was chosen to fit.

Shapes, from PLAN.md Phase 3:

    symbol-lookup       the answer is a named thing; finding it is the task.
    multi-hop-flow      the answer is a path — control or data — that has to be
                        followed across functions or files.
    architecture-why    design rationale, contract, or pattern. Why it is built
                        this way, not where it is.
    history-why         why a change was made. Blame and commit territory.
    cross-file-impact   what depends on this, or what breaks if it changes.

**Two findings fall out of the classification itself, before any arm runs.**

1. **`history-why` is EMPTY. Zero of 48.** PLAN.md predicted that slice as one
   where graph-only tools "structurally score zero", and it is the strongest
   place repowise's git layer could have shown a difference no competitor can
   reach. It is not in this benchmark. That is a fact about SWE-QA, not about
   the tools, and it means this repo cannot test that hypothesis at all — a
   result worth publishing as it stands and a reason to look for a second
   question source rather than to quietly drop the slice.

2. **A sixth shape exists that PLAN.md's taxonomy does not have**:
   `performance-why`, four questions asking why some code path is slow and what
   would make it faster. They are not architecture-rationale and not flow
   tracing. They are recorded as their own slice rather than forced into a
   neighbouring one, because a taxonomy bent to fit is how a category stops
   meaning anything.

Distribution: architecture-why 16, multi-hop-flow 14, symbol-lookup 9,
cross-file-impact 5, performance-why 4, history-why 0.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[1]
SHAPES_FILE = BENCH_ROOT / "data" / "swe_qa" / "django_question_shapes.json"

# Fixed, committed, and used for every draw from this classification. A seed
# chosen after seeing a draw is not a seed, it is a selection.
STRATIFIED_SEED = 20260803

SHAPES = (
    "symbol-lookup",
    "multi-hop-flow",
    "architecture-why",
    "history-why",
    "cross-file-impact",
    "performance-why",
)


def load() -> dict:
    return json.loads(SHAPES_FILE.read_text(encoding="utf-8"))


def by_shape() -> dict[str, list[str]]:
    doc = load()
    out: dict[str, list[str]] = {s: [] for s in SHAPES}
    for qid, row in sorted(doc["questions"].items()):
        out[row["shape"]].append(qid)
    return out


def stratified(per_shape: int = 3, seed: int = STRATIFIED_SEED) -> list[str]:
    """`per_shape` questions from each non-empty slice, drawn once from a seed.

    Equal allocation rather than proportional, deliberately. Proportional would
    put 5 of 15 on architecture-why and 1 on cross-file-impact, and the point of
    the exercise is to compare arms WITHIN each slice — a slice with one
    question compares nothing. The cost is that the pooled mean over this draw
    is not an estimate of the arms' mean over all 48, and any pooled figure from
    it must say so.

    A slice smaller than `per_shape` contributes all of it, and `history-why`
    contributes nothing because it is empty.
    """
    rng = random.Random(seed)
    picked: list[str] = []
    for shape, ids in sorted(by_shape().items()):
        if not ids:
            continue
        picked.extend(sorted(rng.sample(ids, min(per_shape, len(ids)))))
    return sorted(picked)


if __name__ == "__main__":
    groups = by_shape()
    print("shape distribution over the 48 django SWE-QA questions:")
    for shape in SHAPES:
        ids = groups[shape]
        print(f"  {shape:<18} {len(ids):>2}  {' '.join(i.replace('django_','') for i in ids)}")
    draw = stratified()
    print(f"\nstratified draw (seed {STRATIFIED_SEED}, 3 per non-empty slice), "
          f"n={len(draw)}:")
    doc = load()
    for qid in draw:
        print(f"  {qid}  {doc['questions'][qid]['shape']}")
