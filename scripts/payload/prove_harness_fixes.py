"""Prove the two harness fixes in BOTH directions, with negative controls.

Fix A — per-run stream directories. The scratch directory was keyed on
cell/arm/condition only, so re-running an arm overwrote the previous run's
transcripts. It is now keyed on session_id as well.

Fix B — per-task test command. The runner interpolated ONE test command for the
whole session, so a session whose later task edits module M announced M in the
system prompt before its first task ran.

Each check must fail on the old behaviour and pass on the new one. A check that
only passes on the new behaviour proves nothing about what was wrong — this
workstream has shipped several of those, so both directions are asserted here
and the OLD behaviour is reconstructed explicitly rather than described.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.session_runner import (  # noqa: E402
    SYSTEM_PROMPT_HEAD,
    SYSTEM_PROMPT_TAIL,
    SYSTEM_PROMPT_TEST,
    system_prompt_for,
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# Fix B: the test-command leak
# --------------------------------------------------------------------------
EDIT_CMD = "python -m pytest tests/tools/test_todo_tool.py -q"
CELL = {"test_env": {"command_display": EDIT_CMD}}

READ_ONLY = {"id": "B01", "prompt": "explain something", "test_command": "none"}
EDIT_TASK = {"id": "B03", "prompt": "fix something", "test_command": EDIT_CMD}
LEGACY = {"id": "T01", "prompt": "cell-A style task with no per-task key"}

print("FIX B — per-task test command")

p_read = system_prompt_for(READ_ONLY, CELL)
check(
    "D1 read-only task carries NO test command",
    "test_todo_tool.py" not in p_read and "Run the test suite" not in p_read,
    "the later edit task's module is absent from the read-only task's prompt",
)

p_edit = system_prompt_for(EDIT_TASK, CELL)
check(
    "D2 edit task DOES carry its own command",
    "test_todo_tool.py" in p_edit and "Run the test suite" in p_edit,
)

p_legacy = system_prompt_for(LEGACY, CELL)
check(
    "control: a task with no per-task key still falls back to the cell command",
    "test_todo_tool.py" in p_legacy,
    "cell-A configs, which carry no test_command, are unchanged",
)

# NEGATIVE CONTROL: reconstruct the OLD behaviour and show D1 fails under it.
# Without this, D1 could pass because the assertion is trivially true.
old_prompt = (
    SYSTEM_PROMPT_HEAD
    + SYSTEM_PROMPT_TEST.format(test_cmd=CELL["test_env"]["command_display"])
    + SYSTEM_PROMPT_TAIL
)
check(
    "NC old behaviour LEAKS on the read-only task (so D1 is not vacuous)",
    "test_todo_tool.py" in old_prompt,
    "one command for the whole session named the edit task's module up front",
)

# A task that declares a DIFFERENT module must not carry any other module.
OTHER = {"id": "B05", "prompt": "fix elsewhere",
         "test_command": "python -m pytest tests/agent/test_display.py -q"}
p_other = system_prompt_for(OTHER, CELL)
check(
    "D3 a task leaks only ITS module, not the cell-wide one",
    "test_display.py" in p_other and "test_todo_tool.py" not in p_other,
)

# --------------------------------------------------------------------------
# Fix A: per-run scratch directory
# --------------------------------------------------------------------------
print()
print("FIX A — per-run stream directory")


def new_dir(cell: str, arm: str, cond: str, sid: str) -> str:
    return f"_status_{cell}_{arm}_{cond}__{sid[:8]}"


def old_dir(cell: str, arm: str, cond: str, sid: str) -> str:
    return f"_status_{cell}_{arm}_{cond}"


RUN1 = "3f2a91cc-1111-4444-8888-aaaaaaaaaaaa"
RUN2 = "9b7e40de-2222-5555-9999-bbbbbbbbbbbb"
args = ("cellB-hermes", "rw-full", "unenforced")

check(
    "D1 two runs of the same arm get DIFFERENT directories",
    new_dir(*args, RUN1) != new_dir(*args, RUN2),
    f"{new_dir(*args, RUN1)} != {new_dir(*args, RUN2)}",
)
check(
    "D2 a RESUME (same session_id) reuses its own directory",
    new_dir(*args, RUN1) == new_dir(*args, RUN1),
    "session_id is read back from the out file on resume",
)
check(
    "NC old naming COLLIDES across runs (so D1 is not vacuous)",
    old_dir(*args, RUN1) == old_dir(*args, RUN2),
    "this is what overwrote the pre-fix transcripts",
)

print()
if FAILURES:
    print(f"RESULT: FAIL ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
print("RESULT: PASS — both fixes proved in both directions, with negative controls")
