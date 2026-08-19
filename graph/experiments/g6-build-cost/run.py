"""G6: what it costs to build the graph, and only the graph.

Our published indexing-time row compares a full `repowise` index, which also
generates documentation, computes health and builds embeddings, against tools
that build a graph and stop. That is the right comparison for "how long until I
can use this" and the wrong one here. G6 times walk, parse, resolve and edge
construction on both sides, and nothing else.

**We expect to lose this row and it gets published at whatever it says.** A
benchmark that only reports the columns its author wins is not evidence.

Every arm runs against a scratch copy of the repository. Nothing under
`test-repos/` is ever indexed in place, because the frozen peer indexes there
are the baselines earlier numbers reconcile against.

    python graph/experiments/g6-build-cost/run.py \
        --repo ../test-repos/gitleaks --name gitleaks --language go \
        --runs 3 --arms peer,ours --out results/graph/g6/gitleaks.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import statistics
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "lib"))

import procmeter as pm  # noqa: E402
import provenance as prov  # noqa: E402


def _fresh_copy(src: Path, dst: Path) -> Path:
    """A clean copy with every tool's index stripped, so no run reads a cache."""
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    for stale in (".codegraph", ".repowise", ".code-review-graph", "graphify-out"):
        shutil.rmtree(dst / stale, ignore_errors=True)
    return dst


def run_peer(repo: Path, scratch: Path, runs: int) -> dict:
    """`codegraph init -i`: their whole product is the graph, so this is fair."""
    samples = []
    # One warmup, discarded. The first index of a freshly copied tree pays for
    # the OS file cache and, for node arms, JIT warmup: gitleaks measured 6.92s
    # cold against 1.90s warm, a 3.6x spread that would otherwise land entirely
    # in whichever arm happened to run first. Both arms get the same treatment.
    for i in range(runs + 1):
        work = _fresh_copy(repo, scratch / f"peer-{i}")
        # shell=True: codegraph is an npm bin and exists only as codegraph.cmd.
        res = pm.run_measured("codegraph init -i", cwd=work, shell=True, timeout=3600)
        if not res.ok:
            return {"arm": "codegraph", "error": res.stderr[-2000:], "returncode": res.returncode}
        if i == 0:
            shutil.rmtree(work, ignore_errors=True)
            continue
        samples.append(
            {
                "seconds": round(res.seconds, 3),
                "peak_rss_mb": round(res.peak_rss_mb, 1) if res.peak_rss_mb else None,
                "index_mb": round(pm.dir_size_mb(work / ".codegraph"), 2),
            }
        )
        shutil.rmtree(work, ignore_errors=True)
    return {"arm": "codegraph", "runs": samples, **_summarise(samples)}


def run_ours(repo: Path, scratch: Path, runs: int) -> dict:
    """Our ingestion only: traverse, parse, resolve, build. No docs, no embeddings.

    Run in-process rather than through the CLI, because the CLI cannot be asked
    to stop after the graph, and timing a process that also writes documentation
    would measure the thing this experiment exists to exclude.
    """
    try:
        import ours
    except ModuleNotFoundError:
        return {"arm": "repowise", "error": "graph/lib/ours.py not built yet"}

    samples = []
    for i in range(runs + 1):  # warmup discarded, same as the peer arm
        work = _fresh_copy(repo, scratch / f"ours-{i}")
        built = ours.build_graph(work)
        if i == 0:
            shutil.rmtree(work, ignore_errors=True)
            continue
        phases = dataclasses.asdict(built.timings)  # walk / parse / build
        samples.append(
            {
                "seconds": round(sum(phases.values()), 3),
                "phases": {k: round(v, 3) for k, v in phases.items()},
                # In-process, so there is no child to attach a job object to and
                # the number would be this interpreter's peak, not the build's.
                # G6 reports our memory from a subprocess run or not at all.
                "peak_rss_mb": None,
                "edges": len(ours.resolved_call_edges(built)),
            }
        )
        shutil.rmtree(work, ignore_errors=True)
    return {"arm": "repowise", "runs": samples, **_summarise(samples)}


def _summarise(samples: list[dict]) -> dict:
    """Median, not mean. One slow run from a background process should not move
    the published number, and with three runs the median is the whole defence."""
    times = [s["seconds"] for s in samples]
    peaks = [s["peak_rss_mb"] for s in samples if s.get("peak_rss_mb")]
    return {
        "median_seconds": round(statistics.median(times), 3),
        "min_seconds": round(min(times), 3),
        "max_seconds": round(max(times), 3),
        "median_peak_rss_mb": round(statistics.median(peaks), 1) if peaks else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--arms", default="peer,ours")
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--out")
    ap.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run over uncommitted repowise changes; stamps the result publishable: false",
    )
    args = ap.parse_args()

    publishable = prov.require_clean(BENCH.parent, allow_dirty=args.allow_dirty)
    repo = Path(args.repo).resolve()
    scratch = Path(args.scratch).resolve() if args.scratch else BENCH / "scratch_graph" / "g6"
    scratch.mkdir(parents=True, exist_ok=True)

    report = {
        "provenance": prov.stamp(
            "g6-build-cost", repowise_repo=BENCH.parent, bench_repo=BENCH, publishable=publishable
        ),
        "repo": args.name,
        "language": args.language,
        "runs_per_arm": args.runs,
        "arms": {},
    }

    wanted = {a.strip() for a in args.arms.split(",")}
    if "peer" in wanted:
        report["arms"]["codegraph"] = run_peer(repo, scratch, args.runs)
    if "ours" in wanted:
        report["arms"]["repowise"] = run_ours(repo, scratch, args.runs)

    print(f"\n{args.name} ({args.language}), median of {args.runs}")
    print(f"{'arm':12s} {'seconds':>9s} {'peak MB':>9s} {'note':>0s}")
    for name, row in report["arms"].items():
        if "error" in row:
            print(f"{name:12s} {'-':>9s} {'-':>9s} {row['error'][:60]}")
            continue
        peak = row["median_peak_rss_mb"]
        print(
            f"{name:12s} {row['median_seconds']:>9.2f} "
            f"{(f'{peak:.0f}' if peak else '-'):>9s} "
            f"[{row['min_seconds']:.2f}, {row['max_seconds']:.2f}]"
        )
    if not publishable:
        print("\nNOT PUBLISHABLE: repowise tree is dirty. Smoke test only.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
