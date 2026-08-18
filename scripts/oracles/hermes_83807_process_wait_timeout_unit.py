"""Oracle for hermes-agent #83807: `process wait` preview renders timeout as `40000s`.

`agent/display.py::build_tool_preview`, process branch:

    if timeout_val and action == "wait":
        parts.append(f"{timeout_val}s")

so a model that supplies `timeout=40000` (ms out of habit) produces the tool row
`wait proc_abc123 40000s`, which a user reads as 40,000 seconds elapsed -- about
eleven hours -- while the actual wait was ~40 s. The string is the timeout ARG
echoed verbatim, not a measured duration.

Exit 0 = FIXED. Non-zero = the bug is present.

WHY THIS ASSERTS A PROPERTY AND NOT A STRING
--------------------------------------------
The issue names TWO acceptable fixes and does not choose between them: omit the
timeout (`wait <sid>`), or label it unambiguously (`wait <sid> timeout=40000s`).
Pinning either exact output would fail the other correct fix. So the oracle
asserts the property both fixes share: **the raw timeout value may not appear as
a bare duration-looking token; if it appears at all it must carry a label.**

WHY IT ALSO ASSERTS WHAT MUST NOT CHANGE
----------------------------------------
The #83389 lesson, applied forward. An oracle asserting only "40000s is gone"
grades `return None` as a pass, and `return None` deletes the whole tool row.
So the preview must still name the action and the session, and previews for
other actions must be untouched. A degenerate fix fails here rather than
scoring.

BOTH DIRECTIONS -- proved before this file was used, see the header block
printed by `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Runs in the tree under test, in a subprocess, because `agent.display` must be
# imported from the ARM's worktree and not from whatever is installed. The
# hermes-agent package is deliberately NOT installed into the bench venv for
# exactly this reason (cell A did the same with rich).
PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from agent.display import build_tool_preview
out = {
    "wait":       build_tool_preview("process", {"action": "wait", "session_id": "proc_abc123", "timeout": 40000}),
    "wait_small": build_tool_preview("process", {"action": "wait", "session_id": "proc_abc123", "timeout": 30}),
    "wait_no_to": build_tool_preview("process", {"action": "wait", "session_id": "proc_abc123"}),
    "kill":       build_tool_preview("process", {"action": "kill", "session_id": "proc_abc123", "timeout": 40000}),
}
print(json.dumps(out))
"""

# A bare duration token: digits immediately followed by `s` at a word boundary,
# with no label attached. `timeout=40000s` does not match because of the lookbehind.
BARE_DURATION = re.compile(r"(?<![=:\w])\b\d{2,}s\b")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    # Accepted and ignored: the runner passes it to every oracle. The suite half
    # is a separate check and uses ">= baseline with zero failures", never an
    # exact test count.
    ap.add_argument("--baseline-status", default=None)
    a = ap.parse_args()

    tree = Path(a.tree).resolve()
    r = subprocess.run([sys.executable, "-c", PROBE, str(tree)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(tree))
    if r.returncode != 0:
        print(f"FAIL probe crashed: {(r.stderr or '')[-300:]}")
        return 2
    try:
        out = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL probe output unreadable: {exc}: {(r.stdout or '')[-200:]!r}")
        return 2

    wait = out.get("wait") or ""

    # --- what must not change: the row still identifies the call -------------
    for key in ("wait", "wait_small", "wait_no_to", "kill"):
        if not (out.get(key) or "").strip():
            print(f"FAIL preview for {key!r} is empty. Removing the tool row is "
                  f"not a fix for a mislabelled field. got={out}")
            return 1
    for key in ("wait", "wait_small", "wait_no_to"):
        v = out[key]
        if "wait" not in v or "proc_abc123" not in v:
            print(f"FAIL preview for {key!r} lost the action or session id: {v!r}")
            return 1
    if out["kill"] != "kill proc_abc123":
        print(f"FAIL non-wait preview changed: {out['kill']!r} "
              f"(expected 'kill proc_abc123' -- the bug is wait-only)")
        return 1

    # --- the defect itself ---------------------------------------------------
    bare = BARE_DURATION.findall(wait)
    if bare:
        print(f"FAIL #83807 present: wait preview renders {bare} as a bare "
              f"duration, indistinguishable from elapsed time. got={wait!r}")
        return 1

    # If the value is still shown, it must be labelled. Both fixes the issue
    # names pass this; nothing that leaves `40000s` reading as elapsed does.
    if "40000" in wait and not re.search(r"timeout\s*[=:]", wait, re.I):
        print(f"FAIL #83807 partially fixed: the raw timeout is still shown "
              f"without a label. got={wait!r}")
        return 1

    print(f"PASS #83807 fixed: wait preview is {wait!r}; no bare duration token, "
          f"row still names action and session, non-wait previews untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
