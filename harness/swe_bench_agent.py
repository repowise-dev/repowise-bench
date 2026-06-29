"""
SWE-bench agent stage — produce predictions for the Dockerized official scorer.

For each (instance, condition) this checks out an isolated worktree at the
instance's base_commit, optionally indexes it with repowise (C1, index-only),
runs Claude Code to fix the bug, and captures `git diff` as the SWE-bench
`model_patch`. Output is one predictions JSONL per condition:

    {"instance_id", "model_name_or_path", "model_patch"}

Scoring is NOT done here — feed the JSONL to the WSL harness:
    ~/swebench_venv/bin/python -m swebench.harness.run_evaluation \
        --dataset_name princeton-nlp/SWE-bench_Verified \
        --predictions_path <cond>.jsonl --run_id <id> --max_workers 2

Conditions (mirror the plan):
    C0_bare        — no repowise (worktree at base_commit, no .repowise)
    C1_index_only  — repowise MCP, --profile core, index-only

CLI:
    python -m harness.swe_bench_agent run \
        --instances django__django-15629,django__django-16263 \
        --conditions C0_bare,C1_index_only --model sonnet \
        --max-per-task-usd 1.5 --out-dir results/swe_bench_smoke
"""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from harness.swe_qa_runner import (
    _UTF8_ENV, _REPOWISE_CMD, resolve_repo_path, ensure_repo_cloned,
    generate_mcp_config, write_repo_claude_md, run_claude_code,
)
from harness.swe_bench_runner import (
    load_swe_bench_tasks, make_instance_worktree, reset_worktree,
    _BENCH_ROOT,
)

_INDEX_CACHE = _BENCH_ROOT / "indexes_swebench"


def build_prompt(task: dict) -> str:
    repo = task["repo"]
    ps = task.get("problem_statement", "")
    return (
        f"You are fixing a bug in the `{repo}` repository (checked out at the "
        f"commit where the bug exists). Resolve the following issue by editing "
        f"the source files directly.\n\n"
        f"--- ISSUE ---\n{ps}\n--- END ISSUE ---\n\n"
        f"Make the minimal change that fixes the issue. Several files may need "
        f"parallel edits; find the full set before you finish. Do NOT write or "
        f"modify tests — only the library source. When done, stop."
    )


def index_worktree(instance_id: str, wt_path: Path) -> tuple:
    """Index the worktree (index-only) so the repowise MCP server can serve it.

    Caches nothing across instances (each base_commit differs); just ensures a
    fresh .repowise/wiki.db exists under the worktree. Returns (ok, seconds).
    """
    rw_dir = wt_path.resolve() / ".repowise"
    if (rw_dir / "wiki.db").exists():
        return True, 0.0
    rw_dir.mkdir(parents=True, exist_ok=True)
    local_db = (rw_dir / "wiki.db").as_posix()
    cmd = list(_REPOWISE_CMD) + [
        "init", "-y", "--resume", "--index-only", "--commit-limit", "200",
    ]
    env = {
        **_UTF8_ENV,
        "REPOWISE_DB_URL": f"sqlite+aiosqlite:///{local_db}",
    }
    start = time.time()
    print(f"  [index] {instance_id} (index-only) ...")
    r = subprocess.run(cmd, cwd=str(wt_path), capture_output=True, text=True,
                       env=env, timeout=1200, encoding="utf-8", errors="replace")
    elapsed = time.time() - start
    if r.returncode != 0:
        print(f"  [index] FAILED ({elapsed:.0f}s): {r.stderr[-400:]}")
        return False, elapsed
    print(f"  [index] done in {elapsed:.0f}s")
    return True, elapsed


