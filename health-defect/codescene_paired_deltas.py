"""Paired significance for the head-to-head metrics beyond AUC.

``codescene_headtohead.py`` reports a paired DeLong test for AUC, but for Popt,
recall@20%LOC and defect concentration it only reports each tool's separate CI.
Two overlapping CIs are NOT a test on the difference. This script runs the
correct paired test: a **repo-cluster bootstrap of the per-replicate delta**
(Repowise metric − CodeScene metric) on the *same* resampled repos, so the two
tools see identical files every replicate and the delta's CI answers "is the gap
real?". The CI excluding 0 (and the one-sided bootstrap p) is the demonstrated
significance.

Cache-only: rebuilds the paired corpora from the committed CodeScene score cache
(no CLI calls, no token needed). Deterministic (seeded).

    ../../.venv/Scripts/python.exe codescene_paired_deltas.py --label keyword
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Callable

from codescene_comparison import _category_stats
from codescene_headtohead import build_paired_corpus
from statistical_rigor import popt_metric, precision20_metric, recall20_metric

_RESULTS = Path(__file__).resolve().parent.parent / "results"


# Metric callables on a file list (higher = better tool, except where noted).
def _popt(rows: list[dict]) -> float | None:
    return popt_metric(rows)


def _recall20(rows: list[dict]) -> float | None:
    return recall20_metric(rows)


def _precision20(rows: list[dict]) -> float | None:
    return precision20_metric(rows)


def _density_kloc(rows: list[dict]) -> float | None:
    c = _category_stats(rows)
    hi = c["healthy"]["defects_per_kloc"]
    lo = c["alert"]["defects_per_kloc"]
    return lo / hi if hi > 0 else None


def _density_dpf(rows: list[dict]) -> float | None:
    c = _category_stats(rows)
    hi = c["healthy"]["mean_defects_per_file"]
    lo = c["alert"]["mean_defects_per_file"]
    return lo / hi if hi > 0 else None


_METRICS: dict[str, Callable[[list[dict]], float | None]] = {
    "popt": _popt,
    "recall_at_20pct_loc": _recall20,
    "precision_at_20pct_loc": _precision20,
    "defects_per_kloc_ratio": _density_kloc,
    "mean_defects_per_file_ratio": _density_dpf,
}


def paired_delta_ci(
    rw_corpus: dict[str, list[dict]],
    cs_corpus: dict[str, list[dict]],
    metric: Callable[[list[dict]], float | None],
    *,
    n_boot: int,
    seed: int,
) -> dict:
    """Repo-cluster bootstrap of the pooled delta (Repowise − CodeScene).

    Each replicate resamples repos with replacement, pools each tool's files
    over the *same* chosen repos, computes the metric for each tool on that
    pooled set, and takes the difference — so the pairing is preserved (identical
    files, only the score column differs). Returns point delta, 95% CI, and the
    two-sided/one-sided bootstrap p that the delta is 0/≤0.
    """
    names = list(rw_corpus)

    def pooled_delta(chosen: list[str]) -> float | None:
        rw = [r for nm in chosen for r in rw_corpus[nm]]
        cs = [r for nm in chosen for r in cs_corpus[nm]]
        a, b = metric(rw), metric(cs)
        if a is None or b is None:
            return None
        return a - b

    point = pooled_delta(names)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        chosen = [names[rng.randrange(len(names))] for _ in range(len(names))]
        v = pooled_delta(chosen)
        if v is not None and v == v:
            samples.append(v)
    samples.sort()

    def pct(q: float) -> float | None:
        if not samples:
            return None
        pos = q * (len(samples) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(samples) - 1)
        return samples[lo] * (hi - pos) + samples[hi] * (pos - lo)

    p_le0 = sum(1 for x in samples if x <= 0) / len(samples) if samples else None
    p_ge0 = sum(1 for x in samples if x >= 0) / len(samples) if samples else None
    # Two-sided bootstrap p (one convention everywhere): 2x the smaller tail,
    # capped at 1. Direction-agnostic — tests "is the gap nonzero?", which is the
    # honest question for every axis including the one the external tool leads.
    p_two_sided = (
        min(1.0, 2.0 * min(p_le0, p_ge0)) if samples else None
    )
    return {
        "delta_point": point,
        "lo": pct(0.025),
        "hi": pct(0.975),
        "n_boot": len(samples),
        "p_two_sided": p_two_sided,
        # one-sided tails retained for diagnostics only; the report uses two-sided.
        "p_boot_le0": p_le0,
        "p_boot_ge0": p_ge0,
        "excludes_zero": (pct(0.025) is not None
                          and (pct(0.025) > 0 or pct(0.975) < 0)),
    }


def run(repos: list[str], label: str, *, n_boot: int, seed: int) -> dict:
    rw_corpus, cs_corpus, coverage = build_paired_corpus(
        repos, label, do_score=False, timeout=120
    )
    out = {
        "label": label,
        "n_boot": n_boot,
        "seed": seed,
        "repos": list(rw_corpus),
        "n_paired_files": sum(len(v) for v in cs_corpus.values()),
        "paired_deltas": {},
    }
    for mname, mfn in _METRICS.items():
        out["paired_deltas"][mname] = paired_delta_ci(
            rw_corpus, cs_corpus, mfn, n_boot=n_boot, seed=seed
        )
    return out


def print_summary(r: dict) -> None:
    print(f"\n{'='*82}")
    print(f"PAIRED DELTAS (Repowise - CodeScene), repo-cluster bootstrap "
          f"({r['label']} labels)")
    print(f"{'='*82}")
    print(f"repos: {len(r['repos'])}  paired files: {r['n_paired_files']}  "
          f"n_boot: {r['n_boot']}")
    print(f"\n{'metric':30} {'Δ (RW-CS) [95% CI]':>30} {'p(2-sided)':>11}  verdict")
    print("-" * 82)
    for m, d in r["paired_deltas"].items():
        dp = d["delta_point"]
        if dp is None:
            print(f"  {m:28} {'n/a':>30}")
            continue
        ci = f"{dp:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
        excl = d["excludes_zero"]
        p = d["p_two_sided"]
        verdict = ("SIGNIFICANT" if excl else "n.s. (CI spans 0)")
        print(f"  {m:28} {ci:>30} {p:>11.4f}  {verdict}")
    print("\n  excludes_zero ⇒ the paired gap is significant at 95%. "
          "p is the TWO-SIDED bootstrap prob. that the gap is zero.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=None)
    ap.add_argument("--label", default="keyword",
                    choices=["keyword", "szz", "szz_b", "issue"])
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--out", type=Path,
                    default=_RESULTS / "codescene_paired_deltas.json")
    args = ap.parse_args()

    import yaml
    repos = args.repos
    if not repos:
        cfg = yaml.safe_load(
            (Path(__file__).resolve().parent / "config.yaml").read_text()
        )
        repos = [r["name"] for r in cfg["repos"]]

    rep = run(repos, args.label, n_boot=args.n_boot, seed=args.seed)
    print_summary(rep)
    args.out.write_text(json.dumps(rep, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
