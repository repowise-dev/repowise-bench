"""Rung 4: turn the raw matrix JSON + per-cell logs into the RESULT.md tables.

Each arm reports its own index statistics in its own vocabulary and nowhere
else, so the comparable columns (files parsed, nodes, edges) have to be mined
per arm from its log. Where an arm does not report a figure, the cell is `-`
rather than 0: an unreported number is not a zero, and filling it in with one
would understate a competitor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent
LOGS = OUT / "logs"

# arm -> {stat: regex with one capture group}
PATTERNS = {
    "repowise-noprose": {
        "files": r"([\d,]+) files parsed",
        "symbols": r"([\d,]+) symbols extracted",
        "nodes": r"Graph:\s*([\d,]+) nodes",
        "edges": r"([\d,]+) edges",
        "pages": r"Generated ([\d,]+) pages",
    },
    "codegraph": {
        "files": r"Indexed ([\d,]+) files",
        "nodes": r"([\d,]+) nodes,",
        "edges": r"([\d,]+) edges in",
        "self_reported_s": r"edges in ([\d.]+)s",
    },
    "code-review-graph": {
        "files": r"Progress: ([\d,]+)/[\d,]+ files parsed",
        "nodes": r"Loaded ([\d,]+) unique nodes",
        "edges": r"Loaded [\d,]+ unique nodes, ([\d,]+) edges",
    },
    "graphify": {
        "files": r"([\d,]+)/[\d,]+ uncached files \(100%\)",
        "nodes": r"Rebuilt: ([\d,]+) nodes",
        "edges": r"Rebuilt: [\d,]+ nodes, ([\d,]+) edges",
        "communities": r"([\d,]+) communities",
    },
}


def mine(arm: str, repo: str) -> dict:
    p = LOGS / f"{arm}__{repo}.log"
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8", errors="replace")
    out = {}
    for stat, rx in PATTERNS.get(arm, {}).items():
        hits = re.findall(rx, text)
        if hits:
            # progress lines repeat; the last is the final figure
            out[stat] = hits[-1].replace(",", "")
    return out


def main() -> None:
    rows = json.loads((OUT / "smoke_matrix.json").read_text())
    enriched = []
    for r in rows:
        r = dict(r)
        r["stats"] = mine(r["arm"], r["repo"])
        enriched.append(r)
    (OUT / "smoke_matrix_enriched.json").write_text(
        json.dumps(enriched, indent=2), encoding="utf-8"
    )

    repos = ["cli", "svelte", "django"]
    arms = ["repowise-noprose", "codegraph", "code-review-graph", "graphify"]

    print("\n### Wall clock (seconds), full build from a pristine tree\n")
    hdr = f"| {'arm':20s} | " + " | ".join(f"{r:>8s}" for r in repos) + " |"
    print(hdr)
    print("|" + "-" * 22 + "|" + "|".join(["-" * 10] * len(repos)) + "|")
    for a in arms:
        cells = []
        for repo in repos:
            m = [x for x in enriched if x["arm"] == a and x["repo"] == repo]
            cells.append(f"{m[0]['seconds']:>8}" if m else f"{'-':>8}")
        print(f"| {a:20s} | " + " | ".join(cells) + " |")

    print("\n### Index artifact size (MB)\n")
    for a in arms:
        cells = []
        for repo in repos:
            m = [x for x in enriched if x["arm"] == a and x["repo"] == repo]
            cells.append(f"{m[0]['artifact_mb']:>8}" if m else f"{'-':>8}")
        print(f"| {a:20s} | " + " | ".join(cells) + " |")

    print("\n### Self-reported index statistics\n")
    print(f"| {'arm':20s} | {'repo':8s} | files | nodes | edges | extra |")
    for repo in repos:
        for a in arms:
            m = [x for x in enriched if x["arm"] == a and x["repo"] == repo]
            if not m:
                continue
            s = m[0]["stats"]
            extra = {
                k: v
                for k, v in s.items()
                if k not in ("files", "nodes", "edges")
            }
            print(
                f"| {a:20s} | {repo:8s} | {s.get('files', '-'):>6s} | "
                f"{s.get('nodes', '-'):>7s} | {s.get('edges', '-'):>7s} | {extra} |"
            )


if __name__ == "__main__":
    main()
