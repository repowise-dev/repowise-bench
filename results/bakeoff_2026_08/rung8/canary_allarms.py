"""Rung 8 canary: one ContextBench instance per repo, every arm, end to end.

Raghav, 2026-08-01: "before full llm runs, can we run 1 sample across all the
tools we are measuring, so that we validate our harness works and no obvious
issue?" This is that run. It is not a result and produces no publishable
number; n=2 measures nothing. It exists to make the rung 8 overnight run fail
here instead of at hour eleven.

What it exercises, which is the whole rung 8 path minus scale:

    stage a repo at base_commit -> build one index per (arm, instance) in that
    arm's OWN worktree (E3) -> query the arm with the instance's problem
    statement -> extract ranked file paths -> emit a ContextBench-shaped
    prediction -> grade with ContextBench's own contextbench/evaluate.py

Design notes that are not arbitrary:

1. **One worktree per arm, never a shared checkout** (finding E3). Every arm
   writes its index into a dotdir inside the repo, so a shared tree means each
   arm indexes its predecessors' output and the bias favours whoever ran first,
   which was us. Rung 4's alternative fix (clear every artifact dir per cell) is
   right for a timed build and fatal here, because it destroys the indexes the
   query stage needs. Verified live while writing this: `bakeoff/django`'s
   `.repowise` was down to a 1 MB stub and `.codegraph` / `.code-review-graph`
   were gone entirely, exactly the E3 follow-on.

2. **Nothing records a zero without proof of life** (finding E4). Every call
   records `isError`, `status`, the served tool list and the response length. An
   arm that returns no gold file is only scored once the harness can show the
   server started, advertised the tool it was asked for, and answered with
   bytes. Four arms were silently zeroed in rung 5 and every cause was ours.

3. **The grader runs in its OWN venv.** ContextBench pins `tree-sitter==0.20.4`;
   repowise runs `0.25.2` and its parsers are the thing under measurement.
   Installing the grader's requirements into the bench venv would downgrade the
   arm we are benchmarking, so `external/ContextBench/.venv-grader` is separate
   and the grader is invoked as a subprocess.

4. **The two instances are the ones already pinned.** `bakeoff/django` sits at
   `838e432e` and `bakeoff/cli` at `82880111`, and each is the base_commit of
   exactly one verified instance. So the canary covers both repos, both
   languages (python / go) and both `source` values (Verified / Multi) without
   a single extra checkout.
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
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUT = Path(__file__).resolve().parent
BENCH_ROOT = OUT.parents[2]
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
TREES = Path(r"C:\Users\ragha\Desktop\bakeoff")
REPOWISE_EXE = REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"
NPM_BIN = Path(os.environ["APPDATA"]) / "npm"
UV_BIN = Path.home() / ".local" / "bin"

PARQUET = BENCH_ROOT / "data" / "contextbench" / "contextbench_verified.parquet"
GRADER = BENCH_ROOT / "external" / "ContextBench"
GRADER_PY = GRADER / ".venv-grader" / "Scripts" / "python.exe"

LOGS = OUT / "logs"

# Reuse rung 5's extractors rather than re-deriving them. Every one of them
# encodes a specific bug that cost an arm a false zero, so a fresh
# implementation would re-earn all four.
_r5 = OUT.parent / "rung5" / "retrieval_probe_multiarm.py"
_spec = importlib.util.spec_from_file_location("r5probe", _r5)
r5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r5)


# --------------------------------------------------------------------------
# E1: no timed or measured build while another process pool is alive
# --------------------------------------------------------------------------
def _openai_key() -> str | None:
    """The embedder key, from the environment or the repo's provider config."""
    env = os.environ.get("OPENAI_API_KEY")
    if env:
        return env
    cfg = REPOWISE_ROOT / "provider_config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("keys", {}).get("openai")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def preflight() -> dict:
    """Refuse to start under a live python multiprocessing pool (finding E1).

    Name-gated on `python*.exe` deliberately. The first version of this guard
    matched the string `repowise` in any command line and reported 19 live pools
    when there were none, because every process in the session carries the repo
    path. The second matched `spawn_main` and found its own PowerShell query
    text.
    """
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like 'python*.exe' -and "
        "$_.CommandLine -like '*spawn_main*' } | Measure-Object | "
        "Select-Object -ExpandProperty Count"
    )
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=60,
        )
        workers = int((p.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        workers = -1
    return {"mp_workers": workers}


# --------------------------------------------------------------------------
# staging + index build
# --------------------------------------------------------------------------
BUILDS = {
    "repowise": lambda t: [
        str(REPOWISE_EXE), "init", "--no-prose", "--embedder", "openai",
        "--max-file-pages", "0", "--no-workspace", "--no-editor-setup", "--yes",
    ],
    "codegraph": lambda t: [str(NPM_BIN / "codegraph.cmd"), "init", str(t)],
    # `--embedding-provider local` is NOT optional dressing. Without it the
    # graph carries no embeddings, `semantic_search_nodes_tool` answers
    # `search_mode: "none"`, and the arm returns zero hits for every natural
    # language query. The canary's first pass built without it and code-review
    # -graph returned 0 ranked paths in 1,194 chars, which is the exact 0.028
    # state rung 5 spent three passes escaping (finding E4). "Installed the
    # package" is not setup.
    "crg": lambda t: [
        str(UV_BIN / "code-review-graph.exe"), "build", "--repo", str(t),
        "--embedding-provider", "local",
    ],
    "graphify": lambda t: [str(UV_BIN / "graphify.exe"), "update", str(t)],
    # Serena is an LSP wrapper and builds no persistent index. It still needs a
    # worktree to point at, so it is staged and skipped here rather than absent.
    "serena": None,
}


def stage(arm: str, repo_key: str, source_tree: Path, base_commit: str) -> Path:
    """A detached worktree of `source_tree` at `base_commit`, one per arm.

    Placed under `bakeoff/` and therefore outside the repowise tree, because a
    `.repowise-workspace.yaml` above a repo changes the served MCP tool surface
    (11 tools single-repo, 13 in workspace mode) and `repowise mcp` has no
    `--no-workspace` to opt out of it (finding D1b).
    """
    dest = TREES / f"c8-{arm}-{repo_key}"
    if dest.exists():
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(dest),
            capture_output=True, text=True,
        ).stdout.strip()
        if head.startswith(base_commit[:12]):
            return dest
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(dest), base_commit],
        cwd=str(source_tree), capture_output=True, text=True, check=True,
    )
    return dest


