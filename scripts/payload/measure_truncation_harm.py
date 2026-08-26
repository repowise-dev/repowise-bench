"""How often does a truncated body withhold something the answer talks about?

This decides how blunt the confidence cap in item (A) is allowed to be.

A blanket "any truncated body caps confidence" would fire on every response that
truncated anything, and truncation is common. If most truncations withhold
nothing the answer relies on, that cap destroys the calibration of `high` for no
gain. If most of them withhold a symbol the answer names as part of the
mechanism, a targeted gate is both cheaper and better.

Method, per get_answer response found in the agent transcripts on disk:

  1. take each ``symbol_bodies`` entry with ``truncated: true`` and its
     ``continuation`` pointer (``path:start-end``);
  2. read those withheld lines from the real checkout;
  3. extract the ``def`` / ``class`` names defined in the withheld range;
  4. ask whether the answer prose NAMES any of them.

(4) being true is the failure shape from the brief: the answer discusses a
symbol whose body the payload withheld, while reporting high confidence.

Not a proxy for "the answer was wrong" — it is a proxy for "the payload could
not support the claim it made". That is the property a confidence signal is
supposed to track.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
RESULTS = BENCH / "results"

# Which checkout to read withheld lines from, by cell marker in the stream path.
TREE_FOR_CELL = {
    "cellB-hermes": Path(r"C:\Users\ragha\Desktop\bakeoff\se-c0bare-hermes-pf"),
    "cellA-rich": Path(r"C:\Users\ragha\Desktop\bakeoff\se-c0bare-rich"),
}

DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M)
CONT_RE = re.compile(r"^(.*?):(\d+)-(\d+)$")


def tree_for(path: Path) -> Path | None:
    s = str(path)
    for marker, tree in TREE_FOR_CELL.items():
        if marker in s:
            return tree if tree.is_dir() else None
    return None


def iter_answers(p: Path):
    ids: set = set()
    try:
        fh = p.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            msg = ev.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if b.get("type") == "tool_use" and "get_answer" in str(b.get("name", "")):
                    ids.add(b.get("id"))
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in ids:
                    raw = b.get("content")
                    txt = (raw if isinstance(raw, str)
                           else "".join(x.get("text", "") for x in raw if isinstance(x, dict))
                           if isinstance(raw, list) else "")
                    if not txt:
                        continue
                    try:
                        res = json.loads(txt).get("result")
                    except Exception:
                        continue
                    if isinstance(res, dict) and isinstance(res.get("answer"), str):
                        yield res


def _names_in_code_context(answer: str, name: str) -> bool:
    """Does the answer refer to *name* AS CODE, not as an English word?

    A bare word-boundary match is useless here and inflates the harm rate badly.
    Withheld symbols include `on`, `line`, `input`, `width`, `join` and `fit`,
    all of which are ordinary English that appears in any prose answer, so
    ``\\bon\\b`` matches "based on the excerpts" and scores a harmless truncation
    as harmful. Measured: the naive matcher reported 82.6%.

    Require a code context instead: backticked, called, attribute-accessed, or
    qualified. That is how the synthesiser actually writes symbol references
    (the prompt tells it to cite symbols and paths).
    """
    n = re.escape(name)
    patterns = (
        rf"`[^`]*\b{n}\b[^`]*`",   # inside backticks, e.g. `_validate` or `Todo.write`
        rf"\b{n}\s*\(",            # called: name(
        rf"\.{n}\b",               # attribute: obj.name
        rf"\b{n}\s*=",             # assigned
    )
    return any(re.search(p, answer) for p in patterns)


def withheld_names(tree: Path, cont: str) -> list[str]:
    m = CONT_RE.match(cont or "")
    if not m:
        return []
    rel, a, b = m.group(1), int(m.group(2)), int(m.group(3))
    f = tree / rel
    if not f.is_file():
        return []
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return sorted(set(DEF_RE.findall("\n".join(lines[a - 1:b]))))


def main() -> int:
    streams = sorted(set(list(RESULTS.rglob("*_stream.jsonl"))
                         + list(RESULTS.rglob("*.stream.jsonl"))))
    total = trunc = 0
    harmful = 0
    conf_when_harmful: Counter = Counter()
    conf_when_trunc: Counter = Counter()
    examples: list[tuple] = []
    no_tree = 0

    for sp in streams:
        tree = tree_for(sp)
        for res in iter_answers(sp):
            total += 1
            bodies = [b for b in (res.get("symbol_bodies") or [])
                      if isinstance(b, dict) and b.get("truncated")]
            if not bodies:
                continue
            trunc += 1
            conf_when_trunc[res.get("confidence")] += 1
            if tree is None:
                no_tree += 1
                continue
            ans = res["answer"]
            hit_names: list[str] = []
            for b in bodies:
                for name in withheld_names(tree, b.get("continuation") or ""):
                    if _names_in_code_context(ans, name):
                        hit_names.append(name)
            if hit_names:
                harmful += 1
                conf_when_harmful[res.get("confidence")] += 1
                if len(examples) < 8:
                    examples.append((sp.parent.name, res.get("confidence"),
                                     sorted(set(hit_names))[:4]))

    print(f"get_answer responses           : {total}")
    print(f"  with a truncated body        : {trunc}"
          + (f"  ({100.0 * trunc / total:.1f}%)" if total else ""))
    print(f"  (no checkout to read)        : {no_tree}")
    gradable = trunc - no_tree
    print(f"  gradable                     : {gradable}")
    if gradable:
        print(f"  ** withheld a symbol the answer NAMES : {harmful}"
              f"  ({100.0 * harmful / gradable:.1f}% of gradable)")
    print()
    print(f"confidence when truncated : {dict(conf_when_trunc)}")
    print(f"confidence when HARMFUL   : {dict(conf_when_harmful)}")
    if examples:
        print("\nexamples (run, confidence, withheld symbols the answer names):")
        for e in examples:
            print(f"   {e[0][:52]:52s} {str(e[1]):7s} {e[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
