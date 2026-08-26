"""Build and gate the step-2 arms: CLI vs MCP with transport as the variable.

Separate from `session_arms.py` on purpose. That module builds run 1's arms,
which ablate the three PRODUCT COMPONENTS (block, hooks, MCP) and resolve their
surface through `configs/arms.yaml`. Step 2 ablates something else entirely —
transport, payload and enforcement — and every arm here carries NO resident
block and NO repowise hooks. Bending the run-1 builder to that shape would
change the module run 1's arms are reproducible from, for no gain.

Pre-registration: `local-stash/competitive-proof/session-cost-eval/
02_STEP2_PREREGISTRATION.md`. Every choice below is written down there first.

Usage:
    python harness/session_arms_s2.py --arm s2-cli-full --prepare
    python harness/session_arms_s2.py --arm s2-cli-full --gate
    python harness/session_arms_s2.py --arm s2-cli-full --emit-cmd --out <jsonl>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[1]
TREES_ROOT = Path(r"C:\Users\ragha\Desktop\bakeoff")
SOURCE_TREE = TREES_ROOT / "se-c0bare-rich"          # any pinned rich checkout
PIN = "46cebbb032f920eb096efbaf23cdc6fe9dd541f7"
BIN_DIR = Path(r"C:\Users\ragha\Desktop\repowise-sessioneval\.venv\Scripts")
BIN = BIN_DIR / "repowise.exe"
BIN_PY = BIN_DIR / "python.exe"
OUT_DIR = (BENCH_ROOT / "results" / "bakeoff_2026_08" / "session-cost-eval"
           / "arms-s2")

# The six capabilities, in ONE list, so the two surfaces cannot drift apart.
# Order is the order both nudges and both coaching blocks name them in; the
# nudge's `_NAME_CAP` takes the first three, so the order is part of the
# treatment and not a formatting detail.
TOOLS = ["get_answer", "get_context", "get_symbol", "get_why",
         "search_codebase", "get_risk"]
COMMANDS = ["ask", "context", "symbol", "why", "search", "risk"]

# (transport, payload, enforced)
ARMS: dict[str, tuple[str, str, bool]] = {
    "s2-cli-full":  ("cli", "full", True),
    "s2-mcp":       ("mcp", "full", True),     # MCP's native payload IS --full
    "s2-cli-trim":  ("cli", "trim", True),
    "s2-cli-unenf": ("cli", "trim", False),
    "s2-mcp-unenf": ("mcp", "full", False),
    "s2-c0bare":    ("none", "none", False),
}

BLOCK_HEADING = "## Codebase Intelligence for"
BLOCK_LOCATIONS = (".claude/CLAUDE.md", "CLAUDE.md", "AGENTS.md")


def shape(arm: str) -> tuple[str, str, bool]:
    if arm not in ARMS:
        raise KeyError(f"unknown arm {arm!r}. Known: {sorted(ARMS)}")
    return ARMS[arm]


def tree_for(arm: str) -> Path:
    return TREES_ROOT / f"se-{arm}-rich"


def _env() -> dict:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1",
                "COLUMNS": "400"})
    return env


def _run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=_env(),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


# ---------------------------------------------------------------------------
# Coaching: ONE source, rendered per surface
# ---------------------------------------------------------------------------

_LEAD = ("A codebase index for this repository is available through {vehicle}. "
         "The capabilities, which you can use from the repository root:")

_TAIL = ("Prefer these over exploring the repository by hand when you need to "
         "find where something lives, understand why it is shaped that way, or "
         "judge the risk of changing it.")

# One description per capability, shared verbatim by both surfaces. If these
# ever differ between arms, the run is measuring the description.
_WHAT = {
    "get_answer": "answer a question about this codebase, with citations",
    "get_context": "triage card for files, modules or symbols",
    "get_symbol": "read one symbol's body with verified line bounds",
    "get_why": "why the code is shaped this way: decisions and rationale",
    "search_codebase": "search the index by keyword, meaning or symbol name",
    "get_risk": "what history says about touching these files",
}

_CLI_ARGS = {
    "ask": '"<question>"',
    "context": "<path or path::Symbol>",
    "symbol": "<path::Symbol>",
    "why": '"<question>"',
    "search": '"<query>" --limit 5',
    "risk": "--target <path>",
}


def coaching_for(arm: str) -> str:
    """The coaching this arm's agent receives, or "" for the bare arm.

    Held IDENTICAL across transports except where the invocation genuinely
    differs (pre-registration D3). Run 1 gave the CLI arm a six-line block and
    the MCP arms nothing at all, so its CLI-vs-MCP contrast varied discovery as
    well as transport. Everything here that is not an invocation is byte-shared.
    """
    transport, payload, _ = shape(arm)
    if transport == "none":
        return ""
    lines = []
    if transport == "cli":
        lines.append(_LEAD.format(
            vehicle="the `repowise` command line tool, which you run with Bash"))
        flag = " --full" if payload == "full" else ""
        for cmd, tool in zip(COMMANDS, TOOLS):
            lines.append(f"  repowise {cmd} {_CLI_ARGS[cmd]}{flag}"
                         f"   {_WHAT[tool]}")
        if payload == "full":
            # The payload switch is part of the treatment and must be stated as
            # plainly as the commands are, or the arm silently runs trimmed.
            lines.append("Always pass --full: it emits the complete payload.")
    else:
        lines.append(_LEAD.format(
            vehicle="an MCP server, whose tools you load with ToolSearch"))
        for tool in TOOLS:
            lines.append(f"  mcp__repowise__{tool}   {_WHAT[tool]}")
    lines.append(_TAIL)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tree, index, settings, mcp config
# ---------------------------------------------------------------------------

def scrub_block(tree: Path) -> list[str]:
    """Remove the resident block from EVERY place the agent could read it.

    `--no-editor-setup` does not suppress it (`--no-claude-md` does), and run 1
    section 5b found that every repowise arm in every prior Layer B run had been
    carrying the block while the control did not. Step 2 ablates transport, so
    no arm here carries it and the removal is asserted rather than assumed.
    """
    removed = []
    for name in BLOCK_LOCATIONS:
        p = tree / name
        if p.exists() and BLOCK_HEADING in p.read_text(encoding="utf-8",
                                                       errors="replace"):
            p.unlink()
            removed.append(name)
    return removed


def block_visible(tree: Path) -> str | None:
    for name in BLOCK_LOCATIONS:
        p = tree / name
        if p.exists() and BLOCK_HEADING in p.read_text(encoding="utf-8",
                                                       errors="replace"):
            return name
    return None


def vector_dim(tree: Path) -> int | None:
    """Vector width off the index itself. 8 is MockEmbedder; D13 is why."""
    lance = tree / ".repowise" / "lancedb"
    if not lance.exists():
        return None
    code = (
        "import lancedb,sys;"
        f"db=lancedb.connect(r'{lance}');"
        "n=db.table_names();"
        "t=db.open_table(n[0]);"
        "f=next(x for x in t.schema if x.name=='vector');"
        "print(f.type.list_size)"
    )
    r = _run([str(BIN_PY), "-c", code])
    try:
        return int((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _hook_run_rows(tree: Path) -> tuple[bool, str]:
    """(ok, detail) for "this tree's ledger records no hook runs".

    Reads `hook_runs`, which is a table this schema demonstrably has. A missing
    DATABASE is a pass (nothing ran); a missing TABLE is a FAIL, because that is
    the shape of run 1's third dead detector, where a query against a
    non-existent table returned 0 through its exception handler and would have
    failed a correctly-built arm with a plausible message.
    """
    import sqlite3
    db = tree / ".repowise" / "sessions" / "sessions.db"
    if not db.exists():
        return True, "no ledger database"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        try:
            n = int(con.execute("SELECT COUNT(*) FROM hook_runs").fetchone()[0])
        except sqlite3.Error as exc:
            return False, f"hook_runs unreadable, cannot assert absence: {exc}"
    finally:
        con.close()
    return n == 0, f"hook_runs rows: {n}"


def build_tree(arm: str, force: bool = False) -> Path:
    tree = tree_for(arm)
    if tree.exists() and not force:
        return tree
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
    r = _run(["git", "clone", "-q", "--no-local", str(SOURCE_TREE), str(tree)])
    if r.returncode != 0:
        raise RuntimeError(f"clone failed: {r.stderr[-300:]}")
    r = _run(["git", "checkout", "-q", "--detach", PIN], cwd=tree)
    if r.returncode != 0:
        raise RuntimeError(f"checkout failed: {r.stderr[-300:]}")
    return tree


def build_index(tree: Path) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        # Measured 2026-08-11: without the key `--embedder openai` falls back to
        # MockEmbedder and writes 8-dim vectors while init's closing summary
        # says nothing that distinguishes it from a real run. A silent mock
        # index is D13, and D13 invalidated a whole run.
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Indexing would silently write an "
            "8-dimension mock index and the arm would be graded as repowise.")
    r = _run([str(BIN), "init", ".", "--no-prose", "--embedder", "openai",
              "--max-file-pages", "0", "--no-workspace", "--no-editor-setup",
              "--yes"], cwd=tree)
    if r.returncode != 0:
        raise RuntimeError(f"index failed: {(r.stderr or r.stdout)[-500:]}")


def write_settings(arm: str, out_dir: Path) -> Path:
    transport, _, enforced = shape(arm)
    hooks: dict = {}
    if enforced and transport in ("cli", "mcp"):
        cmd = (f'"{BENCH_ROOT.parents[0] / ".venv" / "Scripts" / "python.exe"}" '
               f'"{BENCH_ROOT / "harness" / "force_tool_use.py"}" '
               f'--mode pre-guide --surface {transport} '
               f'--prefix mcp__repowise__ '
               f'--tools "{",".join(TOOLS if transport == "mcp" else COMMANDS)}"')
        hooks = {"PreToolUse": [{
            "matcher": "Read|Grep|Glob",
            "hooks": [{"type": "command", "command": cmd, "timeout": 15}],
        }]}
    settings = {"hooks": hooks, "enabledPlugins": {}, "mcpServers": {},
                "alwaysThinkingEnabled": False, "includeCoAuthoredBy": False}
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "settings.json"
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return p


def write_mcp_config(arm: str, tree: Path, out_dir: Path) -> Path:
    """The server, served down to exactly the six capabilities.

    `env` is explicit and complete: Claude Code launches the server with this
    and inherits nothing (finding A9). `OPENAI_API_KEY` is in it because the
    index is embedded with openai and a server without the key queries a
    1536-dim index on full-text alone while reporting itself healthy.
    """
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set, so the MCP arm's server would answer "
            "on full-text alone against a vector index and look healthy (A9).")
    cfg = {"mcpServers": {"repowise": {
        "command": str(BIN),
        "args": ["mcp", str(tree), "--transport", "stdio",
                 "--tools", ",".join(TOOLS)],
        "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1",
                "OPENAI_API_KEY": key},
    }}}
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "repowise.json"
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return p


def prepare(arm: str, force: bool = False) -> dict:
    transport, payload, enforced = shape(arm)
    tree = build_tree(arm, force=force)
    if transport != "none" and not (tree / ".repowise").is_dir():
        build_index(tree)
    removed = scrub_block(tree)

    out_dir = OUT_DIR / arm
    settings = write_settings(arm, out_dir)
    mcp_config = (str(write_mcp_config(arm, tree, out_dir))
                  if transport == "mcp" else None)

    coaching = coaching_for(arm)
    coaching_file = None
    if coaching:
        # NEVER inline: PowerShell splits multi-line coaching on its embedded
        # quotes, and a luckier split runs the arm with silently truncated
        # coaching and reports it as a low-adoption result.
        coaching_file = out_dir / "coaching.txt"
        coaching_file.write_text(coaching, encoding="utf-8")

    return {"arm": arm, "tree": str(tree), "transport": transport,
            "payload": payload, "enforced": enforced,
            "block_removed": removed,
            "settings": str(settings), "mcp_config": mcp_config,
            "coaching_file": str(coaching_file) if coaching_file else None,
            "client_tools": [f"mcp__repowise__{t}" for t in TOOLS]
                            if transport == "mcp" else []}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def gate(arm: str) -> dict:
    transport, payload, enforced = shape(arm)
    tree = tree_for(arm)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("tree_exists", tree.is_dir(), str(tree))
    if tree.is_dir():
        head = (_run(["git", "rev-parse", "HEAD"], cwd=tree).stdout or "").strip()
        check("tree_at_pin", head == PIN, head)
        dirty = (_run(["git", "status", "--porcelain"], cwd=tree).stdout or "")
        # `.repowise/` and `.mcp.json` are ours, not the agent's.
        stray = [ln for ln in dirty.splitlines()
                 if ln.strip() and ".repowise" not in ln and ".mcp.json" not in ln]
        check("tree_clean", not stray, stray[:5])

    # No arm in step 2 carries the resident block. A gate that has never failed
    # has not been tested, so this reads the file rather than trusting the flag.
    check("no_resident_block", block_visible(tree) is None, block_visible(tree))

    indexed = (tree / ".repowise").is_dir()
    check("indexed_iff_treated", indexed == (transport != "none"), indexed)
    if transport != "none":
        dim = vector_dim(tree)
        check("index_vector_dim_1536", dim == 1536, dim)

    # The hook ledger must hold no HOOK RUNS: step 2 installs no repowise hooks,
    # and rows here would mean the product's own PostToolUse/SessionStart hooks
    # got in, which is a component this run is not measuring.
    #
    # Asserting the FILE is absent is wrong and this gate said so on its first
    # run: `init` creates `sessions.db` itself, so file-presence fails a
    # correctly-built arm. The table is `hook_runs` — checked against the real
    # schema, because run 1's third dead detector queried a `savings_events`
    # table that does not exist and read 0 through its exception handler.
    check("no_hook_ledger_rows", *_hook_run_rows(tree))

    out_dir = OUT_DIR / arm
    settings_p = out_dir / "settings.json"
    check("settings_exists", settings_p.is_file(), str(settings_p))
    if settings_p.is_file():
        s = json.loads(settings_p.read_text(encoding="utf-8"))
        has_hook = bool(s.get("hooks", {}).get("PreToolUse"))
        check("enforcement_matches_condition", has_hook == enforced, has_hook)
        if has_hook:
            cmd = s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            check("nudge_surface_correct", f"--surface {transport}" in cmd, cmd[-90:])

    if transport == "mcp":
        p = out_dir / "repowise.json"
        check("mcp_config_exists", p.is_file(), str(p))
        if p.is_file():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            args = cfg["mcpServers"]["repowise"]["args"]
            served = args[args.index("--tools") + 1].split(",") if "--tools" in args else []
            check("serves_exactly_six", sorted(served) == sorted(TOOLS), served)
            check("server_has_openai_key",
                  bool(cfg["mcpServers"]["repowise"]["env"].get("OPENAI_API_KEY")),
                  "present" if cfg["mcpServers"]["repowise"]["env"].get("OPENAI_API_KEY") else "MISSING")

    if transport != "none":
        c = out_dir / "coaching.txt"
        check("coaching_exists", c.is_file(), str(c))
        if c.is_file():
            txt = c.read_text(encoding="utf-8")
            names = COMMANDS if transport == "cli" else TOOLS
            check("coaching_names_all_six",
                  all(n in txt for n in names), [n for n in names if n not in txt])
            # The payload switch is the treatment on the CLI arms. An arm that
            # declares `full` and does not say so runs trimmed and is graded as
            # full, which is the confound this whole run exists to remove.
            if transport == "cli":
                check("coaching_payload_switch_matches",
                      ("--full" in txt) == (payload == "full"),
                      f"declares {payload}, --full in coaching: {'--full' in txt}")
    else:
        check("bare_arm_has_no_coaching",
              not (out_dir / "coaching.txt").exists(), "absent")

    ok = all(c["ok"] for c in checks)
    return {"arm": arm, "pass": ok, "checks": checks}


def emit_cmd(arm: str, manifest: dict, config_path: str, out_path: str,
             model: str) -> list:
    transport, _, enforced = shape(arm)
    cmd = [sys.executable, str(BENCH_ROOT / "harness" / "session_runner.py"),
           "--config", config_path, "--arm", arm,
           "--condition", "enforced" if enforced else "unenforced",
           "--tree", manifest["tree"], "--out", out_path, "--model", model,
           "--settings", manifest["settings"]]
    if transport == "mcp":
        cmd += ["--uses-mcp",
                "--client-tools", ",".join(manifest["client_tools"]),
                "--mcp-config", manifest["mcp_config"]]
    if manifest.get("coaching_file"):
        cmd += ["--coaching-file", manifest["coaching_file"]]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--emit-cmd", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--config",
                    default=str(BENCH_ROOT / "configs"
                                / "session_cost_eval_cellA_rich.yaml"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default="claude-sonnet-5")
    a = ap.parse_args()

    manifest = None
    if a.prepare:
        manifest = prepare(a.arm, force=a.force)
        print(json.dumps(manifest, indent=2))

    if a.gate:
        g = gate(a.arm)
        print(json.dumps(g, indent=2))
        if not g["pass"]:
            print(f"\n!! {a.arm} FAILS ITS GATE. An arm that fails its gate is "
                  f"rebuilt, not graded.", file=sys.stderr)
            return 1

    if a.emit_cmd:
        if manifest is None:
            manifest = prepare(a.arm)
        if not a.out:
            print("--emit-cmd needs --out", file=sys.stderr)
            return 2
        print(json.dumps(emit_cmd(a.arm, manifest, a.config, a.out, a.model)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
