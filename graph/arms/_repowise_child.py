"""Build our graph in a child process and write the protocol's sets to JSON.

This exists for one column. G6 reports peak memory from a Windows job object
attached to a child process, and every competitor has one because every
competitor is a binary we spawn. We build in process, so our memory cell was
empty -- and reading this process's own peak would have measured the harness,
the interpreter, networkx, and every module the bench imported alongside the
graph.

So: same `lib/ours.py` build, in a process that does nothing else, and exits.
What it costs is what the graph costs.

Deliberately not an entry point in `repowise` itself. A `python -m repowise...`
subcommand would be production surface added for a benchmark's convenience, and
this session does not edit `packages/`. It also would not be the same
measurement: the CLI resolves config, sets up logging and opens a store before
it reaches ingestion, and none of that is graph construction.

Invoked by `arms/repowise_subprocess.py`, never by hand:

    python _repowise_child.py --repo <path> --out <json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import arms  # noqa: E402
import ours  # noqa: E402


def _file_of(graph, node_id: str) -> str | None:
    data = graph.nodes[node_id]
    if data.get("node_type") == "file":
        return node_id
    if data.get("node_type") == "symbol":
        return data.get("file_path")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    built = ours.build_graph(Path(args.repo))

    # Every cross-file edge kind is emitted once, tagged, rather than one pass
    # per edge set. The parent filters. Two subprocess builds to answer two
    # questions about one repository would double G6's cost for nothing.
    tagged: dict[str, list[list[str]]] = {}
    graph = built.graph
    for u, v, data in graph.edges(data=True):
        kind = data.get("edge_type")
        if kind not in ours.DEPENDENCY_KINDS:
            continue
        src, tgt = _file_of(graph, u), _file_of(graph, v)
        if src and tgt and src != tgt:
            tagged.setdefault(kind, []).append([arms.norm_path(src), arms.norm_path(tgt)])

    payload = {
        "files_seen": sorted(arms.norm_path(p) for p in built.walked_files),
        "symbol_files": sorted(arms.norm_path(p) for p in ours.symbol_bearing_files(built)),
        "call_edges": [
            [arms.norm_path(f), line, target]
            for f, line, target in sorted(ours.resolved_call_edges(built))
        ],
        "cross_file_edges_by_kind": {k: sorted(map(list, {tuple(e) for e in v}))
                                     for k, v in tagged.items()},
        "file_languages": {
            arms.norm_path(p): parsed.file_info.language
            for p, parsed in built.parsed_files.items()
        },
        "timings_sec": {
            "walk": round(built.timings.walk, 3),
            "parse": round(built.timings.parse, 3),
            "build": round(built.timings.build, 3),
            "total": round(built.timings.total, 3),
        },
        "calls_raw": len(built.resolved_calls),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
    }
    Path(args.out).write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
