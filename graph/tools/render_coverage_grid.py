"""Render all four coverage cells at once, in files as well as rates.

Phase 9c. `render_coverage.py` answers one cell per invocation and prints only
rates; the four cells together are the object the publishing decision is taken
on, and a coverage difference must be read in files (a denominator argument
moves a rate and cannot move the file difference). This wraps that script
rather than reimplementing it, so the two can never disagree.

Decides nothing. Ordering the cells is not ranking them.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_coverage import DIRECTIONS, SETS, cell, load, verdict  # noqa: E402

CELLS = [(s, d) for s in SETS for d in DIRECTIONS]


def one_cell(rows: dict, a: str, b: str, edge_set: str, direction: str) -> dict:
    key = f"{edge_set}__{direction}"
    by_lang: dict[str, list] = collections.defaultdict(list)
    for name, row in rows.items():
        by_lang[row["language"]].append((name, row))

    langs, tally = [], collections.Counter()
    for lang in sorted(by_lang):
        denom = a_hit = b_hit = 0
        seps = []
        for name, row in by_lang[lang]:
            c = cell(row, a, b)
            if not c or not c.get("denominator"):
                continue
            x, y = c[a][key], c[b][key]
            denom += c["denominator"]
            a_hit += x["covered"]
            b_hit += y["covered"]
            v = verdict(x, y)
            tally[v] += 1
            if v != "tie":
                seps.append(f"{name}({'us' if v == 'A' else 'them'})")
        if denom:
            langs.append(
                {"lang": lang, "repos": len(by_lang[lang]), "denom": denom,
                 "a": a_hit, "b": b_hit, "sep": seps}
            )
    return {"langs": langs, "tally": tally}


def emit(res: dict, a: str, b: str, edge_set: str, direction: str) -> None:
    print(f"\n## {edge_set}, {direction}\n")
    print(f"| language | repos | denom | {a} files | {b} files | "
          f"them-minus-us (files) | {a} | {b} | separated |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    td = ta = tb = 0
    for r in res["langs"]:
        td += r["denom"]; ta += r["a"]; tb += r["b"]
        print(f"| {r['lang']} | {r['repos']} | {r['denom']} | {r['a']} | {r['b']} "
              f"| {r['b'] - r['a']:+d} | {r['a'] / r['denom']:.3f} "
              f"| {r['b'] / r['denom']:.3f} | {', '.join(r['sep']) or 'none - all ties'} |")
    print(f"| **corpus** | | **{td}** | **{ta}** | **{tb}** | **{tb - ta:+d}** "
          f"| **{ta / td:.3f}** | **{tb / td:.3f}** | |")
    t = res["tally"]
    print(f"\nper-repository verdicts: {a} {t['A']}, {b} {t['B']}, ties {t['tie']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", nargs="+")
    ap.add_argument("--a", default="repowise")
    ap.add_argument("--b", default="codebase-memory-mcp")
    ap.add_argument("--directions", default="incoming,either",
                    help="comma-separated; 'all' adds outgoing")
    args = ap.parse_args()

    dirs = DIRECTIONS if args.directions == "all" else args.directions.split(",")
    for d in dirs:
        if d not in DIRECTIONS:
            ap.error(f"bad direction {d!r}")

    rows = load(args.results)
    print(f"# Coverage grid - {args.a} vs {args.b}")
    print(f"# {len(rows)} repositories, pairwise shared denominator")
    print("# Counts are files. A rate moves with the denominator; the file "
          "difference does not.")

    summary = []
    for s in SETS:
        for d in dirs:
            res = one_cell(rows, args.a, args.b, s, d)
            emit(res, args.a, args.b, s, d)
            td = sum(r["denom"] for r in res["langs"])
            ta = sum(r["a"] for r in res["langs"])
            tb = sum(r["b"] for r in res["langs"])
            t = res["tally"]
            summary.append((s, d, td, ta, tb, t["A"], t["B"], t["tie"]))

    print("\n## The grid\n")
    print(f"| definition | direction | denom | {args.a} | {args.b} "
          f"| them-minus-us (files) | repos won us/them/tie |")
    print("|---|---|---:|---:|---:|---:|---|")
    for s, d, td, ta, tb, na, nb, nt in summary:
        print(f"| {s} | {d} | {td} | {ta} ({ta / td:.3f}) | {tb} ({tb / td:.3f}) "
              f"| {tb - ta:+d} | {na}/{nb}/{nt} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
