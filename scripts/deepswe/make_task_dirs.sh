#!/bin/bash
# Generate the four task-dir variants the smoke needs, from the corpus task.
#
#   tasks/codex/bare/<task>   corpus image,      no MCP
#   tasks/codex/rw/<task>     arm image,         MCP via --ak config_toml_file
#   tasks/cc/bare/<task>      corpus image,      no MCP
#   tasks/cc/rw/<task>        arm image,         MCP via pier's own registration
#
# The dir NAME stays the task id in every variant, so pier records the same task
# name and the four cells group cleanly.
#
# Codex and Claude Code need different MCP wiring and it is not a preference.
# pier's Codex registration joins `command` and `args` into one string that
# Codex then execs as a single program name (defect D3), so the Codex arm
# bypasses pier entirely and supplies its own config.toml. pier's Claude Code
# registration emits the two keys separately and is correct, so the Claude arm
# uses it. Both point at the same no-argument wrapper baked into the image.
set -eu

RERUN="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun"
CORPUS="C:/Users/ragha/Desktop/bakeoff/deep-swe/tasks"

PAIRS="bandit-interprocedural-taint-checks:bandit
python-statemachine-state-data-scoping:python-statemachine
onedump-dump-encryption-pipeline:onedump"

rm -rf "$RERUN/tasks"
for pair in $PAIRS; do
  task="${pair%%:*}"
  repo="${pair##*:}"
  img="deepswe-arm/$repo:repowise"

  for variant in codex/bare codex/rw cc/bare cc/rw; do
    out="$RERUN/tasks/$variant/$task"
    mkdir -p "$(dirname "$out")"
    cp -r "$CORPUS/$task" "$out"
    python - "$out/task.toml" "$variant" "$img" <<'PY'
import re, sys
path, variant, image = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()

if variant.endswith("/rw"):
    text, n = re.subn(r'^docker_image = ".*"$', f'docker_image = "{image}"', text,
                      count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"FATAL: expected 1 docker_image line in {path}, replaced {n}")

if variant == "cc/rw":
    # pier's Claude Code MCP registration is correct, so use it. The command
    # carries no arguments anyway, which keeps the two harnesses pointed at the
    # identical server invocation.
    block = (
        '\n[[environment.mcp_servers]]\n'
        'name = "repowise"\n'
        'transport = "stdio"\n'
        'command = "/usr/local/bin/repowise-mcp"\n'
        'args = []\n'
    )
    text, n = re.subn(r'^mcp_servers = \[\]$', block.strip(), text, count=1, flags=re.M)
    if n == 0:
        text = text.rstrip("\n") + "\n" + block
open(path, "w", encoding="utf-8", newline="\n").write(text)
PY
  done
  echo "task dirs ready: $task"
done

echo "--- verification: docker_image per variant ---"
for pair in $PAIRS; do
  task="${pair%%:*}"
  for variant in codex/bare codex/rw cc/bare cc/rw; do
    img=$(grep -m1 '^docker_image = ' "$RERUN/tasks/$variant/$task/task.toml" | sed 's/.*"\(.*\)".*/\1/')
    mcp=$(grep -c 'repowise-mcp' "$RERUN/tasks/$variant/$task/task.toml" || true)
    printf '%-46s %-10s mcp=%s  %s\n' "$task" "$variant" "$mcp" "$img"
  done
done

echo "--- verification: instruction.md byte-identical to corpus in all variants ---"
for pair in $PAIRS; do
  task="${pair%%:*}"
  for variant in codex/bare codex/rw cc/bare cc/rw; do
    if ! cmp -s "$CORPUS/$task/instruction.md" "$RERUN/tasks/$variant/$task/instruction.md"; then
      echo "FATAL: instruction.md differs for $task/$variant"
      exit 1
    fi
  done
done
echo "OK: every variant carries the corpus instruction.md unchanged"
