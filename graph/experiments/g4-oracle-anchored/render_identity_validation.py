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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graded", default="identity-validation-gitleaks.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    text = render(json.loads(Path(args.graded).read_text(encoding="utf-8")))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
