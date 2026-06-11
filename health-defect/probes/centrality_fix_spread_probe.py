"""Centrality -> fix-spread (repair-cost) probe.

Hypothesis: dependency-graph centrality predicts not WHETHER a file breaks but
HOW FAR the eventual fix spreads (the co-change scatter / blast radius of the
post-T0 fix commits that touch the file).

Universe = files with >=1 post-T0 keyword fix commit (the "defective" files).
Targets:
  fix_spread_mean  - mean #distinct source files modified across the file's fixes
  fix_spread_max   - max  #distinct source files in any one of its fixes
  fix_cofiles_total- #distinct OTHER source files co-modified across all its fixes

Predictors (at T0): pagerank, betweenness, eigenvector, in_degree, out_degree.
Baselines: log-nloc, churn (commit_count_90d), co_change_scatter (historical,
recomputed from pre-T0 git log).

Read-only on git. No mutation. Outputs JSON scorecard + markdown report.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from scipy.stats import spearmanr

BENCH = Path(__file__).resolve().parents[2]
HD = BENCH / "health-defect"
RESULTS = BENCH / "results"
REPOS = BENCH / "repos"
OUT_JSON = RESULTS / "centrality_fix_spread_scorecard.json"
OUT_MD = RESULTS / "centrality_fix_spread_report.md"

sys.path.insert(0, str(HD))
from lib.defect_counter import find_fix_commits, resolve_t0_sha  # noqa: E402

CENTRALITY_VARIANTS = ["pagerank", "betweenness", "eigenvector", "in_degree", "out_degree"]
TARGETS = ["fix_spread_mean", "fix_spread_max", "fix_cofiles_total"]
CO_CHANGE_WINDOW_DAYS = 365  # match the centrality / change_times build window
MIN_FILES_PER_REPO = 5       # drop from per-repo stats (kept in pooled)


def git(args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr[:300]}")
    return r.stdout


def is_source(path, source_root, exts):
    return path.startswith(source_root) and any(path.endswith(e) for e in exts)


def commit_source_files(repo_dir, sha, source_root, exts):
    """Distinct source files touched by one commit (excludes merges upstream)."""
    out = git(["diff-tree", "--no-commit-id", "-r", "--name-only", sha], repo_dir)
    files = set()
    for f in out.strip().split("\n"):
        f = f.strip()
        if f and is_source(f, source_root, exts):
            files.add(f)
    return files


def build_fix_spread(repo_dir, t0_sha, cfg):
    """For each source file touched by >=1 post-T0 fix commit, compute spread."""
    source_root = cfg.get("source_root", "")
    exts = tuple(cfg.get("extensions", [".py"]))
    fixes = find_fix_commits(
        repo_dir, t0_sha, "HEAD", strategy="keyword",
        include=cfg.get("bug_keywords"), exclude=cfg.get("exclude_keywords"),
    )
    per_file_spreads = defaultdict(list)   # file -> [spread per fix]
    per_file_cofiles = defaultdict(set)    # file -> set of other source files
    n_fix_commits_touching_src = 0
    for sha, _msg in fixes:
        srcs = commit_source_files(repo_dir, sha, source_root, exts)
        if not srcs:
            continue
        n_fix_commits_touching_src += 1
        spread = len(srcs)
        for f in srcs:
            per_file_spreads[f].append(spread)
            per_file_cofiles[f] |= (srcs - {f})
    rows = {}
    for f, spreads in per_file_spreads.items():
        rows[f] = {
            "fix_spread_mean": sum(spreads) / len(spreads),
            "fix_spread_max": max(spreads),
            "fix_cofiles_total": len(per_file_cofiles[f]),
            "n_fixes": len(spreads),
        }
    return rows, len(fixes), n_fix_commits_touching_src


def build_cochange_scatter(repo_dir, t0_sha, cfg):
    """Historical co_change_scatter analog: for each source file, #distinct
    source partners that co-changed with it in >=2 pre-T0 commits within the
    window. Mirrors the biomarker's threshold (partner weight >= 2) without the
    decay weighting (raw co-occurrence count)."""
    source_root = cfg.get("source_root", "")
    exts = tuple(cfg.get("extensions", [".py"]))
    since = f"--since={CO_CHANGE_WINDOW_DAYS} days ago"
    # commits on/before t0, within window, no merges, with file lists
    out = git(["log", t0_sha, "--no-merges", since, "--name-only",
               "--format=__C__%H"], repo_dir)
    pair_counts = defaultdict(int)
    cur = []
    commits = 0

    def flush(files):
        nonlocal commits
        srcs = [f for f in files if is_source(f, source_root, exts)]
        if len(srcs) < 2 or len(srcs) > 50:  # cap mega-commits as the indexer does in spirit
            return
        commits += 1
        srcs = sorted(set(srcs))
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                pair_counts[(srcs[i], srcs[j])] += 1

    for line in out.split("\n"):
        if line.startswith("__C__"):
            flush(cur)
            cur = []
        elif line.strip():
            cur.append(line.strip())
    flush(cur)

    scatter = defaultdict(int)
    for (a, b), c in pair_counts.items():
        if c >= 2:
            scatter[a] += 1
            scatter[b] += 1
    return dict(scatter)


def rank_transform(vals):
    """Average-rank transform NORMALIZED to [0,1] within the group (ties
    averaged). Normalizing to [0,1] per repo is essential: raw 0..n-1 ranks
    would reintroduce repo-size as a between-repo scale and let a pooled
    Spearman be driven by repo size rather than within-repo association."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    if n > 1:
        ranks = [r / (n - 1) for r in ranks]
    return ranks


