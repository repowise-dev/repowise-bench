"""Rung 5: agent-free multi-arm retrieval probe.

Generalizes `local-stash/agent-context/bench/retrieval_battery.py` from one
server to N. One tool call per (arm, question), no agent and no LLM, scored as
file-level recall@k and MRR against a hand-labeled gold set.

Why this is worth running before spending anything on Layer A: it uses the same
instrument (rank the files a tool surfaces, compare to gold) on a corpus we
labeled ourselves, so it tells us the direction of the result and shakes out the
multi-arm plumbing while costing nothing.

Two deliberate fairness constraints, both of which cost our own arm points:

1. **The 14 `gold_pages`-only hierarchy questions are excluded.** Their gold is a
   repowise page-identity structural key. No competitor emits page identities, so
   scoring them would gift our arm 14 questions no other arm can win by
   construction. n drops from 99 to 85.
2. **Every arm gets one call to its own best retrieval tool**, chosen from that
   arm's own documentation of what its primary entry point is, not a tool we
   picked to make it look bad. Where an arm's primary tool is explicitly
   "answers almost any question in one call" (CodeGraph), that is the one used.

This is NOT citable the way ContextBench is: the gold set was written by us,
about our own repo, which is the single most favourable corpus our own tool
could face. Treat the ranking as directional and the absolute numbers as
meaningless outside this repo. That caveat belongs on every chart made from it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUT = Path(__file__).resolve().parent
REPOWISE_ROOT = Path(r"C:\Users\ragha\Desktop\repowise")
NPM_BIN = Path(os.environ["APPDATA"]) / "npm"
UV_BIN = Path.home() / ".local" / "bin"
KS = (1, 3, 5, 10)


# --------------------------------------------------------------------------
# gold matching (same normalisation as lib/retrieval_eval.py)
# --------------------------------------------------------------------------
def norm(p: str) -> str:
    return str(p).replace("\\", "/").lstrip("./").lower()


def path_matches(surfaced: str, gold: str) -> bool:
    s, g = norm(surfaced), norm(gold)
    if not s or not g:
        return False
    if s == g or s.endswith("/" + g) or g.endswith("/" + s):
        return True
    # gold may be given as a bare basename
    return "/" not in g and s.rsplit("/", 1)[-1] == g


def score(ranked: list[str], gold: list[str]) -> dict:
    hit_rank = None
    for i, p in enumerate(ranked, 1):
        if any(path_matches(p, g) for g in gold):
            hit_rank = i
            break
    return {
        "hit_rank": hit_rank,
        "rr": (1.0 / hit_rank) if hit_rank else 0.0,
        **{f"recall@{k}": int(bool(hit_rank and hit_rank <= k)) for k in KS},
    }


# --------------------------------------------------------------------------
# per-arm: how to launch, what to call, how to read a ranked file list back
# --------------------------------------------------------------------------
# No left-anchor. An earlier version required the path to be preceded by
# whitespace or a quote, which silently discarded EVERY path Graphify emits:
# its node lines read `NODE foo() [src=packages/.../_verify.py loc=L149]`, and
# `=` was not in the allowed prefix set. That scored Graphify at 0.012 MRR when
# it was in fact returning the gold file at rank 2. A near-miss worth
# remembering: an extractor bug and a genuinely bad arm look identical in the
# summary table, and the arm that gets silently zeroed is never our own,
# because ours is the only one whose output format we already know.
PATH_RE = re.compile(
    r"((?:[\w.\-]+[/\\])+[\w.\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|rb|cs|c|h|cc|cpp|hpp|swift|scala|php|sh|sql|md|mdx|rst|yaml|yml|json|toml|txt|html|css))",
    re.MULTILINE,
)


def paths_from_text(text: str) -> list[str]:
    """Ordered, de-duplicated file paths mentioned in a free-text response.

    Fallback extractor for arms that return prose or a bespoke layout rather
    than a structured hit list. Order of first mention is the only ranking
    signal such an arm exposes, so that is what gets scored. This is generous
    to those arms, not stingy: any path they name anywhere counts.
    """
    seen, out = set(), []
    for m in PATH_RE.finditer(text or ""):
        p = m.group(1)
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def paths_from_repowise(payload: dict, text: str) -> list[str]:
    """Structured extraction for our own arm, mirroring lib/retrieval_eval.py:
    retrieval[] first (the hits that actually feed synthesis), then citations,
    fallback_targets, symbol_bodies, best_guesses, then `candidates`. Falls back
    to text scan.

    `candidates` is read LAST and was added 2026-08-02, after the retrieval fix
    that introduced it (finding A10: get_answer capped its evidence list at 5
    and then confidence-gated it to 0/2/3, so a high-confidence answer named no
    file at all). Two notes for anyone comparing rung 8's row against a later
    one:

    * It is appended, never interleaved, so every path the pre-fix extractor
      would have produced keeps its exact rank. A rung-8 response run through
      this version extracts identically, because it carries no `candidates`.
    * Not extracting it would be finding E4's asymmetry pointed at ourselves:
      the arm genuinely offers those paths to a real agent, and a stale
      extractor would under-report us the way rung 5's under-reported graphify.
      Any row using this version must say the extractor changed with it.
    """
    out: list[str] = []

    def push(p) -> None:
        if isinstance(p, str) and p and p not in out:
            out.append(p)

    if isinstance(payload, dict):
        for hit in payload.get("retrieval") or []:
            if isinstance(hit, dict):
                push(hit.get("target_path") or hit.get("file") or hit.get("path"))
        for hit in payload.get("results") or []:
            if isinstance(hit, dict):
                push(hit.get("file") or hit.get("target_path") or hit.get("path"))
        for key in ("citations", "fallback_targets", "best_guesses"):
            for hit in payload.get(key) or []:
                if isinstance(hit, dict):
                    push(hit.get("file") or hit.get("target_path") or hit.get("path"))
                else:
                    push(hit)
        for hit in payload.get("symbol_bodies") or []:
            if isinstance(hit, dict):
                push(hit.get("file") or hit.get("path"))
        for hit in payload.get("candidates") or []:
            if isinstance(hit, dict):
                push(hit.get("path") or hit.get("file") or hit.get("target_path"))
            else:
                push(hit)
    return out or paths_from_text(text)


def paths_from_json(payload: dict, text: str) -> list[str]:
    """Structured extraction for arms returning JSON hit lists.

    code-review-graph reports `results[].file_path` as an ABSOLUTE Windows path
    with backslash separators, JSON-escaped in the raw response text.
    The free-text regex cannot read that (escaped separators), and even parsed
    it is absolute, so it needs normalising against the tree root before it can
    match a repo-relative gold path. `path_matches` already handles the
    absolute-vs-relative case once separators are real, so only extraction
    needed fixing.
    """
    out: list[str] = []

    def push(v) -> None:
        if isinstance(v, str) and v and v not in out:
            out.append(v)

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("file_path", "file", "path", "target_path", "src") and isinstance(v, str):
                    push(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(payload)
    return out or paths_from_text(text)


def paths_from_cocoindex(payload, text: str) -> list[str]:
    """CocoIndex Code returns ranked code CHUNKS, several per file.

    `search` answers a `SearchResultModel`: `{"success": bool, "results":
    [{"file_path", "language", "content", "start_line", "end_line", "score"}],
    "total_returned", "offset", "message"}`. Paths are repo-relative with
    forward slashes, so `path_matches` needs no normalisation — unlike
    code-review-graph, which reports absolute Windows paths.

    Two things this does that the generic JSON walker does not:

    * It DE-DUPLICATES BY FILE while keeping first-hit order. `limit=10` buys
      ten chunks, not ten files, and a chunker that splits a large file into
      six pieces would otherwise spend six of the ten slots on one file. The
      ranked list this returns is therefore shorter than `limit`, and that is
      the arm's own behaviour rather than a handicap: it is what an agent
      reading the response would see.
    * It refuses the `success: false` shape rather than falling through to a
      text scan. A failed call carries a `message` and no results, and letting
      the free-text regex mine an error string for anything path-shaped is how
      a dead arm scores like a live one (finding E4).

    Written against a captured response and checked against a known-hit case
    before any cell was graded (pre-registration section 5, finding E5).
    """
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return []
        results = payload.get("results")
        if isinstance(results, list):
            out: list[str] = []
            for hit in results:
                if not isinstance(hit, dict):
                    continue
                p = hit.get("file_path")
                if isinstance(p, str) and p and p not in out:
                    out.append(p)
            return out
    return paths_from_text(text)


def paths_from_serena(payload, text: str) -> list[str]:
    """Serena reports paths as JSON *keys*, not values.

    `search_for_pattern` returns `{"packages\server\...\file.py": [lines]}`
    and `find_symbol` returns a list of dicts carrying `relative_path`. The
    generic value-walker misses the first shape entirely, and the free-text
    regex cannot read either because the separators are JSON-escaped
    backslashes. Both were scoring 0 on queries that plainly did find the file.
    """
    out: list[str] = []

    def push(v) -> None:
        if isinstance(v, str) and v and ("/" in v or "\\" in v) and v not in out:
            out.append(v)

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                push(k)
                if k in ("relative_path", "file_path", "file", "path"):
                    push(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(payload)
    return out or paths_from_text(text)


def arm_specs(repo: str) -> dict[str, dict]:
    """`repo` is the base staged checkout. Each arm queries its OWN worktree
    copy (`r5-<arm>`), never a shared one.

    Rung 4's finding E3 says arms contaminate each other when they share a tree.
    The rung-4 fix (clear every artifact dir per cell) cannot be used here,
    because rung 5 needs all four indexes alive at once. One tree per arm
    satisfies both: no arm indexes another's output, and nothing gets deleted.
    """
    base = Path(repo).parent

    def tree(arm: str) -> str:
        t = base / f"r5-{arm}"
        return str(t if t.exists() else Path(repo))

    specs = {
        "repowise": {
            "command": str(REPOWISE_ROOT / ".venv" / "Scripts" / "repowise.exe"),
            "args": ["mcp", tree("repowise"), "--transport", "stdio"],
            # question -> (tool, args)
            "call": lambda q, kind: (
                ("get_answer", {"question": q})
                if kind == "get_answer"
                else ("search_codebase", {"query": q, "limit": 10})
            ),
            "extract": paths_from_repowise,
        },
        "codegraph": {
            "command": str(NPM_BIN / "codegraph.cmd"),
            "args": ["serve", "--mcp", "--path", tree("codegraph"), "--no-watch"],
            # their own README: codegraph_explore "answers almost any question
            # in one call". Single advertised tool by default.
            "call": lambda q, kind: ("codegraph_explore", {"query": q}),
            "extract": lambda payload, text: paths_from_text(text),
        },
        "code-review-graph": {
            "command": str(UV_BIN / "code-review-graph.exe"),
            "args": ["serve", "--repo", tree("crg")],
            # NOTE the `_tool` suffix: every code-review-graph tool carries it
            # (`query_graph_tool`, `semantic_search_nodes_tool`, ...). Calling
            # the unsuffixed name returns tool-absent for every question, which
            # scores as a clean 0.0 and looks exactly like a real retrieval
            # failure. Rung 4's own surface enumeration had already recorded the
            # real names; not reading my own output cost this arm a false zero.
            # `query_graph_tool` is NOT their search entry point: it requires
            # `pattern` + `target` (callers_of / callees_of style structural
            # queries) and rejects a natural-language `query` outright, which
            # scored 84/84 isError. `semantic_search_nodes_tool` is the tool
            # whose own description is "Search for code entities by name,
            # keyword, or semantic similarity", so it is the fair counterpart
            # to our search_codebase / get_answer.
            # provider+model MUST be passed on the search call itself. Running
            # `build --embedding-provider local` and `embed_graph_tool` embeds
            # 23,366 nodes and reports "Semantic search is now active", but the
            # search tool still answers `search_mode: "none"` and returns zero
            # hits for a natural-language query unless the same provider/model
            # are repeated per call. Without them this arm scored 0.028 MRR with
            # 81 of 84 queries returning nothing, which looks exactly like a
            # tool that cannot retrieve.
            "call": lambda q, kind: (
                "semantic_search_nodes_tool",
                {
                    "query": q,
                    "limit": 10,
                    "provider": "local",
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                },
            ),
            "extract": paths_from_json,
        },
        "graphify": {
            "command": str(UV_BIN / "graphify-mcp.exe"),
            "args": [
                "--transport",
                "stdio",
                "--graph",
                str(Path(tree("graphify")) / "graphify-out" / "graph.json"),
            ],
            "call": lambda q, kind: ("query_graph", {"question": q}),
            "extract": lambda payload, text: paths_from_text(text),
        },
        "cocoindex": {
            "command": str(UV_BIN / "ccc.exe"),
            # `ccc mcp` TAKES NO PROJECT ARGUMENT, and this is the arm's one
            # real hazard. `cli.py:mcp` calls `require_project_root()`, which is
            # `find_project_root(Path.cwd())` walking UP from the working
            # directory for a `.cocoindex_code/settings.yml`. There is no flag
            # and no env var on this path (`COCOINDEX_CODE_ROOT_PATH` is read
            # only by the legacy `cocoindex-code` entry point, not by `ccc`).
            # Launched without `cwd`, the server inherits the HARNESS's working
            # directory and answers every question about whatever repository it
            # finds above it, while reporting itself perfectly healthy. That is
            # finding A9's shape, and A9 cost this workstream a rung.
            #
            # So `cwd` is the whole fix, and it is verified positively rather
            # than assumed: two instance trees are asked the same question and
            # the answers must differ. A server pinned to one repo returns
            # identical bytes for both, which is the only check that catches it.
            "args": ["mcp"],
            "cwd": tree("cocoindex"),
            # `refresh_index` DEFAULTS TO TRUE (server.py), and an unmodified
            # call reindexes before querying: it bills a rebuild to the cell and
            # makes the index a variable mid-run. Same objection that excluded
            # crg's build_or_update_graph_tool, and it cannot be fixed by
            # exclusion because it is a parameter on the only tool served.
            # Layer A calls the tool directly, so Layer A pins it false.
            "call": lambda q, kind: (
                "search", {"query": q, "limit": 10, "refresh_index": False}
            ),
            "extract": paths_from_cocoindex,
        },
        "serena": {
            "command": str(UV_BIN / "serena.exe"),
            "args": [
                "start-mcp-server",
                "--project",
                tree("serena"),
                "--transport",
                "stdio",
                "--enable-web-dashboard",
                "false",
                "--enable-gui-log-window",
                "false",
            ],
            # Serena is an LSP wrapper with no retrieval-by-question tool at
            # all. search_for_pattern is the closest thing it offers, and that
            # asymmetry is the finding, not a rigging.
            "call": lambda q, kind: (
                "search_for_pattern",
                {"substring_pattern": q, "relative_path": "."},
            ),
            "extract": paths_from_serena,
        },
    }
    for _arm, _spec in specs.items():
        _spec["tree"] = tree(_arm)
    return specs


async def run_arm(arm: str, spec: dict, questions: list[dict], timeout=180.0) -> list:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "DO_NOT_TRACK": "1",
            "REPOWISE_SKIP_EDITOR_SETUP": "1",
        }
    )
    sp = StdioServerParameters(command=spec["command"], args=spec["args"], env=env)
    rows: list[dict] = []
    try:
        async with asyncio.timeout(60):
            cm = stdio_client(sp)
            r, w = await cm.__aenter__()
    except Exception as e:  # noqa: BLE001
        print(f"{arm}: SERVER FAILED TO START: {type(e).__name__}: {e}", flush=True)
        return [{"arm": arm, "status": "server-failed", "error": str(e)}]

    try:
        async with ClientSession(r, w) as s:
            await s.initialize()
            served = {t.name for t in (await s.list_tools()).tools}
            # Serena needs an explicit activate_project even when --project is
            # passed on the command line: without it every tool answers
            # "No active project" and the arm scores a clean 0.000 that looks
            # exactly like a tool with no retrieval capability. Fourth arm this
            # rung to be silently zeroed by a harness bug rather than by its own
            # behaviour, so this is done for ANY arm advertising the tool.
            if "activate_project" in served:
                try:
                    async with asyncio.timeout(600):
                        await s.call_tool("activate_project", {"project": spec["tree"]})
                except Exception as e:  # noqa: BLE001
                    print(f"{arm}: activate_project failed: {e}", flush=True)
            for q in questions:
                text_q = q.get("question") or q.get("query") or ""
                tool, args = spec["call"](text_q, q.get("tool"))
                row = {
                    "arm": arm,
                    "id": q["id"],
                    "category": q.get("category"),
                    "tags": q.get("tags") or [],
                    "tool": tool,
                    "question": text_q,
                    "gold": q["gold"],
                }
                if tool not in served:
                    row.update({"status": "tool-absent", "served": sorted(served)})
                    rows.append(row)
                    continue
                try:
                    async with asyncio.timeout(timeout):
                        res = await s.call_tool(tool, args)
                    text = "\n".join(getattr(c, "text", "") or "" for c in res.content)
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError):
                        payload = {}
                    ranked = spec["extract"](payload, text)
                    row.update(
                        {
                            "status": "ok",
                            "isError": bool(res.isError),
                            "chars": len(text),
                            "ranked": ranked[:25],
                            **score(ranked, q["gold"]),
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    row.update({"status": "call-failed", "error": f"{type(e).__name__}: {e}"})
                rows.append(row)
                print(
                    f"  {arm:18s} {q['id']:32s} "
                    f"{row.get('status')} hit@{row.get('hit_rank')}",
                    flush=True,
                )
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001,S110
            pass
    return rows


def summarize(rows: list[dict]) -> dict:
    by_arm: dict[str, list] = {}
    for r in rows:
        if r.get("status") == "ok" or r.get("status") == "tool-absent":
            by_arm.setdefault(r["arm"], []).append(r)
    out = {}
    for arm, rs in by_arm.items():
        n = len(rs)
        out[arm] = {
            "n": n,
            "errors": sum(1 for r in rs if r.get("isError")),
            "tool_absent": sum(1 for r in rs if r.get("status") == "tool-absent"),
            "MRR": round(sum(r.get("rr", 0.0) for r in rs) / n, 4) if n else 0.0,
            **{
                f"recall@{k}": round(
                    sum(r.get(f"recall@{k}", 0) for r in rs) / n, 4
                )
                if n
                else 0.0
                for k in KS
            },
        }
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--evals",
        default=str(
            REPOWISE_ROOT
            / "local-stash/agent-context/bench/eval/repowise_retrieval_v2.yaml"
        ),
    )
    ap.add_argument("--repo", required=True, help="staged checkout every arm indexes")
    ap.add_argument("--arms", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(OUT / "retrieval_probe.json"))
    a = ap.parse_args()

    suite = yaml.safe_load(Path(a.evals).read_text(encoding="utf-8"))
    # fairness constraint 1: file-path gold only. gold_pages-only questions are
    # repowise page identities no competitor can emit.
    questions = [e for e in suite["evals"] if e.get("gold")]
    n_pathgold = len(questions)

    # fairness constraint 3: a gold file that no longer exists is unreachable by
    # every arm, so scoring it would depress all arms by a constant and pretend
    # the instrument measured something. The v2 set was frozen against an older
    # HEAD; drop what has since moved and say how many.
    stale = [
        e
        for e in questions
        if not any((Path(a.repo) / g).exists() for g in e["gold"])
    ]
    questions = [e for e in questions if e not in stale]
    if a.limit:
        questions = questions[: a.limit]
    print(
        f"{len(questions)} questions scored "
        f"({len(suite['evals'])} in suite, "
        f"{len(suite['evals']) - n_pathgold} gold_pages-only excluded, "
        f"{len(stale)} dropped as stale gold: "
        f"{[e['id'] for e in stale]})\n"
    )

    specs = arm_specs(a.repo)
    wanted = [x for x in a.arms.split(",") if x] or list(specs)

    rows: list[dict] = []
    out_path = Path(a.out)
    if out_path.exists():
        rows = json.loads(out_path.read_text(encoding="utf-8"))
    for arm in wanted:
        new = await run_arm(arm, specs[arm], questions)
        rows = [r for r in rows if r["arm"] != arm]
        rows.extend(new)
        out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    summary = summarize(rows)
    (OUT / "retrieval_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
