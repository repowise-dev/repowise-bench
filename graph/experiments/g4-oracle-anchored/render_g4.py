"""Render the G4 precision and recall tables from the cell artifacts.

Typed tables drift from the artifacts they claim to summarise, and this
experiment is about to grow a language at a time. Input is any number of
`<cell>-g4.json` files written by `compare.py`; output is markdown on stdout.

Bold marks the best arm in a row only when its interval clears every other
interval in that row. An overlap is a tie and is printed as one, because a
ranking that promotes overlapping intervals to wins is the failure this whole
benchmark is trying not to commit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ARM_ORDER = ["repowise", "codegraph", "codebase-memory-mcp"]
LABEL = {
    "repowise": "repowise",
    "codegraph": "CodeGraph",
    "codebase-memory-mcp": "codebase-memory-mcp",
}


def cell_label(path: Path, payload: dict) -> str:
    name = payload.get("repo", path.stem)
    tests = payload.get("oracle", {}).get("tests_included")
    if tests is None:
        return name
    return f"{name} ({'with tests' if tests else 'no tests'})"


CI = {"precision_vs_oracle": "precision_ci", "recall": "recall_ci"}


def separates(best: dict, others: list[dict], key: str) -> bool:
    """True when the best arm's interval clears every other arm's."""
    lo = best[CI[key]][0]
    return all(lo > o[CI[key]][1] for o in others)


def table(cells: list[tuple[str, dict]], key: str, title: str) -> str:
    head = [f"### {title}", "", "| cell | " + " | ".join(LABEL[a] for a in ARM_ORDER) + " |",
            "|---" * (len(ARM_ORDER) + 1) + "|"]
    for label, payload in cells:
        arms = payload["arms"]
        present = [a for a in ARM_ORDER if a in arms and arms[a].get(key) is not None]
        best = max(present, key=lambda a: arms[a][key]) if present else None
        won = bool(best) and separates(arms[best], [arms[a] for a in present if a != best], key)
        row = [label]
        for a in ARM_ORDER:
            r = arms.get(a)
            if not r or r.get(key) is None:
                row.append("n/a")
                continue
            lo, hi = r[CI[key]]
            cell = f"{r[key]:.3f} [{lo:.3f}, {hi:.3f}]"
            row.append(f"**{cell}**" if a == best and won else cell)
        head.append("| " + " | ".join(row) + " |")
    return "\n".join(head)


def scope(cells: list[tuple[str, dict]]) -> str:
    out = ["### What the oracle analysed", "",
           "| cell | files | oracle edges | functions judged | unjudged share, per arm |",
           "|---|---:|---:|---:|---|"]
    for label, payload in cells:
        o, arms = payload["oracle"], payload["arms"]
        counts = o.get("counts", {})
        shares = ", ".join(
            f"{LABEL[a]} {arms[a]['unjudged_share']:.0%}" for a in ARM_ORDER if a in arms
        )
        first = next(iter(arms.values()))
        out.append(
            f"| {label} | {o.get('analysed_file_count', '?')} | {first['oracle_edges']} | "
            f"{counts.get('functions_judged', '?')} | {shares} |"
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cells", nargs="+", help="<cell>-g4.json files, in display order")
    args = ap.parse_args()
    cells = []
    for c in args.cells:
        p = Path(c)
        payload = json.loads(p.read_text(encoding="utf-8"))
        cells.append((cell_label(p, payload), payload))
    print(table(cells, "precision_vs_oracle", "Precision against the oracle"))
    print()
    print(table(cells, "recall", "Recall against the oracle"))
    print()
    print(scope(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
