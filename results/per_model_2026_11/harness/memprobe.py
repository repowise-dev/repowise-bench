"""Zero-tool memory probe: can a model answer a bench question with no tools and no checkout?

    python memprobe.py --models fable,opus,... --ids FOR-01,OKF-02,LEV-01 --out ../probe/memory
Grades with the same must-regex rule as grade.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from driver import BENCH, DESC, REPOS, child_env, claude_exe

ASK = ("The GitHub repository {repo} is described as: {desc}. Without any tools, from what you know, "
       "answer this question about its source code. If you do not know, say so.\n\n{q}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    empty = BENCH / "probe" / "empty"
    urls = {"foreman": "thruwire/foreman", "okf-agent-memory": "okf-memory/okf-agent-memory",
            "leviathan": "elstongun/leviathan"}
    qs = {}
    for repo in REPOS:
        for q in json.loads((BENCH / "questions" / f"{repo}.json").read_text(encoding="utf-8"))["questions"]:
            qs[q["id"]] = (q, repo)
    jobs = [(m, i) for m in a.models.split(",") for i in a.ids.split(",")]

    def one(job):
        m, i = job
        f = out / f"{m}__{i}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        q, repo = qs[i]
        prompt = ASK.format(repo=urls[repo], desc=DESC[repo], q=q["question"])
        p = subprocess.run([claude_exe(), "-p", "--model", m, "--output-format", "json", "--tools", "",
                            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config",
                            '{"mcpServers":{}}', "--disable-slash-commands", "--no-session-persistence"],
                           input=prompt, text=True, encoding="utf-8", capture_output=True, cwd=str(empty),
                           env=child_env(), timeout=300)
        ans = json.loads(p.stdout).get("result", "")
        hit = [mu["label"] for mu in q["must"] if re.search(mu["regex"], ans, re.I)]
        r = {"model": m, "id": i, "must_hit": hit, "must_n": len(q["must"]), "answer": ans}
        f.write_text(json.dumps(r, indent=1), encoding="utf-8")
        return r

    with ThreadPoolExecutor(max_workers=2) as ex:
        for r in ex.map(one, jobs):
            print(f"{r['model']:20} {r['id']}: {len(r['must_hit'])}/{r['must_n']} {r['must_hit']}", flush=True)


if __name__ == "__main__":
    main()
