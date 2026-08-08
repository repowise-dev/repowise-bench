"""Rung 8: Layer A ContextBench over the full instance set, parallel and resumable.

This is the canary grown up. `canary_allarms.py` stays exactly as it ran, because
its provenance block in `50-results/rung8-canary/RESULT.md` names it and a
renamed script makes a published command unrunnable. Everything reusable is
imported from it rather than copied: the arm launch specs, the query path with
its proof-of-life recording, the `traj_data` prediction shape, and the grader
invocation. What is added here is the three things the canary did not need at
n=2 and cannot survive without at n=112.

**1. Parallelism.** The unit of work is (instance, build-arm). Standing rule E1
forbids concurrent process pools during a *timed* build, and that rule is not
waived here so much as out of scope: rung 4 already published the build timings,
and rung 8 measures retrieval quality. So **rung 8 publishes no timings of its
own**, the build seconds it records are marked contended, and anyone who later
wants build times from this rung reruns it serially. The E1 preflight is demoted
from a refusal to a recorded warning for the same reason, and every cell records
the worker count and the live multiprocessing-worker count so a contended cell is
visible in the data instead of inferred.

**2. Resume.** 560 query cells across ~4 arms of index builds is not a job that
gets one clean shot. Resume keys on `(instance_id, arm)`: a cell whose record
exists with `status == "ok"` is skipped whole, and a staged tree whose index
marker is already present is not rebuilt. Note the deviation from "skip a cell
whose index AND graded row exist": grading is a batch step over an arm's whole
prediction file, so there is no per-cell graded row to key on. The cell record is
the resume key and grading is re-run at the end over every accumulated record,
which costs minutes and guarantees the graded output matches the cells on disk.

**3. Reaping, which is not optional and was nearly missed.** The canary stages
one worktree per (arm, repo). At 112 instances the tree must be per (arm,
instance) because every instance has its own `base_commit`. Measured sizes:
one django instance across the five arms is ~1.15 GB, so 80 django instances is
**~92 GB against 44 GB free on this machine.** Cells are therefore reaped after
their arms have been queried, which bounds peak disk at roughly workers x one
arm's tree. `--keep-trees` disables it for anyone with the disk.

Arms sharing a tree (`repowise-search` rides on `repowise`'s index) are queried
inside the same unit, before the reap.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("canary8", HERE / "canary_allarms.py")
c8 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c8)
r5 = c8.r5

OUT = HERE
BENCH_ROOT = c8.BENCH_ROOT
TREES = c8.TREES
SPLIT = BENCH_ROOT / "results" / "bakeoff_2026_08" / "dev_test_split.json"

# Serena is deliberately absent from the default. DECISIONS.md, "Serena's rung 5
# number": it is not scored, because a single-shot agent-free probe is the wrong
# instrument for an LSP wrapper and the number would measure our harness. It can
# still be named explicitly with --arms.
DEFAULT_ARMS = ["repowise", "repowise-search", "codegraph", "crg", "graphify"]

# Arms that stage and build their own tree. Everything else rides on one of
# these (see c8.SHARED_TREE) and must be queried before that tree is reaped.
#
# cocoindex added 2026-08-08 for the sealed-42 run
# (configs/layera_cocoindex_contextbench.PREREGISTRATION.md). It builds its own
# tree like the rest: `ccc index` writes `.cocoindex_code/` per worktree, and
# its server resolves the project by WORKING DIRECTORY with no path flag, so a
# shared tree would not merely bias it (finding E3), it would point it at
# whatever repository the cwd resolved to.
BUILD_ARMS = ("repowise", "codegraph", "crg", "graphify", "serena", "cocoindex")

# What "the index exists" means per arm, for the resume check. Deliberately a
# concrete file rather than "the dotdir exists": finding E3's follow-on left
# `.repowise/` present but reduced to a 492 KB stub, which a directory-existence
# check would have called done.
INDEX_MARKERS = {
    "repowise": (".repowise/wiki.db", ".repowise/lancedb"),
    "codegraph": (".codegraph",),
    "crg": (".code-review-graph",),
    "graphify": ("graphify-out/graph.json",),
    "serena": (),  # builds nothing by design
    # A concrete file, not the dotdir: the mui reset found `.cocoindex_code/`
    # surviving a removal because the daemon's live SQLite handles kept
    # `target_sqlite.db` on disk underneath it. A directory-existence check
    # would have called that build done.
    "cocoindex": (".cocoindex_code/target_sqlite.db",),
}


# --------------------------------------------------------------------------
# contention sampling (finding E1, demoted to a recording)
# --------------------------------------------------------------------------
class Contention:
    """Samples live python multiprocessing workers in the background.

    Sampled rather than measured per cell because the check shells out to
    PowerShell and takes about a second; 560 cells would spend ten minutes on
    it. Every cell records the most recent sample plus this runner's own worker
    count and in-flight count, which is what makes a contended cell visible
    after the fact.
    """

    def __init__(self, period: float = 60.0) -> None:
        self.period = period
        self.last = c8.preflight()
        self.inflight = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.period):
            try:
                self.last = c8.preflight()
            except Exception:  # noqa: BLE001,S110
                pass

    def enter(self) -> dict:
        with self._lock:
            self.inflight += 1
            n = self.inflight
        return {"inflight_at_start": n, "mp_workers": self.last.get("mp_workers")}

    def leave(self) -> None:
        with self._lock:
            self.inflight -= 1


# --------------------------------------------------------------------------
# instances
# --------------------------------------------------------------------------
def select_instances(split: str, limit: int) -> list[str]:
    """Instance ids from the pinned split, interleaved by repo.

    The split file is binding and never revised (DECISIONS.md, overfitting
    protocol): dev is the only half any tuning may see, and test is evaluated
    once at publication. `--limit` interleaves by repo so a scaled canary covers
    both repos and both languages rather than taking the first N of a
    repo-sorted list.
    """
    sp = json.loads(SPLIT.read_text(encoding="utf-8"))
    if split == "all":
        ids = sorted(sp["dev"] + sp["test"])
    else:
        ids = sorted(sp[split])
    if not limit:
        return ids
    # Instance ids carry their source in the prefix, which is 1:1 with the repo
    # on this subset (django=Verified, cli=Multi), so grouping on the prefix
    # groups on the repo without touching the parquet.
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(i.split("__")[0], []).append(i)
    out, keys = [], sorted(groups)
    while len(out) < limit and any(groups[k] for k in keys):
        for k in keys:
            if groups[k] and len(out) < limit:
                out.append(groups[k].pop(0))
    return sorted(out)


def load_instances(ids: list[str]) -> list[dict]:
    import pandas as pd

    df = pd.read_parquet(c8.PARQUET)
    df = df[df.instance_id.isin(ids)]
    found = set(df.instance_id)
    missing = [i for i in ids if i not in found]
    if missing:
        raise SystemExit(f"instance ids not in the parquet: {missing[:5]}")
    out = []
    for _, r in df.iterrows():
        out.append({
            "instance_id": r.instance_id,
            "repo": r.repo,
            "repo_url": r.repo_url,
            "language": r.language,
            "source": r.source,
            "base_commit": r.base_commit,
            "problem_statement": r.problem_statement,
            "gold_files": sorted({s["file"] for s in json.loads(r.gold_context)}),
        })
    out.sort(key=lambda i: i["instance_id"])
    shorts = {short_id(i["instance_id"]) for i in out}
    if len(shorts) != len(out):
        raise SystemExit("short-id collision; worktree names would collide")
    return out


def short_id(instance_id: str) -> str:
    return instance_id.split("__")[-1]


# --------------------------------------------------------------------------
# staging, building, reaping
# --------------------------------------------------------------------------
def cell_tree(arm: str, inst: dict) -> Path:
    parent = c8.SHARED_TREE.get(arm, arm)
    return TREES / f"r8-{parent}-{short_id(inst['instance_id'])}"


def source_tree(inst: dict) -> Path:
    return TREES / c8.REPO_TREE[inst["repo"]][1]


def stage(arm: str, inst: dict) -> Path:
    dest = cell_tree(arm, inst)
    src = source_tree(inst)
    want = inst["base_commit"]
    if dest.exists():
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(dest),
            capture_output=True, text=True,
        ).stdout.strip()
        if head.startswith(want[:12]):
            return dest
        reap(dest, src)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(dest), want],
        cwd=str(src), capture_output=True, text=True, check=True,
    )
    return dest


def reap(dest: Path, src: Path) -> None:
    """Remove a staged worktree. Best effort: disk, not correctness."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(dest)],
        cwd=str(src), capture_output=True, text=True,
    )
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"], cwd=str(src), capture_output=True, text=True,
    )


