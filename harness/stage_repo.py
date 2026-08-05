"""Stage a benchmark target repo outside the workspace tree.

Benchmark target repos checked out under the repowise workspace root get
captured by the workspace marker (`.repowise-workspace.yaml`) above them and
served the WRONG index; a freshly spawned stdio server in workspace mode can
also hang its first tool call for minutes. Staging a clean clone outside the
tree sidesteps both, and keeping `.git` in the staged copy lets staleness
experiments advance the worktree to a later commit without re-staging.

Usage:
    python harness/stage_repo.py --src <path-or-url> --ref <sha-or-tag> \
        --dest ~/bench-staging/org_repo [--index-from <dir-containing-.repowise>]

The staged copy never contains CLAUDE.md, .mcp.json, or a workspace marker;
`.repowise/` is present only when --index-from supplies one.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Agent-visible artifacts that would leak context or hijack the served index.
_STRIP = [".repowise-workspace.yaml", ".repowise-workspace", "CLAUDE.md",
          ".mcp.json", ".repowise", ".claude", ".codex"]


def stage(src: str, ref: str, dest: Path, index_from: Path | None,
          force: bool = False) -> Path:
    dest = dest.expanduser().resolve()
    if dest.exists():
        if not force:
            raise SystemExit(f"{dest} exists; pass --force to re-stage")
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "clone", src, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--detach", ref],
                   check=True)

    for name in _STRIP:
        path = dest / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    if index_from is not None:
        src_idx = index_from.expanduser().resolve() / ".repowise"
        if not src_idx.is_dir():
            raise SystemExit(f"no .repowise under {index_from}")
        shutil.copytree(src_idx, dest / ".repowise")

    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    print(f"staged {src} @ {head} -> {dest}")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="local path or git URL")
    ap.add_argument("--ref", required=True, help="commit sha or tag to pin")
    ap.add_argument("--dest", required=True, help="staging destination dir")
    ap.add_argument("--index-from", default=None,
                    help="dir whose .repowise/ is copied into the staged repo")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    stage(args.src, args.ref, Path(args.dest),
          Path(args.index_from) if args.index_from else None, args.force)


if __name__ == "__main__":
    main()
