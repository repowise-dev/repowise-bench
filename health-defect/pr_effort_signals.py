"""Queue-independent PR effort signals vs. health — does unhealthy code take
*more work* to fix (not just more wall-clock)?

The wall-clock PR merge-time proxy (``resolution_time.py``) failed to reproduce
CodeScene's −0.58 / +124% because GitHub merge-time is dominated by maintainer
review-queue latency, not by how hard the change was. This script tests two
GitHub-native signals that sidestep the queue confound, attributed to the source
files each fix PR changed and bucketed by Repowise (and CodeScene) health:

  * **commit_span_h** — first→last authored-commit time on the PR branch. Strips
    the post-ready review wait; closer to "in-development" time. (Single-commit
    PRs ⇒ span 0 — itself meaningful: a one-shot fix.)
  * **n_commits** — commits on the PR branch (a coarse iteration count).
  * **review_rounds** — number of submitted reviews (how many passes the change
    needed). ``changes_requested`` counts the rework-demanding subset.
  * **commits_after_first_review** — branch commits dated after the first review:
    direct rework volume.
  * **review_comment_density** — inline review comments per changed line
    (``review_comments / (additions + deletions)``): scrutiny per unit of change.

Iteration counts measure *how hard the change was to get right*, independent of
how long a maintainer took to look — the signal most likely to survive where
merge-time died.

For each signal we report the Alert:Healthy ratio (median, robust) and
Spearman(health, signal), each with a repo-cluster bootstrap 95% CI.

Auth: ``gh`` CLI. Per-PR fields cached under ``results/<repo>/pr_effort/<N>.json``
(3 API calls per PR, once). Cache-only re-runs are offline.

Usage::

    ../../.venv/Scripts/python.exe pr_effort_signals.py --repos pydantic hono axios
    ../../.venv/Scripts/python.exe pr_effort_signals.py            # full corpus
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from lib.defect_counter import _touched_source_files, find_fix_commits, resolve_t0_sha
from lib.filters import normalize_path
from lib.issue_links import gh_available, owner_repo_from_url, parse_issue_refs
from lib.stats import spearman_correlation
from statistical_rigor import load_repo

_BENCH = Path(__file__).resolve().parent
_RESULTS = _BENCH.parent / "results"
_REPOS_DIR = _BENCH.parent / "repos"

HEALTHY_MIN = 8.0
ALERT_MAX = 4.0

# Each signal: (key, higher_means_harder). All these are "higher = more effort",
# so Spearman with health (higher = healthier) is expected NEGATIVE if the
# hypothesis holds (unhealthy files cost more effort to fix).
_SIGNALS = [
    "commit_span_h",
    "n_commits",
    "review_rounds",
    "changes_requested",
    "commits_after_first_review",
    "review_comment_density",
]


def _bucket(score: float) -> str:
    if score >= HEALTHY_MIN:
        return "healthy"
    if score < ALERT_MAX:
        return "alert"
    return "warning"


def _resolve_repo_dir(name: str) -> Path:
    base = _REPOS_DIR / name
    nested = base / name
    if nested.exists() and (nested / ".git").exists():
        return nested
    return base


def _config_by_name() -> dict[str, dict]:
    cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
    return {r["name"]: r for r in cfg["repos"]}


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _gh_json(args: list[str]) -> Any | None:
    try:
        r = subprocess.run(
            ["gh", "api", *args], capture_output=True, text=True, encoding="utf-8"
        )
    except (FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        if "Not Found" in r.stderr or "404" in r.stderr:
            return {"_missing": True}
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def fetch_pr_effort(owner: str, repo: str, number: int, cache_dir: Path) -> dict | None:
    """Fetch+cache the effort signals for one PR. Returns the computed-fields
    dict, ``{"_missing": True}`` for a non-PR/404, or ``None`` on transport
    error (so a re-run can retry)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{number}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    meta = _gh_json([f"repos/{owner}/{repo}/pulls/{number}",
                     "--jq", "{commits,additions,deletions,changed_files,"
                             "review_comments,created_at,merged_at,"
                             "is_pr:true}"])
    if meta is None:
        return None
    if meta.get("_missing"):
        # number is an issue, not a PR — no PR-branch effort to measure.
        data = {"_missing": True, "number": number}
        cache_path.write_text(json.dumps(data))
        return data

    commits = _gh_json([f"repos/{owner}/{repo}/pulls/{number}/commits",
                        "--paginate", "--jq",
                        "[.[].commit.author.date]"]) or []
    reviews = _gh_json([f"repos/{owner}/{repo}/pulls/{number}/reviews",
                        "--paginate", "--jq",
                        "[.[]|{state,submitted_at}]"]) or []
    if isinstance(commits, dict):  # _missing/None guard
        commits = []
    if isinstance(reviews, dict):
        reviews = []

    cdates = sorted(d for d in (_ts(x) for x in commits) if d is not None)
    span_h = ((cdates[-1] - cdates[0]).total_seconds() / 3600.0
              if len(cdates) >= 2 else 0.0)
    review_times = sorted(
        t for t in (_ts(r.get("submitted_at")) for r in reviews) if t is not None
    )
    first_review = review_times[0] if review_times else None
    commits_after = (
        sum(1 for d in cdates if first_review and d > first_review)
        if first_review else 0
    )
    changed = (meta.get("additions", 0) or 0) + (meta.get("deletions", 0) or 0)
    n_changes_req = sum(1 for r in reviews if r.get("state") == "CHANGES_REQUESTED")

    data = {
        "number": number,
        "is_pr": True,
        "commit_span_h": span_h,
        "n_commits": meta.get("commits", len(cdates)) or len(cdates),
        "review_rounds": len(reviews),
        "changes_requested": n_changes_req,
        "commits_after_first_review": commits_after,
        "review_comment_density": (
            (meta.get("review_comments", 0) or 0) / changed if changed else 0.0
        ),
        "changed_lines": changed,
        "changed_files": meta.get("changed_files", 0),
    }
    cache_path.write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------------------
