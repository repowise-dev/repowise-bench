"""The mui Layer A draw: 15 development instances, 30 sealed, computed not listed.

Run BEFORE any index is built. Layer A's whole credibility rests on a held-out
half that nothing was tuned against, and the django split was pinned by instance
id before rung 8 built anything. A second repo does not get a weaker protocol
than the first one.

WHY THE DRAW IS COMPUTED RATHER THAN PASTED
    `harness/question_shapes.py` sets the house pattern: commit the rule and the
    seed, derive the ids. A later reader who disagrees with the stratification
    edits the bins and the draw follows, instead of arguing with a hand-written
    list whose provenance is a sentence in a markdown file.

WHAT IS STRATIFIED, AND WHY THAT VARIABLE
    Gold FILE count, not span count. Layer A's metric is file coverage against
    ContextBench's `gold_context`, so the number of distinct gold files is the
    axis along which instances differ in difficulty, and the multi-hop instances
    are the ones a graph-only competitor is supposed to struggle with. Drawing
    without stratifying would let a seed hand us fifteen single-file instances
    and quietly delete the comparison the run exists to make.

ALLOCATION
    PROPORTIONAL, not equal. The django stratified Layer B run used equal
    allocation and had to carry a caveat that its pooled mean is not an estimate
    of the arms' mean over all 48. Layer A reports a pooled per-instance mean, so
    proportional allocation keeps that number an unbiased estimate of the 45.

    Seats are assigned by largest remainder. Ties are broken toward the LARGER
    gold-file stratum, stated here rather than left to dict ordering, because
    the declared purpose of stratifying is that multi-hop is represented.

THE 116-FILE INSTANCE
    One of the 45 carries 116 gold files against a median of 1. It is NOT
    excluded: dropping the hardest instance because it is inconvenient is
    selection, and it would flatter every arm including ours. If it lands in the
    draw, RESULT.md reports the median beside the mean so one instance cannot
    carry the column.

Usage:
    python scripts/draw_mui_instances.py            # print the draw
    python scripts/draw_mui_instances.py --write    # also write the JSON
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path

import pandas as pd

BENCH_ROOT = Path(__file__).resolve().parents[1]
PARQUET = BENCH_ROOT / "data" / "contextbench" / "contextbench_verified.parquet"
MUI_CLONE = BENCH_ROOT / "repos" / "mui" / "material-ui"
OUT = BENCH_ROOT / "data" / "contextbench" / "mui_split.json"

# Pinned. Changing either of these changes the draw and is a new experiment.
SEED = 20260806
N_DEV = 15

# (label, lo, hi) on gold FILE count, inclusive. Ordered ascending; the tie-break
# walks this list in reverse so larger-gold strata win a contested seat.
STRATA = [("1", 1, 1), ("2", 2, 2), ("3-4", 3, 4), ("5+", 5, 10**9)]


def gold_files(gold_context) -> list[str]:
    spans = json.loads(gold_context) if isinstance(gold_context, str) else gold_context
    return sorted({s["file"] for s in spans})


def load_mui() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    mui = df[df["repo"].astype(str).str.contains("mui", case=False, na=False)].copy()
    mui["n_gold"] = mui["gold_context"].map(lambda g: len(gold_files(g)))
    # Sort by instance_id so the frame order cannot depend on parquet layout.
    return mui.sort_values("instance_id").reset_index(drop=True)


def stratum_of(n: int) -> str:
    for label, lo, hi in STRATA:
        if lo <= n <= hi:
            return label
    raise ValueError(n)


def allocate(counts: dict[str, int], total: int) -> dict[str, int]:
    """Proportional seats by largest remainder, ties toward larger gold counts."""
    n_all = sum(counts.values())
    exact = {k: total * v / n_all for k, v in counts.items()}
    seats = {k: int(v) for k, v in exact.items()}
    left = total - sum(seats.values())
    # Descending remainder; tie broken by position in STRATA, largest first.
    order = {label: i for i, (label, _, _) in enumerate(STRATA)}
    ranked = sorted(exact, key=lambda k: (-(exact[k] - seats[k]), -order[k]))
    for k in ranked[:left]:
        seats[k] += 1
    return seats


def commit_date(sha: str) -> str:
    if not MUI_CLONE.exists():
        return "?"
    try:
        return subprocess.run(
            ["git", "-C", str(MUI_CLONE), "show", "-s", "--format=%cs", sha],
            capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return "?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    mui = load_mui()
    mui["stratum"] = mui["n_gold"].map(stratum_of)
    counts = {label: int((mui["stratum"] == label).sum()) for label, _, _ in STRATA}
    seats = allocate(counts, N_DEV)

    print(f"45 mui instances, seed {SEED}, {N_DEV} dev / {len(mui) - N_DEV} sealed")
    print(f"{'stratum':>8} {'pool':>5} {'seats':>6}")
    for label, _, _ in STRATA:
        print(f"{label:>8} {counts[label]:>5} {seats[label]:>6}")
    assert sum(seats.values()) == N_DEV

    rng = random.Random(SEED)
    dev: list[str] = []
    for label, _, _ in STRATA:
        pool = sorted(mui.loc[mui["stratum"] == label, "instance_id"])
        dev.extend(rng.sample(pool, seats[label]))
    dev = sorted(dev)
    sealed = sorted(set(mui["instance_id"]) - set(dev))
    assert len(dev) == N_DEV and len(sealed) == len(mui) - N_DEV
    assert not (set(dev) & set(sealed))

    info = mui.set_index("instance_id")
    print(f"\nDEV {len(dev)}:")
    for i in dev:
        r = info.loc[i]
        print(f"  {i}  gold={r['n_gold']:>3}  {r['base_commit'][:10]}  "
              f"{commit_date(r['base_commit'])}")

    dated = sorted(dev, key=lambda i: commit_date(info.loc[i, "base_commit"]))
    smoke = [dated[0], dated[-1]]
    print("\nSMOKE PAIR (oldest and newest base_commit among the dev 15, so the "
          "measured build cost BRACKETS the range rather than sampling its "
          "middle; mui at an old commit is far smaller than at HEAD):")
    for i in smoke:
        r = info.loc[i]
        print(f"  {i}  {r['base_commit'][:10]}  {commit_date(r['base_commit'])}")

    if args.write:
        OUT.write_text(json.dumps({
            "seed": SEED,
            "n_dev": N_DEV,
            "strata": [[label, lo, hi] for label, lo, hi in STRATA],
            "seats": seats,
            "dev": dev,
            "sealed": sealed,
            "smoke": smoke,
            "base_commits": {i: info.loc[i, "base_commit"] for i in dev + sealed},
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
