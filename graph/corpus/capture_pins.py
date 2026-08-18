"""Generate `corpus.lock` from the checkouts already in `test-repos/`.

Written rather than hand-typed for the same reason the result tables are:
`corpus.yaml` carried pins for four repositories out of ninety-two, and the
other eighty-eight were only ever "whatever is on that laptop". A pin captured
by hand across thirty repositories drifts; a pin captured by a script that runs
`git rev-parse` does not.

    python graph/corpus/capture_pins.py --write

Language is inferred from the tracked-file extension histogram, which is a
`git ls-files` index read rather than a tree walk, so this is cheap enough to
re-run. Anything inferred is stamped `language_inferred: true` -- an inferred
language is a guess about a repository, and a reader must be able to see which
rows are guesses.

`kind` is deliberately left null for the breadth pool. The three-kinds rule
only binds the repositories a per-language claim rests on, and inventing a
classification for the other fifty-odd would put made-up data in a lock file
whose whole job is to be trustworthy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
TEST_REPOS = BENCH.parent / "test-repos"

# Extension -> language, using the names `run_corpus.py` filters on. Only
# extensions that decide a repository's primary language are listed; docs,
# data and asset extensions are ignored rather than mapped, because a
# repository whose largest extension is `.html` (Alamofire's docs, exposed's
# generated site) is not an HTML repository.
_EXT_LANG = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp", ".go": "go",
    ".rs": "rust", ".php": "php", ".rb": "ruby", ".swift": "swift",
    ".dart": "dart", ".scala": "scala", ".vue": "vue", ".svelte": "svelte",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".h": "cpp", ".c": "c", ".m": "objc", ".mm": "objc",
}

# The three-kinds table from the corpus plan, for the repositories a
# per-language claim is meant to rest on. Everything absent from here is
# breadth pool and gets a null kind.
_KIND = {
    "requests": "library", "scrapy": "application", "django": "framework",
    "celery": "framework",
    "zod": "library", "taxonomy": "application", "dub": "application",
    "hono": "framework",
    "caffeine": "library", "javalin": "application",
    "spring-petclinic": "framework", "jhipster-sample-app": "framework",
    "eShopOnWeb": "application", "Ocelot": "framework",
    "CleanArchitecture": "framework",
    "gitleaks": "application", "hugo": "framework", "syft": "framework",
    "bevy": "framework",
    "guzzle": "library",
    "jekyll": "application", "sinatra": "framework",
    "fmt": "library", "nlohmann-json": "library", "leveldb": "library",
    "Crow": "framework", "seastar": "framework",
    "exposed": "library", "ktor": "framework",
    "Alamofire": "library",
}

# Repositories with a frozen CodeGraph index that published baselines
# reconcile against. Their pins are recorded as they sit on disk and must
# never be re-cloned to a newer commit.
_FROZEN_PEER = {"caffeine", "zod", "Ocelot", "celery", "gitleaks", "dub"}

# Carried over from corpus.yaml, which the lock supersedes but whose notes
# cost a session each to learn.
_NOTES = {
    "gitleaks": "Vendors its own regexp wrapper that its tests import instead "
                "of the stdlib. Rows there look wrong and are not.",
    "caffeine": "Has a guava/ compatibility subtree that mirrors Guava names "
                "deliberately. The index also carries kotlin and python "
                "callers, so any java figure must pin files.language.",
    "zod": "Parallel v3/v4/mini trees plus a zod4 package.json alias to a "
           "published npm package. Read the imports before grading.",
    "celery": "Framework and repository under test at once. Any "
              "framework-shaped result measured here must be rerun on a "
              "repository that imports celery rather than vendoring it.",
    "dub": "Largest of the six. No hand-graded precision rows exist for it.",
}


# Paths every checkout here carries edits to because the benchmark and the
# agent tooling write them. A modification under one of these does not mean the
# source under measurement differs from the pin. Anything else does, and
# `autogpt` is the reason this distinction is drawn rather than assumed: its
# index carries 4,350 staged deletions, so it is not a checkout of its pin at
# all, and nothing about that is visible from `rev-parse HEAD`.
_TOOLING_PREFIXES = (
    ".gitignore", ".claude/", ".vscode/", ".repowise/", ".mcp.json",
    ".codegraph/", "graphify-out/", ".code-review-graph/", ".agents/",
    ".branchlet.json",
)


def is_tooling(path: str) -> bool:
    return any(path.startswith(p) for p in _TOOLING_PREFIXES)


def git(repo: Path, *args: str, keep_leading: bool = False) -> str | None:
    """Read-only git. `keep_leading` is not optional for `status --porcelain`.

    Columns 0 and 1 are the index and worktree states, so a worktree-modified
    file is " M path". A plain .strip() eats that leading space on the first
    line only, turning one path into "gitignore" while every other path stays
    intact -- which read as "13 repositories are not at their pin" when twelve
    of them only had a tooling file edited. `provenance.py` carries the same
    note; this is the second time the trap has been paid for.
    """
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if out.returncode != 0:
        return None
    return out.stdout.rstrip("\r\n") if keep_leading else out.stdout.strip()


def infer_language(files: list[str]) -> tuple[str | None, dict[str, int]]:
    counts: Counter[str] = Counter()
    for f in files:
        dot = f.rfind(".")
        lang = _EXT_LANG.get(f[dot:].lower()) if dot != -1 else None
        if lang:
            counts[lang] += 1
    if not counts:
        return None, {}
    return counts.most_common(1)[0][0], dict(counts.most_common(6))


def entry(path: Path) -> dict | None:
    if not (path / ".git").exists():
        return None
    pin = git(path, "rev-parse", "HEAD")
    if not pin:
        return None
    files = (git(path, "ls-files") or "").splitlines()
    lang, hist = infer_language(files)
    status = git(path, "status", "--porcelain", keep_leading=True) or ""
    tracked_dirty = [ln[3:] for ln in status.splitlines() if not ln.startswith("??")]
    row: dict = {
        "name": path.name,
        "language": lang,
        "language_inferred": True,
        "kind": _KIND.get(path.name),
        "url": git(path, "config", "--get", "remote.origin.url"),
        "pin": pin,
        "files": len(files),
        "extensions": hist,
    }
    if path.name in _FROZEN_PEER:
        row["peer_index"] = f"test-repos/{path.name}/.codegraph/codegraph.db"
    # Untracked benchmark artifacts are expected in every checkout here and are
    # not recorded. A modified tracked file matters only if it is source: the
    # source under measurement then is not the source at the pin, which is the
    # one thing a lock file must not hide.
    source_dirty = [p for p in tracked_dirty if not is_tooling(p)]
    if tracked_dirty:
        row["tracked_modifications"] = len(tracked_dirty)
    if source_dirty:
        row["usable"] = False
        row["unusable_because"] = (
            f"{len(source_dirty)} tracked source paths differ from the pin, "
            f"e.g. {', '.join(source_dirty[:3])}. Re-clone before using this "
            "repository for a measurement."
        )
    if path.name in _NOTES:
        row["note"] = _NOTES[path.name]
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test-repos", default=str(TEST_REPOS))
    ap.add_argument("--out", default=str(Path(__file__).parent / "corpus.lock"))
    ap.add_argument("--write", action="store_true", help="without this, prints a summary only")
    args = ap.parse_args()

    root = Path(args.test_repos).resolve()
    rows: list[dict] = []
    skipped: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        row = entry(child)
        if row:
            rows.append(row)
        else:
            skipped.append(child.name)

    doc = {
        "schema": "corpus-lock/1",
        "note": (
            "Pins for every checkout in test-repos/, captured by "
            "graph/corpus/capture_pins.py. A rerun on a different pin is a "
            "different measurement and must say so."
        ),
        "captured_from": str(root),
        "not_a_git_checkout": skipped,
        "repos": rows,
    }
    langs = Counter(r["language"] for r in rows)
    print(f"{len(rows)} pinned, {len(skipped)} skipped: {', '.join(skipped) or 'none'}")
    print("languages:", dict(langs.most_common()))
    print("kinds assigned:", sum(1 for r in rows if r["kind"]))
    dirty = [r["name"] for r in rows if "tracked_modifications" in r]
    print("tooling edits in:", len(dirty), "checkouts")
    unusable = [r["name"] for r in rows if r.get("usable") is False]
    print("NOT AT THEIR PIN:", unusable or "none")
    if args.write:
        Path(args.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
