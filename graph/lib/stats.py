"""Intervals and tests for every number this benchmark publishes.

The rule this module exists to enforce: **no proportion is published without an
interval.** Session 1 nearly wrote up a five-file difference on gitleaks as a
win. At n=213 a five-file difference is inside the noise, and the only reliable
way to know that is to compute it every time rather than to remember when it
matters.

What n buys, computed rather than recalled (`margin(k, n)` below, 2026-08-18):

    n=30  at 60%  ->  +/-17.7 points   n=30  at 97%  ->  +/-13.4 points
    n=150 at 89%  ->  +/-6.0  points   n=300 at 62%  ->  +/-5.6  points

This is why the module exists rather than the recollection it replaces. The
session brief carried "about +/-16 points near 60%, narrowing to +/-8 near 95%"
from memory; the first half is close and **the second is wrong by nearly a
factor of two** -- at n=30 a 96.7% cell still spans [83.3, 99.4]. Wilson stays
wide at the top of the range because the interval is asymmetric there, which is
precisely the intuition an approximation from memory gets backwards.

The consequence: a single-language cell at 30 rows cannot separate 60% from
76%, so a per-language ordering claim needs the pooled figure or a larger draw.
`min_n_for_margin(0.62, 0.05)` says 380 rows to state the pooled peer rate to
+/-5 points, against the 300 already graded.

Three tools, each answering a different question:

  `wilson`     one proportion, one arm.  "Is 0.608 distinguishable from 0.517?"
  `sign_test`  paired, per repository.   "Does one arm beat the other across
               the corpus?" -- the right test for six repositories measured
               under both arms, because it assumes nothing about the six being
               drawn from one population, which they are not (see the README's
               own "six repositories is not a language" caveat).
  `bootstrap`  a pooled figure whose unit of resampling is the repository, not
               the file. Resampling files inside a fixed corpus of six would
               give an interval far too narrow, because it treats repository
               choice as if it carried no uncertainty.

Everything here is stdlib. scipy is not a dependency of this benchmark and one
proportion interval is not a reason to make it one.

Verified against published values on 2026-08-18: Wilson 29/30 -> [83.3, 99.4]
and 20/30 -> [48.8, 80.8] reproduce the SYMMETRIC_PRECISION_AUDIT table exactly.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# 95% two-sided normal quantile. Hard-coded rather than computed because the
# only alternative without scipy is an inverse-erf approximation, and one
# constant is easier to check than an approximation is.
Z95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Interval:
    """A proportion with its confidence interval, all on the 0..1 scale."""

    k: int
    n: int
    rate: float
    low: float
    high: float
    method: str

    @property
    def margin(self) -> float:
        """Half-width in proportion units. Asymmetric intervals report the
        larger side, so this never understates the uncertainty."""
        return max(self.rate - self.low, self.high - self.rate)

    def pct(self) -> str:
        """`96.7% [83.3, 99.4]` -- the form every table in this bench uses."""
        return f"{self.rate * 100:.1f}% [{self.low * 100:.1f}, {self.high * 100:.1f}]"

    def as_dict(self) -> dict:
        return {
            "k": self.k,
            "n": self.n,
            "rate": self.rate,
            "ci_low": self.low,
            "ci_high": self.high,
            "ci_method": self.method,
        }


def wilson(k: int, n: int, z: float = Z95) -> Interval:
    """Wilson score interval for k successes in n trials.

    Wilson rather than the normal approximation because every interesting cell
    in this benchmark sits near a boundary: 29/30 under the normal
    approximation runs past 106%, and zod's 0.138 and our java 0.667 are both
    far enough from 0.5 that a symmetric approximation misstates which side the
    mass is on. Wilson stays inside [0, 1] by construction and is the standard
    choice for exactly this shape of data.
    """
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k} n={n}")
    if n == 0:
        return Interval(0, 0, float("nan"), 0.0, 1.0, "wilson")
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return Interval(k, n, p, max(0.0, center - half), min(1.0, center + half), "wilson")


def margin(k: int, n: int, z: float = Z95) -> float:
    """Just the half-width, for sizing a draw before making it."""
    return wilson(k, n, z).margin


def min_n_for_margin(rate: float, target_margin: float, z: float = Z95) -> int:
    """Smallest n whose Wilson half-width at *rate* is within *target_margin*.

    Answers "how many rows must I grade to say this to +/-5 points?" before the
    grading rather than after it. Searched rather than solved: inverting Wilson
    for n is messy and this runs in microseconds.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"rate must be in [0, 1], got {rate}")
    n = 1
    while n < 1_000_000:
        if margin(round(rate * n), n, z) <= target_margin:
            return n
        n = n + 1 if n < 200 else n + 10
    raise ValueError(f"no n under 1e6 reaches +/-{target_margin} at rate {rate}")


