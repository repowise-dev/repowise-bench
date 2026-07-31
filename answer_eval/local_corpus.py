"""Export a locally generated wiki into the shape the snapshot cache holds.

The runner reads its corpus from a hosted snapshot: ``fetch_snapshot`` returns
the file at ``snapshots/<short_id>.json`` whenever one exists and only downloads
when it does not. ``--rebuild-index`` then rebuilds the full-text rows and the
vectors from those pages — it never renders a page. So a run measures whatever
templates were live when the hosted snapshot was made, and a change to what a
page *says* is invisible to it.

That is fine for the index path and the query path, which is what most of this
harness has measured. It is useless for a change to page content, and one such
change was reported flat before anyone noticed the templates had never run.

Writing an export of a locally generated ``wiki.db`` into the cache closes that:
the runner indexes a corpus this machine rendered, with no other change and no
network. Two rules make the comparison mean anything, and both are here rather
than in a habit:

- Generate **both** sides after deleting ``.repowise``. A run that reuses
  preserved pages carries rows the other side does not have, and page-type
  composition then differs for reasons that are not the change under test.
  :func:`describe_corpus` exists so that can be checked before spending a run.
- Numbers from a local corpus are their own baseline. Page selection differs
  from the hosted run, so they are not comparable with hosted-snapshot numbers.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "CorpusDescription",
    "describe_corpus",
    "export_local_corpus",
    "read_wiki_pages",
]

# A page row as the hosted API serves it. Only these six fields are read back by
# ``parse_snapshot``; the rest of the hosted payload is not reconstructed,
# because inventing values for it would make an export look like a snapshot in
# ways that are not true.
_PAGE_FIELDS = ("page_id", "page_type", "title", "content", "target_path", "summary")


@dataclass
class CorpusDescription:
    """What a corpus contains, in the terms a before/after pair has to match.

    Compare two of these before running an eval. A difference in
    ``pages_by_type`` is a difference the questions will see, and attributing it
    to the change under test is how a preserved-page leftover becomes a finding.
    """

    total_pages: int
    pages_by_type: dict[str, int] = field(default_factory=dict)
    empty_content: int = 0

    def differences(self, other: CorpusDescription) -> list[str]:
        """Human-readable differences against *other*, empty when comparable."""
        out: list[str] = []
        if self.total_pages != other.total_pages:
            out.append(f"total pages {self.total_pages} vs {other.total_pages}")
        for page_type in sorted(set(self.pages_by_type) | set(other.pages_by_type)):
            mine = self.pages_by_type.get(page_type, 0)
            theirs = other.pages_by_type.get(page_type, 0)
            if mine != theirs:
                out.append(f"{page_type} {mine} vs {theirs}")
        return out


def read_wiki_pages(db_path: str | Path) -> list[dict[str, object]]:
    """Every page in a generated ``wiki.db``, as snapshot-shaped dicts.

    Pages whose content is empty are dropped. One of those indexes as a
    retrieval miss that cannot be told apart from a ranking failure afterwards,
    so it is cheaper to lose it here, where the count is reported, than to carry
    it into a number nobody can decompose later.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"no wiki database at {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, page_type, title, content, target_path, summary FROM wiki_pages"
        ).fetchall()
    finally:
        conn.close()

    pages: list[dict[str, object]] = []
    for row in rows:
        content = row["content"] or ""
        if not content.strip():
            continue
        pages.append(
            {
                "page_id": row["id"],
                "page_type": row["page_type"],
                # A page with no title would fail validation on the way back in,
                # and its id is the only other thing that names it.
                "title": row["title"] or row["id"],
                "content": content,
                "target_path": row["target_path"],
                "summary": row["summary"],
            }
        )
    return pages


def describe_corpus(pages: list[dict[str, object]]) -> CorpusDescription:
    """Summarise *pages* so two corpora can be compared before a run."""
    return CorpusDescription(
        total_pages=len(pages),
        pages_by_type=dict(Counter(str(p["page_type"]) for p in pages)),
    )


def export_local_corpus(db_path: str | Path, out_path: str | Path) -> CorpusDescription:
    """Write *db_path*'s pages to *out_path* in snapshot-payload shape.

    ``pages_ready`` and ``pages_total`` are both the exported count, so the
    completeness check in ``parse_snapshot`` passes for a whole export and fails
    for a truncated one, which is the behaviour it has for hosted payloads.
    """
    pages = read_wiki_pages(db_path)
    if not pages:
        raise ValueError(
            f"{db_path} holds no pages with content; indexing it would measure "
            "recall against an empty corpus"
        )

    payload = {
        "available": True,
        "pages": pages,
        "pages_ready": len(pages),
        "pages_total": len(pages),
        # Not a hosted snapshot, and a run should be able to tell from the blob
        # which kind of corpus it measured.
        "wiki_format": "local",
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Written whole then moved, for the same reason the download is: a
    # half-written cache file is indistinguishable from a small corpus.
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(out_path)

    return describe_corpus(pages)
