"""repowise's own graph, built in process, on the arms protocol.

Wraps `lib/ours.py`. The asymmetry with every other arm is real and is stated
rather than hidden: the competitors write an index to disk and this one does
not exist outside the process that built it. That has one measurement
consequence, which is that `peak_rss_mb` is `None` here -- there is no child to
attach a job object to, and reading this process's own peak would measure the
harness, the interpreter and every module the bench imported alongside the
graph.

That is what `arms/repowise_subprocess.py` exists to fix, and why G6's memory
column for us comes from that arm rather than this one. This arm stays because
it is roughly 5s against the subprocess arm's interpreter startup plus 5s, and
every experiment that does not need a cost number should pay the cheaper one.

`cross_file_edges` is derived from the built graph; `call_edges` is not, and
must not be. `GraphBuilder._resolve_calls` collapses several call sites onto
one `calls` edge, which undercounts distinct sites by roughly 27% on gitleaks,
so call edges are observed at `CallResolver.resolve_file` instead. The full
argument, including why the wrap matches on list identity rather than type, is
in `lib/ours.py`'s module docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import arms  # noqa: E402
import ours  # noqa: E402


class RepowiseInProcessArm:
    name = "repowise"

    def version(self) -> str:
        """The commit under measurement, not a package version.

        A `repowise.__version__` would be the same string across every commit
        in a release cycle, and this benchmark's whole point is to tell
        `3594ba75` from its parent. `provenance.git_state` already reads the
        checkout that `repowise.core` was imported from, which is the tree
        actually being measured even when it is a detached worktree.
        """
        import provenance
        import repowise.core

        # packages/core/src/repowise/core/__init__.py -> the checkout root
        root = Path(repowise.core.__file__).resolve().parents[4]
        state = provenance.git_state(root, paths=["packages"])
        return f"{state['head_short']}{'-dirty' if state['dirty'] else ''}"

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> arms.Artifact:
        """`fresh` is ignored: this arm has nothing cached to reuse.

        Every build walks, parses and resolves from scratch, so `fresh=True`
        and `fresh=False` are the same run. Accepting the argument and doing
        nothing with it keeps the protocol uniform; silently having no effect
        is safe here in a way it would not be for an arm with an on-disk index.

        Nothing is copied to scratch either, because nothing is written: the
        graph is held in memory and discarded. This is the one arm that can
        read the repository in place without endangering a frozen baseline.
        """
        built = ours.build_graph(repo)
        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle=built,
            seconds=built.timings.total,
            # No child process, so no job object, so no honest peak. See the
            # module docstring; the subprocess arm supplies this column.
            peak_rss_mb=None,
            index_size_mb=None,
            extra={
                "in_process": True,
                "timings_sec": {
                    "walk": round(built.timings.walk, 3),
                    "parse": round(built.timings.parse, 3),
                    "build": round(built.timings.build, 3),
                },
                "calls_raw": len(built.resolved_calls),
            },
        )

    def close(self, art: arms.Artifact) -> None:
        art.handle = None  # the graph is garbage, nothing to release

    def files_seen(self, art: arms.Artifact) -> set[str]:
        return {arms.norm_path(p) for p in art.handle.walked_files}

    def symbol_files(self, art: arms.Artifact) -> set[str]:
        return {arms.norm_path(p) for p in ours.symbol_bearing_files(art.handle)}

    def call_edges(self, art: arms.Artifact) -> set[tuple[str, int, str]]:
        return {
            (arms.norm_path(f), line, target)
            for f, line, target in ours.resolved_call_edges(art.handle)
        }

    def call_pairs(self, art: arms.Artifact) -> set[tuple[str, str]]:
        return {
            (arms.norm_path(f), target)
            for f, target in ours.resolved_call_pairs(art.handle)
        }

    def cross_file_edges(
        self, art: arms.Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        kinds = ours.DEPENDENCY_KINDS if kinds is None else kinds
        graph = art.handle.graph
        out: set[tuple[str, str]] = set()
        for u, v, data in graph.edges(data=True):
            if data.get("edge_type") not in kinds:
                continue
            src = _file_of(graph, u)
            tgt = _file_of(graph, v)
            if src and tgt and src != tgt:
                out.add((arms.norm_path(src), arms.norm_path(tgt)))
        return out

    def file_languages(self, art: arms.Artifact) -> dict[str, str]:
        """Only parsed files carry a language.

        `walked_files` can exceed `parsed_files` when a file cannot be read off
        disk, so this mapping is allowed to be smaller than `files_seen`. A
        caller filtering by language is therefore filtering to files that
        parsed, which is the intended denominator everywhere it is used.
        """
        return {
            arms.norm_path(path): parsed.file_info.language
            for path, parsed in art.handle.parsed_files.items()
        }


def _file_of(graph, node_id: str) -> str | None:
    """The file a graph node belongs to: a file node is its own path, a symbol
    node carries `file_path`, and anything else contributes no cross-file
    information."""
    data = graph.nodes[node_id]
    if data.get("node_type") == "file":
        return node_id
    if data.get("node_type") == "symbol":
        return data.get("file_path")
    return None


arms.register(RepowiseInProcessArm())
