"""Recompute every table on this page from the graded rows in `rows/`.

Same contract as G1's script of the same name: the rows are the artifact and
the tables are the summary, so a reader who edits a verdict sees the table move.

    python verify_rows.py               # every table, plus the G1 comparison
    python verify_rows.py --rows ruby   # the graded rows of one language
    python verify_rows.py --strata      # the by-origin and by-confidence reads
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))

from stats import wilson  # noqa: E402

ROWS = HERE / "rows"
G1_ROWS = HERE.parent / "g1-edge-precision" / "rows"

LANGUAGES = ["csharp", "python", "java", "swift", "kotlin", "rust", "cpp", "php", "ruby"]

# The seven with a G1 cell; php and ruby have none and are printed as such.
G1_CELL = {
    "csharp": (28, 30), "python": (28, 30), "java": (20, 30), "swift": (23, 30),
    "kotlin": (27, 30), "rust": (22, 30), "cpp": (23, 30),
}

# The two bare-name strata: the fallback tier this experiment set out to price.
BARE_NAME = {"suffix_match", "unique_name"}


def load(language: str) -> dict:
    return json.loads((ROWS / f"{language}-cbm.json").read_text(encoding="utf-8"))


def score(rows: list[dict]) -> tuple[int, int]:
    """Correct out of n. `ambiguous` is never counted as correct."""
    return sum(1 for r in rows if r["verdict"] == "correct"), len(rows)


def is_typed(origin: str) -> bool:
    """The strata that claim to know a type, as opposed to matching a name."""
    return (
        origin.startswith("lsp_")
        or origin.endswith("_typed")
        or origin in {"field_type_hint", "same_module", "import_map", "cs_self_method",
                      "php_self_static", "php_method_inherited", "cs_inherited_method",
                      "cs_ctor", "cs_ctor_synthetic", "lsp_kt_this"}
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", metavar="LANGUAGE", help="print the graded rows of one language")
    ap.add_argument("--strata", action="store_true", help="the by-origin and by-confidence reads")
    ap.add_argument("--threeway", action="store_true",
                    help="the seven languages all three hand-graded arms share")
    ap.add_argument("--population", action="store_true",
                    help="the bare-name share of the drawn-from population")
    args = ap.parse_args()

    if args.population:
        # Over the unrounded per-cell counts. Rebuilding this from the rounded
        # per-language shares in PREREGISTRATION.md lands about half a point low.
        bare = total = 0
        print("| language | repo | in-language distinct | bare-name | share |")
        print("|---|---|---:|---:|---:|")
        for path in sorted((HERE / "draws").glob("*.json")):
            cell = json.loads(path.read_text(encoding="utf-8"))
            mix = cell["origin_mix"]
            n = sum(mix.values())
            k = sum(mix.get(origin, 0) for origin in BARE_NAME)
            bare += k
            total += n
            print(f"| {cell['language']} | {cell['repo']} | {n:,} | {k:,} | {k / n * 100:.1f}% |")
        print(f"| **all 21 cells** | | **{total:,}** | **{bare:,}** | "
              f"**{bare / total * 100:.1f}%** |")
        return

    if args.threeway:
        # php and ruby have no G1 cell, and go and typescript are not in this
        # experiment, so the only like-for-like pool is the seven in G1_CELL.
        # Anything wider mixes a different language set into one of the columns.
        arms = {"repowise": {}, "CodeGraph 1.5.0": {}, "codebase-memory-mcp 0.10.8": {}}
        for language in G1_CELL:
            for arm, path in (
                ("repowise", G1_ROWS / f"{language}-ours.json"),
                ("CodeGraph 1.5.0", G1_ROWS / f"{language}-codegraph.json"),
            ):
                cell = json.loads(path.read_text(encoding="utf-8"))
                rows = [r for r in cell["rows"] if r.get("in_pooled_30", True)]
                # The rust cell on our side ships its draw without its grading.
                if any(r["verdict"] is None for r in rows):
                    k, n = cell["pooled_cell"]["correct"], cell["pooled_cell"]["n"]
                else:
                    k, n = score(rows)
                arms[arm][language] = (k, n)
            arms["codebase-memory-mcp 0.10.8"][language] = score(load(language)["rows"])

        print("| language | repowise | CodeGraph 1.5.0 | codebase-memory-mcp 0.10.8 |")
        print("|---|---|---|---|")
        for language in G1_CELL:
            cols = [f"{k}/{n} = {k / n * 100:.1f}%" for k, n in
                    (arms[a][language] for a in arms)]
            print(f"| {language} | " + " | ".join(cols) + " |")
        cols = []
        for arm in arms:
            k = sum(v[0] for v in arms[arm].values())
            n = sum(v[1] for v in arms[arm].values())
            cols.append(f"**{k}/{n} = {k / n * 100:.1f}%** {wilson(k, n).pct().split(' ', 1)[1]}")
        print("| **pooled (7 languages)** | " + " | ".join(cols) + " |")
        return

    cells = {}
    for language in LANGUAGES:
        try:
            cells[language] = load(language)
        except FileNotFoundError:
            print(f"missing cell: {language}", file=sys.stderr)

    if args.rows:
        cell = cells[args.rows]
        print(f"\n## {args.rows} / {cell['arm']}\n")
        for i, r in enumerate(cell["rows"], 1):
            print(f"{i:3d}  {r['verdict']:<9}  {r['repo']}  {r['file']}:{r['line']} -> {r['target']}")
            print(f"     {r.get('source_line', '')}")
            print(f"     {r['reason']}")
        return

    if args.strata:
        by_origin = collections.defaultdict(lambda: [0, 0])
        by_band = collections.defaultdict(lambda: [0, 0])
        by_kind = collections.defaultdict(lambda: [0, 0])
        for cell in cells.values():
            for r in cell["rows"]:
                ok = r["verdict"] == "correct"
                for bucket, key in (
                    (by_origin, r["origin"]),
                    (by_kind, "bare-name" if r["origin"] in BARE_NAME
                     else "typed / lsp" if is_typed(r["origin"]) else "other"),
                    (by_band, "conf < 0.3" if (r.get("confidence") or 0) < 0.3
                     else "conf 0.3-0.79" if (r.get("confidence") or 0) < 0.8 else "conf >= 0.8"),
                ):
                    bucket[key][0] += ok
                    bucket[key][1] += 1

        for title, bucket in (("By stratum kind", by_kind), ("By stored confidence", by_band),
                              ("By origin", by_origin)):
            print(f"\n### {title}\n")
            print("| stratum | correct / n | rate | 95% CI |")
            print("|---|---|---:|---|")
            for key, (k, n) in sorted(bucket.items(), key=lambda kv: -kv[1][1]):
                iv = wilson(k, n)
                print(f"| `{key}` | {k}/{n} | {k / n * 100:.1f}% | {iv.pct().split(' ', 1)[1]} |")
        return

    print("| language | repositories | codebase-memory-mcp | 95% CI | our G1 cell |")
    print("|---|---|---|---|---|")
    pooled_k = pooled_n = 0
    for language, cell in cells.items():
        k, n = score(cell["rows"])
        pooled_k += k
        pooled_n += n
        g1 = G1_CELL.get(language)
        g1s = f"{g1[0]}/{g1[1]} = {g1[0] / g1[1] * 100:.1f}%" if g1 else "*(none)*"
        iv = wilson(k, n)
        print(f"| {language} | {', '.join(cell['repos'])} | {k}/{n} = {k / n * 100:.1f}% "
              f"| {iv.pct().split(' ', 1)[1]} | {g1s} |")
    iv = wilson(pooled_k, pooled_n)
    print(f"| **pooled** | | **{pooled_k}/{pooled_n} = {pooled_k / pooled_n * 100:.1f}%** "
          f"| {iv.pct().split(' ', 1)[1]} | **229/270 = 84.8%** |")

    print("\n| verdict | rows |")
    print("|---|---:|")
    tally = collections.Counter(r["verdict"] for c in cells.values() for r in c["rows"])
    for verdict in ("correct", "wrong", "ambiguous"):
        print(f"| {verdict} | {tally[verdict]} |")

    print("\n### Per repository, where a language has more than one\n")
    print("| language | split |")
    print("|---|---|")
    for language, cell in cells.items():
        if len(cell["repos"]) == 1:
            continue
        per = collections.defaultdict(lambda: [0, 0])
        for r in cell["rows"]:
            per[r["repo"]][0] += r["verdict"] == "correct"
            per[r["repo"]][1] += 1
        print(f"| {language} | " + ", ".join(f"{repo} {k}/{n}" for repo, (k, n) in per.items()) + " |")

    if len(cells) < len(LANGUAGES):
        sys.exit(1)


if __name__ == "__main__":
    main()
