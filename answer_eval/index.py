"""Building a local repowise index from a docs snapshot.

The eval cannot query the hosted index directly: the API serves page markdown,
not vectors. So a run materialises its own index from the snapshot -- database
rows, full-text index, and vectors -- and asks the real server against that.

Rebuilding is not a workaround, it is the point. Sessions that change *what
gets embedded* (the per-item cap, dropping skeleton pages, unifying the recipe)
cannot be measured against vectors the eval is unable to rebuild.

**The embed recipe is a decision, not a detail.** Three disagree today:

===============  ======================  ==========================
where            embedded text           keyed by
===============  ======================  ==========================
generation       ``content``             ``page_id``
reindex          ``title\\ncontent``      ``page_id``
hosted           ``content``             ``target_path``
===============  ======================  ==========================

This module follows **generation** -- the path a real index is created by --
and records the choice in :class:`IndexBuildReport` so a run's numbers can
never be read against the wrong recipe.

Unlike repowise's generation path, an embedding failure here is fatal. There,
embedding is a RAG enhancement and generation must still finish, so a failure
degrades to a warning. Here the vectors are the entire measurement: a build
that quietly loses some of them yields recall numbers that look like a
retrieval result.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path

from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import LanceDBVectorStore

from answer_eval.snapshot import SnapshotPage

logger = logging.getLogger(__name__)

METADATA_CONTENT_PREVIEW_CHARS = 600
DEFAULT_EMBED_BATCH_SIZE = 64

#: Recorded in the build report. See the module docstring for the alternatives.
EMBED_RECIPE = "content"


class IndexBuildError(RuntimeError):
    """An index could not be built completely enough to score against."""


@dataclass(frozen=True)
class IndexBuildReport:
    """What a build actually produced, for the run's result blob.

    Every field here exists so a number can be traced back to the corpus and
    the recipe that produced it.
    """

    pages_written: int
    vectors_written: int
    embed_failures: int
    embedder: str
    embed_recipe: str
    repo_dir: str
    symbols: dict | None = None
    """The symbol index, when one was built. ``None`` means an index of pages
    alone, which cannot produce a high-confidence answer - see
    :mod:`answer_eval.symbols`."""
    fts_rows: int = 0
    """Rows in ``page_fts``. Recorded because a build once created that table
    and never wrote to it: the full-text arm of retrieval returned nothing for
    every question and the run still reported a fused result. ``0`` on a report
    written before this was measured."""


def embed_item(page: SnapshotPage) -> tuple[str, str, dict]:
    """Build the ``(page_id, text, metadata)`` tuple for one page.

    Mirrors repowise's generation-time recipe: the embedded text is the page
    content alone, and ``title`` rides along as metadata because the serving
    side uses it for the coverage rerank haystack and the grounding corpus.
    """
    return (
        page.page_id,
        page.content,
        {
            "title": page.title,
            "page_type": page.page_type,
            "target_path": page.target_path or "",
            "content": page.content[:METADATA_CONTENT_PREVIEW_CHARS],
            "summary": page.summary or "",
        },
    )


async def write_page_fts(engine) -> int:
    """Index every ``wiki_pages`` row for full-text search. Returns the row count.

    A build used to create the FTS5 table and never write to it, so ``page_fts``
    sat empty while ``get_answer``'s full-text arm returned nothing for every
    question. Retrieval still worked -- the vector arm carried it and the fused
    ranking looked healthy -- so the run reported a hybrid system while
    measuring half of one.

    Written through ``FullTextSearch.index``, the same call the product's
    generation and job paths use, rather than raw SQL: when the FTS schema
    changes, an index built here has to change with it or the eval measures a
    corpus shape no user has.

    Every indexed field is passed, not only the ones the eval happens to think
    of. A page's summary is a fresh paraphrase whose wording need not appear in
    its content, and its target path is not in the content at all — omitting
    either builds a corpus searchable on strictly less text than a real index,
    and a measurement of those fields would come back flat with nothing to say
    they had never been written.

    Idempotent, and cheap enough to run on an index that already exists -- it
    reads the pages already in the database and needs no embedder, no network
    and no spend. That is what lets an index built before this existed repair
    itself instead of being re-embedded.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    fts = FullTextSearch(engine)
    await fts.ensure_index()

    already = await fts.list_indexed_ids()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(Page.id, Page.title, Page.content, Page.summary, Page.target_path)
            )
        ).all()

    written = 0
    for page_id, title, content, summary, target_path in rows:
        if page_id in already:
            continue
        await fts.index(
            page_id,
            title or "",
            content or "",
            summary=summary or "",
            target_path=target_path or "",
        )
        written += 1
    if written:
        logger.info("indexed %d page(s) for full-text search", written)
    return len(await fts.list_indexed_ids())


async def repair_page_fts(repo_dir: str | Path) -> int:
    """Backfill ``page_fts`` in an index on disk. Returns its final row count.

    The reuse path calls this because an index built before the full-text write
    existed is otherwise indistinguishable from a healthy one, and reusing it
    silently measures the vector arm alone.
    """
    repowise_dir = Path(repo_dir) / ".repowise"
    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    try:
        return await write_page_fts(engine)
    finally:
        await engine.dispose()


