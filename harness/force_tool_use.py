"""A `Stop` hook that refuses to let a cell finish without using its arm's tool.

WHY THIS EXISTS. Finding E13: under Claude Code the tool under test is mostly
not called at all, so a quality or token A/B there mostly measures two bare
agents against each other. 17 of 30 Claude cells never issued a `ToolSearch`,
which is the gate the deferred MCP schemas put in front of every server, so the
tool was never a CANDIDATE rather than a rejected one. The identical repowise
server, index and questions under Codex: 15 of 15, every cell calling
`get_answer`. Codex therefore has a measured answer to "do these tools save
tokens when they are used" and Claude Code does not, because on Claude Code
they mostly are not.

This closes that gap by construction. It is NOT repowise's shipped hook and has
nothing to do with it: repowise's hook injects context and deliberately does
not force a call. This one forces the call, is written by the benchmark, and is
attached IDENTICALLY to every MCP arm with that arm's own server prefix and
tool names substituted in. A mechanism that only one vendor could use would be
the graphify defect pointing at a competitor again.

MECHANISM, verified against the Claude Code binary at 2.1.224 rather than the
docs (the docs do not name the per-event payload fields):

  * the `Stop` payload carries `stop_hook_active`, `transcript_path` and
    `last_assistant_message`
  * a command hook whose stdout is `{"decision": "block", "reason": ...}`
    blocks the turn from ending, and `reason` comes back to the agent as a
    blocking error, so the loop continues instead of terminating
  * `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` defaults to 8 consecutive blocks, after
    which the CLI overrides the hook and ends the turn anyway

WHAT IT COSTS, stated here because it is a confound and not a detail. Blocking
a turn buys another turn, and another turn costs output tokens. So enforcement
is not free and it pushes every forced arm's token count UP relative to an
unforced one. Three consequences:

  1. It blocks ONLY when the arm's server was never called, so a cell where the
     agent reached for the tool by itself pays nothing.
  2. It honours `stop_hook_active`, so it blocks at most once per turn rather
     than grinding against the cap. One nudge, then the agent's own judgement.
  3. `c0-bare` has no server and therefore cannot be forced. Any run using this
     hook must say how it handled that asymmetry, because "tokens against the
     bare control" now includes the price of the nudge on one side only.

TWO MODES, because the first one measured badly.

  `--mode stop-block`   the Stop hook above. It WORKS: adoption went 0 of 4 to
                        4 of 4 across repowise and a competitor. It is also
                        expensive, because it fires after the agent has written
                        a complete answer, which is then discarded and
                        rewritten: +61% output tokens on the repowise cell it
                        blocked and +127% on the codegraph one.

  `--mode pre-guide`    a PreToolUse hook on Read/Grep/Glob that injects
                        guidance instead of denying anything. It arrives BEFORE
                        an answer exists, so there is nothing to throw away and
                        no second pass to pay for. `additionalContext` on
                        PreToolUse is verified present in the binary's own
                        response schema at 2.1.224, alongside
                        `permissionDecision`, which this mode deliberately does
                        not use: denying the read would dictate the agent's
                        workflow, and the point is to make the tool a candidate,
                        not to make file reading impossible.

Both modes stay silent once the arm's server has been called, and `pre-guide`
is capped so it nudges a bounded number of times rather than on every read. An
uncapped nudge would be noise, would inflate input tokens on every cell, and
would make the arm's own prompt smaller than the harness's commentary on it.

A prompt-only mandate was tried first and DOES NOT WORK: an explicit "REQUIRED,
and not optional" paragraph in the system prompt produced 2 of 6, and both were
a cell that adopts unprompted anyway. The mandate was verified to have arrived
(`coaching_mandatory: true` on the row) before that was concluded.

Usage, as a hook command:

    python harness/force_tool_use.py --mode stop-block --prefix mcp__repowise__ --tools "..."
    python harness/force_tool_use.py --mode pre-guide  --prefix mcp__repowise__ --tools "..."
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _cli_needles(tools: str) -> list[str]:
    """The `Bash` command spellings that count as using the CLI surface.

    The CLI arm's "tool call" is a `Bash` tool_use whose command invokes one of
    the arm's own subcommands. There is no server prefix to match on, so the
    needle is the command line itself, and it has to be tight enough that
    `repowise --version` or a `grep repowise` does not read as adoption. That is
    the negative direction this detector is proved in before it is trusted:
    step 2's pre-registration section 5 makes it a gate, because run 1 lost four
    numbers to detectors that were never shown to fire in both directions.
    """
    return [f"repowise {t.strip()}" for t in tools.split(",") if t.strip()]


def _called_cli(transcript_path: str, tools: str, since_line: int = 0) -> bool:
    """Did this unit of work run one of the arm's subcommands through Bash?

    Same contract as :func:`_called`: ISSUED not answered, scoped to the task by
    `since_line`, and failing OPEN on unreadable input so a hook bug cannot burn
    a cell's turns.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return True
    needles = _cli_needles(tools)
    if not needles:
        return False
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n < since_line:
                    continue
                if '"tool_use"' not in line or '"Bash"' not in line:
                    continue
                if any(needle in line for needle in needles):
                    return True
    except OSError:
        return True
    return False


