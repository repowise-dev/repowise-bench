#!/usr/bin/env python3
"""agent_defect_labels.py — firehose-aware defect labeling for the agent corpus.

Per repo (window = 2025-06-01 → HEAD, the corpus-gate window), builds a
per-commit label record over the non-merge commit stream:

  * ``is_fix``        — benchmark keyword classifier (subject line; the exact
                        ``defect_counter`` patterns, so rates match the screen)
  * ``issue_gated``   — fix references an issue carrying a bug/defect/
                        regression label (bulk GraphQL fetch, cached)
  * ``is_revert`` / ``reverted_by`` — revert commits and their victims
  * ``self_fix``      — fix where ≥1 touched source file's previous toucher is
                        the same identity within SELF_FIX_HOURS (agent fix-spam
                        collapse; per-file detail kept for strict variants)
  * provenance        — {agent, autonomy_tier, channel, confidence} joined
                        from the Phase-1 walk

plus per-file attribution sets for raw vs gated file-level positive rates
(the labels-quality / saturation-rescue table).

Identity for self-fix: the agent name when the commit is agent-attributed,
else the author e-mail (lowercased) — an agent fixing another instance of
itself is still "self".

Run (venv python; gh authenticated for the issue fetch)::

    .venv/Scripts/python.exe health-defect/agent_defect_labels.py \
        --repos-dir <data>/agent-repos --provenance-dir <data>/agent-repos/_provenance \
        --out-dir <data>/agent-repos/_labels [--only name1,name2]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.defect_counter import _DEFAULT_EXCLUDE, _DEFAULT_INCLUDE  # noqa: E402
from lib.issue_links import parse_issue_refs  # noqa: E402

WINDOW_START = "2025-06-01"
SELF_FIX_HOURS = 48.0
BUG_LABEL_SUBSTRINGS = ("bug", "defect", "regression")

_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
              ".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".rb",
              ".php", ".scala", ".swift", ".m", ".mm", ".clj", ".cljc", ".cljs",
              ".dart", ".svelte", ".vue", ".ex", ".exs"}
_TEST_PAT = re.compile(r"(^|/)(tests?|specs?|__tests__|testing)(/|$)|"
                       r"(\.|_)(test|spec)s?\.[a-z]+$|Tests?\.[a-z]+$", re.IGNORECASE)
_REVERT_SHA_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return "." + base.rsplit(".", 1)[1].lower() if "." in base else ""


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}... failed: {r.stderr[-300:]}")
    return r.stdout


# -------------------------------------------------------------- commit walk --


def walk_window(repo: Path) -> list[dict]:
    """Chronological (oldest-first) non-merge commits in the window with
    touched files. One tree-level pass; offline on blobless clones."""
    out = _git(["log", f"--since={WINDOW_START}", "--no-merges", "--reverse",
                "--name-only", "--date=unix",
                "--format=%x01%H%x00%ae%x00%ad%x00%s"], repo)
    commits: list[dict] = []
    cur: dict | None = None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            sha, email, ts, subject = line[1:].split("\x00", 3)
            cur = {"sha": sha, "email": email.lower(), "ts": int(ts),
                   "subject": subject, "files": [], "test_files": 0,
                   "other_files": 0}
            commits.append(cur)
        elif line.strip() and cur is not None:
            f = line.strip()
            if _TEST_PAT.search(f):
                cur["test_files"] += 1
            if _ext(f) in _CODE_EXTS:
                cur["files"].append(f)
            else:
                cur["other_files"] += 1
    return commits


# --------------------------------------------------------- bulk issue fetch --


def fetch_issue_labels_bulk(owner: str, repo: str, numbers: list[int],
                            cache_path: Path, *, batch: int = 80) -> dict[int, dict]:
    """{number: {"is_pr": bool, "labels": [...]}} via aliased GraphQL
    issueOrPullRequest lookups (~80 per call). Cached incrementally."""
    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    todo = [n for n in sorted(set(numbers)) if str(n) not in cache]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        fields = "\n".join(
            f'i{n}: issueOrPullRequest(number: {n}) {{ __typename '
            f'... on Issue {{ labels(first: 20) {{ nodes {{ name }} }} }} '
            f'... on PullRequest {{ labels(first: 20) {{ nodes {{ name }} }} }} }}'
            for n in chunk)
        query = f'query {{ repository(owner: "{owner}", name: "{repo}") {{ {fields} }} }}'
        r = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                           capture_output=True, text=True, encoding="utf-8")
        time.sleep(0.4)
        if r.returncode != 0 and not r.stdout:
            log(f"    graphql batch failed ({r.stderr[:120]}); marking chunk unknown")
            for n in chunk:
                cache[str(n)] = {"unknown": True}
        else:
            data = json.loads(r.stdout).get("data") or {}
            repo_data = data.get("repository") or {}
            for n in chunk:
                node = repo_data.get(f"i{n}")
                if not node:
                    cache[str(n)] = {"missing": True}
                else:
                    cache[str(n)] = {
                        "is_pr": node.get("__typename") == "PullRequest",
                        "labels": [x["name"] for x in
                                   ((node.get("labels") or {}).get("nodes") or [])],
                    }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return {int(k): v for k, v in cache.items()}


def is_bug_ref(info: dict | None) -> bool:
    if not info or info.get("missing") or info.get("unknown") or info.get("is_pr"):
        return False
    labels = [str(x).lower() for x in info.get("labels", [])]
    return any(sub in lab for sub in BUG_LABEL_SUBSTRINGS for lab in labels)


# ------------------------------------------------------------------- labels --


_CLOSING_BODY_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#(\d+)", re.IGNORECASE)
_PR_SUBJ_RE = re.compile(r"\(#(\d+)\)\s*$")


def label_repo(rec: dict, repos_dir: Path, prov_dir: Path, issue_cache_dir: Path) -> dict:
    name, full = rec["dir"], rec["repo"]
    repo = repos_dir / name
    owner, rname = full.split("/")

    prov = {}
    prov_path = prov_dir / f"{name}.json"
    if prov_path.exists():
        for r in json.loads(prov_path.read_text(encoding="utf-8"))["rows"]:
            prov[r["sha"]] = r

    # Squash-merge subjects reference the PR, not the issue; the closing
    # "Fixes #N" usually lives in the PR body. Map pr_number -> closing issue
    # refs from the cached PR index so gating sees them.
    pr_issue_refs: dict[str, list[int]] = {}
    pr_cache = repos_dir / "_prcache" / f"{name}.json"
    if pr_cache.exists():
        for num, pr in json.loads(pr_cache.read_text(encoding="utf-8")).items():
            if not isinstance(pr, dict):
                continue
            refs = [int(m.group(1)) for m in
                    _CLOSING_BODY_RE.finditer(pr.get("body") or "")]
            if refs:
                pr_issue_refs[num] = refs

    commits = walk_window(repo)
    inc, exc = _DEFAULT_INCLUDE, _DEFAULT_EXCLUDE
    sha_index = {c["sha"]: c for c in commits}
    short_index: dict[str, str] = {}
    for c in commits:
        short_index[c["sha"][:7]] = c["sha"]

    # pass 1: classify fixes + reverts, collect issue refs (commit subject +
    # closing refs in the linked PR's body)
    issue_refs: set[int] = set()
    for c in commits:
        subj = c["subject"]
        c["is_fix"] = (not any(p.search(subj) for p in exc)) and \
            any(p.search(subj) for p in inc)
        c["is_revert"] = subj.startswith("Revert ") or "This reverts commit" in subj
        m = _PR_SUBJ_RE.search(subj)
        c["refs"] = parse_issue_refs(subj)
        if m and m.group(1) in pr_issue_refs:
            c["refs"] = list(dict.fromkeys(c["refs"] + pr_issue_refs[m.group(1)]))
        if c["is_fix"]:
            issue_refs.update(c["refs"])
        p = prov.get(c["sha"])
        c["agent"] = p["agent"] if p else None
        c["tier"] = p["autonomy_tier"] if p else None
        c["confidence"] = p["confidence"] if p else None

    # issue gating (bulk, cached)
    issues = fetch_issue_labels_bulk(owner, rname, sorted(issue_refs),
                                     issue_cache_dir / f"{name}.json") if issue_refs else {}
    for c in commits:
        c["issue_gated"] = c["is_fix"] and any(
            is_bug_ref(issues.get(n)) for n in c["refs"])

    # revert victims (full-message scan only for revert commits — cheap)
    reverted: set[str] = set()
    for c in commits:
        if not c["is_revert"]:
            continue
        body = _git(["log", "-1", "--format=%B", c["sha"]], repo)
        for m in _REVERT_SHA_RE.finditer(body):
            sha = m.group(1)
            hit = sha if sha in sha_index else short_index.get(sha[:7])
            if hit:
                reverted.add(hit)
    for c in commits:
        c["was_reverted"] = c["sha"] in reverted

    # self-fix: previous toucher of each file, chronological
    def identity(c: dict) -> str:
        return c["agent"] or c["email"]

    last_touch: dict[str, tuple[str, int]] = {}  # file -> (identity, ts)
    for c in commits:
        ident = identity(c)
        n_self = 0
        if c["is_fix"]:
            for f in c["files"]:
                prev = last_touch.get(f)
                if prev and prev[0] == ident and \
                        (c["ts"] - prev[1]) <= SELF_FIX_HOURS * 3600:
                    n_self += 1
        c["self_fix_files"] = n_self
        c["self_fix"] = c["is_fix"] and n_self > 0
        for f in c["files"]:
            last_touch[f] = (ident, c["ts"])

    # file-level attribution per strategy (for the saturation table)
    def file_rate(pred) -> tuple[int, int]:
        touched: set[str] = set()
        for c in commits:
            if pred(c):
                touched.update(c["files"])
        return len(touched), len({f for c in commits for f in c["files"]})

    raw_pos, universe = file_rate(lambda c: c["is_fix"])
    gated_pos, _ = file_rate(lambda c: c["issue_gated"])
    nospam_pos, _ = file_rate(lambda c: c["is_fix"] and not c["self_fix"]
                              and not c["was_reverted"] and not c["is_revert"])
    full_pos, _ = file_rate(lambda c: c["issue_gated"] and not c["self_fix"]
                            and not c["was_reverted"] and not c["is_revert"])

    n = len(commits)
    n_fix = sum(1 for c in commits if c["is_fix"])
    summary = {
        "repo": full, "cohort": rec["cohort"], "n_commits": n, "n_fix": n_fix,
        "n_issue_gated": sum(1 for c in commits if c["issue_gated"]),
        "n_self_fix": sum(1 for c in commits if c["self_fix"]),
        "n_reverts": sum(1 for c in commits if c["is_revert"]),
        "n_reverted": len(reverted),
        "n_issue_refs": len(issue_refs),
        "files_touched_universe": universe,
        "file_rate_raw": round(raw_pos / max(universe, 1), 4),
        "file_rate_issue_gated": round(gated_pos / max(universe, 1), 4),
        "file_rate_spam_collapsed": round(nospam_pos / max(universe, 1), 4),
        "file_rate_fully_gated": round(full_pos / max(universe, 1), 4),
    }
    return {"summary": summary, "self_fix_hours": SELF_FIX_HOURS,
            "window_start": WINDOW_START,
            "commits": [{k: c[k] for k in
                         ("sha", "email", "ts", "is_fix", "issue_gated",
                          "is_revert", "was_reverted", "self_fix",
                          "self_fix_files", "agent", "tier", "confidence",
                          "test_files", "other_files")} |
                        {"n_files": len(c["files"]), "files": c["files"]}
                        for c in commits]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", type=Path, required=True)
    ap.add_argument("--provenance-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    issue_cache = args.repos_dir / "_issuecache"

    report = json.loads((args.repos_dir / "clone_report.json").read_text(encoding="utf-8"))
    repos = [r for r in report["repos"] if r["status"] != "FAILED"]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        repos = [r for r in repos if r["dir"] in keep]

    for rec in repos:
        out_path = args.out_dir / f"{rec['dir']}.json"
        if out_path.exists() and not args.force:
            log(f"{rec['dir']}: exists, skipping")
            continue
        t0 = time.time()
        try:
            result = label_repo(rec, args.repos_dir, args.provenance_dir, issue_cache)
        except Exception as e:  # noqa: BLE001
            log(f"{rec['dir']}: ERROR {e}")
            continue
        s = result["summary"]
        log(f"{rec['dir']}: {s['n_commits']} commits, fix {s['n_fix']} "
            f"(gated {s['n_issue_gated']}, self-fix {s['n_self_fix']}, "
            f"reverts {s['n_reverts']}) file-rate raw {s['file_rate_raw']} → "
            f"fully-gated {s['file_rate_fully_gated']} ({time.time() - t0:.0f}s)")
        out_path.write_text(json.dumps(result), encoding="utf-8")
    log("done")


if __name__ == "__main__":
    main()