def build_index(arm: str, tree: Path, tag: str) -> dict:
    argv_fn = BUILDS[arm]
    if argv_fn is None:
        return {"arm": arm, "skipped": "no-index-by-design", "seconds": 0.0}
    argv = argv_fn(tree)
    env = dict(os.environ)
    env.update({
        "DO_NOT_TRACK": "1",
        "REPOWISE_SKIP_EDITOR_SETUP": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    LOGS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    p = subprocess.run(
        argv, cwd=str(tree), env=env, capture_output=True,
        text=True, errors="replace", timeout=3 * 60 * 60,
    )
    el = round(time.time() - t0, 1)
    (LOGS / f"build__{tag}.log").write_text(
        f"$ {' '.join(argv)}\n\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n",
        encoding="utf-8",
    )
    return {"arm": arm, "rc": p.returncode, "seconds": el, "argv": argv}


# --------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------
def arm_spec(arm: str, tree: Path) -> dict:
    """Launch command, primary tool and extractor, taken verbatim from rung 5.

    `arm_specs` there is keyed to the `r5-<arm>` trees, so the shapes are reused
    and only the tree is re-pointed. Every comment in that file about why a
    given tool name or argument is what it is applies here unchanged.
    """
    specs = r5.arm_specs(str(TREES / "repowise-self"))
    key = {"crg": "code-review-graph"}.get(arm, arm)
    spec = dict(specs[key])
    spec["tree"] = str(tree)
    if arm == "repowise":
        spec["args"] = ["mcp", str(tree), "--transport", "stdio"]
        # Cheapest tool that forces the index open, so the cold-start cost is
        # paid by a call nobody scores.
        spec["warm"] = ("search_codebase", {"query": "test", "limit": 1})
    elif arm == "codegraph":
        spec["args"] = ["serve", "--mcp", "--path", str(tree), "--no-watch"]
    elif arm == "crg":
        spec["args"] = ["serve", "--repo", str(tree)]
    elif arm == "graphify":
        spec["args"] = [
            "--transport", "stdio", "--graph",
            str(tree / "graphify-out" / "graph.json"),
        ]
    elif arm == "serena":
        spec["args"] = [
            "start-mcp-server", "--project", str(tree), "--transport", "stdio",
            "--enable-web-dashboard", "false", "--enable-gui-log-window", "false",
        ]
    return spec


async def query_arm(arm: str, spec: dict, instance: dict, timeout=300.0) -> dict:
    """One call, with enough recorded that a zero is separable from a failure.

    `isError`, `status`, the served tool list and the response length are all
    captured whatever happens, because rung 5's finding E4 is that a dead arm
    and a bad arm produce identical summary rows and the silently-dead one is
    never ours.
    """
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "DO_NOT_TRACK": "1",
        "REPOWISE_SKIP_EDITOR_SETUP": "1",
    })
    # The index is built with `--embedder openai`, which reads the key from
    # `provider_config.json`. The MCP server reads `OPENAI_API_KEY` from its
    # environment and nothing bridges the two, so a server launched the obvious
    # way queries an openai-embedded index with a mock embedder. Bridge it here
    # for every arm's environment (harmless to the arms that do not use it) so
    # our own arm is measured with the retrieval stack it was built with.
    if not env.get("OPENAI_API_KEY"):
        key = _openai_key()
        if key:
            env["OPENAI_API_KEY"] = key
    question = instance["problem_statement"]
    row = {
        "arm": arm,
        "instance_id": instance["instance_id"],
        "repo": instance["repo"],
        "tree": spec["tree"],
    }
    sp = StdioServerParameters(command=spec["command"], args=spec["args"], env=env)
    try:
        async with asyncio.timeout(120):
            cm = stdio_client(sp)
            r, w = await cm.__aenter__()
    except Exception as e:  # noqa: BLE001
        row.update({"status": "server-failed", "error": f"{type(e).__name__}: {e}"})
        return row

    try:
        async with ClientSession(r, w) as s:
            await s.initialize()
            served = sorted(t.name for t in (await s.list_tools()).tools)
            row["served_tools"] = served
            row["served_count"] = len(served)
            # Per-arm activation steps. Each of these exists because its absence
            # produced a clean, plausible zero (finding E4), so they run for ANY
            # arm advertising the tool rather than being special-cased by name.
            if "activate_project" in served:
                try:
                    async with asyncio.timeout(600):
                        await s.call_tool("activate_project", {"project": spec["tree"]})
                    row["activated"] = True
                except Exception as e:  # noqa: BLE001
                    row["activated"] = f"failed: {e}"
            if "embed_graph_tool" in served:
                try:
                    async with asyncio.timeout(1800):
                        await s.call_tool("embed_graph_tool", {
                            "provider": "local",
                            "model": "sentence-transformers/all-MiniLM-L6-v2",
                        })
                    row["embedded"] = True
                except Exception as e:  # noqa: BLE001
                    row["embedded"] = f"failed: {e}"

            # Warm-up. repowise's first call on a cold index exceeded the 300s
            # timeout on django in the canary's first pass, while the server was
            # demonstrably alive (11 tools served). Rung 5 hit the same thing:
            # question E01 timed out on "repowise's cold first call" and our arm
            # went to n=83 against everyone else's 84. A cold-start cost charged
            # to the first graded question is both a false zero and, at rung 8
            # scale, a false zero for whichever instance happens to go first.
            # So the index is warmed with a throwaway call that is never scored.
            warm = spec.get("warm")
            if warm:
                wtool, wargs = warm
                if wtool in served:
                    t0 = time.time()
                    try:
                        async with asyncio.timeout(timeout):
                            await s.call_tool(wtool, wargs)
                        row["warm_seconds"] = round(time.time() - t0, 1)
                    except Exception as e:  # noqa: BLE001
                        row["warm_seconds"] = round(time.time() - t0, 1)
                        row["warm_error"] = f"{type(e).__name__}: {e}"

            tool, args = spec["call"](question, "get_answer")
            row["tool"] = tool
            if tool not in served:
                row["status"] = "tool-absent"
                return row
            try:
                async with asyncio.timeout(timeout):
                    res = await s.call_tool(tool, args)
                text = "\n".join(getattr(c, "text", "") or "" for c in res.content)
                try:
                    payload = json.loads(text)
                except (ValueError, TypeError):
                    payload = {}
                ranked = spec["extract"](payload, text)
                row.update({
                    "status": "ok",
                    "isError": bool(res.isError),
                    "chars": len(text),
                    "ranked": ranked[:50],
                    "n_ranked": len(ranked),
                })
                # "The arm was alive" is not enough. An arm can start, serve
                # its tools, answer with bytes, and still have half its
                # retrieval stack dead. repowise's MCP server reads
                # OPENAI_API_KEY from its own environment and does NOT pick up
                # the provider_config.json key that `init --embedder openai`
                # used, so it silently falls back to mock vectors that cannot
                # match the real index. It says so in every response's `_meta`
                # (`embedder_degraded`), and rung 5 recorded no such field, so
                # whether OUR OWN arm's 0.643 was measured on real embeddings
                # or on full-text search alone cannot now be established from
                # the data. Record it per call so that question is never
                # unanswerable again. This is finding E4 one level deeper:
                # prove the arm was FULLY alive, not merely alive.
                meta = payload.get("_meta") if isinstance(payload, dict) else None
                if isinstance(meta, dict):
                    row["embedder"] = meta.get("embedder")
                    row["embedder_degraded"] = meta.get("embedder_degraded")
                    if meta.get("embedder_degraded"):
                        print(
                            f"  !! {arm}: EMBEDDER DEGRADED — semantic search is "
                            f"on mock vectors and cannot match the real index. "
                            f"This row is not a measurement.",
                            flush=True,
                        )
            except Exception as e:  # noqa: BLE001
                row.update({"status": "call-failed", "error": f"{type(e).__name__}: {e}"})
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001,S110
            pass
    return row


