"""Prove the CLI-surface enforcement detector fires in BOTH directions.

Run 1 lost four numbers to detectors that were never shown to fire. This is the
newest and least-tested mechanism in step 2, so it is proved before a cent of
agent spend, on synthetic transcripts whose correct verdict is known by
construction. A clean absence proves nothing until the detector is shown to fire.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = Path(r"C:\Users\ragha\Desktop\repowise\repowise-bench\harness")
PY = sys.executable
TOOLS = "ask,context,symbol,why,search,risk"


def tool_use(name: str, inp: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": inp}]}})


def bash(cmd: str) -> str:
    return tool_use("Bash", {"command": cmd})


def read(path: str) -> str:
    return tool_use("Read", {"file_path": path})


def mcp(tool: str) -> str:
    return tool_use(f"mcp__repowise__{tool}", {})


CASES = [
    # (label, transcript lines, surface, expect_nudge)
    ("cli: only unrelated bash -> MUST nudge",
     [read("rich/ansi.py"), bash("git status"), bash("pytest -q"),
      bash("repowise --version")], "cli", True),
    ("cli: a real `repowise search` -> MUST go silent",
     [read("rich/ansi.py"), bash('repowise search "expand width" --limit 5')],
     "cli", False),
    ("cli: `repowise ask` -> MUST go silent",
     [bash('repowise ask "how does from_ansi work?"')], "cli", False),
    ("cli: grep FOR the word repowise -> MUST nudge (not adoption)",
     [bash('grep -rn "repowise" .')], "cli", True),
    ("cli: an MCP call does NOT count as CLI adoption -> MUST nudge",
     [mcp("get_answer")], "cli", True),
    ("mcp: an mcp call -> MUST go silent",
     [mcp("get_answer")], "mcp", False),
    ("mcp: a `repowise search` does NOT count as MCP adoption -> MUST nudge",
     [bash('repowise search "expand width"')], "mcp", True),
    ("mcp: only reads -> MUST nudge", [read("rich/ansi.py")], "mcp", True),
]


def run_case(lines: list[str], surface: str, task_id: str) -> tuple[bool, str]:
    """Returns (nudged, stdout) for a call made DURING the task.

    Two fires, not one. `_task_baseline` records the transcript length at the
    task's first hook fire, so `_called` only sees lines written after the task
    started — correct behaviour (a call in an EARLIER task must not silence this
    one) that a single-fire rig reads as a dead detector. So: fire once on a
    prior-turns transcript to set the baseline, append the case's lines, fire
    again, and report the SECOND verdict.
    """
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "transcript.jsonl"
        tp.write_text(json.dumps({"type": "user", "message": "earlier task"}) + "\n",
                      encoding="utf-8")
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "session_id": f"prove-{task_id}",
            "transcript_path": str(tp),
        })
        env = dict(os.environ)
        env["BENCH_TASK_ID"] = task_id

        def fire() -> str:
            r = subprocess.run(
                [PY, str(HARNESS / "force_tool_use.py"), "--mode", "pre-guide",
                 "--surface", surface, "--prefix", "mcp__repowise__",
                 "--tools", TOOLS],
                input=payload, capture_output=True, text=True, encoding="utf-8",
                env=env)
            return (r.stdout or "").strip()

        fire()  # establishes this task's baseline
        with tp.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        out = fire()
        return bool(out), out


def main() -> int:
    failures = 0
    for i, (label, lines, surface, expect) in enumerate(CASES):
        # A fresh BENCH_TASK_ID per case: the nudge cap and the task baseline
        # are both keyed on it, and reusing one would let case N silence N+1.
        nudged, out = run_case(lines, surface, f"t{i:02d}")
        ok = nudged == expect
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        print(f"        expected nudge={expect}  got nudge={nudged}")
        if nudged and out:
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            print(f"        wording: {ctx[-110:]}")

    # The two nudges must differ ONLY in the invocation clause. Anything else is
    # a discovery asymmetry smuggled in as wording, which is the exact confound
    # step 2 exists to remove.
    _, cli_out = run_case([read("x.py")], "cli", "wordcli")
    _, mcp_out = run_case([read("x.py")], "mcp", "wordmcp")
    cli_txt = json.loads(cli_out)["hookSpecificOutput"]["additionalContext"]
    mcp_txt = json.loads(mcp_out)["hookSpecificOutput"]["additionalContext"]
    print("\n--- wording parity ---")
    print(f"CLI: {cli_txt}")
    print(f"MCP: {mcp_txt}")
    cli_w, mcp_w = cli_txt.split(), mcp_txt.split()
    print(f"word counts: cli={len(cli_w)} mcp={len(mcp_w)} "
          f"delta={len(cli_w) - len(mcp_w)}")
    print(f"names offered: cli={cli_txt.count('repowise ')} "
          f"mcp={mcp_txt.count('mcp__repowise__')}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} detector cases correct")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
