"""The arm registry: what a benchmarked tool IS, as data rather than as code.

`swe_qa_runner.py` used to dispatch on a boolean::

    repowise_enabled = bool(condition.get("repowise_enabled"))

and everything downstream — the tool allowlist, the served-tool allowlist, the
system prompt, the MCP config, the index build — branched on it. That harness
could drive exactly three arms (C0 bare, repowise-lean, repowise-full) and no
fourth arm could exist without editing five places in a 1,600-line file. Four of
the seven arms this bake-off is about had no plumbing at all, and the gap
between a 3-arm and a 7-arm pilot was measured at nine dollars: the blocker was
never money, it was that "which tool are we running" was not a value.

Here it is a value. An arm is a record loaded from `configs/arms.yaml` (plus any
`configs/arms.d/*.yaml` overlay, so a third party adds an arm without editing a
tracked file), carrying:

    launch      how to start its MCP server, and what tool surface to pin
    tools       what the agent may call
    coaching    what the agent is told
    index       how to build whatever it needs built, once per (arm, repo)
    activate    the setup calls whose absence produced a clean zero
    warm        the call whose result is thrown away
    hooks       what fires around the agent, DECLARED rather than inherited
    teardown    what to stop afterwards

Three properties this buys, in order of how much they cost to learn:

1. **An arm cannot be silently half-configured.** Serena needs
   `activate_project` even with `--project` on its command line; crg needs its
   graph embedded AND provider/model repeated per call; every crg tool carries a
   `_tool` suffix. Each of those, when missing, produced a clean 0.000 that
   looks exactly like a tool that cannot retrieve. They are fields now, so an
   arm that omits one omits it visibly.

2. **The comparison can be made fair on purpose.** Set `prompt_style: neutral`
   in the experiment config and every arm gets the same prompt with only the
   server and tool names substituted. The legacy repowise prompt tells the agent
   which tool to call first, when to trust confidence, and not to verify — no
   competitor was ever offered coaching like that, and a run using it is
   measuring our prompt engineering as much as our tool. Both modes exist; the
   run must say which it used.

3. **Hooks stop being ambient.** See `env_isolation_probe.py`: the operator's
   own `~/.claude/settings.json` fires inside every cell, and two of its hooks
   are repowise's own, so the C0 BARE control was being handed injected repowise
   context before its first turn. Hooks are a legitimate part of the repowise
   product surface and an arm may declare them here. What no arm may do is
   inherit them by accident.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

BENCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARMS_FILE = BENCH_ROOT / "configs" / "arms.yaml"
DEFAULT_ARMS_DIR = BENCH_ROOT / "configs" / "arms.d"

# Pinned settings for every agent subprocess. See env_isolation_probe.py.
BENCH_SETTINGS = BENCH_ROOT / "configs" / "bench_settings.json"
BENCH_CLAUDE_HOME = BENCH_ROOT / ".claude_bench_home"

# Arm worktrees. Deliberately OUTSIDE the repowise checkout: a
# `.repowise-workspace.yaml` anywhere above a repo flips the server into
# workspace mode and changes the served tool surface (11 tools single-repo, 13
# in workspace mode), and `repowise mcp` has no `--no-workspace` to opt out
# (finding D1b).
DEFAULT_TREES = Path(os.environ.get("BAKEOFF_TREES") or (Path.home() / "Desktop" / "bakeoff"))


# ---------------------------------------------------------------------------
# Coaching
# ---------------------------------------------------------------------------

# Legacy prompts, referenced from YAML as `builtin:<NAME>`. They live here and
# not in the YAML so they stay byte-identical to what the published flask48
# numbers were produced with; restating them as YAML text would risk a
# whitespace difference nobody would ever find.
BUILTIN_COACHING: dict[str, str] = {}


def register_builtin_coaching(name: str, text: str) -> None:
    BUILTIN_COACHING[name] = text


# The fair prompt. Identical for every arm, naming only what exists.
#
# The last paragraph is load-bearing and its first version was not. That version
# ended "use the server when it helps you answer, and read source files when it
# does not", and the very first cell run under it produced this:
#
#     served_tools    4  (get_answer, get_context, get_symbol, search_codebase)
#     mcp_tools_issued []
#     num_tool_calls  9  (all Grep and Read)
#     error           null
#     judge score     7.8 / 10
#     cost            $0.42
#
# A clean, well-scored, fully-paid-for cell in which the arm under test was
# never used. That is a bare agent wearing the arm's name — finding D1's exact
# shape, arrived at from the opposite direction — and at pilot scale it is 70
# cells and $30 of measuring nothing.
#
# So every arm is now instructed to TRY its server first and fall back freely.
# This is still neutral: the instruction is identical across arms and only the
# server and tool names are substituted. It has a stated cost, which belongs on
# any table built from it: **it measures the tool when used, not whether an
# agent spontaneously reaches for it.** Those are different questions and the
# second one is also worth answering — see the RESULT.md note on deferred tool
# discovery — but it cannot be answered in the same run as the first, because a
# cell that does not call the tool contributes nothing to either arm's mean.
#
# The "answer from it and stop" clause was added after the rung 6 pilot, and it
# is a fairness fix rather than a favour. Without it the agent called the
# server, got a usable answer, and then re-derived the same answer with Grep and
# Read anyway: the repowise arms averaged MORE tool calls (7.8-8.5) and MORE
# turns (8.8-9.5) than the bare control (6.2 / 7.2) while reading FEWER files.
# An arm that pays for a tool and then does the untooled work regardless cannot
# win on cost by construction, whatever the tool is worth, and that is a
# property of the instruction rather than of the tool.
#
# It is applied identically to every arm, including the ones we did not write,
# and it is the instruction a real user's agent operates under: nobody installs
# a codebase-intelligence server in order to ignore its output. The legacy
# repowise prompt said the same thing in stronger terms ("CITE THE ANSWER
# DIRECTLY. Do NOT call Grep, Read, get_context, or get_symbol to verify"), and
# no competitor was ever given the equivalent — which is precisely why it now
# lives in the shared template rather than in ours.
NEUTRAL_COACHING = """\
You have the {server} MCP server available alongside your standard tools. It
provides codebase intelligence for the repository in your current directory.

