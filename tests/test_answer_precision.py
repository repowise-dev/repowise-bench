"""Precision Q&A runner: MCP round-trip, citation parsing, H1 metrics, agreement.

The MCP test spawns a tiny local stdio server through the same REPOWISE_EXE
hook the runner uses for ``repowise mcp``, so no index, key or network is
needed.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import answer_precision as ap
from harness.swe_qa_runner import CATEGORY_RUBRICS, build_judge_prompt

FAKE_SERVER = """
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("fake")

@mcp.tool()
def get_answer(question: str) -> str:
    import json
    if "boom" in question:
        raise RuntimeError("kaput")
    return json.dumps({"answer": "It is in a.py.", "confidence": "high",
                       "citations": ["a.py:1-3", "missing.py"]})

mcp.run(transport="stdio")
"""


def _question(qid, question, trap=False):
    return {
        "id": qid,
        "question": question,
        "category": "trap" if trap else "where_defined",
        "trap": trap,
        "reference_answer": "In a.py.",
        "key_facts": ["a.py"],
        "must_cite_files": ["a.py"],
        "forbidden_claims": ["It is in b.py"],
    }


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    sha = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout
    return root, sha.strip()


def test_run_over_stdio_mcp(tmp_path, repo, monkeypatch):
    root, sha = repo
    server = tmp_path / "server.py"
    server.write_text(FAKE_SERVER)
    exe = tmp_path / "fake-repowise"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {server}\n")
    exe.chmod(0o755)
    monkeypatch.setenv("REPOWISE_EXE", str(exe))
    judged = []
    monkeypatch.setattr(
        ap, "judge_answer", lambda *a, **kw: judged.append(kw) or {"verdict": "correct"}
    )
    qfile = tmp_path / "toy.json"
    qfile.write_text(
        json.dumps(
            {
                "repo": "toy",
                "commit": sha,
                "questions": [
                    _question("toy-01", "where is x?"),
                    _question("toy-02", "boom"),
                ],
            }
        )
    )
    out = tmp_path / "out"
    out.mkdir()
    foreign = {"id": "other-01", "repo": "other", "verdict": "correct"}
    (out / "results.jsonl").write_text(json.dumps(foreign) + "\n")

    assert (
        ap.main(
            [
                "run",
                "--questions",
                str(qfile),
                "--repo-dir",
                f"toy={root}",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    rows = {r["id"]: r for r in ap._read_jsonl(out / "results.jsonl")}
    assert rows["other-01"] == foreign  # another repo subset's rows survive
    ok, err = rows["toy-01"], rows["toy-02"]
    assert ok["confidence"] == "high"
    assert ok["citations"] == [
        {"path": "a.py", "resolves": True},
        {"path": "missing.py", "resolves": False},
    ]
    assert ok["must_cite_hit"] == [True]
    assert "kaput" in err["payload"]["error"]
    # Only the real answer reaches the judge; a failed call is graded "error".
    assert len(judged) == 1 and judged[0]["category"] == "precision-qa"
    assert ok["verdict"] == "correct" and err["verdict"] == "error"
    summary = json.loads((out / "summary.json").read_text())
    assert summary["repos"]["toy"]["citation_resolves"] == 0.5
    assert summary["repos"]["toy"]["errors"] == 1


def test_cited_paths_normalizes_and_dedupes():
    payload = {
        "citations": [
            "src/a.py:10-20",
            "./src/a.py",
            {"source_path": "b.ts"},
            {"path": "c.go::Run"},
            ".github/x.yml#L3",
            None,
            "",
        ]
    }
    assert ap.cited_paths(payload) == ["src/a.py", "b.ts", "c.go", ".github/x.yml"]


def _row(qid, verdict, conf="medium", trap=False, cites=()):
    return {
        "id": qid,
        "repo": "r",
        "trap": trap,
        "confidence": conf,
        "verdict": verdict,
        "citations": [{"path": p, "resolves": ok} for p, ok in cites],
        "must_cite_hit": [True],
        "payload": {},
    }


def test_summary_and_score_rows():
    rows = [
        _row("q1", "correct", "high", cites=[("a.py", True)]),
        _row("q2", "confidently_wrong", "high", cites=[("b.py", False)]),
        _row("q3", "partial"),
        _row("q4", "error"),
        _row("t1", "refused_hedged", trap=True),
        _row("t2", "wrong", trap=True),
    ]
    s = ap.summarize(rows)["r"]
    assert s["correct"] == 0.25  # 1 of 4 non-trap; an error is not correct
    assert s["high_tier_correct"] == 0.5
    assert s["confidently_wrong"] == round(1 / 6, 4)
    assert s["trap_refused"] == 0.5
    assert s["citation_resolves"] == 0.5

    findings, labels = ap.score_rows(rows)
    by_key = {(lab["family"], lab["finding_key"]): lab["label"] for lab in labels}
    assert by_key[("get_answer", "q1")] is True
    assert by_key[("get_answer", "q3")] is False
    assert by_key[("get_answer.not_confidently_wrong", "q2")] is False
    assert by_key[("get_answer.trap_refused", "t1")] is True
    assert ("get_answer", "t1") not in by_key  # traps are scored only as traps
    assert by_key[("get_answer.citation_resolves", "q2:b.py")] is False
    tiers = {f["key"]: f["tier"] for f in findings if f["family"] == "get_answer"}
    assert tiers["q1"] == "high"


def test_rows_feed_score_py(tmp_path):
    """The emitted rows score cleanly with the shared scorer when it is present."""
    score_py = Path(
        os.environ.get(
            "KG_SCORE_PY",
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "kg_validate"
            / "score.py",
        )
    )
    if not score_py.exists():
        pytest.skip(f"score.py not found at {score_py}")
    findings, labels = ap.score_rows(
        [_row("q1", "correct", "high"), _row("q2", "partial")]
    )
    ap._write_jsonl(tmp_path / "f.jsonl", findings)
    ap._write_jsonl(tmp_path / "l.jsonl", labels)
    out = tmp_path / "s.json"
    subprocess.run(
        [
            sys.executable,
            str(score_py),
            "--findings",
            str(tmp_path / "f.jsonl"),
            "--labels",
            str(tmp_path / "l.jsonl"),
            "--json",
            str(out),
        ],
        check=False,  # exits 1 when a floor fails, which these toy rows do
    )
    groups = json.loads(out.read_text())["groups"]
    pooled = next(
        g
        for g in groups
        if g["family"] == "get_answer" and g["repo"] == "*" and g["tier"] == "*"
    )
    assert pooled["precision"] == 0.5


def test_cohen_kappa_and_agreement():
    assert ap.cohen_kappa([("a", "a"), ("b", "b")]) == 1.0
    # po = 0.5, pe = 0.5 -> kappa 0
    assert ap.cohen_kappa([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]) == 0.0
    sample = [
        {"judge_verdict": "correct", "human_verdict": "correct"},
        {"judge_verdict": "partial", "human_verdict": "wrong"},
        {"judge_verdict": "wrong", "human_verdict": None},
    ]
    res = ap.agreement(sample)
    assert res["labelled"] == 2 and res["exact_agreement"] == 0.5
    assert res["binary_agreement"] == 1.0


def test_precision_rubric_asks_for_verdict():
    q = _question("t", "Where is Foo?", trap=True)
    prompt = build_judge_prompt(
        q["question"], ap.gold_text(q), "It does not exist.", category="precision-qa"
    )
    assert CATEGORY_RUBRICS["precision-qa"].strip() in prompt
    assert "TRAP QUESTION" in prompt and "FORBIDDEN CLAIMS" in prompt
    for v in ap.VERDICTS:
        assert f'"{v}"' in prompt
