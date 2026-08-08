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

Usage, as a `Stop` hook command:

    python harness/force_tool_use.py --prefix mcp__repowise__ --tools "get_answer,..."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _called(transcript_path: str, prefix: str) -> bool:
    """Did this session issue a tool call to the arm's own server?

    Read from the transcript rather than from `last_assistant_message`, because
    the question is about the whole session and the last message is one turn.
    ISSUED, not answered: this hook decides whether to nudge, and an agent that
    tried and got an error has already stopped needing the nudge. Arm-integrity
    accounting stays where it was, on `arm_exercised`, which is stricter.
    """
    p = Path(transcript_path)
    if not p.is_file():
        # No transcript is not evidence of no call. Fail OPEN: a hook that
        # blocks on its own bad input would burn a cell's turns on a bug.
        return True
    needle = f'"{prefix}'
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if needle in line and '"tool_use"' in line:
                    return True
    except OSError:
        return True
    return False


def main() -> int:
    argv = sys.argv[1:]
    prefix = tools = ""
    for i, a in enumerate(argv):
        if a == "--prefix" and i + 1 < len(argv):
            prefix = argv[i + 1]
        elif a == "--tools" and i + 1 < len(argv):
            tools = argv[i + 1]
    if not prefix:
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    if payload.get("hook_event_name") != "Stop":
        return 0
    # The binary's own guidance: return success while this is true. Honouring
    # it is what makes this ONE nudge rather than a fight with the block cap.
    if payload.get("stop_hook_active"):
        return 0
    if _called(payload.get("transcript_path", ""), prefix):
        return 0

    named = ", ".join(f"{prefix}{t.strip()}" for t in tools.split(",") if t.strip())
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
