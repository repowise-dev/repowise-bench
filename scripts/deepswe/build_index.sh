#!/bin/bash
# Step 0: rebuild every DeepSWE arm index fresh, at a PINNED repowise build.
#
# Why fresh: the indexes under bakeoff/deepswe/repos were stamped 2026-08-09
# 19:05 by repowise 0.39.0. The indexer has changed heavily since, and all 24
# hermes trees were deleted on 2026-08-27 for exactly this reason.
#
# Why pinned and isolated: the host `repowise` reports 0.46.0 but its
# repowise.core.__file__ resolves into the primary checkout's WORKING TREE - an
# editable install on a live branch, which is not a build anyone can reproduce
# and which could change under a running index. `repowise --version` cannot
# fingerprint a build; core.__file__ can.
#
# Why outside the container: an index built inside the arm image would have no
# OPENAI_API_KEY at build time and would silently write 8-dim mock vectors while
# every health field read clean (D13 / D4).
set -u

RERUN="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun"
REPOS="C:/Users/ragha/Desktop/bakeoff/deepswe/repos"
RW="$RERUN/bin/repowise"

export OPENAI_API_KEY="$(python -c "import json;print(json.load(open(r'C:\\Users\\ragha\\Desktop\\repowise\\provider_config.json'))['keys']['openai'])")"
export DO_NOT_TRACK=1
export REPOWISE_SKIP_EDITOR_SETUP=1
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "FATAL: no OPENAI_API_KEY. A keyless run falls back to 8-dim mock vectors and exits 0."
  exit 1
fi

for repo in "$@"; do
  dir="$REPOS/$repo"
  [ -d "$dir" ] || { echo "FATAL: no repo at $dir"; exit 1; }
  echo "########## INDEX $repo  started=$(date -Iseconds)"
  echo "repo HEAD: $(git -C "$dir" rev-parse HEAD)"
  rm -rf "$dir/.repowise"
  # Flags mirror the 2026-08-09 index config so the only intended difference
  # between that index and this one is the repowise build.
  "$RW" init "$dir" --yes --provider openai --model gpt-5.4-nano --embedder openai \
      --no-claude-md --no-agents --no-codex --no-distill-hook
  echo "########## INDEX $repo exit=$? finished=$(date -Iseconds)"
done
echo "INDEX BUILD COMPLETE"
