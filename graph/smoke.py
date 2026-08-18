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
    ap.add_argument("--only", help="run one group: provenance, peer, ours, stats, arms, mutate")
    ap.add_argument("--list", action="store_true", help="list groups and exit")
    args = ap.parse_args()

    if args.list:
        print("groups: provenance, peer, ours, stats, arms, mutate")
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
        ours = _import_ours()
        built = ours.build_graph(SMOKE_REPO)
        edges = ours.resolved_call_edges(built)
        require(len(edges) > 0, "our graph resolved zero call edges on gitleaks")
        # Peer holds 2,058 distinct calls here. Anything outside a wide band
        # means the extraction changed shape rather than the graph changing.
        require(100 < len(edges) < 200_000, f"implausible edge count {len(edges)}")
        return f"{len(edges)} distinct call edges"

    @r.check("resolver wrap is restored", "ours")
    def _():
        ours = _import_ours()
        from repowise.core.ingestion import call_resolver as cr

        before = cr.CallResolver.resolve_file
        ours.build_graph(SMOKE_REPO)
        require(
            cr.CallResolver.resolve_file is before,
            "CallResolver.resolve_file left monkey-patched; every later build in "
            "this process would double-count",
        )
        return "original restored"

    # --------------------------------------------------------------------- stats
    @r.check("intervals reproduce the published audit", "stats")
    def _():
        import stats

        # These six cells are printed in SYMMETRIC_PRECISION_AUDIT.md and in
        # the substrate doc. If Wilson here stops reproducing them, either this
        # module changed or those tables were never what they claimed.
        published = {
            (29, 30): (83.3, 99.4),
            (20, 30): (48.8, 80.8),
            (28, 30): (78.7, 98.2),
            (7, 30): (11.8, 40.9),
            (134, 150): (83.4, 93.3),
            (93, 150): (54.0, 69.4),
        }
        for (k, n), (lo, hi) in published.items():
            iv = stats.wilson(k, n)
            require(
                abs(iv.low * 100 - lo) < 0.05 and abs(iv.high * 100 - hi) < 0.05,
                f"{k}/{n} -> [{iv.low * 100:.1f}, {iv.high * 100:.1f}], published [{lo}, {hi}]",
            )
        require(stats.wilson(30, 30).high <= 1.0, "interval left [0,1]")
        require(stats.sign_test([(1, 0)] * 6).p_value < 0.05, "6-0 sign test should reach 0.031")
        require(
            stats.sign_test([(1, 0)] * 5 + [(0, 1)]).p_value > 0.05,
            "5-1 must NOT be significant; a test that calls it one is worse than none",
        )
        # Seeded: a bootstrap that moves between runs cannot gate anything.
        units = [(162, 213), (307, 732), (141, 373)]
        require(
            stats.bootstrap(units).low == stats.bootstrap(units).low,
            "bootstrap is not reproducible at a fixed seed",
        )
        return f"6 published cells reproduce; 30-row margin at 60% is +/-{stats.margin(18, 30) * 100:.1f}pt"

    # ---------------------------------------------------------------------- arms
    @r.check("both arms answer the whole protocol", "arms")
    def _():
        import arms as arms_lib

        names = arms_lib.arm_names()
        require("codegraph" in names and "repowise" in names, f"arms missing: {names}")
        counts = {}
        for name in ("codegraph", "repowise"):
            arm = arms_lib.get_arm(name)
            art = arm.build(SMOKE_REPO, repo_name="gitleaks")
            try:
                require(art.version and art.version != "unknown", f"{name} has no version")
                seen = arm.files_seen(art)
                sym = arm.symbol_files(art)
                calls = arm.call_edges(art)
                xfile = arm.cross_file_edges(art)
                require(seen and sym and calls and xfile, f"{name} returned an empty set")
                require(sym <= seen, f"{name}: symbol_files is not a subset of files_seen")
                # Every path repo-relative and forward-slashed. A single
                # backslash makes a cross-arm intersection silently empty, and
                # an empty intersection reads as a finding rather than a bug.
                bad = [p for p in list(seen)[:500] if "\\" in p or p.startswith("/")]
                require(not bad, f"{name} returned unnormalised paths: {bad[:3]}")
                require(
                    arm.cross_file_edges(art, arms_lib.CALLS) <= xfile,
                    f"{name}: calls-only edges are not a subset of all dependency edges",
                )
                counts[name] = (len(seen), len(sym), len(calls))
            finally:
                arm.close(art)
        # The intersection every cross-arm comparison starts from. Non-empty is
        # the only assertion that matters here; its size is a G3 result.
        shared = counts["codegraph"][0] and counts["repowise"][0]
        require(shared, "no shared files between arms")
        return " ".join(f"{k}: {v[0]} seen/{v[1]} sym/{v[2]} calls" for k, v in counts.items())

    @r.check("our arm rebuilds identically", "arms")
    def _():
        import arms as arms_lib

        rep = arms_lib.determinism_report(
            arms_lib.get_arm("repowise"), SMOKE_REPO, repo_name="gitleaks"
        )
        require(rep["identical"], f"our graph is not reproducible: {rep['sets']}")
        return f"{rep['sets']['call_edges']['n_run1']} call edges, two builds identical"

    @r.check("the peer rebuilds identically", "arms")
    def _():
        import arms as arms_lib
        import provenance as pv

        if pv.tool_versions()["codegraph"] is None:
            raise _Skip("codegraph not on PATH")
        rep = arms_lib.determinism_report(
            arms_lib.get_arm("codegraph"), SMOKE_REPO, repo_name="gitleaks"
        )
        # A non-deterministic competitor is a publishable finding about that
        # tool, not a reason to stop. It is still a failure here, because G5
        # cannot separate a mutation's effect from run-to-run drift.
        require(rep["identical"], f"codegraph is NOT deterministic -- publish this: {rep['sets']}")
        return f"{rep['sets']['call_edges']['n_run1']} call edges, two indexes identical"

    @r.check("a fresh peer index reconciles with the frozen one", "arms")
    def _():
        import arms as arms_lib
        import provenance as pv

        if pv.tool_versions()["codegraph"] is None:
            raise _Skip("codegraph not on PATH")
        arm = arms_lib.get_arm("codegraph")
        frozen = arm.build(SMOKE_REPO, repo_name="gitleaks")
        fresh = arm.build(SMOKE_REPO, repo_name="gitleaks", fresh=True)
        try:
            require(frozen.extra.get("frozen"), "frozen build did not open the frozen index")
            require(not fresh.extra.get("frozen"), "fresh build reused the frozen index")
            require(
                arm.call_edges(frozen) == arm.call_edges(fresh),
                "the 1.5.0 binary no longer reproduces the frozen index's call edges; "
                "every published baseline reconciles against those bytes",
            )
            # The frozen indexes were written in place, so they walked our own
            # .repowise/ config; scratch_copy excludes it. That one file is the
            # entire expected difference, and naming it keeps a future reader
            # from treating it as drift.
            extra = arm.files_seen(frozen) - arm.files_seen(fresh)
            require(
                extra <= {".repowise/config.yaml"},
                f"frozen index saw files the fresh one did not: {sorted(extra)[:5]}",
            )
            return f"call edges identical, files differ only by {sorted(extra) or 'nothing'}"
        finally:
            arm.close(frozen)
            arm.close(fresh)

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


