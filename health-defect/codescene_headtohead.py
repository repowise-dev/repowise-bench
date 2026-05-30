"""Same-repo, same-label head-to-head: Repowise vs. CodeScene Code-Health.

The literature comparison (``codescene_comparison.py``) puts *our* number on
*our* repos next to *their* published number on *their* repos — similar metric,
different corpus, so it cannot establish "better". This script removes that
confound: it scores the **same files** at the **same T0 commit** with **both**
tools, joins the **same** defect labels, and runs **both** through the identical
metric code path (defect-concentration, ROC AUC, Popt, partial-Spearman vs
NLOC) with cluster-bootstrap CIs — plus a **paired DeLong** test on the two
tools' AUCs over the pooled files (the field-standard correlated-ROC test).

Both score columns come from the *same* row set: ``results/<repo>/
joined_data.json`` already lists every T0-scored file with Repowise's health
score and NLOC; we add CodeScene's score for the identical files at the identical
T0 SHA (``lib/codescene_runner``), drop any file CodeScene can't score (recorded
as a coverage gap, not silently), and compare on the paired remainder.

Cache-only for the defect labels and Repowise scores (no re-index). CodeScene
scores are cached per repo under ``results/<repo>/codescene_scores.json`` and
resume across runs. Deterministic (seeded) so the report reproduces.

Usage::

    # smoke test on a 4-repo / 4-language subset first
    ../../.venv/Scripts/python.exe codescene_headtohead.py \
        --repos clap pydantic gin hono --label keyword
    # full corpus (drops --repos)
    ../../.venv/Scripts/python.exe codescene_headtohead.py --label keyword

Requires ``CS_ACCESS_TOKEN`` (free codescene.io PAT) and the ``cs`` binary
(``CS_BIN`` env, or the default stash path). Neither is committed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from codescene_comparison import _category_stats, _cluster_ci
from lib.codescene_runner import cs_available, load_cache, score_repo
from lib.defect_counter import resolve_t0_sha
from statistical_rigor import (
    HEADLINE_METRICS,
    _fmt,
    cluster_bootstrap_mean,
    delong_cov,
    load_repo,
    pooled_ci,
)
from lib.stats import spearman_correlation
import math

_BENCH = Path(__file__).resolve().parent
_RESULTS = _BENCH.parent / "results"
_REPOS_DIR = _BENCH.parent / "repos"

# Headline metrics where, for the score, higher = healthier (so the bench's
# risk = 10 - score convention applies identically to both tools).
_TOOLS = ("repowise", "codescene")


def _resolve_repo_dir(name: str) -> Path:
    """Mirror run_benchmark.resolve_repo_dir (handle the nested-clone layout)."""
    base = _REPOS_DIR / name
    nested = base / name
    if nested.exists() and (nested / ".git").exists():
        return nested
    return base


def _config_by_name() -> dict[str, dict]:
    cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
    return {r["name"]: r for r in cfg["repos"]}


# ---------------------------------------------------------------------------
# Build the paired corpora (one row set, two score columns)
# ---------------------------------------------------------------------------
def build_paired_corpus(
    repos: list[str],
    label: str,
    *,
    do_score: bool,
    timeout: int,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, dict]]:
    """Return ``(repowise_corpus, codescene_corpus, coverage_meta)``.

    Both corpora share the identical file set per repo — every file CodeScene
    returned a numeric score for. Files CodeScene skipped (``null`` = no scorable
    code, or absent at T0) are dropped from *both* and counted in coverage_meta,
    so the paired comparison is honest and the gap is reported, not hidden.
    """
    cfg = _config_by_name()
    rw_corpus: dict[str, list[dict]] = {}
    cs_corpus: dict[str, list[dict]] = {}
    coverage: dict[str, dict] = {}

    for name in repos:
        rw_rows = load_repo(name, label)
        if not rw_rows:
            print(f"  {name}: no cached joined_data/labels — skipped")
            continue
        if name not in cfg:
            print(f"  {name}: not in config.yaml — skipped")
            continue
        repo_dir = _resolve_repo_dir(name)
        if not (repo_dir / ".git").exists():
            print(f"  {name}: repo not cloned at {repo_dir} — skipped")
            continue
        t0_sha = resolve_t0_sha(str(repo_dir), cfg[name]["t0_date"])
        rel_paths = [r["file_path"] for r in rw_rows]
        cache_path = _RESULTS / f"health_defect_{name}" / "codescene_scores.json"
        print(f"  {name}: T0 {t0_sha[:12]} ({cfg[name]['t0_date']}), "
              f"{len(rel_paths)} files")
        if do_score:
            cs_scores = score_repo(
                str(repo_dir), t0_sha, rel_paths, cache_path, timeout=timeout
            )
        else:
            cs_scores = load_cache(cache_path)

        rw_keep: list[dict] = []
        cs_keep: list[dict] = []
        n_null = n_absent = 0
        for r in rw_rows:
            if r["file_path"] not in cs_scores:
                n_absent += 1
                continue
            s = cs_scores[r["file_path"]]
            if s is None:
                n_null += 1
                continue
            rw_keep.append(r)
            cs_keep.append({**r, "health_score": float(s)})
        rw_corpus[name] = rw_keep
        cs_corpus[name] = cs_keep
        coverage[name] = {
            "n_repowise_scored": len(rw_rows),
            "n_codescene_scored": len(cs_keep),
            "n_codescene_null": n_null,
            "n_codescene_absent": n_absent,
            "n_paired": len(cs_keep),
            "n_defective_paired": sum(1 for d in cs_keep if d["defect_count"] > 0),
        }
        print(f"    paired {len(cs_keep)}/{len(rw_rows)} "
              f"(null={n_null}, absent={n_absent})")
    return rw_corpus, cs_corpus, coverage


# ---------------------------------------------------------------------------
# Per-tool metrics + paired significance
# ---------------------------------------------------------------------------
def _tool_metrics(corpus: dict[str, list[dict]], *, n_boot: int, seed: int) -> dict:
    out: dict[str, Any] = {}
    for mname, mfn in HEADLINE_METRICS.items():
        out[mname] = {
            "cross_project_mean": cluster_bootstrap_mean(
                corpus, mfn, n_boot=n_boot, seed=seed
            ),
            "pooled": pooled_ci(corpus, mfn, n_boot=n_boot, seed=seed),
        }
    # Defect concentration (their flagship): Alert:Healthy ratios, cluster CI.
    out["defect_concentration"] = {
        "mean_defects_per_file_ratio": _cluster_ci(
            corpus, "mean_defects_per_file", n_boot=n_boot, seed=seed
        ),
        "defects_per_kloc_ratio": _cluster_ci(
            corpus, "defects_per_kloc", n_boot=n_boot, seed=seed
        ),
    }
    pooled = [r for n in corpus for r in corpus[n]]
    out["categories"] = _category_stats(pooled)
    sp = spearman_correlation(
        [d["health_score"] for d in pooled],
        [int(d["defect_count"]) for d in pooled],
    )
    out["health_vs_defectcount_spearman"] = sp
    return out


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def paired_delong(
    rw_pooled: list[dict], cs_pooled: list[dict]
) -> dict[str, Any]:
    """Paired DeLong on the two tools' AUCs over the identical pooled files.

    ``rw_pooled`` and ``cs_pooled`` are row-aligned (same file order, same
    labels; only ``health_score`` differs). Risk = 10 - score for both.
    """
    labels = [1 if d["defect_count"] > 0 else 0 for d in rw_pooled]
    if sum(labels) == 0 or sum(labels) == len(labels):
        return {"error": "degenerate (single-class pooled set)"}
    preds = {
        "repowise": [10.0 - d["health_score"] for d in rw_pooled],
        "codescene": [10.0 - d["health_score"] for d in cs_pooled],
    }
    res = delong_cov(preds, labels)
    if res is None:
        return {"error": "degenerate"}
    aucs, cov, order = res
    ia, ib = order.index("repowise"), order.index("codescene")
    var = cov[ia][ia] + cov[ib][ib] - 2 * cov[ia][ib]
    delta = aucs["repowise"] - aucs["codescene"]
    if var <= 0:
        return {
            "auc_repowise": aucs["repowise"],
            "auc_codescene": aucs["codescene"],
            "delta": delta, "z": None, "p_value": None,
            "note": "non-positive variance",
            "n_pos": sum(labels), "n_neg": len(labels) - sum(labels),
        }
    z = delta / math.sqrt(var)
    return {
        "auc_repowise": aucs["repowise"],
        "auc_codescene": aucs["codescene"],
        "delta": delta, "z": z, "p_value": 2 * _norm_sf(abs(z)),
        "n_pos": sum(labels), "n_neg": len(labels) - sum(labels),
    }


def run(repos: list[str], label: str, *, n_boot: int, seed: int,
        do_score: bool, timeout: int) -> dict:
    print(f"\n=== Building paired corpus ({label} labels) ===")
    rw_corpus, cs_corpus, coverage = build_paired_corpus(
        repos, label, do_score=do_score, timeout=timeout
    )
    if not rw_corpus:
        raise SystemExit("No repos produced a paired corpus — nothing to compare.")

    report: dict[str, Any] = {
        "label": label,
        "n_boot": n_boot,
        "seed": seed,
        "repos": list(rw_corpus),
        "coverage": coverage,
        "n_paired_files": sum(len(v) for v in cs_corpus.values()),
        "n_defective_paired": sum(
            1 for v in cs_corpus.values() for d in v if d["defect_count"] > 0
        ),
        "repowise": _tool_metrics(rw_corpus, n_boot=n_boot, seed=seed),
        "codescene": _tool_metrics(cs_corpus, n_boot=n_boot, seed=seed),
    }

    # Paired significance on the pooled, row-aligned file set.
    rw_pooled = [r for n in rw_corpus for r in rw_corpus[n]]
    cs_pooled = [r for n in cs_corpus for r in cs_corpus[n]]
    report["paired_delong_auc"] = paired_delong(rw_pooled, cs_pooled)
    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _row(label: str, rw: dict, cs: dict) -> str:
    return f"  {label:30} {_fmt(rw):>26}   {_fmt(cs):>26}"


def print_summary(r: dict) -> None:
    print(f"\n{'='*78}")
    print(f"HEAD-TO-HEAD: Repowise vs CodeScene  ({r['label']} labels)")
    print(f"{'='*78}")
    print(f"repos: {', '.join(r['repos'])}")
    print(f"paired files: {r['n_paired_files']}  "
          f"(defective: {r['n_defective_paired']})")
    print("\nPer-repo CodeScene coverage:")
    for name, cov in r["coverage"].items():
        print(f"  {name:12} paired {cov['n_paired']:>4}/{cov['n_repowise_scored']:<4}"
              f"  null={cov['n_codescene_null']:<3} absent={cov['n_codescene_absent']:<3}"
              f"  defective={cov['n_defective_paired']}")

    rw, cs = r["repowise"], r["codescene"]
    print(f"\n{'metric':32} {'Repowise':>26}   {'CodeScene':>26}")
    print("  " + "-" * 84)
    for m in ("auc", "partial_rho_nloc", "popt",
              "precision_at_20pct_loc", "recall_at_20pct_loc"):
        print(_row(m + " (pooled)", rw[m]["pooled"], cs[m]["pooled"]))
    print(_row("defect-conc. defects/file",
               rw["defect_concentration"]["mean_defects_per_file_ratio"],
               cs["defect_concentration"]["mean_defects_per_file_ratio"]))
    print(_row("defect-conc. defects/KLOC",
               rw["defect_concentration"]["defects_per_kloc_ratio"],
               cs["defect_concentration"]["defects_per_kloc_ratio"]))

    print("\nCategory breakdown (pooled files):")
    for tool in _TOOLS:
        c = r[tool]["categories"]
        print(f"  [{tool}]  "
              + "  ".join(
                  f"{k}: {c[k]['files']}f/{c[k]['mean_defects_per_file']:.2f}dpf"
                  for k in ("healthy", "warning", "alert")))

    d = r["paired_delong_auc"]
    print("\nPaired DeLong (Repowise AUC − CodeScene AUC, pooled):")
    if d.get("p_value") is None:
        print(f"  AUC repowise={d.get('auc_repowise')}  "
              f"codescene={d.get('auc_codescene')}  "
              f"Δ={d.get('delta')}  (p n/a: {d.get('note') or d.get('error')})")
    else:
        winner = "Repowise" if d["delta"] > 0 else "CodeScene"
        sig = "significant" if d["p_value"] < 0.05 else "n.s."
        print(f"  AUC  repowise={d['auc_repowise']:.3f}  "
              f"codescene={d['auc_codescene']:.3f}")
        print(f"  ΔAUC={d['delta']:+.3f}  z={d['z']:+.2f}  p={d['p_value']:.4g}  "
              f"({sig}; leads: {winner})")
        print(f"  n_pos={d['n_pos']}  n_neg={d['n_neg']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=None,
                    help="subset to compare (default: all repos in config.yaml)")
    ap.add_argument("--label", default="keyword",
                    choices=["keyword", "szz", "szz_b", "issue"])
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--timeout", type=int, default=120,
                    help="per-file CodeScene CLI timeout (s)")
    ap.add_argument("--no-score", action="store_true",
                    help="reuse cached CodeScene scores; do not call the CLI")
    ap.add_argument("--out", type=Path,
                    default=_RESULTS / "codescene_headtohead.json")
    args = ap.parse_args()

    if not args.no_score:
        ok, reason = cs_available()
        if not ok:
            raise SystemExit(
                f"CodeScene CLI not usable: {reason}\n"
                "Set CS_ACCESS_TOKEN (free codescene.io PAT) and CS_BIN, or pass "
                "--no-score to reuse cached scores."
            )

    repos = args.repos
    if not repos:
        cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
        repos = [r["name"] for r in cfg["repos"]]

    rep = run(repos, args.label, n_boot=args.n_boot, seed=args.seed,
              do_score=not args.no_score, timeout=args.timeout)
    print_summary(rep)
    args.out.write_text(json.dumps(rep, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