Available tools:
{tool_lines}

These tools are loaded on demand and are NOT in your initial tool list: call
ToolSearch with the tool name first (e.g. ToolSearch "{first_tool}") to load the
schema, then call the tool. Do this silently.

Start with the {server} tools before reading source files. If what they return
already answers the question, answer from it and stop — do not re-verify it with
Grep or Read. If it does not answer the question, fall back to Read, Grep and
Glob without hesitation; an unhelpful answer from the server is a reason to stop
using it, not a reason to retry it.
"""


def neutral_coaching(arm: "Arm") -> str:
    if not arm.mcp:
        return ""
    tools = arm.client_tools or []
    lines = "\n".join(f"- {t}" for t in tools) or "- (the server's advertised tools)"
    return NEUTRAL_COACHING.format(
        server=arm.mcp.get("server_name", arm.name),
        tool_lines=lines,
        first_tool=tools[0] if tools else "",
    )


# ---------------------------------------------------------------------------
# Template substitution
# ---------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{([a-z_]+)\}")


def _npm_bin() -> str:
    return str(Path(os.environ.get("APPDATA", str(Path.home()))) / "npm")


def _uv_bin() -> str:
    return str(Path.home() / ".local" / "bin")


def repowise_exe() -> str:
    """The repowise binary under test.

    `REPOWISE_EXE` wins, and it is the reason this function exists. The venv's
    `repowise.exe` is an EDITABLE install from the shared checkout, so it runs
    whatever branch that checkout happens to be on — on 2026-08-03 that was
    `fix/punch-card-utc-copy`, entirely unrelated work. The default binary would
    have silently measured a feature branch and published it as main. The
    resolved path is stamped on every cell so no row can be published without
    saying which binary produced it.
    """
    env_exe = os.environ.get("REPOWISE_EXE")
    if env_exe and Path(env_exe).exists():
        return env_exe
    root = Path(os.environ.get("REPOWISE_ROOT") or BENCH_ROOT.parent)
    cand = root / ".venv" / "Scripts" / "repowise.exe"
    return str(cand)


def _substitute(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        def repl(m):
            key = m.group(1)
            return str(ctx[key]) if key in ctx else m.group(0)
        return _TEMPLATE_RE.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, ctx) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

@dataclass
class Arm:
    name: str
    description: str = ""
    isolation: str = "worktree"
    mcp: Optional[dict] = None
    client_tools: list = field(default_factory=list)
    coaching: Optional[str] = None
    index: Optional[dict] = None
    shares_index_with: Optional[str] = None
    activate: list = field(default_factory=list)
    warm: Optional[dict] = None
    hooks: dict = field(default_factory=dict)
    teardown: Optional[list] = None
    raw: dict = field(default_factory=dict)

    # -- derived --------------------------------------------------------
    @property
    def uses_mcp(self) -> bool:
        return bool(self.mcp)

    @property
    def server_name(self) -> str:
        return (self.mcp or {}).get("server_name", self.name)

    @property
    def tree_owner(self) -> str:
        """Whose worktree this arm queries. Sharing a tree is how `repowise-lean`
        reuses `repowise-full`'s index without rebuilding it, and it is the ONLY
        sanctioned way for two arms to touch one tree. Two unrelated arms sharing
        a checkout is finding E3: each indexes its predecessors' output and the
        bias favours whoever ran first."""
        return self.shares_index_with or self.name

    def resolved_coaching(self, style: str = "arm") -> str:
        if not self.uses_mcp:
            return ""
        if style == "neutral":
            return neutral_coaching(self)
        text = self.coaching or ""
        if text.startswith("builtin:"):
            key = text.split(":", 1)[1]
            if key not in BUILTIN_COACHING:
                raise KeyError(
                    f"arm {self.name!r} references builtin coaching {key!r}, "
                    f"which is not registered. Known: {sorted(BUILTIN_COACHING)}"
                )
            return BUILTIN_COACHING[key]
        return text

    def provenance(self) -> dict:
        """What has to travel with every cell this arm produces."""
        return {
            "arm": self.name,
            "server_name": self.server_name if self.uses_mcp else None,
            "launch_command": (self.mcp or {}).get("command"),
            "launch_args": (self.mcp or {}).get("args"),
            "served_tools": (self.mcp or {}).get("served_tools"),
            "client_tools": list(self.client_tools),
            "index": self.index,
            "shares_index_with": self.shares_index_with,
            "hooks_declared": sorted(self.hooks.keys()),
            "isolation": self.isolation,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_registry(arms_file: Optional[str] = None,
                  arms_dir: Optional[str] = None) -> dict[str, dict]:
    """Raw arm definitions, before templating. Overlays merge over the base."""
    path = Path(arms_file) if arms_file else DEFAULT_ARMS_FILE
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = doc.get("defaults") or {}
    arms = {name: _deep_merge(defaults, spec or {})
            for name, spec in (doc.get("arms") or {}).items()}

    overlay_dir = Path(arms_dir) if arms_dir else DEFAULT_ARMS_DIR
    if overlay_dir.exists():
        for extra in sorted(overlay_dir.glob("*.yaml")):
            edoc = yaml.safe_load(extra.read_text(encoding="utf-8")) or {}
            edefaults = _deep_merge(defaults, edoc.get("defaults") or {})
            for name, spec in (edoc.get("arms") or {}).items():
                merged = _deep_merge(edefaults, spec or {})
                arms[name] = _deep_merge(arms.get(name, {}), merged)
    return arms


def resolve_arm(name: str, tree: Path, repo_path: Path, repo_name: str,
                arms_file: Optional[str] = None,
                arms_dir: Optional[str] = None) -> Arm:
    """One arm, with every `{placeholder}` bound for this (arm, repo) cell."""
    registry = load_registry(arms_file, arms_dir)
    if name not in registry:
        raise KeyError(
            f"unknown arm {name!r}. Known arms: {sorted(registry)}. "
            f"Add one by appending a block to configs/arms.yaml or dropping a "
            f"file into configs/arms.d/ — no Python change is needed."
        )
    ctx = {
        "tree": str(tree),
        "repo": str(repo_path),
        "repo_name": repo_name,
        "repowise_exe": repowise_exe(),
        "bench_root": str(BENCH_ROOT),
        "npm_bin": _npm_bin(),
        "uv_bin": _uv_bin(),
    }
    spec = _substitute(registry[name], ctx)
    return Arm(
        name=name,
        description=spec.get("description", ""),
        isolation=spec.get("isolation", "worktree"),
        mcp=spec.get("mcp"),
        client_tools=list(spec.get("client_tools") or []),
        coaching=spec.get("coaching"),
        index=spec.get("index"),
        shares_index_with=spec.get("shares_index_with"),
        activate=list(spec.get("activate") or []),
        warm=spec.get("warm"),
        hooks=dict(spec.get("hooks") or {}),
        teardown=spec.get("teardown"),
        raw=spec,
    )


def arm_names(arms_file: Optional[str] = None,
              arms_dir: Optional[str] = None) -> list[str]:
    return sorted(load_registry(arms_file, arms_dir))


# ---------------------------------------------------------------------------
# Per-arm worktrees (finding E3)
# ---------------------------------------------------------------------------

def _safe_rmtree(path: Path, retries: int = 3) -> None:
    for i in range(retries):
        if not path.exists():
            return
        try:
            shutil.rmtree(str(path))
            return
        except OSError:
            if i == retries - 1:
                raise
            time.sleep(0.5 * (i + 1))


def arm_tree(arm_name: str, repo_path: Path, trees_root: Optional[Path] = None,
             owner: Optional[str] = None) -> Path:
    """The worktree this arm indexes and queries, created if absent.

    One tree per arm, never a shared checkout. Every arm writes its index into a
    dotdir inside the repo, so a shared tree means each arm indexes its
    predecessors' output and the bias favours whoever ran first — which, in
    every run this workstream has done, was us (finding E3). Rung 4's
    alternative fix (clear every artifact dir per cell) is right for a timed
    build and fatal here, because it destroys the indexes the query stage needs.

    A git worktree shares the object store, so this is cheap in disk and instant
    to create, and untracked files (`.repowise/`, `.codegraph/`, `CLAUDE.md`)
    are physically absent rather than merely disallowed. That is also what makes
    C0 a real control: `--disallowed-tools mcp__*` stops the agent calling a
    server, it does not stop it reading a previous arm's wiki off disk.
    """
    root = trees_root or DEFAULT_TREES
    key = owner or arm_name
    dest = root / f"lb-{key}-{repo_path.parent.name}-{repo_path.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)

    src_head = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()

    if dest.exists():
        wt_head = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        if wt_head and wt_head == src_head:
            return dest
        # HEAD moved under us. Tear down and recut rather than index a tree
        # whose commit no cell can name.
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(dest)],
            capture_output=True, text=True,
        )
        _safe_rmtree(dest)

    subprocess.run(["git", "-C", str(repo_path), "worktree", "prune"],
                   capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "--detach",
         str(dest), src_head],
        check=True, capture_output=True, text=True,
    )
    return dest


def scrub_tree(tree: Path, keep: tuple[str, ...] = ()) -> list[str]:
    """Remove other arms' artifacts from a tree. Only for C0, which is defined
    by their absence; every other arm needs its own dotdir to survive."""
    removed = []
    for leak in (".repowise", ".codegraph", ".code-review-graph", "graphify-out",
                 ".serena", ".mcp.json", "CLAUDE.md"):
        if leak in keep:
            continue
        p = tree / leak
        if p.exists():
            if p.is_dir():
                _safe_rmtree(p)
            else:
                p.unlink()
            removed.append(leak)
    return removed


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

def build_index(arm: Arm, tree: Path, logs_dir: Path,
                extra_env: Optional[dict] = None) -> dict:
    """Run this arm's index command in its own tree. One row of evidence back.

    Returns a dict that is stored on the cell, not a bool, because "the build
    exited 0" has repeatedly not meant "the index is usable". `repowise init
    --embedder openai` without the key in the BUILD environment falls back to
    MockEmbedder, writes 8-dimensional vectors, exits 0, and looks complete
    (finding D13). The caller checks `index_vector_dim`.
    """
    if arm.index is None:
        return {"arm": arm.name, "skipped": "no-index-by-design", "seconds": 0.0}
    if arm.index.get("builtin"):
        return {"arm": arm.name, "builtin": arm.index["builtin"], "seconds": 0.0}

    argv = list(arm.index["command"])
    env = {
        **os.environ,
        "DO_NOT_TRACK": "1",
        "REPOWISE_SKIP_EDITOR_SETUP": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        **(arm.index.get("env") or {}),
        **(extra_env or {}),
    }
    logs_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    p = subprocess.run(
        argv, cwd=str(tree), env=env, capture_output=True, text=True,
        errors="replace", timeout=arm.index.get("timeout_seconds", 10800),
    )
    elapsed = round(time.time() - t0, 1)
    (logs_dir / f"build__{arm.name}__{tree.name}.log").write_text(
        f"$ {' '.join(argv)}\n\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}\n",
        encoding="utf-8",
    )
    row = {"arm": arm.name, "rc": p.returncode, "seconds": elapsed,
           "argv": argv, "tree": str(tree)}
    row.update(index_embedding_proof(arm, tree))
    return row


def index_embedding_proof(arm: Arm, tree: Path) -> dict:
    """What embedder actually wrote this index, read off the index itself.

    `embedder_degraded` in a query response answers "can this process resolve an
    embedder now", which is a claim about the query side and was green through
    every run finding D13 invalidated. The claim nobody checked is about the
    INDEX, it is answerable in one line, and the answer is a number: MockEmbedder
    writes 8 dimensions and a real embedder writes hundreds. An index reading 8
    is not a measurement of repowise and must not be graded as one.
    """
    if not arm.name.startswith("repowise"):
        return {}
    lance = tree / ".repowise" / "lancedb"
    if not lance.exists():
        return {"index_vector_dim": None, "index_embedder_mock": None}
    try:
        import lancedb
        db = lancedb.connect(str(lance))
        names = list(db.table_names())
        if not names:
            return {"index_vector_dim": None, "index_embedder_mock": None}
        table = db.open_table(names[0])
        f = next(x for x in table.schema if x.name == "vector")
        dim = int(f.type.list_size)
    except Exception as exc:  # noqa: BLE001
        return {"index_vector_dim": None, "index_embedder_probe_error": repr(exc)}
    return {"index_vector_dim": dim, "index_embedder_mock": dim <= 16}


# ---------------------------------------------------------------------------
# MCP config
# ---------------------------------------------------------------------------

def generate_mcp_config(arm: Arm, out_dir: Path, extra_env: Optional[dict] = None) -> Path:
    """Write the `.mcp.json` Claude Code mounts for this arm.

    Named by (arm, tree) so two arms over the same tree — `repowise-full` and
    `repowise-lean` differ only in `served_tools` — get distinct server launches
    and neither can pick up the other's file.

    The server's `env` block is explicit and complete: Claude Code launches the
    server with exactly this and nothing is inherited. That is finding A9. The
    index is built with `--embedder openai`, which reads the key from
    `provider_config.json`; the server reads `OPENAI_API_KEY` from its own
    environment; nothing bridged the two, so a server launched the obvious way
    queried an openai-embedded index with mock vectors and answered on full-text
    alone while reporting itself healthy.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    mcp = arm.mcp or {}
    args = list(mcp.get("args") or [])
    if mcp.get("served_tools"):
        args = args + ["--tools", mcp["served_tools"]]

    server_env: dict[str, str] = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "DO_NOT_TRACK": "1",
        "REPOWISE_SKIP_EDITOR_SETUP": "1",
    }
    for key in mcp.get("env_passthrough") or []:
        val = os.environ.get(key)
        if val:
            server_env[key] = val
    server_env.update(extra_env or {})

    safe = re.sub(r"[^A-Za-z0-9]+", "-", arm.name)
    path = out_dir / f"{safe}.json"
    path.write_text(json.dumps({
        "mcpServers": {
            arm.server_name: {
                "command": mcp.get("command"),
                "args": args,
                "env": server_env,
            }
        }
    }, indent=2), encoding="utf-8")
    return path.resolve()


# ---------------------------------------------------------------------------
# Settings / hooks, per arm
# ---------------------------------------------------------------------------

def generate_settings(arm: Arm, out_dir: Path) -> Path:
    """The `--settings` file for this arm: pinned empty, plus what it declares.

    Everything an arm does NOT declare is switched off, which is the opposite of
    the default. See `env_isolation_probe.py` for the measurement: on this
    machine an unpinned cell fires 8 hooks and receives two injected context
    blocks, one of which advertises repowise's MCP tools — into the C0 arm.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": copy.deepcopy(arm.hooks),
        "enabledPlugins": {},
        "mcpServers": {},
        "alwaysThinkingEnabled": False,
        "includeCoAuthoredBy": False,
    }
    safe = re.sub(r"[^A-Za-z0-9]+", "-", arm.name)
    path = out_dir / f"settings__{safe}.json"
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path.resolve()


def prepare_claude_home() -> Path:
    """An isolated CLAUDE_CONFIG_DIR for benchmark subprocesses.

    `--settings` MERGES, so it cannot remove a hook the operator's settings
    define; measured, it dropped one injection of two and left all 8 plugins
    loaded. `CLAUDE_CONFIG_DIR` replaces the whole config root and does remove
    them: 0 hooks, 0 plugins. Measured 2026-08-03, `env_isolation_probe.py`.

    The credentials are HARD-LINKED, not copied — same file, same volume, no
    second copy of a secret on disk, and this directory is gitignored. If the
    OAuth token is refreshed the link may break, so the link is recreated on
    every run and `swe_qa_runner` treats an unauthenticated reply as a hard
    error rather than as an answer. It has to: an unauthenticated `claude -p`
    exits 0 with `{"subtype": "success", "result": "Not logged in ...",
    "total_cost_usd": 0}`, which the harness would otherwise record as a
    completed cell with a cheap wrong answer, for every cell, in every arm.
    """
    BENCH_CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BENCH_SETTINGS, BENCH_CLAUDE_HOME / "settings.json")

    src = Path(os.environ.get("CLAUDE_CREDENTIALS")
               or Path.home() / ".claude" / ".credentials.json")
    dst = BENCH_CLAUDE_HOME / ".credentials.json"
    if src.exists():
        relink = True
        if dst.exists():
            try:
                relink = dst.stat().st_ino != src.stat().st_ino
            except OSError:
                relink = True
        if relink:
            if dst.exists():
                dst.unlink()
            try:
                os.link(str(src), str(dst))
            except OSError:
                # Cross-volume or no permission. Fall back to the operator's own
                # config dir rather than run unauthenticated; the caller's
                # isolation assertion will then fail loudly instead of a run
                # quietly measuring a contaminated environment.
                return Path.home() / ".claude"
    return BENCH_CLAUDE_HOME
