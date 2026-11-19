"""Grade and tabulate the per-model bench.

    python analyze.py ../runs/<model> [...more model dirs] [--md out.md] [--csv rows.csv]

Grading is deterministic: each question's `must` items are regexes the final answer has to
match (case-insensitive). quality = share of must items matched; correct = all matched.
Pairing: bare rN with rw rN on the same question. Per-question differences are averaged over
reps, then over questions; the 95% interval is a bootstrap over questions (10,000 resamples).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shlex
import statistics as st
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
SHELL_READ = re.compile(r"^\s*(cat|head|tail|sed|less|more|nl|bat|type|Get-Content|gc|awk)\b")
PATHLIKE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}$")


def questions() -> dict:
    out = {}
    for f in (BENCH / "questions").glob("*.json"):
        for q in json.loads(f.read_text(encoding="utf-8")).get("questions", []) if isinstance(json.loads(f.read_text(encoding="utf-8")), dict) else []:
            out[q["id"]] = q
    return out


def answer_section(text: str) -> str:
    m = re.search(r"ANSWER:(.*?)(?:\n\s*CONFIDENCE:|$)", text or "", re.S)
    return m.group(1) if m else (text or "")


def grade(q: dict, text: str) -> tuple[float, bool]:
    ans = answer_section(text)
    hits = sum(1 for m in q["must"] if re.search(m["regex"], ans, re.I))
    return hits / len(q["must"]), hits == len(q["must"])


def norm(path: str) -> str:
    """Repo-relative, lower-case, forward slashes, so the same file counts once whatever the spelling."""
    p = path.replace("\\", "/").lower()
    m = re.search(r"/(?:repos|bare)/[^/]+/(.+)$", p)
    p = m.group(1) if m else p
    return re.sub(r"^(\./)+", "", p)


def files_opened(stream: Path) -> int:
    """Distinct files opened: Read paths plus file arguments of shell read commands (cat/sed/head/...)."""
    seen = set()
    for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "assistant":
            continue
        for c in ev.get("message", {}).get("content", []) or []:
            if c.get("type") != "tool_use":
                continue
            inp = c.get("input") or {}
            if c.get("name") == "Read":
                seen.add(norm(str(inp.get("file_path", ""))))
            elif c.get("name") in ("Bash", "PowerShell"):
                for part in re.split(r"&&|;|\|\|", str(inp.get("command", ""))):
                    seg = part.split("|")[0]
                    if not SHELL_READ.match(seg):
                        continue
                    try:
                        toks = shlex.split(seg, posix=True)
                    except ValueError:
                        toks = seg.split()
                    for t in toks[1:]:
                        if not t.startswith("-") and PATHLIKE.search(t):
                            seen.add(norm(t))
    return len(seen)


def load(model_dir: Path, qs: dict) -> list[dict]:
    rows = []
    for meta in sorted(model_dir.glob("runs/*/meta.json")):
        m = json.loads(meta.read_text(encoding="utf-8"))
        q = qs[m["task"]]
        score, ok = grade(q, m.get("answer", ""))
        mu_out = m["tokens"]["output"]
        rows.append({
            "model": model_dir.name, "q": m["task"], "repo": m["repo"], "kind": m["kind"], "arm": m["arm"],
            "rep": m["rep"], "output": mu_out, "total_tokens": m["total_tokens"], "tool_calls": m["tool_calls"],
            "files": m["files_opened"] if "files_opened" in m else files_opened(meta.parent / "stream.jsonl"), "wall": m["wall_s"], "cost": m.get("cost_usd") or 0,
            "quality": score, "correct": ok, "rw_calls": m["rw_calls"], "api_calls": m["api_calls"],
            "model_id": m.get("model"),
        })
    return rows


def boot_ci(vals: list[float], n: int = 10000, seed: int = 7) -> tuple[float, float]:
    if len(vals) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(st.fmean(rng.choices(vals, k=len(vals))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


METRICS = ["output", "tool_calls", "files", "wall", "total_tokens", "cost", "quality"]


def summarise(rows: list[dict]) -> dict:
    by = {}
    for r in rows:
        by.setdefault((r["q"], r["rep"]), {})[r["arm"]] = r
    pairs = [(v["bare"], v["rw"]) for v in by.values() if "bare" in v and "rw" in v]
    perq: dict[str, list] = {}
    for b, w in pairs:
        perq.setdefault(b["q"], []).append((b, w))
    s = {"n_pairs": len(pairs), "n_questions": len(perq)}
    for k in METRICS:
        bm = [st.fmean(b[k] for b, _ in v) for v in perq.values()]
        wm = [st.fmean(w[k] for _, w in v) for v in perq.values()]
        if not bm:
            continue
        d = [x - y for x, y in zip(wm, bm)]
        lo, hi = boot_ci(d)
        base = st.fmean(bm)
        s[k] = {"bare": base, "rw": st.fmean(wm), "diff": st.fmean(d), "lo": lo, "hi": hi,
                "pct": (st.fmean(d) / base * 100) if base else float("nan"),
                "pct_lo": (lo / base * 100) if base else float("nan"), "pct_hi": (hi / base * 100) if base else float("nan")}
    s["leaner_q"] = sum(1 for v in perq.values() if st.fmean(w["output"] for _, w in v) < st.fmean(b["output"] for b, _ in v))
    s["correct_bare"] = sum(b["correct"] for b, _ in pairs)
    s["correct_rw"] = sum(w["correct"] for _, w in pairs)
    s["rw_used"] = sum(1 for _, w in pairs if w["rw_calls"] > 0)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--csv")
    ap.add_argument("--json")
    a = ap.parse_args()
    qs = questions()
    allrows, out = [], {}
    for d in a.dirs:
        rows = load(Path(d), qs)
        allrows += rows
        out[Path(d).name] = summarise(rows)
        s = out[Path(d).name]
        print(f"\n== {Path(d).name}: {s['n_pairs']} pairs over {s['n_questions']} questions; repowise used in {s['rw_used']}/{s['n_pairs']}; leaner on {s['leaner_q']}/{s['n_questions']}; correct bare {s['correct_bare']} rw {s['correct_rw']}")
        for k in METRICS:
            if k in s:
                v = s[k]
                print(f"  {k:13} bare {v['bare']:10.2f}  rw {v['rw']:10.2f}  diff {v['diff']:+9.2f} [{v['lo']:+.2f}, {v['hi']:+.2f}]  {v['pct']:+6.1f}% [{v['pct_lo']:+.1f}, {v['pct_hi']:+.1f}]")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(allrows[0].keys()))
            w.writeheader()
            w.writerows(allrows)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