def overlaps(a: Interval, b: Interval) -> bool:
    """Do two intervals overlap?

    Deliberately blunt, and deliberately used in one direction only: a bench row
    whose intervals overlap must NOT be written up as a win. Non-overlap is a
    *conservative* test of difference -- two 95% intervals can fail to overlap
    only when the difference is significant, but they can overlap while it still
    is. So this answers "am I allowed to claim this?" and never "is there really
    no difference?". For the sharper question use `diff_significant`.
    """
    return a.low <= b.high and b.low <= a.high


def diff_significant(a: Interval, b: Interval, z: float = Z95) -> bool:
    """Two-proportion z-test on the pooled rate, at the same confidence as *z*.

    Used where `overlaps` is too conservative to be informative. gitleaks and
    caffeine were both called ties from overlap alone, and one of those calls
    deserves the sharper test.
    """
    if a.n == 0 or b.n == 0:
        return False
    p_pool = (a.k + b.k) / (a.n + b.n)
    if p_pool in (0.0, 1.0):
        return False
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / a.n + 1 / b.n))
    return se > 0 and abs(a.rate - b.rate) / se > z


@dataclass(frozen=True, slots=True)
class SignTest:
    wins: int
    losses: int
    ties: int
    p_value: float

    @property
    def n_effective(self) -> int:
        return self.wins + self.losses

    def as_dict(self) -> dict:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "p_value": self.p_value,
        }


def sign_test(pairs: list[tuple[float, float]], *, tie_eps: float = 1e-12) -> SignTest:
    """Exact two-sided sign test over paired (ours, theirs) measurements.

    One pair per repository. Ties are dropped, which is the standard treatment
    and which matters here: G2 produced a byte-identical cell before and after
    `#1684`, and dropping that tie is honest about it carrying no directional
    information, where counting it as half a win would not be.

    Six repositories is a hard ceiling on what this can show. Even a 6-0 sweep
    lands at p=0.031, and 5-1 at p=0.219 is not significant at all. That is a
    fact about the corpus, not a defect in the test, and it is the reason the
    bench does not lead with a corpus-level win claim.
    """
    wins = losses = ties = 0
    for ours, theirs in pairs:
        if abs(ours - theirs) <= tie_eps:
            ties += 1
        elif ours > theirs:
            wins += 1
        else:
            losses += 1
    n = wins + losses
    if n == 0:
        return SignTest(wins, losses, ties, 1.0)
    extreme = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(extreme, n + 1)) / (2**n)
    return SignTest(wins, losses, ties, min(1.0, 2 * tail))


def bootstrap(
    units: list[tuple[float, float]],
    *,
    iterations: int = 10_000,
    seed: int = 2026,
    pct_low: float = 2.5,
    pct_high: float = 97.5,
) -> Interval:
    """Percentile bootstrap for a pooled rate, resampling whole units.

    Each unit is `(numerator, denominator)` for one repository, and a resample
    draws repositories with replacement, then pools. That is the right unit:
    the corpus is six hand-picked repositories, so repository choice carries
    most of the uncertainty in any pooled figure, and resampling files inside a
    fixed six would report an interval that ignores it.

    Seeded, because METHODOLOGY rule 5's spirit applies to statistics too: a
    number that changes when you run it again is not a result. Same seed and
    same input give the same interval, which the determinism gate asserts.

    `k` and `n` on the returned Interval are the observed pooled totals; only
    the bounds come from the resampling.
    """
    if not units:
        return Interval(0, 0, float("nan"), 0.0, 1.0, "bootstrap")
    num = sum(u[0] for u in units)
    den = sum(u[1] for u in units)
    point = num / den if den else float("nan")

    rng = random.Random(seed)
    m = len(units)
    rates: list[float] = []
    for _ in range(iterations):
        picked = [units[rng.randrange(m)] for _ in range(m)]
        d = sum(p[1] for p in picked)
        if d:
            rates.append(sum(p[0] for p in picked) / d)
    if not rates:
        return Interval(int(num), int(den), point, 0.0, 1.0, "bootstrap")
    rates.sort()

    def _pct(q: float) -> float:
        idx = min(len(rates) - 1, max(0, int(round(q / 100 * (len(rates) - 1)))))
        return rates[idx]

    return Interval(int(num), int(den), point, _pct(pct_low), _pct(pct_high), "bootstrap")
