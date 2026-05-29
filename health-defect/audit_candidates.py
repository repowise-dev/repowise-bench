"""Defect-signal audit for candidate corpus repos (criteria-driven selection).

For each cloned candidate: resolve T0, find keyword fix commits in (T0, HEAD],
count distinct source files (by extension) they touch, split test vs non-test.
A repo qualifies for the calibration corpus only with >= ~5 non-test
defect-bearing source files in the window (the Phase-5 bar). Run BEFORE adding
to config.yaml — selection criteria declared before the numbers (PLAN §5).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.defect_counter import _touched_source_files, find_fix_commits, resolve_t0_sha

REPOS = Path(__file__).resolve().parents[1] / "repos"
T0 = "2025-11-23"

# (name, extensions, test-path markers)
CAND = [
    ("mockito", (".java",)),
    ("gson", (".java",)),
    ("caffeine", (".java",)),
    ("okhttp", (".kt", ".java")),
    ("detekt", (".kt",)),
    ("coroutines", (".kt",)),
    ("fmt", (".cc", ".cpp", ".h", ".hpp")),
    ("spdlog", (".h", ".hpp", ".cpp", ".cc")),
    ("jsoncpp", (".hpp", ".cpp", ".cc", ".h")),
    ("polly", (".cs",)),
    ("serilog", (".cs",)),
    ("dapper", (".cs",)),
    ("npgsql", (".cs",)),
    ("ocelot", (".cs",)),
    ("efcore", (".cs",)),
    ("jellyfin", (".cs",)),
    ("restsharp", (".cs",)),
    ("mqttnet", (".cs",)),
    ("quartznet", (".cs",)),
    ("fluentassertions", (".cs",)),
]

_TEST_MARKERS = ("/test/", "/tests/", "test/", "tests/", ".test.", "_test.", "/spec/")


def _is_test(path: str) -> bool:
    low = path.lower()
    return any(m in low for m in _TEST_MARKERS) or "test" in Path(low).name


def audit(name: str, exts: tuple[str, ...]) -> None:
    repo = REPOS / name
    if not repo.exists():
        print(f"{name:12} MISSING (not cloned)")
        return
    try:
        t0 = resolve_t0_sha(str(repo), T0)
    except Exception as e:  # noqa: BLE001
        print(f"{name:12} ERROR resolving T0: {e}")
        return
    fixes = find_fix_commits(str(repo), t0, "HEAD", strategy="keyword")
    src_files: dict[str, int] = {}
    for sha, _ in fixes:
        for f in _touched_source_files(str(repo), sha, "", exts):
            src_files[f] = src_files.get(f, 0) + 1
    nontest = {f: c for f, c in src_files.items() if not _is_test(f)}
    verdict = "OK " if len(nontest) >= 5 else "WEAK"
    print(
        f"{name:12} {verdict} fixes={len(fixes):4} "
        f"src_files={len(src_files):4} non-test={len(nontest):4} "
        f"(t0={t0[:9]})"
    )
    # Show a couple of representative dirs of the non-test hits.
    dirs: dict[str, int] = {}
    for f in nontest:
        d = str(Path(f).parent)
        dirs[d] = dirs.get(d, 0) + 1
    top = sorted(dirs.items(), key=lambda kv: -kv[1])[:4]
    for d, c in top:
        print(f"               {c:3}  {d}")


if __name__ == "__main__":
    targets = sys.argv[1:] or [c[0] for c in CAND]
    ext_map = dict(CAND)
    for name in targets:
        audit(name, ext_map.get(name, (".txt",)))
