"""Dump one repository's incoming-covered file set, for diffing across commits.

Session 3 measured caffeine's incoming coverage going **down** across three PRs
meant to add: 326 covered files became 325 on an unchanged denominator of 536,
between `3594ba75` and `6540c8b6`. Three call edges landed, and one file lost
the only incoming cross-file call it had. Which file that is cannot be read off
a rate.

This dumps the set rather than the rate, so two runs can be diffed by name:

    # one process per commit -- the editable install follows a git checkout,
    # but an already-imported repowise.core does not
    git -C <worktree> checkout --quiet 3594ba75
    $PY graph/experiments/covered_at_commit.py --repo caffeine --out base.json
    git -C <worktree> checkout --quiet 6540c8b6
    $PY graph/experiments/covered_at_commit.py --repo caffeine --out head.json
    $PY graph/experiments/covered_at_commit.py --diff base.json head.json

**The worktree is shared with any run in flight.** A checkout here during a
measurement silently changes what is being measured, mid-run, with no trace in
the result. Run this only when nothing else is building.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
GRAPH = BENCH / "graph"
sys.path.insert(0, str(GRAPH / "lib"))

import arms as arms_lib  # noqa: E402
import corpus as corpus_lib  # noqa: E402
import provenance  # noqa: E402


def dump(repo_name: str, arm_name: str, test_repos: Path) -> dict:
    rows = {r["name"]: r for r in corpus_lib.load()["repos"]}
    entry = rows.get(repo_name, {})
    repo_path = test_repos / repo_name
    if not repo_path.is_dir():
        raise SystemExit(f"no checkout at {repo_path}")

    import repowise.core

    measured = Path(repowise.core.__file__).resolve()
    for parent in measured.parents:
        if (parent / ".git").exists():
            measured = parent
            break

    arm = arms_lib.get_arm(arm_name)
    art = arm.build(repo_path, repo_name=repo_name, fresh=True)
    try:
        xfile = arm.cross_file_edges(art, arms_lib.CALLS)
        sym = arm.symbol_files(art)
        langs = {p: (l or "").lower() for p, l in arm.file_languages(art).items()}
        lang = entry.get("language")
        in_lang = {p for p, l in langs.items() if l == lang} if lang else set(langs)
        incoming = {t for _, t in xfile}
        denom = sym & in_lang
        return {
            "repo": repo_name,
            "arm": arm_name,
            "language": lang,
            "pin": entry.get("pin"),
            "commit": provenance.git_state(measured, paths=["packages"]),
            "version": provenance._package_version(measured),
            "denominator": sorted(denom),
            "covered_incoming": sorted(incoming & denom),
            "call_edges": len(arm.call_edges(art)),
            "cross_file_calls": len(xfile),
        }
    finally:
        arm.close(art)


def diff(base: dict, head: dict) -> int:
    """Name what moved. A rate cannot, and the rate is what went backwards."""
    b_cov, h_cov = set(base["covered_incoming"]), set(head["covered_incoming"])
    b_den, h_den = set(base["denominator"]), set(head["denominator"])
    print(f"{base['repo']}  {base['commit']['head_short']} -> {head['commit']['head_short']}")
    print(f"  denominator      {len(b_den)} -> {len(h_den)}"
          + ("" if b_den == h_den else "   (CHANGED -- the rates are not comparable)"))
    print(f"  covered incoming {len(b_cov)} -> {len(h_cov)}")
    print(f"  call edges       {base['call_edges']} -> {head['call_edges']}")
    print(f"  cross-file calls {base['cross_file_calls']} -> {head['cross_file_calls']}")
    lost, gained = sorted(b_cov - h_cov), sorted(h_cov - b_cov)
    if b_den != h_den:
        for label, s in (("only in base", b_den - h_den), ("only in head", h_den - b_den)):
            for p in sorted(s)[:20]:
                print(f"  denominator {label}: {p}")
    print(f"\n  LOST coverage ({len(lost)}):")
    for p in lost:
        print(f"    - {p}")
    print(f"  GAINED coverage ({len(gained)}):")
    for p in gained:
        print(f"    + {p}")
    if not lost and not gained:
        print("    (identical covered sets)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo")
    ap.add_argument("--arm", default="repowise")
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--out")
    ap.add_argument("--diff", nargs=2, metavar=("BASE", "HEAD"))
    args = ap.parse_args()

    if args.diff:
        base = json.loads(Path(args.diff[0]).read_text(encoding="utf-8"))
        head = json.loads(Path(args.diff[1]).read_text(encoding="utf-8"))
        if base["repo"] != head["repo"] or base["arm"] != head["arm"]:
            raise SystemExit("refusing to diff different (repo, arm) pairs")
        if base["pin"] != head["pin"]:
            raise SystemExit(
                f"refusing to diff across repository pins ({base['pin']} vs {head['pin']}): "
                "the source changed underneath, so a lost file is not a resolver result"
            )
        return diff(base, head)

    if not args.repo:
        raise SystemExit("--repo is required unless --diff is given")
    doc = dump(args.repo, args.arm, Path(args.test_repos).resolve())
    out = Path(args.out) if args.out else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    print(f"{doc['repo']} at {doc['commit']['head_short']}: "
          f"{len(doc['covered_incoming'])}/{len(doc['denominator'])} covered, "
          f"{doc['cross_file_calls']} cross-file calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
