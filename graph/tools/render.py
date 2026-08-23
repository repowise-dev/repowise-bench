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


NEWLINE = chr(10)


def _fmt(rate, low=None, high=None) -> str:
    if rate is None:
        return "-"
    if low is None:
        return f"{rate:.3f}"
    return f"{rate:.3f} [{low:.2f}, {high:.2f}]"


def _publishable(p: dict, experiment: str | None = None) -> bool:
    """Read a verdict that may be one flag or one per experiment.

    Documents written before the stamp was split carry a single bool, so both
    shapes have to be readable. A dict is truthy, so a reader that forgot this
    would call every split document publishable -- which is the failure mode
    worth being explicit about rather than terse.
    """
    pub = p["publishable"]
    if not isinstance(pub, dict):
        return bool(pub)
    if experiment is not None:
        return bool(pub.get(experiment, False))
    return all(pub.values())


def _provenance_line(doc: dict, experiment: str | None = None) -> str:
    """Provenance, distinguishing what a caveat actually invalidates.

    `publishable` is one document-level boolean and it is deliberately
    conservative, so a run whose only defect is a cached *cost* row reads the
    same as one measured against somebody's half-finished working tree. Those
    are not the same claim. Coverage depends on the sets an artifact yields,
    and neither a skipped warmup nor a restored artifact changes one -- the
    restore is byte-exact on every protocol set and `smoke.py` asserts it.

    So the line separates them: a dirty tree invalidates everything, while a
    cached peer or a skipped warmup invalidates only cost. Anything else
    unknown falls back to the blanket warning rather than being assumed benign.
    """
    p = doc["provenance"]
    bits = [
        f"Measured at `{p['repowise']['head_short']}`",
        f"{p['repowise'].get('version') or 'version not stamped'}",
        f"run {p['run_at'][:10]}",
        f"warmup {'on' if p.get('warmup') else 'OFF'}",
    ]
    if not _publishable(p, experiment):
        why = p.get("not_publishable_because")
        if isinstance(why, dict):
            # The stamp is split, so the reason is already attributed and the
            # heuristic below is not needed.
            bits.append("**NOT PUBLISHABLE** ("
                        + (why.get(experiment) or "; ".join(sorted(set(why.values()))))
                        + ")")
            return " | ".join(bits)
        cost_only = not p["repowise"].get("dirty", True) and not any(
            "allow-dirty" in c for c in p.get("caveats", [])
        )
        if cost_only and (p.get("cost_from_cache") or not p.get("warmup")):
            why = []
            if p.get("cost_from_cache"):
                why.append("competitor artifacts restored from cache")
            if not p.get("warmup"):
                why.append("no warmup")
            bits.append(
                "coverage stands, **cost NOT PUBLISHABLE** (" + ", ".join(why) + ")"
            )
        else:
            bits.append("**NOT PUBLISHABLE**")
    # ASCII separator: this prints to a cp1252 console on the measurement
    # machine, where a middot comes out as a replacement character.
    return " | ".join(bits)


def render_g2(doc: dict) -> str:
    """Coverage, per arm, on both denominators, with intervals."""
    out = ["## G2 cross-file coverage", "", _provenance_line(doc, "g2-cross-file-coverage"), ""]

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
    out = ["## G6 graph build cost", "", _provenance_line(doc, "g6-build-cost"), ""]
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


