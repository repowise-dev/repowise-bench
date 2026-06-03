#!/usr/bin/env python3
"""hunk_dataset.py — Phase-4 per-hunk change dataset + line-resolution labels.

RESEARCH ARTIFACT (bench-only). The Phase-4 thesis: whole-repo *file*-level
size-orthogonal prediction has saturated (5/5 nulls, Phases 1-3), but inside a
**bounded diff** the unit can be narrowed to a hunk/line for free, where
localization carries direct reviewer payoff. This builds the substrate: every
non-merge commit in a pre-T0 window is decomposed into hunks; each hunk gets
interpretable per-hunk change features; a hunk is labelled **positive iff it
adds a bug-inducing line** (function-SZZ, the exact line-resolution label the
shipped change-risk model trains against).

Labels (leakage-free, same source of truth as the file/function datasets):
``lib/function_szz.inducing_lines_by_file`` blames the lines a *post-T0* fix
changed back to the ancestor-of-T0 commit that wrote them, keeping the specific
``(inducing_sha, whitespace-normalised line fingerprint)``. A hunk in commit C
is positive iff C is an inducing commit and one of the hunk's *added* lines has
a fingerprint in C's bug-inducing set. Because the inducing commit predates T0
and the buggy line is unchanged from C through T0 up to the fix's parent, the
label is strictly pre-T0 evidence — no leakage from the future fix.

Per-hunk features (all properties of the *change*, not file size — the plan's
six):
  * ``la_hunk``            lines added in the hunk (the localization effort unit)
  * ``local_entropy``      Shannon entropy of the added lines' lexical token mix
  * ``touches_fix_prone``  1 iff the file had >=1 prior fix before this commit
  * ``prior_fix_recur``    count of prior fixes on the file before this commit
  * ``surprisal_mean``     mean Phase-2 naturalness surprisal of the added lines,
                           scored against the **T0-anchored** global n-gram model
                           (rebuilt here from naturalness.py; global-only prob so
                           every added line is scorable regardless of survival to
                           T0 — avoids the survival-bias confound of looking the
                           value up only for lines that reach T0). ``None`` when
                           the file does not tokenize.
  * ``test_cochange_absent`` 1 iff the commit touched NO test file (commit-level)

Right-censoring (SZZ): a commit just before T0 has had the least time for its
induced bugs to surface as post-T0 fixes, so it is under-labelled. We drop the
most recent ``--gap-days`` before T0 from the dataset (the prototype's protocol).

Output: ``results/health_defect_<repo>/hunk_dataset.json`` — one row per hunk
(``n_added`` and ``n_buggy_lines`` retained so the line-level effort-aware eval
reconstructs exact per-line recall from hunk rows).

Run (venv python — live editable walker/parser; NOT ``uv run``)::

    cd health-defect
    C:/Users/ragha/Desktop/repowise/.venv/Scripts/python.exe hunk_dataset.py \
        --results-dir <bench>/results --repos-dir <bench>/repos \
        [--repo clap] [--window 1500] [--gap-days 120] [--no-surprisal]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from bisect import bisect_left
from collections import Counter, defaultdict
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
from lib.szz import _ext  # noqa: E402

# Reuse the Phase-2 naturalness model verbatim (global n-gram, T0 snapshot).
import naturalness as nat  # noqa: E402

_DAY = 86400
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# Cheap lexical tokenizer for local entropy (identifiers / numbers / operators).
_LEX_RE = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*|[^\s\w]")


def _make_exclude_matcher(patterns: list[str]):
    if not patterns:
        return lambda _p: False
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return lambda p: spec.match_file(p)


def _commit_time(repo_dir: str, sha: str) -> int:
    out = _git(["show", "-s", "--format=%ct", sha], cwd=repo_dir)
    return int(out.strip().split("\n")[0])


def _local_entropy(texts: list[str]) -> float:
    """Shannon entropy (bits) of the lexical-token distribution of the added
    lines — a within-hunk diversity measure (high = many distinct tokens)."""
    counts: Counter[str] = Counter()
    for t in texts:
        counts.update(_LEX_RE.findall(t))
    total = sum(counts.values())
    if total <= 0 or len(counts) < 2:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _build_prior_fix_index(
    repo_dir: str, t0_sha: str, cfg: dict, source_root: str, extensions: tuple[str, ...]
) -> dict[str, list[int]]:
    """Per-file sorted list of *prior* (root..T0) fix-commit timestamps, for the
    prior-fix-recurrence feature. Uses the same fix classifier as the labels."""
    roots = _git(["rev-list", "--max-parents=0", t0_sha], cwd=repo_dir).strip().split("\n")
    root = roots[-1] if roots and roots[-1] else t0_sha
    prior_fixes = find_fix_commits(
        repo_dir, root, t0_sha,
        strategy=cfg["defect_strategy"],
        emoji=cfg.get("gitmoji_bug", "\U0001F41B"),
        prefix=cfg.get("bug_prefix", "Fixed #"),
        include=cfg.get("bug_keywords"),
        exclude=cfg.get("exclude_keywords"),
    )
    by_file: dict[str, list[int]] = defaultdict(list)
    for sha, _msg in prior_fixes:
        try:
            ct = _commit_time(repo_dir, sha)
        except Exception:  # noqa: BLE001
            continue
        out = _git(["diff-tree", "--no-commit-id", "-r", "--name-only", sha], cwd=repo_dir)
        for f in out.strip().split("\n"):
            f = normalize_path(f)
            if f and f.startswith(source_root) and any(f.endswith(e) for e in extensions):
                by_file[f].append(ct)
    for f in by_file:
        by_file[f].sort()
    return by_file


def _show_bytes(repo_dir: str, sha: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{sha}:{path}"], cwd=repo_dir, capture_output=True
    )
    return proc.stdout if proc.returncode == 0 else None


def _file_line_surprisal(
    content: bytes, lang: str, gmodel, vocab_size: int, parser_cache: dict
) -> dict[int, float]:
    """Per-line mean surprisal (bits) of *content* under the global T0 model.

    Global-only probability (no within-file cache): the cache needs the file's
    full left-context which a historical hunk does not own, and global-only is a
    clean, leakage-free per-line surprisal for any revision's snapshot."""
    toks = nat.tokenize(content, lang, parser_cache)
    if not toks:
        return {}
    order = gmodel.order
    pad = [nat._BOS] * (order - 1)
    seq = pad + [t for t, _ in toks]
    by_line: dict[int, list[float]] = defaultdict(list)
    for idx, (tok, line) in enumerate(toks):
        pos = idx + (order - 1)
        ctx = tuple(seq[pos - (order - 1): pos])
        p = gmodel.prob(ctx, tok)
        by_line[line].append(-math.log2(max(p, nat._TINY)))
    return {ln: sum(v) / len(v) for ln, v in by_line.items() if v}


