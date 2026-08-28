#!/bin/bash
# The smoke grid: 3 tasks x 2 harnesses x 2 arms x 3 reps = 36 cells.
#
# Ordered task-major, not arm-major, so a COMPLETE four-cell picture exists for
# task 1 before task 2 starts. If the run is stopped early - budget, a token
# expiry, a laptop problem - what survives is a whole comparison rather than
# three quarters of three of them.
#
# Within a task the bare arms run first. If a bare arm is broken, that is the
# cheapest possible moment to find out, and it costs nothing to discover it
# before spending the treated cells.
set -u

RERUN="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun"
REPS="${REPS:-3}"

TASKS="bandit-interprocedural-taint-checks
python-statemachine-state-data-scoping
onedump-dump-encryption-pipeline"

for task in $TASKS; do
  for cell in "codex bare" "codex rw" "cc bare" "cc rw"; do
    set -- $cell
    harness="$1"; arm="$2"
    echo "########## GRID $task / $harness / $arm  x$REPS  started=$(date -Iseconds)"
    bash "$RERUN/run_cell.sh" "$task" "$harness" "$arm" "$REPS" \
        > "$RERUN/logs/cell-$task-$harness-$arm.log" 2>&1
    echo "########## GRID $task / $harness / $arm exit=$? finished=$(date -Iseconds)"
  done
  echo "########## TASK COMPLETE $task"
done
echo "SMOKE GRID COMPLETE"
