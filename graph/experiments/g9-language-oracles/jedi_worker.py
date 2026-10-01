"""jedi `goto` over a batch of call sites, run in a clean interpreter.

`python_defs(repo, files, queries)` sends every `(file, line, col)` query to a
child process running this file with the `jedi` argument, and returns one
answer per query, in order:

  * `{"s": "defs", "d": [[file, line, name], ...]}` -- in-repo definitions
  * `{"s": "external", "d": []}` -- resolved, but outside the repository
  * `{"s": "unknown", "d": []}` -- jedi found no static target

The child runs in an interpreter that has jedi and nothing else installed,
because packages visible to jedi would turn some unknown third-party calls
into external ones. Set `ORACLE_PY` to the command for that interpreter, or
have `uv` on PATH and one is created per run.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

JEDI_VERSION = "0.20.0"
UNKNOWN = {"s": "unknown", "d": []}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".repowise"}


def tracked_files(repo: Path) -> list[str]:
    """Repo-relative posix paths: `git ls-files` when a git repo, else a walk."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True
        ).stdout.decode()
        files = [f for f in out.split("\0") if f]
        if files:
            return [f for f in files if (repo / f).is_file()]
    except (OSError, subprocess.CalledProcessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = Path(dirpath).relative_to(repo).as_posix()
        found += [f if rel == "." else f"{rel}/{f}" for f in filenames]
    return sorted(found)


def _manifest_roots(repo: Path, manifest: str) -> list[str]:
    """Import roots one pyproject.toml / setup.cfg / setup.py declares."""
    base = PurePosixPath(manifest).parent
    rels: list[str] = [".", "src"]
    text = (repo / manifest).read_text(encoding="utf-8", errors="ignore")
    name = PurePosixPath(manifest).name
    if name == "pyproject.toml":
        try:
            tool = tomllib.loads(text).get("tool", {})
        except tomllib.TOMLDecodeError:
            tool = {}
        st = tool.get("setuptools", {})
        pkg_dir = st.get("package-dir", {})
        if isinstance(pkg_dir, dict) and isinstance(pkg_dir.get(""), str):
            rels.append(pkg_dir[""])
        find = (st.get("packages") or {}) if isinstance(st.get("packages"), dict) else {}
        rels += (find.get("find") or {}).get("where", [])
        wheel = tool.get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
        rels += [str(PurePosixPath(p).parent) for p in wheel.get("packages", [])]
        rels += [p["from"] for p in tool.get("poetry", {}).get("packages", []) if "from" in p]
        pdm_dir = tool.get("pdm", {}).get("build", {}).get("package-dir")
        maturin_dir = tool.get("maturin", {}).get("python-source")
        rels += [d for d in (pdm_dir, maturin_dir) if isinstance(d, str)]
    else:
        # setup.cfg `package_dir = =src`, `where = src`; setup.py
        # `package_dir={"": "src"}`, `find_packages(where="src")`.
        rels += re.findall(r"package_dir\s*=\s*\n?\s*=\s*([\w./-]+)", text)
        rels += re.findall(r"""package_dir\s*=\s*\{\s*['"]{2}\s*:\s*['"]([\w./-]+)['"]""", text)
        rels += re.findall(r"""where\s*=\s*\[?\s*['"]?([\w./-]+)""", text)
    roots = []
    for r in rels:
        p = os.path.normpath((base / r).as_posix()).replace(os.sep, "/")
        if p not in roots and not p.startswith(".."):
            roots.append(p)
    return roots


def shared_roots(repo: Path, files: list[str]) -> list[str]:
    """Every import root the repository's packaging manifests declare and that
    exists as a directory, plus the repository root."""
    dirs = {str(PurePosixPath(f).parent) for f in files}
    for d in list(dirs):
        while d not in (".", ""):
            d = str(PurePosixPath(d).parent)
            dirs.add(d)
    roots = ["."]
    for f in files:
        if PurePosixPath(f).name in ("pyproject.toml", "setup.cfg", "setup.py"):
            roots += [r for r in _manifest_roots(repo, f) if r not in roots]
    return [r for r in roots if r == "." or r in dirs]


def _jedi_worker() -> None:
    """`jedi_worker.py jedi`: resolve the queries on stdin, answers on stdout."""
    import jedi

    req = json.load(sys.stdin)
    repo = Path(req["repo"]).resolve()
    tracked = set(req["files"])
    project = jedi.Project(repo, added_sys_path=[str(repo / r) for r in req["roots"] if r != "."])
    env = jedi.create_environment(sys.executable, safe=False)
    scripts: dict[str, jedi.Script] = {}

    def place(name) -> tuple[str, list] | None:
        if name.module_path is None:
            return ("external", []) if name.in_builtin_module() else None
        try:
            rel = Path(name.module_path).resolve().relative_to(repo).as_posix()
        except ValueError:
            return ("external", [])
        if rel not in tracked:
            return ("external", [])
        return ("defs", [[rel, name.line, name.name]])

    def resolve(file: str, line: int, col: int) -> dict:
        if file not in scripts:
            scripts[file] = jedi.Script(path=str(repo / file), project=project, environment=env)
        script = scripts[file]
        found = script.goto(line, col, follow_imports=True)
        defs, unknown, external = [], not found, False
        for name in found:
            if name.type == "statement":  # a called variable: what it holds
                held = [n for n in script.infer(line, col) if n.type in ("function", "class")]
                unknown |= not held
            elif name.type in ("function", "class"):
                held = [name]
            else:  # a parameter, an unfollowed import, an instance: no static target
                held, unknown = [], True
            for n in held:
                where = place(n)
                if where is None:
                    unknown = True
                elif where[0] == "external":
                    external = True
                else:
                    defs += where[1]
        if defs:
            return {"s": "defs", "d": defs}
        return {"s": "external" if external and not unknown else "unknown", "d": []}

    out = []
    for file, line, col in req["queries"]:
        try:
            out.append(resolve(file, line, col))
        except Exception:  # jedi gives up on some code; that site is unknown
            out.append(UNKNOWN)
    json.dump(out, sys.stdout)


def _oracle_py() -> list[str]:
    if os.environ.get("ORACLE_PY"):
        return shlex.split(os.environ["ORACLE_PY"])
    uv = shutil.which("uv")
    if uv:
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        run = ["run", "--quiet", "--no-project", "--isolated", "--python", version]
        return [uv, *run, "--with", f"jedi=={JEDI_VERSION}", "python"]
    return [sys.executable]


def python_defs(
    repo: Path, all_files: list[str], queries: list[tuple[str, int, int]]
) -> list[dict]:
    if not queries:
        return []
    req = {
        "repo": str(repo),
        "files": all_files,
        "roots": shared_roots(repo, all_files),
        "queries": queries,
    }
    res = subprocess.run(
        [*_oracle_py(), str(Path(__file__).resolve()), "jedi"],
        input=json.dumps(req),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        raise RuntimeError(f"jedi worker failed: {res.stderr.strip()[-2000:]}")
    return json.loads(res.stdout)


if __name__ == "__main__" and sys.argv[1:] == ["jedi"]:
    _jedi_worker()