def embedder_proof(arm: str, tree: Path, row: dict) -> dict:
    """Positive evidence that repowise queried on real vectors, not mock ones.

    **Absence of `embedder_degraded` does not prove a live embedder**, and the
    canary's RESULT.md says "the embedder is healthy on every repowise row" on
    exactly that basis: its recorded value is `null` in all four rows, because
    `_embedder_meta` "emits nothing when the embedder is healthy **or
    unresolved**" (`_meta.py:331-356`). The unresolved branch is the dangerous
    one: if `_configured_embedder_name()` returns empty, `_resolve_embedder`
    returns `MockEmbedder()` with `degraded: False` and no flag at all
    (`_server.py:107-115`), which is the same silent mock-vector state A9
    describes, reported as clean.

    So health is asserted as a chain of three structured facts, none of them log
    prose: the tree's own `.repowise/config.yaml` names `openai`, the key is in
    the server's environment, and the response carries no degradation flag.
    Given the first two, a failure to initialise *would* set the flag, so its
    absence then does mean the openai embedder is active.
    """
    if arm not in ("repowise", "repowise-search"):
        return {}
    cfg = tree / ".repowise" / "config.yaml"
    configured = None
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("embedder:"):
                configured = line.split(":", 1)[1].strip().lower()
    key = bool(os.environ.get("OPENAI_API_KEY") or c8._openai_key())
    degraded = row.get("embedder_degraded")
    return {
        "configured_embedder": configured,
        "openai_key_available": key,
        "embedder_degraded": degraded,
        "embedder_live": bool(configured == "openai" and key and not degraded),
    }


