"""Mechanical pass of harness/wiki_claims.py on a tiny git fixture.

Needs the kg_validate oracles: run from repowise/repowise-bench, or set
REPOWISE_ROOT to a repowise checkout.
"""

import subprocess
from pathlib import Path

import pytest

from harness import wiki_claims as wc

PAGE = """## What it does

It is small. It depends on `pkg/b.py` for helpers.
It also relies on `pkg/c.py` for formatting.

Key consumers include:
- `app/main.py`
- `app/other.py`

| File | Role |
|---|---|
| `pkg/a.py` | Entry module that calls `helper`. |
| `pkg/missing.py` | Does not exist. |

```sh
not a claim at all
```

## Questions this page answers

- Which module calls `helper`?

## Concept index

| Concept | Symbol | File |
| --- | --- | --- |
| Helper | `helper` | `pkg/b.py` |
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@x", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def facts(tmp_path_factory) -> wc.RepoFacts:
    root = tmp_path_factory.mktemp("repo")
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "from pkg.b import helper\n\n\ndef main():\n    return helper()\n",
        "pkg/b.py": "def helper():\n    return 1\n",
        "pkg/c.py": "def fmt(x):\n    return str(x)\n",
        "app/main.py": "from pkg.a import main\n",
        "app/other.py": "print('standalone')\n",
    }
    _git(root, "init", "-q")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    for i in range(2):
        (root / "pkg/b.py").write_text(files["pkg/b.py"] + f"# rev {i}\n")
        _git(root, "commit", "-qam", f"fix: helper edge case {i}")
    return wc.RepoFacts(root)


def _claim(text: str, context: str = "", kind: str = "sentence") -> wc.Claim:
    refs = wc.REF.findall(text)
    return wc.Claim(wc.claim_key("p", text), "p", "s", kind, text, context, refs)


def test_extract_claims_units_and_skips():
    claims = wc.extract_claims("p", PAGE)
    texts = [c.text for c in claims]
    assert "It depends on `pkg/b.py` for helpers." in texts
    assert "It also relies on `pkg/c.py` for formatting." in texts
    item = next(c for c in claims if c.text == "`app/main.py`")
    assert item.kind == "item" and item.context == "Key consumers include:"
    row = next(c for c in claims if c.text.startswith("| `pkg/a.py`"))
    assert row.kind == "row" and row.context == "| File | Role |"
    assert row.refs == ["pkg/a.py", "helper"]
    # code blocks, questions and the deterministic concept index are not claims
    assert not any(
        "not a claim" in t or "Which module" in t or "Concept" in t for t in texts
    )
    # short filler without references is skipped; keys are stable
    assert "It is small." not in texts
    assert [c.key for c in claims] == [c.key for c in wc.extract_claims("p", PAGE)]


def test_check_ref(facts):
    assert wc.check_ref(facts, "pkg/a.py") == "ok_path"
    assert wc.check_ref(facts, "pkg") == "ok_ident"  # a bare dir name reads as a name
    assert wc.check_ref(facts, "pkg/*.py") == "ok_path"
    assert wc.check_ref(facts, "b.py") == "ok_path"  # unique suffix
    assert wc.check_ref(facts, "pkg/b") == "ok_path"  # module path without extension
    assert wc.check_ref(facts, "pkg/missing.py") == "bad_path"
    assert wc.check_ref(facts, "helper") == "ok_ident"
    assert wc.check_ref(facts, "helper()") == "ok_ident"
    assert wc.check_ref(facts, "nonexistentThing") == "bad_ident"
    assert wc.check_ref(facts, "pnpm test") == "skip"
    assert wc.check_ref(facts, "pkg/.../x.py") == "skip"
    assert wc.check_ref(facts, ".d.ts") == "skip"


def test_check_imports(facts):
    scope = ["pkg/__init__.py", "pkg/a.py"]
    ok = _claim("It depends on `pkg/b.py` for helpers.")
    assert wc.check_imports(facts, ok, scope) == []
    bad = _claim("It also relies on `pkg/c.py` for formatting.")
    assert wc.check_imports(facts, bad, scope) == [
        "import oracle: no import edge between the page scope and `pkg/c.py`"
    ]
    explicit = _claim("`pkg/c.py` imports `pkg/b.py` directly.")
    assert len(wc.check_imports(facts, explicit, scope)) == 1
    consumer = _claim("`app/main.py`", context="Key consumers include:", kind="item")
    assert wc.check_imports(facts, consumer, scope) == []
    phantom = _claim("`app/other.py`", context="Key consumers include:", kind="item")
    assert len(wc.check_imports(facts, phantom, scope)) == 1
    # direction is not graded, co-change and table rows are left alone
    assert (
        wc.check_imports(facts, _claim("`pkg/b.py` is used by `pkg/a.py`."), scope)
        == []
    )
    assert (
        wc.check_imports(
            facts, _claim("It co-changes with and depends on `pkg/c.py`."), scope
        )
        == []
    )
    row = _claim("| `pkg/c.py` | imported by the scope |", kind="row")
    assert wc.check_imports(facts, row, scope) == []


def test_check_counts(facts):
    assert (
        wc.check_counts(facts, _claim("There are 2 bug-fix commits in `pkg/b.py`."))
        == []
    )
    over = _claim("There are 5 bug-fix commits in `pkg/b.py`.")
    assert wc.check_counts(facts, over) == [
        "count: 5 bug-fix commits claimed for `pkg/b.py`, which has 3 commits in total"
    ]
    attached = _claim("The hotspot `pkg/b.py` (7 commits) dominates.")
    assert len(wc.check_counts(facts, attached)) == 1
    # a module-wide total followed by "most in X" is not about X alone
    total = _claim("The module saw 40 bug-fix commits, most in `pkg/b.py`.")
    assert wc.check_counts(facts, total) == []


def test_mechanical_labels_wrong_only_on_oracle_failure(facts):
    scope = ["pkg/a.py"]
    good = _claim("`pkg/a.py` calls `helper` from `pkg/b.py`.")
    wc.mechanical(facts, good, scope)
    assert good.label is None and good.checks == []
    bad = _claim("`pkg/a.py` calls `missingFn` from `pkg/nope.py`.")
    wc.mechanical(facts, bad, scope)
    assert bad.label == "wrong" and bad.source == "mechanical"
    assert bad.checks == [
        "symbol: `missingFn` occurs nowhere in the repo",
        "path: `pkg/nope.py` does not exist",
    ]


def test_score_rows_and_agreement():
    claims = [_claim(f"`pkg/a.py` claim number {i} here.") for i in range(4)]
    for c, label in zip(claims, ["supported", "wrong", "misleading", wc.NOT_A_CLAIM]):
        c.label, c.source = label, "llm"
    baseline = [
        {"key": claims[0].key, "label": "supported"},
        {"key": "gone", "label": "supported"},
    ]
    findings, labels, truth = wc.score_rows("r", claims, baseline)
    assert len(findings) == 6  # 3 judged claims x 2 families; not_a_claim excluded
    wiki = {
        lab["finding_key"]: lab["label"] for lab in labels if lab["family"] == "wiki"
    }
    not_wrong = {
        lab["finding_key"]: lab["label"]
        for lab in labels
        if lab["family"] == "wiki.not_wrong"
    }
    assert wiki == {claims[0].key: True, claims[1].key: False, claims[2].key: False}
    assert not_wrong == {claims[0].key: True, claims[1].key: False, claims[2].key: True}
    assert truth == [
        {"repo": "r", "family": "wiki", "key": claims[0].key},
        {"repo": "r", "family": "wiki", "key": "gone"},
    ]
    gold = {
        "groups": [
            {
                "name": "g",
                "pages": ["p"],
                "counts": {"correct": 1, "wrong": 1, "misleading": 0, "speculative": 0},
            }
        ],
        "claims": [
            {"page_id": "p", "label": "wrong", "anchors": ["NUMBER 1"]},
            {"page_id": "p", "label": "correct", "anchors": ["number 0"]},
            {"page_id": "p", "label": "wrong", "anchors": ["absent"]},
        ],
    }
    a = wc.agreement(claims, gold)
    assert a["audit_supported_rate"] == 0.5
    assert a["ours_supported_rate"] == round(1 / 3, 4)
    assert (
        a["claim_level"]["matched"] == 2 and a["claim_level"]["binary_agreement"] == 1.0
    )
    assert len(a["unmatched"]) == 1


def test_review_sample_is_stratified():
    claims = []
    for label, n in (
        ("supported", 80),
        ("wrong", 10),
        ("misleading", 6),
        ("speculative", 4),
    ):
        for i in range(n):
            c = _claim(f"{label} claim {i} with words")
            c.label = label
            claims.append(c)
    sample = wc.review_sample(claims, 50)
    counts = {lab: sum(c.label == lab for c in sample) for lab in wc.LABELS}
    assert len(sample) == 50 and len({c.key for c in sample}) == 50
    assert (
        counts["speculative"] == 4
        and counts["misleading"] >= 5
        and counts["wrong"] >= 5
    )
    assert wc.review_sample(claims, 50) == sample  # seeded
