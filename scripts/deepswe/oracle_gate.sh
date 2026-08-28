#!/bin/bash
# Step 2 oracle gate. Prove each chosen task's verifier fires in BOTH directions
# before any agent is paid for: the gold patch must score reward 1, and an empty
# patch must score reward 0. A verifier that passes everything, or fails
# everything, produces a healthy-looking cell that measures nothing.
#
# Run from bash, not PowerShell: pier writes progress to stderr and PowerShell
# wraps every such line in a NativeCommandError, which under 'Stop' terminated a
# LIVE cell on 2026-08-09 and orphaned its container.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

CORPUS="C:/Users/ragha/Desktop/bakeoff/deep-swe/tasks"
OUT="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun/jobs"
LOGS="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun/logs"

TASKS="bandit-interprocedural-taint-checks python-statemachine-state-data-scoping onedump-dump-encryption-pipeline"

for t in $TASKS; do
  for agent in oracle nop; do
    echo "########## ORACLE GATE $t / $agent  started=$(date -Iseconds)"
    pier run -p "$CORPUS/$t" --agent "$agent" -o "$OUT" \
        --job-name "gate--$t--$agent" -n 1 --no-delete -y \
        > "$LOGS/gate-$t-$agent.log" 2>&1
    echo "########## exit=$? finished=$(date -Iseconds)"
  done
done
echo "ORACLE GATE COMPLETE"
