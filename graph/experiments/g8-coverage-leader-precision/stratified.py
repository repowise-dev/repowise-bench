"""The proportional-allocation sample G1 used, so G8 draws by the same rule.

`stratified` below is a verbatim copy of the function behind the published
84.8% / 57.0% pair, not a reimplementation of its description. It lives here
because the sampling harnesses that produced G1 were never published with it,
and a draw whose allocation a reader cannot inspect is a draw they have to take
on trust.

If the two ever diverge, this file is the one the G8 numbers were drawn with.
"""

from __future__ import annotations

import collections
import random


def stratified(rows: list[dict], key: str, n: int, seed: int) -> list[dict]:
    """Proportional-allocation sample, deterministic under ``seed``.

    Largest-remainder allocation so the quotas sum to exactly ``n``, then a
    seeded sample inside each stratum. Strata are walked in sorted order so the
    result does not depend on dict insertion order.
    """
    if len(rows) <= n:
        return sorted(rows, key=lambda r: (r["file"], r["line"], r["target"]))

    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        buckets[r[key]].append(r)

    total = len(rows)
    exact = {k: len(v) * n / total for k, v in buckets.items()}
    quota = {k: int(v) for k, v in exact.items()}
    # Largest remainder, ties broken by stratum name for determinism.
    remainder = sorted(buckets, key=lambda k: (-(exact[k] - quota[k]), k))
    i = 0
    while sum(quota.values()) < n:
        k = remainder[i % len(remainder)]
        if quota[k] < len(buckets[k]):
            quota[k] += 1
        i += 1

    rng = random.Random(seed)
    out: list[dict] = []
    for k in sorted(buckets):
        pool = sorted(buckets[k], key=lambda r: (r["file"], r["line"], r["target"]))
        out.extend(rng.sample(pool, min(quota[k], len(pool))))
    return sorted(out, key=lambda r: (r["file"], r["line"], r["target"]))
