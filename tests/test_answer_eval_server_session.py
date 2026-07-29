"""Booting the answer server, and the states it must refuse to run in.

None of these boot a real server. They exercise the checks that stand between
a broken boot and a full set of plausible numbers, because every failure mode
here produces a run that finishes and reports something.
"""

import asyncio

import pytest

from answer_eval.server_session import (
    ANSWER_CACHE_DISABLE_ENV,
    EmbedderConfig,
    ServerSessionError,
    _await_vector_store,
    _require_real_embedder,
    answer_server,
    resolve_synthesis_model,
)

EMBEDDER = EmbedderConfig(name="gemini", model="gemini-embedding-001", dims=768)


class FakeState:
    def __init__(self, status=None, ready=True):
        self._embedder_status = status
        self._vector_store_ready = None
        if ready:
            self._vector_store_ready = asyncio.Event()
            self._vector_store_ready.set()


class TestEmbedderConfig:
    def test_env_names_match_what_the_server_reads(self):
        assert EMBEDDER.as_env() == {
            "REPOWISE_EMBEDDER": "gemini",
            "REPOWISE_EMBEDDING_MODEL": "gemini-embedding-001",
            "REPOWISE_EMBEDDING_DIMS": "768",
        }


class TestRequiresARealEmbedder:
    def test_a_healthy_embedder_passes(self):
        state = FakeState({"active": "gemini", "requested": "gemini", "degraded": False})
        _require_real_embedder(state, EMBEDDER)

    def test_a_degraded_embedder_stops_the_run(self):
        """The server falls back to mock vectors and keeps serving. Right for a
        developer, fatal for an eval: mock vectors cannot match a real index, so
        every question scores near zero and it reads as a retrieval collapse."""
        state = FakeState(
            {"active": "mock", "requested": "gemini", "degraded": True, "reason": "no API key"}
        )
        with pytest.raises(ServerSessionError, match="mock vectors"):
            _require_real_embedder(state, EMBEDDER)

    def test_a_different_embedder_than_the_index_was_built_with_stops_the_run(self):
        state = FakeState({"active": "openai", "requested": "openai", "degraded": False})
        with pytest.raises(ServerSessionError, match="pinned"):
            _require_real_embedder(state, EMBEDDER)

    def test_no_status_at_all_stops_the_run(self):
        """Absent status is not proof of health; it is proof of nothing."""
        with pytest.raises(ServerSessionError, match="no embedder status"):
            _require_real_embedder(FakeState(status=None), EMBEDDER)


class TestWaitsForVectors:
    async def test_returns_once_the_load_signals_ready(self):
        await _await_vector_store(FakeState(ready=True))

    async def test_a_missing_ready_event_stops_the_run(self):
        """Without the event there is nothing to wait on, and the run would
        query before the vectors loaded - measuring full-text search alone."""
        with pytest.raises(ServerSessionError, match="full-text search alone"):
            await _await_vector_store(FakeState(ready=False))

    async def test_a_load_that_never_finishes_times_out_loudly(self, monkeypatch):
        import answer_eval.server_session as module

        monkeypatch.setattr(module, "VECTOR_STORE_LOAD_TIMEOUT_SECONDS", 0.01)
        state = FakeState()
        state._vector_store_ready = asyncio.Event()  # never set
        with pytest.raises(ServerSessionError, match="did not load"):
            await _await_vector_store(state)


class TestRefusesToBootWithoutAnIndex:
    async def test_missing_database_names_the_path(self, tmp_path):
        with pytest.raises(ServerSessionError, match="no index at"):
            async with answer_server(tmp_path, EMBEDDER):
                pass


class TestCacheFlag:
    def test_the_runner_owns_the_flag_rather_than_the_environment(self):
        """The measurement rule is that runs never read a previous run's answers.

        A rule that depends on the caller exporting a variable is a rule that
        gets broken, so the name lives here and the session sets it.
        """
        assert ANSWER_CACHE_DISABLE_ENV == "REPOWISE_ANSWER_DISABLE_CACHE"


class TestSynthesisModel:
    def test_no_provider_stops_the_run(self, monkeypatch, tmp_path):
        """Missing synthesis does not fail loudly at answer time.

        The tool degrades to a retrieval-only response, which reads as a tool
        that got more cautious rather than one that had no model at all - so a
        whole run of low-confidence answers would look like a finding.
        """
        import repowise.server.mcp_server.tool_answer.synthesis as synthesis

        monkeypatch.setattr(
            synthesis, "_resolve_provider_for_answer", lambda repo_path=None: None
        )
        with pytest.raises(ServerSessionError, match="no synthesis provider"):
            resolve_synthesis_model(tmp_path)

    def test_the_resolved_provider_and_model_are_reported(self, monkeypatch, tmp_path):
        import repowise.server.mcp_server.tool_answer.synthesis as synthesis

        class FakeProvider:
            provider_name = "gemini"
            model_name = "gemini-3.1-flash-lite-preview"

        monkeypatch.setattr(
            synthesis, "_resolve_provider_for_answer", lambda repo_path=None: FakeProvider()
        )
        resolved = resolve_synthesis_model(tmp_path)
        assert resolved.provider == "gemini"
        assert resolved.model == "gemini-3.1-flash-lite-preview"