# Written last, by this runner, only after a build exits 0. The marker files
# above prove a build STARTED: `.repowise/wiki.db` and `.repowise/lancedb` both
# exist minutes into a build that is later killed, and the resume then skips the
# build and queries a half-written index while recording `status: ok`. A stamp
# written after the fact is the only thing on disk that can tell a finished
# index from an abandoned one.
STAMP = ".bench_build_complete.json"


def stamp_path(arm: str, tree: Path) -> Path:
    return tree / f"{STAMP[:-5]}__{arm}.json"


def write_stamp(arm: str, tree: Path, build: dict) -> None:
    try:
        stamp_path(arm, tree).write_text(
            json.dumps({k: build.get(k) for k in
                        ("arm", "rc", "seconds", "index_vector_dim",
                         "index_embedder_mock")}, default=str),
            encoding="utf-8",
        )
    except OSError:  # noqa: S110
        pass


def index_present(arm: str, tree: Path) -> bool:
    markers = INDEX_MARKERS.get(arm, ())
    if not markers:
        return True
    if not all((tree / m).exists() for m in markers):
        return False
    return stamp_path(arm, tree).exists()


# --------------------------------------------------------------------------
# cells
# --------------------------------------------------------------------------
def cell_path(tag: str, arm: str, inst: dict) -> Path:
    return OUT / "cells" / tag / f"{arm}__{inst['instance_id']}.json"


