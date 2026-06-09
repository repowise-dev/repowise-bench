#!/usr/bin/env python3
"""provenance_precision_table.py — score a filled validation sample.

Reads the validation_sample.json produced by provenance_validation_sample.py
(after verdicts are filled) and prints the per-channel precision table:

  precision        = agent_correct / (agent_correct + not_agent)
  agent accuracy   = agent_correct / verdicts with a judged agent
  tier accuracy    = share of correct-agent verdicts with the right tier
  negatives        = missed_agent rate among sampled unlabeled commits

``wrong_agent``/``wrong_tier`` count as detected-but-misattributed: they hurt
agent/tier accuracy, not detection precision.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=Path, required=True)
    ap.add_argument("--markdown", type=Path, default=None)
    args = ap.parse_args()
    items = json.loads(args.sample.read_text(encoding="utf-8"))["items"]

    by_channel: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    neg = {"true_human": 0, "missed_agent": 0, "unjudged": 0}
    for it in items:
        v = it.get("verdict")
        if not it["agent"]:
            neg[v if v in neg else "unjudged"] += 1
            continue
        by_channel[it["channel"]][v or "unjudged"] += 1

    lines = ["| channel | n judged | precision | wrong agent | wrong tier |",
             "|---|--:|--:|--:|--:|"]
    tot_det = tot_not = 0
    for ch, counts in sorted(by_channel.items()):
        ok = counts["agent_correct"]
        wa, wt, na = counts["wrong_agent"], counts["wrong_tier"], counts["not_agent"]
        n = ok + wa + wt + na
        detected = ok + wa + wt
        tot_det += detected
        tot_not += na
        prec = (detected / n) if n else float("nan")
        lines.append(f"| {ch} | {n} | {prec:.2%} ({detected}/{n}) | {wa} | {wt} |")
    overall = tot_det / max(tot_det + tot_not, 1)
    lines.append(f"| **overall** | {tot_det + tot_not} | **{overall:.2%}** | | |")
    n_neg = neg["true_human"] + neg["missed_agent"]
    lines.append("")
    lines.append(f"Negative spot-check (agent-heavy unlabeled): {neg['missed_agent']}"
                 f"/{n_neg} missed agents ({neg['missed_agent'] / max(n_neg, 1):.0%} FN rate"
                 f" in this stratum); {neg['unjudged']} unjudged.")
    text = "\n".join(lines)
    print(text)
    if args.markdown:
        args.markdown.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
