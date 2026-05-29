"""GitHub issue-linkage labeling — the high-precision label subset.

Keyword fix-detection carries 20–40% noise (a `fix:` commit can be a refactor, a
test tweak, a perf change). Linking a fix to a closed GitHub issue *labeled a
bug* is near-ground-truth where issue hygiene is good. This module keeps only
the fix commits that reference a bug/defect/regression-labeled issue, producing
a high-precision fix subset that the report contrasts against keyword and SZZ.

Auth: reuses the already-authenticated ``gh`` CLI (no token minting / handling).
Responses are cached per issue under ``results/<repo>/issues/<N>.json`` so
re-runs are offline. If ``gh`` is unavailable (headless/cron), callers degrade
to keyword/SZZ-only — ``available`` in the returned meta says which happened.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Closing keywords give the strongest signal that #N is the bug this commit
# fixes (vs. a bare "(#1234)" PR-number suffix). We capture both but only the
# referenced *issues* (not PRs) labeled a bug confirm a fix.
_CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#(\d+)", re.IGNORECASE
)
_BARE_RE = re.compile(r"#(\d+)")

_DEFAULT_BUG_LABEL_SUBSTRINGS = ("bug", "defect", "regression")


def parse_issue_refs(msg: str) -> list[int]:
    """Issue/PR numbers referenced by a commit message (closing refs first)."""
    nums: list[int] = []
    seen: set[int] = set()
    for m in _CLOSING_RE.finditer(msg):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            nums.append(n)
    for m in _BARE_RE.finditer(msg):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            nums.append(n)
    return nums


def gh_available() -> bool:
    try:
        r = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True
        )
        return r.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def owner_repo_from_url(repo_url: str) -> tuple[str, str] | None:
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def fetch_issue(
    owner: str, repo: str, number: int, cache_dir: Path
) -> dict | None:
    """Fetch (and cache) one issue via ``gh api``. Returns the JSON, or
    ``{"_missing": True}`` for a 404, or ``None`` on a transport error."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{number}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/issues/{number}",
             "--jq", "{number,state,labels:[.labels[].name],"
                     "is_pr:(has(\"pull_request\")),created_at,title}"],
            capture_output=True, text=True, encoding="utf-8",
        )
    except (FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        # 404 (number doesn't exist) is a real, cacheable answer; other errors
        # (rate limit, network) are not cached so a re-run can retry.
        if "Not Found" in r.stderr or "404" in r.stderr:
            data = {"_missing": True, "number": number}
            cache_path.write_text(json.dumps(data))
            return data
        return None
    data = json.loads(r.stdout)
    cache_path.write_text(json.dumps(data))
    return data


def is_bug_issue(issue: dict, bug_substrings: tuple[str, ...]) -> bool:
    if not issue or issue.get("_missing") or issue.get("is_pr"):
        return False
    labels = [str(name).lower() for name in issue.get("labels", [])]
    return any(any(sub in lab for sub in bug_substrings) for lab in labels)


def confirmed_fixes(
    fixes: list[tuple[str, str]],
    owner: str,
    repo: str,
    cache_dir: Path,
    *,
    bug_labels: list[str] | None = None,
) -> tuple[list[tuple[str, str]], dict]:
    """Keep only fixes referencing a bug-labeled issue. Returns
    ``(confirmed_fixes, meta)``; meta records availability + counts so the
    report can state how many fixes survived confirmation."""
    bug_substrings = tuple(
        (s.lower() for s in bug_labels) if bug_labels else _DEFAULT_BUG_LABEL_SUBSTRINGS
    )
    if not gh_available():
        return [], {"available": False, "reason": "gh CLI not authenticated",
                    "n_fixes": len(fixes), "n_confirmed": 0}

    confirmed: list[tuple[str, str]] = []
    n_with_ref = 0
    bug_issue_numbers: set[int] = set()
    for sha, msg in fixes:
        refs = parse_issue_refs(msg)
        if refs:
            n_with_ref += 1
        for number in refs:
            issue = fetch_issue(owner, repo, number, cache_dir)
            if is_bug_issue(issue, bug_substrings):
                confirmed.append((sha, msg))
                bug_issue_numbers.add(number)
                break
    meta = {
        "available": True,
        "n_fixes": len(fixes),
        "n_fixes_with_issue_ref": n_with_ref,
        "n_confirmed": len(confirmed),
        "n_bug_issues": len(bug_issue_numbers),
        "bug_label_substrings": list(bug_substrings),
    }
    return confirmed, meta
