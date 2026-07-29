"""Booting the real answer server against a freshly built index.

The eval calls ``get_answer`` itself, in-process, rather than going through a
subprocess and the MCP wire protocol. It is the same function the tool serves,
with the same retrieval, the same confidence gates and the same synthesis - so
a number measured here is a number about the shipped tool.

Three things this module refuses to let pass quietly, because each of them
produces a full set of plausible numbers that mean nothing:

**A degraded embedder.** The server falls back to a mock embedder when the
configured one fails to initialise, and keeps serving. That is right for a
developer whose API key expired mid-session and wrong for an eval: mock vectors
cannot match a real index, so every question scores near zero and it reads as a
retrieval collapse. Checked after boot, raised on.

**The answer cache.** Confidence and cost comparisons are meaningless if a
second run answers from the first run's cache. The runner sets the disable flag
itself instead of trusting the environment it was launched in.

**A vector store that never finished loading.** The server loads vectors in a
background task so it can accept calls immediately. A run that starts querying
before that finishes measures FTS alone.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The flag that stops a run reading a previous run's answers. Set by the
#: runner rather than required of the caller - a measurement rule that depends
#: on someone exporting a variable is a measurement rule that will be broken.
ANSWER_CACHE_DISABLE_ENV = "REPOWISE_ANSWER_DISABLE_CACHE"

VECTOR_STORE_LOAD_TIMEOUT_SECONDS = 300.0


class ServerSessionError(RuntimeError):
    """The answer server did not come up in a state worth measuring."""


@dataclass(frozen=True)
class EmbedderConfig:
    """The embedding parameters a run is pinned to.

    Recorded verbatim in the result blob. Two runs whose recall differs are
    only comparable if these match, and the only way to know they match is to
    have written them down at the time.
    """

    name: str
    model: str
    dims: int

    def as_env(self) -> dict[str, str]:
        return {
            "REPOWISE_EMBEDDER": self.name,
            "REPOWISE_EMBEDDING_MODEL": self.model,
            "REPOWISE_EMBEDDING_DIMS": str(self.dims),
        }


@asynccontextmanager
async def answer_server(repo_dir: str | Path, embedder: EmbedderConfig):
    """Run the real server lifespan over ``repo_dir``, yielding ``get_answer``.

    The environment is set inside the context and restored on the way out, so a
    test process is not left carrying the run's embedder settings.
    """
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".repowise" / "wiki.db").is_file():
        raise ServerSessionError(
            f"no index at {repo_dir}/.repowise/wiki.db - build one before booting the server"
        )

    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._server import _lifespan, mcp
    from repowise.server.mcp_server.tool_answer.answer import get_answer

    overrides = {**embedder.as_env(), ANSWER_CACHE_DISABLE_ENV: "1"}
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)

    previous_repo_path = _state._repo_path
    _state._repo_path = str(repo_dir)

    try:
        async with _lifespan(mcp):
            await _await_vector_store(_state)
            _require_real_embedder(_state, embedder)
            yield get_answer
    finally:
        _state._repo_path = previous_repo_path
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _await_vector_store(state) -> None:
    """Block until the background vector-store load finishes, or give up loudly."""
    ready = state._vector_store_ready
    if ready is None:
        raise ServerSessionError(
            "server lifespan did not create a vector-store ready event; "
            "the run would have measured full-text search alone"
        )
    try:
        await asyncio.wait_for(ready.wait(), timeout=VECTOR_STORE_LOAD_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise ServerSessionError(
            f"vector stores did not load within {VECTOR_STORE_LOAD_TIMEOUT_SECONDS:.0f}s"
        ) from exc


def _require_real_embedder(state, embedder: EmbedderConfig) -> None:
    """Fail the run if the server quietly fell back to mock vectors.

    ``_embedder_status`` is the server's own report of this. It is surfaced in
    every tool's metadata as a warning; here it has to be fatal, because a
    warning in a log nobody reads becomes a recall number in a table everybody
    reads.
    """
    status = state._embedder_status
    if status is None:
        raise ServerSessionError(
            "server reported no embedder status; cannot confirm the run used real vectors"
        )
    if status.get("degraded"):
        raise ServerSessionError(
            f"server fell back to mock vectors: {status.get('reason', 'no reason given')}"
        )
    active = status.get("active")
    if active != embedder.name:
        raise ServerSessionError(
            f"server is serving embedder {active!r} but the run is pinned to "
            f"{embedder.name!r}; the index was built with a different one"
        )
    logger.info("answer server ready on embedder %s (%s)", embedder.name, embedder.model)
