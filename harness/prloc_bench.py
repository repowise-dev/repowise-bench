"""PR localization benchmark: which files must change to implement this PR?

Judge-free by construction: ground truth is the set of files a real merged
PR actually changed, so scoring is set precision/recall/F1 over normalized
paths instead of an LLM opinion. Each arm receives the PR title and body and
must output a JSON array of repo-relative file paths, under a hard turn cap
so cost stays flat across arms.

Run:
    python harness/prloc_bench.py --config configs/context_bench_flask.yaml \
        --dataset data/context_bench/prloc_flask.json \
        --results-dir results/context_bench/track3_flask

The config is the same one Track 1 uses: conditions, repo overrides, model,
and budgets are shared so the arms are identical across tracks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.metrics import BudgetTracker, RawOutputSaver, ResultWriter, RunMetrics  # noqa: E402
from harness import swe_qa_runner  # noqa: E402

MAX_TURNS = 15

PROMPT_TEMPLATE = """\
Below is the title and description of a real change request for this
repository. Identify which existing files in this repository would need to
be modified to implement it. Do not make any edits.

TITLE: {title}

DESCRIPTION:
{body}

Answer with ONLY a JSON array of repo-relative file paths, e.g.
["src/pkg/module.py", "src/pkg/other.py"]. Include only files that must be
modified (not new files, not test files)."""


# ---------------------------------------------------------------------------
# Path normalization + scoring
# ---------------------------------------------------------------------------

def normalize_path(path: str) -> str:
    """Normalize a predicted or actual path for set comparison."""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    return p.lower()


def extract_paths(text: str) -> list:
    """Pull the predicted file list out of messy agent output.

    Prefers the LAST JSON array of strings in the text (agents often think
    aloud before the final answer); falls back to an empty prediction rather
    than guessing from prose, so a non-answer scores 0 instead of noise.
    """
    candidates = []
    for match in re.finditer(r"\[[^\[\]]*\]", text or "", re.DOTALL):
        try:
            arr = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if isinstance(arr, list) and arr and all(isinstance(x, str) for x in arr):
            candidates.append(arr)
    return candidates[-1] if candidates else []


def score_prediction(predicted: list, actual: list) -> dict:
    pred = {normalize_path(p) for p in predicted if normalize_path(p)}
    gold = {normalize_path(p) for p in actual if normalize_path(p)}
    tp = len(pred & gold)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(gold) if gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "true_positives": tp,
            "predicted_count": len(pred), "gold_count": len(gold)}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_prloc(config: dict, dataset: list, results_dir: Path,
              conditions: list, repeat_tag: str = "n1") -> None:
    writer = ResultWriter(str(results_dir))
    raw_saver = RawOutputSaver(str(results_dir / "logs"))
    budget = BudgetTracker(
        max_total_usd=config.get("budget", {}).get("max_total_usd", 15.0),
        max_per_task_usd=config.get("budget", {}).get("max_per_task_usd", 1.0),
    )
    completed = writer.load_completed()

    overrides = config["paths"].get("repo_overrides") or {}
    for pr in dataset:
        repo_name = pr["repo"]
        repo_path = Path(overrides.get(repo_name, "")).expanduser()
        if not repo_path.exists():
            raise SystemExit(f"repo override missing for {repo_name}")
        for condition in conditions:
            task_id = f"pr{pr['pr_number']}_{repeat_tag}"
            if f"{task_id}_{condition['name']}" in completed:
                continue
            if not budget.check_budget(estimated_cost=0.5):
                print("budget exhausted, stopping")
                return

            metrics = RunMetrics(
                task_id=task_id, benchmark="prloc",
                condition=condition["name"], repo=repo_name,
                model_used=config["agent"]["model"],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            prompt = PROMPT_TEMPLATE.format(title=pr["title"], body=pr["body"])
            metrics.prompt_sent = prompt

            # Third-party configs are bench-root-relative in the yaml.
            cond = dict(condition)
            if cond.get("mcp_server"):
                cfg = Path(cond["mcp_server"]["config"])
                if not cfg.is_absolute():
                    cfg = Path(__file__).resolve().parents[1] / cfg
                cond["mcp_server"] = {**cond["mcp_server"], "config": str(cfg)}
                mcp_config_path = str(cfg)
            else:
                mcp_config_path = None
            if cond.get("repowise_enabled"):
                bench_root = Path(__file__).resolve().parents[1]
                profile = "lean" if cond.get("repowise_mode") == "lean" else None
                mcp_config_path = str(swe_qa_runner.generate_mcp_config(
                    repo_path, bench_root, profile=profile))

            start = time.time()
            output, retries = swe_qa_runner.run_claude_code(
                prompt=prompt, repo_path=str(repo_path), condition=cond,
                model=config["agent"]["model"],
                timeout=config["agent"]["timeout_seconds"],
                max_budget_usd=config.get("budget", {}).get("max_per_task_usd", 1.0),
                mcp_config_path=mcp_config_path,
                benchmark="swe_qa",
                max_turns=config["agent"].get("max_turns", MAX_TURNS),
            )
            metrics.wall_clock_seconds = time.time() - start
            metrics.retries = retries
            metrics.raw_output_file = raw_saver.save(task_id, cond["name"], output)

            if output.get("is_error") or "error" in output:
                metrics.error = str(output.get("error", "unknown"))[:500]
            else:
                predicted = extract_paths(output.get("result", ""))
                metrics.answer = json.dumps(predicted)
                metrics.judge_scores = score_prediction(
                    predicted, pr["changed_files"])
                for k in ("input_tokens", "output_tokens"):
                    setattr(metrics, k, output.get("usage", {}).get(k, 0))
                metrics.num_turns = output.get("num_turns", 0)
                metrics.num_tool_calls = output.get("num_tool_calls", 0)
                metrics.files_explored = output.get("files_explored", [])
                metrics.server_tools_called = output.get("server_tools_called", {})
                metrics.token_source = output.get("token_source", "")
                metrics.estimated_cost_usd = output.get("total_cost_usd", 0.0)

            metrics.compute_derived()
            budget.record(metrics.estimated_cost_usd, task_id)
            writer.write(metrics, "prloc")
            f1 = metrics.judge_scores.get("f1") if metrics.judge_scores else None
            print(f"[{datetime.now().strftime('%H:%M')}] {cond['name']}/{task_id} "
                  f"f1={f1} ${metrics.estimated_cost_usd:.3f} | {budget.summary()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--conditions", default=None,
                    help="comma-separated condition names (default: all)")
    ap.add_argument("--limit", type=int, default=None,
                    help="run only the first N PRs of the dataset")
    ap.add_argument("--repeat-tag", default="n1",
                    help="distinguishes repeat runs (n1, n2) in task ids")
    args = ap.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.limit:
        dataset = dataset[:args.limit]
    conditions = config["conditions"]
    if args.conditions:
        names = set(args.conditions.split(","))
        conditions = [c for c in conditions if c["name"] in names]
    run_prloc(config, dataset, args.results_dir, conditions, args.repeat_tag)


if __name__ == "__main__":
    main()
