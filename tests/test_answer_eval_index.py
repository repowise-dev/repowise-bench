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


class TestFullTextRows:
    """The full-text arm has to be *in* the index, not merely created.

    A build created the FTS5 table and never wrote a row to it, so
    ``get_answer``'s full-text search matched nothing for every question. The
    vector arm carried retrieval and the fused ranking looked healthy, so the
    run reported a hybrid system while measuring half of one. Nothing raised,
    nothing warned, and the recall figures read as normal results.
    """

    async def _fts_count(self, repo_dir) -> int:
        import sqlite3

        con = sqlite3.connect(repo_dir / ".repowise" / "wiki.db")
        try:
            return con.execute("select count(*) from page_fts").fetchone()[0]
        finally:
            con.close()

    async def test_a_build_indexes_every_page_for_full_text_search(self, tmp_path):
        report = await build_index(
            [page("p1"), page("p2", target_path="b.py")],
            repo_dir=tmp_path,
            embedder_name="mock",
        )
        assert report.fts_rows == 2
        assert await self._fts_count(tmp_path) == 2

    async def test_a_question_matches_a_page_by_its_content(self, tmp_path):
        """The end the eval actually depends on: text in a page is findable."""
        from repowise.core.persistence.database import create_engine
        from repowise.core.persistence.search import FullTextSearch

        await build_index(
            [page("p1", content="# a.py\n\nThe walker skips nested git checkouts.")],
            repo_dir=tmp_path,
            embedder_name="mock",
        )
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / '.repowise' / 'wiki.db'}")
        try:
            hits = await FullTextSearch(engine).search("nested git checkouts", limit=5)
        finally:
            await engine.dispose()
        assert [h.page_id for h in hits] == ["p1"]

    async def test_a_page_is_findable_by_its_summary(self, tmp_path):
        """The summary is an indexed field, so the eval index has to carry it.

        A page's summary is a fresh paraphrase — none of its wording is
        guaranteed to appear in the content. Writing the row without it builds
        a corpus searchable on strictly less text than a real index, and any
        measurement of the summary column would come back flat with nothing to
        say it had never been tested.
        """
        from repowise.core.persistence.database import create_engine
        from repowise.core.persistence.search import FullTextSearch

        await build_index(
            [page("p1", summary="Reconciles the changelog against the release manifest.")],
            repo_dir=tmp_path,
            embedder_name="mock",
        )
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / '.repowise' / 'wiki.db'}")
        try:
            hits = await FullTextSearch(engine).search("release manifest", limit=5)
        finally:
            await engine.dispose()
        assert [h.page_id for h in hits] == ["p1"]

    async def test_a_page_is_findable_by_its_target_path(self, tmp_path):
        """Same for the path column: a question naming a directory must hit."""
        from repowise.core.persistence.database import create_engine
        from repowise.core.persistence.search import FullTextSearch

        await build_index(
            [
                # Neither the title, the content nor the summary carries
                # "telemetry" or "collector" — only the path does.
                page(
                    "p1",
                    title="Counter flushing",
                    target_path="packages/telemetry/collector.py",
                    content="# Overview\n\nGathers counters and flushes them on a timer.",
                    summary="Gathers counters.",
                )
            ],
            repo_dir=tmp_path,
            embedder_name="mock",
        )
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / '.repowise' / 'wiki.db'}")
        try:
            hits = await FullTextSearch(engine).search("telemetry collector", limit=5)
        finally:
            await engine.dispose()
        assert [h.page_id for h in hits] == ["p1"]

    async def test_a_short_full_text_index_fails_the_build(self, tmp_path, monkeypatch):
        """Same rule the vectors get: a half-written arm is not scoreable."""
        import answer_eval.index as index_module

        async def write_nothing(_engine):
            return 0

        monkeypatch.setattr(index_module, "write_page_fts", write_nothing)
        with pytest.raises(IndexBuildError, match="full-text"):
            await build_index([page("p1")], repo_dir=tmp_path, embedder_name="mock")

    async def test_repair_backfills_an_index_that_has_none(self, tmp_path):
        """An index built before the write existed repairs itself rather than
        being re-embedded -- full-text rows need no embedder and cost nothing."""
        import sqlite3

        await build_index([page("p1"), page("p2")], repo_dir=tmp_path, embedder_name="mock")
        con = sqlite3.connect(tmp_path / ".repowise" / "wiki.db")
        con.execute("delete from page_fts")
        con.commit()
        con.close()
        assert await self._fts_count(tmp_path) == 0

        from answer_eval.index import repair_page_fts

        assert await repair_page_fts(tmp_path) == 2
        assert await self._fts_count(tmp_path) == 2

    async def test_repair_is_idempotent(self, tmp_path):
        from answer_eval.index import repair_page_fts

        await build_index([page("p1")], repo_dir=tmp_path, embedder_name="mock")
        assert await repair_page_fts(tmp_path) == 1
        assert await repair_page_fts(tmp_path) == 1
        assert await self._fts_count(tmp_path) == 1

    async def test_a_report_without_the_field_still_loads(self, tmp_path):
        """Old reports describe an index that is repairable, not one to discard."""
        import json

        report = await build_index([page("p1")], repo_dir=tmp_path, embedder_name="mock")
        write_build_report(report, tmp_path)
        path = build_report_path(tmp_path)
        fields = json.loads(path.read_text())
        del fields["fts_rows"]
        path.write_text(json.dumps(fields))

        assert read_build_report(tmp_path).fts_rows == 0


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
