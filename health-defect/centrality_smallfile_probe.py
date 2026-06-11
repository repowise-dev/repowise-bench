#!/usr/bin/env python3
"""centrality_smallfile_probe.py — does centrality pay *where the model is blind*?

RESEARCH ARTIFACT (bench-only). Follow-up to ``centrality_experiment.py``, which
found dependency centrality PARKs in aggregate (flat-to-negative OOF AUC delta
over the calibrated 24-biomarker + NLOC model) **even though** PageRank/
eigenvector beat the shipped health score *within the small-file NLOC bands*
(Q1 ≤22 LOC: PageRank AUC 0.73 vs shipped 0.53). The aggregate wash is explained
by the large bands — where most positives live — pulling the pooled number down.

The open question this probe answers: **small files often have too little code to
trip the complexity/coupling biomarkers, so the calibrated model is near-guessing
there. Does centrality add real lift *within the small-file population* — over the
full model, not just over the shipped score?** If yes, a size-*targeted* centrality
signal (used only below an NLOC threshold) is a shippable lead; if it also parks,
the small-band win was already subsumed and centrality is closed out.

Mechanics: build a centrality column **masked to files at/below an NLOC threshold**
(files above it are *absent*, never zeroed) and run it through the same §3 gate.
``candidate_eval`` then refits the calibrated model on that small-file universe and
reports the marginal OOF AUC delta + CI there. Reuses the cached
``graph_centrality.json`` (no graph rebuild, no re-index).

Run (venv python)::

    cd health-defect
    ../../.venv/Scripts/python.exe centrality_smallfile_probe.py \
        --results-dir <bench>/results [--variants pagerank,eigenvector] \
        [--thresholds 48,108]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import candidate_eval as ce

_HERE = Path(__file__).resolve().parent


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def load_cached_centrality(results_dir: Path, repos: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """{variant: {repo: {file: value}}} from each repo's graph_centrality.json."""
    by_variant: dict[str, dict[str, dict[str, float]]] = {}
    for repo in repos:
        p = results_dir / f"health_defect_{repo}" / "graph_centrality.json"
        if not p.exists():
            continue
        cols = json.loads(p.read_text())
        for v, col in cols.items():
            if v == "_meta":
                continue
            by_variant.setdefault(v, {})[repo] = {_norm(k): float(val) for k, val in col.items()}
    return by_variant


def mask_to_small(
    column: dict[str, dict[str, float]],
    nloc_by: dict[str, dict[str, float]],
    threshold: float | None,
) -> dict[str, dict[str, float]]:
    """Keep a file's centrality only when its NLOC <= threshold; otherwise drop
    it (absent, not zero). ``threshold=None`` → unrestricted (the baseline run)."""
    if threshold is None:
        return column
    out: dict[str, dict[str, float]] = {}
    for repo, files in column.items():
        nl = nloc_by.get(repo, {})
        kept = {f: v for f, v in files.items() if f in nl and nl[f] <= threshold}
        if kept:
            out[repo] = kept
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--variants", default="pagerank,eigenvector,betweenness,in_degree")
    ap.add_argument("--thresholds", default="48,108",
                    help="NLOC ceilings to probe; 'all' is always included as reference")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    import yaml
    cfg = yaml.safe_load(args.config.read_text())
    repos = [r["name"] for r in cfg["repos"]]
    variants = args.variants.split(",")
    thresholds: list[float | None] = [None] + [float(t) for t in args.thresholds.split(",")]

    rows = ce.load_corpus(args.results_dir, args.config, args.label)
    nloc_by: dict[str, dict[str, float]] = {}
    for r in rows:
        nloc_by.setdefault(r["repo"], {})[r["file_path"]] = float(r["nloc"])

    by_variant = load_cached_centrality(args.results_dir, repos)

    print(f"=== Small-file-targeted centrality lift (label={args.label}) ===")
    print("OOF AUC Δ = (calibrated model + centrality) − (calibrated model), "
          "evaluated WITHIN each NLOC ceiling.\n")
    hdr = (f"{'variant':12s} {'ceiling':>8s} {'files':>6s} {'pos':>4s} "
           f"{'AUCbase':>8s} {'AUCcand':>8s} {'OOF Δ':>9s} {'Δ CI95':>20s} "
           f"{'coefCI excl0+':>13s} {'verdict':>8s}")
    print(hdr); print("-" * len(hdr))

    results = {}
    for v in variants:
        col = by_variant.get(v)
        if not col:
            print(f"  ({v}: no cached column)")
            continue
        for thr in thresholds:
            masked = mask_to_small(col, nloc_by, thr)
            name = f"{v}_nloc<={int(thr)}" if thr is not None else f"{v}_all"
            card = ce.evaluate_candidate(
                masked, name, results_dir=args.results_dir, config_path=args.config,
                label=args.label, corpus_rows=rows,
                cost_note=f"masked to NLOC<={thr}" if thr is not None else "unrestricted",
            )
            results[name] = card
            da = card["oof_auc_delta"]
            lb = card["lift_beyond_size"]
            dci = da["delta_ci95"]
            dci_s = f"[{dci[0]:+.4f},{dci[1]:+.4f}]" if dci else "n/a"
            coef_ok = card["gate_components"]["coef_excludes_zero_positive"]
            ceiling = "all" if thr is None else str(int(thr))
            print(f"{v:12s} {ceiling:>8s} {card['n_files']:>6d} {card['n_positives']:>4d} "
                  f"{str(da['auc_base']):>8s} {str(da['auc_candidate']):>8s} "
                  f"{str(da['delta']):>9s} {dci_s:>20s} {str(coef_ok):>13s} {card['verdict']:>8s}")
        print()

    if args.out:
        args.out.write_text(json.dumps(results, indent=2))
        print(f"Wrote {args.out}")
    else:
        default = args.results_dir / "centrality_smallfile_probe.json"
        default.write_text(json.dumps(results, indent=2))
        print(f"Wrote {default}")


if __name__ == "__main__":
    main()
