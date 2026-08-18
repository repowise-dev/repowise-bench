"""T04 oracle (mechanical control): no PytestRemovedIn10Warning from the suite.

Issue Textualize/rich#4169. This is one of the two honesty-control tasks: the
issue names the file and the test, so there is nothing for a retrieval tool to
find. If repowise "helps" here, the instrument is measuring position or
variance rather than the tool.
"""

import subprocess
from pathlib import Path

from _common import BENCH_VENV_PY, _env, main_wrapper, suite_verdict

WARNING = "PytestRemovedIn10Warning"


def check(tree: Path, args):
    r = subprocess.run(
        [str(BENCH_VENV_PY), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=str(tree),
        env=_env(tree),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    out = (r.stdout or "") + (r.stderr or "")
    warned = WARNING in out
    # The green rule is `_common`'s, never a local copy: this oracle carried one
    # written before defect D1 and it still required the EXACT baseline count,
    # so it failed three arms on a suite of 988 passed / 0 failed.
    green, summary = suite_verdict(r.returncode, r.stdout or "")
    if warned:
        return False, f"{WARNING} still emitted; suite: {summary}"
    if not green:
        return False, f"warning gone but suite not green: {summary}"
    return True, f"no {WARNING}; suite: {summary}"


main_wrapper("T04", "pytest emits no parametrize deprecation warning", check)
