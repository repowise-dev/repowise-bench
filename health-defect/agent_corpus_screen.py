#!/usr/bin/env python3
"""agent_corpus_screen.py — corpus-gate screen for the agent-era corpus.

Per repo (clone under --repos-dir), measures the pre-registered gate inputs:

  * commit-level fix share in the window (keyword classifier, non-merge)
  * file-level projected positive rate: files at T0 touched by a window fix /
    source files at T0 (the openclaw saturation number was this metric: 68%)
  * single-author dominance (top author share of window commits — firehose
    indicator)
  * agent share of window commits by autonomy tier (from the provenance walk)

Gate (locked before measurement): file-level positive rate in 5–25%;
>=~40% -> saturation exhibit; <3% or <30 positives -> label-starved.

T0 = last commit before the window start (falls back to the root commit for
born-in-window repos; flagged). NLOC at T0 is counted from a detached worktree
checkout so a blobless clone fetches the needed blobs in one pack instead of
lazy-fetching per file.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_corpus_screen.py \
        --repos-dir <data>/agent-repos --provenance-dir <data>/agent-repos/_provenance \
        --out-dir <data>/agent-repos/_screen [--only name1,name2]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.defect_counter import _DEFAULT_EXCLUDE, _DEFAULT_INCLUDE  # noqa: E402

WINDOW_START = "2025-06-01"
MIN_NLOC = 10

_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
              ".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".rb",
              ".php", ".scala", ".swift", ".m", ".mm", ".clj", ".cljc", ".cljs",
              ".dart", ".svelte", ".vue", ".ex", ".exs"}


def _git(args: list[str], cwd: Path) -> str:
    # core.longpaths: several corpus repos exceed the Windows 260-char limit
    r = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}... failed: {r.stderr[-300:]}")
    return r.stdout


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return "." + base.rsplit(".", 1)[1].lower() if "." in base else ""


def resolve_t0(repo: Path) -> tuple[str, bool]:
    sha = _git(["log", "--format=%H", f"--before={WINDOW_START}T00:00:00", "-1"],
               repo).strip()
    if sha:
        return sha, False
    root = _git(["rev-list", "--max-parents=0", "HEAD"], repo).split()[0]
    return root, True


def nloc_via_worktree(repo: Path, sha: str, exts: set[str]) -> dict[str, int]:
    """{path: nloc} at sha via a detached worktree (one pack fetch on blobless)."""
    tmp = repo.parent / f"_wt_{repo.name}"
    _git(["worktree", "add", "--detach", "--force", str(tmp), sha], repo)
    try:
        files = _git(["ls-tree", "-r", "--name-only", sha], repo).splitlines()
        nloc: dict[str, int] = {}
        for rel in files:
            if _ext(rel) not in exts:
                continue
            p = tmp / rel
            try:
                with open(p, "rb") as fh:
                    nloc[rel] = sum(1 for ln in fh if ln.strip())
            except OSError:
                continue
        return nloc
    finally:
        _git(["worktree", "remove", "--force", str(tmp)], repo)


def window_stats(repo: Path, t0_sha: str, exts: set[str]) -> dict:
    """One log pass over (t0, HEAD]: fix commits, fix-touched files, authors."""
    out = _git(["log", f"{t0_sha}..HEAD", "--no-merges", "--name-only",
                "--format=%x01%H%x00%ae%x00%s"], repo)
    inc, exc = _DEFAULT_INCLUDE, _DEFAULT_EXCLUDE
    n_commits = n_fix = 0
    authors: Counter = Counter()
    fix_files: dict[str, int] = defaultdict(int)
    is_fix = False
    for line in out.split("\n"):
        if line.startswith("\x01"):
            _, email, subj = line[1:].split("\x00", 2)
            n_commits += 1
            authors[email.lower()] += 1
            is_fix = (not any(p.search(subj) for p in exc)) and \
                any(p.search(subj) for p in inc)
            if is_fix:
                n_fix += 1
        elif line.strip() and is_fix and _ext(line.strip()) in exts:
            fix_files[line.strip()] += 1
    top_author, top_n = (authors.most_common(1) or [("", 0)])[0]
    return {"n_commits": n_commits, "n_fix": n_fix,
            "commit_fix_share": round(n_fix / max(n_commits, 1), 4),
            "n_authors": len(authors), "top_author": top_author,
            "top_author_share": round(top_n / max(n_commits, 1), 4),
            "fix_files": dict(fix_files)}


def agent_stats(prov_path: Path) -> dict | None:
    if not prov_path.exists():
        return None
    data = json.loads(prov_path.read_text(encoding="utf-8"))
    n = a = t1 = t2 = t3 = 0
    by_agent: Counter = Counter()
    for r in data["rows"]:
        if (r.get("date") or "") < WINDOW_START or r.get("is_merge"):
            continue
        n += 1
        if r["agent"]:
            a += 1
            by_agent[r["agent"]] += 1
            if r["autonomy_tier"] == 1:
                t1 += 1
            elif r["autonomy_tier"] == 2:
                t2 += 1
            else:
                t3 += 1
    return {"window_commits": n, "agent_commits": a,
            "agent_share": round(a / max(n, 1), 4),
            "t1": t1, "t2": t2, "t3": t3, "by_agent": dict(by_agent)}


def gate_verdict(file_rate: float, n_pos: int) -> str:
    if file_rate >= 0.40:
        return "SATURATION"
    if file_rate < 0.03 or n_pos < 30:
        return "LABEL_STARVED"
    if 0.05 <= file_rate <= 0.25:
        return "PASS"
    return "BORDERLINE"


def screen_repo(rec: dict, repos_dir: Path, prov_dir: Path) -> dict:
    name = rec["dir"]
    repo = repos_dir / name
    t0_sha, born_in_window = resolve_t0(repo)
    t_start = time.time()
    ws = window_stats(repo, t0_sha, _CODE_EXTS)
    nloc = {p: n for p, n in nloc_via_worktree(repo, t0_sha, _CODE_EXTS).items()
            if n >= MIN_NLOC}
    files = set(nloc)
    pos = {p for p in ws["fix_files"] if p in files}
    file_rate = len(pos) / max(len(files), 1)
    out = {"repo": rec["repo"], "cohort": rec["cohort"], "t0_sha": t0_sha[:12],
           "born_in_window": born_in_window,
           "files_t0": len(files), "file_positives": len(pos),
           "file_positive_rate": round(file_rate, 4),
           "n_commits": ws["n_commits"], "n_fix": ws["n_fix"],
           "commit_fix_share": ws["commit_fix_share"],
           "n_authors": ws["n_authors"],
           "top_author_share": ws["top_author_share"],
           "top_author": ws["top_author"],
           "agent": agent_stats(prov_dir / f"{name}.json"),
           "gate": gate_verdict(file_rate, len(pos)),
           "screen_seconds": round(time.time() - t_start, 1)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", type=Path, required=True)
    ap.add_argument("--provenance-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    report = json.loads((args.repos_dir / "clone_report.json").read_text(encoding="utf-8"))
    repos = [r for r in report["repos"] if r["status"] != "FAILED"]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        repos = [r for r in repos if r["dir"] in keep]

    out_path = args.out_dir / "screen_report.json"
    results: dict[str, dict] = {}
    if out_path.exists():
        results = {r["repo"]: r for r in
                   json.loads(out_path.read_text(encoding="utf-8"))["repos"]}
    for rec in repos:
        if rec["repo"] in results and not args.only:
            log(f"{rec['dir']}: already screened, skipping")
            continue
        log(f"screening {rec['dir']} ...")
        try:
            res = screen_repo(rec, args.repos_dir, args.provenance_dir)
        except Exception as e:  # noqa: BLE001 — record and continue
            res = {"repo": rec["repo"], "cohort": rec["cohort"], "error": str(e)[:300]}
        results[rec["repo"]] = res
        log(f"  {rec['dir']}: gate={res.get('gate')} file_rate={res.get('file_positive_rate')} "
            f"fix_share={res.get('commit_fix_share')} top_author={res.get('top_author_share')}")
        out_path.write_text(json.dumps(
            {"generated": datetime.now(timezone.utc).isoformat(),
             "window_start": WINDOW_START,
             "repos": sorted(results.values(), key=lambda r: r["repo"])},
            indent=2), encoding="utf-8")
    log("done")


if __name__ == "__main__":
    main()
