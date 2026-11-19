#!/bin/bash
# Rerun one row of the per-model bench against repowise at a given sha.
#
#   RW_REPO=/path/to/repowise SHA=<main sha> MODEL=claude-opus-5-5 REPS=3 bash harness/rerun_at_sha.sh
#
# 1. checks out repowise at SHA in a detached worktree (RW_WT, default ../rw-<short sha> beside the bench)
# 2. rebuilds the three keyless indexes with that build on Modal (modal/index_app.py, same flags as the
#    published grid: init --yes --no-prose --commit-limit 1000), downloads them into repos/<name>/.repowise
#    and sets mcp.always_load: true
# 3. runs the grid for MODEL at REPS runs per arm, one session at a time (JOBS=1), into runs-<short sha>/
#    with the MCP server importing the SHA worktree; a usage or rate limit writes runs/STOP and halts,
#    and rerunning the same command resumes (a run with a meta.json is never repeated)
# 4. prints the summary for the new row (analyze.py)
#
# Needs: git, modal (MODAL_PROFILE set by the caller), the claude CLI logged in, and REPOWISE_PY: a Python
# whose environment has repowise's dependencies installed for SHA.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BENCH=$(dirname "$HERE")
RW_REPO=${RW_REPO:?set RW_REPO to a repowise clone}
SHA=${SHA:?set SHA to the repowise commit under test}
MODEL=${MODEL:-claude-opus-5-5}
REPS=${REPS:-3}
JOBS=${JOBS:-1}

git -C "$RW_REPO" fetch -q origin
FULL=$(git -C "$RW_REPO" rev-parse "$SHA^{commit}")
SHORT=${FULL:0:9}
RW_WT=${RW_WT:-$(dirname "$BENCH")/rw-$SHORT}
[ -d "$RW_WT" ] || git -C "$RW_REPO" worktree add -q --detach "$RW_WT" "$FULL"
[ "$(git -C "$RW_WT" rev-parse HEAD)" = "$FULL" ] || { echo "worktree $RW_WT is not at $FULL"; exit 2; }
echo "repowise $FULL in $RW_WT"

if [ "${SKIP_INDEX:-0}" != "1" ]; then
  rm -rf "$BENCH/modal/src" && mkdir -p "$BENCH/modal/src"
  git -C "$RW_WT" archive HEAD | tar -x -C "$BENCH/modal/src"
  (cd "$BENCH/modal" && modal run index_app.py)
  mkdir -p "$BENCH/modal/idx-$SHORT"
  for r in foreman okf-agent-memory leviathan; do
    modal volume get --force mk-bench-index "$r.tar.gz" "$BENCH/modal/idx-$SHORT/$r.tar.gz"
    rm -rf "$BENCH/repos/$r/.repowise"
    tar -xzf "$BENCH/modal/idx-$SHORT/$r.tar.gz" -C "$BENCH/repos/$r"
    python - "$BENCH/repos/$r/.repowise/config.yaml" <<'PY'
import sys, yaml
p = sys.argv[1]
c = yaml.safe_load(open(p, encoding="utf-8")) or {}
c.setdefault("mcp", {})["always_load"] = True
open(p, "w", encoding="utf-8").write(yaml.safe_dump(c, sort_keys=False))
PY
  done
fi

export REPOWISE_SRC="$RW_WT"
OUT="$BENCH/runs-$SHORT"
MODELS="$MODEL" REPS="$REPS" JOBS="$JOBS" RUNS="$OUT" bash "$HERE/run_grid.sh" 2>&1 | tee -a "$OUT.log"
python "$HERE/analyze.py" "$OUT/$MODEL" --json "$OUT/$MODEL.summary.json" --csv "$OUT/$MODEL.rows.csv"
