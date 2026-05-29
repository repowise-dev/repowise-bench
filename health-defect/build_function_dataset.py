#!/usr/bin/env python3
"""Build the function/symbol-level defect dataset (Phase 8, bench side).

For each corpus repo this:

  1. Resolves T0 and the post-T0 fix set (same source of truth as file-level).
  2. Runs function-level SZZ (``lib/function_szz``) → ``file -> {inducing_sha ->
     {fix_sha}}`` (bug-inducing commits that are ancestors of T0).
  3. Lists every T0 source file (``git ls-tree`` at T0, filtered to source_root +
     extensions, test files + index-excludes dropped — identical universe rule to
     the file-level join).
  4. For each file, **at T0**:
       * ``git show T0:path`` → source bytes → ``walk_file`` → per-function spans
         + structural features (the product walker — same code that scores).
       * ``git blame T0 -- path`` → per-line (sha, author_time) → per-function
         process features (modification count, recent mods, median line age).
       * A function is **defective-at-T0** iff any of its T0 lines is blamed to a
         bug-inducing commit; defect_count = #distinct post-T0 fixes so attributed.
  5. Emits ``results/health_defect_<repo>/function_joined.json`` — the
     function-granularity analogue of ``joined_data.json``.

Run with the venv interpreter (live editable install of the product walker):

    ../../.venv/Scripts/python.exe build_function_dataset.py [--repo NAME]

Deterministic, cached SZZ, pure git subprocesses + the in-tree walker. No new
dependency; the only product import is the read-only complexity walker + the
porcelain-blame parser + the language registry.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

import yaml

_BENCH_DIR = Path(__file__).resolve().parent
_REPOWISE_ROOT = _BENCH_DIR.parents[1]
for _src in ("core", "cli", "server"):
    p = _REPOWISE_ROOT / "packages" / _src / "src"
    if p.exists():
        sys.path.insert(0, str(p))

from repowise.core.analysis.health.complexity.walker import walk_file  # noqa: E402
from repowise.core.ingestion.git_indexer.function_blame import _parse_porcelain  # noqa: E402
from repowise.core.ingestion.languages import REGISTRY  # noqa: E402

from lib.defect_counter import _git, find_fix_commits, resolve_t0_sha  # noqa: E402
from lib.filters import is_test_file, normalize_path  # noqa: E402
from lib.function_szz import _norm_line, inducing_lines_by_file  # noqa: E402

# Functions below this NLOC are mostly trivial accessors/one-liners — they add
# label noise (a one-line getter is rarely "the buggy symbol") and dominate the
# count. Mirrors the file-level min_nloc gate in spirit.
_MIN_FUNC_NLOC = 3
_DAY = 86400
_RECENT_WINDOW_DAYS = 90


def _make_exclude_matcher(patterns: list[str]):
    if not patterns:
        return lambda _p: False
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return lambda p: spec.match_file(p)


def _t0_commit_time(repo_dir: str, t0_sha: str) -> int:
    out = _git(["show", "-s", "--format=%ct", t0_sha], cwd=repo_dir)
    return int(out.strip().split("\n")[0])


def _list_t0_source_files(
    repo_dir: str,
    t0_sha: str,
    *,
    source_root: str,
    extensions: tuple[str, ...],
    is_excluded,
) -> list[str]:
    out = _git(["ls-tree", "-r", "--name-only", t0_sha], cwd=repo_dir)
    files: list[str] = []
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


def _show_bytes(repo_dir: str, t0_sha: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{t0_sha}:{path}"],
        cwd=repo_dir, capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _blame_at_t0(repo_dir: str, t0_sha: str, path: str) -> dict[int, tuple[str, int]]:
    proc = subprocess.run(
        ["git", "blame", "-w", "-C", "--line-porcelain", t0_sha, "--", path],
        cwd=repo_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not proc.stdout:
        return {}
    lines, _authors = _parse_porcelain(proc.stdout)
    return lines


def _process_features(
    blame: dict[int, tuple[str, int]],
    start: int,
    end: int,
    t0_time: int,
) -> tuple[int, int, float | None]:
    """(modification_count, recent_modifications, median_age_days) over a span."""
    shas: set[str] = set()
    recent: set[str] = set()
    times: list[int] = []
    recent_cutoff = t0_time - _RECENT_WINDOW_DAYS * _DAY
    for ln in range(start, end + 1):
        entry = blame.get(ln)
        if entry is None:
            continue
        sha, ts = entry
        shas.add(sha)
        if ts > 0:
            times.append(ts)
        if ts >= recent_cutoff:
            recent.add(sha)
    age_days: float | None = None
    if times:
        age_days = round((t0_time - statistics.median(times)) / _DAY, 1)
    return len(shas), len(recent), age_days


def build_repo(repo_config: dict, repos_dir: Path, results_dir: Path) -> dict | None:
    name = repo_config["name"]
    repo_dir = repos_dir / name
    nested = repo_dir / name
    if nested.exists() and (nested / ".git").exists():
        repo_dir = nested
    if not repo_dir.exists():
        print(f"  SKIP {name}: {repo_dir} missing")
        return None
    repo_dir = str(repo_dir)

    source_root = repo_config["source_root"]
    extensions = tuple(repo_config.get("extensions", [".py"]))
    is_excluded = _make_exclude_matcher(list(repo_config.get("exclude") or []))

    t0_sha = resolve_t0_sha(repo_dir, repo_config["t0_date"])
    t0_time = _t0_commit_time(repo_dir, t0_sha)
    fixes = find_fix_commits(
        repo_dir, t0_sha, "HEAD",
        strategy=repo_config["defect_strategy"],
        emoji=repo_config.get("gitmoji_bug", "\U0001F41B"),
        prefix=repo_config.get("bug_prefix", "Fixed #"),
        include=repo_config.get("bug_keywords"),
        exclude=repo_config.get("exclude_keywords"),
    )
    fix_sha_set = {s for s, _ in fixes}
    print(f"  {name}: T0 {t0_sha[:12]} | {len(fixes)} fixes")

    inducing = inducing_lines_by_file(
        repo_dir, t0_sha, fixes,
        source_root=source_root, extensions=extensions,
        fix_sha_set=fix_sha_set, variant="ag",
    )
    print(f"  {name}: {len(inducing)} SZZ-defective files")

    files = _list_t0_source_files(
        repo_dir, t0_sha,
        source_root=source_root, extensions=extensions, is_excluded=is_excluded,
    )

    rows: list[dict] = []
    for path in files:
        ext = "." + path.rsplit(".", 1)[-1]
        lang = REGISTRY.from_extension(ext)
        content = _show_bytes(repo_dir, t0_sha, path)
        if content is None:
            continue
        fc = walk_file(path, lang, content)
        if not fc.functions:
            continue
        # Per-function label needs the file's inducing-line fingerprints matched
        # against the T0 blame + T0 line content; process features need the blame.
        induce_map = inducing.get(path, {})
        blame = _blame_at_t0(repo_dir, t0_sha, path)
        text_lines = content.decode("utf-8", errors="replace").split("\n")
        for fn in fc.functions:
            if fn.nloc < _MIN_FUNC_NLOC:
                continue
            attributed_fixes: set[str] = set()
            if induce_map:
                for ln in range(fn.start_line, fn.end_line + 1):
                    entry = blame.get(ln)
                    if entry is None:
                        continue
                    txt = text_lines[ln - 1] if 1 <= ln <= len(text_lines) else ""
                    key = (entry[0], _norm_line(txt))
                    if key in induce_map:
                        attributed_fixes |= induce_map[key]
            mod_count, recent_mods, age_days = _process_features(
                blame, fn.start_line, fn.end_line, t0_time
            )
            rows.append({
                "function_id": f"{path}::{fn.name}:{fn.start_line}",
                "file_path": path,
                "name": fn.name,
                "start_line": fn.start_line,
                "end_line": fn.end_line,
                # Structural features (the product walker).
                "ccn": fn.ccn,
                "cognitive": fn.cognitive,
                "max_nesting": fn.max_nesting,
                "nloc": fn.nloc,
                "param_count": fn.param_count,
                "n_conditions": len(fn.complex_conditions),
                "bumps": fn.bumps,
                # Process features (T0 blame).
                "mod_count": mod_count,
                "recent_mods": recent_mods,
                "age_days": age_days,
                # Label.
                "defect_count": len(attributed_fixes),
                "label": 1 if attributed_fixes else 0,
            })

    n_pos = sum(r["label"] for r in rows)
    out_dir = results_dir / f"health_defect_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "function_joined.json").write_text(json.dumps(rows, indent=2))
    summary = {
        "repo": name, "t0_sha": t0_sha, "n_files": len(files),
        "n_functions": len(rows), "n_positive_functions": n_pos,
        "n_szz_defective_files": len(inducing),
    }
    print(f"  {name}: {len(rows)} functions / {n_pos} positive "
          f"({n_pos / len(rows):.1%})" if rows else f"  {name}: 0 functions")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="Only this repo (by config name)")
    ap.add_argument("--config", type=Path, default=_BENCH_DIR / "config.yaml")
    ap.add_argument("--repos-dir", type=Path, default=None)
    ap.add_argument("--results-dir", type=Path, default=None)
    args = ap.parse_args()

    config = yaml.safe_load(args.config.read_text())
    repos_dir = (args.repos_dir or (_BENCH_DIR.parent / "repos")).resolve()
    results_dir = (args.results_dir or (_BENCH_DIR.parent / "results")).resolve()

    summaries = []
    for rc in config["repos"]:
        if args.repo and rc["name"] != args.repo:
            continue
        try:
            s = build_repo(rc, repos_dir, results_dir)
            if s:
                summaries.append(s)
        except Exception as exc:  # noqa: BLE001 — one bad repo must not abort the batch
            import traceback
            print(f"  !! {rc['name']} FAILED: {exc}")
            traceback.print_exc()

    print("\n=== Function-level dataset summary ===")
    tot_fn = tot_pos = 0
    for s in summaries:
        print(f"  {s['repo']:12s} files={s['n_files']:4d}  functions={s['n_functions']:5d}  "
              f"positive={s['n_positive_functions']:4d}  szz_files={s['n_szz_defective_files']:3d}")
        tot_fn += s["n_functions"]
        tot_pos += s["n_positive_functions"]
    if tot_fn:
        print(f"  {'TOTAL':12s} functions={tot_fn}  positive={tot_pos} ({tot_pos / tot_fn:.1%})")


if __name__ == "__main__":
    main()