def load_cell(tag: str, arm: str, inst: dict) -> dict | None:
    p = cell_path(tag, arm, inst)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_cell(tag: str, arm: str, inst: dict, row: dict) -> None:
    p = cell_path(tag, arm, inst)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def run_unit(inst: dict, parent: str, arms: list[str], args, con: Contention) -> dict:
    """One build-arm's tree for one instance, then every arm that queries it."""
    tag = args.tag
    done = {a: load_cell(tag, a, inst) for a in arms}
    todo = [a for a in arms if not (done[a] and done[a].get("query", {}).get("status") == "ok")]
    if not todo:
        return {"instance_id": inst["instance_id"], "parent": parent, "resumed": True}

    ctx = con.enter()
    unit = {
        "instance_id": inst["instance_id"], "parent": parent, "arms": todo,
        "contention": {**ctx, "workers": args.workers},
    }
    try:
        tree = stage(parent, inst)
        unit["tree"] = str(tree)

        build = None
        if parent in c8.BUILDS and c8.BUILDS[parent] is not None:
            if index_present(parent, tree) and not args.rebuild:
                build = {"arm": parent, "skipped": "index-present", "seconds": 0.0}
            elif args.skip_build:
                build = {"arm": parent, "skipped": "--skip-build", "seconds": 0.0}
            else:
                build = c8.build_index(parent, tree, f"r8__{parent}__{short_id(inst['instance_id'])}")
                if build.get("rc") == 0:
                    write_stamp(parent, tree, build)
        unit["build"] = build

        # A BUILD THAT FAILED DOES NOT GET TO PRODUCE A SCORED ROW.
        #
        # Until now a build exiting rc=1 was recorded and then ignored: the query
        # ran anyway against whatever the failed build left behind, answered with
        # bytes, and the cell was written `status: ok`. A tool cannot be measured
        # on an index it did not finish writing, and the failure is invisible in
        # the aggregate because the row looks exactly like a tool that simply
        # ranked badly.
        #
        # The mock-embedder case (finding D13) is the same defect one level down:
        # rc is 0, the index is complete, and its vectors are 8-dimensional, so
        # the vector leg raises on every query and is swallowed. Two thirds of a
        # retrieval stack is not the retrieval stack. Refused here as well as in
        # `main`, so the cell carries the reason rather than only the run report.
        bad = None
        if build and build.get("rc") not in (None, 0):
            bad = f"build-failed rc={build['rc']}"
        elif build and build.get("index_embedder_mock"):
            bad = (f"index-mock-embedded dim={build.get('index_vector_dim')} "
                   f"(finding D13)")
        if bad:
            for arm in todo:
                write_cell(tag, arm, inst, {
                    "instance_id": inst["instance_id"], "arm": arm,
                    "repo": inst["repo"], "language": inst["language"],
                    "source": inst["source"], "base_commit": inst["base_commit"],
                    "build": build if arm == parent else {"shared_with": parent},
                    "contention": unit["contention"],
                    "timings_are_contended": True,
                    "query": {"arm": arm, "instance_id": inst["instance_id"],
                              "repo": inst["repo"], "tree": str(tree),
                              "status": "build-unusable", "error": bad},
                })
            print(f"[build] {parent:10s} {short_id(inst['instance_id'])} "
                  f"REFUSED: {bad}; {len(todo)} cell(s) not queried", flush=True)
            unit["refused"] = bad
            return unit
        print(
            f"[build] {parent:10s} {short_id(inst['instance_id'])} "
            f"rc={(build or {}).get('rc')} {(build or {}).get('seconds')}s "
            f"{(build or {}).get('skipped', '')}".rstrip(),
            flush=True,
        )

        for arm in todo:
            spec = c8.arm_spec(arm, tree)

            async def _guarded(arm=arm, spec=spec):
                # A hard ceiling over the WHOLE cell, not just the scored call.
                # `query_arm` already bounds the connect (120s) and the call
                # (300s), but leaves `initialize()`, `list_tools()` and the
                # stdio teardown unguarded, and that is where the dev-full run
                # wedged: two units hung after their parent query, the pass
                # never reached grading, and four `repowise mcp` processes were
                # still alive eleven hours later. A benchmark that can hang
                # forever on one cell out of 350 is not a benchmark.
                return await asyncio.wait_for(
                    c8.query_arm(arm, spec, inst, timeout=args.timeout,
                                 warm_timeout=args.warm_timeout),
                    timeout=args.unit_timeout,
                )

            try:
                row = asyncio.run(_guarded())
            except (TimeoutError, asyncio.TimeoutError):
                row = {"arm": arm, "instance_id": inst["instance_id"],
                       "repo": inst["repo"], "tree": str(tree),
                       "status": "cell-timeout",
                       "error": f"exceeded --unit-timeout {args.unit_timeout}s"}
            row["gold_files"] = inst["gold_files"]
            row["gold_hit_rank"] = next(
                (i for i, p in enumerate(row.get("ranked", []), 1)
                 if any(r5.path_matches(p, g) for g in inst["gold_files"])),
                None,
            )
            proof = embedder_proof(arm, tree, row)
            write_cell(tag, arm, inst, {
                "instance_id": inst["instance_id"],
                "arm": arm,
                "repo": inst["repo"],
                "language": inst["language"],
                "source": inst["source"],
                "base_commit": inst["base_commit"],
                "build": build if arm == parent else {"shared_with": parent},
                "contention": unit["contention"],
                # Rung 8 reports no timings (see the module docstring). The
                # seconds are kept because a build that suddenly takes 3x is a
                # signal something broke, not because they are publishable.
                "timings_are_contended": True,
                "embedder_proof": proof,
                "query": row,
            })
            print(
                f"[query] {arm:16s} {short_id(inst['instance_id'])} "
                f"status={row.get('status')} served={row.get('served_count')} "
                f"chars={row.get('chars')} n={row.get('n_ranked')} "
                f"gold_rank={row.get('gold_hit_rank')}"
                + (f" embedder_live={proof.get('embedder_live')}" if proof else ""),
                flush=True,
            )
        return unit
    except Exception as e:  # noqa: BLE001
        unit["error"] = f"{type(e).__name__}: {e}"
        return unit
    finally:
        con.leave()
        if not args.keep_trees:
            try:
                reap(cell_tree(parent, inst), source_tree(inst))
            except Exception:  # noqa: BLE001,S110
                pass


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------
def parse_eval(stderr: str) -> dict:
    """`n/n` from the grader's own summary line, plus the file-level aggregate."""
    out: dict = {}
    for line in stderr.splitlines():
        s = line.strip()
        if s.startswith("EVALUATION:"):
            out["evaluation"] = s.split("EVALUATION:", 1)[1].strip()
    return out


