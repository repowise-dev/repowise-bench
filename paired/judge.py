"""One blind judge per task over every arm's answer.

    python paired/judge.py --tasks paired/tasks/<set>.json --out OUT [--model haiku]

The judge is a Claude Code session in the task's checkout (Read/Grep/Glob only),
so it verifies claims against the code, not only against the gold notes. It sees
the task, the gold notes and the answers (and diffs, for edit tasks) under
opaque ids; the id -> run map is written to OUT/judge/map.json, outside the
checkout, and never shown. Arms, tool calls and confidence lines are stripped.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_paired import anthropic_env, claude_exe, repo_path  # noqa: E402

JUDGE = """You are grading answers to one coding task about the repository in your working directory.
Verify every claim you rely on against the code with Read/Grep/Glob. The GOLD notes list what a
correct answer must contain; an answer may add true detail. Grade each item:
correct = every Must point present and no false claim that matters; partial = some Must points
missing or a minor false claim; wrong = the main point missing or a material false claim.
{edit_rule}
TASK
{task}

GOLD
{gold}

ITEMS
{items}

Reply with only a JSON list, one object per item: {{"id": "...", "grade": "correct|partial|wrong", "note": "<one sentence>"}}
"""


_ARM_TELLS = re.compile(r"mcp__repowise__\w+|\b(get_answer|get_context|search_codebase|get_symbol|repowise)\b", re.I)


def strip_answer(text: str) -> str:
    """Drop the confidence line and every mention of the tool or its calls, which would reveal the arm."""
    text = re.sub(r"\n?CONFIDENCE:.*", "", text or "", flags=re.S).strip()
    return _ARM_TELLS.sub("[tool]", text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--repos-dir", default="repos")
    ap.add_argument("--keys", default=str(Path.home() / "Desktop/repowise/backend/.env"))
    ap.add_argument("--seed", type=int, default=20261113)
    a = ap.parse_args()
    spec = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    root = Path(a.out)
    jd = root / "judge"
    jd.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    mp_path = jd / "map.json"
    mapping = json.loads(mp_path.read_text()) if mp_path.exists() else {}
    used = {int(k[1:]) for k in mapping}
    pool = [n for n in range(1000, 10000) if n not in used]
    rng.shuffle(pool)
    cfg = root / "cfg" / "judge"
    cfg.mkdir(parents=True, exist_ok=True)
    cost = 0.0
    for t in spec["tasks"]:
        out_p = jd / f"verdict_{t['id']}.json"
        metas = sorted(root.glob(f"runs/{t['id']}__*/meta.json"))
        if not metas:
            continue
        if out_p.exists():
            judged = {v.get("run") for v in json.loads(out_p.read_text(encoding="utf-8")).get("verdicts", [])}
            if judged >= {mp.parent.name for mp in metas}:
                continue  # every finished run already has a grade; a new run re-judges the whole task
        items = []
        for mp in metas:
            m = json.loads(mp.read_text(encoding="utf-8"))
            oid = f"e{pool.pop()}"
            mapping[oid] = mp.parent.name
            item = {"id": oid, "answer": strip_answer(m.get("answer", ""))}
            diff = mp.parent / "diff.patch"
            if diff.exists():
                item["diff"] = diff.read_text(encoding="utf-8")[:20000]
            items.append(item)
        rng.shuffle(items)
        mp_path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        edit_rule = ("For edit tasks grade the DIFF: the change must do what the task asks and nothing that breaks the code."
                     if t.get("kind") == "edit" else "")
        prompt = JUDGE.format(task=t["prompt"], gold=t["gold"], edit_rule=edit_rule,
                              items=json.dumps(items, indent=1, ensure_ascii=False))
        (jd / f"packet_{t['id']}.txt").write_text(prompt, encoding="utf-8")
        repo = str(repo_path(a.repos_dir, t["repo"]))
        cmd = [claude_exe(), "-p", "--model", a.model, "--output-format", "json", "--tools", "Read,Grep,Glob",
               "--allowedTools", "Read Grep Glob", "--permission-mode", "dontAsk", "--max-turns", "30",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        r = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", capture_output=True, cwd=repo,
                           env=anthropic_env(cfg, Path(a.keys)), timeout=1200)
        try:
            res = json.loads(r.stdout)
        except ValueError:
            print(t["id"], "judge output unreadable", r.stderr[-300:])
            continue
        cost += res.get("total_cost_usd") or 0
        m = re.search(r"\[.*\]", res.get("result", ""), re.S)
        try:
            verdicts = [v for v in (json.loads(m.group(0)) if m else []) if isinstance(v, dict) and v.get("grade")]
        except ValueError:
            verdicts = []
        if not verdicts:
            print(t["id"], "judge reply had no verdicts; left ungraded", flush=True)
            continue
        for v in verdicts:
            v["grade"] = str(v["grade"]).lower()
            v["run"] = mapping.get(v.get("id"))
        out_p.write_text(json.dumps({"verdicts": verdicts, "cost_usd": res.get("total_cost_usd")}, indent=2), encoding="utf-8")
        print(t["id"], [(v["run"], v["grade"]) for v in verdicts], f"${cost:.3f}", flush=True)


if __name__ == "__main__":
    main()
