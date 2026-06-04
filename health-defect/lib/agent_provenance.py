"""Per-commit agent-provenance classification — deterministic, precision-first.

Labels every commit in a clone ``{agent, autonomy_tier, channel, confidence}``
from the channels that survive attribution-stripping, in strip-resistance
order:

  T1 (near-autonomous, bot account drives the PR/commit):
     * commit author/committer is a known agent bot account
       (``copilot-swe-agent[bot]``, ``devin-ai-integration[bot]``,
       ``cursoragent@cursor.com``, ...)
     * linked merged PR was opened by a known agent bot account
  T2 (human-driven agent, the agent wrote the change):
     * commit-message footer ("Generated with Claude Code", opencode, Codex)
     * aider author-name suffix ``... (aider)``
     * linked merged PR has an agent branch prefix (``codex/``, ``claude/``,
       ``cursor/``, ``copilot/``) or an agent footer in the PR body
  T3 (assisted, agent contributed but a human authored):
     * ``Co-authored-by:`` trailer naming a known agent

Precedence T1 > T2 > T3; first match wins within a tier. A commit with no
match is human (``agent=None``). Precision-first: every pattern is anchored to
a service identity (bot login, service e-mail, exact footer phrase) — a false
"agent" label on a human commit is worse than a miss.

PR linkage is resolved through a bulk merged-PR index fetched once per repo
via the authenticated ``gh`` CLI (paginated ``/pulls?state=closed``, newest
first, bounded at PR creation >= ``pr_since``), cached as one JSON per repo so
re-runs are offline. Commits map to PRs by squash suffix ``(#N)`` in the
subject or by merge_commit_sha.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------- identities --

# bot login (lowercase, no [bot] suffix) -> agent
BOT_LOGINS = {
    "copilot-swe-agent": "copilot",
    "copilot": "copilot",                     # squash author "Copilot <...+Copilot@users.noreply.github.com>"
    "devin-ai-integration": "devin",
    "cursor": "cursor",
    "cursoragent": "cursor",
    "google-labs-jules": "jules",
    "codegen-sh": "codegen",
    "openhands-ai": "openhands",
    "sweep-ai": "sweep",
    "claude": "claude",                       # the Claude GH app account
}

# exact service e-mail -> (agent, tier) for commit author/committer identity
SERVICE_EMAILS = {
    "cursoragent@cursor.com": ("cursor", 1),
    "devin-ai-integration[bot]@users.noreply.github.com": ("devin", 1),
}

# author/committer e-mail regexes -> agent, tier 1 (bot identity on the commit)
_BOT_EMAIL_RE = re.compile(
    r"^(?:\d+\+)?(" + "|".join(re.escape(b) for b in BOT_LOGINS) + r")\[bot\]@users\.noreply\.github\.com$",
    re.IGNORECASE,
)

# commit-message footers -> agent, tier 2 (exact service phrases only)
FOOTER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"generated with \[?claude code\]?", re.IGNORECASE), "claude"),
    (re.compile(r"generated with \[?opencode\]?", re.IGNORECASE), "opencode"),
    (re.compile(r"generated with \[?(?:openai )?codex\]?", re.IGNORECASE), "codex"),
    (re.compile(r"^\s*aider wrote this", re.IGNORECASE | re.MULTILINE), "aider"),
]

# Co-authored-by trailers -> agent, tier 3. Anchored to the trailer position
# and the agent's service identity (name alone is not enough for "cursor").
COAUTHOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^co-authored-by:\s*claude(?:\s+(?:opus|sonnet|haiku)[^<]*)?\s*<[^>]*@anthropic\.com>",
                re.IGNORECASE | re.MULTILINE), "claude"),
    (re.compile(r"^co-authored-by:\s*copilot\s*<\d+\+copilot@users\.noreply\.github\.com>",
                re.IGNORECASE | re.MULTILINE), "copilot"),
    (re.compile(r"^co-authored-by:\s*cursor(?:\s*agent)?\s*<(?:cursoragent@cursor\.com|[^>]*cursor[^>]*)>",
                re.IGNORECASE | re.MULTILINE), "cursor"),
    (re.compile(r"^co-authored-by:\s*opencode\s*<",
                re.IGNORECASE | re.MULTILINE), "opencode"),
    # NB: name-only matching is NOT safe here — e.g. atproto's core dev
    # "Devin Ivy" matched a loose devin pattern. Anchor to service e-mails.
    (re.compile(r"^co-authored-by:\s*devin[^<]*<[^>]*devin-ai-integration[^>]*>",
                re.IGNORECASE | re.MULTILINE), "devin"),
    (re.compile(r"^co-authored-by:\s*aider[^<]*<[^>]*aider[^>]*>",
                re.IGNORECASE | re.MULTILINE), "aider"),
]

# aider commits: author name carries " (aider)"
_AIDER_NAME_RE = re.compile(r"\(aider\)\s*$")

# PR head-ref prefixes -> agent, tier 2 (the agent created the branch)
BRANCH_PREFIXES = {
    "codex/": "codex",
    "claude/": "claude",
    "cursor/": "cursor",
    "copilot/": "copilot",
    "devin/": "devin",
    "opencode/": "opencode",
}

# PR-body footers -> agent, tier 2
PR_BODY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"generated with \[?claude code\]?", re.IGNORECASE), "claude"),
    (re.compile(r"generated with \[?opencode\]?", re.IGNORECASE), "opencode"),
    (re.compile(r"generated with \[?(?:openai )?codex\]?", re.IGNORECASE), "codex"),
    (re.compile(r"https://cursor\.com/agents\b", re.IGNORECASE), "cursor"),
]

_PR_NUM_RE = re.compile(r"\(#(\d+)\)\s*$")  # squash-merge subject suffix
_MERGE_SUBJ_RE = re.compile(r"^Merge pull request #(\d+)\b")


@dataclass
class Provenance:
    agent: str | None = None
    autonomy_tier: int | None = None   # 1 near-autonomous · 2 human-driven · 3 assisted
    channel: str | None = None
    confidence: str | None = None      # high | medium
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"agent": self.agent, "autonomy_tier": self.autonomy_tier,
                "channel": self.channel, "confidence": self.confidence}


# ------------------------------------------------------- local-only channels --


def classify_local(author_name: str, author_email: str, committer_name: str,
                   committer_email: str, message: str) -> Provenance:
    """Classification from commit metadata alone (no GitHub API)."""
    # T1 — bot identity on the commit itself. Validation note: a service
    # identity as COMMITTER over a human author means the agent pushed/amended
    # a human-driven change — that is T2, not T1.
    for is_author, (name, email) in ((True, (author_name, author_email)),
                                     (False, (committer_name, committer_email))):
        e = (email or "").strip().lower()
        if e in SERVICE_EMAILS:
            agent, tier = SERVICE_EMAILS[e]
            return Provenance(agent, tier if is_author else 2, "service_email", "high")
        m = _BOT_EMAIL_RE.match(e)
        if m:
            return Provenance(BOT_LOGINS[m.group(1).lower()],
                              1 if is_author else 2, "bot_email", "high")
    # T2 — message footer / aider name
    for pat, agent in FOOTER_PATTERNS:
        if pat.search(message):
            return Provenance(agent, 2, "message_footer", "high")
    if _AIDER_NAME_RE.search(author_name or ""):
        return Provenance("aider", 2, "author_name_aider", "high")
    # T3 — co-author trailer
    for pat, agent in COAUTHOR_PATTERNS:
        if pat.search(message):
            return Provenance(agent, 3, "coauthor_trailer", "high")
    return Provenance()


def classify_pr(pr: dict) -> Provenance:
    """Classification from a merged-PR index record (see fetch_pr_index)."""
    login = (pr.get("login") or "").lower().removesuffix("[bot]")
    if pr.get("is_bot") and login in BOT_LOGINS:
        return Provenance(BOT_LOGINS[login], 1, "pr_bot_author", "high")
    head = (pr.get("head_ref") or "").lower()
    for prefix, agent in BRANCH_PREFIXES.items():
        if head.startswith(prefix):
            return Provenance(agent, 2, "pr_branch_prefix", "medium")
    body = pr.get("body") or ""
    for pat, agent in PR_BODY_PATTERNS:
        if pat.search(body):
            # medium, not high: a PR-level footer labels every commit in the
            # PR, but agent PRs can contain human follow-up commits —
            # validation measured 3/13 such cases on this channel.
            return Provenance(agent, 2, "pr_body_footer", "medium")
    return Provenance()


def merge_provenance(local: Provenance, pr: Provenance) -> Provenance:
    """Combine local + PR channels: lowest tier number (highest autonomy) wins;
    on a tie the local channel wins (it is the stronger evidence)."""
    if local.agent and not pr.agent:
        return local
    if pr.agent and not local.agent:
        return pr
    if not local.agent:
        return local
    return local if (local.autonomy_tier or 9) <= (pr.autonomy_tier or 9) else pr


# ------------------------------------------------------------- PR bulk index --


def _gh_json(path: str, params: dict[str, str], *, max_retries: int = 4) -> list | dict | None:
    cmd = ["gh", "api", "-X", "GET", path]
    for k, v in params.items():
        cmd += ["-f", f"{k}={v}"]
    for attempt in range(max_retries):
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                return None
        err = (r.stderr or "") + (r.stdout or "")
        if "rate limit" in err.lower() or "403" in err or "429" in err:
            time.sleep(65 * (attempt + 1))
            continue
        time.sleep(5)
    return None


def fetch_pr_index(owner: str, repo: str, cache_path: Path, *,
                   pr_since: str = "2024-01-01", page_sleep: float = 0.35,
                   log=print) -> dict[str, dict]:
    """All merged PRs created >= pr_since, newest-created first, as
    {str(number): {login,is_bot,head_ref,body,merged_at,merge_commit_sha}}.
    Cached to one JSON; cache hit = offline."""
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    partial_path = cache_path.with_suffix(".partial.json")
    index: dict[str, dict] = {}
    page = 1
    if partial_path.exists():  # resume an interrupted fetch
        state = json.loads(partial_path.read_text(encoding="utf-8"))
        index, page = state["index"], state["next_page"]
        log(f"    {owner}/{repo}: resuming PR index at page {page} ({len(index)} cached)")
    while True:
        batch = _gh_json(f"repos/{owner}/{repo}/pulls",
                         {"state": "closed", "sort": "created", "direction": "desc",
                          "per_page": "100", "page": str(page)})
        if not isinstance(batch, list) or not batch:
            break
        stop = False
        for pr in batch:
            created = pr.get("created_at") or ""
            if created and created < pr_since:
                stop = True
                break
            if not pr.get("merged_at"):
                continue
            user = pr.get("user") or {}
            index[str(pr["number"])] = {
                "login": user.get("login"),
                "is_bot": (user.get("type") == "Bot"),
                "head_ref": (pr.get("head") or {}).get("ref"),
                "body": (pr.get("body") or "")[:4000],
                "merged_at": pr.get("merged_at"),
                "merge_commit_sha": pr.get("merge_commit_sha"),
            }
        if page % 20 == 0:
            log(f"    {owner}/{repo}: PR index page {page}, {len(index)} merged so far")
            partial_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path.write_text(json.dumps({"index": index, "next_page": page + 1}),
                                    encoding="utf-8")
        if stop or len(batch) < 100:
            break
        page += 1
        time.sleep(page_sleep)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if partial_path.exists():
        partial_path.unlink()
    cache_path.write_text(json.dumps(index), encoding="utf-8")
    return index


def fetch_pr_index_search(owner: str, repo: str, cache_path: Path, *,
                          page_sleep: float = 2.2, log=print) -> dict[str, dict]:
    """Targeted agent-PR index via the search API — for firehose repos where
    paginating every merged PR is prohibitive (e.g. homebrew-core, ~100k
    merged PRs/yr). Only agent-attributed PRs matter for classification, so
    one search per channel suffices. Search caps at 1000 results per query;
    a capped query is recorded in ``_truncated`` (recall loss, not precision).
    """
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    truncated: list[str] = []

    def run_query(q: str, fill: dict) -> None:
        total = None
        for page in range(1, 11):
            data = _gh_json("search/issues",
                            {"q": q, "per_page": "100", "page": str(page)})
            time.sleep(page_sleep)
            if not isinstance(data, dict):
                return
            total = data.get("total_count", 0)
            items = data.get("items", [])
            for it in items:
                num = str(it["number"])
                rec = index.setdefault(num, {"login": None, "is_bot": False,
                                             "head_ref": None, "body": "",
                                             "merged_at": None,
                                             "merge_commit_sha": None})
                rec.update({k: v for k, v in fill.items() if v is not None})
                if not rec["body"]:
                    rec["body"] = (it.get("body") or "")[:4000]
                user = it.get("user") or {}
                if rec["login"] is None:
                    rec["login"] = user.get("login")
                    rec["is_bot"] = user.get("type") == "Bot"
            if len(items) < 100:
                return
        if total and total > 1000:
            truncated.append(q)

    base = f"repo:{owner}/{repo} is:pr is:merged"
    for slug in ("copilot-swe-agent", "devin-ai-integration", "cursor",
                 "google-labs-jules", "claude"):
        run_query(f"{base} author:app/{slug}", {"is_bot": True, "login": slug})
    for prefix in BRANCH_PREFIXES:
        run_query(f"{base} head:{prefix.rstrip('/')}",
                  {"head_ref": prefix + "?"})  # prefix evidence; exact ref unknown
    for phrase in ("Generated with Claude Code", "Generated with opencode",
                   "Generated with Codex"):
        run_query(f'{base} "{phrase}"', {})
    if truncated:
        index["_truncated"] = {"queries": truncated}  # type: ignore[assignment]
        log(f"    {owner}/{repo}: {len(truncated)} search queries hit the 1000 cap")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(index), encoding="utf-8")
    return index


# ------------------------------------------------------------- repo walking --

_LOG_FORMAT = "%H%x00%an%x00%ae%x00%cn%x00%ce%x00%aI%x00%P%x00%B%x02"


def walk_commits(repo_dir: Path, *, since: str | None = None) -> list[dict]:
    """All commits (newest first) with identity + full message."""
    args = ["log", f"--format={_LOG_FORMAT}"]
    if since:
        args.append(f"--since={since}")
    out = subprocess.run(["git", *args], cwd=str(repo_dir), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", check=True).stdout
    commits = []
    for chunk in out.split("\x02"):
        chunk = chunk.lstrip("\n")
        if not chunk.strip():
            continue
        parts = chunk.split("\x00", 7)
        if len(parts) < 8:
            continue
        sha, an, ae, cn, ce, date, parents, body = parts
        commits.append({"sha": sha, "author_name": an, "author_email": ae,
                        "committer_name": cn, "committer_email": ce,
                        "date": date, "n_parents": len(parents.split()) if parents else 0,
                        "message": body})
    return commits


def classify_repo(repo_dir: Path, *, pr_index: dict[str, dict] | None = None,
                  since: str | None = None) -> list[dict]:
    """Classify every commit; returns [{sha, date, author_email, is_merge,
    pr_number, agent, autonomy_tier, channel, confidence}]."""
    sha_to_pr: dict[str, str] = {}
    if pr_index:
        for num, pr in pr_index.items():
            sha = pr.get("merge_commit_sha")
            if sha:
                sha_to_pr[sha] = num
    rows = []
    for c in walk_commits(repo_dir, since=since):
        local = classify_local(c["author_name"], c["author_email"],
                               c["committer_name"], c["committer_email"], c["message"])
        pr_prov = Provenance()
        pr_num = None
        if pr_index is not None:
            subject = c["message"].split("\n", 1)[0]
            m = _PR_NUM_RE.search(subject) or _MERGE_SUBJ_RE.match(subject)
            if m:
                pr_num = m.group(1)
            elif c["sha"] in sha_to_pr:
                pr_num = sha_to_pr[c["sha"]]
            if pr_num and pr_num in pr_index:
                pr_prov = classify_pr(pr_index[pr_num])
        prov = merge_provenance(local, pr_prov)
        rows.append({"sha": c["sha"], "date": c["date"],
                     "author_email": c["author_email"],
                     "is_merge": c["n_parents"] > 1, "pr_number": pr_num,
                     **prov.as_dict()})
    return rows
