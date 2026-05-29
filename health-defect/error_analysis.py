#!/usr/bin/env python3
"""Failure forensics for the code-health defect predictor.

Reads the cached per-repo benchmark artifacts (``joined_data.json`` +
``health_scores.json`` under ``../results/health_defect_<repo>/``, produced by
``run_benchmark.py --score-at t0``) and systematically characterizes *where* and
*why* the shipped health score mis-ranks files for defect prediction.

It is a read-only analysis tool — it never re-indexes or re-scores; it consumes
the same T0-anchored, leakage-free findings the calibration uses. Output is a
written failure taxonomy (stdout) plus a machine-readable JSON dump.

Three views (Phase-9 Part A):
  1. **Residual dump** — the worst false positives (clean files the score flags
     as risky) and false negatives (defective files the score rates healthy),
     corpus-wide and per repo, each annotated with its biomarker fingerprint,
     NLOC, language and directory.
  2. **Cluster cost** — partition files by NLOC band, language, dominant
     biomarker, and top-level directory; for each cluster report n / positives /
     within-cluster AUC and the *leave-cluster-out ΔAUC* (how much the global
     pooled AUC changes when that cluster's files are dropped — a positive ΔAUC
     means the cluster is actively dragging the global ranking down).
  3. **Size-regime decomposition** — AUC computed strictly *within* NLOC
     quartiles, to answer the standing question of whether the score inverts on
     micro-modules vs. large files.

The risk signal analysed is the **shipped** score (``risk = 10 - health_score``)
so the taxonomy reflects the product users actually get, not a re-fit model.

Usage (venv python — needs numpy):
    ../../.venv/Scripts/python.exe error_analysis.py \
        [--results-dir ../results] [--config config.yaml] \
        [--out ../results/error_analysis.json] [--top 25]
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

# Biomarkers that contribute to the file score (governance/additive ones excluded
# — same roster the calibration uses).
SCORING_BIOMARKERS = {
    "brain_method", "low_cohesion", "god_class", "nested_complexity",
    "complex_method", "bumpy_road", "complex_conditional", "large_method",
    "primitive_obsession", "dry_violation", "untested_hotspot", "coverage_gap",
    "developer_congestion", "knowledge_loss", "hidden_coupling", "function_hotspot",
    "code_age_volatility", "ownership_risk", "churn_risk", "change_entropy",
    "co_change_scatter", "prior_defect",
    "large_assertion_block", "duplicated_assertion_block",
}

_SEV_WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0,
               "1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0}


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def _sev_weight(sev: Any) -> float:
    return _SEV_WEIGHT.get(str(sev).strip().lower(), 1.0)


def auc(y: list[int], score: list[float]) -> float | None:
    """Mann-Whitney AUC with tie handling. None when single-class."""
    pos = [s for s, t in zip(score, y) if t == 1]
    neg = [s for s, t in zip(score, y) if t == 0]
    if not pos or not neg:
        return None
    # Rank-sum (average ranks for ties).
    order = sorted(range(len(score)), key=lambda i: score[i])
    ranks = [0.0] * len(score)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and score[order[j + 1]] == score[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rank_pos = sum(ranks[i] for i in range(len(score)) if y[i] == 1)
    n_pos, n_neg = len(pos), len(neg)
    return (rank_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def top_dir(path: str, source_root: str) -> str:
    """First path segment below the repo's source_root (the rough 'domain')."""
    p = _norm(path)
    sr = _norm(source_root)
    if sr and p.startswith(sr + "/"):
        p = p[len(sr) + 1:]
    elif sr and p == sr:
        return "<root>"
    parts = p.split("/")
    return parts[0] if len(parts) > 1 else "<root>"


