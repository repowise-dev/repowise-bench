"""T10 oracle (mechanical control): README.md no longer links at master.

Issue Textualize/rich#4173. The second honesty-control task: the issue names
the file, so there is nothing to retrieve.

The oracle also asserts that README.md is the ONLY modified file, because a
"fix" that rewrites master across the whole tree is a different, larger change
and must not score as this task.
"""

import subprocess
from pathlib import Path

from _common import baseline_paths, main_wrapper

STALE = ("/rich/raw/master/", "/rich/blob/master/")
FRESH = ("/rich/raw/main/", "/rich/blob/main/")
BASELINE_STALE_LINES = 43


def _changed_files(tree: Path) -> list[str]:
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(tree), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = []
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        path = ln.split(None, 1)[-1].strip().strip('"')
        out.append(path)
    return out


def check(tree: Path, args):
    readme = tree / "README.md"
    if not readme.is_file():
        return False, "README.md missing"
    text = readme.read_text(encoding="utf-8", errors="replace")
    remaining = {s: text.count(s) for s in STALE}
    added = {s: text.count(s) for s in FRESH}
    if any(remaining.values()):
        return False, f"README.md still links at master: {remaining}"
    if sum(added.values()) < BASELINE_STALE_LINES:
        return False, (
            f"master links removed but only {sum(added.values())} main links "
            f"present, expected at least {BASELINE_STALE_LINES}: {added}"
        )
    # Tasks run in ONE session, so files changed by earlier tasks are expected.
    # The assertion is "this task touched nothing but README.md", which is only
    # meaningful against the snapshot taken immediately before it ran.
    already = baseline_paths(args)
    changed = [p for p in _changed_files(tree)
               if not p.startswith(".repowise") and not p.startswith(".codegraph")]
    stray = [p for p in changed if p != "README.md" and p not in already]
    if stray:
        return False, f"this task modified files other than README.md: {stray}"
    return True, (
        f"README.md rewritten to main ({added}); "
        f"no file outside the pre-task baseline touched"
    )


main_wrapper("T10", "README.md GitHub URLs point at main", check)
