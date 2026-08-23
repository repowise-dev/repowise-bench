"""Recompute every table on this page from the graded rows in `rows/`.

The point of the directory is that nobody has to take the tables on trust: the
rows carry the verdict and the reason it was given, and this script turns them
back into the published numbers. If a row is edited the table moves, which is
what makes the rows the artifact and the table the summary.

    python verify_rows.py            # tables, plus a check against the headline
    python verify_rows.py --rows go  # the graded rows of one language, both sides
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from stats import wilson  # noqa: E402

HERE = Path(__file__).resolve().parent
ROWS = HERE / "rows"

# Descending by our rate, the order the published table uses.
LANGUAGES = [
    "typescript", "go", "csharp", "python", "kotlin", "cpp", "swift", "rust", "java",
]
SIDES = [("ours", "repowise"), ("codegraph", "CodeGraph")]

# The headline this page publishes, so the script can disagree with it out loud.
# 280 rather than 270 since the java cell was widened to a second repository:
# java carries 40 rows over caffeine and spring-petclinic, every other cell 30.
PUBLISHED = {"ours": (240, 280), "codegraph": (164, 280)}


def load(language: str, side: str) -> dict:
    return json.loads((ROWS / f"{language}-{side}.json").read_text(encoding="utf-8"))


def counted(cell: dict) -> list[dict]:
    """The rows that enter the pooled cross-language figure.

    C++ was graded at 10 rows per repository and enters the pooled row at the
    seeded 6 of each 10, so it carries the same weight as every other language.
    """
    rows = cell["rows"]
    if any("in_pooled_30" in r for r in rows):
        return [r for r in rows if r["in_pooled_30"]]
    return rows


def check_depth(language: str, side: str, cell: dict) -> str | None:
    """A row file must agree with its own stated ``depth_read``.

    Added after a published row file carried two draws mixed together and summed
    to a depth of 43 against its own stated 42. The pooled cell agreed with the
    page by coincidence, so checking only the pooled cell caught nothing.
    """
    stated = cell.get("depth_read")
    if not stated:
        return None
    rows = cell["rows"]
    if any(r["verdict"] is None for r in rows):
        return None
    k = sum(1 for r in rows if r["verdict"] == "correct")
    if (k, len(rows)) != (stated["correct"], stated["n"]):
        return (
            f"{language}/{side}: rows sum to {k}/{len(rows)} against a stated "
            f"depth_read of {stated['correct']}/{stated['n']}"
        )
    return None


def score(rows: list[dict]) -> tuple[int, int] | None:
    """Correct out of n. `None` when a cell's verdicts were never written down."""
    if any(r["verdict"] is None for r in rows):
        return None
    return sum(1 for r in rows if r["verdict"] == "correct"), len(rows)


def fmt(scored: tuple[int, int] | None, stated: dict) -> str:
    if scored is None:
        return f"{stated['correct']}/{stated['n']} *(stated; rows carry no verdict)*"
    k, n = scored
    return f"{k}/{n} = {wilson(k, n).pct()}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", metavar="LANGUAGE", help="print the graded rows of one language")
    args = ap.parse_args()

    if args.rows:
        for side, arm in SIDES:
            cell = load(args.rows, side)
            print(f"\n## {args.rows} / {arm} ({cell['repowise_commit']})\n")
            for i, r in enumerate(cell["rows"], 1):
                verdict = r["verdict"] or "not recorded"
                print(f"{i:3d}  {verdict:<9}  {r['repo']}  {r['file']}:{r['line']} -> {r['target']}")
                if r["reason"]:
                    print(f"     {r['reason']}")
        return

    pooled = {side: [0, 0] for side, _ in SIDES}
    missing = []
    depth_errors = []

    print("| language | repositories | repowise | CodeGraph |")
    print("|---|---|---|---|")
    for language in LANGUAGES:
        line = [language]
        cells = {side: load(language, side) for side, _ in SIDES}
        line.append(", ".join(cells["ours"]["repositories"]))
        for side, _ in SIDES:
            cell = cells[side]
            problem = check_depth(language, side, cell)
            if problem:
                depth_errors.append(problem)
            scored = score(counted(cell))
            line.append(fmt(scored, cell["pooled_cell"]))
            if scored is None:
                missing.append(f"{language}/{side}")
                k, n = cell["pooled_cell"]["correct"], cell["pooled_cell"]["n"]
            else:
                k, n = scored
            pooled[side][0] += k
            pooled[side][1] += n
        print("| " + " | ".join(line) + " |")

    print()
    print("| | correct / n | 95% CI |")
    print("|---|---|---|")
    for side, arm in SIDES:
        k, n = pooled[side]
        print(f"| **{arm}** | **{k}/{n} = {k / n * 100:.1f}%** | {wilson(k, n).pct().split(' ', 1)[1]} |")

    print()
    ok = True
    for side, arm in SIDES:
        k, n = pooled[side]
        want = PUBLISHED[side]
        state = "matches" if (k, n) == want else f"DISAGREES with the published {want[0]}/{want[1]}"
        ok = ok and (k, n) == want
        print(f"{arm}: {k}/{n} {state}")
    if missing:
        print(f"\nCells whose rows carry no verdict, counted from the stated cell total: {', '.join(missing)}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
