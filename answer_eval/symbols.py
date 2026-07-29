"""Populating the symbol table the answer tool's confidence gates depend on.

A docs snapshot carries wiki pages and nothing else. An index built from pages
alone is queryable and scores fine on recall - and cannot reach high confidence
at all, for a reason that looks nothing like a harness bug in the output.

The citation-source gate demotes any high-confidence answer that does not cite
a page carrying hydrated symbols::

    if confidence == "high":
        cited = set(citations)
        if not any(h.get("symbols") for h in hits if h.get("target_path") in cited):
            confidence = "medium"

With no symbol rows, ``h["symbols"]`` is empty on every hit, so that condition
holds for every question and every ``high`` becomes ``medium``. The same
missing data forces ``earn_high`` false, which closes the only other route. The
result is a clean run reporting zero high-confidence answers, which reads as a
finding about the tool's calibration rather than an empty table.

So the eval parses a real checkout at the snapshot's commit and writes the
symbol rows itself. Symbols are keyed by file path, which is what the answer
tool joins retrieval hits on, and the source excerpts are read live from that
same checkout - which is why the checkout has to stay on disk for the run, not
just for the build.

**The checkout must be at the commit the snapshot was generated from.** Symbols
carry line numbers. A checkout at a later commit yields line numbers that point
at whatever moved into those positions since, and the excerpts served to
synthesis would be real code from the wrong place - the hardest kind of wrong
answer to notice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Files bigger than this are skipped by the traverser. Matches the ingestion
#: default; changing it here would index a different file set than a real repo.
MAX_FILE_SIZE_KB = 500

SYMBOL_BATCH_SIZE = 500


class SymbolIndexError(RuntimeError):
    """A checkout could not be turned into the symbol rows a run needs."""


@dataclass(frozen=True)
class SymbolIndexReport:
    """What parsing a checkout produced, for the run's result blob."""

    checkout_path: str
    commit: str
    files_parsed: int
    files_failed: int
    symbols_written: int


async def _require_filed_symbols(engine, repository_id: str) -> None:
    """Refuse an index whose symbols carry no file path.

    The parser leaves ``file_path`` unset and the persistence layer reads it
    with a ``""`` default, so forgetting to fill it in writes 25,000 rows that
    join to no page and raises nothing. Every hit then arrives with no symbol
    bodies, the citation-source gate demotes every high answer, and the run
    reports a confidence distribution that looks like a finding about the tool.

    This is the check that turns that into an error at build time.
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence.models import WikiSymbol

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        unfiled = await session.scalar(
            select(func.count())
            .select_from(WikiSymbol)
            .where(WikiSymbol.repository_id == repository_id, WikiSymbol.file_path == "")
        )
    if unfiled:
        raise SymbolIndexError(
            f"{unfiled} symbols were written with an empty file_path. They join to "
            "no page, so every retrieval hit arrives without symbol bodies and the "
            "citation-source gate caps every answer at medium confidence."
        )


def resolve_commit(checkout: Path) -> str:
    """The commit a checkout is on, recorded so a run names its source tree."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SymbolIndexError(f"{checkout} is not a git checkout: {exc}") from exc
    return out.stdout.strip()


def require_matching_commit(checkout: Path, expected_commit: str | None) -> str:
    """Refuse a checkout that is not at the commit the snapshot came from.

    Symbols carry line numbers. Parsing a later commit produces line numbers
    that point at whatever moved into those positions since, so synthesis is
    served real code from the wrong place - which is far harder to spot than
    an empty excerpt.
    """
    actual = resolve_commit(checkout)
    if expected_commit and not actual.startswith(expected_commit.strip()):
        raise SymbolIndexError(
            f"checkout {checkout} is at {actual[:12]} but the snapshot was generated "
            f"from {expected_commit}. Symbol line numbers from the wrong commit point "
            "into code that moved, and synthesis would be served real source from the "
            "wrong place."
        )
    return actual


def parse_checkout(checkout: str | Path):
    """Yield ``(FileInfo, ParsedFile)`` for every source file in a checkout.

    Uses repowise's own traverser and parser, so the file set and the symbol
    shapes match what a real index would contain rather than an approximation
    of them.
    """
    from repowise.core.ingestion.parser import parse_file
    from repowise.core.ingestion.traverser import FileTraverser

    checkout = Path(checkout)
    if not checkout.is_dir():
        raise SymbolIndexError(f"checkout does not exist: {checkout}")

    traverser = FileTraverser(checkout, max_file_size_kb=MAX_FILE_SIZE_KB)
    for info in traverser.traverse():
        try:
            source = Path(info.abs_path).read_bytes()
        except OSError as exc:
            logger.warning("could not read %s: %s", info.path, exc)
            yield info, None
            continue
        try:
            yield info, parse_file(info, source)
        except Exception as exc:
            # One unparseable file must not end the build - but it must be
            # counted, because a parser regression that silently drops a
            # language would otherwise show up only as lower recall.
            logger.warning("could not parse %s: %s", info.path, exc)
            yield info, None


async def build_symbol_index(
    engine,
    repository_id: str,
    checkout: str | Path,
    *,
    expected_commit: str | None = None,
    batch_size: int = SYMBOL_BATCH_SIZE,
) -> SymbolIndexReport:
    """Parse ``checkout`` and write its symbols against ``repository_id``.

    Raises rather than returning an empty index: zero symbols is precisely the
    state that silently caps every answer at medium confidence.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence.crud.external_systems import batch_upsert_symbols

    checkout = Path(checkout)
    commit = require_matching_commit(checkout, expected_commit)

    files_parsed = files_failed = symbols_written = 0
    batch: list = []

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        for info, parsed in parse_checkout(checkout):
            if parsed is None:
                files_failed += 1
                continue
            files_parsed += 1
            # The parser leaves file_path unset - it knows the symbol, not where
            # the file sits in the repo - and the persistence layer reads it with
            # a "" default, so unset means an empty column and no error. Symbols
            # then join to no page, hits carry no bodies, and every answer is
            # capped at medium. repowise's own pipeline sets it here too.
            for symbol in parsed.symbols:
                if not getattr(symbol, "file_path", None):
                    symbol.file_path = info.path
            batch.extend(parsed.symbols)
            if len(batch) >= batch_size:
                await batch_upsert_symbols(session, repository_id, batch)
                symbols_written += len(batch)
                batch = []
        if batch:
            await batch_upsert_symbols(session, repository_id, batch)
            symbols_written += len(batch)
        await session.commit()

    if not symbols_written:
        raise SymbolIndexError(
            f"parsed {files_parsed} files from {checkout} and found no symbols. "
            "An index with no symbols caps every answer at medium confidence, "
            "because the citation-source gate demotes any high-confidence answer "
            "that cannot cite a page carrying symbol bodies."
        )

    await _require_filed_symbols(engine, repository_id)

    logger.info(
        "symbol index: %d symbols from %d files (%d unparseable) at %s",
        symbols_written,
        files_parsed,
        files_failed,
        commit[:12],
    )
    return SymbolIndexReport(
        checkout_path=str(checkout.resolve()),
        commit=commit,
        files_parsed=files_parsed,
        files_failed=files_failed,
        symbols_written=symbols_written,
    )
