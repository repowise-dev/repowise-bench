"""Score the G5 mutations: does the resolver resolve, or does it match names?

`mutate.py` builds the trees. This scores them, for any arm on the protocol.

The question G5 asks is one no competitor currently answers, and it is not
answerable from a coverage or an edge count: a resolver that binds calls by
bare name and one that understands scope produce *the same* numbers on an
unmutated repository. They differ only when the source is changed in a way
whose correct answer is known in advance. That is what a mutation is.

## What each mutation asks, and what a correct answer is

**M1 decoy twin.** A same-named, unreachable declaration is added in a package
nothing imports. Every call that named the original still means the original.
A name-matcher now has two candidates and must guess.
*Correct answer: zero edges pointing at the decoy.* Denominator is the
manifest's `call_sites_naming_symbol`.

**M2 consistent rename.** One symbol is renamed at its declaration and at every
reference, isomorphically. The graph after the rename must be the same graph
with one node relabelled -- nothing gained, nothing lost.
*Correct answer: zero edges lost and zero gained*, after applying the rename to
the baseline's own targets. This is the strictest of the three and the only one
where any non-zero result is a failure rather than a degree.

**M3 shadowing.** A local identifier shadows an imported package inside one
function's scope. Calls on it no longer reach the import.
*Correct answer: the shadowed call site is no longer bound to the import.*

## Why the determinism gate had to come first

Every score here is a difference between two builds. If an arm does not rebuild
identically inside one session, that difference contains run-to-run drift and
the mutation's effect cannot be separated from it. `lib/arms.determinism_report`
is asserted for each arm before it is scored, and a drifting arm is refused
rather than reported with a caveat.

## Baseline trees are copies, not the source repository

`mutate.py` copies the tree excluding `.git` and mutates the copy. The baseline
is copied the same way rather than read from the original checkout, so the two
builds see the same set of files and a difference in the walk cannot be
mistaken for a difference in resolution.

    python graph/experiments/g5-invariance/score.py --arms repowise,codegraph
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

import arms as arms_lib  # noqa: E402
import provenance  # noqa: E402

MUTATE = Path(__file__).resolve().parent / "mutate.py"
SCHEMA = "graph-result/1"


def copy_baseline(src: Path, dst: Path) -> None:
    """Copy exactly the way mutate.py does, so the two walks are comparable."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"))