def _parse_added_hunks(diff: str) -> list[list[tuple[int, str]]]:
    """Parse a ``--unified=0`` diff body into hunks; each hunk → list of
    ``(new_lineno, added_text)`` for its added (``+``) lines."""
    hunks: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] | None = None
    new_ln = 0
    for raw in diff.split("\n"):
        m = _HUNK_RE.match(raw)
        if m:
            if cur is not None:
                hunks.append(cur)
            cur = []
            new_ln = int(m.group(1))
            continue
        if cur is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            cur.append((new_ln, raw[1:]))
            new_ln += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            pass  # deletion consumes no new-file line number
    if cur is not None:
        hunks.append(cur)
    return [h for h in hunks if h]


def _build_global_model(
    repo_dir: str, t0_sha: str, cfg: dict, source_root: str,
    extensions: tuple[str, ...], is_excluded, parser_cache: dict,
):
    """Rebuild the Phase-2 T0-anchored global n-gram model (no cache component)."""
    files = nat._list_t0_source_files(
        repo_dir, t0_sha, source_root=source_root,
        extensions=extensions, is_excluded=is_excluded,
    )
    vocab: set[str] = {nat._BOS, nat._STR, nat._NUM}
    file_tokens = []
    for path in files:
        ext = "." + path.rsplit(".", 1)[-1]
        lang = REGISTRY.from_extension(ext)
        if lang == "unknown":
            continue
        content = _show_bytes(repo_dir, t0_sha, path)
        if content is None:
            continue
        toks = nat.tokenize(content, lang, parser_cache)
        if not toks:
            continue
        file_tokens.append([t for t, _ in toks])
        vocab.update(t for t in file_tokens[-1])
    if not file_tokens:
        return None, 0
    vocab_size = len(vocab)
    gmodel = nat.NgramModel(nat.DEFAULT_ORDER, vocab_size)
    for seq in file_tokens:
        gmodel.add_tokens(seq)
    return gmodel, vocab_size