def load_config_langs(config_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    import yaml
    cfg = yaml.safe_load(config_path.read_text())
    langs, roots = {}, {}
    for r in cfg.get("repos", []):
        langs[r["name"]] = r.get("language", "?")
        roots[r["name"]] = r.get("source_root", "")
    return langs, roots


def build_rows(results_dir: Path, langs: dict[str, str],
               roots: dict[str, str], label: str = "keyword") -> list[dict]:
    """One flat row per file across the whole corpus, annotated with its biomarker
    fingerprint and the shipped risk = 10 - health_score.

    ``label`` selects the defect ground truth: ``defect_counts_<label>.json`` (the
    same per-file fix counts the calibration ships on — default ``keyword``).
    Files absent from that file have 0 defects. ``joined`` falls back to
    joined_data.json's own defect_count.
    """
    rows: list[dict] = []
    for repo, lang in langs.items():
        rdir = results_dir / f"health_defect_{repo}"
        jp, hp = rdir / "joined_data.json", rdir / "health_scores.json"
        if not jp.exists() or not hp.exists():
            print(f"  (skip {repo}: missing artifacts)")
            continue
        joined = json.loads(jp.read_text())
        health = json.loads(hp.read_text())
        counts = None
        if label != "joined":
            lp = rdir / f"defect_counts_{label}.json"
            if lp.exists():
                counts = {_norm(k): int(v) for k, v in json.loads(lp.read_text()).items()}
            else:
                print(f"  (warn {repo}: no defect_counts_{label}.json — using joined_data)")
        # file -> {biomarker -> severity-weighted hit}, and dominant impact
        fired: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        impact: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for f in health.get("findings", []):
            bt = f.get("biomarker_type")
            if bt not in SCORING_BIOMARKERS:
                continue
            fp = _norm(f.get("file_path", ""))
            fired[fp][bt] += _sev_weight(f.get("severity"))
            impact[fp][bt] += abs(float(f.get("health_impact") or 0.0))
        for d in joined:
            fp = _norm(d["file_path"])
            bm = dict(fired.get(fp, {}))
            imp = impact.get(fp, {})
            dominant = max(imp, key=imp.get) if imp else "<none>"
            dc = counts.get(fp, 0) if counts is not None else int(d.get("defect_count", 0) or 0)
            d = {**d, "defect_count": dc}
            rows.append({
                "repo": repo, "language": lang, "file_path": fp,
                "dir": top_dir(fp, roots.get(repo, "")),
                "nloc": int(d.get("nloc", 0) or 0),
                "health_score": float(d.get("health_score", 10.0)),
                "risk": 10.0 - float(d.get("health_score", 10.0)),
                "defect_count": int(d.get("defect_count", 0) or 0),
                "y": 1 if int(d.get("defect_count", 0) or 0) > 0 else 0,
                "finding_count": int(d.get("finding_count", 0) or 0),
                "biomarkers": bm,
                "dominant": dominant,
                "commit_count_90d": d.get("commit_count_90d"),
                "prior_defect_count": d.get("prior_defect_count"),
                "line_coverage_pct": d.get("line_coverage_pct"),
            })
    return rows


def nloc_quartiles(rows: list[dict]) -> list[float]:
    nlocs = sorted(r["nloc"] for r in rows)
    return [float(np.percentile(nlocs, q)) for q in (25, 50, 75)]


def band_of(nloc: int, cuts: list[float]) -> str:
    if nloc <= cuts[0]:
        return f"Q1 (<= {cuts[0]:.0f})"
    if nloc <= cuts[1]:
        return f"Q2 (<= {cuts[1]:.0f})"
    if nloc <= cuts[2]:
        return f"Q3 (<= {cuts[2]:.0f})"
    return f"Q4 (> {cuts[2]:.0f})"


def cluster_report(rows: list[dict], key_fn, label: str) -> list[dict]:
    """For each cluster: n, positives, within-cluster AUC, leave-cluster-out ΔAUC."""
    global_y = [r["y"] for r in rows]
    global_s = [r["risk"] for r in rows]
    base = auc(global_y, global_s)
    groups: dict[Any, list[dict]] = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    out = []
    for k, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        y = [r["y"] for r in members]
        s = [r["risk"] for r in members]
        within = auc(y, s)
        rest = [r for r in rows if key_fn(r) != k]
        loo = auc([r["y"] for r in rest], [r["risk"] for r in rest])
        out.append({
            "cluster": str(k), "n": len(members), "positives": sum(y),
            "defect_rate": round(sum(y) / len(members), 3),
            "within_auc": round(within, 3) if within is not None else None,
            "loo_auc": round(loo, 3) if loo is not None else None,
            "loo_delta_auc": round(loo - base, 4) if (loo is not None and base is not None) else None,
            "mean_nloc": round(sum(r["nloc"] for r in members) / len(members), 1),
            "mean_risk": round(sum(s) / len(members), 2),
        })
    return out


def fmt_fingerprint(bm: dict[str, float]) -> str:
    if not bm:
        return "<no findings>"
    return ", ".join(f"{k}×{v:.0f}" for k, v in sorted(bm.items(), key=lambda x: -x[1])[:5])


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=here.parent / "results")
    ap.add_argument("--config", type=Path, default=here / "config.yaml")
    ap.add_argument("--out", type=Path, default=here.parent / "results" / "error_analysis.json")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--label", default="keyword",
                    help="Defect ground truth: keyword (shipped calibration "
                         "label, default) / szz / issue / joined.")
    args = ap.parse_args()

    langs, roots = load_config_langs(args.config)
    rows = build_rows(args.results_dir, langs, roots, label=args.label)
    if not rows:
        raise SystemExit("No rows loaded.")

    n, npos = len(rows), sum(r["y"] for r in rows)
    base_auc = auc([r["y"] for r in rows], [r["risk"] for r in rows])
    print(f"\n=== Corpus: {len(langs)} repos | {n} files | {npos} positives "
          f"({npos/n:.1%}) | pooled AUC {base_auc:.3f} ===\n")

    cuts = nloc_quartiles(rows)
    for r in rows:
        r["band"] = band_of(r["nloc"], cuts)

    # ---- Part A.1: residual dumps -------------------------------------------
    # Worst FPs: clean files (y=0) with the highest risk.
    fps = sorted([r for r in rows if r["y"] == 0], key=lambda r: -r["risk"])[:args.top]
    # Worst FNs: defective files (y=1) with the lowest risk.
    fns = sorted([r for r in rows if r["y"] == 1], key=lambda r: r["risk"])[:args.top]

    print(f"--- Top {args.top} FALSE POSITIVES (clean, flagged risky) ---")
    print(f"{'repo':10s} {'lang':10s} {'nloc':>5s} {'risk':>5s}  file / fingerprint")
    for r in fps:
        print(f"{r['repo']:10s} {r['language']:10s} {r['nloc']:5d} {r['risk']:5.1f}  "
              f"{r['file_path']}")
        print(f"{'':33s}  -> {fmt_fingerprint(r['biomarkers'])}")

    print(f"\n--- Top {args.top} FALSE NEGATIVES (defective, rated healthy) ---")
    print(f"{'repo':10s} {'lang':10s} {'nloc':>5s} {'risk':>5s} {'dfx':>3s}  file / fingerprint")
    for r in fns:
        print(f"{r['repo']:10s} {r['language']:10s} {r['nloc']:5d} {r['risk']:5.1f} "
              f"{r['defect_count']:3d}  {r['file_path']}")
        print(f"{'':37s}  -> {fmt_fingerprint(r['biomarkers'])}")

    # ---- Part A.2: cluster cost ---------------------------------------------
    clusters = {
        "by_language": cluster_report(rows, lambda r: r["language"], "language"),
        "by_nloc_band": cluster_report(rows, lambda r: r["band"], "nloc_band"),
        "by_dominant_biomarker": cluster_report(rows, lambda r: r["dominant"], "dominant"),
        "by_repo": cluster_report(rows, lambda r: r["repo"], "repo"),
    }
    for view, rep in clusters.items():
        print(f"\n--- Cluster cost: {view} "
              f"(base pooled AUC {base_auc:.3f}; +ΔAUC ⇒ cluster hurts) ---")
        print(f"{'cluster':28s} {'n':>5s} {'pos':>4s} {'rate':>5s} "
              f"{'within':>7s} {'looΔAUC':>8s} {'mNLOC':>6s} {'mRisk':>6s}")
        for c in rep:
            print(f"{c['cluster'][:28]:28s} {c['n']:5d} {c['positives']:4d} "
                  f"{c['defect_rate']:5.2f} {str(c['within_auc']):>7s} "
                  f"{str(c['loo_delta_auc']):>8s} {c['mean_nloc']:6.0f} {c['mean_risk']:6.2f}")

    # ---- Part A.3: size-regime decomposition --------------------------------
    print(f"\n--- Size-regime decomposition: AUC within NLOC quartiles ---")
    print(f"NLOC quartile cuts: {[round(c) for c in cuts]}")
    band_aucs = {}
    for band in [f"Q1 (<= {cuts[0]:.0f})", f"Q2 (<= {cuts[1]:.0f})",
                 f"Q3 (<= {cuts[2]:.0f})", f"Q4 (> {cuts[2]:.0f})"]:
        members = [r for r in rows if r["band"] == band]
        a = auc([r["y"] for r in members], [r["risk"] for r in members])
        band_aucs[band] = a
        pos = sum(r["y"] for r in members)
        print(f"  {band:18s} n={len(members):4d} pos={pos:3d} "
              f"within-AUC={a:.3f}" if a is not None else
              f"  {band:18s} n={len(members):4d} pos={pos:3d} within-AUC=n/a")

    # ---- dump ----
    out = {
        "corpus": {"repos": len(langs), "files": n, "positives": npos,
                   "pooled_auc": base_auc},
        "nloc_quartile_cuts": cuts,
        "false_positives": [{k: r[k] for k in
                             ("repo", "language", "file_path", "nloc", "risk",
                              "dominant", "biomarkers")} for r in fps],
        "false_negatives": [{k: r[k] for k in
                             ("repo", "language", "file_path", "nloc", "risk",
                              "defect_count", "dominant", "biomarkers")} for r in fns],
        "clusters": clusters,
        "size_regime_auc": {k: v for k, v in band_aucs.items()},
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