def build_tree(src: Path, dst: Path, mutation: str, seed: int) -> dict:
    """Produce one mutated tree and return its manifest entry."""
    proc = subprocess.run(
        [sys.executable, str(MUTATE), "--src", str(src), "--dst", str(dst),
         "--mutations", mutation, "--seed", str(seed)],
        capture_output=True, text=True, check=False, timeout=3600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mutate {mutation} failed: {proc.stderr[-1500:]}")
    manifest = json.loads((dst / "G5_MANIFEST.json").read_text(encoding="utf-8"))
    entries = [m for m in manifest["mutations"] if m["id"] == mutation]
    if not entries:
        raise RuntimeError(f"manifest has no entry for {mutation}")
    return entries[0]


def edges_by_site(edges: set[tuple[str, int, str]]) -> dict[tuple[str, int], set[str]]:
    """(file, line) -> the set of targets bound at that call site.

    A set rather than one target: a site can legitimately carry more than one
    edge, and collapsing to "the" target would silently drop the second one --
    which on M1 is exactly the failure being measured, a site that now binds
    both the original and the decoy.
    """
    out: dict[tuple[str, int], set[str]] = {}
    for f, line, target in edges:
        out.setdefault((f, line), set()).add(target)
    return out


def score_m1(mut: dict, base: set, mutated: set) -> dict:
    """Edges pointing at the decoy. Zero is correct."""
    decoy_file = mut["decoy_file"]
    decoy_pkg = mut["decoy_package"]
    symbol = mut["symbol"]

    # A target counts as the decoy if the identity names the decoy's package or
    # its file. Both are checked because the arms differ on what a qualified
    # name contains, and a marker missed on one arm would score a perfect zero
    # for the wrong reason -- the most dangerous possible failure here.
    def is_decoy(target: str) -> bool:
        return decoy_pkg in target or decoy_file.replace("/", ".") in target or decoy_file in target

    decoy_edges = {(f, ln, t) for f, ln, t in mutated if is_decoy(t)}
    # Edges *from* the decoy file are not a failure: the decoy declares a
    # function whose body may itself call something, and that is a real edge in
    # the mutated tree. Only edges pointing AT it are the error.
    decoy_edges = {e for e in decoy_edges if not e[0].startswith(decoy_file.split("/")[0])}

    base_sites, mut_sites = edges_by_site(base), edges_by_site(mutated)
    retargeted = {
        site
        for site, targets in mut_sites.items()
        if site in base_sites
        and targets != base_sites[site]
        and any(symbol in t for t in targets | base_sites[site])
    }
    denom = mut.get("call_sites_naming_symbol") or 0
    return {
        "mutation": "m1",
        "kind": "decoy_twin",
        "symbol": symbol,
        "correct_answer": 0,
        "edges_at_decoy": len(decoy_edges),
        "sites_retargeted": len(retargeted),
        "call_sites_naming_symbol": denom,
        "rate": round(len(decoy_edges) / denom, 4) if denom else None,
        "passed": len(decoy_edges) == 0,
        "sample": sorted(map(str, list(decoy_edges)[:5])),
    }


def score_m2(mut: dict, base: set, mutated: set) -> dict:
    """Edges lost or gained under an isomorphic rename. Zero is the only pass."""
    old, new = mut["old_name"], mut["new_name"]

    def canon(target: str) -> str:
        # Map the mutated tree's names back to the baseline's, so an unchanged
        # graph compares equal. Substring rather than token replacement because
        # a qualified name embeds the symbol inside a dotted path.
        return target.replace(new, old)

    base_c = {(f, ln, canon(t)) for f, ln, t in base}
    mut_c = {(f, ln, canon(t)) for f, ln, t in mutated}
    lost, gained = base_c - mut_c, mut_c - base_c

    # Restrict the headline to edges that touch the renamed symbol. An edge
    # elsewhere that moved is a determinism failure, not a rename failure, and
    # the gate upstream should already have caught it -- but it is reported
    # separately rather than folded in, because merging the two would let a
    # real invariance failure hide inside unrelated noise.
    def touches(e) -> bool:
        return old in e[2] or old in e[0]

    lost_sym, gained_sym = {e for e in lost if touches(e)}, {e for e in gained if touches(e)}
    denom = len({e for e in base_c if touches(e)})
    return {
        "mutation": "m2",
        "kind": "consistent_rename",
        "symbol": mut["symbol"],
        "correct_answer": 0,
        "edges_lost": len(lost_sym),
        "edges_gained": len(gained_sym),
        "baseline_edges_touching_symbol": denom,
        "unrelated_drift": len(lost - lost_sym) + len(gained - gained_sym),
        "rate": round((len(lost_sym) + len(gained_sym)) / denom, 4) if denom else None,
        "passed": not lost_sym and not gained_sym,
        "sample_lost": sorted(map(str, list(lost_sym)[:5])),
        "sample_gained": sorted(map(str, list(gained_sym)[:5])),
    }


def score_m3(mut: dict, base: set, mutated: set) -> dict:
    """The shadowed site must no longer bind to the import."""
    site = (mut["call_site"]["file"], mut["call_site"]["line"])
    shadowed = mut["shadowed_import"]
    tail = shadowed.rstrip("/").split("/")[-1]

    mut_sites = edges_by_site(mutated)
    base_sites = edges_by_site(base)
    still_bound = {t for t in mut_sites.get(site, set()) if tail in t or shadowed in t}
    return {
        "mutation": "m3",
        "kind": "shadowing",
        "symbol": mut["symbol"],
        "correct_answer": 0,
        "site": f"{site[0]}:{site[1]}",
        "shadowed_import": shadowed,
        "was_bound_at_baseline": sorted(base_sites.get(site, set()))[:3],
        "still_bound_to_import": len(still_bound),
        "targets_now": sorted(mut_sites.get(site, set()))[:3],
        "passed": not still_bound,
    }


SCORERS = {"m1": score_m1, "m2": score_m2, "m3": score_m3}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=str(BENCH.parent / "test-repos/gitleaks"))
    ap.add_argument("--name", default="gitleaks")
    ap.add_argument("--arms", default="repowise,codegraph")
    ap.add_argument("--mutations", default="m1,m2,m3")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-determinism", action="store_true",
                    help="dev only; stamps the run not publishable")
    ap.add_argument("--out-root", default=str(BENCH / "results/graph/g5"))
    args = ap.parse_args()

    src = Path(args.repo).resolve()
    arm_names = args.arms.split(",")
    mutations = args.mutations.split(",")

    import repowise.core

    measured_tree = Path(repowise.core.__file__).resolve().parents[4]
    publishable = provenance.require_clean(measured_tree, allow_dirty=args.allow_dirty)
    if args.skip_determinism:
        publishable = False

    work = Path(tempfile.mkdtemp(prefix=f"g5-{args.name}-"))
    result: dict = {
        "schema": SCHEMA,
        "experiments": ["g5-invariance"],
        "provenance": provenance.stamp(
            "g5", repowise_repo=measured_tree, bench_repo=BENCH, publishable=publishable,
            extra={"repo": args.name, "seed": args.seed, "mutations": mutations},
        ),
        "arms": {},
    }

    try:
        baseline_tree = work / "baseline"
        copy_baseline(src, baseline_tree)
        trees: dict[str, tuple[Path, dict]] = {}
        for m in mutations:
            dst = work / m
            trees[m] = (dst, build_tree(src, dst, m, args.seed))
            print(f"built {m}: {trees[m][1]['kind']} on {trees[m][1]['symbol']}", flush=True)

        for arm_name in arm_names:
            arm = arms_lib.get_arm(arm_name)
            print(f"\n=== {arm_name} ===", flush=True)

            if not args.skip_determinism:
                det = arms_lib.determinism_report(arm, baseline_tree, repo_name=args.name)
                if not det["identical"]:
                    result["arms"][arm_name] = {
                        "refused": "arm does not rebuild identically; every score would "
                                   "contain run-to-run drift",
                        "determinism": det,
                    }
                    print("  REFUSED: not deterministic", flush=True)
                    continue
                print(f"  determinism ok ({det['sets']['call_edges']['n_run1']} edges)", flush=True)

            art = arm.build(baseline_tree, repo_name=args.name, fresh=True)
            base_edges = arm.call_edges(art)
            version = art.version
            arm.close(art)

            scores = []
            for m in mutations:
                dst, manifest = trees[m]
                art = arm.build(dst, repo_name=args.name, fresh=True)
                try:
                    mutated_edges = arm.call_edges(art)
                finally:
                    arm.close(art)
                score = SCORERS[m](manifest, base_edges, mutated_edges)
                score["baseline_edges"] = len(base_edges)
                score["mutated_edges"] = len(mutated_edges)
                scores.append(score)
                mark = "PASS" if score["passed"] else "FAIL"
                print(f"  {m} {mark}  {json.dumps({k: v for k, v in score.items() if k not in ('sample', 'sample_lost', 'sample_gained', 'was_bound_at_baseline', 'targets_now')})}", flush=True)

            result["arms"][arm_name] = {"version": version, "scores": scores}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    commit = (result["provenance"]["repowise"]["head_short"] or "nocommit")[:8]
    out_dir = Path(args.out_root) / f"{date.today().isoformat()}-{commit}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
