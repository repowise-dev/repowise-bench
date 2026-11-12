"""Render Table 1 (summary per repository, then every row) from a run directory.

    python table1/render.py OUT > TABLE1.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SIZE = {"flask": "small", "cobra": "small", "hono": "mid", "gson": "mid", "django": "large", "nest": "large"}


def main() -> None:
    out = Path(sys.argv[1])
    summ = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    p = print
    p("| repository | size | rows | median | pooled | lowest row | needed file present | median answer tokens | median grep-opened tokens | bar (>= 100x median) |")
    p("|---|---|---:|---:|---:|---:|---|---:|---:|---|")
    for repo, s in summ.items():
        if s["median_ratio"] is None:
            p(f"| {repo} | {SIZE.get(repo, '?')} | {s['rows']} | no answered rows | | | | | | miss |")
            continue
        bar = "pass" if s["median_ratio"] >= 100 else "miss"
        p(f"| {repo} | {SIZE.get(repo, '?')} | {s['rows']} | {s['median_ratio']:,}x | {s['pooled_ratio']:,}x | {s['worst_ratio']:,}x | "
          f"{s['gold_present']} | {s['median_answer_tokens']:,.0f} | {s['median_grep_file_tokens']:,.0f} | {bar} |")
    p("\n### Every row\n")
    p("| repo | id | tool | call | answer tok | grep files | grep-opened tok | ratio | vs matched lines | vs gold file | needed file present | s |")
    p("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|")
    for f in sorted((out / "raw").glob("*.jsonl"), key=lambda f: list(SIZE).index(f.stem) if f.stem in SIZE else 99):
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            call = next(iter(r["args"].values()))
            call = call if isinstance(call, str) else ",".join(call)
            gp = {True: "yes", False: "no", None: "n/a"}[r["gold_present"]]
            vg = f"{r['ratio_vs_gold_file']}x" if r["ratio_vs_gold_file"] is not None else "n/a"
            p(f"| {r['repo']} | {r['id']} | {r['tool']} | {call[:70]} | {r['answer_tokens']:,} | {r['grep_files']} | "
              f"{r['grep_file_tokens']:,} | {r['ratio']}x | {r['ratio_vs_lines']}x | {vg} | {gp} | {r['wall_s']} |")


if __name__ == "__main__":
    main()