# --------------------------------------------------------------------------
# ContextBench prediction + grading
# --------------------------------------------------------------------------
def to_pred(row: dict) -> dict:
    """A ContextBench trajectory for one (arm, instance).

    **The `traj_data` wrapper is mandatory and its absence is silent.** A flat
    `{"instance_id", "pred_files", ...}` dict parses fine, loads fine, reports
    "1 trajectories loaded", and then scores `no_context_extracted` — because
    `parse_trajectory` reads `data["traj_data"]` and a plain `.jsonl` with no
    `history` field is passed through verbatim by `load_pred`. Caught by
    self-testing a deliberately PERFECT prediction, which scored 0/1. At rung 8
    scale that is 448 cells of zero that look like every tool failing.

    `spans` is a dict keyed by file (`{path: [{"start": int, "end": int}]}`),
    not the list of `{file, start_line, end_line}` that `_step_spans` consumes
    downstream. The two shapes are one function apart and only one is accepted
    here.

    **No spans are emitted, deliberately, and this costs us the metric we most
    wanted.** PLAN.md calls line-level span overlap "the discriminating one, and
    unpublished by anyone in this field". Self-test confirmed a file-perfect
    prediction still scores symbol / span / line Coverage 0.000, so file-level
    is the only granularity an agent-free arm earns here. We could emit spans
    for our own arm today (`get_answer` returns symbol bodies with line ranges)
    and Graphify (`loc=L149`) and code-review-graph (node line numbers) also
    carry them. Doing it for our arm alone would hand us three granularities the
    competition scores zero on, purely because ours is the response format we
    already know. That is finding E4's exact asymmetry, pointed the other way.
    Span-level needs per-arm extractors validated against captured responses, as
    its own pass; until then rung 8 reports file-level for everyone.
    """
    files = [str(p).replace("\\", "/") for p in row.get("ranked", [])]
    return {
        "instance_id": row["instance_id"],
        "traj_data": {
            "pred_files": files,
            "pred_spans": {},
            "pred_steps": [{"files": files, "spans": {}}],
        },
    }


