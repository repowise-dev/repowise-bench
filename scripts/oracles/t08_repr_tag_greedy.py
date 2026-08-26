"""T08 oracle: ReprHighlighter must not merge two adjacent tags into one.

Issue Textualize/rich#4035. At the pin, "<foo> <bar>" yields a single tag whose
contents span swallows the gap and the second tag.
"""

from pathlib import Path

from _common import main_wrapper, run_in_tree, run_suite

PROBE = (
    "from rich.highlighter import ReprHighlighter\n"
    "from rich.text import Text\n"
    "t = Text('<foo> <bar>')\n"
    "ReprHighlighter().highlight(t)\n"
    "spans = [(s.start, s.end, str(s.style)) for s in t.spans]\n"
    "print('SPANS=', spans)\n"
    "names = [(a, b) for a, b, st in spans if st == 'repr.tag_name']\n"
    "crossing = [(a, b, st) for a, b, st in spans if a < 5 < b]\n"
    "print('NAMES=', names)\n"
    "print('CROSSING=', crossing)\n"
    "ok = len(names) == 2 and not crossing\n"
    "print('OK' if ok else 'BAD')\n"
)


def check(tree: Path, args):
    r = run_in_tree(tree, PROBE)
    out = (r.stdout or "").strip().replace("\n", " | ")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return False, f"probe crashed: {tail[-1] if tail else 'no stderr'}"
    if "OK" not in out.split("| ")[-1]:
        return False, f"tags still merged -> {out}"
    if args.skip_suite:
        return True, f"{out} (suite skipped)"
    green, summary = run_suite(tree)
    return green, f"{out}; suite: {summary}"


main_wrapper("T08", "ReprHighlighter separates adjacent tags", check)
