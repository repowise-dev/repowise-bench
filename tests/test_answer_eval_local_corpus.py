"""A locally generated wiki can stand in for a hosted snapshot.

This is what makes a page-content change measurable at all. The runner never
renders a page — it indexes a snapshot and rebuilds full-text rows and vectors
over it — so a template change is invisible to a run against the hosted corpus.
One such change was reported flat before anyone checked whether the templates
had run.

The load-bearing test here is the round trip: what the exporter writes must be
what ``parse_snapshot`` accepts. If those two drift, an export looks like a
corpus and fails at the point where the failure is least readable.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from answer_eval.local_corpus import (
    describe_corpus,
    export_local_corpus,
    read_wiki_pages,
)
from answer_eval.snapshot import parse_snapshot


def _wiki_db(tmp_path, rows):
    """A wiki.db holding *rows*, in the columns the exporter reads."""
    path = tmp_path / "wiki.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE wiki_pages (id TEXT, page_type TEXT, title TEXT, "
        "content TEXT, target_path TEXT, summary TEXT)"
    )
    conn.executemany("INSERT INTO wiki_pages VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


_ROWS = [
    ("file_page:a.py", "file_page", "File: a.py", "# a.py\n\n## Overview\n\nText.", "a.py", "A."),
    (
        "symbol_spotlight:a.py::F",
        "symbol_spotlight",
        "Symbol: F",
        "# F\n\n## Overview\n\nText.",
        "a.py::F",
        "F.",
    ),
]


def test_an_export_is_a_payload_the_runner_can_parse(tmp_path):
    """The round trip. Everything else here is detail next to this.

    The exporter writes the payload and ``parse_snapshot`` reads it, and those
    two live in different files. A field renamed on either side would otherwise
    surface as an unreadable failure in the middle of an eval run.
    """
    out = tmp_path / "snap.json"
    export_local_corpus(_wiki_db(tmp_path, _ROWS), out)

    pages = parse_snapshot(json.loads(out.read_text(encoding="utf-8")))

    assert [p.page_id for p in pages] == ["file_page:a.py", "symbol_spotlight:a.py::F"]
    assert pages[0].content.startswith("# a.py")
    assert pages[0].target_path == "a.py"
    assert pages[0].summary == "A."


def test_page_content_survives_the_round_trip_byte_for_byte(tmp_path):
    """The content is the whole point: it is the thing under test.

    A corpus that loses a section on the way through would report a page-content
    change as flat, which is exactly the failure this module exists to prevent.
    """
    body = "# a.py\n\n## Overview\n\nText.\n\n## Questions this page answers\n\n- What?\n"
    out = tmp_path / "snap.json"
    export_local_corpus(
        _wiki_db(tmp_path, [("file_page:a.py", "file_page", "t", body, "a.py", "s")]), out
    )

    assert parse_snapshot(json.loads(out.read_text(encoding="utf-8")))[0].content == body


def test_an_empty_page_is_dropped_rather_than_indexed(tmp_path):
    """A contentless page scores as a retrieval miss indistinguishable from a
    ranking failure, so it is cheaper to lose it here than in the number."""
    rows = [*_ROWS, ("file_page:empty.py", "file_page", "t", "   ", "empty.py", None)]
    described = export_local_corpus(_wiki_db(tmp_path, rows), tmp_path / "snap.json")

    assert described.total_pages == 2
    assert described.pages_by_type == {"file_page": 1, "symbol_spotlight": 1}


def test_a_page_with_no_title_falls_back_to_its_id(tmp_path):
    """``parse_snapshot`` requires a title, and the id is the only other name."""
    rows = [("file_page:a.py", "file_page", None, "# a.py\n\ntext", "a.py", None)]
    out = tmp_path / "snap.json"
    export_local_corpus(_wiki_db(tmp_path, rows), out)

    assert parse_snapshot(json.loads(out.read_text(encoding="utf-8")))[0].title == "file_page:a.py"


def test_the_counts_say_the_export_is_whole(tmp_path):
    """``pages_ready`` below ``pages_total`` is how a truncated hosted payload
    is refused; an export has to be honest in the same terms."""
    out = tmp_path / "snap.json"
    export_local_corpus(_wiki_db(tmp_path, _ROWS), out)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["pages_ready"] == payload["pages_total"] == 2
    assert payload["available"] is True
    assert payload["wiki_format"] == "local"


def test_an_empty_wiki_raises_rather_than_writing_a_corpus(tmp_path):
    with pytest.raises(ValueError, match="no pages with content"):
        export_local_corpus(_wiki_db(tmp_path, []), tmp_path / "snap.json")


def test_a_missing_database_names_itself(tmp_path):
    with pytest.raises(FileNotFoundError, match="no wiki database"):
        read_wiki_pages(tmp_path / "absent.db")


# ---------------------------------------------------------------------------
# Comparability — the check that would have caught a real leftover
# ---------------------------------------------------------------------------


def test_two_corpora_of_the_same_shape_are_comparable(tmp_path):
    a = describe_corpus(read_wiki_pages(_wiki_db(tmp_path / "a", _ROWS)))
    b = describe_corpus(read_wiki_pages(_wiki_db(tmp_path / "b", _ROWS)))
    assert a.differences(b) == []


def test_preserved_pages_on_one_side_are_reported_as_incomparable(tmp_path):
    """The real case: a run that did not wipe .repowise kept pages from an
    earlier index, and the other side had none of them.

    That difference lands in recall looking exactly like an effect of whatever
    was being tested.
    """
    leftovers = [
        *_ROWS,
        ("api_contract:x.yaml", "api_contract", "t", "# x", "x.yaml", None),
    ]
    clean = describe_corpus(read_wiki_pages(_wiki_db(tmp_path / "clean", _ROWS)))
    reused = describe_corpus(read_wiki_pages(_wiki_db(tmp_path / "reused", leftovers)))

    differences = reused.differences(clean)

    assert "api_contract 1 vs 0" in differences
    assert "total pages 3 vs 2" in differences


@pytest.fixture(autouse=True)
def _tmp_subdirs(tmp_path):
    """``_wiki_db`` is called with sibling directories in the comparison tests."""
    for name in ("a", "b", "clean", "reused"):
        (tmp_path / name).mkdir(exist_ok=True)
