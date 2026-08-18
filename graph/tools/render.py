"""Generate the G2 and G6 tables from `results/graph/`, never by hand.

The retrieval bench has twice published a row that no longer matched the raw
data behind it, because the table was typed once and the run happened again.
Every table this benchmark publishes is printed by this script from a
`graph-result/1` document, so a stale row is a stale file rather than a stale
sentence.

    python graph/tools/render.py                    # newest run
    python graph/tools/render.py --run 2026-08-18-3594ba75
    python graph/tools/render.py --list

Output is markdown on stdout. Paste it under the marked headings in
`graph/README.md` and the per-experiment READMEs, or redirect it to a file.

Every table names the commit it was measured at. Java landing mid-session moves
caffeine, and a table that does not say which commit it came from cannot be
reconciled afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BENCH / "graph" / "lib"))

import stats  # noqa: E402

RESULTS = BENCH / "results" / "graph" / "g2g6"


def load(run: str | None) -> tuple[Path, dict]:
    runs = sorted(p for p in RESULTS.glob("*/result.json"))
    if not runs:
        raise SystemExit(f"no results under {RESULTS}")
    if run:
        match = [p for p in runs if p.parent.name == run]
        if not match:
            raise SystemExit(f"no run {run}; have {[p.parent.name for p in runs]}")
        path = match[0]
    else:
        path = runs[-1]
    return path, json.loads(path.read_text(encoding="utf-8"))


def _fmt(rate, low=None, high=None) -> str:
    if rate is None:
        return "-"
    if low is None:
        return f"{rate:.3f}"
    return f"{rate:.3f} [{low:.2f}, {high:.2f}]"


def _provenance_line(doc: dict) -> str:
    p = doc["provenance"]
    bits = [
        f"Measured at `{p['repowise']['head_short']}`",
        f"run {p['run_at'][:10]}",
        f"warmup {'on' if p.get('warmup') else 'OFF'}",
    ]
    if not p["publishable"]:
        bits.append("**NOT PUBLISHABLE**")
    # ASCII separator: this prints to a cp1252 console on the measurement
    # machine, where a middot comes out as a replacement character.
    return " | ".join(bits)


def render_g2(doc: dict) -> str:
    """Coverage, per arm, on both denominators, with intervals."""
    out = ["## G2 cross-file coverage", "", _provenance_line(doc), ""]

    arms_seen: list[str] = []
    for repo in doc["repos"].values():
        for a in repo["arms"]:
            if a not in arms_seen:
                arms_seen.append(a)

    out += [
        "Incoming `calls` only: the reading that says whether the call graph "
        "connected the file. Denominator is each arm's own symbol-bearing files "
        "in the repository's primary language.",
        "",
        "| repo | language | " + " | ".join(arms_seen) + "|",
        "|---|---|" + "---|" * len(arms_seen),
    ]
    for name, repo in doc["repos"].items():
        cells = []
        for a in arms_seen:
            row = repo["arms"].get(a, {})
            if "error" in row:
                cells.append("FAILED")
                continue
            c = row.get("coverage", {}).get("primary_language__calls_only__incoming")
            cells.append(_fmt(c["rate"], c["ci_low"], c["ci_high"]) if c else "-")
        out.append(f"| {name} | {repo['language']} | " + " | ".join(cells) + " |")

    out += ["", "### Denominators, and whether they are comparable", ""]
    out += [
        "| repo | " + " | ".join(f"{a} sym files" for a in arms_seen) + " | shared files seen | verdict |",
        "|---|" + "---|" * (len(arms_seen) + 2),
    ]
    for name, repo in doc["repos"].items():
        shared = repo.get("shared") or {}
        per_arm = []
        for a in arms_seen:
            row = repo["arms"].get(a, {})
            per_arm.append(str(row.get("counts", {}).get("symbol_files_in_language", "-")))
        counts = [
            repo["arms"][a]["counts"]["symbol_files_in_language"]
            for a in arms_seen
            if "counts" in repo["arms"].get(a, {})
        ]
        if len(counts) >= 2 and max(counts) - min(counts) > 0.05 * max(counts):
            verdict = f"**not comparable**, {max(counts) - min(counts)} file gap"
        elif counts:
            verdict = "comparable"
        else:
            verdict = "-"
        out.append(
            f"| {name} | " + " | ".join(per_arm) + f" | {shared.get('files_seen_intersection', '-')}"
            f" | {verdict} |"
        )

    out += ["", "### All three readings, per arm", ""]
    out += ["| repo | arm | either/any (their metric) | incoming/any | incoming/calls |",
            "|---|---|---|---|---|"]
    for name, repo in doc["repos"].items():
        for a in arms_seen:
            row = repo["arms"].get(a, {})
            if "coverage" not in row:
                continue
            c = row["coverage"]
            out.append(
                f"| {name} | {a} | "
                f"{_fmt(c['primary_language__any_dependency__either']['rate'])} | "
                f"{_fmt(c['primary_language__any_dependency__incoming']['rate'])} | "
                f"{_fmt(c['primary_language__calls_only__incoming']['rate'])} |"
            )

    # The paired test over repositories, which is the only corpus-level claim
    # the six repositories can support -- and usually it supports none.
    if len(arms_seen) >= 2:
        out += ["", "### Paired comparison, per repository", ""]
        base = "repowise" if "repowise" in arms_seen else arms_seen[0]
        for other in arms_seen:
            if other == base:
                continue
            pairs = []
            for repo in doc["repos"].values():
                x = repo["arms"].get(base, {}).get("coverage", {})
                y = repo["arms"].get(other, {}).get("coverage", {})
                k = "primary_language__calls_only__incoming"
                if x.get(k, {}).get("rate") is not None and y.get(k, {}).get("rate") is not None:
                    pairs.append((x[k]["rate"], y[k]["rate"]))
            if not pairs:
                continue
            st = stats.sign_test(pairs)
            verdict = (
                "no corpus-level difference"
                if st.p_value > 0.05
                else f"difference at p={st.p_value:.3f}"
            )
            out.append(
                f"* **{base} vs {other}**: {st.wins}-{st.losses} across {len(pairs)} "
                f"repositories, sign test p={st.p_value:.3f} -- {verdict}."
            )
    return "\n".join(out)


def render_g6(doc: dict) -> str:
    """Build cost. Graph construction only, on every arm."""
    out = ["## G6 graph build cost", "", _provenance_line(doc), ""]
    out += [
        "Graph construction only: walk, parse, resolve, write edges. No "
        "documentation, no embeddings, no health pass. Never quote this beside "
        "the full-index row in `docs/BENCHMARKS.md`, which is a different "
        "denominator.",
        "",
        "| repo | arm | seconds | peak RSS MB | index MB | distinct call edges |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name, repo in doc["repos"].items():
        for a, row in repo["arms"].items():
            if "error" in row:
                out.append(f"| {name} | {a} | FAILED | | | |")
                continue
            cost, counts = row["cost"], row["counts"]
            out.append(
                f"| {name} | {a} | "
                f"{cost['seconds'] if cost['seconds'] is not None else '-'} | "
                f"{cost['peak_rss_mb'] if cost['peak_rss_mb'] is not None else '-'} | "
                f"{cost['index_size_mb'] if cost['index_size_mb'] is not None else '-'} | "
                f"{counts['call_edges_distinct']} |"
            )
    out += [
        "",
        "`-` in seconds means the artifact was opened rather than built (a "
        "frozen baseline index, not timed). `-` in peak RSS means the arm runs "
        "in process and has no child to attach a job object to; use the "
        "`repowise-subprocess` arm for our memory figure.",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", help="a run directory name, e.g. 2026-08-18-3594ba75")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", choices=["g2", "g6"])
    args = ap.parse_args()

    if args.list:
        for p in sorted(RESULTS.glob("*/result.json")):
            doc = json.loads(p.read_text(encoding="utf-8"))
            pub = "publishable" if doc["provenance"]["publishable"] else "not publishable"
            print(f"{p.parent.name}  {len(doc['repos'])} repos  {pub}")
        return 0

    path, doc = load(args.run)
    print(f"<!-- generated by graph/tools/render.py from {path.relative_to(BENCH)} -->\n")
    if args.only in (None, "g2"):
        print(render_g2(doc))
        print()
    if args.only in (None, "g6"):
        print(render_g6(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
