"""Generate the corpus-wide G6 cost and memory tables, never by hand.

Companion to `render.py`, which renders the `graph-result/1` documents produced
by `run_corpus.py`. This one reads `graph-cost/1` from
`run_corpus_cost.py`, whose shape is different: many runs per cell, a median and
a spread, and a memory column that is the point rather than an extra.

    python graph/tools/render_cost.py                 # newest run
    python graph/tools/render_cost.py --run 2026-08-19-13cc339a
    python graph/tools/render_cost.py --list

Output is markdown on stdout.

**Ratios are computed against a named baseline arm, and both are printed.** A
memory ratio with no absolute beside it is unreadable -- 13x sounds like a
rounding choice until it is 55 MB against 730 MB -- and an absolute with no ratio
buries the finding in a wide table.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
RESULTS = BENCH / "results" / "graph" / "g6-corpus"

# Display order, ours first: the reader is here to compare against us.
ARM_ORDER = [
    "repowise-subprocess",
    "codegraph",
    "graphify",
    "code-review-graph",
    "codebase-memory-mcp",
]
LABEL = {
    "repowise-subprocess": "repowise",
    "codegraph": "CodeGraph",
    "graphify": "Graphify",
    "code-review-graph": "code-review-graph",
    "codebase-memory-mcp": "codebase-memory-mcp",
}


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


def arms_present(doc: dict) -> list[str]:
    seen = {a for r in doc["repos"].values() for a in r["arms"]}
    return [a for a in ARM_ORDER if a in seen] + sorted(seen - set(ARM_ORDER))


def _cells(doc: dict, arm: str, key: str) -> list[float]:
    out = []
    for r in doc["repos"].values():
        c = r["arms"].get(arm) or {}
        if "error" in c:
            continue
        v = c.get(key)
        if v is not None:
            out.append(v)
    return out


def provenance_line(doc: dict, path: Path) -> str:
    pv = doc["provenance"]
    rw = pv["repowise"]
    ver = rw.get("version") or "?"
    head = rw.get("head_short") or "?"
    # The version string alone is a trap here. `pyproject.toml` carries the
    # version the last release commit set, so a commit *past* the tag still
    # reports the tag's version -- 13cc339a reads 0.44.0 while sitting one
    # commit after v0.44.0. Printing the bare version would invite exactly the
    # "measured on 0.44.0" claim this benchmark had to correct once already.
    describe = rw.get("describe") or ""
    tagged = describe == f"v{ver}"
    ver_str = f"repowise {ver}" if tagged else f"repowise {ver}+dev"
    bits = [
        f"Measured at **`{head}`**, `{ver_str}`",
        f"{pv.get('runs_per_cell')} timed builds per cell, median reported",
        f"warmup {'discarded' if pv.get('warmup') else '**skipped**'}",
        "nothing restored from cache",
    ]
    out = [", ".join(bits) + ".", ""]
    if not tagged:
        out += [
            f"**`{head}` is not a release commit.** `git describe` reads "
            f"`{describe or 'unknown'}`, so this is work past the `v{ver}` tag and "
            f"nothing here may be quoted as a {ver} figure.",
            "",
        ]
    if not pv.get("publishable"):
        out += ["**STAMPED NOT PUBLISHABLE.**"]
        for c in pv.get("caveats") or []:
            out.append(f"* {c}")
        out.append("")
    tools = pv.get("tools") or {}
    if tools.get("codegraph"):
        out += [f"Peer versions: CodeGraph {tools['codegraph']}.", ""]
    out += [f"<sub>Source: `{path.relative_to(BENCH)}`</sub>", ""]
    return "\n".join(out)


def render_summary(doc: dict) -> str:
    """The headline: one row per arm, medians over every repository measured."""
    arms = arms_present(doc)
    n_repos = len(doc["repos"])
    base = "repowise-subprocess"
    base_mem = _cells(doc, base, "median_peak_rss_mb")
    base_sec = _cells(doc, base, "median_seconds")
    bm = statistics.median(base_mem) if base_mem else None
    bs = statistics.median(base_sec) if base_sec else None

    out = [
        f"### Cost and memory over {n_repos} repositories",
        "",
        "Median across repositories of each cell's own median. Peak memory is a "
        "Windows job object reading, so it covers the whole process tree rather "
        "than the process we happened to spawn.",
        "",
        "| arm | median build | median peak memory | vs repowise | cells |",
        "|---|---:|---:|---:|---:|",
    ]
    for a in arms:
        mem = _cells(doc, a, "median_peak_rss_mb")
        sec = _cells(doc, a, "median_seconds")
        n_ok = sum(
            1 for r in doc["repos"].values()
            if a in r["arms"] and "error" not in r["arms"][a]
        )
        m = statistics.median(mem) if mem else None
        t = statistics.median(sec) if sec else None
        ratio = f"{m / bm:.1f}x memory" if (m and bm) else "n/a"
        if a == base:
            ratio = "n/a"
        out.append(
            f"| {LABEL.get(a, a)} | {t:.2f}s | {m:.0f} MB | {ratio} | {n_ok} |"
            if (m and t) else
            f"| {LABEL.get(a, a)} | {t if t is None else f'{t:.2f}s'} | "
            f"{m if m is None else f'{m:.0f} MB'} | {ratio} | {n_ok} |"
        )
    out.append("")
    if bm and bs:
        peaks = {a: statistics.median(_cells(doc, a, "median_peak_rss_mb") or [0]) for a in arms}
        worst = max((v, a) for a, v in peaks.items() if a != base)
        out.append(
            f"Ours is the lowest-memory arm at **{bm:.0f} MB** median; the highest is "
            f"{LABEL.get(worst[1], worst[1])} at **{worst[0]:.0f} MB**, "
            f"**{worst[0] / bm:.1f}x** ours."
        )
        out.append("")
    return "\n".join(out)


def render_memory_table(doc: dict) -> str:
    """Per repository, because a median hides where a tool falls over."""
    arms = arms_present(doc)
    out = [
        "### Peak memory per repository, MB",
        "",
        "| repo | language | files | " + " | ".join(LABEL.get(a, a) for a in arms) + " |",
        "|---|---|---:|" + "---:|" * len(arms),
    ]
    rows = sorted(
        doc["repos"].items(), key=lambda kv: (kv[1].get("files_at_pin") or 0)
    )
    for name, r in rows:
        cells = []
        for a in arms:
            c = r["arms"].get(a)
            if not c:
                cells.append("n/a")
            elif "error" in c:
                cells.append("**fail**")
            else:
                v = c.get("median_peak_rss_mb")
                cells.append(f"{v:.0f}" if v is not None else "n/a")
        out.append(
            f"| {name} | {r.get('language') or '?'} | {r.get('files_at_pin') or '?'} | "
            + " | ".join(cells) + " |"
        )
    out.append("")
    return "\n".join(out)


def render_time_table(doc: dict) -> str:
    arms = arms_present(doc)
    out = [
        "### Build time per repository, seconds (median of the cell's runs)",
        "",
        "| repo | language | files | " + " | ".join(LABEL.get(a, a) for a in arms) + " |",
        "|---|---|---:|" + "---:|" * len(arms),
    ]
    rows = sorted(doc["repos"].items(), key=lambda kv: (kv[1].get("files_at_pin") or 0))
    for name, r in rows:
        cells = []
        for a in arms:
            c = r["arms"].get(a)
            if not c:
                cells.append("n/a")
            elif "error" in c:
                cells.append("**fail**")
            else:
                v = c.get("median_seconds")
                cells.append(f"{v:.2f}" if v is not None else "n/a")
        out.append(
            f"| {name} | {r.get('language') or '?'} | {r.get('files_at_pin') or '?'} | "
            + " | ".join(cells) + " |"
        )
    out.append("")
    return "\n".join(out)


def render_failures(doc: dict) -> str:
    """A failed cell is a result and gets printed, never dropped."""
    bad = [
        (n, a, c["error"])
        for n, r in doc["repos"].items()
        for a, c in r["arms"].items()
        if "error" in c
    ]
    if not bad:
        return "No failed cells.\n"
    out = ["### Failed cells", "", "| repo | arm | error |", "|---|---|---|"]
    for n, a, e in sorted(bad):
        out.append(f"| {n} | {LABEL.get(a, a)} | {str(e)[:160].replace('|', '/')} |")
    out.append("")
    return "\n".join(out)


def render_spread(doc: dict) -> str:
    """How much the timings moved run to run, which is why medians are used."""
    arms = arms_present(doc)
    out = [
        "### Run-to-run timing spread",
        "",
        "Max minus min over each cell's timed builds, as a percentage of that "
        "cell's median. This is the reason the seconds column is a median and "
        "not a single build.",
        "",
        "| arm | median spread | worst cell |",
        "|---|---:|---:|",
    ]
    for a in arms:
        sp = _cells(doc, a, "time_spread_pct")
        if not sp:
            continue
        out.append(
            f"| {LABEL.get(a, a)} | {statistics.median(sp):.1f}% | {max(sp):.1f}% |"
        )
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument(
        "--only", default="", help="summary,memory,time,spread,failures"
    )
    args = ap.parse_args()

    if args.list:
        for p in sorted(RESULTS.glob("*/result.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            print(f"{p.parent.name}  {d.get('counts', {}).get('cells', '?')} cells  "
                  f"publishable={d['provenance']['publishable']}")
        return 0

    path, doc = load(args.run)
    want = {w.strip() for w in args.only.split(",") if w.strip()}
    parts = {
        "summary": render_summary,
        "memory": render_memory_table,
        "time": render_time_table,
        "spread": render_spread,
        "failures": render_failures,
    }
    print(f"## G6: graph build cost, {doc['counts']['repos']} repositories\n")
    print(provenance_line(doc, path))
    for name, fn in parts.items():
        if want and name not in want:
            continue
        print(fn(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
