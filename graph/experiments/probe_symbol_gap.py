"""Classify the files one arm calls symbol-bearing and another does not.

Sizing this gap was easy and explaining it was not, because no arm persisted
its symbol-bearing file list. The arms protocol persists it now, so this script
diffs the sets and classifies what falls in between.

Written for the caffeine java gap -- 128 files, 19% of the repository -- but it
takes any repo and any two arms, because zod, dub and celery all show the same
shape and a one-repository script would have to be rewritten for each.

    python graph/experiments/probe_symbol_gap.py --repo caffeine --language java \
        --arms repowise,codegraph

Output is a classification plus a concrete file list, which is what a handover
needs: "128 files" is a number to act on only once someone knows which 128.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BENCH / "graph" / "lib"))

import arms as arms_lib  # noqa: E402


def classify(path: str, source: str | None) -> str:
    """Bucket a file by what it most likely is. Path first, then contents.

    Ordered most-specific first: a `package-info.java` under `src/test` is a
    package-info, not a test, because the reason it declares no symbol is that
    it declares no type at all.
    """
    p = path.lower()
    name = Path(p).name
    if name == "package-info.java":
        return "package-info (declares only a package + annotations)"
    if name == "module-info.java":
        return "module-info"
    if source is not None:
        # `public @interface X {}` -- the modifier sits before the
        # keyword, so an anchored `^\s*@interface` misses every real one.
        if re.search(r"^\s*(public\s+|abstract\s+)*@interface\s+\w+", source, re.M):
            return "annotation type (@interface)"
        if re.search(r"^\s*(public\s+)?(abstract\s+)?interface\s+\w+", source, re.M):
            return "interface"
        if re.search(r"^\s*(public\s+)?enum\s+\w+", source, re.M):
            return "enum"
        if re.search(r"^\s*(public\s+)?(final\s+)?class\s+\w+", source, re.M):
            return "class (declares a type; we should have a symbol)"
        if not source.strip():
            return "empty file"
    if "/test/" in p or p.endswith("test.java"):
        return "test"
    if "/generated" in p or "/build/" in p or "/target/" in p:
        return "generated or build output"
    return "unclassified"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--arms", default="repowise,codegraph")
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--out")
    ap.add_argument("--sample", type=int, default=12)
    args = ap.parse_args()

    repo_path = Path(args.test_repos).resolve() / args.repo
    a_name, b_name = args.arms.split(",")

    sets = {}
    for name in (a_name, b_name):
        arm = arms_lib.get_arm(name)
        art = arm.build(repo_path, repo_name=args.repo)
        try:
            langs = arm.file_languages(art)
            in_lang = {p for p, l in langs.items() if (l or "").lower() == args.language}
            sets[name] = {
                "symbol": arm.symbol_files(art) & in_lang,
                "seen": arm.files_seen(art),
                "in_lang": in_lang,
            }
        finally:
            arm.close(art)

    a, b = sets[a_name], sets[b_name]
    only_b = sorted(b["symbol"] - a["symbol"])  # b says symbol-bearing, a does not
    only_a = sorted(a["symbol"] - b["symbol"])

    buckets: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for rel in only_b:
        f = repo_path / rel
        source = None
        if f.is_file():
            try:
                source = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                source = None
        bucket = classify(rel, source)
        buckets[bucket] += 1
        examples.setdefault(bucket, []).append(rel)

    report = {
        "repo": args.repo,
        "language": args.language,
        "arms": {a_name: len(a["symbol"]), b_name: len(b["symbol"])},
        "files_in_language": {a_name: len(a["in_lang"]), b_name: len(b["in_lang"])},
        f"symbol_only_in_{b_name}": len(only_b),
        f"symbol_only_in_{a_name}": len(only_a),
        "classification": dict(buckets.most_common()),
        "examples": {k: v[: args.sample] for k, v in examples.items()},
        "full_list": only_b,
    }

    print(f"\n{args.repo} / {args.language}: {a_name}={len(a['symbol'])} "
          f"{b_name}={len(b['symbol'])} of {len(a['in_lang'])} files in language\n")
    print(f"Files {b_name} calls symbol-bearing and {a_name} does not: {len(only_b)}")
    for bucket, n in buckets.most_common():
        print(f"  {n:5d}  {bucket}")
        for ex in examples[bucket][:3]:
            print(f"           {ex}")
    if only_a:
        print(f"\nThe other direction, {a_name} only: {len(only_a)}")
        for ex in only_a[:5]:
            print(f"           {ex}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
