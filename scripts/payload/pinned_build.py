"""Name the build under test, and refuse a run that cannot.

Every runner in this directory used to hard-code the binary to the primary
checkout, whose console script is an editable install that runs whatever branch
that checkout happens to be on. A run could not say which build produced it,
and one already published a feature branch as main.

Three separate knobs point at a binary here -- which build the hooks are read
out of, which resident block is installed, and which server the agent actually
calls -- and setting two of three leaves an arm running one build's hooks
against another build's server with nothing in the output saying so.

So: both knobs are arguments with no default, they are checked against each
other, and the fingerprint is the file the import lands on. `repowise
--version` reads the same string for builds either side of a fix and cannot
separate them.

The output file is guarded for the same reason. A fixed output name lets a new
run append to an older run's rows, mixing index generations under one filename
and starting a budget counter part-spent against its own ceiling.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def fingerprint(binary_python: Path) -> dict:
    """Which build this interpreter actually imports.

    An editable install resolves to whatever tree it was installed from, so
    the only honest fingerprint is the file the import lands on.
    """
    code = ("import json, repowise.core as c, repowise.server as s;"
            "print(json.dumps({'core': c.__file__, 'server': s.__file__}))")
    try:
        r = subprocess.run([str(binary_python), "-c", code],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except OSError as exc:
        # A path that is not an executable must come back as a refusal, not as
        # a traceback: the caller's job is to print why and stop, and an
        # exception here would skip the reason.
        return {"error": f"cannot execute {binary_python}: {exc}"}
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout)[-500:]}
    try:
        return json.loads(r.stdout.strip())
    except ValueError:
        return {"error": r.stdout[-500:]}


def venv_root(binary_python: Path) -> str:
    """The build root a `<root>/.venv/Scripts/python.exe` belongs to."""
    return str(binary_python.parent.parent.parent).lower()


def check_binary(binary_python: Path, repowise_exe: Path,
                 out=sys.stderr) -> dict | None:
    """Both knobs, resolved and agreeing, or None and a printed reason."""
    for label, path in (("binary-python", binary_python),
                        ("repowise-exe", repowise_exe)):
        if not path.exists():
            print(f"!! {label} does not exist: {path}", file=out)
            return None

    fp = fingerprint(binary_python)
    if "error" in fp:
        print(f"!! could not fingerprint the build: {fp['error']}", file=out)
        return None

    root = venv_root(binary_python)
    if not fp["core"].lower().startswith(root):
        print(f"!! the interpreter imports a build outside {root}. Refusing: "
              f"a row that cannot name its binary is not a measurement.",
              file=out)
        return None
    if not str(repowise_exe).lower().startswith(root):
        print(f"!! repowise-exe is not from the same build as binary-python. "
              f"Refusing rather than mixing two builds in one arm.", file=out)
        return None
    return fp


def check_out(out_path: Path, append: bool, out=sys.stderr) -> bool:
    """True when it is safe to write rows here."""
    if out_path.exists() and out_path.stat().st_size > 0 and not append:
        print(f"!! {out_path.name} already has rows. A re-run that appends to "
              f"a previous run's file mixes two index generations under one "
              f"name and starts the budget counter part-spent. Use a new "
              f"--run-id, or --append if continuing this same run.", file=out)
        return False
    return True


def self_test() -> int:
    """Each refusal below has a matching acceptance, or it is not a check."""
    import io
    import os
    import tempfile

    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    me = Path(sys.executable)

    # A real interpreter fingerprints; a path that is not one does not.
    check("fingerprint of a non-binary", "error" in fingerprint(Path("nope.exe")), True)
    fp = fingerprint(me)
    # This interpreter may or may not have repowise importable; only assert the
    # shape, since asserting the content would make the test environmental.
    check("fingerprint returns one of the two shapes",
          "error" in fp or {"core", "server"} <= set(fp), True)

    # A missing binary is refused...
    buf = io.StringIO()
    check("missing binary refused",
          check_binary(Path("nope.exe"), me, out=buf), None)
    check("...and says which knob", "binary-python does not exist" in buf.getvalue(), True)

    # ...and a missing server is refused separately from a missing interpreter.
    buf = io.StringIO()
    check("missing server refused", check_binary(me, Path("nope.exe"), out=buf), None)
    check("...naming the server knob", "repowise-exe does not exist" in buf.getvalue(), True)

    # venv_root climbs exactly three levels, which is what the layout is.
    check("venv root",
          venv_root(Path(r"C:\a\b\.venv\Scripts\python.exe")),
          r"c:\a\b")

    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty.jsonl"
        rows = Path(td) / "rows.jsonl"
        rows.write_text('{"task_id": "B01"}\n', encoding="utf-8")

        # A file that does not exist is fine, and so is an empty one.
        check("absent out accepted", check_out(empty, False), True)
        empty.write_text("", encoding="utf-8")
        check("empty out accepted", check_out(empty, False), True)

        # A file with rows is refused, and --append is the documented way past
        # it. Without the second half this check could pass by always refusing.
        buf = io.StringIO()
        check("populated out refused", check_out(rows, False, out=buf), False)
        check("...and says why", "already has rows" in buf.getvalue(), True)
        check("populated out accepted with --append", check_out(rows, True), True)
        assert os.path.exists(rows)

    if failures:
        for f in failures:
            print("FAIL", f)
        return 1
    print("self-test: every refusal has a matching acceptance")
    return 0


if __name__ == "__main__":
    sys.exit(self_test())
