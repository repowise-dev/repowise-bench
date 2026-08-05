#!/usr/bin/env bash
# Score a SWE-bench predictions file via the official Dockerized harness (WSL).
# Usage (from WSL):  bash swe_bench_eval.sh <preds.jsonl> <run_id> [max_workers]
# preds path may be a Windows path under /mnt/c/...
set -e
PREDS="$1"
RUN_ID="${2:-smoke}"
WORKERS="${3:-2}"
VENV="$HOME/swebench_venv/bin/python"

if [ ! -f "$PREDS" ]; then echo "preds not found: $PREDS"; exit 1; fi

# Copy preds into the WSL fs (the harness writes a report next to CWD).
mkdir -p "$HOME/swebench_runs"
cp "$PREDS" "$HOME/swebench_runs/$(basename "$PREDS")"
cd "$HOME/swebench_runs"
LOCAL="$(basename "$PREDS")"

"$VENV" -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path "$LOCAL" \
  --run_id "$RUN_ID" \
  --max_workers "$WORKERS" \
  --cache_level instance

echo "=== reports ==="
ls -1 *."$RUN_ID".json 2>/dev/null || true
for f in *."$RUN_ID".json; do echo "--- $f ---"; cat "$f"; done
