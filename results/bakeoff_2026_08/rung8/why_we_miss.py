"""Why does repowise miss files CodeGraph finds? Four hypotheses, one probe.

Rung 8 dev measured our arm surfacing a gold file on 19 of 68 instances against
CodeGraph's 54 of 70, and the shape of our hits is the diagnostic lead: 14 of
the 19 were at **rank 1** and the deepest was rank 5. Nothing sits mid-list. So
ordering is not the failure. Getting the file into the candidate pool is.

Four causes fit that shape and they have different fixes, so this probe
separates them on instances where we missed and CodeGraph hit:

  H1 candidate budget   we offer 5 files, they offer 7-90
                        -> test: is gold in search_codebase's top 50?
  H2 query shape        ContextBench asks with a raw bug report, prose and
                        noisy; our retrieval may want "how does X work"
                        -> test: does a bare identifier find it instantly?
  H3 representation     our pages are prose *about* a file; a graph tool
                        matches raw symbols. Better data for explanation can
                        be worse data for localization.
                        -> test: page exists and neither query finds it
  H4 ingestion          the gold file was never indexed
                        -> test: is there a page whose target_path is gold?

Reading the output:

  page_present false                  -> H4, ingestion gap
  rank50 between 11 and 50            -> H1, budget only
  rank50 miss, ident_rank hit         -> H2, query shape
  rank50 miss, ident_rank miss, page  -> H3, representation. The expensive one.
  path_rank miss                      -> worse than H3: we cannot find a file
                                         when handed its own path

$0. No LLM at query time except the get_answer row, which is kept because it is
the row that scored 0.000 and its 5 paths are the thing being explained.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("r8", HERE / "rung8_runner.py")
r8 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(r8)
c8, r5 = r8.c8, r8.r5

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

TREES = r8.TREES


def gold_symbols(instance_id: str) -> list[str]:
    """Identifier names declared in the gold spans, for the H2 query."""
    import pandas as pd

    df = pd.read_parquet(c8.PARQUET)
    row = df[df.instance_id == instance_id].iloc[0]
    names: list[str] = []
    for span in json.loads(row.gold_context):
        for m in re.finditer(
            r"^\s*(?:func|def|class|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)",
            span.get("content") or "", re.M,
        ):
            if m.group(1) not in names:
                names.append(m.group(1))
    return names[:5]


def relpath(gold: str) -> str:
    """Repo-relative path from a ContextBench gold path.

    Multi-SWE-Bench gold paths carry a container prefix, `/workspace/
    cli__cli__0.1/pkg/...`, and stripping only up to `/workspace/` leaves the
    `cli__cli__0.1/` component behind. The first version of this probe did
    exactly that and then reported that repowise could not find a file when
    handed its own path, which was a probe bug wearing the costume of a product
    bug: the path it handed over did not exist in the repo.
    """
    p = gold.split("/workspace/")[-1].lstrip("/")
    head = p.split("/", 1)
    if len(head) == 2 and "__" in head[0]:
        return head[1]
    return p


def page_for(tree: Path, gold: str) -> dict:
    """Is the gold file in the wiki at all, and what does its page look like?"""
    db = tree / ".repowise" / "wiki.db"
    if not db.exists():
        return {"page_present": None, "note": "no wiki.db"}
    rel = gold.split("/workspace/")[-1]
    rel = rel.split("/", 1)[-1] if rel.startswith("cli__") or rel.startswith("django__") else rel
    con = sqlite3.connect(str(db))
    try:
        rows = list(con.execute(
            "select id, page_type, length(content), length(summary) "
            "from wiki_pages where target_path = ? or target_path like ?",
            (rel, "%" + rel),
        ))
        nsym = list(con.execute(
            "select count(*) from wiki_symbols where file_path = ? or file_path like ?",
            (rel, "%" + rel),
        ))[0][0]
    finally:
        con.close()
    return {
        "page_present": bool(rows),
        "pages": [{"id": r[0], "type": r[1], "content_chars": r[2], "summary_chars": r[3]}
                  for r in rows[:3]],
        "symbols_indexed": nsym,
    }


def rank_of(paths: list[str], gold_files: list[str]) -> int | None:
    for i, p in enumerate(paths, 1):
        if any(r5.path_matches(p, g) for g in gold_files):
            return i
    return None


async def probe_one(inst: dict, tree: Path) -> dict:
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "DO_NOT_TRACK": "1", "REPOWISE_SKIP_EDITOR_SETUP": "1"})
    if not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = c8._openai_key() or ""
    gold = inst["gold"]
    out: dict = {"instance_id": inst["instance_id"], "repo": inst["repo"], "gold": gold}
    out["wiki"] = page_for(tree, gold[0])
    out["gold_symbols"] = gold_symbols(inst["instance_id"])

    sp = StdioServerParameters(
        command=str(c8.REPOWISE_EXE),
        args=["mcp", str(tree), "--transport", "stdio"], env=env,
    )
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            try:  # A8: the first call never returns; abandoning it is the fix
                async with asyncio.timeout(30):
                    await s.call_tool("search_codebase", {"query": "x", "limit": 1})
            except Exception:  # noqa: BLE001,S110
                pass

            async def call(tool, args):
                try:
                    async with asyncio.timeout(180):
                        res = await s.call_tool(tool, args)
                    text = "\n".join(getattr(c, "text", "") or "" for c in res.content)
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError):
                        payload = {}
                    return r5.paths_from_repowise(payload, text), len(text)
                except Exception as e:  # noqa: BLE001
                    return [f"__error__{type(e).__name__}"], 0

            q = inst["problem_statement"]
            deep, _ = await call("search_codebase", {"query": q, "limit": 50})
            out["H1_rank_in_50"] = rank_of(deep, gold)
            out["H1_returned"] = len(deep)

            if out["gold_symbols"]:
                ident, _ = await call(
                    "search_codebase", {"query": " ".join(out["gold_symbols"][:3]), "limit": 10})
                out["H2_identifier_rank"] = rank_of(ident, gold)
                out["H2_query"] = " ".join(out["gold_symbols"][:3])

            base = Path(relpath(gold[0])).name
            byname, _ = await call("search_codebase", {"query": base, "limit": 10})
            out["H2b_basename_rank"] = rank_of(byname, gold)

            # The floor. If handing the tool a file's own path does not find it,
            # nothing above this line matters.
            bypath, _ = await call(
                "search_codebase", {"query": relpath(gold[0]), "limit": 10})
            out["H0_own_path_rank"] = rank_of(bypath, gold)

            ans, chars = await call("get_answer", {"question": q})
            out["answer_paths"] = ans
            out["answer_rank"] = rank_of(ans, gold)
            out["answer_chars"] = chars
    return out


def prepare(inst: dict) -> tuple[dict, Path] | None:
    src = TREES / c8.REPO_TREE[inst["repo"]][1]
    dest = TREES / f"r8diag-{inst['instance_id'].split('__')[-1]}"
    if not dest.exists():
        p = subprocess.run(
            ["git", "worktree", "add", "--detach", str(dest), inst["base_commit"]],
            cwd=str(src), capture_output=True, text=True)
        if p.returncode:
            return None
    if not r8.index_present("repowise", dest):
        b = c8.build_index("repowise", dest, f"diag__{dest.name}")
        if b.get("rc"):
            return None
    return inst, dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    import pandas as pd

    diag = json.loads((HERE / "_diag_set.json").read_text(encoding="utf-8"))
    df = pd.read_parquet(c8.PARQUET)
    by_repo: dict[str, list[dict]] = {}
    for d in diag:
        by_repo.setdefault(d["repo"], []).append(d)
    picked: list[dict] = []
    while len(picked) < args.n and any(by_repo.values()):
        for k in sorted(by_repo):
            if by_repo[k] and len(picked) < args.n:
                picked.append(by_repo[k].pop(0))
    for p in picked:
        p["problem_statement"] = df[df.instance_id == p["instance_id"]].iloc[0].problem_statement

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        prepared = [r for r in ex.map(prepare, picked) if r]
    print(f"prepared {len(prepared)} of {len(picked)}", flush=True)

    results = []
    for inst, tree in prepared:
        try:
            results.append(asyncio.run(probe_one(inst, tree)))
        except Exception as e:  # noqa: BLE001
            results.append({"instance_id": inst["instance_id"], "error": f"{type(e).__name__}: {e}"})
        print(json.dumps(results[-1], indent=1)[:1200], flush=True)

    (HERE / "why_we_miss.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n=== verdict ===")
    for r in results:
        if "error" in r:
            print(f"{r['instance_id'][-8:]} ERROR {r['error']}")
            continue
        w = r.get("wiki", {})
        print(f"{r['instance_id'][-8:]} page={w.get('page_present')} "
              f"syms={w.get('symbols_indexed')} "
              f"top50={r.get('H1_rank_in_50')} ident={r.get('H2_identifier_rank')} "
              f"basename={r.get('H2b_basename_rank')} ownpath={r.get('H0_own_path_rank')} "
              f"answer={r.get('answer_rank')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
