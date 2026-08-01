"""Pin the dev / held-out test split for the bake-off. Run once, then never again.

DECISIONS.md, "Dev / held-out split", agreed with Raghav 2026-08-01: split the
112 django + cli instances into ~70 dev and ~42 test BEFORE rung 8 builds
anything, pin by `instance_id`, commit it, and never revise it. All improvement
work uses dev. The test set is evaluated exactly once, at publication.

Two corrections to the instruction as written, both found while implementing it:

1. **"Stratify by repo and by ContextBench `source`" is one variable, not two.**
   Measured on the actual 112: django/django is 80/80 `Verified` and cli/cli is
   32/32 `Multi`. `language` is collinear too (python / go). So repo, source and
   language are the same partition wearing three names, and stratifying on all
   three is stratifying on one.

2. **The axis that actually needs balancing is single-file vs multi-file gold.**
   Rung 5 measured single-file retrieval as a solved, undifferentiated problem
   (top three arms at 0.943 / 0.944 / 0.944 recall@10) and multi-hop as bad for
   the entire field (our 0.440, leading). So multi-file density is the property
   that decides how hard a subset is, and it is the property the planned
   improvement work (loosening the `_flow_path` / `_neighbor_rerank` gates) is
   aimed squarely at. A dev/test split unbalanced on it would let a real gain on
   dev vanish on test, or a null on dev look like a gain on test, for reasons
   that have nothing to do with the change. 57 of 112 instances are single-file
   and 55 are multi-file, so the axis is close to evenly populated and cheap to
   balance.

Strata are therefore (repo, is_multi_file): 4 cells. Assignment is deterministic
given the seed, and ordering is by `instance_id` before shuffling so the result
does not depend on parquet row order.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
BENCH_ROOT = HERE.parents[1]
PARQUET = BENCH_ROOT / "data" / "contextbench" / "contextbench_verified.parquet"
OUT = HERE / "dev_test_split.json"

REPOS = ["django/django", "cli/cli"]
DEV_FRACTION = 0.625  # 70 / 112
SEED = 20260801


def main() -> int:
    if OUT.exists():
        raise SystemExit(
            f"{OUT} already exists. The split is pinned and must never be "
            f"revised: re-running would silently move instances between dev and "
            f"held-out test and invalidate every comparison made against it. "
            f"Delete it deliberately if you truly mean to re-draw."
        )

    df = pd.read_parquet(PARQUET)
    sub = df[df.repo.isin(REPOS)].copy()
    sub["n_gold_files"] = sub.gold_context.map(
        lambda g: len({s["file"] for s in json.loads(g)})
    )
    sub["is_multi_file"] = sub.n_gold_files > 1

    dev: list[str] = []
    test: list[str] = []
    strata_report = []

    for (repo, multi), grp in sub.groupby(["repo", "is_multi_file"]):
        ids = sorted(grp.instance_id.tolist())
        rng = random.Random(f"{SEED}:{repo}:{multi}")
        rng.shuffle(ids)
        n_dev = round(len(ids) * DEV_FRACTION)
        dev.extend(ids[:n_dev])
        test.extend(ids[n_dev:])
        strata_report.append({
            "repo": repo,
            "multi_file": bool(multi),
            "n": len(ids),
            "dev": n_dev,
            "test": len(ids) - n_dev,
        })

    dev, test = sorted(dev), sorted(test)
    assert not (set(dev) & set(test)), "an instance landed in both splits"
    assert len(dev) + len(test) == len(sub), "split lost or duplicated instances"

    payload = {
        "created": "2026-08-01",
        "purpose": (
            "Overfitting protection for the competitive-proof bake-off. All "
            "improvement work uses dev only. The test set is evaluated exactly "
            "once, at publication. This file is pinned and must never be revised."
        ),
        "seed": SEED,
        "dev_fraction": DEV_FRACTION,
        "stratified_by": ["repo", "is_multi_file"],
        "stratification_note": (
            "source and language are collinear with repo on this subset "
            "(django=Verified=python 80/80, cli=Multi=go 32/32), so DECISIONS.md's "
            "'stratify by repo and by source' is a single variable. is_multi_file "
            "is used as the second axis because rung 5 measured it as the real "
            "difficulty discriminator (single-file 0.94 recall@10 across arms, "
            "multi-hop 0.44) and it is what the planned gating work targets."
        ),
        "dataset": "Contextbench/ContextBench verified subset (500 rows)",
        "dataset_sha256": hashlib.sha256(PARQUET.read_bytes()).hexdigest(),
        "strata": strata_report,
        "counts": {"dev": len(dev), "test": len(test), "total": len(sub)},
        "dev": dev,
        "test": test,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"wrote {OUT}")
    print(f"  dev={len(dev)}  test={len(test)}  total={len(sub)}")
    for s in strata_report:
        print(f"  {s['repo']:16s} multi={str(s['multi_file']):5s} "
              f"n={s['n']:3d} dev={s['dev']:3d} test={s['test']:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