def grade(pred_path: Path, out_path: Path, cache: Path) -> dict:
    argv = [
        str(GRADER_PY), "-m", "contextbench.evaluate",
        "--gold", str(PARQUET),
        "--pred", str(pred_path),
        "--cache", str(cache),
        "--out", str(out_path),
    ]
    p = subprocess.run(
        argv, cwd=str(GRADER), capture_output=True, text=True,
        errors="replace", timeout=60 * 60,
    )
    (LOGS / f"grade__{pred_path.stem}.log").write_text(
        f"$ {' '.join(argv)}\n\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n",
        encoding="utf-8",
    )
    return {"rc": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:]}


# --------------------------------------------------------------------------
def load_instances(commits: list[str]) -> list[dict]:
    import pandas as pd

    df = pd.read_parquet(PARQUET)
    out = []
    for c in commits:
        m = df[df.base_commit.str.startswith(c)]
        if m.empty:
            raise SystemExit(f"no verified instance at base_commit {c}")
        r = m.iloc[0]
        out.append({
            "instance_id": r.instance_id,
            "repo": r.repo,
            "language": r.language,
            "source": r.source,
            "base_commit": r.base_commit,
            "problem_statement": r.problem_statement,
            "gold_files": sorted({s["file"] for s in json.loads(r.gold_context)}),
        })
    return out


