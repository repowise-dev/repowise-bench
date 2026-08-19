"""Render the graded identity draw as the markdown table G4's page carries.

Typed tables drift from the artifact they claim to summarise, so this one is
generated. Input is the graded draw written by `validate_identities.py`, with
the `verdict`, `note` and `decl_line_correct` fields filled in by the person who
read the twenty source windows.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# A Go oracle name carries the full module path, and a method name carries it
# inside the receiver parens, so trimming on the last slash would eat the `(*`.
_PKG_PATH = re.compile(r"(?:[\w.\-]+/)+")


def render(d: dict) -> str:
    rows = d["rows"]
    out = [
        f"Draw: {len(rows)} of {d['pool_size']} declaration positions the oracle "
        f"asserts in `{d['repo']}`, seed `{d['seed']}`.",
        "",
        "| # | position | oracle name | arms holding a symbol there | verdict |",
        "|---:|---|---|---|---|",
    ]
    for r in rows:
        have = [nm for nm, got in r["arms"].items() if got]
        arms = "all three" if len(have) == len(r["arms"]) else (", ".join(have) or "none")
        name = _PKG_PATH.sub("", r["oracle_names"][0])
        out.append(
            f"| {r['n']} | `{r['file']}:{r['line']}` | `{name}` | {arms} | {r['verdict']} |"
        )
    notes = [r for r in rows if r["note"]]
    if notes:
        out += ["", "Notes:", ""]
        out += [f"* **{r['n']}.** {r['note']}" for r in notes]
    return "\n".join(out) + "\n"


def render_edges(d: dict) -> str:
    """The whole-edge draw, which checks the join's direction and not its ends."""
    out = [
        "| caller | callee | site | source at the site |",
        "|---|---|---|---|",
    ]
    for e in d["edge_rows"]:
        src = e["site_source"].strip()
        if len(src) > 88:
            src = src[:85].rstrip() + "..."
        # A pipe would split the cell it sits in, and a Go raw string literal puts a
        # backtick inside one, which ends the span early.
        src = src.replace("|", "\\|")
        fence = "``" if chr(96) in src else "`"
        pad = " " if src.startswith(chr(96)) or src.endswith(chr(96)) else ""
        f, ln = e["call_site"]
        out.append(
            f"| `{_PKG_PATH.sub('', e['caller'])}` | `{_PKG_PATH.sub('', e['callee'])}` "
            f"| `{f}:{ln}` | {fence}{pad}{src}{pad}{fence} |"
        )
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graded", default="identity-validation-gitleaks.json")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--edges",
        action="store_true",
        help="render the whole-edge draw instead of the identity draw",
    )
    args = ap.parse_args()
    payload = json.loads(Path(args.graded).read_text(encoding="utf-8"))
    text = render_edges(payload) if args.edges else render(payload)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
