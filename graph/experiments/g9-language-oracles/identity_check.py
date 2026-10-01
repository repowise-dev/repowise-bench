"""Sample declaration positions from a SCIP index for the identity hand-check.

The protocol requires, per language: the
modal declaration-line offset (already in `score.json`'s
`line_offsets_top`, produced by `compare.py`/`score.py`, nothing to redo
here) plus 20 random identities confirmed against source. This script does
only the second half -- it samples 20 deterministic (seeded) definitions
from `SCIPIndex.definitions` and prints each with a few lines of source
around the reported declaration line, so a reviewer can
visually confirm the position lands on the named declaration rather than a
doc-comment, attribute line, or blank line above it.

Run with the build-step interpreter (needs scip_pb2 via scip_reader.py).

Usage:
    python identity_check.py --scip path/to/Polly/index.scip \
        --repo path/to/Polly --seed 42 --n 20
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from scip_reader import SCIPIndex


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scip", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    idx = SCIPIndex.load(args.scip)
    repo = Path(args.repo)
    defs = [
        (sym, path, line)
        for sym, (path, line, _col) in idx.definitions.items()
        if path  # skip synthetic symbols with no file
    ]
    rng = random.Random(args.seed)
    sample = rng.sample(defs, min(args.n, len(defs)))

    for sym, path, line in sample:
        abs_path = repo / path
        print(f"--- {sym}")
        print(f"    {path}:{line}")
        try:
            lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            print(f"    <read error: {e}>")
            continue
        lo = max(0, line - 2)
        hi = min(len(lines), line + 1)
        for i in range(lo, hi):
            marker = ">>" if i == line - 1 else "  "
            print(f"    {marker} {i + 1}: {lines[i]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
