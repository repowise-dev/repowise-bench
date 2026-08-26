"""Oracle for hermes-agent #84289 — execute_code ignores an explicit tool restriction.

The bug
-------
Both execute paths compute the sandbox's importable tools as::

    session_tools = set(enabled_tools) if enabled_tools else set()
    sandbox_tools = frozenset(SANDBOX_ALLOWED_TOOLS & session_tools)
    if not sandbox_tools:
        sandbox_tools = SANDBOX_ALLOWED_TOOLS

so an EMPTY intersection is treated as "unconfigured" and falls back to granting
everything. `enabled_tools=[]` therefore exposes the full sandbox surface, which
is the opposite of what it asks for. Present at TWO sites at the pin: the local
path in ``execute_code`` and the remote path in ``_execute_remote``.

What this asserts, and why here
-------------------------------
The seam is ``generate_hermes_tools_module(list(sandbox_tools))`` — that call's
argument IS the set of tools made importable inside the sandbox, so capturing it
is a behavioural assertion, not an implementation detail. The probe stubs that
function to record its argument and abort before any sandbox, socket or
subprocess work, so the oracle is fast, hermetic, and needs no working execution
environment.

Four properties, per path:

  1. ``enabled_tools=[]``                -> no sandbox tools
  2. ``enabled_tools=["vision_analyze"]`` -> no sandbox tools (nothing sandbox-capable)
  3. ``enabled_tools=["read_file"]``      -> exactly {"read_file"}, not the full set
  4. ``enabled_tools=None``               -> legacy fallback still grants the full set

BOTH PATHS ARE PROBED, AND THAT IS NOT OPTIONAL. The same four lines are copied
into each site. Driving only ``execute_code`` exercises the local site alone, so
a fix patching one and missing the other would be graded a PASS — precisely the
failure this cell already saw live on a two-site defect, where the patch fixed
one site and a second re-introduced the bug downstream.

Property 4 stops the degenerate fix. "Delete the fallback" makes 1-3 pass and
silently breaks every caller passing None; the issue is explicit that "Only
`enabled_tools is None` should trigger the legacy fallback".

THE ISSUE'S OWN REPRO IS WRONG ON ONE STEP, and following it would have produced
an oracle that FAILS A CORRECT FIX. Step 3 of the body says to retry with
``enabled_tools=["web_search"]`` and calls it "a non-sandbox tool list".
``web_search`` is in fact IN ``SANDBOX_ALLOWED_TOOLS`` (measured at the pin:
patch, read_file, search_files, terminal, web_extract, web_search, write_file),
so that intersection is non-empty, no fallback fires, and exposing exactly
``{"web_search"}`` is correct behaviour before and after any fix. This probe
picks a non-sandbox name by checking the live set instead of trusting the body.

Exit 0 = fixed. Exit 1 = bug present, or a degenerate / half-applied fix.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CANDIDATE_NON_SANDBOX = ("vision_analyze", "execute_code", "browser_exec", "__not_a_tool__")


class _Captured(Exception):
    """Raised to stop execution once the tool set has been observed."""

    def __init__(self, tools):
        self.tools = tools
        super().__init__("captured")


def _observe(mod, enabled_tools, path):
    """Set of tools the given execute path would expose for *enabled_tools*."""
    captured = {}

    def fake_generate(tools, *a, **kw):
        captured["tools"] = set(tools or [])
        raise _Captured(captured["tools"])

    real = mod.generate_hermes_tools_module
    mod.generate_hermes_tools_module = fake_generate
    try:
        if path == "remote":
            mod._execute_remote("pass", "oracle-84289", enabled_tools)
        else:
            mod.execute_code(code="pass", task_id="oracle-84289",
                             enabled_tools=enabled_tools)
    except _Captured:
        pass
    except TypeError:
        mod.generate_hermes_tools_module = real
        raise
    except Exception:
        # Any other failure (no sandbox, no socket, no remote env) is fine as
        # long as the gating decision was reached first. The check below
        # reports it rather than passing silently if it was not.
        pass
    finally:
        mod.generate_hermes_tools_module = real

    if "tools" not in captured:
        raise RuntimeError(
            f"the {path} path never reached generate_hermes_tools_module, so the "
            f"gating decision was not observable. The probe's seam has moved."
        )
    return captured["tools"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True, type=Path)
    # The runner passes this whenever a pre-task git-status snapshot exists.
    # Unused here (this probe asserts behaviour, not "nothing else changed"),
    # but it MUST be accepted: argparse exits 2 on an unrecognised argument, the
    # runner reads a non-zero exit as `passed: false`, and every arm is then
    # graded FAIL with a usage string in the detail field regardless of what the
    # agent did. Proving the probe standalone with only --tree does not catch
    # this; prove it through the runner's invocation path.
    ap.add_argument("--baseline-status", default=None)
    args = ap.parse_args()

    tree = args.tree.resolve()
    sys.path.insert(0, str(tree))
    try:
        from tools import code_execution_tool as mod
        from tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL #84289 could not import tools.code_execution_tool from {tree}: {exc!r}")
        return 1

    full = set(SANDBOX_ALLOWED_TOOLS)
    non_sandbox = next((t for t in CANDIDATE_NON_SANDBOX if t not in full), None)
    if non_sandbox is None:
        print("FAIL #84289 probe error: no non-sandbox tool name available")
        return 1

    problems: list[str] = []
    for path in ("local", "remote"):
        try:
            empty = _observe(mod, [], path)
            nonsb = _observe(mod, [non_sandbox], path)
            one = _observe(mod, ["read_file"], path)
            nothing = _observe(mod, None, path)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL #84289 probe error on the {path} path: {exc}")
            return 1

        if empty:
            problems.append(
                f"[{path}] enabled_tools=[] exposed {len(empty)} sandbox tool(s) "
                f"(expected none): {sorted(empty)[:6]}"
            )
        if nonsb:
            problems.append(
                f"[{path}] enabled_tools=[{non_sandbox!r}] exposed {len(nonsb)} "
                f"sandbox tool(s) (expected none): {sorted(nonsb)[:6]}"
            )
        if one != {"read_file"}:
            problems.append(
                f"[{path}] enabled_tools=['read_file'] exposed {sorted(one)}, "
                f"expected exactly ['read_file'] — a restriction must be "
                f"preserved, neither widened nor emptied"
            )
        if nothing != full:
            problems.append(
                f"[{path}] enabled_tools=None exposed {len(nothing)} of {len(full)}; "
                f"the legacy fallback must still grant the full set (the issue: "
                f"'Only enabled_tools is None should trigger the legacy fallback'). "
                f"missing={sorted(full - nothing)[:6]}"
            )

    if problems:
        print("FAIL #84289 present: " + "; ".join(problems))
        return 1
    print("PASS #84289 fixed: both the local and remote paths respect an explicit "
          "restriction, and the None fallback still grants the full set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