def spearman(x, y):
    if len(x) < 4:
        return None, None
    rho, p = spearmanr(x, y)
    if rho != rho:
        return None, None
    return float(rho), float(p)


def partial_spearman(x, y, z):
    """Spearman partial corr of x,y controlling z."""
    if len(x) < 4:
        return None
    rxy, _ = spearmanr(x, y)
    rxz, _ = spearmanr(x, z)
    ryz, _ = spearmanr(y, z)
    if any(v != v for v in (rxy, rxz, ryz)):
        return None
    denom = math.sqrt((1 - rxz**2) * (1 - ryz**2))
    if denom == 0:
        return None
    return float((rxy - rxz * ryz) / denom)


def main():
    cfg_all = yaml.safe_load(open(HD / "config.yaml", encoding="utf-8"))
    repos = cfg_all["repos"]

    per_repo = {}          # repo -> {file -> merged record}
    repo_meta = {}
    for rc in repos:
        name = rc["name"]
        rdir = REPOS / name
        cdir = RESULTS / f"health_defect_{name}"
        cpath = cdir / "graph_centrality.json"
        if not rdir.exists() or not cpath.exists():
            continue
        cent = json.load(open(cpath, encoding="utf-8"))
        t0_sha = cent.get("_meta", {}).get("t0_sha") or resolve_t0_sha(str(rdir), rc["t0_date"])
        joined = {d["file_path"]: d for d in json.load(open(cdir / "joined_data.json", encoding="utf-8"))}

        fix_rows, n_fixes, n_fix_src = build_fix_spread(str(rdir), t0_sha, rc)
        scatter = build_cochange_scatter(str(rdir), t0_sha, rc)

        recs = {}
        for f, fr in fix_rows.items():
            rec = dict(fr)
            rec["file"] = f
            for v in CENTRALITY_VARIANTS:
                rec[v] = cent.get(v, {}).get(f, 0.0)
            j = joined.get(f, {})
            rec["nloc"] = j.get("nloc")
            rec["log_nloc"] = math.log1p(j["nloc"]) if j.get("nloc") else None
            rec["churn"] = j.get("commit_count_90d")
            rec["co_change_scatter"] = scatter.get(f, 0)
            recs[f] = rec
        per_repo[name] = recs
        repo_meta[name] = {
            "lang": rc["language"], "t0_sha": t0_sha,
            "n_fix_commits": n_fixes, "n_fix_commits_touching_src": n_fix_src,
            "n_fixed_files": len(recs),
        }
        print(f"{name:12s} lang={rc['language']:10s} fixes={n_fixes:4d} "
              f"touch_src={n_fix_src:4d} fixed_files={len(recs):4d}", flush=True)

    # ---------- per-repo spearman ----------
    per_repo_rho = defaultdict(lambda: defaultdict(dict))  # variant->target->{repo:rho}
    for name, recs in per_repo.items():
        files = list(recs.values())
        if len(files) < MIN_FILES_PER_REPO:
            continue
        for v in CENTRALITY_VARIANTS:
            xs = [r[v] for r in files]
            if len(set(xs)) < 2:  # constant predictor (e.g. betweenness all 0)
                continue
            for t in TARGETS:
                ys = [r[t] for r in files]
                rho, p = spearman([float(x) for x in xs], [float(y) for y in ys])
                if rho is not None:
                    per_repo_rho[v][t][name] = rho

    # ---------- pooled (within-repo rank-transform then pool) ----------
    def pooled_rho(variant, target, control=None, repo_subset=None):
        X, Y, Z = [], [], []
        for name, recs in per_repo.items():
            files = list(recs.values())
            if repo_subset is not None and name not in repo_subset:
                continue
            if len(files) < 4:
                continue
            # drop files with a missing predictor/target/control value
            keep = [r for r in files
                    if r.get(variant) is not None and r.get(target) is not None
                    and (control is None or r.get(control) is not None)]
            if len(keep) < 4:
                continue
            xs = [r[variant] for r in keep]
            ys = [r[target] for r in keep]
            if control:
                zs = rank_transform([float(r[control]) for r in keep])
            if len(set(xs)) < 2 or len(set(ys)) < 2:
                continue
            xr = rank_transform([float(x) for x in xs])
            yr = rank_transform([float(y) for y in ys])
            X += xr
            Y += yr
            if control:
                Z += zs
        if len(X) < 4:
            return None
        if control:
            return partial_spearman(X, Y, Z)
        return spearman(X, Y)[0]

    # ---------- repo-cluster bootstrap CI ----------
    def boot_ci(variant, target, control=None, n_boot=2000, seed=4242):
        rng = random.Random(seed)
        names = [n for n, recs in per_repo.items() if len(recs) >= 4]
        # precompute per-repo rank-transformed vectors
        cache = {}
        for name in names:
            files = list(per_repo[name].values())
            keep = [r for r in files
                    if r.get(variant) is not None and r.get(target) is not None
                    and (control is None or r.get(control) is not None)]
            if len(keep) < 4:
                continue
            xs = [r[variant] for r in keep]
            ys = [r[target] for r in keep]
            if len(set(xs)) < 2 or len(set(ys)) < 2:
                continue
            entry = {
                "x": rank_transform([float(v) for v in xs]),
                "y": rank_transform([float(v) for v in ys]),
            }
            if control:
                entry["z"] = rank_transform([float(r[control]) for r in keep])
            cache[name] = entry
        usable = list(cache.keys())
        if len(usable) < 2:
            return None
        samples = []
        for _ in range(n_boot):
            chosen = [rng.choice(usable) for _ in usable]  # resample repos
            X, Y, Z = [], [], []
            for name in chosen:
                e = cache[name]
                m = len(e["x"])
                idx = [rng.randrange(m) for _ in range(m)]  # resample files
                X += [e["x"][i] for i in idx]
                Y += [e["y"][i] for i in idx]
                if control:
                    Z += [e["z"][i] for i in idx]
            if len(X) < 4:
                continue
            if control:
                val = partial_spearman(X, Y, Z)
            else:
                val = spearman(X, Y)[0]
            if val is not None:
                samples.append(val)
        if len(samples) < 50:
            return None
        samples.sort()
        lo = samples[int(0.025 * len(samples))]
        hi = samples[int(0.975 * len(samples))]
        return {"lo": lo, "hi": hi, "n_boot": len(samples)}

    headline = {}
    for v in CENTRALITY_VARIANTS:
        for t in TARGETS:
            pr = per_repo_rho[v][t]
            med = None
            if pr:
                vals = sorted(pr.values())
                med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals)//2-1]+vals[len(vals)//2])/2
            pooled = pooled_rho(v, t)
            ci = boot_ci(v, t)
            part_nloc = pooled_rho(v, t, control="log_nloc")
            ci_nloc = boot_ci(v, t, control="log_nloc")
            part_cc = pooled_rho(v, t, control="co_change_scatter")
            ci_cc = boot_ci(v, t, control="co_change_scatter")
            headline[f"{v}|{t}"] = {
                "variant": v, "target": t,
                "pooled_rho": pooled, "pooled_ci": ci,
                "partial_rho_lognloc": part_nloc, "partial_ci_lognloc": ci_nloc,
                "partial_rho_cochange": part_cc, "partial_ci_cochange": ci_cc,
                "per_repo_median_rho": med, "n_repos": len(pr),
                "n_files_pooled": sum(len(r) for r in per_repo.values()),
            }

    # ---------- baselines (predictor -> target pooled rho) ----------
    baselines = {}
    for base in ["log_nloc", "churn", "co_change_scatter"]:
        for t in TARGETS:
            pooled = pooled_rho(base, t)
            ci = boot_ci(base, t)
            baselines[f"{base}|{t}"] = {
                "predictor": base, "target": t,
                "pooled_rho": pooled, "pooled_ci": ci,
            }

    # ---------- spread distribution sanity ----------
    all_spreads = []
    for recs in per_repo.values():
        for r in recs.values():
            all_spreads.append(r["fix_spread_mean"])
    all_spreads.sort()
    n = len(all_spreads)
    dist = {
        "n_fixed_files_total": n,
        "spread_mean": sum(all_spreads)/n if n else None,
        "spread_median": all_spreads[n//2] if n else None,
        "spread_p90": all_spreads[int(0.9*n)] if n else None,
        "spread_max": all_spreads[-1] if n else None,
    }

    scorecard = {
        "experiment": "centrality -> fix-spread (repair cost)",
        "universe": "files with >=1 post-T0 keyword fix commit",
        "co_change_baseline": "historical: #distinct source partners co-changing >=2x in pre-T0 365d window",
        "repo_meta": repo_meta,
        "distribution": dist,
        "verdict": "PARK (clean null) - no centrality variant predicts fix spread within-repo; CIs straddle 0",
        "headline": headline,
        "baselines": baselines,
        "per_repo_rho": {v: {t: per_repo_rho[v][t] for t in TARGETS} for v in CENTRALITY_VARIANTS},
    }
    OUT_JSON.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print("\nWrote", OUT_JSON)
    write_report(scorecard)
    print("Wrote", OUT_MD)


def fmt(x, d=3):
    return "n/a" if x is None else f"{x:+.{d}f}"


def fmtci(ci):
    if not ci:
        return "n/a"
    excl = "*" if (ci["lo"] > 0 or ci["hi"] < 0) else ""
    return f"[{ci['lo']:+.3f}, {ci['hi']:+.3f}]{excl}"


def write_report(sc):
    L = []
    L.append("# Centrality -> Fix-Spread (Repair-Cost) Probe\n")
    L.append("Hypothesis: dependency-graph centrality predicts not WHETHER a file breaks but ")
    L.append("HOW FAR the eventual fix spreads (blast radius of the post-T0 fix commits touching it).\n")
    L.append(f"\n- Universe: {sc['universe']}")
    L.append(f"\n- Co-change baseline: {sc['co_change_baseline']}")
    d = sc["distribution"]
    L.append(f"\n- Fixed files (corpus): {d['n_fixed_files_total']}; "
             f"spread mean={d['spread_mean']:.2f}, median={d['spread_median']:.2f}, "
             f"p90={d['spread_p90']:.2f}, max={d['spread_max']:.2f}\n")
    L.append("\n`*` on a CI = 95% CI excludes 0.\n")

    L.append("\n## Per-repo corpus\n\n")
    L.append("| repo | lang | fix commits | touching src | fixed files |\n")
    L.append("|---|---|---|---|---|\n")
    for name, m in sc["repo_meta"].items():
        L.append(f"| {name} | {m['lang']} | {m['n_fix_commits']} | "
                 f"{m['n_fix_commits_touching_src']} | {m['n_fixed_files']} |\n")

    L.append("\n## Headline: centrality x spread\n\n")
    L.append("| variant | target | pooled rho [95% CI] | partial \\| log-nloc [CI] | "
             "partial \\| co_change [CI] | per-repo median rho | n_repos |\n")
    L.append("|---|---|---|---|---|---|---|\n")
    for key, h in sc["headline"].items():
        L.append(f"| {h['variant']} | {h['target']} | {fmt(h['pooled_rho'])} {fmtci(h['pooled_ci'])} | "
                 f"{fmt(h['partial_rho_lognloc'])} {fmtci(h['partial_ci_lognloc'])} | "
                 f"{fmt(h['partial_rho_cochange'])} {fmtci(h['partial_ci_cochange'])} | "
                 f"{fmt(h['per_repo_median_rho'])} | {h['n_repos']} |\n")

    L.append("\n## Baselines (predictor x spread, pooled)\n\n")
    L.append("| predictor | target | pooled rho [95% CI] |\n")
    L.append("|---|---|---|\n")
    for key, b in sc["baselines"].items():
        L.append(f"| {b['predictor']} | {b['target']} | {fmt(b['pooled_rho'])} {fmtci(b['pooled_ci'])} |\n")

    L.append("\n## Verdict: PARK (clean null)\n\n")
    L.append(VERDICT)
    OUT_MD.write_text("".join(L), encoding="utf-8")


VERDICT = """\
Within the defective-file universe (a file already has >=1 post-T0 fix), NO
centrality variant predicts how far that file's fixes spread. After a within-repo
[0,1] rank normalization (mandatory: it strips the between-repo graph-size scale
that otherwise manufactures a spurious pooled rho), every centrality x spread
pooled rho sits at ~0 with a 95% repo-cluster-bootstrap CI that straddles 0. The
per-repo median rho was ~0 all along, which is the honest within-repo signal.

A methodological caution worth recording: an earlier run that pooled raw 0..n-1
ranks produced pooled rho of +0.5..+0.66 with CIs excluding 0 for EVERY variant.
That was a pure Simpson's-paradox artifact - large-graph repos have both higher
raw centrality ranks and larger fixes - and it vanished entirely once ranks were
normalized to [0,1] per repo. Any future probe pooling ranks across repos must
normalize, or it will hallucinate signal.

Subsumption is moot since nothing clears the first hurdle, but note the baselines
collapse too: log-nloc, churn (commit_count_90d) and the historical
co_change_scatter analog are ALL ~0 within-repo against fix spread. The two
marginal stars (out_degree -> fix_cofiles_total +0.145; co_change_scatter ->
fix_cofiles_total +0.144) barely exclude 0, do not replicate across the other two
spread targets, and are unremarkable among 24+9 simultaneous tests - i.e.
multiple-comparison noise, not a finding.

Interpretation: conditional on a file being touched by a fix, the blast radius of
that fix is essentially unpredictable from static dependency structure, size, or
recent churn. Repair cost looks driven by what the bug happens to be, not by where
the file sits in the import graph.

Limitations: keyword fix-commit labels are noisy (conventional-commit subjects
only); spread = file count, not severity or true semantic coupling; the universe is
small (~670 fixed files pooled, several repos < 10) so within-repo rho per repo is
high-variance; the co_change_scatter baseline is a faithful but un-decay-weighted
reconstruction of the shipped biomarker. None of these caveats would plausibly
rescue a signal this flat.

Product implication: an "expected blast radius of a future fix" badge on the file
health view is NOT supported by this data and should not be built on centrality (or
on size/churn/co-change). The honest product takeaway is the negative one: blast
radius is not forecastable from the file's structural position, so do not promise it.
"""


if __name__ == "__main__":
    main()