def _called(transcript_path: str, prefix: str, since_line: int = 0) -> bool:
    """Did this unit of work issue a tool call to the arm's own server?

    Read from the transcript rather than from `last_assistant_message`, because
    the question spans more than one turn and the last message is one turn.
    ISSUED, not answered: this hook decides whether to nudge, and an agent that
    tried and got an error has already stopped needing the nudge. Arm-integrity
    accounting stays where it was, on `arm_exercised`, which is stricter.

    `since_line` is what makes this survive a session-shaped run, and it was
    added on measurement rather than on principle. Scanning the WHOLE transcript
    means one MCP call anywhere in the session silences the nudge for every task
    after it, permanently. Measured on the first enforced arm: the agent called
    `get_answer` once on task 1, and enforcement was then mute for the remaining
    ten tasks. Combined with a per-session nudge cap that gave 154 firings, 2
    injections and 1 call in 11 tasks, and the arm was labelled `enforced` while
    running unenforced.

    Scoped to the current task, the question becomes the one the mechanism was
    calibrated on: has the agent used the server for THIS piece of work.
    """
    p = Path(transcript_path)
    if not p.is_file():
        # No transcript is not evidence of no call. Fail OPEN: a hook that
        # blocks on its own bad input would burn a cell's turns on a bug.
        return True
    needle = f'"{prefix}'
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n < since_line:
                    continue
                if needle in line and '"tool_use"' in line:
                    return True
    except OSError:
        return True
    return False


def _transcript_lines(transcript_path: str) -> int:
    p = Path(transcript_path)
    if not p.is_file():
        return 0
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


_READ_TOOLS = {"Read", "Grep", "Glob"}

# How many of an arm's tools the nudge may name. See the note at the call site.
_NAME_CAP = 3


def _nudges_used(session_id: str, prefix: str, cap: int) -> int:
    """How many times this UNIT OF WORK has already been nudged, incrementing.

    A temp counter keyed on the unit of work and the arm, because a PreToolUse
    hook has no memory and an uncapped nudge would fire on every file read.
    Fails OPEN at the cap on any error, so a bookkeeping problem silences the
    nudge rather than turning it into spam.

    THE UNIT OF WORK IS THE TASK, NOT THE SESSION, AND THAT DISTINCTION WAS
    MEASURED RATHER THAN ASSUMED. The cap was keyed on `session_id` alone, which
    was correct when every cell was its own one-question session: each question
    got up to `cap` nudges. Moved to a session-shaped run, an 11-task session is
    ONE session id, so the whole session got 3 nudges and only 2 emitted.
    Measured on the first enforced arm: **154 hook firings, 2 injections, 1 MCP
    call in 11 tasks.** The enforced condition had silently become the unenforced
    one, and the run would have reported "PreToolUse guidance does not work" when
    what happened is that it was applied twice.

    So the marker includes `BENCH_TASK_ID` when the runner sets it, restoring the
    per-question granularity the mechanism was calibrated at (6 of 6 adoption,
    near-zero token cost, `50-results/layerb-opus-preguide/RESULT.md` section 4).
    Absent that variable the behaviour is unchanged, so every existing caller
    keeps its old semantics.

    This generalises past this harness: a countermeasure tuned on one-question
    cells does not survive being moved to a session, which is the same shape as
    the resident-cost problem this whole run exists to measure, pointed at our
    own instrument.
    """
    import hashlib
    import os
    import tempfile

    unit = os.environ.get("BENCH_TASK_ID", "")
    # hashlib, NOT hash(): every hook fire is a fresh process and PYTHONHASHSEED
    # is randomised per process, so `hash(prefix)` named a different marker file
    # every time and the cap never held. Caught by the self-test below, which
    # fires four times and requires the fourth to be silent.
    key = hashlib.sha1(f"{session_id}\x00{unit}\x00{prefix}".encode()).hexdigest()[:20]
    marker = Path(tempfile.gettempdir()) / f".bench-nudge-{key}"
    try:
        used = int(marker.read_text(encoding="utf-8")) if marker.is_file() else 0
        if used < cap:
            marker.write_text(str(used + 1), encoding="utf-8")
        return used
    except (OSError, ValueError):
        return cap


