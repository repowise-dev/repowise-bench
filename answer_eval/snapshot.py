"""Reading a hosted docs snapshot -- the corpus the eval indexes and measures.

A snapshot is pulled from the hosted API::

    GET api.repowise.dev/repos/{owner}/{name}/latest   -> {"short_id": ...}
    GET api.repowise.dev/snapshots/{short_id}/docs     -> {"pages": [...]}

and pinned by short id so a run is reproducible against a named corpus. The
local ``.repowise/wiki.db`` is deliberately not a source here: it drifts from
what the hosted index actually serves, and measuring the wrong corpus produces
numbers that look like retrieval results.

**Everything in this module raises rather than skips.** If parsing drops pages,
every recall figure afterwards is computed against a corpus nobody described,
and the shortfall reads as a retrieval failure instead of a loading bug. That
includes the two header checks: a snapshot whose generation did not finish
(``pages_ready < pages_total``), and one whose page list disagrees with its own
header, are both refused.

One thing that is explicitly *not* an error: two pages sharing a
``target_path``. Pages are keyed by ``page_id``, which is unique. The repo
overview and the architecture diagram both target the repository root, so a
corpus keyed by ``target_path`` silently loses one of them.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_PAGE_FIELDS = ("page_id", "page_type", "title", "content")


class SnapshotError(ValueError):
    """A snapshot is missing, unparseable, incomplete, or internally inconsistent."""


@dataclass(frozen=True)
class SnapshotPage:
    """One generated wiki page, as the hosted API serves it."""

    page_id: str
    page_type: str
    title: str
    target_path: str | None
    content: str
    summary: str | None = None


def _require_str(page: dict, field: str, index: int) -> str:
    value = page.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(
            f"page at position {index}: {field} must be a non-empty string, got {value!r}"
        )
    return value


def parse_snapshot(payload: Any) -> list[SnapshotPage]:
    """Validate a ``/snapshots/{short_id}/docs`` payload and return its pages.

    Preserves snapshot order. Raises ``SnapshotError`` on anything that would
    make the corpus differ from what the snapshot claims to contain.
    """
    if not isinstance(payload, dict):
        raise SnapshotError(f"snapshot must be a JSON object, got {type(payload).__name__}")

    if payload.get("available") is False:
        raise SnapshotError("snapshot reports itself not available; nothing to index")

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        raise SnapshotError("snapshot has no 'pages' list")
    if not raw_pages:
        raise SnapshotError("snapshot contains no pages")

    ready = payload.get("pages_ready")
    total = payload.get("pages_total")
    if isinstance(ready, int) and isinstance(total, int) and ready < total:
        raise SnapshotError(
            f"snapshot is incomplete: {ready} of {total} pages ready. "
            "Indexing it would measure recall against a corpus missing pages."
        )
    if isinstance(total, int) and len(raw_pages) != total:
        raise SnapshotError(
            f"snapshot serves {len(raw_pages)} pages but pages_total says {total}"
        )

    pages: list[SnapshotPage] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(raw_pages):
        if not isinstance(raw, dict):
            raise SnapshotError(f"page at position {index} must be a JSON object")

        values = {field: _require_str(raw, field, index) for field in REQUIRED_PAGE_FIELDS}
        page_id = values["page_id"]
        if page_id in seen_ids:
            raise SnapshotError(f"page at position {index}: duplicate page_id {page_id!r}")
        seen_ids.add(page_id)

        target_path = raw.get("target_path")
        summary = raw.get("summary")
        pages.append(
            SnapshotPage(
                page_id=page_id,
                page_type=values["page_type"],
                title=values["title"],
                target_path=target_path if isinstance(target_path, str) and target_path else None,
                content=values["content"],
                summary=summary if isinstance(summary, str) and summary.strip() else None,
            )
        )

    _warn_on_shared_target_paths(pages)
    return pages


def _warn_on_shared_target_paths(pages: list[SnapshotPage]) -> None:
    """Log pages that share a target_path.

    Not an error here -- ``page_id`` is the key and stays unique. It is logged
    because any consumer keying by ``target_path`` drops all but one of them,
    and that loss is otherwise invisible.
    """
    counts = Counter(page.target_path for page in pages if page.target_path)
    shared = {path: n for path, n in counts.items() if n > 1}
    if not shared:
        return
    for path, n in sorted(shared.items()):
        ids = [p.page_id for p in pages if p.target_path == path]
        logger.warning(
            "%d pages share target_path %r (%s); a corpus keyed by target_path keeps only one",
            n,
            path,
            ", ".join(ids),
        )


def read_snapshot_file(path: str | Path) -> list[SnapshotPage]:
    """Parse a snapshot payload previously saved to disk."""
    path = Path(path)
    if not path.is_file():
        raise SnapshotError(f"snapshot file does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"{path} is not valid JSON ({exc.msg})") from exc
    return parse_snapshot(payload)
