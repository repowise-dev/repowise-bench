"""T06 oracle: Text.from_ansi must not lose content on CRLF input.

Issue Textualize/rich#4090.
"""

from pathlib import Path

from _common import main_wrapper, run_in_tree, run_suite

PROBE = (
    "from rich.text import Text\n"
    "crlf = [ln.plain for ln in Text.from_ansi('one\\r\\ntwo\\r\\nthree').split('\\n')]\n"
    "lf = [ln.plain for ln in Text.from_ansi('one\\ntwo\\nthree').split('\\n')]\n"
    "print('CRLF=', crlf)\n"
    "print('LF=', lf)\n"
    "print('MATCH' if crlf == lf == ['one', 'two', 'three'] else 'MISMATCH')\n"
)


def check(tree: Path, args):
    r = run_in_tree(tree, PROBE)
    out = (r.stdout or "").strip().replace("\n", " | ")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return False, f"probe crashed: {tail[-1] if tail else 'no stderr'}"
    if "MATCH" not in out or "MISMATCH" in out:
        return False, f"CRLF lines differ from LF lines -> {out}"
    if args.skip_suite:
        return True, f"{out} (suite skipped)"
    green, summary = run_suite(tree)
    return green, f"{out}; suite: {summary}"


main_wrapper("T06", "Text.from_ansi handles CRLF line endings", check)
