"""Arm preparation and the configuration gate for the session-cost eval.

`session_runner.py` drives ONE arm's session and takes its tree, MCP config,
settings file and client tools on the command line. This module is what produces
those, and then proves each arm is what it claims to be.

The proving is the point. This workstream's house speciality is a plausible zero:
`cmd /c echo >>` wrote UTF-16LE so a hook counter read five firings as zero and
printed POSITIVE CONTROL FAILED while the control worked; `--settings` merged
rather than replaced and left the operator's hooks firing; `--ignore-user-config`
left `$CODEX_HOME/hooks.json` firing seven times with the flag set. So no arm
here asserts its configuration from its config file. Every claim is read back
from evidence, and every absence carries a positive control that must fire.

The ablation, which is why "repowise on vs off" is not the design:

    arm         MCP     hooks   resident block
    c0-bare     no      no      no
    rw-full     yes     yes     yes
    rw-mcp      yes     no      no
    rw-block    no      no      yes
    rw-hooks    no      yes     no
    codegraph   yes     no      no

`rw-mcp` vs `codegraph` is the clean tool-quality comparison, because CodeGraph
ships no hooks and no resident block. `rw-full` vs `rw-mcp` is the price of
everything we add on top.

Usage:
    python session_arms.py --config <cell.yaml> --arm rw-mcp --prepare
    python session_arms.py --config <cell.yaml> --arm rw-mcp --gate --phase pre
    python session_arms.py --config <cell.yaml> --arm rw-mcp --emit-cmd
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from harness import arms as arm_registry  # noqa: E402

BENCH_ROOT = Path(__file__).resolve().parents[1]
TREES_ROOT = Path(r"C:\Users\ragha\Desktop\bakeoff")

BLOCK_HEADING = "## Codebase Intelligence for"
BLOCK_END = "<!-- REPOWISE:END -->"

# arm -> (uses_mcp, uses_hooks, uses_block)
ARM_SHAPE: dict[str, tuple[bool, bool, bool]] = {
    "c0-bare":   (False, False, False),
    "rw-full":   (True,  True,  True),
    "rw-mcp":    (True,  False, False),
    "rw-block":  (False, False, True),
    "rw-hooks":  (False, True,  False),
    "codegraph": (True,  False, False),
}

# Which registry arm supplies the server, index recipe and client allowlist.
# The block-only and hooks-only arms carry no server at all, so they take
# c0-bare's agent surface and get their single treatment applied on top. That is
# what makes them ablations rather than flavours of the product.
REGISTRY_ARM = {
    "c0-bare": "c0-bare",
    "rw-full": "repowise",
    "rw-mcp": "repowise",
    "rw-block": "c0-bare",
    "rw-hooks": "c0-bare",
    "codegraph": "codegraph",
}

# Tree BASENAMES, without the cell slug, for arms whose tree on disk is not
# `se-<arm>-<slug>`. Only the control has one, because its tree predates the
# naming rule.
TREE_ALIAS = {"c0-bare": "c0bare"}

# The cell slug is what keeps two cells' trees apart, and it used to be the
# hardcoded literal `rich` inside `tree_for`. That is a live foot-gun the moment
# a second cell exists: `se-c0-bare-r1-rich` and `se-c0-short-rN-rich` are
# ALREADY on disk from cell A, so a cell-B arm reusing one of those names would
# resolve to cell A's tree, `prepare` would skip the worktree add because the
# path already exists, and the run would measure cell A while every row said
# cell B. Nothing downstream would have contradicted it.
#
# Default is `rich` so cell A's configs, which carry no `tree_slug`, resolve to
# byte-identically the same paths they always have.
DEFAULT_TREE_SLUG = "rich"

# Session B repeats whole arms, because every number this workstream has
# produced is n=1 and the trajectory spread has only ever been estimated from a
# single pair of runs (16.5% on cost, RESULT_STEP2.md section 2). A repeat is a
# distinct arm NAME rather than a flag, so it gets its own tree, its own output
# directory and its own row, and nothing downstream has to learn what a repeat
# is. `tree_for` already derives `se-<arm>-rich` for any name not aliased, so
# the reps cannot share a tree with each other or with the original.
#
# The treatment reps are named `rw-pre-rN` / `rw-post-rN` rather than
# `rw-full-rN` because THE BINARY IS PART OF THE TREATMENT and the arm name is
# the only identifier that reaches every knob. `session_arms.py` points at a
# build three separate ways -- `--binary-python` (which build the hooks are read
# out of), `--block-source` (the CLAUDE.md block that build generates), and
# `$REPOWISE_EXE` (the server the agent actually calls) -- and setting two of
# the three leaves an arm running one build's hooks against another build's
# server, with nothing in the output saying so. Deriving all three from the name
# makes that mismatch checkable, and `gate` checks it.
SESSION_B_REPS = 6
SESSION_B_BINARY = {
    "rw-pre": (Path(r"C:\Users\ragha\Desktop\repowise-s3pre"), "282de05a"),
    "rw-post": (Path(r"C:\Users\ragha\Desktop\repowise-s3post"), "2f738ef0"),
}
for _r in range(1, SESSION_B_REPS + 1):
    ARM_SHAPE[f"c0-bare-r{_r}"] = ARM_SHAPE["c0-bare"]
    REGISTRY_ARM[f"c0-bare-r{_r}"] = REGISTRY_ARM["c0-bare"]
    for _state in SESSION_B_BINARY:
        ARM_SHAPE[f"{_state}-r{_r}"] = ARM_SHAPE["rw-full"]
        REGISTRY_ARM[f"{_state}-r{_r}"] = REGISTRY_ARM["rw-full"]


# Session C step 1: purpose-run SHORT (3-task) bare sessions, testing the claim
# in `RESULT_S3.md` section 4.3 that shortening a session cuts its variance,
# which that document recommends and does not test.
#
# A distinct arm NAME rather than a `--short` flag, for the same reason the
# Session B reps are names: `tree_for` then derives a distinct tree per run, and
# the runner's resume key is (cell_id, arm, condition, task_id). Reusing
# `c0-bare-rN` would match the existing 11-task rows, skip all three tasks as
# "already recorded", and append a session summary for work it never did -- a
# zero-cost row that reads as a successful run.
#
# The shape is c0-bare's, unchanged: the ONLY thing that varies between these and
# the 11-task bare runs is how many tasks the session works. Task count is the
# treatment, so nothing else may move.
SESSION_C_SHORT_REPS = 8
for _r in range(1, SESSION_C_SHORT_REPS + 1):
    ARM_SHAPE[f"c0-short-r{_r}"] = ARM_SHAPE["c0-bare"]
    REGISTRY_ARM[f"c0-short-r{_r}"] = REGISTRY_ARM["c0-bare"]


# Cell B SCORED cell: 3 sessions x 2 arms x N reps, on hermes-agent post-#1443.
#
# A distinct arm NAME per (session, rep), for the reason the Session B reps are
# names rather than flags: `tree_for` derives `se-<arm>-<slug>` so every run
# gets its own tree, and the runner's resume key is
# (cell_id, arm, condition, task_id). Sharing a name across reps would match the
# earlier rows, skip every task as "already recorded", and append a summary for
# work that never ran -- a zero-cost row that reads as a successful session.
#
# The session index is in the name too, not only the rep, because the three
# sessions work DIFFERENT task slices against the same tree_slug. Without it,
# `cb-rw-r1` would resolve to one tree for all three sessions and session C2
# would inherit whatever C1's edit tasks left behind.
CELL_B_SESSIONS = 3
CELL_B_REPS = 4
for _s in range(1, CELL_B_SESSIONS + 1):
    for _r in range(1, CELL_B_REPS + 1):
        ARM_SHAPE[f"cb-bare-s{_s}r{_r}"] = ARM_SHAPE["c0-bare"]
        REGISTRY_ARM[f"cb-bare-s{_s}r{_r}"] = REGISTRY_ARM["c0-bare"]
        ARM_SHAPE[f"cb-rw-s{_s}r{_r}"] = ARM_SHAPE["rw-full"]
        REGISTRY_ARM[f"cb-rw-s{_s}r{_r}"] = REGISTRY_ARM["rw-full"]


def session_b_binary(arm: str):
    """(worktree, commit) the arm declares, or None for a non-Session-B arm."""
    for state, spec in SESSION_B_BINARY.items():
        if arm.startswith(f"{state}-r"):
            return spec
    return None


def binary_identity(worktree: Path) -> dict:
    """What this build IS, read out of the build rather than off its label.

    `repowise --version` prints `0.40.0` for BOTH builds under test, so it
    cannot tell them apart. Two things can. `repowise.core.__file__` proves
    which tree the interpreter actually imports, which matters because these
    venvs bridge to the main venv's site-packages and editable-install
    shadowing is live. `_CORE_TOOLS` is a fingerprint of the change under test
    itself: it names `get_symbol` before #1427 and does not after, so the check
    fires in both directions by construction rather than only on a happy path.
    """
    py = worktree / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        return {"error": f"no interpreter at {py}"}
    code = ("import repowise.core;"
            "from repowise.cli.commands.augment_cmd.session_start"
            " import _CORE_TOOLS;"
            "print(repowise.core.__file__);print(_CORE_TOOLS)")
    r = _run([str(py), "-c", code])
    lines = (r.stdout or "").strip().splitlines()
    if len(lines) < 2:
        return {"error": (r.stderr or r.stdout or "")[-300:]}
    return {"core_file": lines[-2], "core_tools": lines[-1]}


def shape(arm: str) -> tuple[bool, bool, bool]:
    if arm not in ARM_SHAPE:
        raise KeyError(f"unknown arm {arm!r}. Known: {sorted(ARM_SHAPE)}")
    return ARM_SHAPE[arm]


def tree_slug(cell: dict) -> str:
    return str(cell.get("tree_slug") or DEFAULT_TREE_SLUG)


def tree_for(cell: dict, arm: str) -> Path:
    return TREES_ROOT / f"se-{TREE_ALIAS.get(arm, arm)}-{tree_slug(cell)}"


def _run(cmd: list, **kw) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1"})
    kw.setdefault("env", env)
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


# ---------------------------------------------------------------------------
# The hooks the product ships TODAY, read out of the binary under test
# ---------------------------------------------------------------------------

def shipped_hooks(binary_python: str) -> dict:
    """repowise's Claude Code hook entries, as `repowise init` would install them.

    Read from the binary under test rather than restated here, and deliberately
    NOT taken from `configs/arms.yaml`: that file's `repowise-hooks` arm still
    carries the pre-#1382 matcher (`Bash|PowerShell|Grep|Glob|...`) and has no
    `PostToolUseFailure` entry at all. Running it would measure a hook surface the
    product no longer installs, and would charge repowise for exactly the shell
    wake-ups #1382 removed.

    A matcher change upstream therefore cannot silently desynchronise this arm,
    which is the same rule the adapter states about itself: a gate that stops
    matching looks exactly like a hook with nothing to say.
    """
    code = (
        "import json;"
        "from repowise.cli.editor_integrations import claude_config as c;"
        "print(json.dumps({"
        "'cmd': c._AUGMENT_HOOK_COMMAND,"
        "'session_start': c._SESSION_START_MATCHER,"
        "'augment': c._AUGMENT_MATCHER,"
        "'failure': c._FAILURE_MATCHER}))"
    )
    r = _run([binary_python, "-c", code])
    if r.returncode != 0:
        raise RuntimeError(
            f"could not read the shipped hook matchers out of the binary under "
            f"test: {r.stderr.strip()[-300:]}")
    m = json.loads(r.stdout.strip().splitlines()[-1])

    def entry(matcher: str) -> dict:
        return {"matcher": matcher,
                "hooks": [{"type": "command", "command": m["cmd"], "timeout": 10}]}

    return {
        "SessionStart": [entry(m["session_start"])],
        "PostToolUse": [entry(m["augment"])],
        "PostToolUseFailure": [entry(m["failure"])],
    }


# ---------------------------------------------------------------------------
# The resident block
# ---------------------------------------------------------------------------

def block_chars(md: Path) -> int:
    """Chars charged to repowise: the block only, never the user's own sections.

    Same boundary `footprint.claude_md_block_chars` uses, including returning 0
    when the heading is absent, so an arm that never opted in must read a zero
    rather than a small number.
    """
    if not md.exists():
        return 0
    text = md.read_text(encoding="utf-8", errors="replace")
    if BLOCK_HEADING not in text:
        return 0
    tail = text[text.index(BLOCK_HEADING):]
    if BLOCK_END in tail:
        return len(tail[:tail.index(BLOCK_END)])
    nxt = re.search(r"\n## (?!Codebase Intelligence for)", tail)
    return len(tail[:nxt.start()]) if nxt else len(tail)


def install_block(tree: Path, block_source: Optional[Path]) -> int:
    """Ensure the arm carries exactly ONE copy of the resident block.

    `repowise init` already writes it to `.claude/CLAUDE.md`, and
    `--no-editor-setup` does NOT suppress that (`--no-claude-md` is the flag that
    does). So the common case here is that the block is already present and the
    right action is to leave it alone. Writing a second copy at the repo root
    would double the very quantity this run exists to price.
    """
    already = block_path(tree)
    if already is not None:
        return block_chars(already)
    if block_source is None:
        raise RuntimeError(
            f"{tree.name} declares the resident block but has none, and no "
            f"--block-source was given to install one.")
    if not block_source.exists():
        raise RuntimeError(
            f"block source {block_source} is missing. It must be GENERATED by "
            f"the binary under test, never hand-written: the whole question is "
            f"what the shipped block costs.")
    md = tree / "CLAUDE.md"
    existing = md.read_text(encoding="utf-8") if md.exists() else ""
    if BLOCK_HEADING not in existing:
        body = block_source.read_text(encoding="utf-8")
        md.write_text((existing.rstrip() + "\n\n" if existing.strip() else "") + body,
                      encoding="utf-8")
    return block_chars(md)


# ---------------------------------------------------------------------------
# Evidence readers
# ---------------------------------------------------------------------------

def ledger_rows(tree: Path) -> dict:
    """Row counts in this tree's hook ledger. Absent database reads as zeros."""
    db = tree / ".repowise" / "sessions" / "sessions.db"
    out = {"db_present": db.exists(), "sessions": 0, "hook_runs": 0,
           "skeleton_or_digest": 0}
    if not db.exists():
        return out
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        def count(sql: str) -> int:
            try:
                return int(con.execute(sql).fetchone()[0])
            except sqlite3.Error:
                return 0
        # There is no `sessions` or `savings_events` table in this schema. The
        # first version of this reader asked for both, got 0 from the exception
        # handler, and would have failed a correctly-built hooks arm for the
        # wrong reason. Third dead detector in this run's own gate, and the
        # reason every absence here is now read from a table that demonstrably
        # exists: hook_runs, injections, rewrite_runs.
        out["hook_runs"] = count("SELECT COUNT(*) FROM hook_runs")
        out["hook_calls"] = count("SELECT COALESCE(SUM(calls),0) FROM hook_runs")
        out["hook_emitted"] = count("SELECT COALESCE(SUM(emitted),0) FROM hook_runs")
        out["injections"] = count("SELECT COUNT(*) FROM injections")
        # `sessions` is what the older schema called it; keep the key so callers
        # that read it still work, populated from what this schema does have.
        out["sessions"] = out["hook_calls"]
        # The two REPLACING surfaces (read_skeleton, search_digest) are 62% and
        # 22% of the ledger's claimed token credit. Whether they FIRE and
        # whether they EMIT are different questions, and conflating them is how
        # an arm gets called "not installed" when it is installed and silent.
        out["read_hook_calls"] = count(
            "SELECT COALESCE(SUM(calls),0) FROM hook_runs WHERE tool='Read'")
        out["read_hook_emitted"] = count(
            "SELECT COALESCE(SUM(emitted),0) FROM hook_runs WHERE tool='Read'")
        out["search_hook_calls"] = count(
            "SELECT COALESCE(SUM(calls),0) FROM hook_runs WHERE tool IN ('Grep','Glob')")
        out["search_hook_emitted"] = count(
            "SELECT COALESCE(SUM(emitted),0) FROM hook_runs WHERE tool IN ('Grep','Glob')")
    finally:
        con.close()
    return out