# Attribute PR signals to files; mean per file
# ---------------------------------------------------------------------------
def file_signals(
    repo_dir: str, t0_sha: str, cfg: dict, cache_dir: Path
) -> tuple[dict[str, dict[str, list[float]]], dict]:
    owner_repo = owner_repo_from_url(cfg.get("repo_url", ""))
    if owner_repo is None:
        return {}, {"available": False, "reason": "no github repo_url"}
    owner, repo = owner_repo
    source_root = cfg.get("source_root", "")
    extensions = tuple(cfg.get("extensions", [".py"]))
    fixes = find_fix_commits(
        repo_dir, t0_sha, "HEAD", strategy="keyword",
        include=cfg.get("bug_keywords"), exclude=cfg.get("exclude_keywords"),
    )
    # file -> signal -> [values]
    per_file: dict[str, dict[str, list[float]]] = {}
    n_linked = 0
    for sha, msg in fixes:
        refs = parse_issue_refs(msg)
        pr = None
        for number in refs:
            d = fetch_pr_effort(owner, repo, number, cache_dir)
            if d and not d.get("_missing") and d.get("is_pr"):
                pr = d
                break
        if pr is None:
            continue
        n_linked += 1
        files = _touched_source_files(repo_dir, sha, source_root, extensions)
        for f in files:
            fp = normalize_path(f)
            bucket = per_file.setdefault(fp, {s: [] for s in _SIGNALS})
            for s in _SIGNALS:
                bucket[s].append(float(pr[s]))
    meta = {
        "available": True,
        "n_fixes": len(fixes),
        "n_fixes_linked_to_pr": n_linked,
        "n_files": len(per_file),
    }
    return per_file, meta


def _join(rows: list[dict], per_file: dict[str, dict[str, list[float]]]) -> list[dict]:
    out = []
    for r in rows:
        sig = per_file.get(normalize_path(r["file_path"]))
        if not sig or not sig[_SIGNALS[0]]:
            continue
        row = {"file_path": r["file_path"], "health_score": r["health_score"]}
        for s in _SIGNALS:
            vals = sig[s]
            row[s] = sum(vals) / len(vals) if vals else 0.0
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Stats per signal
# ---------------------------------------------------------------------------
def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _bucket_stat(joined: list[dict], signal: str, stat: str) -> dict:
    cats: dict[str, list[float]] = {"healthy": [], "warning": [], "alert": []}
    for d in joined:
        cats[_bucket(d["health_score"])].append(d[signal])
    out = {}
    for k, vals in cats.items():
        if stat == "median":
            out[k] = {"files": len(vals), "v": _median(vals)}
        else:
            out[k] = {"files": len(vals),
                      "v": sum(vals) / len(vals) if vals else None}
    return out


def _ratio_fn(signal: str, stat: str):
    def fn(joined: list[dict]) -> float | None:
        b = _bucket_stat(joined, signal, stat)
        hi, lo = b["healthy"]["v"], b["alert"]["v"]
        if hi is None or lo is None or hi <= 0:
            return None
        return lo / hi
    return fn


def _spearman_fn(signal: str):
    def fn(joined: list[dict]) -> float | None:
        if len(joined) < 4:
            return None
        xs = [d["health_score"] for d in joined]
        ys = [d[signal] for d in joined]
        if len(set(ys)) < 2:
            return None
        return spearman_correlation(xs, ys)["rho"]
    return fn


def _cluster_ci(corpus, fn, *, n_boot, seed) -> dict:
    names = list(corpus)
    point = fn([r for n in names for r in corpus[n]])
    rng = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        chosen = [names[rng.randrange(len(names))] for _ in range(len(names))]
        pooled = [r for nm in chosen for r in corpus[nm]]
        v = fn(pooled)
        if v is not None and v == v:
            samples.append(v)
    samples.sort()

    def pct(q):
        if not samples:
            return None
        pos = q * (len(samples) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(samples) - 1)
        return samples[lo] * (hi - pos) + samples[hi] * (pos - lo)

    return {"point": point, "lo": pct(0.025), "hi": pct(0.975), "n_boot": len(samples)}


