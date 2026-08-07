"""The cocoindex arm's gate, run before it is allowed to score anything.

Three proofs, and each one exists because its absence produces a number that
looks fine and is not (pre-registration `configs/layera_cocoindex.PREREGISTRATION.md`,
sections 4 and 5):

1. THE TREE BINDING, PROVEN POSITIVELY. `ccc mcp` takes no project argument —
   `cli.py:mcp` resolves the repository by walking UP from the working directory
   — so a server launched without `cwd` answers about whatever repo sits above
   the harness, while every health field reads clean. That is finding A9's
   shape, and A9 cost this workstream a rung. Asserting "we passed cwd" is not
   the check. The check is asking TWO DIFFERENT INSTANCE TREES the same question
   and requiring the answers to differ: a server pinned to one repo returns
   identical bytes for both, and nothing else catches that.

2. THE EXTRACTOR, AGAINST A REAL RESPONSE. graphify scored 0.012 MRR against a
   true 0.539 because a regex wanted whitespace before a path. cocoindex returns
   ranked code CHUNKS and no extractor in this tree knew its shape until now, so
   one response is written out verbatim beside what the extractor pulled from
   it, for a human to read (finding E5). It is also run against a question whose
   gold file is known, so a zero here is separable from a zero there.

3. THE NON-CODE SURFACE. 8 of the dev 15's 38 gold files are `.md` or `.json`,
   and on the graded run every arm scored 0 of 8 — for three of them because the
   files are not in the retrieval surface at all, which is a FILE-TYPE EXCLUSION
   rather than a retrieval failure. cocoindex's own surface is read off disk
   here before its number stands, from the table `query_codebase` ranks against
   and not from any column that happens to be called path.

Run:
    python results/bakeoff_2026_08/layera_mui_dev15/prove_cocoindex.py \
        --trees cbmui_2bb4ea7a cbmui_8fcb53e6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import grade_mui_layera as G  # noqa: E402

c8 = G.c8


def ask(tree: Path, question: str, sink: Path) -> dict:
    """One call, with the RAW response written verbatim beside what came out.

    `capture_spec` rather than `arm_spec`, because the whole point of this file
    is that a human reads an actual response before any cell is graded. Without
    it `query_arm` records `n_ranked` and throws the bytes away, which is the
    state that let graphify score 0.012 MRR against a true 0.539.
    """
    spec, _holder = G.capture_spec("cocoindex", tree, sink)
    inst = {"instance_id": f"probe__{tree.name}", "repo": "mui/material-ui",
            "problem_statement": question, "gold_files": []}
    return asyncio.run(c8.query_arm("cocoindex", spec, inst, timeout=300.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trees", nargs=2, required=True,
                    help="two task ids whose trees are built")
    ap.add_argument("--question", default=(
        "How does the Badge component render its badge content and apply "
        "invisible styling?"))
    ap.add_argument("--out", default=str(HERE / "gate__cocoindex.json"))
    args = ap.parse_args()

    tasks = {t["id"]: t for t in G.load_tasks(0, None)}
    report: dict = {"question": args.question, "trees": args.trees}

    # --- gate 1+2 first: a tree off its commit or without a finished build is
    # refused here, not diagnosed later from a bad number.
    report["gate_trees"] = [G.gate_tree("cocoindex", tasks[t]) for t in args.trees]
    for g in report["gate_trees"]:
        print(f"[gate ] {g['task']} ok={g['ok']} {g.get('why', '')}")
    if not all(g["ok"] for g in report["gate_trees"]):
        Path(args.out).write_text(json.dumps(report, indent=2, default=str),
                                  encoding="utf-8")
        return 1

    # --- 1. the tree binding
    #
    # ONE question, asked of BOTH trees, and that is the design rather than a
    # shortcut: the failure being tested for is a server that resolves a single
    # project regardless of which tree it was pointed at, and only an identical
    # question can show identical answers. It is also why this pass cannot
    # double as the known-hit control below — the shared question is not either
    # instance's own, so a miss here means nothing.
    answers = []
    for tid in args.trees:
        tree = G.tree_for("cocoindex", tasks[tid])
        row = ask(tree, args.question,
                  HERE / "responses" / "gate-cocoindex" / f"raw__bind__{tid}.json")
        answers.append(row)
        print(f"[ask  ] {tid} status={row.get('status')} cwd={row.get('cwd')} "
              f"served={row.get('served_tools')} chars={row.get('chars')} "
              f"n={row.get('n_ranked')} args={row.get('tool_args')}")
    a, b = answers
    same_bytes = (a.get("ranked") == b.get("ranked")
                  and a.get("chars") == b.get("chars"))
    report["binding"] = {
        "answers": answers,
        "identical": same_bytes,
        # DIFFERENT ANSWERS ARE THE PASS. Identical ranked lists AND identical
        # response lengths from two different repositories is what a single
        # mis-resolved project looks like.
        "verdict": "FAIL: both trees answered identically, the server is "
                   "resolving one project" if same_bytes else
                   "pass: the two trees answered differently",
    }
    print(f"[bind ] {report['binding']['verdict']}")

    # --- 2. the extractor, on each instance's OWN question (finding E5)
    #
    # The known-hit half of standing rule 9. A path extractor that returns a
    # plausible-looking list can still be wrong in a way no summary row shows,
    # so it is run against the question whose gold file is known and the rank of
    # that gold file is recorded. A `null` here is a real miss by the arm; a
    # `null` in the binding pass above is an artifact of the shared question.
    caps = []
    for tid in args.trees:
        tree = G.tree_for("cocoindex", tasks[tid])
        sink = HERE / "responses" / "gate-cocoindex" / f"raw__own__{tid}.json"
        row = ask(tree, tasks[tid]["problem_statement"], sink)
        print(f"[own  ] {tid} status={row.get('status')} n={row.get('n_ranked')} "
              f"chars={row.get('chars')}")
        sink = HERE / "responses" / "gate-cocoindex" / f"cocoindex__{tid}.json"
        sink.parent.mkdir(parents=True, exist_ok=True)
        sink.write_text(json.dumps({
            "task": tid, "tree": row.get("tree"), "cwd": row.get("cwd"),
            "tool": row.get("tool"), "tool_args": row.get("tool_args"),
            "isError": row.get("isError"), "chars": row.get("chars"),
            "extracted": row.get("ranked"), "n_extracted": row.get("n_ranked"),
            "gold_files": tasks[tid]["gold_files"],
            "gold_hit_rank": next(
                (i for i, p in enumerate(row.get("ranked") or [], 1)
                 if any(G.r5.path_matches(p, g)
                        for g in tasks[tid]["gold_files"])), None),
        }, indent=2), encoding="utf-8")
        caps.append(str(sink))
        print(f"[cap  ] {tid} -> {sink}")
    report["captures"] = caps

    # --- 3. the non-code surface, off each index
    surf = [G.nonc0de_proof("cocoindex", tasks[t]) for t in args.trees]
    report["noncode_index_proof"] = surf
    for s in surf:
        print(f"[nonc0de] {s['task']} readable={s.get('readable')} "
              f"surface={s.get('surface')} files={s.get('total_files')} "
              f"md={s.get('md')} json={s.get('json')} chunks={s.get('chunks')} "
              f"aux_match={s.get('aux_rows_match_vec_rows')}")

    Path(args.out).write_text(json.dumps(report, indent=2, default=str),
                              encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0 if not same_bytes else 2


if __name__ == "__main__":
    sys.exit(main())
