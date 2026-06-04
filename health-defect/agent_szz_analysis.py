#!/usr/bin/env python3
"""agent_szz_analysis.py — defect-introduction rate by authorship tier.

Joins the SZZ inducing sets (``agent_szz_induction.py``) with the per-commit
label records (``agent_defect_labels.py``) and asks, per repo then pooled:
**do agent-authored window commits induce defects at a different rate than
human commits in the same repo?**

Design (bench hygiene):
  * outcome: commit is blamed (AG-SZZ) as bug-inducing by >=1 window fix,
    under three fix-set variants — ``raw`` (keyword fixes),
    ``spam_collapsed`` (minus self-fix churn + reverts), ``fully_gated``
    (issue-gated minus churn) — the "does gating rescue attribution?" axis;
  * eligibility: commit is >=90 d older than repo HEAD (right-censor guard);
  * unadjusted: mean within-repo delta (tier - human), cluster-bootstrap
    95% CI over repos, MIN_N per cell;
  * adjusted (Kamei-style size/churn controls): pooled logistic regression
    with repo fixed effects + log1p(lines added/deleted/files touched),
    tier dummies; cluster bootstrap (resample repos) CI on tier odds ratios.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_szz_analysis.py \
        --szz-dir <data>/agent-repos/_szz --labels-dir <data>/agent-repos/_labels \
        --out-dir <data>/agent-repos/_szz
"""
from __future__ import annotations

import argparse
import json
import random
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MIN_N = 30
ELIG_DAYS = 90
VARIANTS = ("raw", "spam_collapsed", "fully_gated")
# AG-SZZ is the headline; B-SZZ is the mandatory sensitivity check — AG drops
# inducers that are themselves fixes, and agents fix more, so AG could
# mechanically shield agent commits from blame.
SZZ_KIND = "ag"


def log(msg: str) -> None:
    print(msg, flush=True)


def group_of(c: dict) -> str:
    return f"t{c['tier']}" if c["agent"] else "human"


def fix_variant_shas(commits: list[dict]) -> dict[str, set[str]]:
    clean = lambda c: not c["self_fix"] and not c["is_revert"] and not c["was_reverted"]  # noqa: E731
    return {
        "raw": {c["sha"] for c in commits if c["is_fix"]},
        "spam_collapsed": {c["sha"] for c in commits if c["is_fix"] and clean(c)},
        "fully_gated": {c["sha"] for c in commits if c["issue_gated"] and clean(c)},
    }


