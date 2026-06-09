#!/usr/bin/env python3
"""provenance_validation_sample.py — stratified sample for hand-validation.

Draws, deterministically (seeded), from the provenance-walk outputs:
  * up to --per-channel commits per detection channel, spread across repos
    (precision check), and
  * --negatives agent-UNlabeled commits from the agent_heavy cohort
    (false-negative spot-check).

Writes <out>/validation_sample.json; verdicts are filled in by hand (or by an
independent reviewer working only from the raw GitHub evidence) and scored by
provenance_precision_table.py.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-channel", type=int, default=13)
    ap.add_argument("--negatives", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260604)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    by_channel: dict[str, list[dict]] = defaultdict(list)
    negatives: list[dict] = []
    for path in sorted(args.provenance_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data["rows"]:
            if r.get("is_merge"):
                continue
            item = {"repo": data["repo"], "sha": r["sha"], "date": r["date"],
                    "pr_number": r.get("pr_number"), "agent": r["agent"],
                    "autonomy_tier": r["autonomy_tier"], "channel": r["channel"],
                    "confidence": r["confidence"]}
            if r["agent"]:
                by_channel[r["channel"]].append(item)
            elif data["cohort"] == "agent_heavy":
                negatives.append(item)

    sample: list[dict] = []
    for channel, items in sorted(by_channel.items()):
        # spread across repos: round-robin over per-repo shuffled lists
        per_repo: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            per_repo[it["repo"]].append(it)
        for lst in per_repo.values():
            rng.shuffle(lst)
        picked: list[dict] = []
        repo_lists = sorted(per_repo.values(), key=len, reverse=True)
        i = 0
        while len(picked) < min(args.per_channel, len(items)):
            lst = repo_lists[i % len(repo_lists)]
            if lst:
                picked.append(lst.pop())
            i += 1
            if all(not l for l in repo_lists):
                break
        sample.extend(picked)

    rng.shuffle(negatives)
    sample.extend(negatives[:args.negatives])

    for it in sample:
        it["verdict"] = None      # fill: "agent_correct" | "wrong_agent" |
        #        "wrong_tier" | "not_agent" | (negatives) "true_human" | "missed_agent"
        it["verdict_note"] = ""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"generated": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
         "n": len(sample), "items": sample}, indent=2), encoding="utf-8")
    print(f"sampled {len(sample)} commits "
          f"({sum(1 for s in sample if s['agent'])} positives across "
          f"{len(by_channel)} channels, {min(args.negatives, len(negatives))} negatives)")


if __name__ == "__main__":
    main()
