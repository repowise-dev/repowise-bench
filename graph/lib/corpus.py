"""The pinned corpus, read from `corpus.lock`, and the one selection rule.

`run_corpus.py` used to carry a hardcoded six-repository list. That constant was
the single thing standing between the pinned corpus and every per-language claim
on the published page: six repositories are typescript x2, java x1, csharp x1,
python x1, go x1, so four of five languages rested on one repository each, which
is exactly what the three-kinds rule forbids.

Selection lives here rather than in either script because the measurement sweep
and the artifact prebuild have to agree on which repositories exist. If they
drift, the sweep asks for peer artifacts the prebuild never built, and a missing
cache entry reads as a slow run rather than as a mismatch.

## The rules, in order

* **Only rows with a `kind`.** `corpus.lock` pins all 98 checkouts in
  `test-repos/`; the ones carrying `library` / `application` / `framework` are
  the designed corpus and the rest are the breadth pool.
* **`usable: false` is dropped.** `autogpt`'s checkout carries 4,278 staged
  source deletions and is not a checkout of its pin at all.
* **The size cap must never delete a per-language claim.** Repositories over
  `max_files` are skipped, *except* where a `(language, kind)` slot has no
  under-cap member at all -- then its smallest member is kept however large,
  because the alternative is a G7 table with no Rust framework row that still
  looks complete.
* **Cheapest first**, so a broken arm shows up in the first ten minutes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GRAPH = Path(__file__).resolve().parents[1]
LOCK = GRAPH / "corpus" / "corpus.lock"

# Session 3's cap, from measurement rather than taste: graphify took 176s on dub
# against 5s on gitleaks, and size buys nothing when the denominators are already
# tens of thousands of call expressions.
DEFAULT_MAX_FILES = 2000


@dataclass(frozen=True)
class Selection:
    """What a run will measure, and what it consciously left out."""

    rows: list[dict]
    oversize: list[dict]          # skipped by the cap
    kept_oversize: list[dict]     # over the cap, kept as a slot's only member

    def names(self) -> list[str]:
        return [r["name"] for r in self.rows]

    def describe(self) -> list[str]:
        """Lines a caller must print. A silent cap reads as full coverage."""
        out = [f"{len(self.rows)} repositories from {LOCK.name}"]
        if self.kept_oversize:
            out.append(
                "kept over the cap as the only repository in its (language, kind): "
                + ", ".join(
                    f"{r['name']}({r['files']}, {r['language']}/{r['kind']})"
                    for r in sorted(self.kept_oversize, key=lambda r: r["files"])
                )
            )
        if self.oversize:
            out.append(
                "skipped over the size cap: "
                + ", ".join(f"{r['name']}({r['files']})" for r in self.oversize)
            )
        return out


def load(lock: Path | str = LOCK) -> dict:
    return json.loads(Path(lock).read_text(encoding="utf-8"))


def select(
    *,
    lock: Path | str = LOCK,
    repos: str = "",
    kinds: str = "",
    languages: str = "",
    max_files: int = DEFAULT_MAX_FILES,
) -> Selection:
    """The repositories a run should measure. See the module docstring."""
    rows = [r for r in load(lock)["repos"] if r.get("usable", True)]

    if repos and repos != "all":
        want = set(repos.split(","))
        rows = [r for r in rows if r["name"] in want]
        # An explicit list is an explicit choice; the cap does not override it.
        rows.sort(key=lambda r: r["files"])
        return Selection(rows=rows, oversize=[], kept_oversize=[])

    rows = [r for r in rows if r.get("kind")]
    if kinds:
        want = set(kinds.split(","))
        rows = [r for r in rows if r.get("kind") in want]
    if languages:
        want = set(languages.split(","))
        rows = [r for r in rows if r.get("language") in want]

    under = {r["name"] for r in rows if r["files"] <= max_files}
    covered = {(r["language"], r["kind"]) for r in rows if r["name"] in under}
    kept_oversize = [
        min((r for r in rows if (r["language"], r["kind"]) == slot), key=lambda r: r["files"])
        for slot in sorted({(r["language"], r["kind"]) for r in rows} - covered)
    ]
    keep = under | {r["name"] for r in kept_oversize}

    oversize = sorted((r for r in rows if r["name"] not in keep), key=lambda r: r["files"])
    rows = sorted((r for r in rows if r["name"] in keep), key=lambda r: r["files"])
    return Selection(rows=rows, oversize=oversize, kept_oversize=kept_oversize)


def language_coverage(rows: list[dict]) -> dict[str, dict]:
    """Kinds present per language, so a run can say where it is still n=1.

    A language with one repository is not a language row. G7 has to print this
    beside its table or a single-repository language reads with the same weight
    as one measured three ways.
    """
    out: dict[str, dict] = {}
    for r in rows:
        lang = r["language"] or "unknown"
        slot = out.setdefault(lang, {"repos": [], "kinds": []})
        slot["repos"].append(r["name"])
        if r["kind"] not in slot["kinds"]:
            slot["kinds"].append(r["kind"])
    for lang, slot in out.items():
        slot["n"] = len(slot["repos"])
        slot["three_kinds"] = len(slot["kinds"]) >= 3
    return out