def capture_diff(wt_path: Path) -> str:
    """git diff of tracked changes in the worktree (the agent's source edits).

    Untracked artifacts (.repowise/, .mcp.json, CLAUDE.md) are excluded by
    construction since `git diff` only reports tracked modifications.
    """
    r = subprocess.run(["git", "-C", str(wt_path), "diff"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.stdout


def run_one(task: dict, condition: dict, model: str, timeout: int,
            max_per_task_usd: float, raw_dir: Optional[Path] = None) -> dict:
    """Run one (instance, condition); return a record incl. model_patch."""
    iid = task["instance_id"]
    repo = task["repo"]
    base = task["base_commit"]
    cname = condition["name"]
    repo_path = resolve_repo_path(repo, str(_BENCH_ROOT / "repos"))
    if not repo_path.exists():
        ensure_repo_cloned(repo, str(_BENCH_ROOT / "repos"))

    # Separate worktree per condition so C0 never sees C1's .repowise.
    wt = make_instance_worktree(repo_path, f"{iid}__{cname}", base)
    reset_worktree(wt, base)  # ensure clean start

    rec = {
        "instance_id": iid,
        "model_name_or_path": cname,
        "repo": repo,
        "base_commit": base,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    mcp_config_path = None
    if condition.get("repowise_enabled"):
        ok, idx_t = index_worktree(iid, wt)
        rec["index_seconds"] = round(idx_t, 1)
        if not ok:
            rec["error"] = "index_failed"
            rec["model_patch"] = ""
            return rec
        mcp_cfg = generate_mcp_config(wt, _BENCH_ROOT, profile="core")
        mcp_config_path = str(mcp_cfg)
        write_repo_claude_md(wt, condition.get("repowise_mode", "index-only"))

    prompt = build_prompt(task)
    stream_log = None
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        stream_log = str(raw_dir / f"{iid}__{cname}.stream.jsonl")
    start = time.time()
    output, retries = run_claude_code(
        prompt=prompt,
        repo_path=str(wt),
        condition=condition,
        model=model,
        timeout=timeout,
        max_budget_usd=max_per_task_usd,
        mcp_config_path=mcp_config_path,
        benchmark="swe_bench",
        manage_c0_worktree=False,
        stream_log_path=stream_log,
    )
    rec["wall_clock_seconds"] = round(time.time() - start, 1)
    rec["retries"] = retries
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_lines = output.get("_raw_stream_lines", [])
        (raw_dir / f"{iid}__{cname}.jsonl").write_text(
            "\n".join(raw_lines), encoding="utf-8")
    rec["agent_error"] = output.get("error")
    rec["num_turns"] = output.get("num_turns", 0)
    rec["cost_usd"] = output.get("total_cost_usd", 0.0)
    rec["files_edited"] = output.get("files_edited", [])
    rec["repowise_tools_called"] = output.get("repowise_tools_called", [])

    rec["model_patch"] = capture_diff(wt)
    reset_worktree(wt, base)
    return rec


def main():
    ap = argparse.ArgumentParser(description="SWE-bench agent stage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--instances", required=True, help="comma-separated ids")
    r.add_argument("--conditions", default="C0_bare,C1_index_only")
    r.add_argument("--model", default="sonnet")
    r.add_argument("--timeout", type=int, default=900)
    r.add_argument("--max-per-task-usd", type=float, default=1.5)
    r.add_argument("--out-dir", default="results/swe_bench_smoke")
    args = ap.parse_args()

    CONDITIONS = {
        "C0_bare": {"name": "C0_bare", "repowise_enabled": False},
        "C1_index_only": {"name": "C1_index_only", "repowise_enabled": True,
                          "repowise_mode": "index-only"},
    }

    instances = args.instances.split(",")
    cond_names = args.conditions.split(",")
    tasks = load_swe_bench_tasks(instances=instances)
    found = {t["instance_id"]: t for t in tasks}
    missing = [i for i in instances if i not in found]
    if missing:
        print(f"WARN: not in dataset: {missing}")

    out_dir = _BENCH_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_log = out_dir / "agent_runs.jsonl"

    for cname in cond_names:
        condition = CONDITIONS[cname]
        preds_path = out_dir / f"preds_{cname}.jsonl"
        with open(preds_path, "w", encoding="utf-8") as pf, \
             open(meta_log, "a", encoding="utf-8") as mf:
            for iid in instances:
                if iid not in found:
                    continue
                print(f"\n=== {iid} [{cname}] ===")
                rec = run_one(found[iid], condition, args.model,
                              args.timeout, args.max_per_task_usd,
                              raw_dir=out_dir / "raw")
                # SWE-bench predictions schema (model_patch only).
                pf.write(json.dumps({
                    "instance_id": rec["instance_id"],
                    "model_name_or_path": rec["model_name_or_path"],
                    "model_patch": rec.get("model_patch", ""),
                }) + "\n")
                pf.flush()
                mf.write(json.dumps(rec, default=str) + "\n")
                mf.flush()
                print(f"  turns={rec.get('num_turns')} "
                      f"${rec.get('cost_usd'):.3f} "
                      f"{rec.get('wall_clock_seconds')}s "
                      f"edited={len(rec.get('files_edited', []))} "
                      f"repowise_calls={len(rec.get('repowise_tools_called', []))} "
                      f"patch_len={len(rec.get('model_patch',''))}")
        print(f"\nWrote {preds_path}")


if __name__ == "__main__":
    main()