def build_repo(cfg: dict, repos_dir: Path, results_dir: Path, *,
               window: int, gap_days: float, with_surprisal: bool) -> dict | None:
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
    t0_time = _commit_time(repo_dir, t0_sha)
    gap_cutoff = t0_time - gap_days * _DAY
    t_start = time.time()

    # --- labels: function-SZZ bug-inducing lines, pooled by inducing commit ---
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
    # Key by (inducing_commit, file) + fingerprint — exactly the granularity
    # build_function_dataset matches at (mirrors its induce_map[path] lookup).
    # Pooling fingerprints across a commit's files over-attributes via common
    # short lines (e.g. `return response;`) recurring in sibling files.
    inducing_by_commit_file: dict[tuple[str, str], set[str]] = defaultdict(set)
    inducing_commits_set: set[str] = set()
    for _file, m in inducing.items():
        nf = normalize_path(_file)
        for (sha, fp), _fixset in m.items():
            inducing_by_commit_file[(sha, nf)].add(fp)
            inducing_commits_set.add(sha)
    n_inducing_commits = len(inducing_commits_set)

    prior_fix = _build_prior_fix_index(repo_dir, t0_sha, cfg, source_root, extensions)

    parser_cache: dict = {}
    gmodel = None
    vocab_size = 0
    if with_surprisal:
        gmodel, vocab_size = _build_global_model(
            repo_dir, t0_sha, cfg, source_root, extensions, is_excluded, parser_cache
        )

    # --- walk a window of pre-T0 non-merge commits, oldest first -------------
    log = _git(
        ["log", t0_sha, "--no-merges", f"-{window}", "--format=%H%x00%ct"],
        cwd=repo_dir,
    )
    commits = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        sha, _, ct = line.partition("\x00")
        commits.append((sha, int(ct)))
    commits.reverse()  # oldest first (time order)

    rows: list[dict] = []
    surphit = surpmiss = 0
    for sha, ct in commits:
        if ct > gap_cutoff:
            continue  # right-censoring guard: under-labelled near T0
        # all changed files (for the commit-level test-co-change signal)
        all_changed = _git(
            ["diff-tree", "--no-commit-id", "-r", "--name-only", sha], cwd=repo_dir
        ).strip().split("\n")
        commit_touches_test = any(is_test_file(f) for f in all_changed if f)
        test_absent = 0 if commit_touches_test else 1

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

            line_surp: dict[int, float] = {}
            if with_surprisal and gmodel is not None:
                ext = "." + path.rsplit(".", 1)[-1]
                lang = REGISTRY.from_extension(ext)
                if lang != "unknown":
                    content = _show_bytes(repo_dir, sha, path)
                    if content is not None:
                        line_surp = _file_line_surprisal(
                            content, lang, gmodel, vocab_size, parser_cache
                        )

            # prior-fix recurrence for this file at this commit's time
            ftimes = prior_fix.get(path, [])
            recur = bisect_left(ftimes, ct)

            for h in hunks:
                texts = [t for _, t in h]
                la = len(h)
                n_buggy = sum(1 for _, t in h if _norm_line(t) in induce_fps)
                surps = [line_surp[ln] for ln, _ in h if ln in line_surp]
                if with_surprisal:
                    if surps:
                        surphit += 1
                    else:
                        surpmiss += 1
                rows.append({
                    "repo": name,
                    "commit": sha,
                    "ct": ct,
                    "file_path": path,
                    "la_hunk": la,
                    "local_entropy": round(_local_entropy(texts), 5),
                    "touches_fix_prone": 1 if recur > 0 else 0,
                    "prior_fix_recur": recur,
                    "surprisal_mean": round(sum(surps) / len(surps), 5) if surps else None,
                    "test_cochange_absent": test_absent,
                    "n_added": la,
                    "n_buggy_lines": n_buggy,
                    "label": 1 if n_buggy > 0 else 0,
                })

    n_pos = sum(r["label"] for r in rows)
    n_buggy_lines = sum(r["n_buggy_lines"] for r in rows)
    n_added_lines = sum(r["n_added"] for r in rows)
    out_dir = results_dir / f"health_defect_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "repo": name, "t0_sha": t0_sha, "t0_time": t0_time,
        "window": window, "gap_days": gap_days,
        "n_hunks": len(rows), "n_positive_hunks": n_pos,
        "n_buggy_lines": n_buggy_lines, "n_added_lines": n_added_lines,
        "n_inducing_commits": n_inducing_commits,
        "with_surprisal": with_surprisal,
        "surprisal_coverage": round(surphit / (surphit + surpmiss), 4) if (surphit + surpmiss) else None,
        "build_seconds": round(time.time() - t_start, 1),
    }
    (out_dir / "hunk_dataset.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, indent=2)
    )
    print(f"  {name:12s} hunks={len(rows):6d} pos={n_pos:5d} "
          f"({n_pos / max(len(rows),1):.1%})  buggy_lines={n_buggy_lines:5d}  "
          f"surp_cov={meta['surprisal_coverage']}  {meta['build_seconds']:.0f}s")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_BENCH_DIR.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_BENCH_DIR.parent / "repos")
    ap.add_argument("--config", type=Path, default=_BENCH_DIR / "config.yaml")
    ap.add_argument("--repo", default="", help="comma list; default = all config repos")
    ap.add_argument("--window", type=int, default=1500, help="recent pre-T0 non-merge commits")
    ap.add_argument("--gap-days", type=float, default=120.0)
    ap.add_argument("--no-surprisal", action="store_true", help="skip the Phase-2 surprisal feature (fast)")
    args = ap.parse_args()

    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)

    print(f"=== Hunk dataset (window={args.window} gap={args.gap_days}d "
          f"surprisal={not args.no_surprisal}) over {len(repos)} repos ===")
    summaries = []
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo}: not in config)")
            continue
        try:
            s = build_repo(cfg, args.repos_dir, args.results_dir,
                           window=args.window, gap_days=args.gap_days,
                           with_surprisal=not args.no_surprisal)
            if s:
                summaries.append(s)
        except Exception as exc:  # noqa: BLE001 — one bad repo must not abort
            import traceback
            print(f"  !! {repo} FAILED: {exc}")
            traceback.print_exc()

    th = sum(s["n_hunks"] for s in summaries)
    tp = sum(s["n_positive_hunks"] for s in summaries)
    tb = sum(s["n_buggy_lines"] for s in summaries)
    print(f"\n=== TOTAL hunks={th} positive={tp} ({tp/max(th,1):.1%}) "
          f"buggy_lines={tb} over {len(summaries)} repos ===")


if __name__ == "__main__":
    main()
