"""A persistent cache of built competitor artifacts, keyed on what produced them.

The scheduling fact this exists for: **a competitor artifact depends only on
`(tool, tool_version, repo, repo_pin)`. It has no dependency on our commit.**
Nothing about a change to our resolver invalidates a CodeGraph index. So the
expensive half of this benchmark -- indexing thirty repositories with three
external tools -- can be paid once and reused by every later graph session,
which re-runs only our own column.

    artifacts/<arm>-<arm_version>/<repo>-<pin8>/
        payload/          the artifact itself, verbatim
        meta.json         what produced it, and the cost measured when it was

## The honesty rule

A restored artifact reports the cost measured **when it was built**, which was a
real timed build, and carries `from_cache: true` plus the timestamp. It never
reports the restore as though it were a build. G6 and the determinism gate keep
using `fresh=True`, which bypasses this module entirely, so no cost number that
gets published has ever come out of a cache.

Any cache whose key includes a tool version is only as good as that version
string. `graphify --version` was 0.9.31 on a day the survey table said 0.9.46;
a stale entry under the wrong version is worse than no cache, so the version
goes in the path rather than in a field somebody could forget to compare.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCH = Path(__file__).resolve().parents[2]
ROOT = BENCH / "artifacts"


def _safe(part: str) -> str:
    """Version strings and repo names go into a path, so anything that would
    escape it or break Windows is folded rather than trusted."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in part)[:64]


def slot(arm: str, arm_version: str, repo_name: str, pin: str) -> Path:
    return ROOT / f"{_safe(arm)}-{_safe(arm_version)}" / f"{_safe(repo_name)}-{pin[:8]}"


def lookup(arm: str, arm_version: str, repo_name: str, pin: str) -> tuple[Path, dict] | None:
    """The cached payload and its metadata, or None.

    A slot with a `meta.json` but no payload is treated as a miss rather than
    an error: an interrupted store leaves exactly that, and the cure is to
    build again, not to make the caller handle a half-written cache.
    """
    d = slot(arm, arm_version, repo_name, pin)
    meta_path, payload = d / "meta.json", d / "payload"
    if not (meta_path.is_file() and payload.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = list(payload.iterdir())
    if not entries:
        return None
    # A single-file payload (a SQLite database) is returned as that file; a
    # multi-file one (graphify's output directory) as the directory. The
    # adapter that stored it knows which shape it asked for.
    inner = entries[0] if len(entries) == 1 and entries[0].is_file() else payload
    return inner, meta


def store(
    arm: str,
    arm_version: str,
    repo_name: str,
    pin: str,
    payload: Path,
    meta: dict[str, Any],
) -> Path:
    """Copy *payload* into its slot and record what produced it.

    Written to a sibling `.partial` directory and renamed, so an interrupted
    store cannot leave a slot that looks complete. A benchmark that silently
    reads half an index is the failure mode this whole file is trying to avoid.
    """
    d = slot(arm, arm_version, repo_name, pin)
    staging = d.with_name(d.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "payload").mkdir(parents=True, exist_ok=True)
    if payload.is_dir():
        shutil.copytree(payload, staging / "payload", dirs_exist_ok=True)
    else:
        shutil.copy2(payload, staging / "payload" / payload.name)
    (staging / "meta.json").write_text(
        json.dumps(
            {
                **meta,
                "arm": arm,
                "arm_version": arm_version,
                "repo": repo_name,
                "pin": pin,
                "stored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    shutil.rmtree(d, ignore_errors=True)
    staging.rename(d)
    return d


def index() -> list[dict]:
    """Every stored entry, for reporting what a prebuild run actually covered."""
    if not ROOT.is_dir():
        return []
    out = []
    for meta_path in sorted(ROOT.glob("*/*/meta.json")):
        try:
            out.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({"broken": str(meta_path)})
    return out
