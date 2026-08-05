"""Populating the symbol table the confidence gates read.

An index built from wiki pages alone is queryable, scores normally on recall,
and cannot produce a single high-confidence answer - because the answer tool
demotes any high answer that cannot cite a page carrying symbol bodies. That
failure has no signature in the output; it looks like a calibration finding.

These tests hold the two things that keep it from recurring: symbols actually
get written, and the states that would silently produce none are refused.
"""

import subprocess

import pytest

from answer_eval.symbols import (
    SymbolIndexError,
    parse_checkout,
    require_matching_commit,
    resolve_commit,
)


@pytest.fixture
def checkout(tmp_path):
    """A tiny git checkout with one parseable Python file."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "cache.py").write_text(
        'def invalidate(key):\n    """Drop one entry."""\n    return key\n\n\n'
        "class Cache:\n    def get(self, key):\n        return None\n",
        encoding="utf-8",
    )
    for cmd in (
        ["init", "-q"],
        ["config", "user.email", "eval@example.com"],
        ["config", "user.name", "eval"],
        ["add", "-A"],
        ["commit", "-qm", "seed"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True, capture_output=True)
    return tmp_path


class TestResolveCommit:
    def test_reads_head(self, checkout):
        assert len(resolve_commit(checkout)) == 40

    def test_a_directory_that_is_not_a_checkout_raises(self, tmp_path):
        with pytest.raises(SymbolIndexError, match="not a git checkout"):
            resolve_commit(tmp_path / "nope")


class TestRequireMatchingCommit:
    def test_a_matching_short_sha_passes(self, checkout):
        head = resolve_commit(checkout)
        assert require_matching_commit(checkout, head[:8]) == head

    def test_no_expected_commit_accepts_whatever_is_there(self, checkout):
        assert require_matching_commit(checkout, None) == resolve_commit(checkout)

    def test_the_wrong_commit_is_refused(self, checkout):
        """Symbols carry line numbers, so the wrong commit is worse than none.

        Parsing a later commit yields line numbers pointing at whatever moved
        into those positions since, and synthesis is then served real source
        from the wrong place - harder to notice than an empty excerpt.
        """
        with pytest.raises(SymbolIndexError, match="moved"):
            require_matching_commit(checkout, "deadbeefdeadbeef")


class TestParseCheckout:
    def test_finds_symbols_in_a_real_file(self, checkout):
        parsed = {
            info.path: result for info, result in parse_checkout(checkout) if result is not None
        }
        names = {s.name for result in parsed.values() for s in result.symbols}
        assert "invalidate" in names
        assert "Cache" in names

    def test_a_missing_checkout_raises(self, tmp_path):
        with pytest.raises(SymbolIndexError, match="does not exist"):
            list(parse_checkout(tmp_path / "nope"))

    def test_an_unparseable_file_is_counted_not_fatal(self, checkout, monkeypatch):
        """One bad file must not end the build, and must not vanish either.

        A parser regression that silently dropped a language would otherwise
        surface only as lower recall, months later.
        """
        import repowise.core.ingestion.parser as parser

        def boom(info, source):
            raise ValueError("parser said no")

        monkeypatch.setattr(parser, "parse_file", boom)
        results = list(parse_checkout(checkout))
        assert results
        assert all(result is None for _, result in results)


class TestSymbolsAreFiledAgainstTheirFile:
    """The parser leaves `file_path` unset and persistence defaults it to "".

    Forgetting to fill it in writes tens of thousands of rows that join to no
    page and raises nothing. Every hit then arrives with no symbol bodies, the
    citation-source gate demotes every high answer, and the run reports a
    confidence distribution that reads as a finding about the tool.
    """

    async def test_written_symbols_carry_the_path_of_the_file_they_came_from(
        self, checkout, tmp_path
    ):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from repowise.core.persistence.database import create_engine, init_db
        from repowise.core.persistence.models import Repository, WikiSymbol

        from answer_eval.symbols import build_symbol_index

        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}")
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            repo = Repository(name="eval", local_path="eval")
            session.add(repo)
            await session.commit()
            repo_id = repo.id

        report = await build_symbol_index(engine, repo_id, checkout)
        assert report.symbols_written > 0

        async with factory() as session:
            paths = (await session.scalars(select(WikiSymbol.file_path))).all()
        await engine.dispose()

        assert paths
        assert all(p for p in paths), "a symbol with no file_path joins to no page"
        assert "pkg/cache.py" in set(paths)
