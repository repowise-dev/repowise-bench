"""Parsing a hosted docs snapshot into the corpus the eval indexes.

The snapshot is the eval's corpus. If parsing quietly drops pages, every recall
number afterwards is measured against a corpus nobody described, and it will
look like a retrieval result rather than a loading bug.
"""

import json

import pytest

from answer_eval.snapshot import (
    SnapshotError,
    SnapshotPage,
    parse_snapshot,
    read_snapshot_file,
)


def payload(pages=None, **overrides):
    pages = (
        pages
        if pages is not None
        else [
            {
                "page_id": "file_page:a.py",
                "page_type": "file_page",
                "title": "a.py",
                "target_path": "a.py",
                "content": "# a.py\n\nOverview.",
                "summary": "Overview.",
            }
        ]
    )
    body = {
        "available": True,
        "pages": pages,
        "pages_ready": len(pages),
        "pages_total": len(pages),
    }
    body.update(overrides)
    return body


class TestParsesAValidSnapshot:
    def test_reads_every_field(self):
        (page,) = parse_snapshot(payload())
        assert page == SnapshotPage(
            page_id="file_page:a.py",
            page_type="file_page",
            title="a.py",
            target_path="a.py",
            content="# a.py\n\nOverview.",
            summary="Overview.",
        )

    def test_keeps_snapshot_order(self):
        pages = [
            {
                "page_id": f"file_page:{n}.py",
                "page_type": "file_page",
                "title": f"{n}.py",
                "target_path": f"{n}.py",
                "content": f"# {n}",
                "summary": "s",
            }
            for n in ("a", "b", "c")
        ]
        assert [p.page_id for p in parse_snapshot(payload(pages))] == [
            "file_page:a.py",
            "file_page:b.py",
            "file_page:c.py",
        ]

    def test_absent_summary_becomes_none_not_empty_string(self):
        pages = [
            {
                "page_id": "p1",
                "page_type": "file_page",
                "title": "t",
                "target_path": "a.py",
                "content": "c",
            }
        ]
        assert parse_snapshot(payload(pages))[0].summary is None


class TestRefusesASnapshotThatWouldSkewTheCorpus:
    def test_not_a_mapping(self):
        with pytest.raises(SnapshotError, match="JSON object"):
            parse_snapshot([1, 2, 3])

    def test_unavailable_snapshot(self):
        with pytest.raises(SnapshotError, match="not available"):
            parse_snapshot(payload(available=False))

    def test_no_pages_key(self):
        body = payload()
        del body["pages"]
        with pytest.raises(SnapshotError, match="pages"):
            parse_snapshot(body)

    def test_zero_pages(self):
        with pytest.raises(SnapshotError, match="no pages"):
            parse_snapshot(payload([]))

    def test_partial_snapshot_is_refused(self):
        """pages_ready < pages_total means generation did not finish.

        Indexing it would measure recall against a corpus missing pages, and
        the miss would read as a retrieval failure.
        """
        with pytest.raises(SnapshotError, match="incomplete"):
            parse_snapshot(payload(pages_ready=1, pages_total=99))

    def test_page_count_disagreeing_with_the_header_is_refused(self):
        """The list must contain what the header claims it does."""
        with pytest.raises(SnapshotError, match="pages_total"):
            parse_snapshot(payload(pages_total=99, pages_ready=99))

    @pytest.mark.parametrize("field", ["page_id", "page_type", "title", "content"])
    def test_page_missing_a_required_field(self, field):
        page = {
            "page_id": "p1",
            "page_type": "file_page",
            "title": "t",
            "target_path": "a.py",
            "content": "c",
        }
        del page[field]
        with pytest.raises(SnapshotError, match=field):
            parse_snapshot(payload([page]))

    def test_blank_content_is_refused(self):
        """A page embedded as an empty string is a hole in the corpus."""
        page = {
            "page_id": "p1",
            "page_type": "file_page",
            "title": "t",
            "target_path": "a.py",
            "content": "   ",
        }
        with pytest.raises(SnapshotError, match="content"):
            parse_snapshot(payload([page]))

    def test_duplicate_page_id_is_refused(self):
        page = {
            "page_id": "p1",
            "page_type": "file_page",
            "title": "t",
            "target_path": "a.py",
            "content": "c",
        }
        with pytest.raises(SnapshotError, match="duplicate"):
            parse_snapshot(payload([page, dict(page)]))


class TestDuplicateTargetPath:
    """Two pages may legitimately share a target_path; page_id is the key.

    The real snapshot has exactly one such pair - the repo overview and the
    architecture diagram both target the repo root. Keying a corpus by
    target_path silently drops one of them, so this must parse cleanly and
    stay countable rather than raise or dedupe.
    """

    def test_both_pages_survive(self):
        pages = [
            {
                "page_id": "repo_overview:r",
                "page_type": "repo_overview",
                "title": "Repository Overview",
                "target_path": "r",
                "content": "overview",
            },
            {
                "page_id": "architecture_diagram:r",
                "page_type": "architecture_diagram",
                "title": "Architecture Diagram",
                "target_path": "r",
                "content": "diagram",
            },
        ]
        parsed = parse_snapshot(payload(pages))
        assert [p.page_id for p in parsed] == ["repo_overview:r", "architecture_diagram:r"]


class TestReadSnapshotFile:
    def test_reads_and_parses(self, tmp_path):
        path = tmp_path / "snap.json"
        path.write_text(json.dumps(payload()), encoding="utf-8")
        assert len(read_snapshot_file(path)) == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SnapshotError, match="does not exist"):
            read_snapshot_file(tmp_path / "nope.json")

    def test_unparseable_file_raises(self, tmp_path):
        path = tmp_path / "snap.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SnapshotError, match="valid JSON"):
            read_snapshot_file(path)
