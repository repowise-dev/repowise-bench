"""Building a local repowise index from a docs snapshot.

Uses the mock embedder throughout, so these run with no API key and no spend.
What they assert is the bookkeeping, not the vector quality: that every page
reaches the database, that the embed recipe is the one recorded in the report,
and above all that a partial build is refused rather than measured.
"""

import pytest

from answer_eval.index import (
    IndexBuildError,
    build_index,
    build_report_path,
    embed_item,
    read_build_report,
    write_build_report,
)
from answer_eval.snapshot import SnapshotPage


def page(page_id="file_page:a.py", content="# a.py\n\nIt parses things.", **kw):
    fields = {
        "page_id": page_id,
        "page_type": "file_page",
        "title": "a.py",
        "target_path": "a.py",
        "content": content,
        "summary": "It parses things.",
    }
    fields.update(kw)
    return SnapshotPage(**fields)


class TestEmbedItem:
    """The recipe must match repowise's generation-time recipe exactly.

    Generation embeds page content alone and carries title / page_type /
    target_path / summary as metadata. Reindex embeds "title\\ncontent"
    instead, and hosted embeds bare content keyed by target_path. Those three
    disagree, so which one the eval uses is a decision, not a detail -- it is
    asserted here and recorded in the build report.
    """

    def test_text_is_content_alone(self):
        page_id, text, _ = embed_item(page())
        assert page_id == "file_page:a.py"
        assert text == "# a.py\n\nIt parses things."

    def test_metadata_carries_the_serving_side_fields(self):
        _, _, metadata = embed_item(page())
        assert metadata == {
            "title": "a.py",
            "page_type": "file_page",
            "target_path": "a.py",
            "content": "# a.py\n\nIt parses things.",
            "summary": "It parses things.",
        }

    def test_long_content_is_clipped_only_in_metadata(self):
        """metadata['content'] is a 600-char preview; the embedded text is whole."""
        long_page = page(content="x" * 2000)
        _, text, metadata = embed_item(long_page)
        assert len(text) == 2000
        assert len(metadata["content"]) == 600

    def test_absent_summary_becomes_empty_string_not_none(self):
        """The vector store's metadata must not carry None where a str is expected."""
        _, _, metadata = embed_item(page(summary=None))
        assert metadata["summary"] == ""


class TestBuildIndex:
    async def test_writes_every_page_and_reports_the_recipe(self, tmp_path):
        report = await build_index(
            [page("p1"), page("p2", target_path="b.py")],
            repo_dir=tmp_path,
            embedder_name="mock",
        )
        assert report.pages_written == 2
        assert report.vectors_written == 2
        assert report.embed_failures == 0
        assert report.embedder == "mock"
        assert report.embed_recipe == "content"

    async def test_creates_a_queryable_database(self, tmp_path):
        await build_index([page("p1")], repo_dir=tmp_path, embedder_name="mock")
        assert (tmp_path / ".repowise" / "wiki.db").is_file()

    async def test_no_pages_raises(self, tmp_path):
        with pytest.raises(IndexBuildError, match="no pages"):
            await build_index([], repo_dir=tmp_path, embedder_name="mock")

    async def test_duplicate_page_ids_raise(self, tmp_path):
        with pytest.raises(IndexBuildError, match="duplicate"):
            await build_index(
                [page("p1"), page("p1")], repo_dir=tmp_path, embedder_name="mock"
            )

    async def test_an_embedding_failure_fails_the_build(self, tmp_path, monkeypatch):
        """A half-embedded store scores as a retrieval miss, so it must not be scoreable.

        repowise's generation path deliberately downgrades an embed failure to
        a warning, because embedding is a RAG enhancement there and generation
        must still finish. Here the vectors are the entire point: a build that
        silently loses some of them produces recall numbers that look like a
        retrieval result.
        """
        import answer_eval.index as index_module

        async def boom(self, items):
            raise RuntimeError("embedder said no")

        monkeypatch.setattr(
            index_module.LanceDBVectorStore, "embed_batch", boom, raising=True
        )
        with pytest.raises(IndexBuildError, match="embed"):
            await build_index([page("p1")], repo_dir=tmp_path, embedder_name="mock")


class TestBuildReportRoundTrip:
    """An index is only safe to reuse if the report that describes it survives.

    Reuse is what keeps a question-set iteration free instead of a re-embed.
    The price is that an index with no report, or a report that does not match
    the fields a build produces, must be refused rather than guessed at.
    """

    async def test_written_report_reads_back_identical(self, tmp_path):
        report = await build_index([page("p1")], repo_dir=tmp_path, embedder_name="mock")
        write_build_report(report, tmp_path)
        assert read_build_report(tmp_path) == report

    def test_an_index_with_no_report_is_refused(self, tmp_path):
        with pytest.raises(IndexBuildError, match="no build report"):
            read_build_report(tmp_path)

    def test_an_unparseable_report_is_refused(self, tmp_path):
        path = build_report_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(IndexBuildError, match="valid JSON"):
            read_build_report(tmp_path)

    def test_a_report_missing_a_field_is_refused_rather_than_defaulted(self, tmp_path):
        """A missing `embedder` must not read as an index built by nobody."""
        import json

        path = build_report_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"pages_written": 3}), encoding="utf-8")
        with pytest.raises(IndexBuildError, match="does not describe an index build"):
            read_build_report(tmp_path)
