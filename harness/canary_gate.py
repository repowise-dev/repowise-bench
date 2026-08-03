"""Assert a small run is a MEASUREMENT before the big run behind it spends.

Every rung in this workstream that skipped the by-hand cell paid for it, and
the by-hand cell is not available to an unattended overnight queue. So the
checks a human would make are written down and made to exit non-zero.

The checks are in the order a silent failure hides, which is the order session
8 established and every session since has used. Each one exists because
something once passed every other field on the row while being wrong:

  1. cells exist and completed        — an empty results file reads as "no errors"
  2. no cell errored, none timed out
  3. hooks did not fire (D16)         — the control was never bare; an unpinned
                                        environment injects repowise's own
                                        context into the arm defined by its
                                        absence
  4. the arm was EXERCISED            — an MCP arm that never called its server
                                        is a bare agent wearing its name, and
                                        `arm_exercised` requires a call the
                                        server ANSWERED, not merely one issued
  5. the server served something      — a dead server scores as a bad arm (D1)
  6. tokens came from modelUsage      — a top-level `usage` read misses a model
  7. the judge produced a score       — a judge that fails on long answers
                                        removes cells from the control only

Usage:
    python -m harness.canary_gate --results <dir> --expect-cells 4 \
        --require-exercised C2_full
Exit 0 if every check passes, 1 otherwise, with the reason on stdout.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def load(results_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(results_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    latest: dict = {}
    for r in rows:
        latest[(r.get("task_id"), r.get("condition"))] = r
    return list(latest.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--expect-cells", type=int, default=1)
    ap.add_argument("--require-exercised", default="",
                    help="comma-separated condition names that MUST have called "
                         "their server and had it answer")
    ap.add_argument("--allow-hooks", action="store_true",
                    help="only for a deliberate hooks-on arm; never for a control")
    args = ap.parse_args()

    rows = load(Path(args.results))
    fails: list[str] = []

    if len(rows) < args.expect_cells:
        fails.append(f"expected >= {args.expect_cells} cells, found {len(rows)}")

    for r in rows:
        tag = f"{r.get('condition')}/{r.get('task_id')}"
        if r.get("error"):
            fails.append(f"{tag}: error {str(r['error'])[:100]}")
        if r.get("timed_out"):
            fails.append(f"{tag}: timed out")
        # `None` is Codex saying it has no channel to report hooks on, which is
        # honest and different from `[]`. A non-empty list is the failure.
        if not args.allow_hooks and r.get("hook_injections"):
            fails.append(f"{tag}: HOOK INJECTIONS PRESENT — the environment is "
                         f"not pinned (D16): {str(r['hook_injections'])[:120]}")
        if r.get("token_source") not in (None, "modelUsage",
                                         "codex:turn.completed.usage"):
            fails.append(f"{tag}: token_source is {r.get('token_source')!r}")
        js = r.get("judge_scores") or {}
        if not [v for v in js.values() if isinstance(v, (int, float))]:
            fails.append(f"{tag}: no judge score ({str(js)[:80]})")

    for cond in [c.strip() for c in args.require_exercised.split(",") if c.strip()]:
        cells = [r for r in rows if r.get("condition") == cond]
        if not cells:
            fails.append(f"{cond}: required to be exercised but ran no cells")
        for r in cells:
            tag = f"{cond}/{r.get('task_id')}"
            if not (r.get("served_tools") or r.get("served_count")):
                fails.append(f"{tag}: server advertised NO tools (D1)")
            per = r.get("mcp_per_server") or {}
            answered = any((v or {}).get("ok", 0) > 0 for v in per.values())
            if not answered:
                fails.append(f"{tag}: NOT EXERCISED — no MCP call the server "
                             f"answered. This cell measures a bare agent.")

    print(f"canary gate over {len(rows)} cells in {args.results}")
    for r in sorted(rows, key=lambda r: (str(r.get("condition")), str(r.get("task_id")))):
        js = [v for v in (r.get("judge_scores") or {}).values()
              if isinstance(v, (int, float))]
        per = r.get("mcp_per_server") or {}
        hooks = r.get("hook_injections")
        hooks_txt = "None" if hooks is None else str(len(hooks))
        judge_txt = f"{statistics.mean(js):.2f}" if js else "NONE"
        mcp_txt = {k: (v or {}).get("ok") for k, v in per.items()}
        print(f"  {str(r.get('condition')):12s} {str(r.get('task_id')):14s} "
              f"err={str(r.get('error'))[:20]:20s} "
              f"served={r.get('served_count')} mcp={mcp_txt} "
              f"hooks={hooks_txt} judge={judge_txt}")

    if fails:
        print(f"\nGATE FAILED ({len(fails)}):")
        for f in fails:
            print(f"  !! {f}")
        return 1
    print("\nGATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