def render_g7(doc: dict) -> str:
    """Language breadth: what a claimed language actually delivers, per arm.

    Every tool in this field claims twenty to forty languages and none of them
    says what a claimed language produces. Three readings per cell, all off the
    arm's own artifact:

    * **node share** -- files in the language that declare at least one symbol,
      over files in the language the arm walked. A language that parses but
      yields no symbols cannot contribute an edge, and that is the first place
      support turns out to be nominal.
    * **edge share** -- the G2 incoming `calls` reading, over the arm's
      symbol-bearing files in the language.
    * **calls/file** -- distinct call edges over symbol-bearing files.

    The designed metric was edges per thousand lines. `run_corpus.py` records
    no line counts and adding them is a tree walk on every repository, so this
    prints per symbol-bearing file instead and says so rather than quietly
    relabelling a different denominator.

    An empty cell is a result. code-review-graph resolves no cross-file call
    edges at all on Go and C# while resolving 12,395 on TypeScript; that is a
    per-language capability gap, and printing it as a blank row rather than
    dropping it is the entire point of the experiment.
    """
    out = ["## G7 language breadth", "", _provenance_line(doc), ""]
    out += _g7_spread(doc)
    out += [
        "### Per repository",
        "",
        "| repo | kind | language | arm | files (lang) | node share | edge share | calls/file |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    nominal: list[str] = []
    for name, repo in doc["repos"].items():
        lang = repo["language"]
        for a, row in repo["arms"].items():
            if "error" in row:
                out.append(
                    f"| {name} | {repo.get('kind') or '-'} | {lang} | {a} | | FAILED | | |")
                continue
            counts = row["counts"]
            in_lang = counts["files_in_language"]
            sym = counts["symbol_files_in_language"]
            cell = row.get("coverage", {}).get("primary_language__calls_only__incoming") or {}
            node_share = sym / in_lang if in_lang else None
            per_file = counts["call_edges_distinct"] / sym if sym else None
            out.append(
                f"| {name} | {repo.get('kind') or '-'} | {lang} | {a} | {in_lang} | "
                f"{_fmt(node_share)} | {_fmt(cell.get('rate'))} | "
                f"{f'{per_file:.1f}' if per_file is not None else '-'} |"
            )
            if in_lang and not cell.get("covered"):
                nominal.append(f"{a} on {lang} ({name})")
    out += [
        "",
        "`node share` is symbol-bearing files over walked files, in the "
        "repository's primary language. `edge share` is the incoming `calls` "
        "coverage on the arm's own denominator. `calls/file` is distinct call "
        "edges over symbol-bearing files -- not per thousand lines; the runner "
        "records no line counts.",
    ]
    if nominal:
        out += [
            "",
            "**Walked the language and resolved no cross-file call edge in it:** "
            + "; ".join(sorted(set(nominal)))
            + ". Recorded as a capability gap, not as a failed run.",
        ]
    return "\n".join(out)


def _g7_spread(doc: dict) -> list[str]:
    """Edge share per (language, arm), across the language's three repositories.

    The per-repository table below is the evidence, but at thirty-five
    repositories times five arms it is 175 rows and no reader extracts a
    language from it. Worse, reading any single row as the language's number is
    the exact mistake the three-kinds rule exists to forbid -- we nearly
    published a fact about zod as a fact about TypeScript.

    So the spread is printed rather than the mean: a language whose three
    repositories read 3%, 14% and 26% does not have "a" rate, and collapsing
    that to 14% throws away the finding and hides which kind the tool is weak
    on. A language below three repositories, or missing a kind, is named
    underneath instead of being quietly averaged in with the rest.
    """
    per: dict[tuple[str, str], list[tuple[float, str]]] = {}
    kinds: dict[str, set[str]] = {}
    repos_per_lang: dict[str, set[str]] = {}
    for name, repo in doc["repos"].items():
        lang = repo["language"]
        kinds.setdefault(lang, set()).add(repo.get("kind") or "?")
        repos_per_lang.setdefault(lang, set()).add(name)
        for a, row in repo["arms"].items():
            if "error" in row:
                continue
            cell = row.get("coverage", {}).get("primary_language__calls_only__incoming") or {}
            if cell.get("rate") is None:
                continue
            per.setdefault((lang, a), []).append((cell["rate"], name))

    out = [
        "### Edge share by language, across kinds",
        "",
        "| language | kinds | arm | n | min | median | max | spread |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for lang, arm in sorted(per):
        vals = sorted(per[(lang, arm)])
        rates = [v for v, _ in vals]
        n = len(rates)
        median = rates[n // 2] if n % 2 else (rates[n // 2 - 1] + rates[n // 2]) / 2
        lo, hi = rates[0], rates[-1]
        out.append(
            f"| {lang} | {','.join(sorted(kinds.get(lang, set())))} | {arm} | {n} | "
            f"{lo:.3f} ({vals[0][1]}) | {median:.3f} | {hi:.3f} ({vals[-1][1]}) | "
            f"{hi - lo:.3f} |"
        )

    thin = sorted(
        lang for lang in kinds
        if len({k for k in kinds[lang] if k != "?"}) < 3 or len(repos_per_lang[lang]) < 3
    )
    out += [
        "",
        "`spread` is max minus min across the language's repositories, for that "
        "arm. It is reported instead of a mean because disagreement between the "
        "three kinds is a finding, not noise to average away.",
    ]
    if thin:
        out += [
            "",
            "**Below three repositories, or missing a kind, so no language-level "
            "claim is available:** " + ", ".join(thin) + ". Those rows describe "
            "the repositories named, not the language.",
        ]
    return out + [""]


def render_shared(doc: dict, peer: str = "codegraph", ours: str = "repowise") -> str:
    """Coverage on the denominator both arms agree on, per language.

    The own-denominator columns in G2 are not a comparison. Two arms disagree
    about which files can carry an edge at all, so each is answering the same
    question about a different population, and the difference between the two
    populations has already been large enough to reverse a headline: on
    caffeine, 123 `package-info.java` files padded the peer's denominator and a
    shared recount turned a 0.608-to-0.517 win into a 0.640-to-0.608 loss.

    Pooled across each language's repositories, with a Wilson interval, and a
    verdict of `tie` whenever the intervals overlap. Pooling weights by
    repository size, which is what a pooled estimate means; the per-repository
    median is printed beside it so a language carried by one large repository
    is visible rather than hidden.
    """
    import statistics as _st

    key = "calls_only__incoming"
    pair_a, pair_b = sorted((ours, peer))
    pair = f"{pair_a}__vs__{pair_b}"

    per: dict[str, dict] = {}
    for name, repo in doc["repos"].items():
        cell = (repo.get("shared") or {}).get("pairwise", {}).get(pair)
        if not cell or not cell.get("denominator") or ours not in cell:
            continue
        slot = per.setdefault(repo["language"], {
            "denom": 0, "ours": 0, "peer": 0, "ours_rates": [], "peer_rates": [], "repos": 0,
        })
        slot["denom"] += cell["denominator"]
        slot["ours"] += cell[ours][key]["covered"]
        slot["peer"] += cell[peer][key]["covered"]
        slot["ours_rates"].append(cell[ours][key]["rate"])
        slot["peer_rates"].append(cell[peer][key]["rate"])
        slot["repos"] += 1

    if not per:
        # Vacuity is not a pass. An empty table under a heading reads as "no
        # differences were found"; the truth is that this run predates the
        # pairwise block, or names arms it does not contain.
        have = sorted({a for r in doc["repos"].values() for a in r.get("arms", {})})
        return NEWLINE.join([
            f"## Shared-denominator coverage: {ours} vs {peer}",
            "",
            _provenance_line(doc),
            "",
            f"**Not available from this run.** No `shared.pairwise` block scored "
            f"for `{ours}` against `{peer}`. Either the result predates "
            "shared-denominator scoring, or one of those arms did not run "
            f"(this run has: {', '.join(have) or 'none'}). Re-run "
            "`run_corpus.py` rather than reading the absence as a null result.",
        ])

    out = [
        f"## Shared-denominator coverage: {ours} vs {peer}",
        "",
        _provenance_line(doc),
        "",
        "Incoming cross-file `calls`, over the files **both** arms call "
        "symbol-bearing within the walk they share. This is the fair reading; "
        "the own-denominator table is each tool's own metric on its own "
        "population.",
        "",
        "| language | repos | shared denom | ours | 95% CI | theirs | 95% CI | verdict |",
        "|---|---:|---:|---:|---|---:|---|---|",
    ]
    tally = {"ours": 0, "theirs": 0, "tie": 0}
    for lang in sorted(per):
        v = per[lang]
        n = v["denom"]
        ro, rp = v["ours"] / n, v["peer"] / n
        io, ip = stats.wilson(v["ours"], n), stats.wilson(v["peer"], n)
        overlap = io.low <= ip.high and ip.low <= io.high
        if overlap:
            verdict, k = "tie", "tie"
        elif ro > rp:
            verdict, k = "**ours**", "ours"
        else:
            verdict, k = "theirs", "theirs"
        tally[k] += 1
        out.append(
            f"| {lang} | {v['repos']} | {n} | {ro:.3f} | [{io.low:.3f}, {io.high:.3f}] | "
            f"{rp:.3f} | [{ip.low:.3f}, {ip.high:.3f}] | {verdict} |"
        )
    out += [
        "",
        f"**{tally['ours']} languages ours, {tally['theirs']} theirs, "
        f"{tally['tie']} tied** on non-overlapping 95% Wilson intervals. A tie is "
        "reported as a tie: overlapping intervals are not a win for whoever has "
        "the larger point estimate.",
        "",
        "Per-repository medians, for the languages a pooled figure could be "
        "carrying on one large repository:",
        "",
        "| language | ours (median) | theirs (median) |",
        "|---|---:|---:|",
    ]
    for lang in sorted(per):
        v = per[lang]
        out.append(
            f"| {lang} | {_st.median(v['ours_rates']):.3f} | "
            f"{_st.median(v['peer_rates']):.3f} |"
        )
    return NEWLINE.join(out)


def render_compare(base: dict, head: dict) -> str:
    """What moved between two runs, per repo per arm.

    The question every graph session opens with is "did the resolver work that
    landed since the last measurement actually move a row, and by how much".
    Answering it by eye across two tables is how a 1% drift gets read as a win.

    Only our arms can move between two runs at different commits of ours. A
    competitor row that moves is a warning, not a result: the tool version or
    the repository pin changed underneath the comparison, and the run is not
    comparable until that is explained.
    """
    bp, hp = base["provenance"], head["provenance"]
    out = [
        "## Change between runs",
        "",
        f"base `{bp['repowise']['head_short']}` "
        f"({bp['repowise'].get('version') or 'version not stamped'}) "
        f"-> head `{hp['repowise']['head_short']}` "
        f"({hp['repowise'].get('version') or 'version not stamped'})",
        "",
        "| repo | arm | calls | change | edge share | change |",
        "|---|---|---:|---:|---:|---:|",
    ]
    surprises: list[str] = []
    for name, hrepo in head["repos"].items():
        brepo = base["repos"].get(name)
        if not brepo:
            continue
        for a, hrow in hrepo["arms"].items():
            brow = brepo["arms"].get(a)
            if not brow or "error" in hrow or "error" in brow:
                continue
            bc = brow["counts"]["call_edges_distinct"]
            hc = hrow["counts"]["call_edges_distinct"]
            key = "primary_language__calls_only__incoming"
            br = (brow.get("coverage", {}).get(key) or {}).get("rate")
            hr = (hrow.get("coverage", {}).get(key) or {}).get("rate")
            d_calls = hc - bc
            d_rate = (hr - br) if (br is not None and hr is not None) else None
            if d_calls and not a.startswith("repowise"):
                surprises.append(f"{a} on {name} moved {d_calls:+d} call edges")
            out.append(
                f"| {name} | {a} | {hc} | {d_calls:+d} | "
                f"{_fmt(hr)} | {f'{d_rate:+.3f}' if d_rate is not None else '-'} |"
            )
    if surprises:
        out += [
            "",
            "**A competitor row moved, which it should not have.** "
            + "; ".join(surprises)
            + ". A competitor artifact depends only on (tool, version, repo, "
            "pin) and on none of our commits, so this is a changed tool "
            "version or a changed checkout, and the comparison is not sound "
            "until that is explained.",
        ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", help="a run directory name, e.g. 2026-08-18-3594ba75")
    ap.add_argument("--compare-to", help="a base run directory name; prints the delta table")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", choices=["g2", "g6", "g7", "shared"])
    ap.add_argument("--peer", default="codegraph", help="the arm to compare against")
    args = ap.parse_args()

    if args.list:
        for p in sorted(RESULTS.glob("*/result.json")):
            doc = json.loads(p.read_text(encoding="utf-8"))
            pub = "publishable" if _publishable(doc["provenance"]) else "not publishable"
            print(f"{p.parent.name}  {len(doc['repos'])} repos  {pub}")
        return 0

    path, doc = load(args.run)
    print(f"<!-- generated by graph/tools/render.py from {path.relative_to(BENCH)} -->\n")
    if args.compare_to:
        _, base = load(args.compare_to)
        print(render_compare(base, doc))
        return 0
    if args.only in (None, "g2"):
        print(render_g2(doc))
        print()
    if args.only in (None, "g6"):
        print(render_g6(doc))
        print()
    if args.only in (None, "g7"):
        print(render_g7(doc))
    if args.only in (None, "shared"):
        print(render_shared(doc, peer=args.peer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