def run(repos: list[str], label: str, *, n_boot: int, seed: int) -> dict:
    cfg = _config_by_name()
    corpus: dict[str, list[dict]] = {}
    per_repo_meta: dict[str, dict] = {}
    for name in repos:
        rows = load_repo(name, label)
        if not rows or name not in cfg:
            continue
        repo_dir = _resolve_repo_dir(name)
        if not (repo_dir / ".git").exists():
            print(f"  {name}: not cloned — skipped")
            continue
        t0_sha = resolve_t0_sha(str(repo_dir), cfg[name]["t0_date"])
        cache_dir = _RESULTS / f"health_defect_{name}" / "pr_effort"
        per_file, meta = file_signals(str(repo_dir), t0_sha, cfg[name], cache_dir)
        joined = _join(rows, per_file)
        per_repo_meta[name] = {**meta, "n_files_joined": len(joined)}
        print(f"  {name:10} fixes={meta.get('n_fixes')} "
              f"linked_pr={meta.get('n_fixes_linked_to_pr')} files={len(joined)}")
        if joined:
            corpus[name] = joined

    if not corpus:
        return {"label": label, "repos": repos, "per_repo": per_repo_meta,
                "error": "no files with PR effort signals"}

    pooled = [r for n in corpus for r in corpus[n]]
    signals_out: dict[str, Any] = {}
    for s in _SIGNALS:
        signals_out[s] = {
            "buckets_median": _bucket_stat(pooled, s, "median"),
            "alert_healthy_ratio_median": _cluster_ci(
                corpus, _ratio_fn(s, "median"), n_boot=n_boot, seed=seed),
            "spearman_health_vs_signal": _cluster_ci(
                corpus, _spearman_fn(s), n_boot=n_boot, seed=seed),
        }
    return {
        "label": label,
        "n_boot": n_boot,
        "seed": seed,
        "repos": list(corpus),
        "per_repo": per_repo_meta,
        "n_files": len(pooled),
        "signals": signals_out,
        "note": "Spearman expected NEGATIVE if unhealthy (low score) files need "
                "more effort. Queue-independent: commit_span/iterations measure "
                "change difficulty, not maintainer latency.",
    }


def _fmt(ci: dict) -> str:
    if not ci or ci.get("point") is None:
        return "n/a"
    lo, hi = ci.get("lo"), ci.get("hi")
    s = f"{ci['point']:+.3f}"
    if lo is not None:
        s += f" [{lo:+.3f}, {hi:+.3f}]"
    return s


def print_summary(r: dict) -> None:
    print(f"\n{'='*78}")
    print(f"PR EFFORT SIGNALS vs HEALTH  ({r['label']} labels)")
    print(f"{'='*78}")
    if r.get("error"):
        print(f"  {r['error']}")
        return
    print(f"repos: {', '.join(r['repos'])}")
    print(f"files with PR effort signals: {r['n_files']}")
    print(f"\n{'signal':28} {'Alert:Healthy (med)':>22} {'Spearman(health,sig)':>24}")
    print("  " + "-" * 74)
    for s in _SIGNALS:
        blk = r["signals"][s]
        rr = blk["alert_healthy_ratio_median"]
        sp = blk["spearman_health_vs_signal"]
        rrs = (f"{rr['point']:.2f}x [{rr['lo']:.2f},{rr['hi']:.2f}]"
               if rr.get("point") is not None else "n/a")
        print(f"  {s:28} {rrs:>22} {_fmt(sp):>24}")
    print("\n  (Spearman NEGATIVE ⇒ unhealthy files need more effort; "
          "ratio >1 ⇒ Alert harder than Healthy)")
    print("  Median by bucket (commit_span_h / review_rounds / commits_after_review):")
    for s in ("commit_span_h", "review_rounds", "commits_after_first_review"):
        b = r["signals"][s]["buckets_median"]
        print(f"    {s:28} "
              + "  ".join(f"{k}={b[k]['v']}" for k in ("healthy", "warning", "alert")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=None)
    ap.add_argument("--label", default="keyword",
                    choices=["keyword", "szz", "szz_b", "issue"])
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--out", type=Path, default=_RESULTS / "pr_effort_signals.json")
    args = ap.parse_args()

    if not gh_available():
        raise SystemExit("gh CLI not authenticated — needed for PR effort data.")

    repos = args.repos
    if not repos:
        cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
        repos = [r["name"] for r in cfg["repos"]]

    rep = run(repos, args.label, n_boot=args.n_boot, seed=args.seed)
    print_summary(rep)
    args.out.write_text(json.dumps(rep, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
