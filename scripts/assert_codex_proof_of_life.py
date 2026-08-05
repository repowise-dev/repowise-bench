"""Per-arm proof of life for a Codex run, and a detector that proves itself first.

A zero from a competitor arm is the most expensive reading this workstream
produces, and it has been produced wrongly twice: graphify scored 0.012 MRR from
a path regex, code-review-graph returned 84/84 `isError` from a tool-name
suffix. Both looked exactly like a tool that cannot retrieve.

So before this file is allowed to read a real row it has to pass `--self-test`,
which exercises the reading on **four sides, two of them mutations**:

    positive    a real exercised row                      -> EXERCISED
    negative    a real bare row                           -> NOT EXERCISED
    mutation A  the positive row with its ledger BLANKED  -> must FLIP
    mutation B  the negative row with an `ok` INJECTED    -> must FLIP

A one-sided control passes while broken. The mutation sides are the ones that
catch a reader that always says EXERCISED, or always says NOT.

--------------------------------------------------------------------------
What is asserted, and the asymmetry between the competitor arms and c0-bare
--------------------------------------------------------------------------
MCP arms (`repowise`, `codegraph`, `graphify`, `serena`, `code-review-graph`),
on every one of that arm's non-error cells:

    served_count > 0                the server advertised a surface
    mcp_isError_count == 0          nothing the agent called came back an error
    answered >= 1                   at least one call the server ANSWERED

ISSUED IS NOT USED. The first Codex repowise cell ever run reported
`arm_exercised: true`, one `get_answer`, `error: null` and a judge score of
9.0/10, and the call itself had returned `user cancelled MCP tool call` in a run
with no user in it. The agent fell back to its shell, answered well, and every
summary field on the row said the arm had been used.

`c0-bare` gets the INVERSE assertion and it is not symmetry. Finding D16 was
that the C0 control was never actually bare, and D16 was found on Claude's
`--strict-mcp-config` path. Codex isolates differently: a bench-owned
`CODEX_HOME` plus per-invocation `-c mcp_servers.…` overrides, with
`--ignore-user-config` explicitly NOT the mechanism (it does not suppress
`$CODEX_HOME/hooks.json`; measured, 7 firings with the flag set). That isolation
has never been verified on a bare arm under Codex. Asserted:

    the bench CODEX_HOME mounts no server     `codex mcp list --json` -> []
    the arm contributes no override           mcp_overrides(arm, None) -> []
    the cell called nothing                   empty ledger, zero isError
    served_count is null                      no server was probed

Codex's `--json` stream has NO INIT EVENT, so "zero servers mounted" is not
readable from a cell the way it is on the Claude side. The config-level view
plus an empty call ledger is the honest substitute and this file prints that
limit rather than hiding it.

Read-only. Exits non-zero if any assertion fails.

Usage:
    python scripts/assert_codex_proof_of_life.py --self-test
    python scripts/assert_codex_proof_of_life.py --results <results_dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH_ROOT))

OK = "ok"
ERR = "error"


# ---------------------------------------------------------------------------
# The reading itself. One function, so the self-test and the real run cannot
# diverge into two readers that agree on the fixtures and differ on the data.
# ---------------------------------------------------------------------------

def answered_calls(row: dict) -> int:
    """Calls the server ANSWERED, summed across servers. Not calls issued."""
    return sum(int(v.get(OK, 0)) for v in (row.get("mcp_per_server") or {}).values())


def read_cell(row: dict) -> tuple[bool, str]:
    """(exercised, reason) for one cell, from the ledger the server wrote.

    Deliberately NOT `row["arm_exercised"]`. That field is computed by the same
    runner whose plumbing is under test here, and a reader that trusts the thing
    it is checking is not a check. It is compared against this reading instead,
    and a disagreement is reported.
    """
    issued = row.get("mcp_tools_issued") or []
    answered = answered_calls(row)
    errored = int(row.get("mcp_isError_count") or 0)
    if not issued:
        return False, "no MCP call issued"
    if answered < 1:
        return False, f"{len(issued)} call(s) issued, server answered NONE ({errored} errored)"
    return True, f"{answered} answered, {errored} errored"


# ---------------------------------------------------------------------------
# Per-arm assertions
# ---------------------------------------------------------------------------

def assert_mcp_arm(name: str, rows: list[dict]) -> list[str]:
    """The four competitors and repowise. Returns a list of failures."""
    fails: list[str] = []
    live = [r for r in rows if not r.get("error")]
    if not live:
        return [f"{name}: no non-error cell at all"]
    for r in live:
        tid = r.get("task_id", "?")
        served = r.get("served_count")
        if not served:
            fails.append(f"{name}/{tid}: served_count={served!r}, the server advertised nothing")
        if int(r.get("mcp_isError_count") or 0) != 0:
            fails.append(f"{name}/{tid}: mcp_isError_count={r.get('mcp_isError_count')}")
        exercised, why = read_cell(r)
        if not exercised:
            fails.append(f"{name}/{tid}: NOT EXERCISED ({why})")
        if bool(r.get("arm_exercised")) != exercised:
            fails.append(
                f"{name}/{tid}: runner says arm_exercised={r.get('arm_exercised')} "
                f"and the ledger says {exercised} ({why})"
            )
    return fails


def assert_bare_arm(name: str, rows: list[dict]) -> list[str]:
    """c0-bare. The INVERSE assertion. See the module docstring."""
    fails: list[str] = []
    live = [r for r in rows if not r.get("error")]
    if not live:
        return [f"{name}: no non-error cell at all"]
    for r in live:
        tid = r.get("task_id", "?")
        if r.get("mcp_tools_issued"):
            fails.append(f"{name}/{tid}: NOT BARE, issued {r['mcp_tools_issued']}")
        if r.get("mcp_per_server"):
            fails.append(f"{name}/{tid}: NOT BARE, ledger {r['mcp_per_server']}")
        if int(r.get("mcp_isError_count") or 0) != 0:
            fails.append(f"{name}/{tid}: NOT BARE, mcp_isError_count={r.get('mcp_isError_count')}")
        if r.get("served_count"):
            fails.append(f"{name}/{tid}: NOT BARE, served_count={r.get('served_count')}")
        if r.get("hook_injections"):
            fails.append(f"{name}/{tid}: hooks INJECTED CONTEXT (finding D16)")
    return fails


def assert_codex_home_is_clean() -> list[str]:
    """No MCP server is mounted by the bench CODEX_HOME's own config.

    The config-level half of the c0-bare inverse assertion. Its limit: Codex's
    stream carries no init event, so this cannot be read off a cell.
    """
    from harness.codex_runner import (  # noqa: PLC0415 - import after sys.path fix
        BENCH_CODEX_HOME, configured_mcp_servers, prepare_codex_home,
    )
    prepare_codex_home()
    try:
        servers = configured_mcp_servers(str(BENCH_CODEX_HOME))
    except Exception as exc:  # noqa: BLE001 - a probe that dies is a failure
        return [f"CODEX_HOME probe failed: {exc}"]
    if servers:
        names = [s.get("name", s) for s in servers]
        return [f"bench CODEX_HOME mounts {len(servers)} server(s): {names}"]
    return []


def assert_bare_arm_adds_no_override(repo_name: str = "django/django") -> list[str]:
    """`mcp_overrides` contributes no `-c mcp_servers.…` for a non-MCP arm.

    Resolved through the same `resolve_arm` the runner uses rather than read off
    the YAML, so this asserts what the launch would actually receive and not
    what the registry says it ought to.
    """
    from harness.arms import arm_tree, resolve_arm  # noqa: PLC0415
    from harness.codex_runner import mcp_overrides  # noqa: PLC0415
    repo_path = BENCH_ROOT / "repos" / repo_name
    tree = arm_tree("c0-bare", repo_path)
    arm = resolve_arm("c0-bare", tree, repo_path, repo_name)
    if arm.uses_mcp:
        return ["c0-bare is registered as uses_mcp=True"]
    # `None` is what the runner passes for an arm with no generated config, and
    # a non-empty return for ANY config path would be the failure.
    extra = mcp_overrides(arm, None) + mcp_overrides(arm, "/nonexistent/.mcp.json")
    if extra:
        return [f"mcp_overrides(c0-bare) returned {extra}"]
    return []


# ---------------------------------------------------------------------------
# The self-test. Four sides, two of them mutations.
# ---------------------------------------------------------------------------

_POSITIVE = {
    "task_id": "fixture_positive", "condition": "repowise", "error": None,
    "served_count": 11, "mcp_isError_count": 0,
    "mcp_tools_issued": ["mcp__repowise__get_answer"],
    "mcp_per_server": {"repowise": {"ok": 1, "error": 0}},
    "arm_exercised": True,
}
_NEGATIVE = {
    "task_id": "fixture_negative", "condition": "c0-bare", "error": None,
    "served_count": None, "mcp_isError_count": 0,
    "mcp_tools_issued": [], "mcp_per_server": {},
    "arm_exercised": False,
}
# The failure mode that started all of this: a call ISSUED, answered by nobody,
# with every summary field on the row still reading as a healthy cell.
_CANCELLED = {
    "task_id": "fixture_cancelled", "condition": "repowise", "error": None,
    "served_count": 11, "mcp_isError_count": 1,
    "mcp_tools_issued": ["mcp__repowise__get_answer"],
    "mcp_per_server": {"repowise": {"ok": 0, "error": 1}},
    "arm_exercised": True,
}


def self_test() -> int:
    sides = []

    ex, why = read_cell(dict(_POSITIVE))
    sides.append(("positive        real exercised row", ex is True, ex, why))

    ex, why = read_cell(dict(_NEGATIVE))
    sides.append(("negative        real bare row", ex is False, ex, why))

    mutated = dict(_POSITIVE)
    mutated["mcp_per_server"] = {}          # blank the ledger
    ex, why = read_cell(mutated)
    sides.append(("mutation A      ledger BLANKED, reading must flip", ex is False, ex, why))

    mutated = dict(_NEGATIVE)
    mutated["mcp_tools_issued"] = ["mcp__x__y"]
    mutated["mcp_per_server"] = {"x": {"ok": 1, "error": 0}}   # inject a call
    ex, why = read_cell(mutated)
    sides.append(("mutation B      call INJECTED, reading must flip", ex is True, ex, why))

    ex, why = read_cell(dict(_CANCELLED))
    sides.append(("regression      issued-but-never-answered", ex is False, ex, why))

    bad = 0
    print("DETECTOR SELF-TEST")
    for label, passed, ex, why in sides:
        if not passed:
            bad += 1
        print(f"  [{'PASS' if passed else 'FAIL'}] {label:<52} read={ex}  ({why})")

    # The bare-arm assertion has its own inverse: it must REJECT a row that is
    # not bare, or it is a check that always passes.
    inv = assert_bare_arm("fixture", [dict(_POSITIVE)])
    ok = bool(inv)
    if not ok:
        bad += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] "
          f"{'inverse         bare assertion REJECTS an exercised row':<52} "
          f"{len(inv)} failure(s) raised")

    mcp_inv = assert_mcp_arm("fixture", [dict(_NEGATIVE)])
    ok = bool(mcp_inv)
    if not ok:
        bad += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] "
          f"{'inverse         MCP assertion REJECTS a bare row':<52} "
          f"{len(mcp_inv)} failure(s) raised")

    print(f"\n{bad} side(s) failed")
    if bad:
        print("THE DETECTOR IS BROKEN. No zero from it may be recorded.")
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# Reading a real run
# ---------------------------------------------------------------------------

BARE_ARMS = {"c0-bare"}


def read_results(results_dir: Path) -> int:
    path = results_dir / "swe_qa.jsonl"
    if not path.exists():
        print(f"no such file: {path}")
        return 1
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        print(f"{path} is empty")
        return 1

    by_arm: dict[str, list[dict]] = {}
    for r in rows:
        by_arm.setdefault(r.get("condition") or r.get("arm") or "?", []).append(r)

    fails: list[str] = []
    print(f"PROOF OF LIFE  {path}   {len(rows)} cell(s), {len(by_arm)} arm(s)\n")
    print(f"{'arm':<20}{'cells':>6}{'err':>5}{'served':>8}{'issued':>8}"
          f"{'answered':>10}{'isError':>9}  verdict")

    for arm in sorted(by_arm):
        rs = by_arm[arm]
        live = [r for r in rs if not r.get("error")]
        errored = len(rs) - len(live)
        served = sorted({r.get("served_count") for r in live})
        issued = sum(len(r.get("mcp_tools_issued") or []) for r in live)
        answered = sum(answered_calls(r) for r in live)
        is_err = sum(int(r.get("mcp_isError_count") or 0) for r in live)

        if arm in BARE_ARMS:
            arm_fails = assert_bare_arm(arm, rs)
            verdict = "BARE (as required)" if not arm_fails else "NOT BARE"
        else:
            arm_fails = assert_mcp_arm(arm, rs)
            verdict = "ALIVE" if not arm_fails else "FAILED"
        fails.extend(arm_fails)

        print(f"{arm:<20}{len(rs):>6}{errored:>5}{str(served):>8}{issued:>8}"
              f"{answered:>10}{is_err:>9}  {verdict}")

    print("\nCONFIG-LEVEL ISOLATION (Codex has no init event; this is the substitute)")
    home_fails = assert_codex_home_is_clean()
    print(f"  [{'PASS' if not home_fails else 'FAIL'}] bench CODEX_HOME mounts no server")
    ovr_fails = assert_bare_arm_adds_no_override()
    print(f"  [{'PASS' if not ovr_fails else 'FAIL'}] c0-bare contributes no -c mcp_servers override")
    fails.extend(home_fails)
    fails.extend(ovr_fails)

    if fails:
        print(f"\n{len(fails)} FAILURE(S):")
        for f in fails:
            print(f"  - {f}")
        print("\nAn arm that fails here is fixed and re-gated, or is declared "
              "UNRUNNABLE UNDER CODEX in the pre-registration. It is never "
              "recorded as a zero.")
        return 1

    print("\nall arms pass")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true",
                    help="prove the detector on four sides, two of them mutations")
    ap.add_argument("--results", type=Path, help="a run's results dir")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.results:
        ap.error("pass --self-test or --results")

    # The detector proves itself before it reads a real row. Not optional.
    if self_test() != 0:
        return 1
    print()
    return read_results(args.results)


if __name__ == "__main__":
    sys.exit(main())