def load_repo(szz_path: Path, labels_path: Path,
              szz_kind: str = SZZ_KIND) -> tuple[dict, list[dict]]:
    szz = json.loads(szz_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    commits = [c for c in labels["commits"] if c["n_files"] > 0]
    head_ts = max(c["ts"] for c in labels["commits"])
    variants = fix_variant_shas(labels["commits"])

    # blamed-as-inducing sets per variant (window commits only, by construction
    # of the join below)
    induced: dict[str, set[str]] = {v: set() for v in VARIANTS}
    for fix_sha, sets in szz["inducing"].items():
        blamed = set(sets[szz_kind])
        for v in VARIANTS:
            if fix_sha in variants[v]:
                induced[v] |= blamed

    churn = szz.get("churn", {})
    rows = []
    for c in commits:
        if head_ts - c["ts"] < ELIG_DAYS * 86400:
            continue
        ch = churn.get(c["sha"])
        rows.append({
            "repo": szz["repo"], "sha": c["sha"], "group": group_of(c),
            "agent": c["agent"], "n_files": c["n_files"],
            "la": ch["la"] if ch else None, "ld": ch["ld"] if ch else None,
            **{f"induced_{v}": int(c["sha"] in induced[v]) for v in VARIANTS},
        })
    summary = {"repo": szz["repo"], "dir": szz["dir"],
               "n_eligible": len(rows),
               "szz_stats": szz["stats"],
               "n_fixes_variant": {v: len(variants[v]) for v in VARIANTS},
               "n_induced_window": {v: len(induced[v]) for v in VARIANTS}}
    return summary, rows


# ---------------------------------------------------------------- unadjusted --


def per_repo_rates(rows: list[dict]) -> dict[str, dict]:
    by: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for g in (r["group"], f"agent:{r['agent']}" if r["agent"] else None):
            if g:
                for v in VARIANTS:
                    by[g][v].append(r[f"induced_{v}"])
    out = {}
    for g, m in by.items():
        out[g] = {"n": len(m["raw"])}
        for v in VARIANTS:
            vals = m[v]
            out[g][v] = round(sum(vals) / len(vals), 4) if vals else None
    return out


def pooled_delta(per_repo: dict[str, dict], variant: str, group: str,
                 rng: random.Random, n_boot: int = 2000) -> dict | None:
    deltas = []
    for repo, gm in per_repo.items():
        g, h = gm.get(group), gm.get("human")
        if not g or not h or g["n"] < MIN_N or h["n"] < MIN_N:
            continue
        deltas.append((repo, g[variant] - h[variant], g[variant], h[variant]))
    if len(deltas) < 3:
        return None
    vals = [d[1] for d in deltas]
    boots = sorted(
        sum(s := [rng.choice(vals) for _ in vals]) / len(s) for _ in range(n_boot))
    return {"mean_delta": round(sum(vals) / len(vals), 4),
            "ci95": [round(boots[int(0.025 * n_boot)], 4),
                     round(boots[int(0.975 * n_boot)], 4)],
            "n_repos": len(deltas),
            "per_repo": [{"repo": r, "delta": round(d, 4), "group": g,
                          "human": h} for r, d, g, h in deltas]}


# ------------------------------------------------------------------ adjusted --


def fit_adjusted(rows: list[dict], variant: str) -> dict | None:
    """Pooled logit: induced ~ tier + log1p(la) + log1p(ld) + log1p(nf) + repo FE."""
    import pandas as pd
    import statsmodels.api as sm

    df = pd.DataFrame([r for r in rows if r["la"] is not None])
    if df.empty:
        return None
    df["y"] = df[f"induced_{variant}"]
    for col, src in (("log_la", "la"), ("log_ld", "ld"), ("log_nf", "n_files")):
        df[col] = np.log1p(df[src])
    tiers = [t for t in ("t1", "t2", "t3") if (df["group"] == t).sum() >= MIN_N]
    if not tiers:
        return None

    def design(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        X = pd.DataFrame({"const": 1.0, "log_la": d["log_la"],
                          "log_ld": d["log_ld"], "log_nf": d["log_nf"]})
        for t in tiers:
            X[t] = (d["group"] == t).astype(float)
        for repo in sorted(d["repo"].unique())[1:]:
            X[f"fe_{repo}"] = (d["repo"] == repo).astype(float)
        return X, d["y"]

    def fit(d: pd.DataFrame) -> dict[str, float] | None:
        X, y = design(d)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = sm.Logit(y, X).fit(disp=0, maxiter=200)
            return {t: float(res.params[t]) for t in tiers}
        except Exception:  # noqa: BLE001
            return None

    point = fit(df)
    if point is None:
        return None
    repos = sorted(df["repo"].unique())
    rng = np.random.default_rng(20260604)
    boots: dict[str, list[float]] = {t: [] for t in tiers}
    for _ in range(300):
        sample = list(rng.choice(repos, size=len(repos), replace=True))
        bd = pd.concat([df[df["repo"] == r] for r in sample], ignore_index=True)
        coefs = fit(bd)
        if coefs:
            for t in tiers:
                boots[t].append(coefs[t])
    out = {}
    for t in tiers:
        bs = sorted(boots[t])
        if len(bs) < 100:
            continue
        out[t] = {"odds_ratio": round(float(np.exp(point[t])), 3),
                  "or_ci95": [round(float(np.exp(bs[int(0.025 * len(bs))])), 3),
                              round(float(np.exp(bs[int(0.975 * len(bs))])), 3)],
                  "n_boot_ok": len(bs)}
    return {"n_rows": int(len(df)), "n_dropped_no_churn": int(len(rows) - len(df)),
            "tiers": out}


# ---------------------------------------------------------------------- main --


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--szz-dir", type=Path, required=True)
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20260604)
    ap.add_argument("--szz-kind", choices=("ag", "b"), default=SZZ_KIND)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    suffix = "" if args.szz_kind == "ag" else f"_{args.szz_kind}"

    all_rows: list[dict] = []
    summaries: list[dict] = []
    per_repo: dict[str, dict] = {}
    for szz_path in sorted(args.szz_dir.glob("*.json")):
        if szz_path.name.startswith("_") or szz_path.stem == "szz_induction":
            continue
        labels_path = args.labels_dir / szz_path.name
        if not labels_path.exists():
            continue
        summary, rows = load_repo(szz_path, labels_path, args.szz_kind)
        summaries.append(summary)
        per_repo[summary["repo"]] = per_repo_rates(rows)
        all_rows.extend(rows)
        log(f"{summary['dir']}: eligible {summary['n_eligible']}, induced(window) "
            + ", ".join(f"{v}={summary['n_induced_window'][v]}" for v in VARIANTS))

    contrasts = {v: {g: pooled_delta(per_repo, v, g, rng) for g in ("t1", "t2", "t3")}
                 for v in VARIANTS}
    adjusted = {v: fit_adjusted(all_rows, v) for v in VARIANTS}

    result = {"generated": datetime.now(timezone.utc).isoformat(),
              "min_n": MIN_N, "elig_days": ELIG_DAYS, "szz_kind": args.szz_kind,
              "repos": summaries, "per_repo_rates": per_repo,
              "contrasts": contrasts, "adjusted": adjusted}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"szz_induction{suffix}.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")

    lines = [f"# SZZ defect-introduction by authorship tier "
             f"({args.szz_kind.upper()}-SZZ, within-repo)",
             f"\nGenerated {result['generated']} · window 2025-06 → HEAD · "
             f"eligibility ≥{ELIG_DAYS} d before HEAD · MIN_N={MIN_N} per cell · "
             "Δ = tier rate − human rate, same repo · cluster-bootstrap 95% CI.\n",
             "## Unadjusted within-repo contrasts\n",
             "| fix-set variant | tier | mean Δ | 95% CI | n repos |",
             "|---|---|--:|---|--:|"]
    for v in VARIANTS:
        for g in ("t1", "t2", "t3"):
            c = contrasts[v][g]
            if not c:
                continue
            lo, hi = c["ci95"]
            star = " **\\***" if lo > 0 or hi < 0 else ""
            lines.append(f"| {v} | {g} | {c['mean_delta']:+.4f}{star} | "
                         f"[{lo:+.4f}, {hi:+.4f}] | {c['n_repos']} |")
    lines += ["", "## Adjusted (logit: tier + log churn/size + repo FE, "
              "cluster-bootstrap OR CI)\n",
              "| fix-set variant | tier | odds ratio | 95% CI | rows |",
              "|---|---|--:|---|--:|"]
    for v in VARIANTS:
        a = adjusted[v]
        if not a:
            continue
        for t, d in a["tiers"].items():
            lo, hi = d["or_ci95"]
            star = " **\\***" if lo > 1 or hi < 1 else ""
            lines.append(f"| {v} | {t} | {d['odds_ratio']}{star} | "
                         f"[{lo}, {hi}] | {a['n_rows']} |")
    lines += ["", "\\* CI excludes the null.", "",
              "## Per-repo induction rates (eligible commits)\n",
              "| repo | group | n | raw | spam-collapsed | fully-gated |",
              "|---|---|--:|--:|--:|--:|"]
    for repo, gm in sorted(per_repo.items()):
        for g in ("human", "t1", "t2", "t3"):
            r = gm.get(g)
            if not r or r["n"] < MIN_N:
                continue
            lines.append(f"| {repo} | {g} | {r['n']} | {r['raw']:.4f} | "
                         f"{r['spam_collapsed']:.4f} | {r['fully_gated']:.4f} |")
    md = args.out_dir / f"SZZ_INDUCTION{suffix.upper()}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote {md}")


if __name__ == "__main__":
    main()
