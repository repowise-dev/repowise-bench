#!/usr/bin/env python3
"""
Main experiment runner — production grade.

Features:
- Parallel workers (configurable concurrency)
- Crash-safe resume (JSONL append per task)
- Rate-limit and usage-cap backoff (handled in runner)
- Full metadata capture + raw output saving
- Budget enforcement (thread-safe)
- Graceful Ctrl-C handling

Usage:
    # Full run (leave overnight):
    python harness/run_experiment.py --config configs/swe_qa.yaml

    # Resume after crash/interrupt:
    python harness/run_experiment.py --config configs/swe_qa.yaml --resume

    # Subset:
    python harness/run_experiment.py --config configs/swe_qa.yaml \\
        --conditions C0_bare,C2_full --repos pallets/flask
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness.metrics import BudgetTracker, ResultWriter, RawOutputSaver


# Graceful shutdown
_shutdown = threading.Event()


def _signal_handler(signum, frame):
    print("\n\nShutdown requested — finishing current tasks...")
    _shutdown.set()


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_swe_qa_experiment(config: dict, conditions: list,
                           budget: BudgetTracker, completed: set,
                           writer: ResultWriter, raw_saver: RawOutputSaver):
    """Run SWE-QA benchmark across conditions with parallel workers."""
    def _resolve_task_ids(bench_cfg: dict):
        """`task_ids:` verbatim, or `stratified_shapes:` computed from the
        committed classification.

        A config naming fifteen ids by hand and a config asking for a
        stratified draw are the same run, but only the second one stays correct
        when someone disagrees with a label and edits
        `data/swe_qa/django_question_shapes.json`. The seed and the
        per-slice size are the reproducible part; the ids are derived. See
        `harness/question_shapes.py`.
        """
        explicit = bench_cfg.get("task_ids")
        strat = bench_cfg.get("stratified_shapes")
        if explicit and strat:
            raise ValueError(
                "set task_ids OR stratified_shapes, not both: a hand-written "
                "list silently overriding a computed draw is how a stratified "
                "run stops being one."
            )
        if explicit:
            return list(explicit)
        if not strat:
            return None
        from harness.question_shapes import stratified, STRATIFIED_SEED
        per_shape = int(strat.get("per_shape", 3))
        seed = int(strat.get("seed", STRATIFIED_SEED))
        ids = stratified(per_shape=per_shape, seed=seed)
        print(f"  Stratified draw: {len(ids)} questions, {per_shape} per "
              f"non-empty shape, seed {seed}")
        return ids

    from harness.swe_qa_runner import load_swe_qa_tasks, run_swe_qa_task, ensure_repo_cloned

    bench_cfg = config["benchmarks"]["swe_qa"]

    tasks = load_swe_qa_tasks(
        data_dir=bench_cfg.get("data_dir", "./data"),
        max_tasks=bench_cfg.get("max_tasks"),
        repos=bench_cfg.get("repos"),
        skip_tasks=bench_cfg.get("skip_tasks", 0),
        exclude_indices=bench_cfg.get("exclude_indices"),
        include_indices=bench_cfg.get("include_indices"),
        task_ids=_resolve_task_ids(bench_cfg),
        tasks_file=bench_cfg.get("tasks_file"),
    )

    # Build work items: (task, condition) pairs — interleaved so we get
    # paired data (C0+C1 for the same task) even if the run is interrupted.
    work = []
    for task in tasks:
        tid = task.get("id", task.get("instance_id", ""))
        for condition in conditions:
            cname = condition["name"]
            key = f"{tid}_{cname}"
            if key not in completed:
                work.append((task, condition))

    # Warm regime: group each condition's tasks consecutively so a serial run
    # keeps that arm's ~47k prefix warm across all of its questions (each
    # question re-warms it for the next, within the 5-min TTL). The default
    # task-major order interleaves arms, which cycles a different prefix every
    # step and evicts each arm's cache before it is reused — the exact thrash
    # that makes billed cost incomparable. Only reorder under warmup (the
    # warm+serial regime); leave task-major as the default so an interrupted
    # ordinary run still yields paired data. Stable sort preserves task order
    # within each condition.
    if config.get("warmup"):
        cond_rank = {c["name"]: i for i, c in enumerate(conditions)}
        work.sort(key=lambda tc: cond_rank.get(tc[1]["name"], 0))

    total = len(tasks) * len(conditions)
    skipped = total - len(work)
    print(f"\nSWE-QA: {len(tasks)} tasks x {len(conditions)} conditions = {total} runs")
    if skipped:
        print(f"  Resuming: {skipped} already done, {len(work)} remaining")

    if not work:
        print("  Nothing to do!")
        return

    # Pre-clone repos (sequential, one per repo)
    repos_needed = set(t.get("repo", "") for t, _ in work)
    repos_dir = config["paths"]["repos_dir"]
    for repo in sorted(repos_needed):
        if _shutdown.is_set():
            return
        try:
            ensure_repo_cloned(repo, repos_dir)
        except Exception as e:
            print(f"  Failed to clone {repo}: {e}")

    # Cache warm-up (opt-in). Prompt-cache reuse is keyed on the ~47k static
    # prefix (Claude Code system prompt + MCP tool schemas + append-system-
    # prompt) with a 5-min TTL. Whether a question's first turn READS that
    # prefix (warm, $0.30/M) or WRITES it (cold, $3.75/M — 12.5x) is decided
    # by ambient cache state, not the code under test, so billed cost is not
    # comparable across runs unless warmth is held constant. One throwaway
    # invocation PER CONDITION, through the identical run path (so the warmed
    # prefix is byte-identical to the real questions), pins Q1 warm; with
    # max_workers=1 each question then re-warms the prefix (<5-min TTL) for
    # the next. Applied uniformly to every arm — it removes a harness-induced
    # confound, it does not advantage any arm. Disclose it in RESULTS.md.
    if config.get("warmup"):
        seen_conditions = []
        for _task, cond in work:
            if cond["name"] not in [c["name"] for c in seen_conditions]:
                seen_conditions.append(cond)
        warm_repo = next((t.get("repo", "") for t, _ in work), "")
        warm_task = {
            "id": "warmup", "repo": warm_repo, "split_name": "warmup",
            "question": "Name one source file in this repository.",
            "gold_answer": "", "category": "warmup",
        }
        for cond in seen_conditions:
            if _shutdown.is_set():
                return
            print(f"  Warming cache for {cond['name']}...")
            try:
                # raw_saver=None → no transcript written; result discarded so
                # the warm-up never enters swe_qa.jsonl.
                run_swe_qa_task(warm_task, cond, config, budget, raw_saver=None)
            except Exception as e:
                print(f"    warm-up for {cond['name']} failed (non-fatal): {e}")
        print()

    # Run with thread pool
    max_workers = config.get("parallelism", {}).get("max_workers", 1)
    done_count = 0
    error_count = 0
    start_time = time.time()

    def _worker(item):
        task, condition = item
        if _shutdown.is_set():
            return None
        return run_swe_qa_task(task, condition, config, budget, raw_saver)

    print(f"  Workers: {max_workers} | Budget remaining: ${budget.max_total - budget.total_spent:.2f}")
    print()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker, item): item for item in work}

        for future in as_completed(futures):
            if _shutdown.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break

            metrics = future.result()
            if metrics is None:
                continue

            done_count += 1
            writer.write(metrics, "swe_qa")

            # Progress
            has_error = bool(metrics.error)
            if has_error:
                error_count += 1

            elapsed = time.time() - start_time
            rate = done_count / elapsed * 3600 if elapsed > 0 else 0
            score_str = ""
            if metrics.judge_scores and "error" not in metrics.judge_scores:
                avg = sum(metrics.judge_scores.values()) / len(metrics.judge_scores)
                score_str = f" score={avg:.1f}"

            status = "ERR" if has_error else "OK"
            now = datetime.now().strftime("%H:%M")
            print(
                f"  [{now}] {done_count}/{len(work)} "
                f"[{status}] {metrics.condition}/{metrics.task_id} "
                f"${metrics.estimated_cost_usd:.3f} "
                f"{metrics.wall_clock_seconds:.0f}s"
                f"{score_str} "
                f"| {budget.summary()} "
                f"| {rate:.0f}/hr"
                f"{f' err={metrics.error[:60]}' if has_error else ''}"
            )

            if metrics.error == "budget_exceeded":
                print(f"\n  Budget exceeded! {budget.summary()}")
                pool.shutdown(wait=False, cancel_futures=True)
                return

    elapsed = time.time() - start_time
    print(f"\n  Done: {done_count} tasks in {elapsed:.0f}s ({error_count} errors)")
    print(f"  {budget.summary()}")


def main():
    parser = argparse.ArgumentParser(description="Run repowise benchmark experiments")
    parser.add_argument("--config", required=True)
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-completed task+condition pairs")
    parser.add_argument("--conditions", type=str, default=None,
                        help="Comma-separated condition names")
    parser.add_argument("--repos", type=str, default=None,
                        help="Comma-separated repo names (org/name)")
    args = parser.parse_args()

    config = load_config(args.config)

    # Filter conditions
    conditions = config["conditions"]
    if args.conditions:
        names = set(args.conditions.split(","))
        conditions = [c for c in conditions if c["name"] in names]

    # Filter repos
    if args.repos:
        repo_list = args.repos.split(",")
        for bench in config.get("benchmarks", {}).values():
            if isinstance(bench, dict):
                bench["repos"] = repo_list

    # Budget
    bcfg = config.get("budget", {})
    budget = BudgetTracker(
        max_total_usd=bcfg.get("max_total_usd", 500),
        max_per_task_usd=bcfg.get("max_per_task_usd", 5),
    )

    # Results + logging
    results_dir = config["paths"]["results_dir"]
    logs_dir = config["paths"]["logs_dir"]
    writer = ResultWriter(results_dir)
    raw_saver = RawOutputSaver(logs_dir)

    # Resume
    completed = set()
    if args.resume:
        completed = writer.load_completed()
        print(f"Resuming: {len(completed)} completed task+condition pairs found")

    # Always resume by default (idempotent — re-running is safe)
    if not args.resume:
        completed = writer.load_completed()
        if completed:
            print(f"Auto-resume: {len(completed)} completed pairs found (use fresh results dir to start over)")

    # Metadata
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    meta = {
        "config_path": args.config,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "conditions": [c["name"] for c in conditions],
        "config": config,
    }
    meta_path = Path(results_dir) / "experiment_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    print("=" * 65)
    print(f"EXPERIMENT: {config['experiment_name']}")
    print(f"Conditions: {[c['name'] for c in conditions]}")
    print(f"Budget: ${budget.max_total:.0f}")
    workers = config.get("parallelism", {}).get("max_workers", 1)
    print(f"Workers: {workers}")
    print(f"Results: {results_dir}")
    print("=" * 65)

    benchmarks = config.get("benchmarks", {})
    if benchmarks.get("swe_qa", {}).get("enabled"):
        run_swe_qa_experiment(config, conditions, budget, completed, writer, raw_saver)

    print("\n" + "=" * 65)
    print("EXPERIMENT COMPLETE" if not _shutdown.is_set() else "EXPERIMENT INTERRUPTED (safe to resume)")
    print(budget.summary())
    print(f"Results: {results_dir}")
    print("=" * 65)


if __name__ == "__main__":
    main()
