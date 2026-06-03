#!/usr/bin/env python3
"""jitline_tokens.py — JITLine-style token bag-of-words arm for the Phase-4
head-to-head.

RESEARCH ARTIFACT (bench-only). JITLine (Pornprasit & Tantithamthavorn, MSR'21)
predicts defective changes from a **bag-of-tokens of the changed code** (then uses
LIME to push the commit score onto lines). This emits, per hunk, the **token bag of
its added lines** (the product tree-sitter leaf stream, same tokenizer the Phase-2
naturalness model uses) plus the identical labels/effort the additive arm uses, so
both models are evaluated on the **same hunks under the same effort-aware
localization protocol** (`hunk_localization._recall_curve`). The token model is
scored directly per hunk (no LIME) — a cleaner apples-to-apples than mixing
protocols; both arms share the localization machinery and only the feature set
differs (tokens vs interpretable change features).

Aligned to `hunk_dataset.py`'s walk so the hunk sets match repo-for-repo.

Output: `results/health_defect_<repo>/hunk_tokens.json` — `{meta, rows:[{commit,
file_path, n_added, n_buggy_lines, label, tokens:"tok tok ..."}]}`.

Run (venv python), from health-defect/::

    C:/Users/ragha/Desktop/repowise/.venv/Scripts/python.exe jitline_tokens.py \
        --results-dir <bench>/results --repos-dir <bench>/repos [--repo a,b]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

_BENCH_DIR = Path(__file__).resolve().parent
_REPOWISE_ROOT = _BENCH_DIR.parents[1]
for _src in ("core", "cli", "server"):
    _p = _REPOWISE_ROOT / "packages" / _src / "src"
    if _p.exists():
        sys.path.insert(0, str(_p))

from repowise.core.ingestion.languages import REGISTRY  # noqa: E402

from lib.defect_counter import _git, find_fix_commits, resolve_t0_sha  # noqa: E402
from lib.filters import is_test_file, normalize_path  # noqa: E402
from lib.function_szz import _norm_line, inducing_lines_by_file  # noqa: E402
import naturalness as nat  # noqa: E402
from hunk_dataset import (  # noqa: E402
    _make_exclude_matcher, _parse_added_hunks, _show_bytes, _commit_time,
)


def build_repo(cfg: dict, repos_dir: Path, results_dir: Path, *, window: int) -> dict | None:
    name = cfg["name"]
    repo_dir = (repos_dir / name).resolve()
    nested = repo_dir / name
    if nested.exists() and (nested / ".git").exists():
        repo_dir = nested
    if not repo_dir.exists():
        print(f"  SKIP {name}: {repo_dir} missing")
        return None
    repo_dir = str(repo_dir)

    source_root = cfg["source_root"]
    extensions = tuple(cfg.get("extensions", [".py"]))
    is_excluded = _make_exclude_matcher(list(cfg.get("exclude") or []))
    t0_sha = resolve_t0_sha(repo_dir, cfg["t0_date"])
    t_start = time.time()

    fixes = find_fix_commits(
        repo_dir, t0_sha, "HEAD",
        strategy=cfg["defect_strategy"],
        emoji=cfg.get("gitmoji_bug", "\U0001F41B"),
        prefix=cfg.get("bug_prefix", "Fixed #"),
        include=cfg.get("bug_keywords"),
        exclude=cfg.get("exclude_keywords"),
    )
    inducing = inducing_lines_by_file(
        repo_dir, t0_sha, fixes,
        source_root=source_root, extensions=extensions,
        fix_sha_set={s for s, _ in fixes}, variant="ag",
    )
    inducing_by_commit_file: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _file, m in inducing.items():
        nf = normalize_path(_file)
        for (sha, fp), _fixset in m.items():
            inducing_by_commit_file[(sha, nf)].add(fp)

    parser_cache: dict = {}
    log = _git(["log", t0_sha, "--no-merges", f"-{window}", "--format=%H%x00%ct"], cwd=repo_dir)
    commits = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        sha, _, ct = line.partition("\x00")
        commits.append((sha, int(ct)))
    commits.reverse()

    rows: list[dict] = []
    for sha, ct in commits:
        all_changed = _git(
            ["diff-tree", "--no-commit-id", "-r", "--name-only", sha], cwd=repo_dir
        ).strip().split("\n")
        src_files = [
            normalize_path(f) for f in all_changed
            if f and normalize_path(f).startswith(source_root)
            and any(normalize_path(f).endswith(e) for e in extensions)
            and not is_test_file(f) and not is_excluded(normalize_path(f))
        ]
        if not src_files:
            continue
        for path in src_files:
            induce_fps = inducing_by_commit_file.get((sha, path), set())
            diff = _git(
                ["diff", "--unified=0", "--no-color", f"{sha}^", sha, "--", path],
                cwd=repo_dir,
            )
            hunks = _parse_added_hunks(diff)
            if not hunks:
                continue
            ext = "." + path.rsplit(".", 1)[-1]
            lang = REGISTRY.from_extension(ext)
            line_tokens: dict[int, list[str]] = defaultdict(list)
            if lang != "unknown":
                content = _show_bytes(repo_dir, sha, path)
                if content is not None:
                    for tok, ln in nat.tokenize(content, lang, parser_cache):
                        line_tokens[ln].append(tok)
            for h in hunks:
                la = len(h)
                n_buggy = sum(1 for _, t in h if _norm_line(t) in induce_fps)
                toks: list[str] = []
                for ln, _txt in h:
                    toks.extend(line_tokens.get(ln, []))
                rows.append({
                    "commit": sha, "file_path": path,
                    "n_added": la, "n_buggy_lines": n_buggy,
                    "label": 1 if n_buggy > 0 else 0,
                    "tokens": " ".join(toks),
                })

    n_pos = sum(r["label"] for r in rows)
    out_dir = results_dir / f"health_defect_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"repo": name, "t0_sha": t0_sha, "window": window,
            "n_hunks": len(rows), "n_positive_hunks": n_pos,
            "build_seconds": round(time.time() - t_start, 1)}
    (out_dir / "hunk_tokens.json").write_text(json.dumps({"meta": meta, "rows": rows}))
    print(f"  {name:12s} hunks={len(rows):6d} pos={n_pos:5d} {meta['build_seconds']:.0f}s")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=_BENCH_DIR.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_BENCH_DIR.parent / "repos")
    ap.add_argument("--config", type=Path, default=_BENCH_DIR / "config.yaml")
    ap.add_argument("--repo", default="")
    ap.add_argument("--window", type=int, default=1500)
    args = ap.parse_args()

    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)
    print(f"=== JITLine token bags (window={args.window}) over {len(repos)} repos ===")
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo})")
            continue
        try:
            build_repo(cfg, args.repos_dir, args.results_dir, window=args.window)
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"  !! {repo} FAILED: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
