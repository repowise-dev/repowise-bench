#!/usr/bin/env python3
"""error_handling_experiment.py — swallowed-exception / unsafe-unwrap density
through the §3 gate. Phase-3 Part C.

RESEARCH ARTIFACT (bench-only). Error-handling anti-patterns are **defect-shaped,
not size-shaped**: an empty ``except: pass`` or a ``.unwrap()`` is a latent bug
regardless of how large the file is, so this is the Phase-3 candidate most likely
to fire *where the calibrated model goes blind* — small files that never trip the
complexity/coupling biomarkers. We therefore report the **within-band AUC** and
the **small-file (<=48 LOC) marginal OOF** explicitly, on top of the standard gate.

Pipeline (leakage-free — T0 snapshot only; defects are (T0, HEAD]):
  1. ``run_self_test()`` (``error_handling.py``) validates every detector on
     handcrafted fixtures FIRST — a noisy query is worse than none.
  2. Per repo: ``git show T0:path`` for every T0 source file (same universe rule
     as the file join), detect anti-patterns, cache per-file counts to
     ``results/health_defect_<repo>/error_handling.json``.
  3. Columns: ``eh_count`` (raw anti-pattern count) and ``eh_density``
     (count / code-line count). A 0 here is a *real* value (the file genuinely has
     no swallowed exceptions), so coverage = parseable-file fraction; absence only
     for unparseable/unsupported files.
  4. Full gate on both columns (keyword + SZZ) + small-file-masked marginal.

Run (absolute venv python; point dirs at the MAIN bench checkout)::

    $env:PYTHONIOENCODING="utf-8"
    C:\\Users\\ragha\\Desktop\\repowise\\.venv\\Scripts\\python.exe error_handling_experiment.py \\
        --results-dir C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\results \\
        --repos-dir   C:\\Users\\ragha\\Desktop\\repowise\\repowise-bench\\repos
"""
from __future__ import annotations

import argparse
import json
import os as _os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_oss = Path(_os.environ.get("REPOWISE_OSS_ROOT", str(Path(__file__).resolve().parents[3])))
for _pkg in ("packages/core/src", "packages/cli/src", "packages/server/src"):
    _pp = _oss / _pkg
    if _pp.exists() and str(_pp) not in sys.path:
        sys.path.insert(0, str(_pp))

import candidate_eval as ce
import error_handling as eh
from lib.defect_counter import _git, resolve_t0_sha
from lib.filters import is_test_file, normalize_path

# reuse naturalness' T0 helpers (same universe rule + git show)
from naturalness import _make_exclude_matcher, _show_bytes  # noqa: E402

from repowise.core.ingestion.languages import REGISTRY  # noqa: E402

_HERE = Path(__file__).resolve().parents[1]


def _list_t0_source_files(repo_dir, t0_sha, *, source_root, extensions, is_excluded):
    out = _git(["ls-tree", "-r", "--name-only", t0_sha], cwd=repo_dir)
    files = []
    for raw in out.split("\n"):
        f = normalize_path(raw)
        if not f or not f.startswith(source_root):
            continue
        if not any(f.endswith(e) for e in extensions):
            continue
        if is_test_file(f) or is_excluded(f):
            continue
        files.append(f)
    return files


def _code_lines(source: bytes) -> int:
    return max(1, sum(1 for ln in source.decode("utf-8", "replace").splitlines() if ln.strip()))


def build_repo(cfg, repos_dir, results_dir, *, rebuild):
    name = cfg["name"]
    out_path = results_dir / f"health_defect_{name}" / "error_handling.json"
    if out_path.exists() and not rebuild:
        return json.loads(out_path.read_text())

    repo_dir = (repos_dir / name).resolve()
    nested = repo_dir / name
    if nested.exists() and (nested / ".git").exists():
        repo_dir = nested
    if not repo_dir.exists():
        print(f"  SKIP {name}: clone missing")
        return None
    repo_dir = str(repo_dir)
    source_root = cfg["source_root"]
    extensions = tuple(cfg.get("extensions", [".py"]))
    is_excluded = _make_exclude_matcher(list(cfg.get("exclude") or []))
    t0_sha = resolve_t0_sha(repo_dir, cfg["t0_date"])
    files = _list_t0_source_files(repo_dir, t0_sha, source_root=source_root,
                                  extensions=extensions, is_excluded=is_excluded)
    parser_cache: dict = {}
    per_file: dict[str, dict] = {}
    t = time.time()
    n_parsed = 0
    total_hits = 0
    for path in files:
        ext = "." + path.rsplit(".", 1)[-1]
        lang = REGISTRY.from_extension(ext)
        if lang == "unknown":
            continue
        content = _show_bytes(repo_dir, t0_sha, path)
        if content is None:
            continue
        res = eh.detect_file(content, lang, parser_cache)
        if res is None:
            continue  # unparseable/unsupported → absent, never zero
        n_parsed += 1
        total_hits += res["eh_count"]
        per_file[path] = {**res, "code_lines": _code_lines(content)}
    meta = {"repo": name, "t0_sha": t0_sha, "n_files": n_parsed,
            "total_hits": total_hits, "build_seconds": round(time.time() - t, 1)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"meta": meta, "files": per_file}, indent=2))
    print(f"  {name:12s} files={n_parsed:4d} hits={total_hits:5d} {meta['build_seconds']:.1f}s")
    return {"meta": meta, "files": per_file}


