#!/usr/bin/env python3
"""labels_quality_table.py — the "does labeling survive the firehose?" table.

Per repo: raw keyword file-level positive rate vs the three gated protocols
(issue-gated; spam-collapsed = minus self-fixes/reverts; fully gated = both),
plus the fix-stream composition (self-fix share, revert share, issue-linked
share). Universe = files touched in the window (NOT files at T0 — slightly
different from the corpus screen; comparable across strategies here).

Run::

    .venv/Scripts/python.exe health-defect/labels_quality_table.py \
        --labels-dir <data>/agent-repos/_labels --out <report.md>
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

SATURATION = {"github/gh-aw", "koala73/worldmonitor",
              "windmill-labs/windmill", "PrimeIntellect-ai/verifiers"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for path in sorted(args.labels_dir.glob("*.json")):
        s = json.loads(path.read_text(encoding="utf-8"))["summary"]
        rows.append(s)
    rows.sort(key=lambda s: (s["cohort"], -s["file_rate_raw"]))

    lines = ["# Labels quality — raw vs gated positive rates (window 2025-06 → HEAD)",
             f"\nGenerated {datetime.now(timezone.utc).isoformat()}.",
             "\nFile-level positive rate over files touched in the window. Gates:",
             "issue-gated (fix closes a bug/defect/regression-labeled issue, via",
             "commit subject + linked-PR body closing refs); spam-collapsed (drop",
             "self-fixes ≤48h, reverts, reverted); fully gated (both).\n",
             "| Repo | Cohort | Commits | Fixes | self-fix% | reverted+reverts% | issue-linked% | raw | issue-gated | spam-collapsed | fully gated |",
             "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for s in rows:
        nf = max(s["n_fix"], 1)
        sat = " ⚠" if s["repo"] in SATURATION else ""
        lines.append(
            f"| {s['repo']}{sat} | {s['cohort']} | {s['n_commits']} | {s['n_fix']} | "
            f"{s['n_self_fix'] / nf:.0%} | "
            f"{(s['n_reverts'] + s['n_reverted']) / max(s['n_commits'], 1):.1%} | "
            f"{s['n_issue_gated'] / nf:.0%} | "
            f"{s['file_rate_raw']:.1%} | {s['file_rate_issue_gated']:.1%} | "
            f"{s['file_rate_spam_collapsed']:.1%} | {s['file_rate_fully_gated']:.1%} |")
    lines.append("\n⚠ = pre-registered saturation exhibit (file rate ≥40% at the corpus screen).")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
