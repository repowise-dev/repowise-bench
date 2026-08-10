"""T11 oracle: rich.progress.track must accept expand= and widen the bar.

Issue Textualize/rich#3588. The oracle deliberately checks BOTH directions:
expand=True must make the BarColumn full width (bar_width None) and the default
must be unchanged, so a fix that simply hardcodes None does not pass.
"""

from pathlib import Path

from _common import main_wrapper, run_in_tree, run_suite

PROBE = (
    "import inspect, io\n"
    "from rich.console import Console\n"
    "from rich.progress import track, BarColumn\n"
    "sig = inspect.signature(track)\n"
    "print('HAS_EXPAND=', 'expand' in sig.parameters)\n"
    "\n"
    "def bar_widths(**kw):\n"
    "    con = Console(file=io.StringIO(), width=80, force_terminal=False)\n"
    "    seen = []\n"
    "    orig = BarColumn.__init__\n"
    "    def spy(self, *a, **k):\n"
    "        orig(self, *a, **k)\n"
    "        seen.append(self.bar_width)\n"
    "    BarColumn.__init__ = spy\n"
    "    try:\n"
    "        list(track(range(2), console=con, disable=True, **kw))\n"
    "    finally:\n"
    "        BarColumn.__init__ = orig\n"
    "    return seen\n"
    "\n"
    "default = bar_widths()\n"
    "expanded = bar_widths(expand=True)\n"
    "print('DEFAULT_BAR_WIDTHS=', default)\n"
    "print('EXPANDED_BAR_WIDTHS=', expanded)\n"
    "ok = ('expand' in sig.parameters and expanded and all(w is None for w in expanded)\n"
    "      and default and not all(w is None for w in default))\n"
    "print('OK' if ok else 'BAD')\n"
)


def check(tree: Path, args):
    r = run_in_tree(tree, PROBE)
    out = (r.stdout or "").strip().replace("\n", " | ")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return False, f"probe crashed: {tail[-1] if tail else 'no stderr'}"
    if not out.endswith("OK"):
        return False, f"track(expand=) not delivered -> {out}"
    if args.skip_suite:
        return True, f"{out} (suite skipped)"
    green, summary = run_suite(tree)
    return green, f"{out}; suite: {summary}"


main_wrapper("T11", "rich.progress.track accepts expand=", check)
