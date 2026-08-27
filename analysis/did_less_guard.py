"""Flag the arms whose cheapness is a regression, and do it in code.

Two failure modes have each appeared in real cells and both look like wins in a
cost column read on its own:

- **did_less** — cost down, calls down, completion down. One arm scored -16.5%
  cost with zero retrieval calls and 3 of 6 oracles. It was cheap because it
  skipped the work.
- **no_retrieval** — a treated arm that never called the tool at all. That is
  not a measurement of the tool, whatever it cost.

Registered before the run rather than applied afterwards, so the rule cannot be
argued with once the numbers are in. A flagged arm is excluded from any cost
claim and its exclusion is reported, whether or not any flag fired: a detector
whose failure path has never fired is not a detector, so both fire in
``--self-test``.

Usage:
    python analysis/did_less_guard.py results/.../sessions_cellB_scored.jsonl
    python analysis/did_less_guard.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TREATED = "treated"
BARE = "bare"


def _oracle_passed(row: dict):
    """True / False for a graded row, None for a task with no oracle.

    A task with no oracle is not a pass and not a fail. Counting it either way
    moves the completion rate without measuring anything.
    """
    oracle = row.get("oracle")
    if not isinstance(oracle, dict):
        return None
    return oracle.get("passed")


PAIR_RE = re.compile(r"s(\d+)r(\d+)\b")


def _pair_key(row: dict) -> tuple:
    """What makes two arms the same comparison: same cell, session and rep.

    Neither session nor rep is its own column. Both live in the arm slug
    (`cb-bare-s1r1`), so they are read off it rather than assumed present.
    """
    match = PAIR_RE.search(row.get("arm") or "")
    session, rep = (match.group(1), match.group(2)) if match else (None, None)
    return (row.get("cell_id"), session, rep)


def _side(row: dict) -> str:
    """Which side of the pair a row is on.

    The scored file carries both an `arm` slug and a `condition`. `condition`
    is enforcement, not treatment, so treatment is read off the arm slug.
    """
    arm = (row.get("arm") or "").lower()
    return BARE if "bare" in arm else TREATED


def summarise(rows: list[dict]) -> dict:
    """Per-arm totals, keyed by (pair, side)."""
    acc: dict = defaultdict(
        lambda: {
            "cost_usd": 0.0,
            "mcp_calls": 0,
            "tool_calls": 0,
            "oracles_passed": 0,
            "oracles_graded": 0,
            "tasks": 0,
            "arm": None,
        }
    )
    for row in rows:
        # The runner appends a per-arm summary row beside the per-task rows.
        # Summing both double counts the whole session.
        if "task_id" not in row:
            continue
        slot = acc[(_pair_key(row), _side(row))]
        slot["arm"] = row.get("arm")
        slot["tasks"] += 1
        slot["cost_usd"] += float(row.get("cost_usd") or 0)
        slot["mcp_calls"] += int(row.get("mcp_calls") or 0)
        slot["tool_calls"] += int(row.get("tool_calls") or 0)
        passed = _oracle_passed(row)
        if passed is not None:
            slot["oracles_graded"] += 1
            slot["oracles_passed"] += 1 if passed else 0
    for slot in acc.values():
        slot["oracle_pass_rate"] = (
            slot["oracles_passed"] / slot["oracles_graded"]
            if slot["oracles_graded"]
            else None
        )
    return dict(acc)


def flag(rows: list[dict]) -> dict:
    """Apply both registered rules and report every arm, flagged or not."""
    arms = summarise(rows)
    out = []
    for (pair, side), slot in sorted(arms.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        flags = []

        # no_retrieval: a treated arm that never called the tool.
        if side == TREATED and slot["mcp_calls"] == 0:
            flags.append("no_retrieval")

        # did_less: strictly worse completion, bought with less of everything.
        other = arms.get((pair, BARE if side == TREATED else TREATED))
        if other is not None:
            mine, theirs = slot["oracle_pass_rate"], other["oracle_pass_rate"]
            if (
                mine is not None
                and theirs is not None
                and mine < theirs
                and slot["cost_usd"] < other["cost_usd"]
                and (
                    slot["mcp_calls"] < other["mcp_calls"]
                    or slot["tool_calls"] < other["tool_calls"]
                )
            ):
                flags.append("did_less")

        out.append(
            {
                "pair": list(pair),
                "side": side,
                "arm": slot["arm"],
                "tasks": slot["tasks"],
                "cost_usd": round(slot["cost_usd"], 4),
                "mcp_calls": slot["mcp_calls"],
                "tool_calls": slot["tool_calls"],
                "oracles": f"{slot['oracles_passed']}/{slot['oracles_graded']}",
                "oracle_pass_rate": slot["oracle_pass_rate"],
                "flags": flags,
            }
        )

    flagged = [a for a in out if a["flags"]]
    return {
        "arms": out,
        "arms_total": len(out),
        "arms_flagged": len(flagged),
        "flagged": flagged,
        "eligible_for_a_cost_claim": [a for a in out if not a["flags"]],
    }


def _row(cell, session, rep, arm, task, cost, mcp, tool, passed):
    # session/rep are passed for readability only; the pair key reads them
    # back out of the arm slug, exactly as it does on real rows.
    return {
        "cell_id": cell,
        "arm": arm,
        "task_id": task,
        "cost_usd": cost,
        "mcp_calls": mcp,
        "tool_calls": tool,
        "oracle": None if passed is None else {"passed": passed},
    }


def self_test() -> int:
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # did_less fires: treated is cheaper, calls fewer, and completes less.
    rows = [
        _row("cb", 1, 1, "cb-rw-s1r1", "B03", 0.50, 2, 10, False),
        _row("cb", 1, 1, "cb-rw-s1r1", "B04", 0.50, 2, 10, True),
        _row("cb", 1, 1, "cb-bare-s1r1", "B03", 0.90, 0, 20, True),
        _row("cb", 1, 1, "cb-bare-s1r1", "B04", 0.90, 0, 20, True),
    ]
    res = flag(rows)
    treated = [a for a in res["arms"] if a["side"] == TREATED][0]
    check("did_less fires", "did_less" in treated["flags"], True)
    check("did_less excludes the arm", len(res["eligible_for_a_cost_claim"]), 1)

    # Negative control: same cost and call advantage, but completion is EQUAL.
    # A cheaper arm that did the work is a real saving and must NOT be flagged.
    rows_ok = [
        _row("cb", 1, 1, "cb-rw-s1r1", "B03", 0.50, 2, 10, True),
        _row("cb", 1, 1, "cb-rw-s1r1", "B04", 0.50, 2, 10, True),
        _row("cb", 1, 1, "cb-bare-s1r1", "B03", 0.90, 0, 20, True),
        _row("cb", 1, 1, "cb-bare-s1r1", "B04", 0.90, 0, 20, True),
    ]
    res_ok = flag(rows_ok)
    check("did_less silent on equal completion", res_ok["arms_flagged"], 0)
    check("both arms remain eligible", len(res_ok["eligible_for_a_cost_claim"]), 2)

    # Negative control 2: worse completion but MORE expensive is a plain loss,
    # not the did-less signature, and must not borrow its label.
    rows_dear = [
        _row("cb", 1, 1, "cb-rw-s1r1", "B03", 1.50, 9, 30, False),
        _row("cb", 1, 1, "cb-bare-s1r1", "B03", 0.90, 0, 20, True),
    ]
    check("did_less silent when the worse arm cost more", flag(rows_dear)["arms_flagged"], 0)

    # no_retrieval fires on a treated arm with zero MCP calls...
    rows_zero = [_row("cb", 1, 1, "cb-rw-s1r1", "B03", 0.50, 0, 10, True)]
    check("no_retrieval fires", flag(rows_zero)["flagged"][0]["flags"], ["no_retrieval"])
    # ...and never on a bare arm, where zero MCP calls is the whole point.
    rows_bare = [_row("cb", 1, 1, "cb-bare-s1r1", "B03", 0.50, 0, 10, True)]
    check("no_retrieval silent on bare", flag(rows_bare)["arms_flagged"], 0)

    # A task with no oracle is neither a pass nor a fail.
    rows_ungraded = [
        _row("cb", 1, 1, "cb-rw-s1r1", "B01", 0.50, 2, 10, None),
        _row("cb", 1, 1, "cb-rw-s1r1", "B03", 0.50, 2, 10, True),
    ]
    treated_u = [a for a in flag(rows_ungraded)["arms"] if a["side"] == TREATED][0]
    check("ungraded task not counted", treated_u["oracles"], "1/1")

    # A per-arm summary row (no task_id) must not double count the session.
    rows_summary = rows_zero + [{"cell_id": "cb", "arm": "cb-rw-s1r1", "cost_usd": 99.0}]
    check("summary row ignored", flag(rows_summary)["arms"][0]["cost_usd"], 0.5)

    if failures:
        for f in failures:
            print("FAIL", f)
        return 1
    print("self-test: both flags fire, and both stay silent on their negative controls")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="?", help="a scored sessions .jsonl")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.rows:
        ap.error("give a scored sessions .jsonl, or --self-test")

    rows = [
        json.loads(ln)
        for ln in Path(args.rows).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    report = flag(rows)
    print(json.dumps(report, indent=2))
    # A flagged arm is a finding, not a crash: exit 0 so a run can report it.
    print(
        f"\n{report['arms_flagged']} of {report['arms_total']} arms flagged"
        " and excluded from any cost claim",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