PREWARM_SRC = '''"""Serially materialise every instance checkout the grader will need.

Written by rung8_runner.py; runs inside ContextBench's own venv.
"""
import json, os, sys
# Run by absolute path, so the script's own directory heads sys.path and the
# grader package next to the cwd is not importable without this.
sys.path.insert(0, os.getcwd())
from contextbench.core import checkout

rows = json.load(open(sys.argv[1], encoding="utf-8"))
cache = sys.argv[2]
for r in rows:
    d = checkout(r["repo_url"], r["base_commit"], cache, verbose=False)
    print(("ok   " if d else "FAIL ") + r["instance_id"], flush=True)
'''


def prewarm_checkouts(instances: list[dict], tag: str) -> dict:
    """Materialise every grader checkout serially, before any parallel grading.

    Found by the smoke run, and it is finding E4's shape again: grading the five
    arms in parallel produced `checkout_failed` on an instance every arm had
    predicted correctly, so five arms lost the same instance and every one of
    them would have published a lower score for a reason that was ours.

    Cause: all five grader processes fetch into one shared base clone, and
    ContextBench's `checkout` guards it with a lock file whose implementation
    does not hold on Windows. The observed failure is git's own
    `Unable to create '.../shallow.lock': File exists`, followed by a worktree
    add against a repository that no longer looked like one.

    `checkout` has a fast path that returns immediately when the worktree for a
    commit already exists, so once every commit is materialised once, serially,
    the parallel graders never take the lock at all.
    """
    payload = OUT / f"_prewarm__{tag}.json"
    payload.write_text(json.dumps([
        {k: i[k] for k in ("instance_id", "repo_url", "base_commit")} for i in instances
    ]), encoding="utf-8")
    script = OUT / "_prewarm_checkouts.py"
    script.write_text(PREWARM_SRC, encoding="utf-8")
    p = subprocess.run(
        [str(c8.GRADER_PY), str(script), str(payload), str(TREES / "_cbcache")],
        cwd=str(c8.GRADER), capture_output=True, text=True,
        errors="replace", timeout=6 * 60 * 60,
    )
    (c8.LOGS / f"prewarm__{tag}.log").write_text(
        f"--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n", encoding="utf-8")
    failed = [l.split(" ", 1)[-1].strip() for l in p.stdout.splitlines() if l.startswith("FAIL")]
    return {"rc": p.returncode, "n": len(instances), "failed": failed}


