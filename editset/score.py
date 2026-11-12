"""Score `get_context(include=["references"])` against the hand-classified rename truth.

    python editset/score.py <refs.jsonl> [<checkout>]

<refs.jsonl>: one line per call, as written by editset/run_editset.py. Truth in
editset/truth/ (repowise-dev/repowise at ddf30359d; see editset/README.md).
"""
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "truth")
METHODS = {"generate", "acquire", "embed", "detect", "extract"}
REFS = sys.argv[1]
REPO = sys.argv[2] if len(sys.argv) > 2 else None  # checkout, for line text when the response omits it


def sites_of(ref):
    """Sites as dicts with path, line, kind, text; accepts the flat `sites` list and the per-file `files` map."""
    if not ref:
        return []
    if "sites" in ref:
        return ref["sites"]
    out = []
    for path, kinds in (ref.get("files") or {}).items():
        lines = None
        for kind, nums in kinds.items():
            for n in nums:
                if lines is None and REPO:
                    try:
                        lines = open(os.path.join(REPO, path), encoding="utf-8", errors="replace").read().splitlines()
                    except OSError:
                        lines = []
                text = lines[n - 1] if lines and 0 < n <= len(lines) else ""
                out.append({"path": path, "line": n, "kind": kind, "text": text})
    return out


def load(p):
    return json.load(open(os.path.join(HERE, p), encoding="utf-8"))


defs = load("defs.json")
review = load("review_verdicts.json")
rev_lines = {(x["symbol"], x["file"], int(x["line"])): x["ref"] == "yes" for x in review["lines"]}
mverd = {}
for f in ("m_detect_verdicts.json", "m_extract_verdicts.json", "m_generate_verdicts.json"):
    sym = f.split("_")[1]
    for x in load(f):
        mverd[(sym, x["path"], int(x["line"]))] = x["class"]
for x in load("m_embed_acquire_verdicts.json"):
    mverd[(x["symbol"], x["path"], int(x["line"]))] = x["class"]


def truth(sym, scope="A"):
    g = load(f"gt_{sym}.json")
    T = set()
    if sym in METHODS:
        for r in g:
            c = mverd.get((sym, r["path"], r["line"]))
            if c == "A" or (scope == "B" and c in ("A", "B")):
                T.add((r["path"], r["line"]))
        return T
    b = load(f"bind_{sym}.json")
    for r in g:
        if r["cls"] not in ("code", "strref"):
            continue
        k = (sym, r["path"], r["line"])
        if k in rev_lines:
            if rev_lines[k]:
                T.add((r["path"], r["line"]))
            continue
        v = b.get(r["path"], "")
        if v.startswith("same") and v not in ("same(mock_only)", "same+local_def"):
            T.add((r["path"], r["line"]))
    return T


def is_test(p):
    return bool(re.search(r"(^|/)(tests?|__tests__)/|\.test\.|\.spec\.|test_[^/]*\.py$|conftest", p))


rows = [json.loads(line) for line in open(REFS, encoding="utf-8") if '"i"' in line]
out = {}
for r in rows:
    sym = r["tag"]
    t = r["payload"]["targets"][sym]
    ref = t.get("references")
    listed = {}
    if ref:
        for s in sites_of(ref):
            listed[(s["path"], s["line"])] = s["kind"]
    for scope in (("A", "B") if sym in METHODS else ("A",)):
        T = truth(sym, scope)
        tp = [k for k in T if k in listed]
        nondoc = {k: v for k, v in listed.items() if v != "doc"}
        fp = [k for k in nondoc if k not in T]
        omitted = sum((ref or {}).get("sites_omitted_by_file", {}).values()) if ref and isinstance((ref or {}).get("sites_omitted_by_file"), dict) else 0
        g_cls = {(h["path"], h["line"]): h["cls"] for h in load(f"gt_{sym}.json")}
        W = re.compile(r"(?<![\w$])" + re.escape(sym) + r"(?![\w$])")
        site_text = {(s["path"], s["line"]): s["text"] for s in sites_of(ref)}
        fpk = Counter()
        for k in fp:
            if not W.search(site_text.get(k, "")):
                fpk["alias_or_noname"] += 1
            elif g_cls.get(k) == "doc":
                fpk["comment_docstring"] += 1
            elif k in g_cls:
                fpk["other_symbol_or_string"] += 1
            else:
                fpk["not_in_grep"] += 1
        key = sym if scope == "A" else sym + " (family)"
        out[key] = dict(
            true=len(T), true_tests=sum(1 for k in T if is_test(k[0])), listed=len(listed), listed_nondoc=len(nondoc),
            tp=len(tp), fp=len(fp), miss=len(T) - len(tp),
            recall=round(len(tp) / len(T), 4) if T else None,
            precision=round(len(tp) / len(nondoc), 4) if nondoc else None,
            delivered=ref is not None, complete=(ref or {}).get("complete"), sites_total=(ref or {}).get("sites_total"),
            reasons=(ref or {}).get("reasons"), resp_bytes=r["text_bytes"],
            dropped=r["payload"].get("dropped_blocks"),
            complete_wrong=(ref is not None and ref.get("complete") is True and len(tp) < len(T)),
            fp_kinds=dict(fpk),
            fp_examples=[list(k) + [listed[k]] for k in fp[:5]],
            miss_examples=[list(k) for k in sorted(T - set(listed))[:5]],
        )
json.dump(out, open(REFS.replace(".jsonl", "") + ".scored.json", "w"), indent=1)
print(f"{'symbol':28} {'true':>5} {'list':>5} {'tp':>5} {'fp':>4} {'rec':>6} {'prec':>6} deliv complete total bytes")
for k, v in out.items():
    print(f"{k:28} {v['true']:5} {v['listed']:5} {v['tp']:5} {v['fp']:4} {str(v['recall']):>6} {str(v['precision']):>6} {str(v['delivered'])[0]} {str(v['complete']):5} {str(v['sites_total']):5} {v['resp_bytes']} {v['fp_kinds']}")
