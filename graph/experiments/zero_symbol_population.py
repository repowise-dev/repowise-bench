"""How many files we walk, in the repository's own language, and extract nothing from.

`symbol_gap_corpus.py` sizes the gap *relative to a peer*, which is the right
frame for a competitive claim and the wrong one for a lever: a file neither arm
extracts from is invisible to it, and CodeGraph turns out to agree with us on
almost everything. This asks our own arm the absolute question instead --
`files_seen - symbol_files`, restricted to the primary language -- because a file
we walked and got nothing out of cannot receive an edge no matter what any
competitor does.

It also splits the answer by whether the file plausibly declares anything, using
a per-language keyword probe on the source. A test fixture of pure data and a
`data class` we failed to parse are both "zero symbols", and only the second is
a bug.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BENCH / "graph" / "lib"))

import arms as arms_lib  # noqa: E402
import corpus as corpus_lib  # noqa: E402

# Deliberately crude and deliberately generous: it over-counts (a keyword in a
# comment or a string counts), so "declares nothing" is a floor and "looks like
# it declares something" is a ceiling. Both are stated as such.
DECL_RE = {
    "kotlin": r"^\s*(@\w+[\s\S]{0,400}?)?(public |internal |private |open |abstract |sealed |data |value |annotation )*\b(class|object|interface|fun|typealias)\s+\w",
    "java": r"^\s*(public |protected |private |static |final |abstract )*\b(class|interface|enum|record|@interface)\s+\w",
    "cpp": r"^\s*(inline |static |constexpr |template\s*<|virtual )*\b(class|struct|namespace|enum)\s+\w|^\s*\w[\w:<>,\s\*&]*\s+\w+\s*\([^;]*\)\s*(const)?\s*\{",
    "rust": r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(unsafe\s+)?\b(fn|struct|enum|trait|impl|mod|type|macro_rules!)\s",
    "typescript": r"^\s*(export\s+)?(default\s+)?(declare\s+)?(abstract\s+)?\b(class|interface|function|enum|type|const|let|var)\s",
    "python": r"^\s*(async\s+)?\b(def|class)\s+\w",
    "go": r"^\s*(func|type)\s+\w",
    "ruby": r"^\s*(class|module|def)\s+\w",
    "csharp": r"^\s*(public |internal |private |protected |static |sealed |abstract |partial )*\b(class|interface|struct|enum|record|delegate)\s+\w",
    "php": r"^\s*(abstract\s+|final\s+)?\b(class|interface|trait|function|enum)\s+\w",
    "swift": r"^\s*(public |internal |private |fileprivate |open |final )*\b(class|struct|enum|protocol|func|extension)\s+\w",
}


def looks_declarative(path: Path, language: str) -> bool | None:
    pat = DECL_RE.get(language)
    if pat is None:
        return None
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return re.search(pat, src, re.M) is not None


def probe(row: dict, test_repos: Path) -> dict:
    name, language = row["name"], row["language"]
    repo_path = test_repos / name
    ours = arms_lib.get_arm("repowise")
    art = ours.build(repo_path, repo_name=name, fresh=True)
    try:
        seen = ours.files_seen(art)
        sym = ours.symbol_files(art)
        langs = {p: (l or "").lower() for p, l in ours.file_languages(art).items()}
    finally:
        ours.close(art)

    in_lang = {p for p, l in langs.items() if l == language}
    empty = sorted((seen & in_lang) - sym)

    declarative, inert, unknown = [], [], []
    for rel in empty:
        verdict = looks_declarative(repo_path / rel, language)
        (declarative if verdict else inert if verdict is False else unknown).append(rel)

    return {
        "repo": name,
        "language": language,
        "kind": row.get("kind"),
        "files_seen_in_language": len(seen & in_lang),
        "symbol_files_in_language": len(sym & in_lang),
        "zero_symbol_in_language": len(empty),
        "zero_symbol_share": round(len(empty) / len(seen & in_lang), 4) if seen & in_lang else None,
        "looks_declarative": len(declarative),
        "looks_inert": len(inert),
        "no_probe_for_language": len(unknown),
        "extensions": dict(Counter(Path(p).suffix.lower() for p in empty).most_common()),
        "examples_declarative": declarative[:25],
        "examples_inert": inert[:10],
        "full_list_declarative": declarative,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test-repos", default=str(BENCH.parent / "test-repos"))
    ap.add_argument("--only")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = corpus_lib.select().rows
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if r["name"] in want]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    if out_path.is_file():
        results = json.loads(out_path.read_text(encoding="utf-8")).get("repos", [])
    done = {r["repo"] for r in results}

    test_repos = Path(args.test_repos).resolve()
    for row in rows:
        if row["name"] in done:
            continue
        print(f"=== {row['name']} ({row['language']})", flush=True)
        try:
            res = probe(row, test_repos)
        except Exception:
            traceback.print_exc()
            res = {"repo": row["name"], "language": row["language"], "error": True}
        results.append(res)
        out_path.write_text(json.dumps({"repos": results}, indent=2), encoding="utf-8")
        print(
            f"    zero-symbol {res.get('zero_symbol_in_language')}/"
            f"{res.get('files_seen_in_language')} "
            f"({res.get('zero_symbol_share')})  declarative {res.get('looks_declarative')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