# Every place the agent can pick the block up. `.claude/CLAUDE.md` is FIRST
# because that is where the generator actually writes it, and reading only the
# repo-root `CLAUDE.md` is how this gate first reported a declared block as
# 0 chars: a detector returning zero for the wrong reason, which is the exact
# failure standing rule 17 exists to catch.
BLOCK_LOCATIONS = (".claude/CLAUDE.md", "CLAUDE.md", "AGENTS.md")


def block_path(tree: Path) -> Optional[Path]:
    """The file the agent would actually read the block out of, if any."""
    for name in BLOCK_LOCATIONS:
        p = tree / name
        if p.exists() and BLOCK_HEADING in p.read_text(encoding="utf-8",
                                                       errors="replace"):
            return p
    return None


def block_visible(tree: Path) -> bool:
    return block_path(tree) is not None


def visible_block_chars(tree: Path) -> int:
    """Chars of the block the agent can see, from wherever it actually lives."""
    p = block_path(tree)
    return block_chars(p) if p else 0


def index_proof(arm_obj, tree: Path) -> dict:
    """Embedder liveness and vector width. D13 is the reason this is a gate."""
    try:
        return arm_registry.index_embedding_proof(arm_obj, tree)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def gate(cell: dict, arm: str, phase: str, block_source: Optional[Path]) -> dict:
    uses_mcp, uses_hooks, uses_block = shape(arm)
    tree = tree_for(cell, arm)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("tree_exists", tree.is_dir(), str(tree))
    if tree.is_dir():
        head = _run(["git", "rev-parse", "HEAD"], cwd=str(tree)).stdout.strip()
        check("tree_pinned", head == cell["pin"],
              {"head": head, "pin": cell["pin"]})

    # --- the resident block, both directions -------------------------------
    visible = block_visible(tree) if tree.is_dir() else False
    chars = visible_block_chars(tree) if tree.is_dir() else 0
    check("block_matches_declaration", visible == uses_block,
          {"visible": visible, "declared": uses_block, "chars": chars})
    if uses_block:
        check("block_non_empty", chars > 0, {"chars": chars})
    else:
        # The positive control for this absence is the rw-block arm in the same
        # run reading a non-zero: a reader that always returns 0 would pass this
        # check for every arm and prove nothing.
        check("block_charges_nothing", chars == 0, {"chars": chars})

    # --- hooks --------------------------------------------------------------
    led = ledger_rows(tree) if tree.is_dir() else {}
    if phase == "post":
        if uses_hooks:
            check("hook_ledger_populated", led.get("hook_calls", 0) > 0, led)
            # ASSERT THE HOOK FIRES; REPORT whether it emits. The pre-registration
            # asked for a `skeleton_served` row or "the replacing surfaces are not
            # actually on", and that conflates two states this ledger separates:
            # installed-and-silent against not-installed. On this cell the Read
            # hook fires and emits nothing, which is a finding about the surface,
            # not a failure of the arm's configuration, and failing the gate on it
            # would delete the finding.
            check("replacing_surfaces_installed",
                  led.get("read_hook_calls", 0) > 0
                  or led.get("search_hook_calls", 0) > 0,
                  {"read_calls": led.get("read_hook_calls"),
                   "search_calls": led.get("search_hook_calls")})
            checks.append({
                "check": "replacing_surfaces_emission_RATE_not_a_gate",
                "ok": True,
                "detail": {"read": f"{led.get('read_hook_emitted')}/{led.get('read_hook_calls')}",
                           "search": f"{led.get('search_hook_emitted')}/{led.get('search_hook_calls')}",
                           "total": f"{led.get('hook_emitted')}/{led.get('hook_calls')}"},
            })
        else:
            check("hook_ledger_empty",
                  led.get("sessions", 0) == 0 and led.get("hook_runs", 0) == 0,
                  led)
    else:
        check("hook_ledger_clean_before_run",
              led.get("sessions", 0) == 0 and led.get("hook_runs", 0) == 0, led)

    # --- MCP ----------------------------------------------------------------
    if uses_mcp and tree.is_dir():
        arm_obj = arm_registry.resolve_arm(REGISTRY_ARM[arm], tree=tree,
                                           repo_path=tree,
                                           repo_name=cell["repo"])
        if REGISTRY_ARM[arm] == "repowise":
            proof = index_proof(arm_obj, tree)
            check("index_present", bool(proof) and "error" not in proof, proof)
            dim = proof.get("index_vector_dim") if isinstance(proof, dict) else None
            check("embedder_live_1536", dim == 1536,
                  {"index_vector_dim": dim,
                   "note": "D13: every index before 2026-08-05 was mock at 8 dims"})
        else:
            # `index_embedding_proof` reads repowise's own store and returns {}
            # for anyone else, so asserting it on a competitor scores a healthy
            # arm as dead. A competitor's index is asserted on its own artifact.
            art = next((tree / d for d in (".codegraph", ".code-review-graph",
                                           "graphify-out", ".serena")
                        if (tree / d).is_dir()), None)
            check("competitor_index_present", art is not None,
                  {"artifact_dir": str(art) if art else None})

    # --- the binary under test, all three knobs at once ---------------------
    spec = session_b_binary(arm)
    if spec is not None:
        worktree, commit = spec
        head = _run(["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(worktree)).stdout.strip()
        check("binary_worktree_at_declared_commit", head == commit,
              {"head": head, "declared": commit, "worktree": str(worktree)})

        ident = binary_identity(worktree)
        check("binary_imports_its_own_tree",
              str(worktree).lower() in ident.get("core_file", "").lower(),
              ident.get("core_file") or ident.get("error"))

        # The fingerprint of the change under test. `pre` MUST name get_symbol
        # and `post` MUST NOT, so a swapped binary fails here even if every
        # path above happens to line up.
        expect_symbol = arm.startswith("rw-pre-")
        has_symbol = "get_symbol" in ident.get("core_tools", "")
        check("binary_advertising_state_matches_arm",
              has_symbol == expect_symbol,
              {"core_tools": ident.get("core_tools"),
               "expected_get_symbol": expect_symbol})

        # The server is the one knob the agent actually calls, and it is set by
        # environment rather than by argument, so it is the easiest of the three
        # to leave pointing at the previous arm's build.
        exe = os.environ.get("REPOWISE_EXE", "")
        check("server_exe_matches_arm",
              bool(exe) and str(worktree).lower() in exe.lower(),
              {"REPOWISE_EXE": exe or "UNSET"})

        # And the block, which is generated by one build and passed as a path.
        check("block_source_matches_arm",
              block_source is not None
              and str(worktree).lower() in str(block_source).lower(),
              {"block_source": str(block_source) if block_source else None})

    return {"arm": arm, "phase": phase, "tree": str(tree),
            "passed": all(c["ok"] for c in checks), "checks": checks}


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------

def prepare(cell: dict, arm: str, block_source: Optional[Path],
            condition: str, binary_python: Optional[str],
            force: bool = False) -> dict:
    uses_mcp, uses_hooks, uses_block = shape(arm)
    src = (BENCH_ROOT / cell["repo_local"]).resolve()
    tree = tree_for(cell, arm)

    if tree.exists() and force:
        _run(["git", "-C", str(src), "worktree", "remove", "--force", str(tree)])
        arm_registry._safe_rmtree(tree)
    if not tree.exists():
        r = _run(["git", "-C", str(src), "worktree", "add", "--detach",
                  str(tree), cell["pin"]])
        if r.returncode != 0:
            raise RuntimeError(f"worktree add failed: {r.stderr.strip()[-300:]}")

    out_dir = BENCH_ROOT / "results" / "bakeoff_2026_08" / "session-cost-eval" / "arms" / arm
    out_dir.mkdir(parents=True, exist_ok=True)

    arm_obj = arm_registry.resolve_arm(REGISTRY_ARM[arm], tree=tree,
                                       repo_path=tree, repo_name=cell["repo"])

    # Hooks are a property of THIS arm, applied on top of whatever the registry
    # arm declares (which for c0-bare is nothing).
    arm_obj.hooks = shipped_hooks(binary_python) if uses_hooks else {}

    chars = install_block(tree, block_source) if uses_block else 0

    settings = arm_registry.generate_settings(
        arm_obj, out_dir,
        force_tool_use=("pre-guide" if (condition == "enforced" and uses_mcp)
                        else False))

    mcp_config = None
    if uses_mcp:
        mcp_config = str(arm_registry.generate_mcp_config(arm_obj, out_dir))

    return {
        "arm": arm, "tree": str(tree), "condition": condition,
        "uses_mcp": uses_mcp, "uses_hooks": uses_hooks, "uses_block": uses_block,
        "block_chars": chars,
        "settings": str(settings),
        "mcp_config": mcp_config,
        "client_tools": arm_obj.client_tools,
        "coaching": arm_obj.resolved_coaching("neutral") if uses_mcp else "",
        # Recorded so the result file can state the mechanism and its cost. The
        # Stop block is forbidden in this run: it works and costs +61% to +127%
        # output tokens, which corrupts the only column this run can resolve.
        "enforcement": ("pre-guide" if (condition == "enforced" and uses_mcp)
                        else "none"),
    }


def emit_cmd(cell: dict, manifest: dict, config_path: str, out_path: str,
             model: str) -> list:
    cmd = [sys.executable, str(BENCH_ROOT / "harness" / "session_runner.py"),
           "--config", config_path, "--arm", manifest["arm"],
           "--condition", manifest["condition"], "--tree", manifest["tree"],
           "--out", out_path, "--model", model]
    if manifest["uses_mcp"]:
        cmd += ["--uses-mcp",
                "--client-tools", ",".join(manifest["client_tools"] or []),
                "--coaching", manifest["coaching"] or ""]
        if manifest["mcp_config"]:
            cmd += ["--mcp-config", manifest["mcp_config"]]
    if manifest["settings"]:
        cmd += ["--settings", manifest["settings"]]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--arm", required=True, choices=sorted(ARM_SHAPE))
    ap.add_argument("--condition", default="enforced",
                    choices=["enforced", "unenforced"])
    ap.add_argument("--block-source", default=None,
                    help="file holding the CLAUDE.md block GENERATED by the "
                         "binary under test")
    ap.add_argument("--binary-python", default=None,
                    help="interpreter of the repowise worktree under test")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--out", default=None, help="runner jsonl path, for --emit-cmd")
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--phase", default="pre", choices=["pre", "post"])
    ap.add_argument("--emit-cmd", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    cell = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    block_source = Path(a.block_source) if a.block_source else None
    result: dict = {}

    if a.prepare:
        result["prepare"] = prepare(cell, a.arm, block_source, a.condition,
                                    a.binary_python, force=a.force)
    if a.gate:
        result["gate"] = gate(cell, a.arm, a.phase, block_source)
    if a.emit_cmd:
        man = result.get("prepare") or prepare(cell, a.arm, block_source,
                                               a.condition, a.binary_python)
        result["cmd"] = emit_cmd(cell, man, a.config,
                                 a.out or "sessions.jsonl", a.model)

    print(json.dumps(result, indent=2))
    if a.gate and not result["gate"]["passed"]:
        # An arm that fails its gate is rebuilt, not graded.
        for c in result["gate"]["checks"]:
            if not c["ok"]:
                print(f"GATE FAILED: {c['check']} :: {c['detail']}",
                      file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
