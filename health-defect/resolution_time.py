"""Open-data replication of CodeScene's resolution-time business-impact claims.

CodeScene's strongest evidence in "Code Red" (Tornhill & Borg, TechDebt 2022) is
*business impact*, computed from Jira issue cycle times we do not have:

  * **+124%** mean issue-resolution time in low-quality (Alert) vs healthy code,
  * up to **9×** longer max cycle time,
  * Pearson **−0.58** between a file's Code Health and its mean resolution time.

This script reproduces the **124%** and **−0.58** analogs on OUR corpus using a
fully **open** resolution-time proxy: for every bug-fixing commit in the post-T0
window that references a GitHub issue (``fixes|closes #N``), the issue's
``closed_at − created_at`` is a real, public resolution time, attributed to the
source files that fixing commit changed. Per file we take the mean resolution
time, bucket files by Repowise health (and, when available, CodeScene health),
and report:

  * Alert-vs-Healthy **mean-resolution-time ratio** (their +124% ⇒ 2.24× analog),
  * **Pearson(health_score, mean_resolution_time)** (their −0.58 analog),

each with a repo-cluster bootstrap 95% CI.

**Proxy caveat (reported, not hidden).** GitHub ``closed_at − created_at`` is
wall-clock issue lifetime — it includes triage, discussion and review latency,
so it is an *upper bound* on, not identical to, Jira "time in development". It is
the honest open analog; Apache Jira status-transition cycle time would be the
heavier true-cycle-time replication (noted in the report, not run here).

Auth: reuses the authenticated ``gh`` CLI. Issue timestamps are cached per repo
under ``results/<repo>/issues_rt/<N>.json`` (a superset of the existing
``issues/`` cache — it adds ``closed_at``), so re-runs are offline.

Usage::

    ../../.venv/Scripts/python.exe resolution_time.py \
        --repos clap pydantic gin hono            # subset
    ../../.venv/Scripts/python.exe resolution_time.py   # full corpus
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
from lib.filters import normalize_path, should_include
from lib.issue_links import gh_available, owner_repo_from_url, parse_issue_refs
from lib.stats import _pearson, spearman_correlation
from statistical_rigor import load_repo

_BENCH = Path(__file__).resolve().parent
_RESULTS = _BENCH.parent / "results"
_REPOS_DIR = _BENCH.parent / "repos"

HEALTHY_MIN = 8.0
ALERT_MAX = 4.0


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


# ---------------------------------------------------------------------------
# Issue timestamps (created_at + closed_at) — cached, gh-backed
# ---------------------------------------------------------------------------
def fetch_issue_rt(owner: str, repo: str, number: int, cache_dir: Path) -> dict | None:
    """Fetch+cache an issue's resolution-time fields. Returns the JSON,
    ``{"_missing": True}`` for a 404, or ``None`` on a transport error."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{number}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/issues/{number}",
             "--jq", "{number,state,labels:[.labels[].name],"
                     "is_pr:(has(\"pull_request\")),created_at,closed_at}"],
            capture_output=True, text=True, encoding="utf-8",
        )
    except (FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        if "Not Found" in r.stderr or "404" in r.stderr:
            data = {"_missing": True, "number": number}
            cache_path.write_text(json.dumps(data))
            return data
        return None
    data = json.loads(r.stdout)
    cache_path.write_text(json.dumps(data))
    return data


def _hours_between(created: str, closed: str) -> float | None:
    try:
        c0 = datetime.fromisoformat(created.replace("Z", "+00:00"))
        c1 = datetime.fromisoformat(closed.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    h = (c1 - c0).total_seconds() / 3600.0
    return h if h >= 0 else None


def _resolution_hours(issue: dict, *, unit: str) -> float | None:
    """Resolution time (hours) for a referenced ticket.

    ``unit`` selects which referenced items count:
      * ``issue`` — real bug issues only (PRs excluded). The closest analog to
        CodeScene's Jira issue resolution time, but sparse in squash-merge OSS
        repos where fix commits reference their *PR* number, not an issue.
      * ``pr`` — pull requests only. The fix PR's open→merge lifetime: a
        populated proxy that excludes triage latency (closer to "time in
        development", but includes code-review latency).
      * ``any`` — either; the widest coverage.
    """
    if not issue or issue.get("_missing"):
        return None
    is_pr = bool(issue.get("is_pr"))
    if unit == "issue" and is_pr:
        return None
    if unit == "pr" and not is_pr:
        return None
    if issue.get("state") != "closed" or not issue.get("closed_at"):
        return None
    return _hours_between(issue.get("created_at"), issue.get("closed_at"))


# ---------------------------------------------------------------------------
# Per-file mean resolution time, attributed via fix-commit -> issue linkage
# ---------------------------------------------------------------------------
def file_resolution_times(
    repo_dir: str,
    t0_sha: str,
    cfg: dict,
    cache_dir: Path,
    *,
    unit: str,
    bug_only: bool,
) -> tuple[dict[str, list[float]], dict]:
    """Map ``file_path -> [resolution_hours, ...]`` over post-T0 bug fixes that
    reference a GitHub ticket (issue or PR — see ``unit``). ``bug_only`` (issue
    unit only) keeps just bug-labeled issues. The fix's resolution time is
    attributed to every source file that fix commit changed."""
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
    bug_subs = ("bug", "defect", "regression")

    per_file: dict[str, list[float]] = {}
    n_fix_with_ref = n_linked = n_pr = n_issue = 0
    ticket_hours: dict[int, float] = {}
    for sha, msg in fixes:
        refs = parse_issue_refs(msg)
        if not refs:
            continue
        n_fix_with_ref += 1
        hrs = None
        for number in refs:
            issue = fetch_issue_rt(owner, repo, number, cache_dir)
            if not issue or issue.get("_missing"):
                continue
            if bug_only and not issue.get("is_pr"):
                labels = [str(x).lower() for x in issue.get("labels", [])]
                if not any(any(s in lab for s in bug_subs) for lab in labels):
                    continue
            h = _resolution_hours(issue, unit=unit)
            if h is not None:
                hrs = h
                ticket_hours[number] = h
                if issue.get("is_pr"):
                    n_pr += 1
                else:
                    n_issue += 1
                break
        if hrs is None:
            continue
        n_linked += 1
        for f in _touched_source_files(repo_dir, sha, source_root, extensions):
            per_file.setdefault(normalize_path(f), []).append(hrs)
    meta = {
        "available": True,
        "unit": unit,
        "n_fixes": len(fixes),
        "n_fixes_with_ref": n_fix_with_ref,
        "n_fixes_linked_to_timed_ticket": n_linked,
        "n_distinct_tickets": len(ticket_hours),
        "n_pr_tickets": n_pr,
        "n_issue_tickets": n_issue,
        "bug_only": bug_only,
    }
    return per_file, meta


# ---------------------------------------------------------------------------
# Join to health, bucket, and compute the two CodeScene analogs
# ---------------------------------------------------------------------------
def _join(
    rows: list[dict], per_file: dict[str, list[float]]
) -> list[dict]:
    """Attach mean resolution time to each scored file that has >=1 timed fix."""
    out = []
    for r in rows:
        times = per_file.get(normalize_path(r["file_path"]))
        if not times:
            continue
        out.append({
            "file_path": r["file_path"],
            "health_score": r["health_score"],
            "nloc": r.get("nloc", 0),
            "mean_resolution_h": sum(times) / len(times),
            "n_fixes_timed": len(times),
        })
    return out


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _bucket_means(joined: list[dict]) -> dict:
    cats: dict[str, list[float]] = {"healthy": [], "warning": [], "alert": []}
    for d in joined:
        cats[_bucket(d["health_score"])].append(d["mean_resolution_h"])
    out = {}
    for name, vals in cats.items():
        out[name] = {
            "files": len(vals),
            "mean_resolution_h": sum(vals) / len(vals) if vals else None,
            "median_resolution_h": _median(vals),
        }
    return out


def _alert_healthy_ratio(joined: list[dict]) -> float | None:
    """Mean-based Alert:Healthy ratio — directly analogous to CodeScene's mean
    resolution-time comparison, but sensitive to the heavy right tail of OSS
    PR lifetimes (a few stale PRs dominate)."""
    b = _bucket_means(joined)
    hi = b["healthy"]["mean_resolution_h"]
    lo = b["alert"]["mean_resolution_h"]
    if not hi or not lo:
        return None
    return lo / hi


def _alert_healthy_ratio_median(joined: list[dict]) -> float | None:
    """Median-based Alert:Healthy ratio — robust to the long-open-PR tail, the
    honest headline for a skewed proxy."""
    b = _bucket_means(joined)
    hi = b["healthy"]["median_resolution_h"]
    lo = b["alert"]["median_resolution_h"]
    if not hi or not lo:
        return None
    return lo / hi


def _pearson_health_rt(joined: list[dict]) -> float | None:
    """Pearson(health, mean resolution time) — the direct −0.58 analog. Linear,
    so the resolution-time skew weakens it; reported alongside the robust
    Spearman below."""
    if len(joined) < 4:
        return None
    return _pearson(
        [d["health_score"] for d in joined],
        [d["mean_resolution_h"] for d in joined],
    )


def _spearman_health_rt(joined: list[dict]) -> float | None:
    """Spearman(health, mean resolution time) — rank correlation, robust to the
    heavy-tailed resolution-time distribution (the defensible analog to their
    Pearson on a less-skewed proprietary cycle-time)."""
    if len(joined) < 4:
        return None
    return spearman_correlation(
        [d["health_score"] for d in joined],
        [d["mean_resolution_h"] for d in joined],
    )["rho"]


def _cluster_ci(
    corpus: dict[str, list[dict]], fn, *, n_boot: int, seed: int
) -> dict:
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


def run(repos: list[str], label: str, *, unit: str, bug_only: bool,
        n_boot: int, seed: int) -> dict:
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
        cache_dir = _RESULTS / f"health_defect_{name}" / "issues_rt"
        per_file, meta = file_resolution_times(
            str(repo_dir), t0_sha, cfg[name], cache_dir,
            unit=unit, bug_only=bug_only,
        )
        joined = _join(rows, per_file)
        per_repo_meta[name] = {**meta, "n_files_with_resolution_time": len(joined)}
        print(f"  {name:10} fixes={meta.get('n_fixes')} "
              f"linked={meta.get('n_fixes_linked_to_timed_ticket')} "
              f"(pr={meta.get('n_pr_tickets')} issue={meta.get('n_issue_tickets')}) "
              f"files_timed={len(joined)}")
        if joined:
            corpus[name] = joined

    if not corpus:
        return {
            "label": label, "bug_only": bug_only, "repos": repos,
            "per_repo": per_repo_meta, "error": "no files with open resolution time",
        }

    pooled = [r for n in corpus for r in corpus[n]]
    return {
        "label": label,
        "unit": unit,
        "bug_only": bug_only,
        "n_boot": n_boot,
        "seed": seed,
        "repos": list(corpus),
        "per_repo": per_repo_meta,
        "n_files_with_resolution_time": len(pooled),
        "bucket_means_hours": _bucket_means(pooled),
        "alert_vs_healthy_ratio": _cluster_ci(
            corpus, _alert_healthy_ratio, n_boot=n_boot, seed=seed
        ),
        "alert_vs_healthy_ratio_median": _cluster_ci(
            corpus, _alert_healthy_ratio_median, n_boot=n_boot, seed=seed
        ),
        "pearson_health_vs_resolution_time": _cluster_ci(
            corpus, _pearson_health_rt, n_boot=n_boot, seed=seed
        ),
        "spearman_health_vs_resolution_time": _cluster_ci(
            corpus, _spearman_health_rt, n_boot=n_boot, seed=seed
        ),
        "codescene_reference": {
            "alert_vs_healthy_resolution_time_pct": 124,
            "alert_vs_healthy_ratio": 2.24,
            "pearson_health_vs_resolution_time": -0.58,
            "note": "Jira cycle time, 39 proprietary repos; ours is GitHub "
                    "closed_at-created_at (wall-clock proxy, upper bound).",
        },
    }


def _fmt(ci: dict, unit: str = "") -> str:
    if not ci or ci.get("point") is None:
        return "n/a"
    lo, hi = ci.get("lo"), ci.get("hi")
    s = f"{ci['point']:.2f}{unit}"
    if lo is not None:
        s += f" [{lo:.2f}, {hi:.2f}]"
    return s


def print_summary(r: dict) -> None:
    print(f"\n{'='*72}")
    print(f"RESOLUTION-TIME REPLICATION  ({r['label']} labels, "
          f"bug_only={r['bug_only']})")
    print(f"{'='*72}")
    if r.get("error"):
        print(f"  {r['error']}")
        return
    print(f"unit: {r['unit']}   repos: {', '.join(r['repos'])}")
    print(f"files with open resolution time: {r['n_files_with_resolution_time']}")
    b = r["bucket_means_hours"]
    print("\nResolution time by health bucket (mean / median hours):")
    for k in ("healthy", "warning", "alert"):
        m = b[k]["mean_resolution_h"]
        md = b[k]["median_resolution_h"]
        print(f"  {k:9} {b[k]['files']:>4} files   "
              f"mean {(f'{m:.1f}h ({m/24:.1f}d)' if m else 'n/a'):>18}   "
              f"median {(f'{md:.1f}h ({md/24:.1f}d)' if md else 'n/a')}")
    print("\nAlert : Healthy resolution-time ratio (their +124% ⇒ 2.24x):")
    print(f"  mean-based   {_fmt(r['alert_vs_healthy_ratio'], 'x')}")
    print(f"  median-based {_fmt(r['alert_vs_healthy_ratio_median'], 'x')}  (robust)")
    print("\nHealth vs resolution time correlation (their Pearson -0.58):")
    print(f"  Pearson  {_fmt(r['pearson_health_vs_resolution_time'])}")
    print(f"  Spearman {_fmt(r['spearman_health_vs_resolution_time'])}  (robust to skew)")
    print(f"\n[proxy: GitHub PR open->merge time (unit={r['unit']}) — wall-clock, "
          "includes review latency; an upper bound on in-development cycle time]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=None)
    ap.add_argument("--label", default="keyword",
                    choices=["keyword", "szz", "szz_b", "issue"])
    ap.add_argument("--unit", default="pr", choices=["issue", "pr", "any"],
                    help="resolution-time unit: bug issue, fix PR, or either. "
                         "Squash-merge OSS repos reference PRs, so 'pr' (fix "
                         "time-to-merge) is the populated default; 'issue' is "
                         "the closest Jira analog but sparse.")
    ap.add_argument("--all-issues", action="store_true",
                    help="(issue unit) count any referenced issue, not only bug-labeled")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--out", type=Path, default=_RESULTS / "resolution_time.json")
    args = ap.parse_args()

    if not gh_available():
        raise SystemExit("gh CLI not authenticated — needed for issue timestamps.")

    repos = args.repos
    if not repos:
        cfg = yaml.safe_load((_BENCH / "config.yaml").read_text())
        repos = [r["name"] for r in cfg["repos"]]

    rep = run(repos, args.label, unit=args.unit, bug_only=not args.all_issues,
              n_boot=args.n_boot, seed=args.seed)
    print_summary(rep)
    args.out.write_text(json.dumps(rep, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
