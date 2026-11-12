"""Table 1: tokens in a Repowise core-tool answer vs tokens in the files an agent
would open after grepping the question's key terms.

    python table1/run_table1.py --repos-dir REPOS --python PY --out OUT [--only flask,cobra]

REPOS/<name> is a full clone at the indexed commit with its keyless `.repowise/`
index (built by `repowise init --no-prose`). PY is a Python that can import
`repowise` and `mcp`. The server runs keyless: no provider key in its environment,
an empty global config dir, and the answer cache off.

Definitions (printed under the table):
- answer tokens: the tool result's text content, cl100k_base tokens.
- grep-opened files: tracked text files matching every key term
  (`git grep -l -I -i -w -F`, one pass per term, intersected; if the
  intersection is empty, the first term alone). Tokens: each file in full.
- ratio: grep-opened tokens / answer tokens.
- needed file present: a gold path appears in the answer (rows with a gold file).
Two stricter baselines are recorded per row as well: the matched grep lines
themselves (`git grep -n` output) and the gold file read in full.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import tiktoken

ENC = tiktoken.get_encoding("cl100k_base")
KEY_ENVS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_KEY",
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY")
HERE = Path(__file__).resolve().parent


def ntok(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def grep_files(repo: Path, terms: list[str]) -> list[str]:
    sets = []
    for t in terms:
        out = git(repo, "grep", "-l", "-z", "-I", "-i", "-w", "-F", "-e", t)  # -z: paths unquoted
        sets.append({f for f in out.split("\0") if f})
    files = set.intersection(*sets) if sets else set()
    if not files and sets:
        files = sets[0]
    return sorted(files)


_tok_cache: dict[str, int] = {}


def file_tokens(repo: Path, rel: str) -> int:
    key = f"{repo}|{rel}"
    if key not in _tok_cache:
        try:
            _tok_cache[key] = ntok((repo / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            _tok_cache[key] = 0
    return _tok_cache[key]


def grep_line_tokens(repo: Path, terms: list[str], files: list[str]) -> int:
    if not files:
        return 0
    args = ["grep", "-n", "-z", "-I", "-i", "-w", "-F"]
    for t in terms:
        args += ["-e", t]
    # Whole-repo grep filtered to the opened files: no path list on the command line (Windows length limit).
    keep = set(files)
    lines = []
    for rec in git(repo, *args).split("\n"):
        path, _, rest = rec.partition("\0")
        if path in keep:
            lines.append(path + ":" + rest.replace("\0", ":"))
    return ntok("\n".join(lines))


def keyless_env(cfg_dir: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENVS}
    env.update(DO_NOT_TRACK="1", REPOWISE_TELEMETRY="0", PYTHONIOENCODING="utf-8",
               REPOWISE_ANSWER_DISABLE_CACHE="1", REPOWISE_CONFIG_DIR=cfg_dir)
    return env


async def call_all(py: str, repo: Path, rows: list[dict], timeout: float) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cfg = tempfile.mkdtemp(prefix="t1cfg_")
    params = StdioServerParameters(command=py, args=["-m", "repowise.cli.main", "mcp", str(repo), "--no-workspace"],
                                   env=keyless_env(cfg), cwd=str(repo))
    out = []
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                # One warm-up call so the first row does not carry server start-up.
                await s.call_tool("search_codebase", {"query": "main"})
                for row in rows:
                    t = time.perf_counter()
                    try:
                        res = await asyncio.wait_for(s.call_tool(row["tool"], row["args"]), timeout=timeout)
                        text = "".join(getattr(c, "text", "") or "" for c in res.content)
                        err = bool(res.isError)
                    except Exception as e:  # recorded, never dropped
                        text, err = f"EXCEPTION {type(e).__name__}: {e}", True
                    out.append({"text": text, "is_error": err, "wall_s": round(time.perf_counter() - t, 3)})
    return out


def measure_repo(name: str, repo: Path, rows: list[dict], py: str, timeout: float) -> list[dict]:
    head = git(repo, "rev-parse", "HEAD").strip()
    results = asyncio.run(call_all(py, repo, rows, timeout))
    recs = []
    for row, res in zip(rows, results):
        files = grep_files(repo, row["terms"])
        opened = sum(file_tokens(repo, f) for f in files)
        ans = ntok(res["text"])
        norm = res["text"].replace("\\\\", "/").replace("\\", "/")
        gold = row.get("gold") or []
        gold_present = any(g in norm for g in gold) if gold else None
        gold_full = file_tokens(repo, gold[0]) if gold else None
        lines = grep_line_tokens(repo, row["terms"], files)
        recs.append({
            "repo": name, "commit": head, "id": row["id"], "tool": row["tool"], "args": row["args"],
            "terms": row["terms"], "answer_tokens": ans, "answer_bytes": len(res["text"].encode()),
            "is_error": res["is_error"], "wall_s": res["wall_s"],
            "grep_files": len(files), "grep_file_tokens": opened,
            "ratio": round(opened / ans, 1) if ans else None,
            "grep_line_tokens": lines, "ratio_vs_lines": round(lines / ans, 2) if ans else None,
            "gold": gold, "gold_present": gold_present, "gold_file_tokens": gold_full,
            "ratio_vs_gold_file": round(gold_full / ans, 2) if (ans and gold_full) else None,
            "answer_text": res["text"],
        })
    return recs


def summarise(recs: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in recs:
        by.setdefault(r["repo"], []).append(r)
    out = {}
    for repo, rs in by.items():
        ok = [r for r in rs if not r["is_error"]]  # an errored call is reported, never a ratio
        ratios = [r["ratio"] for r in ok if r["ratio"] is not None]
        gp = [r["gold_present"] for r in ok if r["gold_present"] is not None]
        out[repo] = {
            "rows": len(rs), "commit": rs[0]["commit"],
            "median_ratio": round(statistics.median(ratios), 1) if ratios else None,
            "pooled_ratio": round(sum(r["grep_file_tokens"] for r in ok) / max(1, sum(r["answer_tokens"] for r in ok)), 1),
            "worst_ratio": min(ratios) if ratios else None,
            "median_answer_tokens": statistics.median(r["answer_tokens"] for r in ok) if ok else None,
            "median_grep_file_tokens": statistics.median(r["grep_file_tokens"] for r in ok) if ok else None,
            "gold_present": f"{sum(gp)} of {len(gp)}",
            "errors": sum(1 for r in rs if r["is_error"]),
            "median_wall_s": round(statistics.median(r["wall_s"] for r in rs), 2),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--questions", default=str(HERE / "questions.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    q = json.loads(Path(a.questions).read_text(encoding="utf-8"))
    out = Path(a.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    for name, rows in q.items():
        if name.startswith("_") or (a.only and name not in a.only.split(",")):
            continue
        recs = measure_repo(name, Path(a.repos_dir) / name, rows, a.python, a.timeout)
        with open(out / "raw" / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(name, json.dumps(summarise(recs)[name]), flush=True)
    every = [json.loads(line) for p in sorted((out / "raw").glob("*.jsonl"))
             for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    (out / "summary.json").write_text(json.dumps(summarise(every), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