REPO_TREE = {"django/django": ("django", "django"), "cli/cli": ("cli", "cli")}


async def amain(args) -> int:
    pre = preflight()
    print(f"preflight: {pre}", flush=True)
    if pre["mp_workers"] > 0 and not args.allow_contended:
        print(
            f"REFUSING: {pre['mp_workers']} python multiprocessing workers alive "
            f"(finding E1, measured at 65% inflation). Pass --allow-contended to "
            f"override; index timings will not be comparable.",
            flush=True,
        )
        return 2

    instances = load_instances(args.commits)
    arms = args.arms
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    builds, rows = [], []
    for inst in instances:
        repo_key, tree_name = REPO_TREE[inst["repo"]]
        src = TREES / tree_name
        for arm in arms:
            tag = f"{arm}__{repo_key}"
            tree = stage(arm, repo_key, src, inst["base_commit"])
            print(f"[stage] {tag} -> {tree}", flush=True)
            if not args.skip_build:
                b = build_index(arm, tree, tag)
                b.update({"instance_id": inst["instance_id"], "tree": str(tree)})
                builds.append(b)
                print(f"[build] {tag} rc={b.get('rc')} {b.get('seconds')}s", flush=True)
                (OUT / "canary_builds.json").write_text(json.dumps(builds, indent=2))

    for inst in instances:
        repo_key, _ = REPO_TREE[inst["repo"]]
        for arm in arms:
            tree = TREES / f"c8-{arm}-{repo_key}"
            row = await query_arm(arm, arm_spec(arm, tree), inst)
            row["gold_files"] = inst["gold_files"]
            # Local sanity signal only. The graded number is ContextBench's.
            row["gold_hit_rank"] = next(
                (i for i, p in enumerate(row.get("ranked", []), 1)
                 if any(r5.path_matches(p, g) for g in inst["gold_files"])),
                None,
            )
            rows.append(row)
            print(
                f"[query] {arm:10s} {repo_key:7s} status={row.get('status')} "
                f"served={row.get('served_count')} chars={row.get('chars')} "
                f"n_ranked={row.get('n_ranked')} gold_rank={row.get('gold_hit_rank')}",
                flush=True,
            )
            (OUT / "canary_queries.json").write_text(json.dumps(rows, indent=2))

    # One prediction file per arm, graded separately: ContextBench aggregates
    # across a file, so mixing arms into one would average them together.
    graded = {}
    for arm in arms:
        arm_rows = [r for r in rows if r["arm"] == arm and r.get("status") == "ok"]
        if not arm_rows:
            graded[arm] = {"skipped": "no successful calls to grade"}
            continue
        pred = OUT / f"pred__{arm}.jsonl"
        pred.write_text(
            "\n".join(json.dumps(to_pred(r)) for r in arm_rows) + "\n",
            encoding="utf-8",
        )
        graded[arm] = grade(pred, OUT / f"graded__{arm}.jsonl", TREES / "_cbcache")
        print(f"[grade] {arm} rc={graded[arm]['rc']}", flush=True)

    (OUT / "canary_report.json").write_text(json.dumps({
        "preflight": pre,
        "instances": [{k: v for k, v in i.items() if k != "problem_statement"}
                      for i in instances],
        "builds": builds,
        "queries": rows,
        "graded": graded,
    }, indent=2, default=str))
    print(f"\nwrote {OUT / 'canary_report.json'}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commits", nargs="+", default=["838e432e", "82880111"])
    ap.add_argument(
        "--arms", nargs="+",
        default=["repowise", "codegraph", "crg", "graphify", "serena"],
    )
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--allow-contended", action="store_true")
    args = ap.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