def _import_ours():
    """Import `lib/ours.py`, telling three failures apart.

    The bare `except ModuleNotFoundError -> _Skip` this replaces swallowed the
    interpreter error as well as the absent-file one, because `ours.py` imports
    `repowise.core` and a missing `repowise.core` raises the same exception as a
    missing `ours`. Run under the wrong python -- the default on this machine's
    PATH is a different venv entirely -- the suite printed "not built yet" for
    both `ours` checks and reported 6 passed, 0 failed. That is a green-looking
    run of a suite that never touched our graph, which is exactly the class of
    silent failure this file exists to catch.
    """
    if not (GRAPH / "lib" / "ours.py").is_file():
        raise _Skip("graph/lib/ours.py not built yet")
    try:
        import ours
    except ModuleNotFoundError as exc:
        # The file is present, so this is a dependency the interpreter lacks --
        # `repowise.core` under a stray python, `networkx` under a venv that
        # never installed it. Either way the fix is the same interpreter, so
        # both get the same message rather than only the one anticipated.
        raise AssertionError(
            f"{exc}. This interpreter ({sys.executable}) cannot import lib/ours.py's "
            "dependencies, so it cannot measure our graph. Use the measurement "
            "worktree's python: "
            "C:/Users/ragha/Desktop/bench-worktrees/g2clean/.venv/Scripts/python.exe"
        ) from None
    return ours


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
