#!/usr/bin/env python3
"""Tiered test-coverage acquisition for the health-defect benchmark (Phase 7).

RESEARCH ARTIFACT — local-stash only, never committed. Produces a normalized
``repowise-coverage-v1`` JSON per repo at (or near) T0, written to
``repowise-bench/results/health_defect_<repo>/coverage_t0.json`` so the
benchmark's ``_resolve_coverage_path`` picks it up and feeds real line coverage
to ``untested_hotspot`` / ``coverage_gap`` instead of the has-test-file fallback.

Two tiers (see plan §Phase-7 Part A):

  * **Tier 1 (scrape)** — Codecov v2 public API. Paginate the repo's commit list
    newest-first, find the covered commit whose date is closest to T0 (preferring
    on/before T0 to avoid leakage), fetch its per-file report, and normalize.
    Records the *actual* source commit + date + day-skew from T0 in a sidecar.
  * **Tier 2 (run)** — execute the suite at the T0 worktree with coverage and
    parse the emitted report. Implemented for Python (``pytest --cov`` →
    Cobertura XML). Go/Rust runners are unavailable on this box (no toolchain),
    so those repos are Tier-1-only here; documented as absent otherwise.

Absent ≠ zero: a repo with no obtainable coverage gets NO artifact (the bench
then runs coverage-blind), never an all-zero file.

Usage (venv python):
    .venv/Scripts/python.exe local-stash/collect_coverage.py --repo rich
    .venv/Scripts/python.exe local-stash/collect_coverage.py --all --tier codecov
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import yaml

_BENCH = Path(__file__).resolve().parents[1]
_CONFIG = _BENCH / "config.yaml"
_RESULTS = _BENCH.parent / "results"
_REPOS = _BENCH.parent / "repos"
_T0 = date(2025, 11, 23)

# owner/repo on GitHub, derived from each config repo_url.
_OWNER_OVERRIDE: dict[str, tuple[str, str]] = {}


def _owner_repo(repo_url: str) -> tuple[str, str] | None:
    if not repo_url:
        return None
    s = repo_url.removesuffix(".git").rstrip("/")
    parts = s.split("/")
    if len(parts) < 2:
        return None
    return parts[-2], parts[-1]


def _http_json(url: str, timeout: int = 30) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": "repowise-cov-collect"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


# ---------------------------------------------------------------- Tier 1: Codecov

# A coverage report whose commit is this far from T0 is not "coverage at T0" —
# the file set and test surface have drifted. Treat as absent (NOT zero).
_MAX_SKEW_DAYS = 120


def codecov_candidates(owner: str, repo: str, t0: date, max_pages: int = 80):
    """Return covered commits within ±_MAX_SKEW_DAYS of T0, nearest-first.

    Each item is ``(abs_skew, sha, date_str, coverage)``. Prefers commits
    on/before T0 (no leakage); the caller walks the list until a report parses.
    """
    base = f"https://api.codecov.io/api/v2/github/{owner}/repos/{repo}/commits/"
    cands: list[tuple[int, str, str, float]] = []
    for page in range(1, max_pages + 1):
        d = _http_json(f"{base}?page={page}&page_size=100")
        if not d:
            break
        results = d.get("results") or []
        if not results:
            break
        page_oldest = None
        for c in results:
            cov = (c.get("totals") or {}).get("coverage")
            ts = (c.get("timestamp") or "")[:10]
            if not ts:
                continue
            try:
                cd = date.fromisoformat(ts)
            except ValueError:
                continue
            page_oldest = cd if page_oldest is None else min(page_oldest, cd)
            if cov is None:
                continue
            skew = (t0 - cd).days  # >0 => before T0
            if abs(skew) <= _MAX_SKEW_DAYS:
                # Rank: prefer on/before T0, then by absolute distance.
                rank = abs(skew) + (0 if skew >= 0 else 1000)
                cands.append((rank, c["commitid"], ts, float(cov)))
        if page_oldest is not None and (t0 - page_oldest).days > _MAX_SKEW_DAYS:
            break
    cands.sort(key=lambda x: x[0])
    return cands


def codecov_normalize(report: dict, source_root: str, extensions: tuple[str, ...]):
    """Codecov per-file report → repowise-coverage-v1 files dict.

    Uses each file's unambiguous ``totals`` (coverage %, line count, branch
    rate). The ``line_coverage`` array's second-value encoding is ambiguous
    across Codecov versions, so covered-line *sets* are left empty — the
    coverage biomarkers derive uncovered counts from pct × total.
    """
    out: dict[str, dict] = {}
    root = source_root.strip("/")
    for f in report.get("files") or []:
        name = (f.get("name") or "").replace("\\", "/").lstrip("/")
        if not name.endswith(extensions):
            continue
        if root and not name.startswith(root + "/") and name != root:
            continue
        t = f.get("totals") or {}
        cov = t.get("coverage")
        lines = t.get("lines")
        if cov is None or not lines:
            continue
        branches = t.get("branches") or 0
        # Codecov 'coverage' on branches isn't separated per-file; approximate
        # branch% only when branch partials are reported.
        partials = t.get("partials") or 0
        branch_pct = None
        if branches:
            hit_branches = max(0, branches - partials)
            branch_pct = round(hit_branches / branches * 100.0, 2)
        out[name] = {
            "line_coverage_pct": round(float(cov), 2),
            "branch_coverage_pct": branch_pct,
            "total_coverable_lines": int(lines),
        }
    return out


def acquire_codecov(repo_cfg: dict) -> tuple[dict, dict] | None:
    owner_repo = _owner_repo(repo_cfg.get("repo_url", ""))
    if not owner_repo:
        return None
    owner, repo = owner_repo
    cands = codecov_candidates(owner, repo, _T0)
    if not cands:
        print(f"    codecov: no covered commit within {_MAX_SKEW_DAYS}d of T0 "
              f"for {owner}/{repo}")
        return None
    exts = tuple(repo_cfg.get("extensions", [".py"]))
    # Walk nearest-first until a report parses to a non-empty filtered set.
    for _rank, sha, ts, cov in cands[:8]:
        report = _http_json(
            f"https://api.codecov.io/api/v2/github/{owner}/repos/{repo}/report/?sha={sha}"
        )
        if not report or not report.get("files"):
            continue
        files = codecov_normalize(report, repo_cfg["source_root"], exts)
        if not files:
            continue
        return files, {
            "tier": "codecov",
            "source_commit": sha,
            "source_date": ts,
            "skew_days_from_t0": (_T0 - date.fromisoformat(ts)).days,
            "repo_overall_coverage": cov,
            "n_files": len(files),
        }
    print(f"    codecov: no usable report among {len(cands)} candidates")
    return None


# --------------------------------------------------------------- Tier 2: run suite

def _resolve_repo_dir(name: str) -> Path:
    base = _REPOS / name
    nested = base / name
    return nested if (nested / ".git").exists() else base


def acquire_pytest(repo_cfg: dict, sha: str) -> tuple[dict, dict] | None:
    """Run the Python suite at T0 with coverage → Cobertura XML → normalized.

    Heavy + flaky (deps must install for a 6-month-old checkout); best-effort.
    Uses an isolated venv so the dev environment is untouched.
    """
    name = repo_cfg["name"]
    repo_dir = _resolve_repo_dir(name)
    wt = Path(tempfile.gettempdir()) / f"cov-{name}-{sha[:10]}"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                   cwd=repo_dir, capture_output=True)
    try:
        r = subprocess.run(["git", "worktree", "add", "--detach", str(wt), sha],
                           cwd=repo_dir, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"    pytest: worktree add failed: {r.stderr[-200:]}")
            return None
        venv = wt / ".cov-venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-U", "pip"],
                       cwd=wt, capture_output=True, text=True, timeout=300)
        # Editable install + common Python test deps (best-effort; suites vary).
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-e", "."],
                       cwd=wt, capture_output=True, text=True, timeout=900)
        subprocess.run(
            [str(py), "-m", "pip", "install", "-q", "pytest", "pytest-cov",
             "pytest-mock", "hypothesis", "dirty-equals", "email-validator",
             "cloudpickle", "tzdata"],
            cwd=wt, capture_output=True, text=True, timeout=600,
        )
        xml = wt / "cov.xml"
        run = subprocess.run(
            [str(py), "-m", "pytest", "-x", "-q", "--no-header",
             f"--cov={repo_cfg['source_root'].strip('/')}",
             "--cov-report", f"xml:{xml}", "-p", "no:cacheprovider"],
            cwd=wt, capture_output=True, text=True, timeout=1800,
        )
        if not xml.exists():
            print(f"    pytest: no coverage xml produced (rc={run.returncode}); "
                  f"{run.stdout[-300:]}")
            return None
        sys.path.insert(0, str(_ROOT / "packages" / "core" / "src"))
        from repowise.core.analysis.health.coverage import parse_cobertura  # noqa: E402
        rep = parse_cobertura(xml.read_text(encoding="utf-8", errors="replace"))
        exts = tuple(repo_cfg.get("extensions", [".py"]))
        files = {
            fc.file_path: {
                "line_coverage_pct": fc.line_coverage_pct,
                "branch_coverage_pct": fc.branch_coverage_pct,
                "covered_lines": fc.covered_lines,
                "total_coverable_lines": fc.total_coverable_lines,
            }
            for fc in rep.files if fc.file_path.endswith(exts)
        }
        if not files:
            return None
        return files, {"tier": "pytest-cov", "source_commit": sha,
                       "source_date": str(_T0), "skew_days_from_t0": 0,
                       "n_files": len(files)}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=repo_dir, capture_output=True)


def _which(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def acquire_node(repo_cfg: dict, sha: str) -> tuple[dict, dict] | None:
    """Run a JS/TS suite at T0 under c8 → LCOV → normalized (best-effort).

    Runner-agnostic: c8 uses V8's built-in coverage, so it wraps whatever test
    command the repo defines. Browser-only suites (e.g. karma) won't be captured
    and yield nothing → marked absent. Heavy/flaky; wrapped in try/except.
    """
    name = repo_cfg["name"]
    repo_dir = _resolve_repo_dir(name)
    wt = Path(tempfile.gettempdir()) / f"cov-{name}-{sha[:10]}"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                   cwd=repo_dir, capture_output=True)
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    try:
        r = subprocess.run(["git", "worktree", "add", "--detach", str(wt), sha],
                           cwd=repo_dir, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"    node: worktree add failed: {r.stderr[-200:]}")
            return None
        pm = npm
        inst = [npm, "install", "--no-audit", "--no-fund"]
        if (wt / "pnpm-lock.yaml").exists() and _which("pnpm"):
            pm = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
            inst = [pm, "install"]
        elif (wt / "yarn.lock").exists() and _which("yarn"):
            pm = "yarn.cmd" if sys.platform == "win32" else "yarn"
            inst = [pm, "install"]
        print(f"    node: installing deps ({inst[0]})…")
        ins = subprocess.run(inst, cwd=wt, capture_output=True, text=True, timeout=1800)
        if ins.returncode != 0:
            print(f"    node: install failed: {ins.stderr[-300:]}")
            return None
        # Run the suite under c8, emitting LCOV. Try the repo's own test script.
        print("    node: running suite under c8…")
        run = subprocess.run(
            [npx, "-y", "c8", "--reporter=lcovonly", "--reporter=text-summary",
             "--", pm.replace(".cmd", "") if pm != npm else "npm", "test"],
            cwd=wt, capture_output=True, text=True, timeout=2400,
        )
        lcov = None
        for cand in (wt / "coverage" / "lcov.info", *wt.glob("**/lcov.info")):
            if cand.exists():
                lcov = cand
                break
        if lcov is None:
            print(f"    node: no lcov produced (rc={run.returncode}); {run.stdout[-200:]}")
            return None
        sys.path.insert(0, str(_ROOT / "packages" / "core" / "src"))
        from repowise.core.analysis.health.coverage import parse_lcov  # noqa: E402
        rep = parse_lcov(lcov.read_text(encoding="utf-8", errors="replace"))
        wt_posix = wt.as_posix().rstrip("/") + "/"
        root = repo_cfg["source_root"].strip("/")
        exts = tuple(repo_cfg.get("extensions", [".ts"]))
        files = {}
        for fc in rep.files:
            p = fc.file_path.replace("\\", "/")
            if p.startswith(wt_posix):
                p = p[len(wt_posix):]
            if not p.endswith(exts):
                continue
            if root and not (p.startswith(root + "/") or p == root):
                continue
            files[p] = {
                "line_coverage_pct": fc.line_coverage_pct,
                "branch_coverage_pct": fc.branch_coverage_pct,
                "covered_lines": fc.covered_lines,
                "total_coverable_lines": fc.total_coverable_lines,
            }
        if not files:
            print("    node: 0 files after source_root/extension filter")
            return None
        return files, {"tier": "c8", "source_commit": sha, "source_date": str(_T0),
                       "skew_days_from_t0": 0, "n_files": len(files)}
    except subprocess.TimeoutExpired:
        print("    node: timed out")
        return None
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=repo_dir, capture_output=True)


# ------------------------------------------------------------------------- driver

def t0_sha(name: str) -> str:
    repo_dir = _resolve_repo_dir(name)
    r = subprocess.run(
        ["git", "rev-list", "-1", "--before=2025-11-23 23:59:59", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    return r.stdout.strip()


def collect_one(repo_cfg: dict, tier: str) -> None:
    name = repo_cfg["name"]
    print(f"  {name}: acquiring coverage (tier={tier})")
    acquired = None
    if tier in ("auto", "codecov"):
        acquired = acquire_codecov(repo_cfg)
    if acquired is None and tier in ("auto", "run"):
        lang = repo_cfg.get("language")
        sha = t0_sha(name)
        if sha and lang == "python":
            acquired = acquire_pytest(repo_cfg, sha)
        elif sha and lang in ("typescript", "javascript"):
            acquired = acquire_node(repo_cfg, sha)
    if acquired is None:
        print(f"    -> no coverage acquired (absent, not zero)")
        return
    files, meta = acquired
    out_dir = _RESULTS / f"health_defect_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = {"format": "repowise-coverage-v1",
                "commit_sha": meta.get("source_commit"), "files": files}
    (out_dir / "coverage_t0.json").write_text(json.dumps(artifact, indent=2))
    (out_dir / "coverage_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"    -> {meta['tier']}: {meta['n_files']} files, "
          f"source {meta.get('source_commit','')[:12]} ({meta.get('source_date')}), "
          f"skew {meta.get('skew_days_from_t0')}d  ✓")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="single repo by config name")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tier", choices=["auto", "codecov", "run"], default="auto")
    args = ap.parse_args()
    cfg = yaml.safe_load(_CONFIG.read_text())
    repos = cfg["repos"]
    if args.repo:
        repos = [r for r in repos if r["name"] == args.repo]
    elif not args.all:
        ap.error("pass --repo NAME or --all")
    for r in repos:
        collect_one(r, args.tier)


if __name__ == "__main__":
    main()
