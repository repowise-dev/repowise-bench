"""Collect the DeepSWE rerun smoke into one table: reward, adoption, cost.

Two rules from the pre-registration are enforced here rather than left to the
reader.

VALIDITY BEFORE REWARD. A cell whose agent never ran still produces a reward,
and that reward looks like a result. It happened on the first Claude Code cell
of this run: the model id was rejected, the agent exited after 1 second having
emitted 0 tokens, and the verifier dutifully scored reward 0 with F2P 0/66. Read
naively that is "the treated arm failed". So every cell is checked for liveness
first, and an invalid cell is excluded from the table rather than explained
underneath it.

NO COST WITHOUT ITS COMPLETION NUMBER. Cost per solved task is the reported
form, and it satisfies that by construction: an arm that gets cheap by giving up
scores worse on it automatically.

Adoption is read SERVER-SIDE only. The model's self-report of its own tool
surface is not a detector; under identical config gpt-5.6-sol returned different
tool lists and twice denied having tools that were present and callable. Two
independent server-side sources are used and cross-checked:

  * ``artifacts/repowise-mcp.err`` -- the MCP server's own stderr, teed to a
    file by the wrapper baked into the arm image. Identical on both harnesses.
  * ``agent/codex.txt`` -- Codex's rmcp client log under
    RUST_LOG=codex_rmcp_client=info. Codex only.

Codex's own ``list_mcp_resources`` surfaces as an ``mcp_tool_call`` stream item,
so stream item counts OVERSTATE usage and are deliberately not used.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Lines the repowise MCP server writes when it serves a request, and the lines
# that mean the arm is degraded even though it is running.
CALL_RE = re.compile(r"CallToolRequest", re.I)
LIST_RE = re.compile(r"ListToolsRequest", re.I)
MOCK_RE = re.compile(r"mock vector|failed to initiali[sz]e", re.I)
CANCEL_RE = re.compile(r"cancelled MCP tool call", re.I)
TOOL_NAME_RE = re.compile(
    r"\b(get_answer|get_context|get_symbol|get_overview|get_why|get_risk|"
    r"get_health|get_dead_code|get_change_risk|search_codebase|list_repos)\b")


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def collect_cell(trial_dir: Path) -> dict:
    result = json.loads((trial_dir / "result.json").read_text(encoding="utf-8"))
    agent = result.get("agent_result") or {}

    reward_path = trial_dir / "verifier" / "reward.json"
    reward = json.loads(reward_path.read_text(encoding="utf-8")) if reward_path.is_file() else {}

    mcp_err = read_text(trial_dir / "artifacts" / "repowise-mcp.err")
    codex_log = read_text(trial_dir / "agent" / "codex.txt")
    detector = mcp_err + "\n" + codex_log

    out_tokens = agent.get("n_output_tokens") or 0
    exception = result.get("exception_info")

    # Liveness. An agent that emitted no tokens did not run, whatever the
    # verifier says about it.
    invalid: list[str] = []
    if exception:
        invalid.append(f"exception:{exception.get('exception_type', 'unknown')}")
    if out_tokens == 0:
        invalid.append("zero_output_tokens")
    if CANCEL_RE.search(detector):
        invalid.append("mcp_calls_cancelled")
    if MOCK_RE.search(detector):
        invalid.append("embedder_on_mock_vectors")

    return {
        "reward": reward.get("reward"),
        "f2p": f"{reward.get('f2p_passed')}/{reward.get('f2p_total')}",
        "p2p": f"{reward.get('p2p_passed')}/{reward.get('p2p_total')}",
        "steps": result.get("n_agent_steps"),
        "out_tokens": out_tokens,
        "cost_usd": agent.get("cost_usd"),
        # Adoption, server-side. list_served proves the server was reachable at
        # all, which is what makes a zero call count readable as the agent's
        # choice rather than a dead arm.
        "list_served": len(LIST_RE.findall(detector)),
        "calls": len(CALL_RE.findall(detector)),
        "tools": sorted(set(TOOL_NAME_RE.findall(mcp_err))),
        "detector_bytes": len(detector),
        "invalid": invalid,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", type=Path, help="pier jobs directory")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    rows = []
    for job in sorted(args.jobs.iterdir()):
        if not job.is_dir() or job.name.startswith("gate--") or "--" not in job.name:
            continue
        task, cell = job.name.split("--", 1)
        for trial in job.iterdir():
            if not (trial / "result.json").is_file():
                continue
            rows.append({"task": task, "cell": cell, **collect_cell(trial)})

    header = (f"{'task':<40} {'cell':<16} {'rw':>3} {'F2P':>9} {'served':>6} "
              f"{'calls':>5} {'steps':>5} {'out_tok':>8} {'cost':>7}  status")
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda r: (r["task"], r["cell"])):
        status = ",".join(r["invalid"]) if r["invalid"] else "ok"
        cost = f"{r['cost_usd']:.4f}" if isinstance(r["cost_usd"], (int, float)) else "-"
        print(f"{r['task'][:40]:<40} {r['cell']:<16} {str(r['reward']):>3} {r['f2p']:>9} "
              f"{r['list_served']:>6} {r['calls']:>5} {str(r['steps']):>5} "
              f"{r['out_tokens']:>8} {cost:>7}  {status}")

    valid = [r for r in rows if not r["invalid"]]
    print(f"\ncells: {len(rows)} total, {len(valid)} valid, {len(rows) - len(valid)} excluded")

    # Per-arm summary. Cost per solved task is the only cost figure printed, so
    # a cost number can never appear without its completion number.
    print(f"\n{'arm':<16} {'n':>3} {'solved':>7} {'pass rate':>10} "
          f"{'total $':>9} {'$/solved':>9} {'mean calls':>11} {'zero-call':>10}")
    by_arm: dict[str, list[dict]] = {}
    for r in valid:
        by_arm.setdefault(r["cell"].rsplit("-r", 1)[0], []).append(r)
    for arm, group in sorted(by_arm.items()):
        solved = sum(1 for r in group if r["reward"] == 1)
        total_cost = sum(r["cost_usd"] or 0 for r in group)
        treated = [r for r in group if arm.endswith("rw")]
        mean_calls = (sum(r["calls"] for r in treated) / len(treated)) if treated else None
        zero = (sum(1 for r in treated if r["calls"] == 0) / len(treated)) if treated else None
        per_solved = f"{total_cost / solved:.3f}" if solved else "n/a"
        print(f"{arm:<16} {len(group):>3} {solved:>7} {solved / len(group):>9.1%} "
              f"{total_cost:>9.4f} {per_solved:>9} "
              f"{('%.2f' % mean_calls) if mean_calls is not None else '-':>11} "
              f"{('%.0f%%' % (100 * zero)) if zero is not None else '-':>10}")

    if args.json_out:
        args.json_out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
