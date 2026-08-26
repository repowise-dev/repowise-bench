"""How often does `get_answer` assert completeness it cannot observe?

Sweeps every agent transcript on disk, pulls each `get_answer` response, and
measures two things:

  A. **Exclusivity claims** -- prose asserting a single site is the whole story
     ("depends entirely on X", "the only place", "solely responsible").
  B. **The #1444 signature** -- an exclusivity claim in the SAME response that
     carries a `symbol_bodies[].truncated == True`. That is the self-contradiction:
     the prose claims completeness while the payload admits it withheld a body.

(B) is fully mechanical and is the number to trust. (A) needs judgement about
whether a claim was *attributed* to something in the retrieved material, and
this script deliberately does NOT pretend to automate that -- it prints the
matched sentences so they can be read. An "unattributed exclusivity" rate
produced by regex alone would be exactly the kind of plausible number this
workstream keeps having to retract.

Detector honesty
----------------
* HIGH patterns are completeness assertions about causation. They are written to
  be precise rather than broad.
* WEAK patterns ("only", "always", "never" anywhere) are counted SEPARATELY and
  reported as an upper bound, because most are legitimate conditionals
  ("only when merge=False") rather than completeness claims.
* A positive control asserts the known B03 answer trips the HIGH detector. If it
  does not, the detector is broken and the run aborts rather than reporting a
  comfortable zero -- a check whose failure path has never fired is not a check.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "results"

# Completeness assertions about causation. Precise on purpose.
HIGH = [
    r"depends? entirely on",
    r"entirely determined by",
    r"is the only (?:place|site|function|method|reason|way|thing)",
    r"the only (?:place|site|function|method|reason) (?:that|where|which)",
    r"(?:is|are) sole?ly responsible",
    r"the sole (?:place|site|source|cause|reason)",
    r"nothing else (?:modifies|sets|touches|affects|changes)",
    r"no other (?:place|site|code|function|method) (?:modifies|sets|touches|affects|changes)",
    r"only (?:place|one place) (?:that|where|which)",
    r"exclusively (?:handled|determined|controlled) by",
]
HIGH_RE = re.compile("|".join(HIGH), re.I)

# Upper bound only. Most of these are legitimate conditionals.
WEAK_RE = re.compile(r"\b(?:only|always|never|entirely|solely|exclusively)\b", re.I)

TOOL = re.compile(r"get_answer", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def iter_answers(path: Path):
    """Yield (result_dict,) for every get_answer response in one stream."""
    ids: set = set()
    try:
        fh = path.open(encoding="utf-8", errors="replace")
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
                if b.get("type") == "tool_use" and TOOL.search(str(b.get("name", ""))):
                    ids.add(b.get("id"))
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in ids:
                    raw = b.get("content")
                    txt = (
                        raw
                        if isinstance(raw, str)
                        else "".join(
                            x.get("text", "") for x in raw if isinstance(x, dict)
                        )
                        if isinstance(raw, list)
                        else ""
                    )
                    if not txt:
                        continue
                    try:
                        obj = json.loads(txt)
                    except Exception:
                        continue
                    res = obj.get("result") if isinstance(obj, dict) else None
                    if isinstance(res, dict) and isinstance(res.get("answer"), str):
                        yield path, res


def main() -> int:
    streams = sorted(
        set(list(RESULTS.rglob("*_stream.jsonl")) + list(RESULTS.rglob("*.stream.jsonl")))
    )
    print(f"transcripts on disk : {len(streams)}")

    total = 0
    high_hits: list[tuple] = []
    weak_n = 0
    trunc_n = 0
    signature: list[tuple] = []
    by_dir: Counter = Counter()
    control_seen = False

    for sp in streams:
        for path, res in iter_answers(sp):
            total += 1
            ans = res["answer"]
            bodies = res.get("symbol_bodies") or []
            has_trunc = any(
                isinstance(b, dict) and b.get("truncated") for b in bodies
            )
            trunc_n += 1 if has_trunc else 0

            matched = [s for s in sentences(ans) if HIGH_RE.search(s)]
            if WEAK_RE.search(ans):
                weak_n += 1
            if matched:
                by_dir[path.parent.name] += 1
                high_hits.append((path, matched, has_trunc, res.get("confidence")))
                if has_trunc:
                    signature.append((path, matched))
            if "depends entirely on" in ans:
                control_seen = True

    print(f"get_answer responses: {total}")
    if not total:
        print("FAIL no get_answer responses parsed -- the reader is broken.")
        return 1

    # Positive control. Without it a clean zero is indistinguishable from a
    # detector that never fires.
    print()
    print("POSITIVE CONTROL: the known 'depends entirely on' answer")
    if not control_seen:
        print("  FAIL not found in corpus -- detector or corpus is wrong, aborting.")
        return 1
    print("  found, and HIGH detector fired on it" if any(
        "depends entirely on" in " ".join(m).lower() for _, m, _, _ in high_hits
    ) else "  FAIL found in corpus but HIGH detector did NOT fire, aborting.")
    if not any("depends entirely on" in " ".join(m).lower() for _, m, _, _ in high_hits):
        return 1

    pct = lambda n: f"{100.0 * n / total:5.1f}%"
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"  responses with a HIGH completeness claim : {len(high_hits):4d}  {pct(len(high_hits))}")
    print(f"  responses carrying a truncated body      : {trunc_n:4d}  {pct(trunc_n)}")
    print(f"  ** #1444 signature (both together)       : {len(signature):4d}  {pct(len(signature))}")
    print(f"  [upper bound] any weak token present     : {weak_n:4d}  {pct(weak_n)}")

    if by_dir:
        print()
        print("  HIGH hits by run:")
        for d, n in by_dir.most_common(12):
            print(f"    {n:3d}  {d}")

    print()
    print("=" * 70)
    print("EVERY HIGH MATCH, for reading (attribution is NOT automated)")
    print("=" * 70)
    for path, matched, has_trunc, conf in high_hits:
        print(f"\n--- {path.parent.name}/{path.name}  conf={conf}  truncated_body={has_trunc}")
        for s in matched:
            print(f"    > {s[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
