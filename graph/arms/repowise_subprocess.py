"""repowise's graph, built in a child process, so its cost is measurable.

Identical build to `repowise_inproc.py` -- same `lib/ours.py`, same walk, parse
and resolve -- run through `procmeter.run_measured` so a Windows job object can
report peak memory across the whole process tree. That column was empty for us
and populated for every competitor, which made G6 a table with a hole in the
row that matters most to us.

The two arms must agree exactly, and `smoke.py` asserts it. If they ever
diverge, the subprocess arm is measuring something the in-process arm is not,
and every cost number taken from it describes that difference instead of the
graph.

**What the seconds here include, and why they are honest to compare.** Roughly
a second of interpreter startup and imports lands in this arm's wall clock and
not in the in-process arm's. That is the same tax every competitor pays -- node
starting up for `codegraph`, a python interpreter for `code-review-graph` --
so charging ourselves for it is the comparison a reader wants. The in-process
arm's `timings_sec` is carried through in `extra` for anyone who wants the
split.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import arms  # noqa: E402
import procmeter  # noqa: E402

_CHILD = Path(__file__).resolve().parent / "_repowise_child.py"


class RepowiseSubprocessArm:
    name = "repowise-subprocess"

    def version(self) -> str:
        # Same commit-as-version rule as the in-process arm. Delegated rather
        # than duplicated: two ways of stamping one tree is how two rows of the
        # same table end up claiming different commits.
        return arms.get_arm("repowise").version()

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> arms.Artifact:
        """`fresh` is ignored -- nothing is cached, every build is from scratch.

        The child writes to a temp file rather than stdout. Our ingestion logs
        to stdout at info level, and parsing a JSON document out of a stream
        that also carries "Graph built edge_types=..." is a decoding bug
        waiting for the first repository whose log line contains a brace.
        """
        out = Path(tempfile.mkdtemp(prefix=f"gq-sub-{repo_name}-")) / "graph.json"
        res = procmeter.run_measured(
            [sys.executable, str(_CHILD), "--repo", str(Path(repo).resolve()), "--out", str(out)],
            timeout=7200,
        )
        if not out.is_file():
            raise RuntimeError(
                f"child wrote no graph for {repo_name}\nexit {res.returncode}\n"
                f"stdout: {res.stdout[-2000:]}\nstderr: {res.stderr[-2000:]}"
            )
        payload = json.loads(out.read_text(encoding="utf-8"))
        shutil.rmtree(out.parent, ignore_errors=True)

        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle=payload,
            seconds=res.seconds,
            peak_rss_mb=res.peak_rss_mb,
            index_size_mb=None,  # nothing is written; the graph dies with the child
            extra={
                "in_process_timings_sec": payload["timings_sec"],
                "calls_raw": payload["calls_raw"],
                "nodes": payload["nodes"],
                "edges": payload["edges"],
                "returncode": res.returncode,
            },
        )

    def close(self, art: arms.Artifact) -> None:
        art.handle = None

    def files_seen(self, art: arms.Artifact) -> set[str]:
        return set(art.handle["files_seen"])

    def symbol_files(self, art: arms.Artifact) -> set[str]:
        return set(art.handle["symbol_files"])

    def call_edges(self, art: arms.Artifact) -> set[tuple[str, int, str]]:
        return {(f, line, target) for f, line, target in art.handle["call_edges"]}

    def cross_file_edges(
        self, art: arms.Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        by_kind = art.handle["cross_file_edges_by_kind"]
        wanted = by_kind.keys() if kinds is None else (k for k in by_kind if k in kinds)
        return {(s, t) for k in wanted for s, t in by_kind[k]}

    def file_languages(self, art: arms.Artifact) -> dict[str, str]:
        return dict(art.handle["file_languages"])


arms.register(RepowiseSubprocessArm())
