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
from dataclasses import dataclass
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


async def build_index(
    pages: Sequence[SnapshotPage],
    repo_dir: str | Path,
    embedder_name: str,
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
) -> IndexBuildReport:
    """Materialise a queryable repowise index under ``repo_dir/.repowise``.

    Raises ``IndexBuildError`` rather than returning a partial index: a store
    missing vectors scores as a retrieval miss and there is no way to tell the
    two apart afterwards.
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
        pages_written = await _write_pages(engine, pages)

        fts = FullTextSearch(engine)
        await fts.ensure_index()

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
    )


async def _write_pages(engine, pages: Sequence[SnapshotPage]) -> int:
    """Insert one ``Page`` row per snapshot page, under a single repository."""
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
    return len(pages)


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
