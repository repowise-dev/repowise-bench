"""Give the mui corpus a QUESTION and a REFERENCE ANSWER, so Layer B can run.

WHY THIS EXISTS, AND IT IS THE ONE THING THE LAYER B BRIEF DID NOT KNOW
----------------------------------------------------------------------
`data/cb_mui/swe_qa/tasks.json` was written by `prep_mui_instances.py` for
Layer A, which grades RETRIEVAL: it needs `gold_files` and `gold_spans` and
nothing else. Its rows carry no `question` and no `answer` field at all.

Layer B is an agent loop. `swe_qa_runner` puts `task["question"]` in the prompt
and hands `task["answer"]` to the judge, so on the Layer A corpus every mui
cell would be asked a null question. That is not a small gap and it is not
fixable in a config.

This is NOT a new benchmark shape. It is the SAME transformation the Go
ContextBench corpus already ships, applied to the same source parquet:

    cb_go     question = FRAME_A % problem_statement,  answer = gold patch
    cb_go_frameB  question = FRAME_B % problem_statement,  answer = gold patch

Both frames are reproduced verbatim below from `data/cb_go*/swe_qa/tasks.json`
rather than paraphrased, because a frame rewritten by hand is a new frame.

FRAME A IS THE DEFAULT AND THE CHOICE IS PRE-REGISTERED
-------------------------------------------------------
`configs/layerb_go_frame_ab.PREREGISTRATION.md` ran A against B on the Go
held-out nine and the result was INCONCLUSIVE BY ITS OWN RULE. So there is no
measured reason to prefer B, and A is what every published ContextBench Layer B
row was produced on. Frame A it is, and `--frame b` exists only so the choice
stays checkable rather than baked in.

IT WRITES A SEPARATE CORPUS DIRECTORY, ON PURPOSE
--------------------------------------------------
Output is `data/cb_mui_layerb/swe_qa/tasks.json`, never `data/cb_mui/`.
`layera_mui_dev15.yaml`'s own header records the footgun: one shared tasks.json
at a fixed path already made the smoke config silently resolve to 15 tasks.
Layer A's graded corpus is not edited in place by a Layer B script.

THE SEALED 30 ARE NEVER TOUCHED. The instance set is read from the Layer A
tasks.json that was actually graded, and the intersection with
`configs/mui_split.json`'s sealed list is asserted empty before anything is
written.

Run:
    python scripts/prep_mui_layerb_questions.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

BENCH = Path(__file__).resolve().parents[1]

SPLIT = BENCH / "configs" / "mui_split.json"
PARQUET = BENCH / "data" / "contextbench" / "contextbench_verified.parquet"
LAYERA_TASKS = BENCH / "data" / "cb_mui" / "swe_qa" / "tasks.json"
OUT = BENCH / "data" / "cb_mui_layerb" / "swe_qa" / "tasks.json"

# Verbatim from data/cb_go/swe_qa/tasks.json. Do not retype.
FRAME_A = (
    "A change was proposed to the repository in your current directory. This "
    "is the change description as its author wrote it:\n\n---\n{ps}\n---\n\n"
    "Which files, and which functions or methods within them, does this change "
    "modify, and what exactly does it change about them? Name every file by "
    "its repository-relative path."
)

# Verbatim from data/cb_go_frameB/swe_qa/tasks.json.
FRAME_B = (
    "A change was proposed to the repository in your current directory. This "
    "is the change description as its author wrote it:\n\n---\n{ps}\n---\n\n"
    "How does this repository currently implement the behaviour this change is "
    "about, and which files and functions would have to change to make it "
    "happen? Name every file by its repository-relative path."
)

ANSWER = (
    "The change was implemented by the following diff against the "
    "repository:\n\n{patch}"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", choices=["a", "b"], default="a")
    args = ap.parse_args()
    frame = FRAME_A if args.frame == "a" else FRAME_B

    layera = json.loads(LAYERA_TASKS.read_text(encoding="utf-8"))
    sealed = set(json.loads(SPLIT.read_text(encoding="utf-8"))["sealed"])
    ids = [t["instance_id"] for t in layera]
    if set(ids) & sealed:
        print("FAIL: the Layer A corpus intersects the sealed 30")
        return 1

    df = pd.read_parquet(PARQUET).set_index("instance_id")
    out = []
    for t in layera:
        row = df.loc[t["instance_id"]]
        ps = (row["problem_statement"] or "").strip()
        patch = (row["patch"] or "").strip()
        if not ps or not patch:
            print(f"FAIL: {t['id']} has an empty problem_statement or patch")
            return 1
        out.append({**t,
                    "question": frame.format(ps=ps),
                    "answer": ANSWER.format(patch=patch),
                    "question_frame": args.frame})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"frame {args.frame}, {len(out)} tasks, sealed {len(sealed)} untouched")
    for t in out:
        print(f"  {t['id']:<16} q={len(t['question']):>6} chars  "
              f"a={len(t['answer']):>7} chars  gold={len(t['gold_files']):>3}")
    print(f"\nwrote -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
