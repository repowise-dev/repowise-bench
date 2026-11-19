#!/bin/bash
# Runs the pre-registered model order; each model up to 3 driver passes (failed runs retried).
# Stops at once if runs/STOP exists (usage/rate limit). Log: runs/grid.log
# MODELS, REPS, JOBS and RUNS (output root, default ../runs) override the defaults.
cd "$(dirname "$0")"
RUNS=${RUNS:-../runs}
for m in ${MODELS:-claude-haiku-4-5 claude-sonnet-4-6 claude-opus-4-8 claude-sonnet-5-5 claude-opus-5-5 claude-fable-5-1 claude-opus-4-6}; do
  for pass in 1 2 3; do
    [ -f ../runs/STOP ] && { echo "STOP present; halting before $m pass $pass"; exit 3; }
    echo "=== $m pass $pass $(date)"
    python driver.py --model $m --out "$RUNS/$m" --reps ${REPS:-3} --jobs ${JOBS:-3}
    want=$((48*${REPS:-3}))
    n=$(ls "$RUNS"/$m/runs/*__r[1-${REPS:-3}]/meta.json 2>/dev/null | wc -l)
    echo "=== $m pass $pass done: $n/$want $(date)"
    [ "$n" -ge "$want" ] && break
  done
  echo "MODEL_DONE $m $(date)"
done
