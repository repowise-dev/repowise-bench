"""Precision Q&A runner: ask ``get_answer`` over MCP, grade, emit H1 metrics.

Each question file (``data/precision_qa/<repo>.json``) holds hand-written
questions with a reference answer, key facts, files a correct answer must
cite and claims that make an answer wrong. The runner speaks MCP to the tool
exactly as a host would, so the same run scores the local ``repowise mcp``
(stdio) and a hosted endpoint (streamable HTTP + bearer token).

A question file is ``{"repo", "commit", "split", "questions": [...]}``; each
question carries ``id, question, category`` (where_defined / who_calls /
how_does / compare / why / trap), ``identifier_style, language, trap,
reference_answer, key_facts[], must_cite_files[], forbidden_claims[],
evidence[]`` (path:lines the author read), ``source`` (``audit:ansNN`` for the
audit's questions), ``author`` and ``reviewed`` (false until a human checks it).
The held-out half lives in ``heldout/precision_qa/``; only nightly CI reads it.

Every answer is graded by ``judge_answer`` (swe_qa_runner) with the
``precision-qa`` rubric into correct / partial / refused_hedged / wrong /
confidently_wrong. Citations are resolved against the repo at the question
file's pinned commit. Outputs, under ``--out``:

- ``results.jsonl``   one row per question: payload summary + verdict (resumable)
- ``findings.jsonl`` / ``labels.jsonl``  rows for scripts/kg_validate/score.py
- ``summary.json`` / ``summary.md``      the H1 get_answer metrics per repo

Usage:
    # local: index each repo first; --repo-dir maps a repo name to its checkout
    python harness/answer_precision.py run --repo-dir click=/path/click \\
        --repo-dir httpx=/path/httpx --out results/precision_qa/local
    # hosted: {repo} in the URL is replaced per repo; token from env or flag
    REPOWISE_MCP_TOKEN=... python harness/answer_precision.py run \\
        --endpoint 'https://host/mcp/{repo}' --repo-dir click=/path/click --out ...
    # 20% sample for a human, then judge-human agreement once it is filled in
    python harness/answer_precision.py sample --out results/precision_qa/local
    python harness/answer_precision.py agreement --out results/precision_qa/local

Score with the shared floors:
    python scripts/kg_validate/score.py --findings <out>/findings.jsonl \\
        --labels <out>/labels.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.swe_qa_runner import judge_answer

BENCH = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = BENCH / "data" / "precision_qa"
VERDICTS = ("correct", "partial", "refused_hedged", "wrong", "confidently_wrong")
CALL_TIMEOUT_S = float(os.environ.get("REPOWISE_BENCH_CALL_TIMEOUT", "240"))
# A judge call through the Claude CLI loads the whole agent system prompt
# (~$0.09 each); with no tools and no settings it is ~$0.01.
LEAN_JUDGE_CLI = ("--tools", "", "--strict-mcp-config", "--setting-sources", "")
SAMPLE_FILE = "human_sample.jsonl"


# --- questions ---------------------------------------------------------------


def load_questions(paths: list[Path], repos: set[str] | None) -> list[dict]:
    """Questions from files or directories of ``<repo>.json``, tagged with repo + commit."""
    files: list[Path] = []
    for p in paths:
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    out = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        if repos and doc["repo"] not in repos:
            continue
        for q in doc["questions"]:
            out.append({**q, "repo": doc["repo"], "commit": doc["commit"]})
    return out


# --- MCP transport -----------------------------------------------------------


@asynccontextmanager
async def open_session(
    repo: str, repo_dir: Path | None, endpoint: str | None, token: str | None
):
    """An initialized MCP ClientSession for one repo, over stdio or streamable HTTP."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if endpoint:
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with (
            streamablehttp_client(endpoint.format(repo=repo), headers=headers) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
        return
    if repo_dir is None:
        raise SystemExit(f"--repo-dir {repo}=<path> is required for a local run")
    exe = os.environ.get("REPOWISE_EXE") or shutil.which("repowise") or "repowise"
    # cwd must be the repo: repowise resolves its index by walking up from cwd.
    params = StdioServerParameters(
        command=exe,
        args=["mcp", str(repo_dir), "--transport", "stdio"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(repo_dir),
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def parse_tool_result(result) -> dict:
    """The tool's JSON payload, or ``{"error": ...}`` for an error / non-JSON reply."""
    text = "".join(getattr(c, "text", "") for c in result.content or [])
    # The SDK's attribute name changed across versions (isError -> is_error).
    if getattr(result, "isError", None) or getattr(result, "is_error", None):
        return {"error": text[:500] or "tool error"}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"answer": text}
    return payload if isinstance(payload, dict) else {"answer": text}


async def ask_repo(questions: list[dict], session_args: tuple, on_row) -> None:
    async with open_session(*session_args) as session:
        for q in questions:
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    session.call_tool("get_answer", {"question": q["question"]}),
                    CALL_TIMEOUT_S,
                )
                payload = parse_tool_result(result)
            except Exception as e:  # noqa: BLE001 - a hung or crashed call is a row, not a crash
                payload = {"error": f"{type(e).__name__}: {e}"[:500]}
            on_row(q, payload, (time.perf_counter() - t0) * 1000)


# --- citations ---------------------------------------------------------------

_LINE_SUFFIX = re.compile(r"(#L\d+.*|:\d+(-\d+)?)$")


def cited_paths(payload: dict) -> list[str]:
    """Unique repo-relative file paths from ``citations`` (strings or dicts)."""
    seen: dict[str, None] = {}
    for row in payload.get("citations") or []:
        if isinstance(row, dict):
            row = row.get("source_path") or row.get("path") or row.get("file") or ""
        if not isinstance(row, str) or not row:
            continue
        path = _LINE_SUFFIX.sub("", row.split("::", 1)[0]).removeprefix("./")
        if path:
            seen[path] = None
    return list(seen)


def path_resolves(repo_dir: Path | None, commit: str, path: str) -> bool | None:
    """True when ``path`` is a file (or dir) in the repo at ``commit``; None if unknown."""
    if repo_dir is None:
        return None
    r = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-e", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    return r.returncode == 0


# --- grading -----------------------------------------------------------------


def gold_text(q: dict) -> str:
    lines = [q["reference_answer"], "", "KEY FACTS:"]
    lines += [f"- {f}" for f in q["key_facts"]]
    lines += ["", "FORBIDDEN CLAIMS:"]
    lines += [f"- {f}" for f in q["forbidden_claims"]]
    if q.get("trap"):
        lines += [
            "",
            "TRAP QUESTION: the asked-about name does not exist in this repository.",
        ]
    return "\n".join(lines)


def agent_text(payload: dict) -> str:
    if payload.get("error"):
        return ""
    text = (payload.get("answer") or "").strip()
    if not text and payload.get("best_guesses"):
        text = (
            "Best guesses:\n" + json.dumps(payload["best_guesses"], default=str)[:3000]
        )
    conf = payload.get("confidence") or "unknown"
    return f"{text}\n\n[tool confidence: {conf}]" if text else ""


def judge_row(row: dict, q: dict, model: str) -> dict:
    if row["payload"].get(
        "error"
    ):  # a failed call is never a refusal: it counts as not correct
        return {**row, "verdict": "error", "judge": {}}
    answer = agent_text(row["payload"])
    if not answer:  # nothing to grade: an empty reply refuses by omission
        return {**row, "verdict": "refused_hedged", "judge": {"note": "empty answer"}}
    scores = judge_answer(
        q["question"],
        gold_text(q),
        answer,
        model,
        category="precision-qa",
        cli_args=LEAN_JUDGE_CLI,
    )
    verdict = scores.get("verdict")
    return {**row, "verdict": verdict if verdict in VERDICTS else None, "judge": scores}


# --- metrics -----------------------------------------------------------------


def _rate(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def summarize(rows: list[dict]) -> dict:
    """H1 get_answer metrics per repo and pooled (``*``)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["repo"]].append(r)
        groups["*"].append(r)
    out = {}
    for repo, rs in sorted(groups.items()):
        judged = [r for r in rs if r.get("verdict")]
        normal = [r for r in judged if not r["trap"]]
        traps = [r for r in judged if r["trap"]]
        high = [r for r in normal if r["confidence"] == "high"]
        cites = [c for r in rs for c in r["citations"] if c["resolves"] is not None]
        must = [m for r in rs for m in r["must_cite_hit"]]
        out[repo] = {
            "questions": len(rs),
            "judged": len(judged),
            "verdicts": dict(Counter(r["verdict"] for r in judged)),
            "tiers": dict(Counter(r["confidence"] for r in normal)),
            "correct": _rate(
                sum(r["verdict"] == "correct" for r in normal), len(normal)
            ),
            "confidently_wrong": _rate(
                sum(r["verdict"] == "confidently_wrong" for r in judged), len(judged)
            ),
            "high_tier_correct": _rate(
                sum(r["verdict"] == "correct" for r in high), len(high)
            ),
            "trap_refused": _rate(
                sum(r["verdict"] == "refused_hedged" for r in traps), len(traps)
            ),
            "citation_resolves": _rate(sum(c["resolves"] for c in cites), len(cites)),
            "must_cite_recall": _rate(sum(must), len(must)),
            "errors": sum(bool(r["payload"].get("error")) for r in rs),
        }
    return out


def score_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """findings + labels rows in scripts/kg_validate/score.py's input format."""
    findings, labels = [], []

    def add(repo, family, key, ok, tier=None):
        findings.append({"repo": repo, "family": family, "key": key, "tier": tier})
        labels.append(
            {"repo": repo, "family": family, "finding_key": key, "label": bool(ok)}
        )

    for r in rows:
        v = r.get("verdict")
        if v is None:
            continue
        repo, qid = r["repo"], r["id"]
        if r["trap"]:
            add(repo, "get_answer.trap_refused", qid, v == "refused_hedged")
        else:
            add(repo, "get_answer", qid, v == "correct", tier=r["confidence"])
        add(repo, "get_answer.not_confidently_wrong", qid, v != "confidently_wrong")
        for c in r["citations"]:
            if c["resolves"] is not None:
                add(
                    repo,
                    "get_answer.citation_resolves",
                    f"{qid}:{c['path']}",
                    c["resolves"],
                )
    return findings, labels


def write_summary(out: Path, summary: dict, meta: dict) -> None:
    (out / "summary.json").write_text(
        json.dumps({"meta": meta, "repos": summary}, indent=2), encoding="utf-8"
    )
    cols = [
        ("correct", "correct (>=85%)"),
        ("confidently_wrong", "conf. wrong (<=3%)"),
        ("high_tier_correct", "high tier (>=95%)"),
        ("trap_refused", "traps refused (100%)"),
        ("citation_resolves", "citations resolve (>=95%)"),
        ("must_cite_recall", "must-cite recall"),
    ]
    fmt = lambda v: "-" if v is None else f"{v:.0%}"
    lines = [
        "| repo | n | " + " | ".join(c[1] for c in cols) + " | tiers |",
        "|---" * (len(cols) + 3) + "|",
    ]
    for repo, s in summary.items():
        cells = " | ".join(fmt(s[k]) for k, _ in cols)
        lines.append(
            f"| {repo} | {s['judged']}/{s['questions']} | {cells} | {s['tiers']} |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# --- commands ----------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def cmd_run(args) -> int:
    repo_dirs = {
        k: Path(v).resolve() for k, v in (s.split("=", 1) for s in args.repo_dir)
    }
    repos = set(args.repos.split(",")) if args.repos else None
    questions = load_questions(args.questions or [DEFAULT_QUESTIONS], repos)
    by_id = {q["id"]: q for q in questions}
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    previous = _read_jsonl(results_path)
    # Rows for questions outside this run (another --repos subset) are kept verbatim.
    others = [r for r in previous if r["id"] not in by_id]
    done = {r["id"]: r for r in previous if r["id"] in by_id}
    todo = [q for q in questions if q["id"] not in done]
    print(
        f"{len(questions)} questions, {len(done)} already answered, {len(todo)} to ask"
    )

    def on_row(q, payload, ms):
        repo_dir = repo_dirs.get(q["repo"])
        paths = cited_paths(payload)
        row = {
            "id": q["id"],
            "repo": q["repo"],
            "category": q["category"],
            "trap": bool(q.get("trap")),
            "confidence": str(payload.get("confidence") or "none").lower(),
            "timing_ms": round(ms),
            "citations": [
                {"path": p, "resolves": path_resolves(repo_dir, q["commit"], p)}
                for p in paths
            ],
            "must_cite_hit": [m in paths for m in q["must_cite_files"]],
            "payload": payload,
        }
        done[q["id"]] = row
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(
            f"  {q['id']:14} {row['confidence']:7} {ms / 1000:5.1f}s {len(paths)} cites"
        )

    by_repo: dict[str, list[dict]] = defaultdict(list)
    for q in todo:
        by_repo[q["repo"]].append(q)
    for repo, qs in by_repo.items():
        print(f"{repo}: asking {len(qs)}")
        session_args = (repo, repo_dirs.get(repo), args.endpoint, args.token)
        asyncio.run(ask_repo(qs, session_args, on_row))

    rows = [done[q["id"]] for q in questions if q["id"] in done]
    if not args.no_judge:
        pending = [r for r in rows if args.rejudge or not r.get("verdict")]
        print(f"judging {len(pending)} answers with {args.judge_model}")
        with ThreadPoolExecutor(args.judge_workers) as pool:
            graded = pool.map(
                lambda r: judge_row(r, by_id[r["id"]], args.judge_model), pending
            )
            graded_by_id = {r["id"]: r for r in graded}
        rows = [graded_by_id.get(r["id"], r) for r in rows]
        _write_jsonl(results_path, others + rows)

    findings, labels = score_rows(rows)
    _write_jsonl(args.out / "findings.jsonl", findings)
    _write_jsonl(args.out / "labels.jsonl", labels)
    meta = {
        "transport": "http" if args.endpoint else "stdio",
        "endpoint": args.endpoint,
        "judge_model": args.judge_model,
        "question_files": [str(p) for p in (args.questions or [DEFAULT_QUESTIONS])],
        "commits": {q["repo"]: q["commit"] for q in questions},
    }
    write_summary(args.out, summarize(rows), meta)
    return 0


def cmd_sample(args) -> int:
    """Export a seeded ``--fraction`` of judged rows for a human to label."""
    rows = [r for r in _read_jsonl(args.out / "results.jsonl") if r.get("verdict")]
    questions = {
        q["id"]: q for q in load_questions(args.questions or [DEFAULT_QUESTIONS], None)
    }
    k = max(1, round(len(rows) * args.fraction))
    picked = random.Random(args.seed).sample(rows, min(k, len(rows)))
    sample = [
        {
            "id": r["id"],
            "question": questions[r["id"]]["question"],
            "reference": gold_text(questions[r["id"]]),
            "agent_answer": agent_text(r["payload"]),
            "judge_verdict": r["verdict"],
            "human_verdict": None,  # fill with one of VERDICTS
        }
        for r in sorted(picked, key=lambda r: r["id"])
    ]
    _write_jsonl(args.out / SAMPLE_FILE, sample)
    print(
        f"wrote {len(sample)} rows to {args.out / SAMPLE_FILE}; fill human_verdict with {VERDICTS}"
    )
    return 0


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def agreement(sample: list[dict]) -> dict:
    """Judge-human agreement on the 5-way verdict and on binary correct/not."""
    pairs = [
        (r["judge_verdict"], r["human_verdict"])
        for r in sample
        if r.get("human_verdict")
    ]
    binary = [(a == "correct", b == "correct") for a, b in pairs]
    return {
        "labelled": len(pairs),
        "exact_agreement": _rate(sum(a == b for a, b in pairs), len(pairs)),
        "kappa": cohen_kappa(pairs),
        "binary_agreement": _rate(sum(a == b for a, b in binary), len(binary)),
        "binary_kappa": cohen_kappa(binary),
    }


def cmd_agreement(args) -> int:
    result = agreement(_read_jsonl(args.out / SAMPLE_FILE))
    print(json.dumps(result, indent=2))
    return 0 if result["labelled"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "sample", "agreement"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True, help="results directory")
        p.add_argument(
            "--questions",
            type=Path,
            action="append",
            help=f"question file or dir (repeatable; default {DEFAULT_QUESTIONS})",
        )
        if name == "run":
            p.add_argument("--repos", help="comma-separated subset of repos")
            p.add_argument(
                "--repo-dir",
                action="append",
                default=[],
                metavar="REPO=PATH",
                help="local checkout: runs the stdio server and resolves citations",
            )
            p.add_argument(
                "--endpoint", help="streamable-HTTP MCP URL; {repo} is substituted"
            )
            p.add_argument(
                "--token",
                default=os.environ.get("REPOWISE_MCP_TOKEN"),
                help="bearer token for --endpoint (default $REPOWISE_MCP_TOKEN)",
            )
            p.add_argument("--judge-model", default="sonnet")
            p.add_argument("--judge-workers", type=int, default=4)
            p.add_argument("--no-judge", action="store_true")
            p.add_argument(
                "--rejudge",
                action="store_true",
                help="grade every answer again (after a rubric or judge change)",
            )
        if name == "sample":
            p.add_argument("--fraction", type=float, default=0.2)
            p.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    return {"run": cmd_run, "sample": cmd_sample, "agreement": cmd_agreement}[args.cmd](
        args
    )


if __name__ == "__main__":
    sys.exit(main())
