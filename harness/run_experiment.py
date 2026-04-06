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
    from harness.swe_qa_runner import load_swe_qa_tasks, run_swe_qa_task, ensure_repo_cloned

    bench_cfg = config["benchmarks"]["swe_qa"]
    tasks = load_swe_qa_tasks(
        data_dir="./data",
        max_tasks=bench_cfg.get("max_tasks"),
        repos=bench_cfg.get("repos"),
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


def run_swe_bench_experiment(config: dict, conditions: list,
                              budget: BudgetTracker, completed: set,
                              writer: ResultWriter, raw_saver: RawOutputSaver):
    """Run SWE-bench benchmark across conditions with parallel workers."""
    from harness.swe_bench_runner import load_swe_bench_tasks, run_swe_bench_task, clone_repo

    bench_cfg = config["benchmarks"]["swe_bench"]
    tasks = load_swe_bench_tasks(
        data_dir="./data",
        max_tasks=bench_cfg.get("max_tasks"),
        repos=bench_cfg.get("repos"),
        difficulty=bench_cfg.get("difficulty"),
    )

    # Build work items
    work = []
    for condition in conditions:
        cname = condition["name"]
        for task in tasks:
            key = f"{task['instance_id']}_{cname}"
            if key not in completed:
                work.append((task, condition))

    total = len(tasks) * len(conditions)
    skipped = total - len(work)
    print(f"\nSWE-bench: {len(tasks)} tasks x {len(conditions)} conditions = {total} runs")
    if skipped:
        print(f"  Resuming: {skipped} already done, {len(work)} remaining")

    if not work:
        print("  Nothing to do!")
        return

    # Pre-clone repos (sequential — SWE-bench needs full clones for checkout)
    repos_needed = set(t["repo"] for t, _ in work)
    repos_dir = config["paths"]["repos_dir"]
    for repo in sorted(repos_needed):
        if _shutdown.is_set():
            return
        try:
            clone_repo(repo, repos_dir)
        except Exception as e:
            print(f"  Failed to clone {repo}: {e}")

    # SWE-bench: sequential per repo (git checkout is not thread-safe on same repo)
    # Group work by repo, run repos sequentially, tasks within a repo sequentially
    from collections import defaultdict
    by_repo = defaultdict(list)
    for task, cond in work:
        by_repo[task["repo"]].append((task, cond))

    max_workers = config.get("parallelism", {}).get("max_workers", 1)
    done_count = 0
    error_count = 0
    start_time = time.time()

    print(f"  Workers: {max_workers} (sequential per repo for git safety)")
    print(f"  Budget remaining: ${budget.max_total - budget.total_spent:.2f}")
    print()

    # For SWE-bench, we process tasks sequentially within each repo
    # but can process different repos in parallel
    def _run_repo_tasks(repo_tasks):
        results = []
        for task, condition in repo_tasks:
            if _shutdown.is_set():
                break
            metrics = run_swe_bench_task(task, condition, config, budget, raw_saver)
            results.append(metrics)
        return results

    repo_list = list(by_repo.items())
    with ThreadPoolExecutor(max_workers=min(max_workers, len(repo_list))) as pool:
        futures = {
            pool.submit(_run_repo_tasks, tasks_list): repo
            for repo, tasks_list in repo_list
        }

        for future in as_completed(futures):
            if _shutdown.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break

            repo_results = future.result()
            if not repo_results:
                continue

            for metrics in repo_results:
                done_count += 1
                writer.write(metrics, "swe_bench")

                has_error = bool(metrics.error)
                if has_error:
                    error_count += 1

                elapsed = time.time() - start_time
                rate = done_count / elapsed * 3600 if elapsed > 0 else 0
                has_patch = bool(metrics.answer and metrics.answer.startswith("diff"))

                status = "ERR" if has_error else ("PATCH" if has_patch else "EMPTY")
                now = datetime.now().strftime("%H:%M")
                print(
                    f"  [{now}] {done_count}/{len(work)} "
                    f"[{status}] {metrics.condition}/{metrics.task_id} "
                    f"${metrics.estimated_cost_usd:.3f} "
                    f"{metrics.wall_clock_seconds:.0f}s "
                    f"turns={metrics.num_turns} "
                    f"| {budget.summary()} "
                    f"| {rate:.0f}/hr"
                    f"{f' err={metrics.error[:60]}' if has_error else ''}"
                )

                if metrics.error == "budget_exceeded":
                    print(f"\n  Budget exceeded! {budget.summary()}")
                    pool.shutdown(wait=False, cancel_futures=True)
                    return

    elapsed = time.time() - start_time
    patches = sum(1 for _ in (Path(raw_saver.logs_dir).parent / "patches").glob("*_C*.patch"))
    print(f"\n  Done: {done_count} tasks in {elapsed:.0f}s ({error_count} errors, {patches} patches)")
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

    if benchmarks.get("swe_bench", {}).get("enabled"):
        run_swe_bench_experiment(config, conditions, budget, completed, writer, raw_saver)

    print("\n" + "=" * 65)
    print("EXPERIMENT COMPLETE" if not _shutdown.is_set() else "EXPERIMENT INTERRUPTED (safe to resume)")
    print(budget.summary())
    print(f"Results: {results_dir}")
    print("=" * 65)


if __name__ == "__main__":
    main()