def selftest(inst: dict, tag: str) -> dict:
    """Standing rule 9: grade a known-perfect and a known-wrong prediction first.

    Finding E5: a prediction naming exactly the gold file scores
    `no_context_extracted` if the `traj_data` wrapper is missing, and at this
    rung's scale that is hundreds of cells of clean zero reading as "every tool
    failed". This runs before any real prediction is graded and aborts the
    grading stage if the instrument does not discriminate.
    """
    cases = {
        "perfect": inst["gold_files"],
        "wrong": ["LICENSE", "README.md"],
    }
    res = {}
    for name, files in cases.items():
        pred = OUT / f"pred__{tag}__SELFTEST_{name}.jsonl"
        pred.write_text(
            json.dumps(c8.to_pred({
                "instance_id": inst["instance_id"], "ranked": files,
            })) + "\n",
            encoding="utf-8",
        )
        graded = OUT / f"graded__{tag}__SELFTEST_{name}.jsonl"
        r = c8.grade(pred, graded, TREES / "_cbcache")
        cov = None
        if graded.exists():
            rows = [json.loads(x) for x in graded.read_text(encoding="utf-8").splitlines() if x.strip()]
            if rows:
                cov = rows[0].get("final", {}).get("file", {}).get("coverage")
        res[name] = {"rc": r["rc"], "file_coverage": cov, **parse_eval(r["stderr"])}
    ok = res["perfect"]["file_coverage"] == 1.0 and res["wrong"]["file_coverage"] == 0.0
    res["discriminates"] = ok
    return res


def grade_arm(arm: str, tag: str, instances: list[dict]) -> dict:
    rows = []
    for inst in instances:
        c = load_cell(tag, arm, inst)
        if c and c.get("query", {}).get("status") == "ok":
            rows.append(c["query"])
    if not rows:
        return {"skipped": "no successful calls to grade", "n": 0}
    pred = OUT / f"pred__{tag}__{arm}.jsonl"
    pred.write_text(
        "\n".join(json.dumps(c8.to_pred(r)) for r in rows) + "\n", encoding="utf-8",
    )
    g = c8.grade(pred, OUT / f"graded__{tag}__{arm}.jsonl", TREES / "_cbcache")
    return {"n": len(rows), "rc": g["rc"], **parse_eval(g["stderr"]),
            "pred": str(pred), "graded": str(OUT / f"graded__{tag}__{arm}.jsonl")}


