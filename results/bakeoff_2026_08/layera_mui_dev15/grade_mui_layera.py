"""Grade Layer A on the mui dev 15, against the indexes ALREADY ON DISK.

Why this file exists at all, rather than `rung8_runner.py --instances ...`:

`rung8_runner.py` stages its own trees. `stage()` calls `source_tree(inst)`,
which reads `c8.REPO_TREE[inst["repo"]]` — a two-entry dict of django and cli —
and then `git worktree add` into `bakeoff/r8-<arm>-<shortid>`. Pointed at mui it
raises `KeyError: 'mui/material'` before it does anything, and if that dict were
merely extended it would create fifteen more empty worktrees and rebuild every
index into them. The mui indexes are 7.8 machine-hours already on disk, under a
different naming scheme, written by a different builder
(`scripts/prebuild_mui_indexes.py` via `harness/arms.py:arm_tree`, which names
trees `lb-<owner>-<parentdir>-<name>`).

So this adapter keeps everything downstream of the tree — `c8.arm_spec`,
`c8.query_arm`, the r5 extractors, `c8.to_pred`'s mandatory `traj_data` wrapper,
ContextBench's own grader — and replaces only the part that decides which
directory an arm is pointed at. Nothing here builds an index; `--rebuild` does
not exist on purpose, and a tree failing the gate is refused rather than
repaired.

THE GATE THIS RUNS BEFORE IT SCORES ANYTHING (pre-registration section 8, plus
one item that is not in it):

1. Every tree is at its own pinned `base_commit`. A stale checkout is a wrong
   answer, not a fast one.
2. Every arm's index marker AND the prebuild stamp are present. The stamp is
   written only after a build exits 0; the marker alone is present minutes into
   a build that was later killed.
3. `--prove-extractor` captures one real response per arm, verbatim, next to
   what the extractor pulled out of it. Graphify scored 0.012 MRR against a true
   0.539 because a regex wanted whitespace before a path, and an extractor bug
   and a genuinely bad arm produce identical summary rows. Nothing is graded
   until a human has read one response per arm.
4. Standing rule 9 / finding E5: a known-perfect and a known-wrong prediction are
   graded first, and grading aborts if the instrument cannot tell them apart.
5. NOT IN THE PRE-REGISTRATION, added 2026-08-07: non-code gold files. 8 of the
   dev 15's 38 gold files are `.md` or `.json`. An arm that indexes only code
   cannot retrieve them and posts a miss that reads as retrieval failure and is
   actually a file-type exclusion. `--prove-nonc0de` reads each arm's own index
   off disk and counts the `.md` / `.json` entries in it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNG8 = HERE.parent / "rung8"

_spec = importlib.util.spec_from_file_location("canary8", RUNG8 / "canary_allarms.py")
c8 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c8)
r5 = c8.r5

BENCH_ROOT = c8.BENCH_ROOT
TREES = c8.TREES
TASKS = BENCH_ROOT / "data" / "cb_mui" / "swe_qa" / "tasks.json"
CACHE = TREES / "_cbcache"

# The scored arms. Serena is absent by the same rule as rung 8 (DECISIONS.md,
# "Serena's rung 5 number"): a single-shot agent-free probe is the wrong
# instrument for an LSP wrapper, and the number would measure our harness. Its
# index was still built, so it can be named explicitly with --arms.
DEFAULT_ARMS = ["repowise", "repowise-search", "codegraph", "crg", "graphify"]

# c8 arm name -> the directory name `harness/arms.py:arm_tree` used, which is the
# BENCHMARK arm name and is not always the same string. `repowise-layera` rather
# than `repowise` is the whole point of commit 01ed645: the plain `repowise` arm
# resolves through `shares_index_with` to the full-prose Layer B index, and the
# smoke measured that mistake at 66 LLM calls and $0.1671 before catching it.
TREE_OWNER = {
    "repowise": "repowise-layera",
    "repowise-search": "repowise-layera",
    "codegraph": "codegraph",
    "crg": "code-review-graph",
    "graphify": "graphify",
    "serena": "serena",
}

INDEX_MARKERS = {
    "repowise-layera": (".repowise/wiki.db", ".repowise/lancedb"),
    "codegraph": (".codegraph",),
    "code-review-graph": (".code-review-graph",),
    "graphify": ("graphify-out/graph.json",),
    "serena": (),
}


def load_tasks(limit: int, only: list[str] | None) -> list[dict]:
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))
    if only:
        want = set(only)
        tasks = [t for t in tasks if t["id"] in want or t["instance_id"] in want]
    if limit:
        tasks = tasks[:limit]
    for t in tasks:
        t["gold_files"] = sorted(set(t["gold_files"]))
    return tasks


def short_id(task: dict) -> str:
    return task["id"].split("_", 1)[1]


def tree_for(arm: str, task: dict) -> Path:
    return TREES / f"lb-{TREE_OWNER[arm]}-cbmui-{short_id(task)}-material-ui"


# --------------------------------------------------------------------------
# gate 1 + 2: the tree is at its pinned commit and carries a FINISHED index
# --------------------------------------------------------------------------
def gate_tree(arm: str, task: dict) -> dict:
    owner = TREE_OWNER[arm]
    tree = tree_for(arm, task)
    row = {"arm": arm, "task": task["id"], "tree": str(tree), "owner": owner}
    if not tree.exists():
        return {**row, "ok": False, "why": "tree-absent"}
    head = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    row["head"] = head
    row["base_commit"] = task["base_commit"]
    if not head.startswith(task["base_commit"][:12]):
        return {**row, "ok": False, "why": "tree-not-at-base-commit"}
    missing = [m for m in INDEX_MARKERS[owner] if not (tree / m).exists()]
    if missing:
        return {**row, "ok": False, "why": f"index-marker-missing: {missing}"}
    stamp = tree / f".bench_prebuild__{owner}.json"
    row["stamp"] = stamp.exists()
    if not stamp.exists():
        return {**row, "ok": False, "why": "no-prebuild-stamp (build may be partial)"}
    st = json.loads(stamp.read_text(encoding="utf-8"))
    row["build_seconds"] = st.get("wall_seconds")
    if owner == "repowise-layera":
        row["index_vector_dim"] = st.get("index_vector_dim")
        # Finding D13, on the build side. A mock-embedded index answers on
        # full-text plus symbols alone and reports itself healthy.
        if st.get("index_embedder_mock") or (st.get("index_vector_dim") or 0) <= 16:
            return {**row, "ok": False, "why": "index is mock-embedded (finding D13)"}
    return {**row, "ok": True}


# --------------------------------------------------------------------------
# gate 5: does this arm's index contain non-code files at all
# --------------------------------------------------------------------------
# Where each arm's RETRIEVAL SURFACE lives, named per arm rather than sniffed.
#
# A generic "any column called path" walker was the first version of this and it
# is wrong in the direction that matters: codegraph's `nodes.name` holds import
# specifiers, so `.json` appears 196 times there while `files.path` — the set of
# files it actually indexed — contains no `.json` at all. A sniffer would have
# reported codegraph as indexing JSON and closed this gate with the opposite of
# the truth. Each entry below is the table a query can actually rank from.
#
# repowise gets TWO surfaces on purpose. `health_file_metrics` proves the walker
# saw the file; `wiki_pages` is what `search_codebase` and `get_answer` can
# return. A file in the first and not the second is one we looked at and cannot
# retrieve, which is exactly the failure this gate was added to separate from a
# ranking miss.
NONCODE_SURFACE = {
    "repowise-layera": [
        ("wiki_pages.target_path", "SELECT DISTINCT target_path FROM wiki_pages", True),
        ("wiki_symbols.file_path", "SELECT DISTINCT file_path FROM wiki_symbols", False),
        ("health_file_metrics.file_path",
         "SELECT DISTINCT file_path FROM health_file_metrics", False),
    ],
    "codegraph": [("files.path", "SELECT DISTINCT path FROM files", True)],
    "code-review-graph": [
        ("nodes.file_path", "SELECT DISTINCT file_path FROM nodes", True)],
}
NONCODE_DB = {
    "repowise-layera": ".repowise/wiki.db",
    "codegraph": ".codegraph/codegraph.db",
    "code-review-graph": ".code-review-graph/graph.db",
}


def nonc0de_proof(arm: str, task: dict) -> dict:
    """Count `.md` / `.json` entries in the arm's OWN index, off disk.

    Read from the index rather than inferred from a query, because a query that
    returns no markdown is exactly the ambiguity this gate exists to remove: an
    arm that cannot RANK markdown and an arm that never INDEXED it produce the
    same miss, and 8 of the dev 15's 38 gold files are `.md` or `.json`. An arm
    whose index shape cannot be read here reports `readable: false` rather than
    zero — an unreadable index is not an empty one, and reporting it as empty
    would be the graphify-0.012 mistake wearing a different hat.
    """
    owner = TREE_OWNER[arm]
    tree = tree_for(arm, task)
    out = {"arm": arm, "task": task["id"], "index": owner}

    def tally(paths) -> dict:
        p = [str(x) for x in paths if x]
        return {
            "total_files": len(p),
            "md": sum(1 for x in p if x.lower().endswith((".md", ".mdx"))),
            "json": sum(1 for x in p if x.lower().endswith(".json")),
        }

    try:
        if owner == "graphify":
            # Graphify's nodes carry `source_file`, not `path`/`file`/`src`.
            g = json.loads((tree / "graphify-out" / "graph.json")
                           .read_text(encoding="utf-8", errors="replace"))
            files = {n.get("source_file") for n in g.get("nodes", [])
                     if isinstance(n, dict) and n.get("source_file")}
            return {**out, "readable": True, "surface": "nodes[].source_file",
                    **tally(files)}

        db = tree / NONCODE_DB[owner]
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            surfaces, primary = {}, None
            for label, sql, is_primary in NONCODE_SURFACE[owner]:
                try:
                    vals = [r[0] for r in con.execute(sql) if r[0]]
                except sqlite3.Error as e:
                    surfaces[label] = {"error": str(e)}
                    continue
                surfaces[label] = tally(vals)
                if is_primary:
                    primary = label
            return {**out, "readable": True, "surface": primary,
                    "surfaces": surfaces, **surfaces.get(primary, {})}
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return {**out, "readable": False, "why": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------
def capture_spec(arm: str, tree: Path, sink: Path) -> tuple[dict, dict]:
    """`c8.arm_spec`, with the extractor wrapped so the RAW response is kept.

    `query_arm` records `n_ranked` and the first 50 paths and throws the response
    away. That is exactly the data the graphify near-miss needed: the summary row
    for a broken extractor and for a tool that found nothing are the same row.
    Wrapping `extract` is the least invasive place to capture it — the extractor
    is the only function handed both the parsed payload and the raw text.

    The returned `holder` also carries `answer_degraded`, straight off the
    response's own top-level `degraded` field. `query_arm` reads `_meta`'s
    `embedder_degraded` and `retrieval_degraded` and not this one, and on the
    first mui pass those two were BOTH `None` while `degraded` read
    `"no-llm-provider"` — a live server, a healthy-looking row, and a query the
    product itself had already flagged as answered without half its stack.
    """
    spec = c8.arm_spec(arm, tree)
    inner = spec["extract"]
    holder: dict = {}

    def wrapped(payload, text):
        ranked = inner(payload, text)
        if isinstance(payload, dict):
            holder["answer_degraded"] = payload.get("degraded")
            holder["confidence"] = payload.get("confidence")
        sink.parent.mkdir(parents=True, exist_ok=True)
        sink.write_text(json.dumps({
            "arm": arm, "tree": str(tree),
            "raw_chars": len(text or ""),
            "raw_text": (text or "")[:20000],
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
            **holder,
            "extracted": ranked[:50],
            "n_extracted": len(ranked),
        }, indent=2), encoding="utf-8")
        return ranked

    spec["extract"] = wrapped
    return spec, holder


def cell_path(tag: str, arm: str, task: dict) -> Path:
    return HERE / "cells" / tag / f"{arm}__{task['id']}.json"


def run_cell(arm: str, task: dict, args) -> dict:
    p = cell_path(args.tag, arm, task)
    if p.exists() and not args.requery:
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
            if prev.get("query", {}).get("status") == "ok":
                return prev
        except (json.JSONDecodeError, OSError):
            pass

    gate = gate_tree(arm, task)
    if not gate["ok"]:
        row = {"arm": arm, "task": task["id"], "instance_id": task["instance_id"],
               "gate": gate,
               "query": {"arm": arm, "instance_id": task["instance_id"],
                         "status": "gate-refused", "error": gate["why"]}}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        print(f"[gate ] {arm:16s} {task['id']} REFUSED {gate['why']}", flush=True)
        return row

    tree = Path(gate["tree"])
    inst = {
        "instance_id": task["instance_id"], "repo": task["upstream_repo"],
        "problem_statement": task["problem_statement"],
        "gold_files": task["gold_files"],
    }
    sink = HERE / "responses" / args.tag / f"{arm}__{task['id']}.json"
    spec, holder = capture_spec(arm, tree, sink)

    async def _guarded():
        return await asyncio.wait_for(
            c8.query_arm(arm, spec, inst, timeout=args.timeout,
                         warm_timeout=args.warm_timeout),
            timeout=args.unit_timeout,
        )

    t0 = time.time()
    try:
        q = asyncio.run(_guarded())
    except (TimeoutError, asyncio.TimeoutError):
        q = {"arm": arm, "instance_id": task["instance_id"], "tree": str(tree),
             "status": "cell-timeout",
             "error": f"exceeded --unit-timeout {args.unit_timeout}s"}
    q.update(holder)
    q["gold_files"] = task["gold_files"]
    q["gold_hit_rank"] = next(
        (i for i, path in enumerate(q.get("ranked", []), 1)
         if any(r5.path_matches(path, g) for g in task["gold_files"])),
        None,
    )
    row = {
        "arm": arm, "task": task["id"], "instance_id": task["instance_id"],
        "base_commit": task["base_commit"], "gate": gate,
        "cell_seconds": round(time.time() - t0, 1),
        "response_capture": str(sink) if sink.exists() else None,
        "query": q,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
    print(f"[query] {arm:16s} {task['id']} status={q.get('status')} "
          f"served={q.get('served_count')} chars={q.get('chars')} "
          f"n={q.get('n_ranked')} gold_rank={q.get('gold_hit_rank')} "
          f"{row['cell_seconds']}s", flush=True)
    return row


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------
PREWARM_SRC = '''"""Serially materialise every checkout the grader needs. See rung8_runner."""
import json, os, sys
sys.path.insert(0, os.getcwd())
from contextbench.core import checkout

rows = json.load(open(sys.argv[1], encoding="utf-8"))
cache = sys.argv[2]
for r in rows:
    d = checkout(r["repo_url"], r["base_commit"], cache, verbose=False)
    print(("ok   " if d else "FAIL ") + r["instance_id"], flush=True)
'''

MUI_URL = "https://github.com/mui/material-ui.git"


def ensure_longpaths() -> dict:
    """`core.longpaths=true` on the grader's base clone, and it is not cosmetic.

    THIS IS THE ONE THAT WOULD HAVE PUBLISHED FIFTEEN ZEROS FOR EVERY ARM.

    ContextBench grades against its own checkout of the instance, materialised
    as a git worktree under `CONTEXTBENCH_TMP_ROOT`. mui carries

        packages/material-ui-icons/test/fixtures/game-icons/svg/icons/
        delapouite/dice/svg/000000/transparent/
        perspective-dice-six-faces-random.svg

    which, under a worktree root that already spends ~100 characters on
    `<root>/contextbench_worktrees/github.com__mui__material-ui/<40-char sha>/`,
    crosses Windows' 260-character MAX_PATH. git reports `Filename too long`
    twice, then `fatal: Could not reset index file to revision 'HEAD'`, removes
    the worktree, and `checkout()` returns None.

    What that looks like downstream is the entire point: `evaluate.py` scores the
    instance `checkout_failed`, every arm loses it, and the summary line reads
    `0/1 instances`. Not an error, not a crash — a clean zero for all five arms
    at once, on a repo where all five had in fact returned the gold file. It was
    caught only because standing rule 9's self-test grades a KNOWN-PERFECT
    prediction and that prediction scored 0.

    `core.longpaths` makes git use the `\\\\?\\` prefixed API itself, so it works
    without the machine-wide `LongPathsEnabled` registry key (measured 0 on this
    machine, and the fix works anyway). Set on the base clone, which worktrees
    inherit.
    """
    base = CACHE / "github.com__mui__material-ui"
    if not (base / ".git").exists():
        return {"base": str(base), "present": False}
    subprocess.run(["git", "-C", str(base), "config", "core.longpaths", "true"],
                   capture_output=True, text=True)
    got = subprocess.run(["git", "-C", str(base), "config", "--get", "core.longpaths"],
                         capture_output=True, text=True).stdout.strip()
    return {"base": str(base), "present": True, "core.longpaths": got}


def prewarm(tasks: list[dict], tag: str) -> dict:
    """Materialise the grader's checkouts serially, before parallel grading.

    Finding E4's shape again: five graders fetching into one shared base clone
    race on a lock file whose implementation does not hold on Windows, and the
    instance every arm predicted correctly comes back `checkout_failed` for all
    five of them.
    """
    ensure_longpaths()
    payload = HERE / f"_prewarm__{tag}.json"
    payload.write_text(json.dumps([
        {"instance_id": t["instance_id"], "repo_url": MUI_URL,
         "base_commit": t["base_commit"]} for t in tasks
    ]), encoding="utf-8")
    script = HERE / "_prewarm_checkouts.py"
    script.write_text(PREWARM_SRC, encoding="utf-8")
    p = subprocess.run(
        [str(c8.GRADER_PY), str(script), str(payload), str(CACHE)],
        cwd=str(c8.GRADER), capture_output=True, text=True,
        errors="replace", timeout=6 * 60 * 60,
    )
    (HERE / "logs").mkdir(exist_ok=True)
    (HERE / "logs" / f"prewarm__{tag}.log").write_text(
        f"--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n", encoding="utf-8")
    failed = [l.split(" ", 1)[-1].strip()
              for l in p.stdout.splitlines() if l.startswith("FAIL")]
    return {"rc": p.returncode, "n": len(tasks), "failed": failed}


def parse_eval(stderr: str) -> dict:
    for line in stderr.splitlines():
        s = line.strip()
        if s.startswith("EVALUATION:"):
            return {"evaluation": s.split("EVALUATION:", 1)[1].strip()}
    return {}


def selftest(task: dict, tag: str) -> dict:
    """Standing rule 9 / finding E5, before any real prediction is graded.

    A prediction naming exactly the gold file scores `no_context_extracted` if
    the `traj_data` wrapper is missing, and at n=75 that is a wall of clean zero
    that reads as every tool failing.
    """
    cases = {"perfect": task["gold_files"], "wrong": ["LICENSE", "README.md"]}
    res: dict = {"instance_id": task["instance_id"], "gold": task["gold_files"]}
    for name, files in cases.items():
        pred = HERE / f"pred__{tag}__SELFTEST_{name}.jsonl"
        pred.write_text(json.dumps(c8.to_pred(
            {"instance_id": task["instance_id"], "ranked": files})) + "\n",
            encoding="utf-8")
        graded = HERE / f"graded__{tag}__SELFTEST_{name}.jsonl"
        r = c8.grade(pred, graded, CACHE)
        cov = None
        if graded.exists():
            rows = [json.loads(x) for x in
                    graded.read_text(encoding="utf-8").splitlines() if x.strip()]
            if rows:
                cov = rows[0].get("final", {}).get("file", {}).get("coverage")
        res[name] = {"rc": r["rc"], "file_coverage": cov, **parse_eval(r["stderr"])}
    res["discriminates"] = (res["perfect"]["file_coverage"] == 1.0
                            and res["wrong"]["file_coverage"] == 0.0)
    return res


def grade_arm(arm: str, tag: str, tasks: list[dict]) -> dict:
    rows = []
    for t in tasks:
        p = cell_path(tag, arm, t)
        if not p.exists():
            continue
        c = json.loads(p.read_text(encoding="utf-8"))
        if c.get("query", {}).get("status") == "ok":
            rows.append(c["query"])
    if not rows:
        return {"skipped": "no successful calls to grade", "n": 0}
    pred = HERE / f"pred__{tag}__{arm}.jsonl"
    pred.write_text("\n".join(json.dumps(c8.to_pred(r)) for r in rows) + "\n",
                    encoding="utf-8")
    g = c8.grade(pred, HERE / f"graded__{tag}__{arm}.jsonl", CACHE)
    return {"n": len(rows), "rc": g["rc"], **parse_eval(g["stderr"])}


def summarize(arm: str, tag: str) -> dict:
    """Pooled mean, mean-of-per-instance, median, and the top instance's share.

    A pooled percentage alone at n=15 is not reportable on this run: gold counts
    are 7,5,5,4,3,3,2,2 then seven 1s, so a pooled figure is carried by a few
    instances. Where the pooled value and the mean of per-instance ratios
    disagree in SIGN the number is an artifact and RESULT.md says so.
    """
    p = HERE / f"graded__{tag}__{arm}.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    valid = [r for r in rows if "file" in r.get("final", {})]
    if not valid:
        return {"graded_rows": len(rows), "valid": 0}

    def med(v):
        v = sorted(v)
        n = len(v)
        return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)

    cov = [r["final"]["file"]["coverage"] for r in valid]
    prec = [r["final"]["file"]["precision"] for r in valid]
    served = [r["final"]["file"]["pred_size"] for r in valid]
    return {
        "graded_rows": len(rows), "valid": len(valid),
        "file_coverage_mean": round(sum(cov) / len(cov), 4),
        "file_coverage_median": round(med(cov), 4),
        "file_precision_mean": round(sum(prec) / len(prec), 4),
        "file_precision_median": round(med(prec), 4),
        "mean_pred_files": round(sum(served) / len(served), 2),
        "median_pred_files": round(med(served), 2),
        "per_instance": {r["instance_id"]: round(r["final"]["file"]["coverage"], 4)
                         for r in valid},
    }


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS)
    ap.add_argument("--instances", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1,
                    help="1 by default: this run publishes no timings, but five "
                         "MCP servers over a 28k-file tree is a lot of RAM")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--warm-timeout", type=float, default=30.0)
    ap.add_argument("--unit-timeout", type=float, default=900.0)
    ap.add_argument("--requery", action="store_true")
    ap.add_argument("--gate-only", action="store_true",
                    help="run the tree/index/non-code gates and stop")
    ap.add_argument("--grade-only", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("CONTEXTBENCH_TMP_ROOT", str(TREES / "_cbwt"))

    # THE EMBEDDER PRECONDITION, ASSERTED POSITIVELY AND BEFORE ANYTHING RUNS.
    #
    # The indexes were built `--embedder openai` and carry 1536-dimension
    # vectors. If the SERVER cannot resolve that same embedder it builds an
    # 8-dimension question vector, every vector search raises
    # `No vector column found to match with the query vector dimension`,
    # `_safe_vector_search` swallows it and returns [], and the arm answers on
    # full-text plus symbols alone (finding D13 / A9).
    #
    # None of that is visible in the row. Measured on the first mui pass:
    # `embedder`, `embedder_degraded` and `retrieval_degraded` were ALL `None`
    # — `_embedder_meta` emits nothing when the embedder is healthy OR
    # unresolved, and the unresolved branch is the dangerous one. The only
    # evidence was the response's own `degraded: "no-llm-provider"`, which
    # appears because the LLM provider and the embedder are autodetected from
    # the same key, so its absence proves the embedder was absent too.
    #
    # So the check is a precondition, not a warning: refuse rather than publish
    # a repowise number measured with two thirds of its retrieval stack. The
    # key is read from the environment ONLY, deliberately — `_openai_key`'s
    # fallback to `REPOWISE_ROOT/provider_config.json` returned None here
    # because the layerb2 checkout has no such file, and a silent fallback that
    # can miss is what produced the state this guard exists to catch.
    if any(a in ("repowise", "repowise-search") for a in args.arms):
        if not os.environ.get("OPENAI_API_KEY"):
            print(
                "REFUSING: OPENAI_API_KEY is not in this process's environment.\n"
                "  The mui indexes carry 1536-dimension vectors. Without the key the\n"
                "  MCP server resolves MockEmbedder, the vector leg raises on every\n"
                "  query and is swallowed, and the arm answers on full-text plus\n"
                "  symbols alone while reporting itself healthy (finding D13).\n"
                "  Set it from C:/Users/ragha/Desktop/repowise/.repowise/.env and\n"
                "  re-run. Pass --arms without the repowise arms to grade the\n"
                "  competitors alone.", flush=True)
            return 5

    tasks = load_tasks(args.limit, args.instances)
    print(f"{len(tasks)} instances x {len(args.arms)} arms = "
          f"{len(tasks) * len(args.arms)} cells", flush=True)

    report: dict = {
        "tag": args.tag, "arms": args.arms, "n_instances": len(tasks),
        "instance_ids": [t["instance_id"] for t in tasks],
        "workers": args.workers,
        # This run grades retrieval. Every build second it quotes came from
        # prebuild.json, measured serially on a quiet machine; nothing here is
        # a timing measurement and the cell seconds are contended by design.
        "timings_publishable": False,
    }

    gates = [gate_tree(a, t) for t in tasks for a in args.arms]
    report["gate_trees"] = gates
    bad = [g for g in gates if not g["ok"]]
    print(f"[gate ] {len(gates) - len(bad)}/{len(gates)} trees pass "
          f"(commit + index marker + prebuild stamp)", flush=True)
    for g in bad:
        print(f"        REFUSED {g['arm']:16s} {g['task']}: {g['why']}", flush=True)

    # Every instance, not just one. The dev 15 span a 12x size range and mui's
    # markdown-to-code ratio is not constant across five years of it, so one
    # instance cannot answer "does this arm index markdown" for the set. Arms
    # sharing a tree are read once (repowise-search rides on repowise's index).
    seen_owner = set()
    nonc0de = []
    for a in args.arms:
        if TREE_OWNER[a] in seen_owner:
            continue
        seen_owner.add(TREE_OWNER[a])
        for t in tasks:
            nonc0de.append(nonc0de_proof(a, t))
    report["noncode_index_proof"] = nonc0de
    for a in sorted({n["arm"] for n in nonc0de}):
        rows = [n for n in nonc0de if n["arm"] == a]
        rd = [n for n in rows if n.get("readable")]
        print(f"[nonc0de] {a:16s} readable={len(rd)}/{len(rows)} "
              f"surface={rows[0].get('surface')} "
              f"md={sum(n.get('md') or 0 for n in rd)} "
              f"json={sum(n.get('json') or 0 for n in rd)} "
              f"files={sum(n.get('total_files') or 0 for n in rd)} "
              f"instances_with_md={sum(1 for n in rd if n.get('md'))}", flush=True)

    if args.gate_only:
        (HERE / f"grade_report__{args.tag}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        return 0 if not bad else 1

    t0 = time.time()
    if not args.grade_only:
        units = [(a, t) for t in tasks for a in args.arms]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(run_cell, a, t, args) for a, t in units]
            for n, f in enumerate(as_completed(futs), 1):
                f.result()
                if n % 10 == 0 or n == len(futs):
                    print(f"--- {n}/{len(futs)} cells, {round(time.time() - t0)}s",
                          flush=True)
    report["query_seconds"] = round(time.time() - t0, 1)

    # Proof of life per arm, before any number is looked at (finding E4).
    alive = {}
    for arm in args.arms:
        cells = []
        for t in tasks:
            p = cell_path(args.tag, arm, t)
            if p.exists():
                cells.append(json.loads(p.read_text(encoding="utf-8")))
        ok = [c for c in cells if c.get("query", {}).get("status") == "ok"]
        row = {
            "cells": len(cells), "ok": len(ok),
            "statuses": sorted({c.get("query", {}).get("status") for c in cells}),
            "empty_responses": sum(1 for c in ok if not c["query"].get("n_ranked")),
            "is_error": sum(1 for c in ok if c["query"].get("isError")),
            "mean_chars": (round(sum(c["query"].get("chars") or 0 for c in ok) / len(ok))
                           if ok else None),
        }
        if arm in ("repowise", "repowise-search"):
            row["embedder_degraded"] = sum(
                1 for c in ok if c["query"].get("embedder_degraded"))
            row["retrieval_degraded"] = sum(
                1 for c in ok if c["query"].get("retrieval_degraded"))
            # The one that was actually populated when the other two were None.
            row["answer_degraded"] = sorted(
                {c["query"].get("answer_degraded") for c in ok})
        alive[arm] = row
        print(f"[alive] {arm:16s} {row}", flush=True)
    report["proof_of_life"] = alive

    report["longpaths"] = ensure_longpaths()
    print(f"[longpaths] {report['longpaths']}", flush=True)
    pw = prewarm(tasks, args.tag)
    report["prewarm"] = pw
    print(f"[prewarm] rc={pw['rc']} {pw['n']} checkouts, failed={pw['failed']}",
          flush=True)
    grade_workers = 1 if (pw["rc"] != 0 or pw["failed"]) else min(len(args.arms), 5)

    st = selftest(tasks[0], args.tag)
    report["selftest"] = st
    print(f"[selftest] {st}", flush=True)
    if not st["discriminates"]:
        print("ABORTING GRADING: the grader does not discriminate a perfect "
              "prediction from a wrong one (finding E5). Cells are on disk; "
              "re-run with --grade-only once fixed.", flush=True)
        (HERE / f"grade_report__{args.tag}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        return 3

    graded = {}
    with ThreadPoolExecutor(max_workers=grade_workers) as ex:
        futs = {ex.submit(grade_arm, a, args.tag, tasks): a for a in args.arms}
        for f in as_completed(futs):
            arm = futs[f]
            graded[arm] = f.result()
            print(f"[grade] {arm} {graded[arm]}", flush=True)
    for arm in args.arms:
        graded[arm].update(summarize(arm, args.tag))
    report["graded"] = graded

    (HERE / f"grade_report__{args.tag}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {HERE / f'grade_report__{args.tag}.json'}")
    for arm in args.arms:
        g = graded.get(arm, {})
        print(f"  {arm:16s} n={g.get('n')} cov_mean={g.get('file_coverage_mean')} "
              f"cov_med={g.get('file_coverage_median')} "
              f"prec_mean={g.get('file_precision_mean')} "
              f"served={g.get('mean_pred_files')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
