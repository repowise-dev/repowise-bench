"""Freeze the authored context-bench question sets into their final schema.

Questions are authored with an `evidence` field (file/line/commit pointers)
that an independent verifier consumes; the frozen files the harness runs on
carry only the task schema, so no arm can be steered by authoring artifacts.
The drafts and verification verdicts move to data/context_bench/authoring/
and stay tracked: they are the audit trail that every answer was verified
against the pinned tree by an agent that did not write it.

Fails if any question's verification verdict is not "pass" — a set is only
frozen when it is fully verified.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "context_bench"
FINAL_FIELDS = ("id", "repo", "split_name", "question", "answer", "category")
CATEGORIES = {"symbol-lookup", "multi-hop-flow", "architecture-why",
              "history-why", "cross-file-impact"}


def freeze(name: str) -> None:
    draft_path = BENCH / f"questions_{name}.draft.json"
    verif_path = BENCH / f"questions_{name}.verification.json"
    questions = json.loads(draft_path.read_text(encoding="utf-8"))
    verdicts = {v["id"]: v["verdict"]
                for v in json.loads(verif_path.read_text(encoding="utf-8"))}

    frozen = []
    for q in questions:
        verdict = verdicts.get(q["id"])
        if verdict != "pass":
            raise SystemExit(f"{q['id']}: verdict {verdict!r}, refusing to freeze")
        assert q["category"] in CATEGORIES, q["id"]
        frozen.append({k: q[k] for k in FINAL_FIELDS})

    out = BENCH / f"questions_{name}.json"
    out.write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    authoring = BENCH / "authoring"
    authoring.mkdir(exist_ok=True)
    shutil.move(str(draft_path), authoring / draft_path.name)
    shutil.move(str(verif_path), authoring / verif_path.name)
    print(f"froze {out.name}: {len(frozen)} questions, all verified")


if __name__ == "__main__":
    freeze("fastify")
    freeze("why")
