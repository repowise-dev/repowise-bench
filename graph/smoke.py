"""End-to-end smoke test for the graph-quality bench.

Not a correctness test of the graph. A check that every instrument in this
directory still runs, on the smallest repository in the corpus, and that its
output is the shape the experiments assume. It is the thing to run before a long
session and after any change to `lib/`.

The bar each check has to clear is deliberately low but not trivial: an
instrument that returns zero edges passes an import test and fails this.

    python graph/smoke.py                 # everything that is built
    python graph/smoke.py --only peer     # one group
    python graph/smoke.py --list

Exit code is the number of failed checks, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

# gitleaks is the smoke repository throughout: 226 files, the smallest frozen
# peer index in the corpus, and the only cell where both tools already agree on
# hand-graded precision. If something is broken, it is broken here too, and here
# it costs seconds instead of minutes.
TEST_REPOS = Path(os.environ.get("GRAPH_TEST_REPOS", BENCH.parent / "test-repos")).resolve()
SMOKE_REPO = TEST_REPOS / "gitleaks"
SCRATCH = Path(
    os.environ.get("GRAPH_SCRATCH", Path(os.environ.get("TEMP", "/tmp")) / "graph-smoke")
).resolve()


@dataclass
class Result:
    name: str
    group: str
    ok: bool
    detail: str
    seconds: float
    skipped: bool = False


@dataclass
class Runner:
    only: str | None = None
    results: list[Result] = field(default_factory=list)

    def check(self, name: str, group: str):
        """Decorator registering one check. A raised exception is a failure."""

        def wrap(fn):
            if self.only and self.only != group:
                return fn
            started = time.perf_counter()
            try:
                detail = fn()
                ok, skipped = True, False
                if isinstance(detail, tuple):  # (detail, skipped)
                    detail, skipped = detail
            except _Skip as exc:
                detail, ok, skipped = str(exc), True, True
            except Exception as exc:  # noqa: BLE001 - a failed check is data
                detail, ok, skipped = f"{type(exc).__name__}: {exc}", False, False
                if os.environ.get("GRAPH_SMOKE_TRACE"):
                    traceback.print_exc()
            self.results.append(
                Result(name, group, ok, detail, time.perf_counter() - started, skipped)
            )
            return fn

        return wrap


class _Skip(Exception):
    """Raised by a check whose prerequisite is absent, not broken."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="run one group: provenance, peer, ours, mutate")
    ap.add_argument("--list", action="store_true", help="list groups and exit")
    args = ap.parse_args()

    if args.list:
        print("groups: provenance, peer, ours, mutate")
        return 0

    r = Runner(only=args.only)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- provenance
    @r.check("git state reads a real commit", "provenance")
    def _():
        import provenance as pv

        state = pv.git_state(BENCH.parent, paths=["packages"])
        require(state["head"] and len(state["head"]) == 40, f"bad HEAD: {state['head']}")
        # The porcelain leading-space bug: every path must survive intact.
        bad = [p for p in state["dirty_paths"] if not p.startswith("packages/")]
        require(not bad, f"dirty path mangled, likely a strip() bug: {bad[:3]}")
        return f"HEAD {state['head_short']} dirty={state['dirty']} ({len(state['dirty_paths'])} paths)"

    @r.check("competitor version is recorded", "provenance")
    def _():
        import provenance as pv

        version = pv.tool_versions()["codegraph"]
        if version is None:
            raise _Skip("codegraph not on PATH; peer arm cannot be version-stamped")
        return f"codegraph {version}"

    @r.check("dirty tree blocks a publishable run", "provenance")
    def _():
        import provenance as pv

        state = pv.git_state(BENCH.parent, paths=["packages"])
        if not state["dirty"]:
            require(pv.require_clean(BENCH.parent, allow_dirty=False) is True, "clean tree rejected")
            return "tree clean, guard allows publishable"
        try:
            pv.require_clean(BENCH.parent, allow_dirty=False)
        except pv.DirtyTreeError:
            require(
                pv.require_clean(BENCH.parent, allow_dirty=True) is False,
                "--allow-dirty must yield publishable=False, not True",
            )
            return "dirty tree refused, --allow-dirty yields publishable=False"
        raise AssertionError("dirty tree was NOT refused")

    # ---------------------------------------------------------------------- peer
    @r.check("frozen peer index opens read-only", "peer")
    def _():
        import peer_codegraph as peer

        db = SMOKE_REPO / ".codegraph" / "codegraph.db"
        if not db.is_file():
            raise _Skip(f"no frozen index at {db}")
        before = _dir_fingerprint(db.parent)
        conn = peer.connect(db)
        try:
            st = peer.stats(conn, "gitleaks", str(db))
            require(st.calls_distinct > 0, "peer index has no distinct call edges")
            require(
                st.calls_raw >= st.calls_distinct,
                f"raw {st.calls_raw} < distinct {st.calls_distinct}, folding is inverted",
            )
        finally:
            conn.close()
        # A read-write open would leave a -wal beside the database. The frozen
        # baselines are the bytes every published number reconciles against.
        require(
            _dir_fingerprint(db.parent) == before,
            "peer index directory changed during a read; the ?mode=ro URI is not holding",
        )
        return f"{st.calls_distinct} distinct calls, v{st.codegraph_version}, bytes unchanged"

    @r.check("coverage is ordered either >= incoming", "peer")
    def _():
        import peer_codegraph as peer

        db = SMOKE_REPO / ".codegraph" / "codegraph.db"
        if not db.is_file():
            raise _Skip("no frozen index")
        conn = peer.connect(db)
        try:
            denom = peer.symbol_bearing_files(conn, ["go"])
            require(denom, "no symbol-bearing go files")
            rates = {}
            for direction in ("incoming", "outgoing", "either"):
                covered = (
                    peer.files_with_cross_file_dependents(
                        conn, peer.DEPENDENCY_KINDS, ["go"], direction
                    )
                    & denom
                )
                rates[direction] = len(covered) / len(denom)
                require(0.0 <= rates[direction] <= 1.0, f"{direction} rate out of range")
            require(
                rates["either"] >= max(rates["incoming"], rates["outgoing"]) - 1e-9,
                f"either {rates['either']:.3f} below its own components {rates}",
            )
        finally:
            conn.close()
        return " ".join(f"{k}={v:.3f}" for k, v in rates.items())

    @r.check("g2 peer runner produces the corpus table", "peer")
    def _():
        script = GRAPH / "experiments/g2-cross-file-coverage/run_peer.py"
        out = SCRATCH / "g2_peer.json"
        proc = _run([sys.executable, str(script), "--test-repos", str(TEST_REPOS), "--out", str(out)])
        require(proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-400:]}")
        import json

        report = json.loads(out.read_text(encoding="utf-8"))
        require(len(report["repos"]) >= 5, f"only {len(report['repos'])} repos in report")
        for repo, row in report["repos"].items():
            for key, cell in row["coverage"].items():
                require(
                    cell["rate"] is None or 0.0 <= cell["rate"] <= 1.0,
                    f"{repo}/{key} rate {cell['rate']} out of range",
                )
        return f"{len(report['repos'])} repos, {len(next(iter(report['repos'].values()))['coverage'])} cells each"

    # ---------------------------------------------------------------------- ours
    @r.check("our graph builds and yields call edges", "ours")
    def _():
        try:
            import ours
        except ModuleNotFoundError:
            raise _Skip("graph/lib/ours.py not built yet") from None
        built = ours.build_graph(SMOKE_REPO)
        edges = ours.resolved_call_edges(built)
        require(len(edges) > 0, "our graph resolved zero call edges on gitleaks")
        # Peer holds 2,058 distinct calls here. Anything outside a wide band
        # means the extraction changed shape rather than the graph changing.
        require(100 < len(edges) < 200_000, f"implausible edge count {len(edges)}")
        return f"{len(edges)} distinct call edges"

    @r.check("resolver wrap is restored", "ours")
    def _():
        try:
            import ours
        except ModuleNotFoundError:
            raise _Skip("graph/lib/ours.py not built yet") from None
        from repowise.core.ingestion import call_resolver as cr

        before = cr.CallResolver.resolve_file
        ours.build_graph(SMOKE_REPO)
        require(
            cr.CallResolver.resolve_file is before,
            "CallResolver.resolve_file left monkey-patched; every later build in "
            "this process would double-count",
        )
        return "original restored"

    # -------------------------------------------------------------------- mutate
    @r.check("mutation is deterministic and non-destructive", "mutate")
    def _():
        script = GRAPH / "experiments/g5-invariance/mutate.py"
        if not script.is_file():
            raise _Skip("g5 mutate.py not built yet")
        dsts = [SCRATCH / "m1a", SCRATCH / "m1b"]
        for dst in dsts:
            shutil.rmtree(dst, ignore_errors=True)
            proc = _run(
                [sys.executable, str(script), "--src", str(SMOKE_REPO), "--dst", str(dst),
                 "--mutations", "m1,m2", "--seed", "2026"]
            )
            require(proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-400:]}")
        diff = _run(["git", "diff", "--no-index", "--stat", str(dsts[0]), str(dsts[1])])
        require(not diff.stdout.strip(), f"same seed produced different trees:\n{diff.stdout[:400]}")

        # The manifest carries the denominator the scorer needs. A mutation that
        # names a symbol nothing calls cannot discriminate anything.
        import json

        manifest = json.loads((dsts[0] / "G5_MANIFEST.json").read_text(encoding="utf-8"))
        muts = manifest["mutations"] if isinstance(manifest, dict) else manifest
        require(muts, "manifest lists no mutations")
        # Tracked files only: an untracked .codegraph/.repowise left by an
        # earlier experiment is not evidence that mutate.py wrote to the source.
        status = _run(["git", "-C", str(SMOKE_REPO), "status", "--porcelain", "--untracked-files=no"])
        require(not status.stdout.strip(), f"source repo was mutated:\n{status.stdout[:400]}")
        return f"{len(muts)} mutations, two runs byte-identical, source untouched"

    return _report(r.results)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=BENCH, timeout=1800, check=False)


def _dir_fingerprint(d: Path) -> list[tuple[str, int]]:
    """Names and sizes under a directory, so a stray -wal file is visible."""
    return sorted((p.name, p.stat().st_size) for p in d.iterdir() if p.is_file())


def _report(results: list[Result]) -> int:
    failed = [r for r in results if not r.ok]
    skipped = [r for r in results if r.skipped]
    width = max((len(r.name) for r in results), default=10)
    group = None
    for r in results:
        if r.group != group:
            group = r.group
            print(f"\n[{group}]")
        mark = "SKIP" if r.skipped else ("ok" if r.ok else "FAIL")
        print(f"  {mark:>4}  {r.name:<{width}}  {r.seconds:6.2f}s  {r.detail}")
    print(
        f"\n{len(results) - len(failed) - len(skipped)} passed, "
        f"{len(failed)} failed, {len(skipped)} skipped"
    )
    if skipped:
        print("Skipped checks are unbuilt prerequisites, not passes.")
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main())