def summarize(arm: str, tag: str) -> dict:
    p = OUT / f"graded__{tag}__{arm}.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    valid = [r for r in rows if "final" in r]
    if not valid:
        return {"graded_rows": len(rows), "valid": 0}
    def mean(path):
        vals = [r["final"][path[0]][path[1]] for r in valid if path[0] in r.get("final", {})]
        return round(sum(vals) / len(vals), 4) if vals else None
    sizes = [r["final"]["file"]["pred_size"] for r in valid if "file" in r.get("final", {})]
    return {
        "graded_rows": len(rows),
        "valid": len(valid),
        "file_coverage": mean(("file", "coverage")),
        "file_precision": mean(("file", "precision")),
        # Reported next to precision on purpose. The arms return wildly
        # different numbers of candidates (get_answer 5, codegraph 10-15,
        # graphify 40-50), and precision is mechanically higher for whoever
        # returns fewest. Without this column our own precision reads as
        # quality when part of it is just a shorter list.
        "mean_pred_files": round(sum(sizes) / len(sizes), 2) if sizes else None,
    }


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--limit", type=int, default=0, help="0 = the whole split")
    ap.add_argument("--instances", nargs="*", default=None, help="explicit ids, overrides --split")
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--warm-timeout", type=float, default=30.0)
    ap.add_argument("--unit-timeout", type=float, default=900.0,
                    help="hard ceiling per (instance, arm) cell, including connect and teardown")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="rebuild even if the index marker is present")
    ap.add_argument("--keep-trees", action="store_true", help="do not reap worktrees; needs ~92 GB for the full run")
    ap.add_argument("--grade-only", action="store_true")
    args = ap.parse_args()

    # The grader materialises one worktree per instance. Left at the default it
    # buries ~6 GB in %TEMP% where nobody looks; under bakeoff/ it sits with
    # everything else this rung created and can be reaped in one command.
    os.environ.setdefault("CONTEXTBENCH_TMP_ROOT", str(TREES / "_cbwt"))

    cap = max(1, (os.cpu_count() or 4) - 2)
    if args.workers > cap:
        print(f"capping workers {args.workers} -> {cap} (cores-2)", flush=True)
        args.workers = cap

    ids = args.instances or select_instances(args.split, args.limit)
    instances = load_instances(ids)

    # E1 is recorded, not enforced. Rung 4 published the build timings; this rung
    # measures retrieval quality and publishes no timings of its own, so a
    # contended build is a data-quality note rather than a stop condition.
    con = Contention()
    pre = con.last
    print(f"preflight: {pre} (recorded, not enforcing: rung 8 publishes no timings)", flush=True)
    if pre.get("mp_workers", 0) > 0:
        print(
            f"WARNING: {pre['mp_workers']} python multiprocessing workers alive. "
            f"Build seconds recorded by this run are contended and must not be "
            f"published as timings (finding E1, measured at 65% inflation).",
            flush=True,
        )
    con.start()

    report = {
        "tag": args.tag, "split": args.split, "arms": args.arms,
        "workers": args.workers, "n_instances": len(instances),
        "instance_ids": [i["instance_id"] for i in instances],
        "preflight": pre, "timings_publishable": False,
    }

    t0 = time.time()
    if not args.grade_only:
        units = []
        for inst in instances:
            for parent in BUILD_ARMS:
                arms = [a for a in args.arms
                        if a == parent or c8.SHARED_TREE.get(a) == parent]
                if arms:
                    units.append((inst, parent, arms))
        print(f"{len(units)} units, {sum(len(u[2]) for u in units)} cells, "
              f"{args.workers} workers", flush=True)
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(run_unit, i, p, a, args, con) for i, p, a in units]
            for n, f in enumerate(as_completed(futs), 1):
                results.append(f.result())
                if n % 10 == 0 or n == len(futs):
                    print(f"--- {n}/{len(futs)} units, {round(time.time()-t0)}s", flush=True)
        report["units"] = results
    con.stop()
    report["run_seconds"] = round(time.time() - t0, 1)

    # Proof of life per arm, before any number is looked at (finding E4). A zero
    # is only a measurement once the arm can be shown to have started, served
    # its tool, answered with bytes, and — for our own arm — queried on real
    # vectors rather than mock ones.
    alive = {}
    for arm in args.arms:
        cells = [load_cell(args.tag, arm, i) for i in instances]
        cells = [c for c in cells if c]
        ok = [c for c in cells if c.get("query", {}).get("status") == "ok"]
        row = {
            "cells": len(cells),
            "ok": len(ok),
            "statuses": sorted({c.get("query", {}).get("status") for c in cells}),
            "empty_responses": sum(1 for c in ok if not c["query"].get("n_ranked")),
            "is_error": sum(1 for c in ok if c["query"].get("isError")),
        }
        if arm in ("repowise", "repowise-search"):
            row["embedder_live"] = sum(1 for c in ok if c.get("embedder_proof", {}).get("embedder_live"))
            row["embedder_degraded"] = sum(1 for c in ok if c.get("embedder_proof", {}).get("embedder_degraded"))
            # Per-query, from the product's own `_meta`. `embedder_live` is a
            # claim about this process resolving an embedder; this is a claim
            # about the query that was actually served.
            row["retrieval_degraded"] = sum(
                1 for c in ok if c.get("query", {}).get("retrieval_degraded"))
        alive[arm] = row
        print(f"[alive] {arm:16s} {row}", flush=True)
    report["proof_of_life"] = alive

    # FINDING D13, WIRED INTO THE ABORT PATH RATHER THAN MERELY RECORDED.
    #
    # `index_embedding_proof` reads the built index's vector dimension back and
    # prints a loud refusal when it is 8. It has done so since the defect was
    # found, and nothing acted on it, which is the difference between a defect
    # being fixed and being documented. Every repowise row rungs 5 and 8 and
    # dev-fix1 published was measured on a mock-embedded index; the run that
    # discovers it again must not be gradeable.
    mock_cells = []
    for arm in args.arms:
        for i in instances:
            c = load_cell(args.tag, arm, i)
            if c and (c.get("build") or {}).get("index_embedder_mock"):
                mock_cells.append(f"{arm}/{short_id(i['instance_id'])}")
    report["mock_embedded_cells"] = mock_cells
    if mock_cells:
        print(
            f"ABORTING GRADING: {len(mock_cells)} repowise build(s) wrote "
            f"mock 8-dimensional vectors, so the vector retrieval leg cannot run "
            f"and these rows are not a measurement of repowise (finding D13). "
            f"First few: {mock_cells[:5]}. Fix the build environment's "
            f"OPENAI_API_KEY, delete those cells, and re-run with the same tag.",
            flush=True,
        )
        (OUT / f"rung8_report__{args.tag}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        return 4

    # Serial, and it must stay serial. See prewarm_checkouts.
    pw = prewarm_checkouts(instances, args.tag)
    report["prewarm"] = pw
    print(f"[prewarm] rc={pw['rc']} {pw['n']} checkouts, failed={pw['failed']}", flush=True)
    # A prewarm that did not run is worse than no prewarm, because grading then
    # proceeds in parallel believing the checkouts are warm. The first version
    # of this failed with ModuleNotFoundError and reported no failures, since it
    # only counted FAIL lines that a dead process never printed.
    grade_workers = min(len(args.arms), 5)
    if pw["rc"] != 0 or pw["failed"]:
        grade_workers = 1
        print("prewarm incomplete: grading SERIALLY so the graders cannot race "
              "on the shared base clone", flush=True)

    # Standing rule 9, before any real prediction is graded.
    st = selftest(instances[0], args.tag)
    report["selftest"] = st
    print(f"[selftest] {st}", flush=True)
    if not st["discriminates"]:
        print("ABORTING GRADING: the grader does not discriminate a perfect "
              "prediction from a wrong one (finding E5). Cells are on disk; "
              "re-run with --grade-only once fixed.", flush=True)
        (OUT / f"rung8_report__{args.tag}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        return 3

    graded = {}
    with ThreadPoolExecutor(max_workers=grade_workers) as ex:
        futs = {ex.submit(grade_arm, a, args.tag, instances): a for a in args.arms}
        for f in as_completed(futs):
            arm = futs[f]
            graded[arm] = f.result()
            print(f"[grade] {arm} {graded[arm]}", flush=True)
    for arm in args.arms:
        graded[arm].update(summarize(arm, args.tag))
    report["graded"] = graded

    (OUT / f"rung8_report__{args.tag}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {OUT / f'rung8_report__{args.tag}.json'}", flush=True)
    for arm in args.arms:
        g = graded.get(arm, {})
        print(f"  {arm:16s} n={g.get('n')} {g.get('evaluation')} "
              f"file_cov={g.get('file_coverage')} file_prec={g.get('file_precision')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