def _task_baseline(session_id: str, unit: str, transcript_path: str) -> int:
    """Transcript line count when this unit of work started.

    Recorded once, on the unit's first hook fire, so `_called` can ask about
    this task rather than about the whole session. Fails OPEN at 0, which makes
    `_called` scan everything and the nudge go quiet: a bookkeeping problem must
    never turn the nudge into spam.
    """
    import hashlib
    import tempfile

    if not unit:
        return 0
    key = hashlib.sha1(f"base\x00{session_id}\x00{unit}".encode()).hexdigest()[:20]
    marker = Path(tempfile.gettempdir()) / f".bench-base-{key}"
    try:
        if marker.is_file():
            return int(marker.read_text(encoding="utf-8"))
        n = _transcript_lines(transcript_path)
        marker.write_text(str(n), encoding="utf-8")
        return n
    except (OSError, ValueError):
        return 0


def main() -> int:
    argv = sys.argv[1:]
    prefix = tools = ""
    mode = "stop-block"
    surface = "mcp"
    cap = 3
    for i, a in enumerate(argv):
        if a == "--prefix" and i + 1 < len(argv):
            prefix = argv[i + 1]
        elif a == "--tools" and i + 1 < len(argv):
            tools = argv[i + 1]
        elif a == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1]
        elif a == "--surface" and i + 1 < len(argv):
            surface = argv[i + 1]
        elif a == "--max-nudges" and i + 1 < len(argv):
            cap = int(argv[i + 1])
    if not prefix:
        return 0
    if surface not in ("mcp", "cli"):
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    event = payload.get("hook_event_name")
    # AT MOST `_NAME_CAP` TOOLS, and the cap is a fairness fix rather than a
    # cosmetic one. Naming every allowlisted tool gave an arm with a 20-tool
    # surface a 20-name wall where a 1-tool arm got a single name, so the
    # nudge's quality varied with a property of the vendor's surface rather
    # than being the same nudge for everyone. Measured: code-review-graph, at
    # 20 names, took all three nudges and never issued a ToolSearch, while
    # codegraph (1), graphify (7) and serena (11) all called their servers.
    # The cut is the arm's own allowlist order, so which tools survive it is
    # the arm author's choice and not ours. The job of the nudge is to make ONE
    # tool a candidate, not to recite a catalogue.
    _all = [t.strip() for t in tools.split(",") if t.strip()]
    # The CLI arm's capabilities are named as the shell would invoke them. Step
    # 2 holds discovery FIXED across transports, so the two nudges must differ
    # only where the invocation genuinely differs and nowhere else — same verb,
    # same cap, same ordering, same count of names.
    if surface == "cli":
        named = ", ".join(f"repowise {t}" for t in _all[:_NAME_CAP])
    else:
        named = ", ".join(f"{prefix}{t}" for t in _all[:_NAME_CAP])

    if mode == "pre-guide" and event == "PreToolUse":
        if payload.get("tool_name") not in _READ_TOOLS:
            return 0
        _unit = os.environ.get("BENCH_TASK_ID", "")
        _sid = payload.get("session_id", "")
        _tp = payload.get("transcript_path", "")
        _base = _task_baseline(_sid, _unit, _tp)
        _used = (_called_cli(_tp, tools, _base) if surface == "cli"
                 else _called(_tp, prefix, _base))
        if _used:
            return 0
        if _nudges_used(payload.get("session_id", ""), prefix, cap) >= cap:
            return 0
        if surface == "cli":
            guidance = (
                "You are reading source files directly and have not used the "
                "codebase-intelligence commands available for this repository in "
                "this session. Run one of them with Bash and try it "
                "before continuing to explore by hand"
                + (f". Available: {named}." if named else ".")
            )
        else:
            guidance = (
                "You are reading source files directly and have not used the "
                "codebase-intelligence server available for this repository in "
                "this session. Load one of its tools with ToolSearch and try it "
                "before continuing to explore by hand"
                + (f". Available: {named}." if named else ".")
            )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": guidance,
        }}))
        return 0

    if mode != "stop-block" or event != "Stop":
        return 0
    if surface == "cli":
        # The block reason below is written for a server surface ("load them
        # with ToolSearch") and step 2 uses `pre-guide` only. Emitting it to a
        # CLI arm would instruct the agent to do something that arm cannot do,
        # which is worse than not nudging. Refuse LOUDLY rather than nudge
        # wrongly or go quietly no-op.
        print("force_tool_use: --mode stop-block has no CLI wording; use "
              "--mode pre-guide for a CLI-surface arm", file=sys.stderr)
        return 0
    # The binary's own guidance: return success while this is true. Honouring
    # it is what makes this ONE nudge rather than a fight with the block cap.
    if payload.get("stop_hook_active"):
        return 0
    _tp = payload.get("transcript_path", "")
    if (_called_cli(_tp, tools) if surface == "cli" else _called(_tp, prefix)):
        return 0

    reason = (
        "You have not used the codebase-intelligence tools available for this "
        "task. Before finishing, load them with ToolSearch and make at least "
        "one call, then revise your answer if what you learn changes it. "
        f"Available: {named}." if named else
        "You have not used the codebase-intelligence tools available for this "
        "task. Before finishing, load them with ToolSearch and make at least "
        "one call, then revise your answer if what you learn changes it."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
