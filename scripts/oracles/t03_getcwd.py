"""T03 oracle: importing rich must survive os.getcwd() raising PermissionError.

Issue Textualize/rich#4201.
"""

from pathlib import Path

from _common import main_wrapper, run_in_tree, run_suite

PROBE = (
    "import os\n"
    "def boom():\n"
    "    raise PermissionError(13, 'denied')\n"
    "os.getcwd = boom\n"
    "import rich, rich.console, rich.traceback, rich.syntax, rich.logging\n"
    "print('IMPORT_OK')\n"
)


def check(tree: Path, args):
    r = run_in_tree(tree, PROBE)
    imported = "IMPORT_OK" in (r.stdout or "")
    if not imported:
        tail = (r.stderr or "").strip().splitlines()
        return False, f"import failed: {tail[-1] if tail else 'no stderr'}"
    if args.skip_suite:
        return True, "import survived a raising os.getcwd (suite skipped)"
    green, summary = run_suite(tree)
    return green, f"import survived a raising os.getcwd; suite: {summary}"


main_wrapper("T03", "rich imports when os.getcwd() raises PermissionError", check)
