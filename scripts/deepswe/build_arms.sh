#!/bin/bash
# Build one repowise arm image per task, and the treated task-dir override.
#
# The arm image layers ONLY two things onto the task's own prebuilt image: the
# repowise binary and the prebuilt, commit-matched index. Everything else - the
# toolchain, the dependencies, the /app checkout - stays byte-identical to the
# bare control, so a difference between the arms is attributable to the tool.
#
# The treated task dir is a copy of the corpus task with exactly one field
# changed: environment.docker_image. instruction.md, the tests and the verifier
# are untouched, so the task checksum the runner records still refers to the
# same task.
set -u

RERUN="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun"
REPOS="C:/Users/ragha/Desktop/bakeoff/deepswe/repos"
CORPUS="C:/Users/ragha/Desktop/bakeoff/deep-swe/tasks"
DOCKERFILE="C:/Users/ragha/Desktop/bakeoff/deepswe/rerun/Dockerfile.arm"
REPOWISE_VERSION="0.46.0"

# task-id:repo-dir-name
PAIRS="bandit-interprocedural-taint-checks:bandit
python-statemachine-state-data-scoping:python-statemachine
onedump-dump-encryption-pipeline:onedump"

for pair in $PAIRS; do
  task="${pair%%:*}"
  repo="${pair##*:}"
  echo "########## ARM $task (repo $repo) started=$(date -Iseconds)"

  base=$(grep -m1 '^docker_image = ' "$CORPUS/$task/task.toml" | sed 's/.*"\(.*\)".*/\1/')
  [ -n "$base" ] || { echo "FATAL: no base docker_image for $task"; exit 1; }
  echo "base image: $base"

  ctx="$RERUN/armbuild/$repo"
  rm -rf "$ctx"; mkdir -p "$ctx"
  cp "$DOCKERFILE" "$ctx/Dockerfile"
  # The index is copied, not mounted: it must be baked so the container needs no
  # host path at run time.
  cp -r "$REPOS/$repo/.repowise" "$ctx/index"

  img="deepswe-arm/$repo:repowise"
  docker build --pull -t "$img" \
      --build-arg "BASE=$base" \
      --build-arg "REPOWISE_VERSION=$REPOWISE_VERSION" \
      "$ctx" || { echo "FATAL: docker build failed for $task"; exit 1; }

  # Treated task dir: identical to the corpus task except for the image.
  out="$RERUN/tasks/$task"
  rm -rf "$out"; mkdir -p "$RERUN/tasks"
  cp -r "$CORPUS/$task" "$out"
  python - "$out/task.toml" "$img" <<'PY'
import re, sys
path, image = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
new, n = re.subn(r'^docker_image = ".*"$', f'docker_image = "{image}"', text,
                 count=1, flags=re.M)
if n != 1:
    raise SystemExit(f"FATAL: expected exactly 1 docker_image line in {path}, replaced {n}")
open(path, "w", encoding="utf-8", newline="\n").write(new)
print(f"  task override -> {image}")
PY

  echo "########## ARM $task exit=$? finished=$(date -Iseconds)"
done
echo "ARM BUILD COMPLETE"
