"""Graphify (`graphifyy` 0.9.31), on the arms protocol.

`graphify extract <path>` writes `graphify-out/graph.json`: NetworkX node-link
JSON, so nodes are under `nodes` and **edges are under `links`**, not `edges`.

## Three things about this arm that change how its numbers read

**It is the only arm that tags edge confidence.** Every edge carries
`confidence` of `EXTRACTED`, `INFERRED` or `AMBIGUOUS`, with a numeric
`confidence_score` alongside (1.0 and 0.8 respectively in every edge observed).
No other tool in the field exposes this, and it is the interesting thing about
this arm: on gitleaks, **1,372 of its 1,476 call edges are INFERRED** -- only
104 call edges are AST-certain. `call_edges(confidence=...)` exists so G1 can
stratify on it, because a single precision number over a set that is 93%
heuristic tells a reader much less than two numbers do.

**It has no file-walk record**, and this is a genuine gap rather than one we
failed to find. `manifest.json` lists only files it classified as code and
parsed (224 of 458 on gitleaks), not the 200 it skipped as unclassified, the 8
it skipped as sensitive, or the docs. `files_seen` therefore returns what it
*processed*, and that is documented in the result rather than papered over: a
G3 recall figure against this arm is on a denominator the tool chose, so it
flatters it, and any comparison must intersect on the *other* arm's walk.

**It carries no symbol-kind and no language field.** An ordinary Go function
node is distinguished from its file node only by shape: the file node's `label`
equals its `source_file`. `symbol_files` uses exactly that test. Language is
inferred from the extension, which is what every consumer of this format has to
do.

## Run configuration, recorded because it is a choice

Built with `--code-only`. Graphify's full pipeline adds an LLM semantic pass
behind an API key; no other arm in this benchmark calls a model, and letting
one arm do so would compare a graph against a graph plus a language model. The
AST path still produces the whole node and edge set including its INFERRED
heuristics. Stated here because a reader who runs the default command will get
different numbers and deserves to know why.

Not deduplicated: `graphify diagnose multigraph` exists because more than one
edge can join the same pair with different `relation`/`context`. The protocol
folds to distinct sets, so this is handled, but do not assume one edge per pair
when reading the raw file.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import arms  # noqa: E402
import procmeter  # noqa: E402

# Their vocabulary. `calls` is the portable one; `contains` is structural
# (file -> symbol, always same-file) and is excluded from dependency readings
# for the same reason we exclude `defines` and the peer excludes `contains`.
DEPENDENCY_KINDS = frozenset({"calls", "references", "method", "embeds", "defines"})
_STRUCTURAL = frozenset({"contains", "rationale_for"})

_EXT_LANG = {
    ".go": "go", ".py": "python", ".java": "java", ".ts": "typescript",
    ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
    ".cs": "csharp", ".rb": "ruby", ".rs": "rust", ".php": "php",
    ".kt": "kotlin", ".swift": "swift", ".cpp": "cpp", ".cc": "cpp",
    ".c": "c", ".h": "c", ".sh": "shell", ".json": "json", ".mod": "go",
}


def _line(loc: str | None) -> int:
    """`"L58"` -> 58. Missing or malformed becomes -1, matching the peer
    reader's `ifnull(e.line, -1)`, so a lineless edge folds consistently on
    both arms instead of being silently dropped by one of them."""
    if not loc:
        return -1
    m = re.match(r"^L(\d+)$", str(loc).strip())
    return int(m.group(1)) if m else -1


class GraphifyArm:
    name = "graphify"

    def __init__(self) -> None:
        self._version: str | None = None

    def version(self) -> str:
        if self._version is None:
            res = procmeter.run_measured(["graphify", "--version"], shell=True, timeout=120)
            text = ((res.stdout or "") + (res.stderr or "")).strip()
            m = re.search(r"(\d+\.\d+\.\d+)", text)
            self._version = m.group(1) if m else (text.splitlines() or ["unknown"])[-1]
        return self._version

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> arms.Artifact:
        """`fresh` is ignored: nothing is frozen for this arm, every build is new."""
        with arms.scratch_copy(repo) as work:
            res = procmeter.run_measured(
                ["graphify", "extract", ".", "--code-only"],
                cwd=work, shell=True, timeout=7200,
            )
            out_dir = work / "graphify-out"
            graph_json = out_dir / "graph.json"
            if not graph_json.is_file():
                raise RuntimeError(
                    f"graphify wrote no graph.json in {work}\nexit {res.returncode}\n"
                    f"stdout: {res.stdout[-2000:]}\nstderr: {res.stderr[-2000:]}"
                )
            size = arms.dir_size_mb(out_dir) if hasattr(arms, "dir_size_mb") else (
                procmeter.dir_size_mb(out_dir)
            )
            doc = json.loads(graph_json.read_text(encoding="utf-8"))
            manifest_path = out_dir / "manifest.json"
            processed = (
                sorted(json.loads(manifest_path.read_text(encoding="utf-8")))
                if manifest_path.is_file()
                else []
            )
            # graph.json and manifest.json are the two files this arm reads
            # back; `cache/` is graphify's own AST cache and is megabytes of
            # nothing we use. Copied out before the scratch tree is removed,
            # so the artifact cache has something to store.
            kept = Path(tempfile.mkdtemp(prefix=f"gq-gfy-{repo_name}-"))
            shutil.copy2(graph_json, kept / "graph.json")
            if manifest_path.is_file():
                shutil.copy2(manifest_path, kept / "manifest.json")

        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle={"doc": doc, "processed": processed},
            seconds=res.seconds,
            peak_rss_mb=res.peak_rss_mb,
            index_size_mb=size,
            extra={
                "nodes": len(doc.get("nodes", [])),
                "links": len(doc.get("links", [])),
                "directed": doc.get("directed"),
                "files_processed": len(processed),
                "run_flags": ["--code-only"],
                "files_seen_is_processed_only": True,
                "returncode": res.returncode,
                "out_dir": str(kept),
            },
        )

    def cache_payload(self, art: arms.Artifact) -> Path | None:
        d = art.extra.get("out_dir")
        return Path(d) if d else None

    def open_cached(
        self, payload: Path, repo: Path, repo_name: str, meta: dict
    ) -> arms.Artifact:
        payload = Path(payload)
        doc = json.loads((payload / "graph.json").read_text(encoding="utf-8"))
        manifest = payload / "manifest.json"
        processed = (
            sorted(json.loads(manifest.read_text(encoding="utf-8")))
            if manifest.is_file()
            else []
        )
        cost = meta.get("cost", {})
        return arms.Artifact(
            arm=self.name,
            version=self.version(),
            repo_name=repo_name,
            repo_path=Path(repo).resolve(),
            handle={"doc": doc, "processed": processed},
            seconds=cost.get("seconds"),
            peak_rss_mb=cost.get("peak_rss_mb"),
            index_size_mb=cost.get("index_size_mb"),
            extra={
                "nodes": len(doc.get("nodes", [])),
                "links": len(doc.get("links", [])),
                "directed": doc.get("directed"),
                "files_processed": len(processed),
                "run_flags": ["--code-only"],
                "files_seen_is_processed_only": True,
                "out_dir": str(payload),
            },
        )

    def close(self, art: arms.Artifact) -> None:
        art.handle = None
        # The copied-out graph.json belongs to this run unless it came from the
        # cache, which owns its own copy.
        if art.extra.get("from_cache"):
            return
        if art.extra.get("out_dir"):
            shutil.rmtree(art.extra["out_dir"], ignore_errors=True)

    def _nodes(self, art: arms.Artifact) -> dict[str, dict]:
        return {n["id"]: n for n in art.handle["doc"].get("nodes", []) if "id" in n}

    def files_seen(self, art: arms.Artifact) -> set[str]:
        """Files graphify *processed*, not files it walked -- it records no walk.

        Falls back to the files named by nodes when no manifest was written.
        Either way this is the tool's own chosen set, so a recall figure
        computed against it is on a denominator the tool picked. Every
        cross-arm comparison intersects with a real walk from another arm.
        """
        if art.handle["processed"]:
            return {arms.norm_path(p) for p in art.handle["processed"]}
        return {
            arms.norm_path(n["source_file"])
            for n in self._nodes(art).values()
            if n.get("source_file")
        }

    def symbol_files(self, art: arms.Artifact) -> set[str]:
        """Files carrying at least one node that is not the file node itself.

        There is no symbol-kind field. A file node's `label` is its
        `source_file`; anything else attributed to that file is a declaration.
        """
        out: set[str] = set()
        for n in self._nodes(art).values():
            src = n.get("source_file")
            if not src:
                continue
            if arms.norm_path(n.get("label", "")) != arms.norm_path(src):
                out.add(arms.norm_path(src))
        return out

    def call_edges(
        self, art: arms.Artifact, confidence: str | None = None
    ) -> set[tuple[str, int, str]]:
        """Distinct `(caller_file, line, callee node id)` for `relation == calls`.

        `confidence` filters to `EXTRACTED` or `INFERRED`. This is the arm G1
        stratifies on; see the module docstring for why that matters more here
        than anywhere else.
        """
        out: set[tuple[str, int, str]] = set()
        for e in art.handle["doc"].get("links", []):
            if e.get("relation") != "calls":
                continue
            if confidence and e.get("confidence") != confidence:
                continue
            src = e.get("source_file")
            if not src:
                continue
            out.add((arms.norm_path(src), _line(e.get("source_location")), str(e.get("target"))))
        return out

    def cross_file_edges(
        self, art: arms.Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        kinds = DEPENDENCY_KINDS if kinds is None else (kinds - _STRUCTURAL)
        nodes = self._nodes(art)
        out: set[tuple[str, str]] = set()
        for e in art.handle["doc"].get("links", []):
            if e.get("relation") not in kinds:
                continue
            s = nodes.get(str(e.get("source")), {}).get("source_file")
            t = nodes.get(str(e.get("target")), {}).get("source_file")
            if s and t:
                s, t = arms.norm_path(s), arms.norm_path(t)
                if s != t:
                    out.add((s, t))
        return out

    def file_languages(self, art: arms.Artifact) -> dict[str, str]:
        """Inferred from the extension: graphify records no language field."""
        return {
            p: _EXT_LANG.get(Path(p).suffix.lower(), Path(p).suffix.lstrip(".").lower())
            for p in self.files_seen(art)
        }


arms.register(GraphifyArm())
