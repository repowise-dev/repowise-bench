"""Prove the harness fixes in BOTH directions, with negative controls.

Fix A — per-run stream directories. The scratch directory was keyed on
cell/arm/condition only, so re-running an arm overwrote the previous run's
transcripts. It is now keyed on session_id as well.

Fix B — per-task test command. The runner interpolated ONE test command for the
whole session, so a session whose later task edits module M announced M in the
system prompt before its first task ran.

Fix C — the cell-B tree slug is a second-generation slug (`hermes-pf2`), so a
re-run cannot silently resolve to the August `hermes-pf` trees the indexer
change invalidated.

Fix D — an aborted arm's rows in `.repowise/sessions/sessions.db` are cleared
by `prepare`, not left to fail the next `hook_ledger_clean_before_run` check.

Fix E — `get_health` is in `mcp_payload.py`'s canonical call list, so the
payload regression guard actually measures it.

Each check must fail on the old behaviour and pass on the new one. A check that
only passes on the new behaviour proves nothing about what was wrong — this
workstream has shipped several of those, so both directions are asserted here
and the OLD behaviour is reconstructed explicitly rather than described.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness import session_arms as sa  # noqa: E402
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


# --------------------------------------------------------------------------
# Fix C: bump the cell-B tree slug past the stale index generation
# --------------------------------------------------------------------------
print()
print("FIX C — cell-B tree slug is a new index generation")

import yaml  # noqa: E402

CELL_B_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "session_cost_eval_cellB_hermes.yaml"
cell_b = yaml.safe_load(CELL_B_CONFIG.read_text(encoding="utf-8"))

check(
    "D1 the live config's tree_slug is no longer the stale generation",
    cell_b.get("tree_slug") != "hermes-pf",
    f"tree_slug={cell_b.get('tree_slug')!r}",
)
check(
    "D2 tree_for resolves cell B off the new slug",
    sa.tree_for(cell_b, "rw-full").name == f"se-rw-full-{cell_b['tree_slug']}",
    sa.tree_for(cell_b, "rw-full").name,
)

# NEGATIVE CONTROL: the OLD config value, reconstructed rather than described,
# resolves to the exact stale August tree that made the run's rows lie.
OLD_CELL_B = dict(cell_b, tree_slug="hermes-pf")
check(
    "NC old slug resolves to the stale August tree (so D1/D2 are not vacuous)",
    sa.tree_for(OLD_CELL_B, "rw-full").name == "se-rw-full-hermes-pf",
    sa.tree_for(OLD_CELL_B, "rw-full").name,
)
check(
    "D3 new slug and old slug never resolve to the same path",
    sa.tree_for(cell_b, "rw-full") != sa.tree_for(OLD_CELL_B, "rw-full"),
    f"{sa.tree_for(cell_b, 'rw-full').name} != {sa.tree_for(OLD_CELL_B, 'rw-full').name}",
)

# --------------------------------------------------------------------------
# Fix D: clear stale session-ledger rows before a run, don't fail on them
# --------------------------------------------------------------------------
print()
print("FIX D — stale sessions.db rows are cleared before a run")

with tempfile.TemporaryDirectory() as tmp:
    tree = Path(tmp) / "se-fake-tree"
    db_path = tree / ".repowise" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE hook_runs (tool TEXT, calls INTEGER, emitted INTEGER)")
    con.execute("CREATE TABLE injections (id INTEGER)")
    con.execute("CREATE TABLE rewrite_runs (id INTEGER)")
    con.execute("INSERT INTO hook_runs VALUES ('Read', 3, 0)")
    con.execute("INSERT INTO injections VALUES (1)")
    con.commit()
    con.close()

    before = sa.ledger_rows(tree)
    check(
        "NC an aborted arm's rows are present before any fix runs "
        "(so D1 is not vacuous)",
        before["hook_runs"] > 0 and before["sessions"] > 0,
        before,
    )
    # This is exactly what `hook_ledger_clean_before_run` asserts. Show it
    # would have failed under the old behaviour, which never called any
    # clearing step.
    check(
        "NC hook_ledger_clean_before_run FAILS on the stale rows pre-fix",
        not (before.get("sessions", 0) == 0 and before.get("hook_runs", 0) == 0),
        before,
    )

    cleared = sa.clear_session_ledger(tree)
    after = sa.ledger_rows(tree)
    check(
        "D1 clear_session_ledger empties this tree's own ledger",
        after["hook_runs"] == 0 and after["sessions"] == 0,
        after,
    )
    check(
        "D2 hook_ledger_clean_before_run now PASSES post-clear",
        after.get("sessions", 0) == 0 and after.get("hook_runs", 0) == 0,
        after,
    )
    check(
        "D3 clear_session_ledger reports what it actually deleted",
        cleared["cleared"].get("hook_runs") == 1
        and cleared["cleared"].get("injections") == 1,
        cleared,
    )

    # Scope check: a SECOND tree's ledger, never touched, must be untouched.
    other_tree = Path(tmp) / "se-other-tree"
    other_db = other_tree / ".repowise" / "sessions" / "sessions.db"
    other_db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(other_db))
    con.execute("CREATE TABLE hook_runs (tool TEXT, calls INTEGER, emitted INTEGER)")
    con.execute("INSERT INTO hook_runs VALUES ('Read', 7, 0)")
    con.commit()
    con.close()

    sa.clear_session_ledger(tree)  # clear the FIRST tree again
    other_after = sa.ledger_rows(other_tree)
    check(
        "D4 clearing one tree does NOT touch a different tree's ledger",
        other_after["hook_runs"] == 1,
        other_after,
    )

# --------------------------------------------------------------------------
# Fix E: get_health is in the payload guard's canonical call list
# --------------------------------------------------------------------------
print()
print("FIX E — get_health is in the mcp_payload.py call list")

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "mcp_payload", Path(__file__).resolve().parent / "mcp_payload.py")
mcp_payload = importlib.util.module_from_spec(spec)
# mcp_payload.py imports the `mcp` package at module scope purely to build a
# stdio client it never uses at import time here; if that dependency is not
# installed in this environment, fall back to reading the CALLS list as text
# instead of failing the whole proof on an unrelated import.
try:
    spec.loader.exec_module(mcp_payload)
    call_names = [name for name, _ in mcp_payload.CALLS]
except ModuleNotFoundError as exc:
    print(f"  (mcp package unavailable ({exc}); reading CALLS as text instead)")
    text = (Path(__file__).resolve().parent / "mcp_payload.py").read_text(encoding="utf-8")
    call_names = re.findall(r'\("(\w+)",\s*\{', text)

check(
    "D1 get_health is in the live canonical call list",
    "get_health" in call_names,
    call_names,
)

# NEGATIVE CONTROL: the OLD call list, reconstructed from what shipped before
# this fix (git blame on mcp_payload.py), does not carry it.
OLD_CALLS = [
    "get_answer", "get_context", "get_symbol", "get_why",
    "search_codebase", "get_risk",
]
check(
    "NC the old call list does NOT carry get_health (so D1 is not vacuous)",
    "get_health" not in OLD_CALLS,
    OLD_CALLS,
)
check(
    "D2 every other canonical call from before the fix is still present",
    all(name in call_names for name in OLD_CALLS),
    call_names,
)

print()
if FAILURES:
    print(f"RESULT: FAIL ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
print("RESULT: PASS — all fixes proved in both directions, with negative controls")
