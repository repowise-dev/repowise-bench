"""Turn the graded Layer A cells into the numbers RESULT.md is allowed to print.

Nothing here re-queries or re-grades. It reads `graded__<tag>__<arm>.jsonl`
(ContextBench's own output), the cells beside them, and the trees, and computes
the four things the reporting rules for this run demand:

1. **Coverage with precision and files-served beside it**, never averaged into
   one figure, and the two repowise rows never pooled (finding E7).
2. **Pooled AND mean-of-per-instance AND median AND the largest instance's
   share.** A pooled percentage alone at n=15 is not reportable here: gold
   counts are 7,5,5,4,3,3,2,2 then seven 1s, so the pooled figure is carried by
   a few instances. Where pooled and mean-of-ratios disagree in SIGN, the number
   is an artifact and is reported as one.
3. **Coverage regressed on repo size.** Finding D predicts our coverage should
   degrade with size: symbol density halves across the range (0.86 per file at
   2,322 files against 0.41 at 28,346) and file-page eligibility falls 32% to
   18%. The dev 15 span a 12x size range, so this is free to test and names a
   mechanism (A7/A35) rather than a mean.
4. **The non-code gold split.** 8 of the 38 gold files are `.md` or `.json`, and
   three of the five arms index neither. A miss on those files is a file-type
   exclusion, not a retrieval failure, and the two must not be added together.

The non-code split is computed from each arm's own ranked list with `r5.
path_matches`, the same matcher the harness uses everywhere else, and is
labelled as harness-computed rather than ContextBench-graded, because
ContextBench reports coverage per instance and not per gold file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNG8 = HERE.parent / "rung8"
_spec = importlib.util.spec_from_file_location("canary8", RUNG8 / "canary_allarms.py")
c8 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c8)
r5 = c8.r5

BENCH = c8.BENCH_ROOT
TASKS = BENCH / "data" / "cb_mui" / "swe_qa" / "tasks.json"
TREES = c8.TREES
SRC_EXT = (".ts", ".tsx", ".js", ".jsx")
NONCODE_EXT = (".md", ".mdx", ".json")


def median(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def src_file_counts(tasks) -> dict:
    """`.ts/.tsx/.js/.jsx` at each instance's own base_commit.

    Same axis as the build-cost curve in RESULT.md section A, counted the same
    way (`git ls-tree` at the pinned commit), so the regression below is against
    the same x the exponents were fitted on rather than a second definition of
    "size" that happens to be lying around.
    """
    out = {}
    for t in tasks:
        short = t["id"].split("_", 1)[1]
        tree = TREES / f"lb-codegraph-cbmui-{short}-material-ui"
        p = subprocess.run(["git", "-C", str(tree), "ls-tree", "-r", "--name-only", "HEAD"],
                           capture_output=True, text=True)
        names = p.stdout.splitlines()
        out[t["id"]] = sum(1 for n in names if n.lower().endswith(SRC_EXT))
    return out


def linfit(xs, ys):
    """OLS slope, intercept, r and a two-sided p, stdlib only.

    `scipy` is not a dependency of this results tree and a 15-point regression
    does not justify making it one. The p is from the t statistic on r with
    n-2 df, via an incomplete-beta continued fraction.
    """
    n = len(xs)
    if n < 3:
        return {}
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return {"n": n, "slope": None, "r": None, "p": None}
    slope = sxy / sxx
    r = sxy / math.sqrt(sxx * syy)
    df = n - 2
    r = max(-0.999999, min(0.999999, r))
    t = r * math.sqrt(df / (1 - r * r))
    p = _betai(0.5 * df, 0.5, df / (df + t * t))
    return {"n": n, "slope": slope, "intercept": my - slope * mx,
            "r": round(r, 4), "r2": round(r * r, 4), "p": round(p, 4)}


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def _betacf(a, b, x, itmax=200, eps=3e-7):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        c = 1 + aa / c
        if abs(d) < 1e-30:
            d = 1e-30
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        c = 1 + aa / c
        if abs(d) < 1e-30:
            d = 1e-30
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < eps:
            break
    return h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="dev15")
    ap.add_argument("--arms", nargs="+",
                    default=["repowise", "repowise-search", "codegraph", "crg", "graphify"])
    args = ap.parse_args()

    tasks = json.loads(TASKS.read_text(encoding="utf-8"))
    by_inst = {t["instance_id"]: t for t in tasks}
    sizes = src_file_counts(tasks)

    gold_ext: dict = {}
    for t in tasks:
        for f in sorted(set(t["gold_files"])):
            gold_ext.setdefault(t["id"], []).append(f)

    out: dict = {
        "tag": args.tag,
        "src_files": sizes,
        "gold_counts": {t["id"]: len(set(t["gold_files"])) for t in tasks},
        "arms": {},
    }

    for arm in args.arms:
        gp = HERE / f"graded__{args.tag}__{arm}.jsonl"
        if not gp.exists():
            continue
        rows = [json.loads(x) for x in gp.read_text(encoding="utf-8").splitlines()
                if x.strip()]
        valid = [r for r in rows if "file" in r.get("final", {})]
        per = {}
        for r in valid:
            f = r["final"]["file"]
            t = by_inst[r["instance_id"]]
            per[t["id"]] = {
                "coverage": f["coverage"], "precision": f["precision"],
                "intersection": f["intersection"], "gold_size": f["gold_size"],
                "pred_size": f["pred_size"], "src_files": sizes[t["id"]],
            }
        cov = [v["coverage"] for v in per.values()]
        prec = [v["precision"] for v in per.values()]
        served = [v["pred_size"] for v in per.values()]
        tot_i = sum(v["intersection"] for v in per.values())
        tot_g = sum(v["gold_size"] for v in per.values())

        # Which instance carries the pooled number. Reported because at n=15
        # with gold counts 7,5,5,4,3,3,2,2 and seven 1s, one instance can be a
        # fifth of the denominator.
        top = max(per.items(), key=lambda kv: kv[1]["gold_size"])

        # NON-CODE GOLD, computed from the arm's own ranked list.
        nc_hit = nc_tot = code_hit = code_tot = 0
        for t in tasks:
            cell = HERE / "cells" / args.tag / f"{arm}__{t['id']}.json"
            if not cell.exists():
                continue
            c = json.loads(cell.read_text(encoding="utf-8"))
            ranked = c.get("query", {}).get("ranked") or []
            for g in sorted(set(t["gold_files"])):
                hit = any(r5.path_matches(p, g) for p in ranked)
                if g.lower().endswith(NONCODE_EXT):
                    nc_tot += 1
                    nc_hit += hit
                else:
                    code_tot += 1
                    code_hit += hit

        xs = [v["src_files"] for v in per.values()]
        out["arms"][arm] = {
            "n": len(per),
            "coverage": {
                "pooled": round(tot_i / tot_g, 4) if tot_g else None,
                "mean_of_instances": round(sum(cov) / len(cov), 4) if cov else None,
                "median": round(median(cov), 4) if cov else None,
                "gold_hit": tot_i, "gold_total": tot_g,
                "top_instance": top[0],
                "top_instance_share_of_gold": round(top[1]["gold_size"] / tot_g, 4)
                if tot_g else None,
            },
            "precision": {
                "mean": round(sum(prec) / len(prec), 4) if prec else None,
                "median": round(median(prec), 4) if prec else None,
            },
            "files_served": {
                "mean": round(sum(served) / len(served), 2) if served else None,
                "median": median(served),
            },
            "noncode_gold": {
                "hit": nc_hit, "total": nc_tot,
                "rate": round(nc_hit / nc_tot, 4) if nc_tot else None,
                "code_hit": code_hit, "code_total": code_tot,
                "code_rate": round(code_hit / code_tot, 4) if code_tot else None,
                "source": "harness r5.path_matches over the arm's ranked list",
            },
            "size_vs_coverage": linfit(xs, cov),
            "size_vs_coverage_log": linfit([math.log(x) for x in xs], cov),
            "per_instance": per,
        }

    p = HERE / f"analysis__{args.tag}.json"
    p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    print(f"{'arm':<17}{'n':>3}{'cov_pool':>9}{'cov_mean':>9}{'cov_med':>8}"
          f"{'prec_mean':>10}{'served':>8}{'nc_gold':>9}{'code':>8}"
          f"{'size_slope':>12}{'r':>8}{'p':>7}")
    for arm, a in out["arms"].items():
        c, f = a["coverage"], a["size_vs_coverage"]
        print(f"{arm:<17}{a['n']:>3}{c['pooled']:>9}{c['mean_of_instances']:>9}"
              f"{c['median']:>8}{a['precision']['mean']:>10}"
              f"{a['files_served']['mean']:>8}"
              f"{a['noncode_gold']['hit']}/{a['noncode_gold']['total']:<7}"
              f"{a['noncode_gold']['code_hit']}/{a['noncode_gold']['code_total']:<5}"
              f"{(f.get('slope') or 0):>12.3e}{(f.get('r') or 0):>8.3f}"
              f"{(f.get('p') or 0):>7.3f}")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
