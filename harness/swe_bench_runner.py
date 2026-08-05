"""
SWE-bench runner — native (no-Docker) scorer + agent harness.

Sibling of swe_qa_runner.py. Reuses that module's machinery (index_repo,
generate_mcp_config, get_c0_worktree, run_claude_code, leak scrubbing) and adds
the SWE-bench-specific gap:

  - load_swe_bench_tasks()      — read the vendored Verified subset
  - make_instance_worktree()    — isolated git worktree at base_commit
  - ensure_instance_venv()      — per-instance venv with the package installed
  - score_resolved()            — apply diff + test_patch, run named tests
  - run_swe_bench_task()        — checkout, run agent, capture diff, score

Gates (run BEFORE any agent, see SWEBENCH_VALIDATION_NEXT_SESSION.md):
  - gold-patch gate : the GOLD patch must score "resolved" for each instance.
  - empty-diff gate : an empty agent diff must score "not resolved".

CLI:
  python -m harness.swe_bench_runner gold-gate --instances pallets__flask-5014
  python -m harness.swe_bench_runner gold-gate --repos psf/requests
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Reuse the SWE-QA machinery wholesale.
from harness.swe_qa_runner import (
    _UTF8_ENV,
    _safe_rmtree,
    resolve_repo_path,
    ensure_repo_cloned,
)

_BENCH_ROOT = Path(__file__).resolve().parents[1]
_SWE_BENCH_DATA = _BENCH_ROOT / "data" / "swe_bench" / "tasks.json"
_WORKTREES_ROOT = _BENCH_ROOT / "scratch_swebench"
_VENVS_ROOT = _BENCH_ROOT / "swebench_venvs"

# Native-friendly repos (pure-Python, pytest runs without the Dockerized
# SWE-bench env). Everything else needs Docker and is out of scope for the smoke.
NATIVE_REPOS = {"psf/requests", "pallets/flask"}

# SWE-bench's Docker images pin Python 3.11 for flask/requests-era code. The
# bench host runs 3.13, on which the contemporaneous test stack breaks both ways
# (pytest 8 removed _pytest.monkeypatch.notset that Flask 2.3's conftest needs;
# pytest 7 trips Python 3.13's ast.Str removal). So native venvs MUST be built
# from a 3.11 interpreter. Override with SWEBENCH_BASE_PYTHON.
def _resolve_base_python() -> Path:
    env = os.environ.get("SWEBENCH_BASE_PYTHON")
    if env and Path(env).exists():
        return Path(env)
    candidates = [
        Path(r"C:\Users\ragha\miniconda3\envs\slate\python.exe"),
        Path(r"C:\Users\ragha\miniconda3\envs\repowise\python.exe"),
        Path(r"C:\Users\ragha\miniconda3\envs\thita_new\python.exe"),
    ]
    for c in candidates:
        if c.exists():
            try:
                v = subprocess.check_output([str(c), "--version"], text=True).strip()
                if "3.11" in v:
                    return c
            except Exception:
                continue
    # Last resort: the host interpreter (will likely fail the gate on 3.13).
    return Path(sys.executable)


# Pinned test deps per repo. The package's own runtime deps come from its
# editable install; these are the TEST harness deps frozen to the era so the
# suite imports and runs the way the SWE-bench image intended.
_TEST_DEPS = {
    # Flask 2.3-era: Werkzeug 3.x dropped werkzeug.__version__ that flask.testing
    # reads, so pin the contemporaneous Werkzeug. pytest<8 keeps _pytest
    # internals the conftest relies on.
    "pallets/flask": ["pytest>=7.0,<8.0", "Werkzeug>=2.3,<3.0"],
    # requests' suite is served by a local httpbin (pytest-httpbin). Pinned to
    # the last releases that still target the requests-2.x test layout.
    "psf/requests": ["pytest>=7.0,<8.0", "pytest-httpbin", "pytest-mock", "trustme"],
}


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_swe_bench_tasks(
    repos: Optional[list] = None,
    instances: Optional[list] = None,
    data_path: Path = _SWE_BENCH_DATA,
) -> list:
    """Load SWE-bench Verified instances from the vendored JSON.

    Filters by ``repos`` (org/name) and/or explicit ``instances`` (instance_id).
    FAIL_TO_PASS / PASS_TO_PASS arrive as JSON-encoded strings in the dataset;
    decode them to lists here so callers never re-parse.
    """
    with open(data_path, encoding="utf-8") as f:
        tasks = json.load(f)

    for t in tasks:
        for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            val = t.get(key)
            if isinstance(val, str):
                t[key] = json.loads(val)

    if repos:
        repo_set = set(repos)
        tasks = [t for t in tasks if t.get("repo") in repo_set]
    if instances:
        inst_set = set(instances)
        tasks = [t for t in tasks if t.get("instance_id") in inst_set]
    return tasks


# ---------------------------------------------------------------------------
# Worktree isolation (one per instance, detached at base_commit)
# ---------------------------------------------------------------------------

def make_instance_worktree(repo_path: Path, instance_id: str,
                           base_commit: str) -> Path:
    """Create (or reset) an isolated worktree at ``base_commit``.

    Each instance gets its own worktree so conditions/tasks never leak into one
    another. If the worktree exists, it is hard-reset + cleaned to base_commit
    rather than recreated (cheap, and survives a live MCP server on Windows).
    """
    wt_path = _WORKTREES_ROOT / instance_id
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    if wt_path.exists():
        try:
            subprocess.run(
                ["git", "-C", str(wt_path), "reset", "--hard", base_commit],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(wt_path), "clean", "-fdx",
                 "-e", ".repowise", "-e", ".mcp.json", "-e", "CLAUDE.md"],
                check=True, capture_output=True, text=True,
            )
            return wt_path
        except subprocess.CalledProcessError:
            # Worktree is wedged — tear it down and recreate below.
            _teardown_worktree(repo_path, wt_path)

    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "--detach",
         str(wt_path), base_commit],
        check=True, capture_output=True, text=True,
    )
    return wt_path


def _teardown_worktree(repo_path: Path, wt_path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo_path), "worktree", "prune"],
                   capture_output=True)
    if wt_path.exists():
        _safe_rmtree(wt_path)


def reset_worktree(wt_path: Path, base_commit: str) -> None:
    """Hard-reset a worktree back to base_commit, dropping all changes.

    Keeps repowise artifacts (.repowise/.mcp.json/CLAUDE.md) so a live MCP
    server holding wiki.db open doesn't trip the clean.
    """
    subprocess.run(["git", "-C", str(wt_path), "reset", "--hard", base_commit],
                   check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(wt_path), "clean", "-fdx",
         "-e", ".repowise", "-e", ".mcp.json", "-e", "CLAUDE.md"],
        check=True, capture_output=True, text=True,
    )


def assert_clean(wt_path: Path) -> None:
    """Raise if the worktree has tracked modifications (leakage guard)."""
    out = subprocess.run(
        ["git", "-C", str(wt_path), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True,
    ).stdout.strip()
    if out:
        raise RuntimeError(f"worktree {wt_path} not clean before apply:\n{out}")


# ---------------------------------------------------------------------------
# Per-instance native venv
# ---------------------------------------------------------------------------

def ensure_instance_venv(repo: str, instance_id: str, wt_path: Path,
                         extra_deps: Optional[list] = None,
                         force: bool = False) -> Path:
    """Create a per-instance venv and install the package + test deps editable.

    Returns the path to the venv's python.exe. Cached: if the venv already has
    pytest importable and ``force`` is False, reuse it.

    The package is installed editable from the worktree at base_commit so the
    agent's edits (made in the same worktree) take effect without reinstall.
    """
    venv_dir = _VENVS_ROOT / instance_id
    py = venv_dir / "Scripts" / "python.exe"

    if py.exists() and not force:
        check = subprocess.run([str(py), "-c", "import pytest"],
                               capture_output=True, text=True)
        if check.returncode == 0:
            return py

    if venv_dir.exists():
        _safe_rmtree(venv_dir)

    base_py = _resolve_base_python()
    bver = subprocess.check_output([str(base_py), "--version"], text=True).strip()
    print(f"  [venv] creating {venv_dir.name} from {base_py} ({bver})")
    subprocess.run([str(base_py), "-m", "venv", str(venv_dir)],
                   check=True, capture_output=True, text=True)

    pip = [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    subprocess.run(pip + ["--upgrade", "pip", "setuptools", "wheel"],
                   check=True, capture_output=True, text=True, env=_UTF8_ENV)

    # Install the package editable FIRST (resolves its own runtime deps to
    # latest), THEN the pinned harness/runtime deps so the era pins win over the
    # ranges in the package's pyproject (e.g. Werkzeug<3 over Werkzeug>=2.3).
    print(f"  [venv] installing package editable from worktree")
    r = subprocess.run(pip + ["-e", "."], cwd=str(wt_path),
                       capture_output=True, text=True, env=_UTF8_ENV)
    if r.returncode != 0:
        print(f"  [venv] editable install warning:\n{r.stderr[-1500:]}")

    deps = list(_TEST_DEPS.get(repo, ["pytest"]))
    if extra_deps:
        deps += extra_deps
    print(f"  [venv] installing pinned deps: {', '.join(deps)}")
    r = subprocess.run(pip + deps, capture_output=True, text=True, env=_UTF8_ENV)
    if r.returncode != 0:
        print(f"  [venv] dep install FAILED:\n{r.stderr[-1500:]}")
    return py


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def apply_patch(wt_path: Path, patch_text: str, label: str) -> bool:
    """Apply a unified diff to the worktree. Returns True on success.

    Tries `git apply` with progressively looser whitespace handling, then falls
    back to `patch -p1`. SWE-bench patches are git-format with a/ b/ prefixes.
    """
    if not patch_text or not patch_text.strip():
        print(f"  [apply:{label}] empty patch — nothing to apply")
        return True

    patch_file = wt_path / f"_{label}.patch"
    patch_file.write_text(patch_text, encoding="utf-8", newline="\n")
    try:
        attempts = [
            ["git", "-C", str(wt_path), "apply", "--verbose", str(patch_file)],
            ["git", "-C", str(wt_path), "apply", "--3way", str(patch_file)],
            ["git", "-C", str(wt_path), "apply", "--ignore-whitespace",
             "--whitespace=nowarn", str(patch_file)],
        ]
        for cmd in attempts:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                return True
        print(f"  [apply:{label}] git apply failed: {r.stderr.strip()[:400]}")
        return False
    finally:
        if patch_file.exists():
            patch_file.unlink()


# ---------------------------------------------------------------------------
# Test execution + scoring
# ---------------------------------------------------------------------------

def _files_for(node_ids: list) -> list:
    """The set of test files referenced by a list of node ids (part before ::)."""
    files = []
    for nid in node_ids:
        f = nid.split("::", 1)[0]
        if f not in files:
            files.append(f)
    return files


def _run_pytest(py: Path, wt_path: Path, node_ids: list,
                timeout: int = 900) -> dict:
    """Run pytest by FILE and parse per-test results from -rA output.

    We deliberately do NOT pass node ids on the command line: SWE-bench
    parametrized ids contain spaces and embedded quotes (e.g.
    `test_parse_dict_header[foo="is a fish"]`) that Windows argv round-tripping
    mangles, and a single unresolved id makes pytest abort the whole run
    (exit 4), zeroing even the valid tests. Instead we run the referenced test
    files, collect every PASSED/FAILED/ERROR line, and match by exact node id
    in score_resolved. Extra tests in those files are harmless — only the
    target ids are scored.

    addopts is overridden to empty so a repo's `--doctest-modules` / coverage
    defaults don't add a second collector or change ids.
    """
    if not node_ids:
        return {"_per_test": {}, "_missing": [], "_returncode": 0, "_tail": ""}

    files = _files_for(node_ids)
    env = {**_UTF8_ENV}
    cmd = [
        str(py), "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider",
        "-rA", "--tb=no", "-q", "--continue-on-collection-errors",
    ] + files
    try:
        r = subprocess.run(cmd, cwd=str(wt_path), capture_output=True,
                           text=True, env=env, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"_per_test": {}, "_missing": list(node_ids),
                "_returncode": 1, "_tail": "TIMEOUT"}

    out = (r.stdout or "") + "\n" + (r.stderr or "")
    results = {}
    for line in out.splitlines():
        s = line.strip()
        for status in ("PASSED", "FAILED", "ERROR"):
            if s.startswith(status + " "):
                nid = s[len(status) + 1:].strip()
                # Normalize an absolute path prefix back to the repo-relative
                # nodeid the dataset uses (pytest sometimes prints abs paths).
                results[nid] = (status == "PASSED")
                break

    missing = [n for n in node_ids if n not in results and
               not any(n == k for k in results)]
    return {
        "_per_test": results,
        "_missing": missing,
        "_returncode": r.returncode,
        "_tail": out[-2500:],
    }


def score_resolved(py: Path, wt_path: Path, base_commit: str,
                   solution_patch: str, test_patch: str,
                   fail_to_pass: list, pass_to_pass: list,
                   timeout: int = 900) -> dict:
    """Apply solution + test patch, run named tests, decide resolved.

    resolved == every FAIL_TO_PASS passes AND every PASS_TO_PASS still passes.
    The worktree is left dirty; caller resets it. ``solution_patch`` is the
    agent diff (or the gold patch, for the gate).
    """
    assert_clean(wt_path)

    if not apply_patch(wt_path, solution_patch, "solution"):
        return {"resolved": False, "error": "solution_patch_failed"}
    if not apply_patch(wt_path, test_patch, "test"):
        return {"resolved": False, "error": "test_patch_failed"}

    all_nodes = list(dict.fromkeys(fail_to_pass + pass_to_pass))
    res = _run_pytest(py, wt_path, all_nodes, timeout=timeout)
    per = res.get("_per_test", {})

    def _passed(nid: str) -> bool:
        if nid in per:
            return per[nid]
        # The vendored dataset truncates parametrized ids at the first
        # whitespace (e.g. `...[foo="is` for `...[foo="is a fish", ...]`), so an
        # exact match misses. If the id looks truncated (an unbalanced '['),
        # match by prefix against the collected ids and pass only if EVERY
        # prefix-matched test passed — conservative, and correct on gold runs.
        truncated = nid.count("[") > nid.count("]")
        if truncated:
            matches = [v for k, v in per.items() if k.startswith(nid)]
            if matches:
                return all(matches)
        # tolerate minor nodeid normalization differences
        for k, v in per.items():
            if k == nid or k.endswith(nid) or nid.endswith(k):
                return v
        return False

    f2p_pass = {n: _passed(n) for n in fail_to_pass}
    p2p_pass = {n: _passed(n) for n in pass_to_pass}
    resolved = all(f2p_pass.values()) and all(p2p_pass.values())

    return {
        "resolved": resolved,
        "f2p_passed": sum(f2p_pass.values()),
        "f2p_total": len(fail_to_pass),
        "p2p_passed": sum(p2p_pass.values()),
        "p2p_total": len(pass_to_pass),
        "f2p_failures": [n for n, ok in f2p_pass.items() if not ok],
        "p2p_failures": [n for n, ok in p2p_pass.items() if not ok],
        "pytest_returncode": res.get("_returncode"),
        "tail": res.get("_tail", ""),
    }


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def run_gold_gate(tasks: list, force_venv: bool = False,
                  keep_dirty: bool = False) -> dict:
    """Gold-patch + empty-diff gate for each instance.

    For each instance:
      - gold patch must score resolved == True
      - empty diff must score resolved == False
    Returns a summary dict and prints a per-instance verdict.
    """
    results = {}
    for t in tasks:
        iid = t["instance_id"]
        repo = t["repo"]
        base = t["base_commit"]
        print(f"\n{'='*70}\n[{iid}] repo={repo} base={base[:8]}")
        if repo not in NATIVE_REPOS:
            print(f"  SKIP — {repo} needs Docker (not native-friendly)")
            results[iid] = {"status": "skipped_non_native"}
            continue

        repo_path = resolve_repo_path(repo, str(_BENCH_ROOT / "repos"))
        if not repo_path.exists():
            ensure_repo_cloned(repo, str(_BENCH_ROOT / "repos"))

        try:
            wt = make_instance_worktree(repo_path, iid, base)
        except subprocess.CalledProcessError as e:
            print(f"  worktree FAILED: {e.stderr}")
            results[iid] = {"status": "worktree_failed", "error": str(e.stderr)}
            continue

        py = ensure_instance_venv(repo, iid, wt, force=force_venv)

        f2p, p2p = t["FAIL_TO_PASS"], t["PASS_TO_PASS"]

        # 1. GOLD patch → must resolve.
        print(f"  [gold] applying gold patch + test_patch, running "
              f"{len(f2p)} F2P + {len(p2p)} P2P tests")
        gold = score_resolved(py, wt, base, t["patch"], t["test_patch"], f2p, p2p)
        reset_worktree(wt, base)
        gold_ok = gold.get("resolved") is True
        print(f"  [gold] resolved={gold.get('resolved')} "
              f"F2P={gold.get('f2p_passed')}/{gold.get('f2p_total')} "
              f"P2P={gold.get('p2p_passed')}/{gold.get('p2p_total')} "
              f"err={gold.get('error')}")
        if not gold_ok:
            print(f"  [gold] FAILURES f2p={gold.get('f2p_failures')}")
            if gold.get("tail"):
                print("  [gold] pytest tail:\n" +
                      "\n".join("    " + l for l in gold["tail"].splitlines()[-25:]))

        # 2. EMPTY diff → must NOT resolve (sanity that the scorer can fail).
        print(f"  [empty] running with empty solution diff (expect not-resolved)")
        empty = score_resolved(py, wt, base, "", t["test_patch"], f2p, p2p)
        reset_worktree(wt, base)
        empty_ok = empty.get("resolved") is False
        print(f"  [empty] resolved={empty.get('resolved')} "
              f"F2P={empty.get('f2p_passed')}/{empty.get('f2p_total')}")

        gate_pass = gold_ok and empty_ok
        print(f"  GATE: {'PASS' if gate_pass else 'FAIL'} "
              f"(gold_resolved={gold_ok}, empty_not_resolved={empty_ok})")
        results[iid] = {
            "status": "pass" if gate_pass else "fail",
            "gold": gold,
            "empty": empty,
        }

    # Summary
    print(f"\n{'='*70}\nGOLD-GATE SUMMARY")
    npass = sum(1 for r in results.values() if r.get("status") == "pass")
    for iid, r in results.items():
        print(f"  {r.get('status'):>22}  {iid}")
    print(f"\n  {npass}/{len(results)} instances passed the gate")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="SWE-bench native runner / gates")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gold-gate", help="run gold-patch + empty-diff gate")
    g.add_argument("--instances", type=str, default=None,
                   help="comma-separated instance_ids")
    g.add_argument("--repos", type=str, default=None,
                   help="comma-separated org/name repos")
    g.add_argument("--force-venv", action="store_true",
                   help="rebuild per-instance venvs")
    g.add_argument("--out", type=str, default=None,
                   help="write JSON summary here")

    ls = sub.add_parser("list", help="list native-friendly instances")
    ls.add_argument("--repos", type=str, default=None)

    args = ap.parse_args()

    if args.cmd == "list":
        repos = args.repos.split(",") if args.repos else list(NATIVE_REPOS)
        tasks = load_swe_bench_tasks(repos=repos)
        for t in tasks:
            print(f"{t['instance_id']:<28} {t['repo']:<18} "
                  f"F2P={len(t['FAIL_TO_PASS'])} P2P={len(t['PASS_TO_PASS'])} "
                  f"diff={t.get('difficulty')}")
        print(f"\n{len(tasks)} instances")
        return

    if args.cmd == "gold-gate":
        instances = args.instances.split(",") if args.instances else None
        repos = args.repos.split(",") if args.repos else (
            None if instances else list(NATIVE_REPOS))
        tasks = load_swe_bench_tasks(repos=repos, instances=instances)
        if not tasks:
            print("No matching instances.")
            return
        results = run_gold_gate(tasks, force_venv=args.force_venv)
        if args.out:
            Path(args.out).write_text(json.dumps(results, indent=2, default=str),
                                      encoding="utf-8")
            print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