async def build_index(
    pages: Sequence[SnapshotPage],
    repo_dir: str | Path,
    embedder_name: str,
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    checkout: str | Path | None = None,
    expected_commit: str | None = None,
) -> IndexBuildReport:
    """Materialise a queryable repowise index under ``repo_dir/.repowise``.

    Raises ``IndexBuildError`` rather than returning a partial index: a store
    missing vectors scores as a retrieval miss and there is no way to tell the
    two apart afterwards.

    ``checkout`` is a source tree at the snapshot's commit. Given one, the build
    also writes the symbol rows the answer tool's confidence gates read; without
    one the index holds pages alone and every answer is capped at medium, which
    in the output looks like a calibration finding rather than a missing table.
    """
    if not pages:
        raise IndexBuildError("cannot build an index from no pages")

    seen: set[str] = set()
    for page in pages:
        if page.page_id in seen:
            raise IndexBuildError(f"duplicate page_id in snapshot: {page.page_id}")
        seen.add(page.page_id)

    repo_dir = Path(repo_dir)
    repowise_dir = repo_dir / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    try:
        await init_db(engine)
        pages_written, repository_id = await _write_pages(engine, pages)

        symbol_report = None
        if checkout is not None:
            from answer_eval.symbols import build_symbol_index

            symbol_report = await build_symbol_index(
                engine, repository_id, checkout, expected_commit=expected_commit
            )
        else:
            logger.warning(
                "no checkout given: building an index of pages alone. Every answer "
                "will be capped at medium confidence, because the citation-source "
                "gate demotes any high answer that cannot cite symbol bodies."
            )

        fts_rows = await write_page_fts(engine)
        if fts_rows != pages_written:
            raise IndexBuildError(
                f"full-text index holds {fts_rows} rows for {pages_written} pages. "
                "The answer tool fuses full-text and vector retrieval, so a short "
                "index measures a system that is half missing while every number "
                "still looks like a retrieval result."
            )

        vectors_written, failures = await _embed_pages(
            repowise_dir, pages, embedder_name, batch_size
        )
    finally:
        await engine.dispose()

    return IndexBuildReport(
        pages_written=pages_written,
        vectors_written=vectors_written,
        embed_failures=failures,
        embedder=embedder_name,
        embed_recipe=EMBED_RECIPE,
        repo_dir=str(repo_dir),
        symbols=asdict(symbol_report) if symbol_report else None,
        fts_rows=fts_rows,
    )


BUILD_REPORT_FILENAME = "build_report.json"


def build_report_path(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / ".repowise" / BUILD_REPORT_FILENAME


def write_build_report(report: IndexBuildReport, repo_dir: str | Path) -> Path:
    """Save the build report beside the index it describes.

    An index on disk with no report cannot be scored against - the blob would
    carry numbers with no record of the recipe or embedder behind them. Saving
    it is what makes reusing an existing index safe, and safe reuse is what
    makes iterating on a question set free rather than a re-embed each time.
    """
    import json

    path = build_report_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_build_report(repo_dir: str | Path) -> IndexBuildReport:
    """Load the report for an already-built index, or refuse to reuse it."""
    import json

    path = build_report_path(repo_dir)
    if not path.is_file():
        raise IndexBuildError(
            f"index at {repo_dir} has no build report ({path}). It was built by an "
            "older or interrupted run, so the embedder and recipe behind it are "
            "unknown and its numbers could not be compared to anything. Rebuild it."
        )
    try:
        fields = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IndexBuildError(f"{path} is not valid JSON ({exc.msg})") from exc

    expected = {field.name for field in dataclass_fields(IndexBuildReport)}
    # ``fts_rows`` is optional so a report written before the full-text write
    # existed still loads: the index it describes is repaired on reuse rather
    # than re-embedded.
    optional = {"symbols", "fts_rows"}
    if not (expected - optional) <= set(fields) <= expected:
        raise IndexBuildError(
            f"{path} does not describe an index build: expected fields "
            f"{sorted(expected)}, got {sorted(fields)}"
        )
    return IndexBuildReport(**fields)


async def _write_pages(engine, pages: Sequence[SnapshotPage]) -> tuple[int, str]:
    """Insert one ``Page`` row per snapshot page, and return the repository id.

    The id is returned because symbols are written against the same repository
    row; a symbol filed under a different one joins to nothing at query time.
    """
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence.models import Page, Repository

    # One timestamp for the whole build. These columns are NOT NULL but play no
    # part in retrieval, and a single value keeps two builds of the same
    # snapshot differing only where they genuinely differ.
    built_at = datetime.now(UTC)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        repository = Repository(name="eval-corpus", local_path="eval-corpus")
        session.add(repository)
        await session.flush()

        for page in pages:
            session.add(
                Page(
                    id=page.page_id,
                    repository_id=repository.id,
                    page_type=page.page_type,
                    title=page.title,
                    content=page.content,
                    summary=page.summary or "",
                    target_path=page.target_path or "",
                    source_hash="",
                    model_name="snapshot",
                    provider_name="snapshot",
                    created_at=built_at,
                    updated_at=built_at,
                )
            )
        await session.commit()
    return len(pages), repository.id


async def _embed_pages(
    repowise_dir: Path,
    pages: Sequence[SnapshotPage],
    embedder_name: str,
    batch_size: int,
) -> tuple[int, int]:
    """Embed every page, in batches. Any batch failure fails the build."""
    from repowise.cli.providers.embedders import build_embedder

    lance_dir = repowise_dir / "lancedb"
    lance_dir.mkdir(parents=True, exist_ok=True)
    store = LanceDBVectorStore(str(lance_dir), embedder=build_embedder(embedder_name))

    items = [embed_item(page) for page in pages]
    written = 0
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        try:
            await store.embed_batch(batch)
        except Exception as exc:
            raise IndexBuildError(
                f"embed failed for batch starting at page {start} "
                f"({len(batch)} pages, embedder={embedder_name}): {exc}. "
                "A partially embedded store cannot be scored — the missing "
                "vectors are indistinguishable from a retrieval miss."
            ) from exc
        written += len(batch)
        logger.info("embedded %d/%d pages", written, len(items))

    return written, 0
