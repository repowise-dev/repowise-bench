"""Oracle for hermes-agent #83869: Gemini MALFORMED_FUNCTION_CALL becomes a success.

`agent/gemini_native_adapter.py::_map_gemini_finish_reason` maps four known
Gemini reasons and then falls through to a catch-all:

    return mapping.get(str(reason or "").upper(), "stop")

`MALFORMED_FUNCTION_CALL` is not in the table, so a provider-side function-call
FAILURE is rendered as `finish_reason="stop"` -- byte-identical to a clean
completion. Downstream the turn is marked `completed: true`, `(empty)` is stored
as a normal assistant reply, and the turn lands in the SUCCESSFUL trajectory
file. The stored success record is false.

Exit 0 = FIXED. Non-zero = the bug is present.

WHY THE ASSERTION IS "DISTINGUISHABLE FROM SUCCESS" AND NOT A LITERAL
---------------------------------------------------------------------
The issue says Hermes "should report the provider's function-call failure and
preserve its reason"; it does NOT name a replacement token, and the
OpenAI-compatible vocabulary has no standard one (`stop`, `length`,
`tool_calls`, `content_filter`). Pinning `"error"` would fail a correct fix that
chose `"malformed_function_call"`, and vice versa. So the oracle asserts the
property the defect actually violates: **a malformed function call must not be
reported with the same finish_reason as a clean completion.** That is exactly
the confusion that produces the false success record.

WHAT MUST NOT CHANGE (the #83389 lesson applied forward)
--------------------------------------------------------
"MALFORMED_FUNCTION_CALL != stop" alone grades `return "error"` for EVERY reason
as a pass, which would break every clean turn in the product. So the four
documented mappings are held, a genuine tool call still reports `tool_calls`,
and an unknown reason must still resolve to something rather than raise.

The probe drives the PUBLIC entry point `translate_gemini_response`, not the
private helper, so a fix that stops routing through `_map_gemini_finish_reason`
is still graded on what the adapter actually returns.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from agent.gemini_native_adapter import translate_gemini_response

def resp(reason, parts=None):
    cand = {"content": {"role": "model", "parts": parts or []}, "finishReason": reason}
    return {"candidates": [cand], "usageMetadata": {}}

def fr(r):
    try:
        out = translate_gemini_response(r, "gemini-2.5-pro")
        return {"finish_reason": out.choices[0].finish_reason, "error": None}
    except Exception as exc:
        return {"finish_reason": None, "error": f"{type(exc).__name__}: {exc}"}

TOOL_PART = [{"functionCall": {"name": "write_file", "args": {"path": "x"}}}]

out = {
    # The shape from the issue: Gemini rejected its own function call and the
    # candidate carries no usable parts.
    "malformed":     fr(resp("MALFORMED_FUNCTION_CALL")),
    "stop":          fr(resp("STOP", [{"text": "hello"}])),
    "max_tokens":    fr(resp("MAX_TOKENS", [{"text": "trunc"}])),
    "safety":        fr(resp("SAFETY")),
    "recitation":    fr(resp("RECITATION")),
    "other":         fr(resp("OTHER")),
    "real_toolcall": fr(resp("STOP", TOOL_PART)),
    "unknown":       fr(resp("SOME_FUTURE_REASON")),
    "empty":         fr(resp("")),
}
print("@@ORACLE@@" + json.dumps(out))
"""

# What the mapping table documents today. Held as non-regression, because a
# blanket "everything is an error" fix would otherwise score.
UNCHANGED = {
    "stop": "stop",
    "max_tokens": "length",
    "safety": "content_filter",
    "recitation": "content_filter",
    "other": "stop",
    "real_toolcall": "tool_calls",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--baseline-status", default=None)  # accepted and ignored
    a = ap.parse_args()

    tree = Path(a.tree).resolve()
    r = subprocess.run([sys.executable, "-c", PROBE, str(tree)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(tree))
    line = next((ln for ln in (r.stdout or "").splitlines()
                 if ln.startswith("@@ORACLE@@")), None)
    if line is None:
        print(f"FAIL probe produced no result: rc={r.returncode} "
              f"{(r.stderr or r.stdout or '')[-400:]}")
        return 2
    out = json.loads(line[len("@@ORACLE@@"):])

    # --- what must not change ------------------------------------------------
    for key, expected in UNCHANGED.items():
        got = out[key]
        if got["error"]:
            print(f"FAIL {key} now raises: {got['error']}")
            return 1
        if got["finish_reason"] != expected:
            print(f"FAIL non-regression: {key} maps to "
                  f"{got['finish_reason']!r}, expected {expected!r}. A blanket "
                  f"'treat everything as an error' change is not a fix.")
            return 1
    for key in ("unknown", "empty"):
        if out[key]["error"] or not out[key]["finish_reason"]:
            print(f"FAIL {key} reason no longer resolves to anything: "
                  f"{out[key]}. The catch-all must still produce a value.")
            return 1

    # --- the defect itself ---------------------------------------------------
    mal = out["malformed"]
    if mal["error"]:
        print(f"FAIL MALFORMED_FUNCTION_CALL now raises rather than reporting a "
              f"failure reason: {mal['error']}")
        return 1
    if mal["finish_reason"] == out["stop"]["finish_reason"]:
        print(f"FAIL #83869 present: MALFORMED_FUNCTION_CALL reports "
              f"finish_reason={mal['finish_reason']!r}, identical to a clean "
              f"completion. A provider-side function-call failure is "
              f"indistinguishable from success, which is what writes the false "
              f"record into the successful trajectory file.")
        return 1

    print(f"PASS #83869 fixed: MALFORMED_FUNCTION_CALL reports "
          f"{mal['finish_reason']!r}, distinct from a clean completion "
          f"({out['stop']['finish_reason']!r}); all documented mappings and the "
          f"unknown-reason fallback intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
