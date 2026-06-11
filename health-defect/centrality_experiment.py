#!/usr/bin/env python3
"""centrality_experiment.py — dependency-graph centrality through the gate.

RESEARCH ARTIFACT (bench-only). The Phase-1 pilot candidate: per-file graph
centrality. It needs **no new extraction** — the dependency graph is the same
one ``repowise health`` builds during indexing — so it de-risks the *harness*
(``candidate_eval.py``), not the science. The literature is genuinely mixed
(Zimmermann–Nagappan ICSE'08 found network measures beat complexity by ~10pts
recall on Windows; the Eclipse replication found only parity), so a clean PARK
here is a valid, publishable outcome.

For each corpus repo it:
  1. resolves T0 (``defect_counter.resolve_t0_sha``) and adds a **detached temp
     worktree at T0** — the graph is therefore structurally leakage-free, built
     from exactly the snapshot the health score was computed on, before the
     (T0, T1] defect labels exist;
  2. parses every file (``FileTraverser`` + ``ASTParser``) and builds the
     dependency graph (``GraphBuilder``), honoring the repo's ``exclude`` list;
  3. computes five per-file columns over the file-level subgraph —
     **betweenness**, **eigenvector**, **in-degree** (dependents),
     **out-degree** (dependencies), and **PageRank** (the sanity anchor, the
     same value the runtime ``FileContext.pagerank_score`` would carry);
  4. caches them to ``results/health_defect_<repo>/graph_centrality.json``;
  5. runs each column through ``candidate_eval.evaluate_candidate`` and writes
     the four/five scorecards + a paste-ready markdown block.

Hypothesis under test: a *small* file can still be a high-betweenness hub, so
centrality should survive the within-NLOC-band test even though it parks if it
is merely a size proxy (big files import/are-imported more).

Run (venv python — has networkx/sklearn; NOT ``uv run``)::

    cd health-defect
    ../../.venv/Scripts/python.exe centrality_experiment.py \
        --results-dir <bench>/results --repos-dir <bench>/repos \
        [--repo clap] [--rebuild-graph]

``--results-dir``/``--repos-dir`` default to this checkout's siblings; point them
at the fully-populated bench checkout when running from an R&D worktree.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import networkx as nx

import candidate_eval as ce
from lib.defect_counter import resolve_t0_sha

_HERE = Path(__file__).resolve().parent

VARIANTS = ["betweenness", "eigenvector", "in_degree", "out_degree", "pagerank"]


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


# --------------------------------------------------------------------------
# Graph construction (in-process, no full index / no health re-run)
# --------------------------------------------------------------------------
def build_graph_metrics(worktree: Path, exclude: list[str]) -> dict[str, dict[str, float]]:
    """Parse a worktree and return per-file centrality columns over the
    file-level dependency subgraph. Keys are repo-relative POSIX paths."""
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    trav = FileTraverser(worktree, extra_exclude_patterns=exclude or None)
    parser = ASTParser()
    gb = GraphBuilder(repo_path=worktree, exclude_patterns=exclude or None)
    parsed = 0
    for p in trav._walk():
        fi = trav._build_file_info(p)
        if fi is None:
            continue
        try:
            src = Path(fi.abs_path).read_bytes()
            gb.add_file(parser.parse_file(fi, src))
            parsed += 1
        except Exception:  # noqa: BLE001 — unparseable file: absent, never zero
            continue
    gb.build()
    # Framework-aware synthetic edges (conftest/Django/FastAPI/Flask) so the
    # graph matches what the indexer feeds the health engine.
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        gb.add_framework_edges([t.name for t in detect_tech_stack(worktree)])
    except Exception:  # noqa: BLE001 — best-effort, mirrors the pipeline
        pass

    sub = gb.file_subgraph()
    n_nodes = sub.number_of_nodes()
    betw = gb.betweenness_centrality()
    indeg = gb.in_degree()
    outdeg = gb.out_degree()
    pr = gb.pagerank()
    # Eigenvector centrality is not exposed by GraphBuilder — compute it here on
    # the same file subgraph. The numpy solver (largest-eigenvalue of the
    # adjacency) is robust on directed graphs; fall back to power iteration, and
    # if both fail leave the column absent (never zero-fill).
    try:
        eig = nx.eigenvector_centrality_numpy(sub)
    except Exception:
        try:
            eig = nx.eigenvector_centrality(sub, max_iter=1000, tol=1e-06)
        except Exception:
            eig = None

    cols: dict[str, dict[str, float]] = {v: {} for v in VARIANTS}
    for node in sub.nodes():
        key = _norm(str(node))
        if key.startswith("external:"):
            continue
        cols["betweenness"][key] = float(betw.get(node, 0.0))
        cols["in_degree"][key] = float(indeg.get(node, 0))
        cols["out_degree"][key] = float(outdeg.get(node, 0))
        cols["pagerank"][key] = float(pr.get(node, 0.0))
        if eig is not None:
            cols["eigenvector"][key] = float(eig.get(node, 0.0))
    if eig is None:
        cols.pop("eigenvector")
    cols["_meta"] = {"parsed_files": parsed, "file_nodes": n_nodes,
                     "edges": sub.number_of_edges()}
    return cols


def graph_for_repo(
    repo: str, cfg: dict, repos_dir: Path, cache_path: Path, *, rebuild: bool
) -> dict[str, dict[str, float]]:
    """Return cached centrality columns for a repo, building them at T0 if
    missing (or ``rebuild``)."""
    if cache_path.exists() and not rebuild:
        return json.loads(cache_path.read_text())

    repo_dir = (repos_dir / repo).resolve()
    if not repo_dir.exists():
        raise FileNotFoundError(f"clone missing: {repo_dir}")
    exclude = list(cfg.get("exclude") or [])
    t0_sha = resolve_t0_sha(str(repo_dir), cfg["t0_date"])
    wt = Path(tempfile.gettempdir()) / "claude" / f"cent-{repo}-{t0_sha[:12]}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                   cwd=repo_dir, capture_output=True, text=True)
    t = time.time()
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(wt), t0_sha],
                       cwd=repo_dir, capture_output=True, text=True, check=True)
        cols = build_graph_metrics(wt, exclude)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=repo_dir, capture_output=True, text=True)
    cols["_meta"]["t0_sha"] = t0_sha
    cols["_meta"]["build_seconds"] = round(time.time() - t, 1)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cols, indent=2))
    return cols


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_HERE.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_HERE.parent / "repos")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--repo", default="", help="comma list; default = all config repos")
    ap.add_argument("--rebuild-graph", action="store_true",
                    help="recompute centrality even if cached")
    ap.add_argument("--out", type=Path, default=None,
                    help="JSON scorecards (default results-dir/centrality_scorecards.json)")
    args = ap.parse_args()

    import yaml
    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)
    out_path = args.out or (args.results_dir / "centrality_scorecards.json")

    # --- Step 1: build/load per-repo centrality columns ---------------------
    print(f"=== Building T0 dependency-graph centrality for {len(repos)} repos ===")
    by_variant: dict[str, dict[str, dict[str, float]]] = {v: {} for v in VARIANTS}
    build_times = []
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo}: not in config)")
            continue
        cache = args.results_dir / f"health_defect_{repo}" / "graph_centrality.json"
        try:
            cols = graph_for_repo(repo, cfg, args.repos_dir, cache, rebuild=args.rebuild_graph)
        except Exception as exc:  # noqa: BLE001
            print(f"  (skip {repo}: graph build failed: {exc})")
            continue
        meta = cols.get("_meta", {})
        if "build_seconds" in meta:
            build_times.append(meta["build_seconds"])
        print(f"  {repo:12s} nodes={meta.get('file_nodes','?'):>5} "
              f"edges={meta.get('edges','?'):>6} "
              f"{'(cached)' if 'build_seconds' not in meta else str(meta['build_seconds'])+'s'}")
        for v in VARIANTS:
            if v in cols:
                by_variant[v][repo] = cols[v]

    # --- Step 2: score each variant through the gate ------------------------
    print(f"\n=== Scoring centrality variants through the promotion gate "
          f"(label={args.label}) ===")
    rows = ce.load_corpus(args.results_dir, args.config, args.label)
    cost = f"in-process graph build at T0; mean {sum(build_times)/len(build_times):.1f}s/repo" \
        if build_times else "centrality cached (no rebuild this run)"

    cards = {}
    md_blocks = []
    for v in VARIANTS:
        col = by_variant.get(v)
        if not col:
            print(f"  ({v}: no columns — skipped)")
            continue
        card = ce.evaluate_candidate(
            col, v, results_dir=args.results_dir, config_path=args.config,
            label=args.label, cost_note=cost, corpus_rows=rows,
        )
        cards[v] = card
        md = ce.scorecard_markdown(card)
        md_blocks.append(md)
        print("\n" + md)

    # --- Step 3: summary table + persist ------------------------------------
    print("\n=== Centrality summary ===")
    print(f"{'variant':14s} {'verdict':12s} {'coef(CI)':>26s} {'OOFΔ(CI)':>26s} "
          f"{'wband':>6s} {'max|ρ|':>7s} {'cov':>5s}")
    for v, c in cards.items():
        lb, da, wb, rd, cc = (c["lift_beyond_size"], c["oof_auc_delta"],
                              c["within_band_auc"], c["redundancy"], c["coverage_cost"])
        coef_s = f"{lb['candidate_coef']:+.3f}[{lb['coef_ci95'][0]:+.2f},{lb['coef_ci95'][1]:+.2f}]" \
            if lb["coef_ci95"] else f"{lb['candidate_coef']:+.3f}[n/a]"
        d_s = f"{da['delta']:+.4f}[{da['delta_ci95'][0]:+.3f},{da['delta_ci95'][1]:+.3f}]" \
            if da["delta_ci95"] else f"{da['delta']}[n/a]"
        print(f"{v:14s} {c['verdict']:12s} {coef_s:>26s} {d_s:>26s} "
              f"{str(wb['candidate_band_mean']):>6s} {str(rd['max_abs_spearman']):>7s} "
              f"{cc['coverage_fraction']:.0%}")

    out_path.write_text(json.dumps({"cards": cards, "cost_note": cost}, indent=2))
    md_path = out_path.with_suffix(".md")
    md_path.write_text("\n\n".join(md_blocks))
    print(f"\nWrote {out_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    sys.exit(main())
