#!/bin/bash
# Run ONE DeepSWE smoke cell.
#
# usage: run_cell.sh <task-id> <codex|cc> <bare|rw> <reps>
#
# Reps run through pier's own -k, sequentially (-n 1). Concurrency is NOT
# used: each container is granted 8 GB, this box has ~9 GB free at rest, and
# it has already frozen hard once under memory pressure. Wall clock is the
# cheaper loss.
#
# One cell per invocation, on purpose. The mui Codex run was made unpublishable
# because two runner processes were launched by mistake and raced on shared
# worktrees; within-cell spread came to 15.2% on tokens, larger than most of the
# effects being measured.
#
# Run from bash, NOT PowerShell. pier writes progress to stderr and PowerShell
# 5.1 wraps every stderr line from a native exe in a NativeCommandError; under
# $ErrorActionPreference='Stop' the first such line terminated a LIVE cell on
# 2026-08-09 and orphaned its container.
set -u

TASK="${1:?task id}"
HARNESS="${2:?codex|cc}"
ARM="${3:?bare|rw}"
REPS="${4:?number of reps}"

RERUN="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun"

# pier's _convert_events_to_trajectory opens the agent rollout JSONL with no
# explicit encoding, so Python uses the locale default (cp1252 here) and dies on
# the first non-ASCII byte the agent emits - a curly apostrophe is enough. The
# trajectory is where adoption is read from, so without this the detector goes
# blind while everything else looks fine.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Git Bash rewrites any argument that looks like a POSIX path into a Windows one
# before the process sees it. That silently turned `--artifact /tmp/repowise-
# mcp.err` into a request for C:/Users/.../AppData/Local/Temp/repowise-mcp.err,
# which does not exist in the container, and the artifact came back
# status: "failed" while the cell itself looked entirely healthy. Same class as
# every other defect in this harness: a broken detector that does not announce
# itself.
export MSYS_NO_PATHCONV=1

# The OpenAI key goes to EVERY cell, both arms and both harnesses. The treated
# arm needs it so the MCP server can embed against the real 1536-dim index; the
# control does not use it, but leaving it out of the control would make the arms
# differ in their process environment as well as in the tool, and the comparison
# would stop being attributable to the tool alone.
OPENAI_KEY=$(python -c "import json;print(json.load(open(r'C:\\Users\\ragha\\Desktop\\repowise\\provider_config.json'))['keys']['openai'])")
[ -n "$OPENAI_KEY" ] || { echo "FATAL: no openai key"; exit 1; }

path="$RERUN/tasks/$HARNESS/$ARM/$TASK"
[ -d "$path" ] || { echo "FATAL: no task dir at $path"; exit 1; }

extra=()
case "$HARNESS" in
  codex)
    agent="codex"
    model="gpt-5.6-terra"
    # Use the ChatGPT subscription rather than the metered API. pier defaults to
    # OPENAI_API_KEY auth for the AGENT; without this every Codex rep would be
    # billed, and the whole reason reps are allocated unevenly between the two
    # harnesses is that Codex reps are close to free.
    extra+=(--ae "CODEX_FORCE_AUTH_JSON=1")
    ;;
  cc)
    agent="claude-code"
    model="claude-sonnet-5"
    TOKEN=$(python -c "import json;print(json.load(open(r'C:\\Users\\ragha\\.claude\\.credentials.json',encoding='utf-8'))['claudeAiOauth']['accessToken'])")
    [ -n "$TOKEN" ] || { echo "FATAL: no claude oauth token"; exit 1; }
    extra+=(--ae "CLAUDE_CODE_OAUTH_TOKEN=$TOKEN")
    ;;
  *) echo "FATAL: unknown harness $HARNESS"; exit 1 ;;
esac

if [ "$ARM" = "rw" ]; then
  # The server-side adoption detector, teed to a file by the wrapper baked into
  # the arm image. THE MODEL'S SELF-REPORT OF ITS OWN TOOL SURFACE IS NOT A
  # DETECTOR: asked twice under identical config, gpt-5.6-sol gave different
  # tool lists and twice claimed to have no repowise tools while they were
  # present and callable.
  extra+=(--artifact "/tmp/repowise-mcp.err")

  if [ "$HARNESS" = "codex" ]; then
    # Codex MCP is configured OUTSIDE pier, for three separately-found reasons:
    #   1. pier joins command+args into one string Codex execs as one program
    #      name (defect D3), so the server never starts and nothing logs it
    #   2. Codex SANITIZES the environment of MCP children, so a key on the
    #      container does not reach the server and repowise falls back to mock
    #      vectors against a real index; [mcp_servers.repowise.env] is the fix
    #   3. Codex's non-interactive approval policy is DENY, so every tool needs
    #      approval_mode="approve" or calls return "user cancelled MCP tool call"
    cfg="$RERUN/codex-$TASK.toml"
    python - "$RERUN/codex_repowise.toml" "$cfg" "$OPENAI_KEY" <<'PY'
import sys
src, dst, key = sys.argv[1], sys.argv[2], sys.argv[3]
open(dst, "w", encoding="ascii", newline="\n").write(
    open(src, encoding="utf-8").read().replace("__OPENAI_API_KEY__", key))
PY
    # Client-side cross-check on the Codex side only. Scoped to one module so
    # the log does not drown the cell.
    extra+=(--ak "config_toml_file=$cfg" --ae "RUST_LOG=codex_rmcp_client=info")
  fi
fi

job="$TASK--$HARNESS-$ARM"
echo "########## CELL $job"
echo "task path : $path"
echo "agent     : $agent   model: $model"
echo "started   : $(date -Iseconds)"

pier run \
    -p "$path" \
    --agent "$agent" \
    --model "$model" \
    -o "$RERUN/jobs" \
    --job-name "$job" \
    -n 1 \
    -k "$REPS" \
    --no-delete \
    --ae "OPENAI_API_KEY=$OPENAI_KEY" \
    "${extra[@]}" \
    -y

code=$?
echo "########## exit=$code finished=$(date -Iseconds)"

# The temp config carries the API key.
[ -n "${cfg:-}" ] && [ -f "${cfg:-}" ] && rm -f "$cfg"
exit $code
