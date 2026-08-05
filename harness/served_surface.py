"""What each arm's server actually advertises, read off the server.

The reason this exists is a defect we committed against a competitor, in our
own favour, and did not notice until the numbers were already in a table.

At rung 6 the four-arm run allowlisted `graphify` ONE tool out of the ten its
server advertises, while `repowise-lean` got all four of its and `codegraph`
got its one. graphify was then exercised on 4 of 10 cells and its row was
excluded. That asymmetry does not prove it caused the non-use — finding A30
says an agent will ignore a tool it was not told to reach for, whatever the
tool is — but **an arm we handicapped is not an arm we measured**, and it is
the same shape as every entry on the measurement-traps list.

The allowlist rule is stated once, in `configs/arms.yaml`, and is:

    every arm gets its FULL served surface, minus tools irrelevant to the
    task shape (for SWE-QA: read-only code question answering).

"irrelevant to the task shape" is the only judgement call and it has to be
made in public, per tool, in the registry, with a reason. graphify's
`list_prs` / `triage_prs` / `get_pr_impact` are PR-review tools: there is no
PR in a SWE-QA cell and no diff to impact. Excluding them is not the same kind
of act as excluding a retrieval tool, and the difference is what this comment
exists to make checkable by someone who disagrees.

Usage::

    python -m harness.served_surface --arm graphify --arm serena \
        --repo repos/django/django

It starts each arm's server exactly as a cell will, lists what comes back, and
diffs that against what the arm allowlists. It builds no index: a server
advertises its tool schemas before it has anything to answer with, which is
precisely why an unbuilt arm scores as a bad arm rather than as an error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import arms as arm_registry  # noqa: E402
from harness.swe_qa_runner import probe_arm_server  # noqa: E402


def surface(arm_name: str, repo_path: Path, timeout: float = 120.0) -> dict:
    tree = arm_registry.arm_tree(arm_name, repo_path)
    arm = arm_registry.resolve_arm(
        arm_name, tree=tree, repo_path=repo_path,
        repo_name=f"{repo_path.parent.name}/{repo_path.name}",
    )
    if not arm.uses_mcp:
        return {"arm": arm_name, "status": "no-mcp-by-design"}

    cfg = arm_registry.generate_mcp_config(
        arm, arm_registry.BENCH_ROOT / "mcp_configs")
    row = probe_arm_server(arm, str(cfg), timeout=timeout)
    served = set(row.get("served_tools") or [])
    allowed = {t.split("__")[-1] for t in arm.client_tools}
    row["allowlisted"] = sorted(allowed)
    row["served_not_allowlisted"] = sorted(served - allowed)
    row["allowlisted_not_served"] = sorted(allowed - served)
    row["tree"] = str(tree)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--repo", default="repos/django/django")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    rows = []
    for name in args.arm:
        row = surface(name, repo, timeout=args.timeout)
        rows.append(row)
        print(f"\n=== {name} ===")
        print(f"  status           {row.get('status')}")
        if row.get("error"):
            print(f"  error            {row['error'][:300]}")
        print(f"  served ({row.get('served_count')}): {row.get('served_tools')}")
        print(f"  allowlisted      {row.get('allowlisted')}")
        print(f"  served, NOT allowlisted: {row.get('served_not_allowlisted')}")
        print(f"  allowlisted, NOT served: {row.get('allowlisted_not_served')}")
        if row.get("activate"):
            print(f"  activate         {row['activate']}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