def columns_from(built: dict[str, dict]) -> dict[str, dict[str, dict[str, float]]]:
    """Build {eh_count, eh_density, eh_flag} columns keyed by repo→file."""
    cols = {"eh_count": {}, "eh_density": {}, "eh_flag": {}}
    for repo, data in built.items():
        c, d, fl = {}, {}, {}
        for path, rec in data["files"].items():
            cnt = float(rec["eh_count"])
            c[path] = cnt
            d[path] = cnt / float(rec.get("code_lines", 1) or 1)
            fl[path] = 1.0 if cnt > 0 else 0.0
        cols["eh_count"][repo] = c
        cols["eh_density"][repo] = d
        cols["eh_flag"][repo] = fl
    return cols


def mask_small(column, nloc_by, threshold):
    out = {}
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
    ap.add_argument("--repos-dir", type=Path, default=_HERE.parent / "repos")
    ap.add_argument("--config", type=Path, default=_HERE / "config.yaml")
    ap.add_argument("--label", default="keyword")
    ap.add_argument("--repo", default="")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--small-threshold", type=float, default=48.0)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    print("=== Part C precondition: detector self-test on fixtures ===")
    if not eh.run_self_test():
        print("!! self-test FAILED — aborting (a noisy query is worse than none).")
        return

    import yaml
    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)
    out_path = args.out or (args.results_dir / "error_handling_scorecards.json")

    print(f"\n=== Building error-handling counts: {len(repos)} repos ===")
    built: dict[str, dict] = {}
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo}: not in config)")
            continue
        try:
            res = build_repo(cfg, args.repos_dir, args.results_dir, rebuild=args.rebuild)
        except Exception as exc:  # noqa: BLE001
            print(f"  (skip {repo}: {exc})")
            continue
        if res:
            built[repo] = res

    cols = columns_from(built)
    rows = ce.load_corpus(args.results_dir, args.config, args.label)
    nloc_by: dict[str, dict[str, float]] = {}
    for r in rows:
        nloc_by.setdefault(r["repo"], {})[r["file_path"]] = float(r["nloc"])
    cost = "tree-sitter pass over T0 snapshot (~walker-adjacent; cheap), 18/18 fixtures pass"

    # --- full gate on each column (keyword) ---------------------------------
    print(f"\n=== Full gate (label={args.label}) ===")
    cards = {}
    md_blocks = []
    for feat in ("eh_count", "eh_density", "eh_flag"):
        card = ce.evaluate_candidate(
            cols[feat], feat, results_dir=args.results_dir, config_path=args.config,
            label=args.label, corpus_rows=rows, cost_note=cost,
        )
        cards[feat] = card
        md_blocks.append(ce.scorecard_markdown(card))
        print("\n" + ce.scorecard_markdown(card))

    # --- cross-label (szz) on the primary columns ---------------------------
    print("\n=== Cross-label check (szz): eh_count, eh_density ===")
    rows_szz = ce.load_corpus(args.results_dir, args.config, "szz")
    for feat in ("eh_count", "eh_density"):
        card = ce.evaluate_candidate(
            cols[feat], feat, results_dir=args.results_dir, config_path=args.config,
            label="szz", corpus_rows=rows_szz, cost_note=cost,
        )
        cards[f"{feat}_szz"] = card
        md_blocks.append(ce.scorecard_markdown(card))
        print("\n" + ce.scorecard_markdown(card))

    # --- small-file-targeted marginal (the headline for Part C) -------------
    thr = args.small_threshold
    print(f"\n=== Small-file marginal (NLOC <= {thr:.0f}) — does it pay where the model is blind? ===")
    print(f"{'feat':12s} {'label':>8s} {'files':>6s} {'pos':>4s} {'AUCbase':>8s} "
          f"{'AUCcand':>8s} {'OOFΔ':>9s} {'Δ CI95':>20s} {'coefCI+':>8s}")
    small = {}
    for label in ("keyword", "szz"):
        rws = rows if label == "keyword" else rows_szz
        for feat in ("eh_count", "eh_density"):
            masked = mask_small(cols[feat], nloc_by, thr)
            card = ce.evaluate_candidate(
                masked, f"{feat}_small{int(thr)}", results_dir=args.results_dir,
                config_path=args.config, label=label, corpus_rows=rws,
                cost_note=f"masked NLOC<={thr}",
            )
            small[f"{feat}_{label}"] = card
            da = card["oof_auc_delta"]; dci = da["delta_ci95"]
            dci_s = f"[{dci[0]:+.4f},{dci[1]:+.4f}]" if dci else "n/a"
            coef_ok = card["gate_components"]["coef_excludes_zero_positive"]
            print(f"{feat:12s} {label:>8s} {card['n_files']:>6d} {card['n_positives']:>4d} "
                  f"{str(da['auc_base']):>8s} {str(da['auc_candidate']):>8s} "
                  f"{str(da['delta']):>9s} {dci_s:>20s} {str(coef_ok):>8s}")

    total_hits = sum(b["meta"]["total_hits"] for b in built.values())
    payload = {"cards": cards, "small_file": small, "cost_note": cost,
               "small_threshold": thr,
               "build_meta": {r: b["meta"] for r, b in built.items()},
               "total_hits": total_hits}
    out_path.write_text(json.dumps(payload, indent=2))
    out_path.with_suffix(".md").write_text("\n\n".join(md_blocks))
    print(f"\nTotal anti-pattern hits across corpus: {total_hits}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    sys.exit(main())
