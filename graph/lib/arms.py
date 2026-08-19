"""One protocol every tool implements, so experiments stop knowing about tools.

Before this module there were two hand-written scripts per experiment, one per
arm, and adding a third tool meant a third script for every experiment. That
does not scale to four arms and seven experiments, and worse, it puts the
normalisation decisions -- which paths count, what a callee is called, which
edge kinds are dependencies -- inside experiment code, where each copy drifts
from the others and none of them is tested.

An experiment now takes an arm name. The arm decides how to build, what it
walked, and what its edges mean.

## The two methods that are load-bearing, and why

**`files_seen`** is the one that looks optional. Every cross-arm comparison
intersects on it first. A tool that skips `vendor/` must not be credited with
perfect recall on the part it read, and a tool that walks more files than
another must not be charged for the extra. Getting this wrong produces a
confident number that means nothing, and unlike most measurement mistakes it is
not recoverable after the fact -- the run has to happen again.

**`call_edges`** returns a callee *identity*, not a name, and normalisation
happens here in the adapter rather than in an experiment. Each tool names
callees differently (we emit a qualified name, CodeGraph emits its own node's
`qualified_name`, a JSON-emitting tool may emit whatever its extractor made up)
and the folding rule that makes two arms comparable -- distinct
`(caller_file, line, callee_identity)`, METHODOLOGY rule 2 -- has to be applied
identically on both sides or the comparison is off by whatever the author
needed.

## Paths

Every path an adapter returns is repo-relative, forward-slashed, no leading
`./`. `norm_path` below is the only implementation; adapters call it rather
than rolling their own, because a Windows backslash on one side of a set
intersection silently makes the intersection empty and the resulting zero looks
like a finding.

## Edge kinds

`cross_file_edges(kinds=...)` takes normalised kind names. Only **`calls`** is
guaranteed to mean the same thing on every arm; every other kind is that
tool's own vocabulary (ours distinguishes `type_use`, `method_implements` and
`dispatches_to` where CodeGraph collapses them into `references` and
`instantiates`). `kinds=None` means "this arm's own dependency set", which is
the right default for reproducing a coverage metric and the wrong one for
claiming two arms emit the same thing. `CALLS` is provided as the portable
constant.

## What an adapter must not do

Index in place. `test-repos/<repo>/.codegraph/` holds six frozen indexes that
every published number reconciles against, and regenerating one silently
invalidates every prior result (METHODOLOGY rule 10). `scratch_copy` below
exists so no adapter has to decide this for itself.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

# The one edge kind whose name means the same thing on every arm.
CALLS = frozenset({"calls"})

# Directories no adapter should copy into a scratch tree: version control, a
# prior index from either tool, and the usual dependency dumps that no tool
# should be walking in the first place. Copying `.codegraph/` in particular
# would let a tool read a frozen peer index as if it were source.
_SCRATCH_EXCLUDE = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".codegraph",
        ".repowise",
        ".code-review-graph",
        "graphify-out",
        "node_modules",
        ".venv",
        "__pycache__",
    }
)


def norm_path(path: str | Path, repo_root: str | Path | None = None) -> str:
    """Repo-relative, forward-slashed, no leading `./`.

    The single most expensive class of bug this benchmark can have is a set
    intersection that comes out empty because one arm said `pkg\\a.go` and the
    other said `pkg/a.go`. That produces a zero, and a zero reads like a
    finding rather than like a mistake.

    An absolute path outside *repo_root* is returned as-is (normalised), not
    raised on: some tools emit stdlib or dependency paths in their edge tables
    and those are legitimately outside the tree. They will simply fail to
    intersect with anything, which is the correct outcome.
    """
    p = str(path).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if repo_root is not None:
        root = str(Path(repo_root).resolve()).replace("\\", "/").rstrip("/")
        if p.lower().startswith(root.lower() + "/"):
            p = p[len(root) + 1 :]
    return p.lstrip("/")


@contextmanager
def scratch_copy(repo: str | Path, *, keep: bool = False) -> Iterator[Path]:
    """Copy a repo to a temp dir so a tool can index it without touching it.

    Every arm that writes an index into the tree it indexes must go through
    this. See METHODOLOGY rule 10: the frozen peer indexes are baselines, and a
    tool that writes `.codegraph/` into `test-repos/gitleaks` destroys one.

    `keep=True` leaves the copy behind for inspection after a failed run;
    nothing in the harness passes it, it is for a human at a prompt.
    """
    src = Path(repo).resolve()
    tmp = Path(tempfile.mkdtemp(prefix=f"gq-{src.name}-"))
    dest = tmp / src.name
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(*_SCRATCH_EXCLUDE),
        symlinks=True,
        ignore_dangling_symlinks=True,
    )
    try:
        yield dest
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class Artifact:
    """One arm's index of one repository, plus how much it cost to make.

    `handle` is arm-private: a sqlite connection, an in-memory graph, a parsed
    JSON document. Nothing outside the adapter that produced it may read it.
    """

    arm: str
    version: str
    repo_name: str
    repo_path: Path
    handle: Any = None
    seconds: float | None = None
    peak_rss_mb: float | None = None
    index_size_mb: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def cost_row(self) -> dict[str, Any]:
        """The G6 row for this build. `None` where the arm cannot measure it."""
        return {
            "arm": self.arm,
            "version": self.version,
            "repo": self.repo_name,
            "seconds": round(self.seconds, 3) if self.seconds is not None else None,
            "peak_rss_mb": round(self.peak_rss_mb, 1) if self.peak_rss_mb is not None else None,
            "index_size_mb": (
                round(self.index_size_mb, 2) if self.index_size_mb is not None else None
            ),
        }


@runtime_checkable
class Arm(Protocol):
    """What every tool in this benchmark must be able to answer.

    Implementations live in `graph/arms/<tool>.py`, one per tool, and are
    registered with `register()` at import. They are stateless: everything one
    build produces lives on the `Artifact`, so two repositories can be held
    open at once without either arm's state leaking into the other.
    """

    name: str

    def version(self) -> str:
        """Tool version, recorded in every result. Non-negotiable: Graphify
        shipped a release the same day it was surveyed, and a number without a
        version is unreconcilable a week later."""

    def build(self, repo: Path, *, repo_name: str, fresh: bool = False) -> Artifact:
        """Index a scratch copy of *repo*. Never writes into *repo* itself.

        `fresh=False` lets an arm return a frozen artifact it did not build
        this run -- the six `.codegraph` baselines, which every published
        number reconciles against and which must not be regenerated. Such an
        artifact carries `seconds=None`, because nothing was timed.

        `fresh=True` forces a real build and a real cost measurement. G6 and
        the determinism gate both require it: reopening one frozen file twice
        would pass a determinism check while testing nothing.
        """

    def close(self, art: Artifact) -> None:
        """Release whatever `build` held open. Idempotent."""

    def files_seen(self, art: Artifact) -> set[str]:
        """Every source file the tool walked, whether or not it understood it.

        The shared denominator for every cross-arm comparison. See the module
        docstring for why this is not optional.
        """

    def symbol_files(self, art: Artifact) -> set[str]:
        """Files declaring at least one symbol. The G2 denominator.

        A file walked but yielding no symbol cannot contribute an edge, so the
        gap between this and `files_seen` is itself a finding -- it is how the
        caffeine 128-file gap was sized.
        """

    def call_edges(self, art: Artifact) -> set[tuple[str, int, str]]:
        """Distinct `(caller_file, line, callee_identity)`, folded."""

    def cross_file_edges(
        self, art: Artifact, kinds: frozenset[str] | None = None
    ) -> set[tuple[str, str]]:
        """Distinct `(source_file, target_file)` where the two differ.

        `kinds=None` means this arm's own dependency vocabulary. Only `CALLS`
        is portable across arms; see the module docstring.
        """

    def file_languages(self, art: Artifact) -> dict[str, str]:
        """Path -> language, for every file in `files_seen`.

        Language filtering happens in the experiment, off this mapping, rather
        than inside each adapter's query: caffeine's index carries kotlin and
        python callers, and a figure quoted as java's is wrong unless the
        denominator is java's files.
        """


_REGISTRY: dict[str, Arm] = {}


def register(arm: Arm) -> Arm:
    """Register an adapter under its `name`. Returns it, so it can decorate."""
    if arm.name in _REGISTRY:
        raise ValueError(f"arm {arm.name!r} is already registered")
    _REGISTRY[arm.name] = arm
    return arm


def get_arm(name: str) -> Arm:
    """Look up a registered adapter, importing `graph/arms/` on first use."""
    if not _REGISTRY:
        _load_adapters()
    if name not in _REGISTRY:
        raise KeyError(f"no arm named {name!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def arm_names() -> list[str]:
    if not _REGISTRY:
        _load_adapters()
    return sorted(_REGISTRY)


def _load_adapters() -> None:
    """Import every module under `graph/arms/`, which registers on import.

    Import errors are deliberately not swallowed. An arm that cannot import is
    an arm that would otherwise silently vanish from a results table, and a
    missing row reads as "not run" when it means "broken".
    """
    import importlib
    import sys

    arms_dir = Path(__file__).resolve().parents[1] / "arms"
    if str(arms_dir) not in sys.path:
        sys.path.insert(0, str(arms_dir))
    for mod in sorted(arms_dir.glob("*.py")):
        if mod.stem.startswith("_"):
            continue
        importlib.import_module(mod.stem)


def build_cached(
    arm: Arm, repo: Path, *, repo_name: str, pin: str | None, fresh: bool = False
) -> Artifact:
    """`arm.build`, but reusing a stored artifact when one exists for this pin.

    A competitor artifact depends only on `(tool, tool_version, repo, pin)` and
    not at all on our commit, so indexing thirty repositories with three
    external tools is a cost this benchmark should pay once rather than every
    graph session. See `artifact_cache.py` for the storage layout.

    `fresh=True` bypasses the cache in both directions -- it neither reads nor
    writes -- because G6 and the determinism gate need a real timed build and a
    cache hit is not one. An arm with no `cache_payload` (both of ours, which
    rebuild in seconds and have nothing on disk to keep) falls straight
    through to a normal build.
    """
    if fresh or not pin or not hasattr(arm, "cache_payload"):
        return arm.build(repo, repo_name=repo_name, fresh=fresh)

    import artifact_cache

    version = arm.version()
    hit = artifact_cache.lookup(arm.name, version, repo_name, pin)
    if hit is not None:
        payload, meta = hit
        art = arm.open_cached(payload, Path(repo), repo_name, meta)
        # The cost carried here was measured on a real build, on the date in
        # the metadata. Flagged so no table can quote it as this run's.
        art.extra["from_cache"] = True
        art.extra["cost_measured_at"] = meta.get("stored_at")
        return art

    art = arm.build(repo, repo_name=repo_name, fresh=True)
    payload = arm.cache_payload(art)
    if payload is not None and Path(payload).exists():
        artifact_cache.store(
            arm.name, version, repo_name, pin, Path(payload),
            {"cost": art.cost_row(), "extra": {k: v for k, v in art.extra.items()
                                               if isinstance(v, (str, int, float, bool, type(None)))}},
        )
    return art


def determinism_report(arm: Arm, repo: Path, *, repo_name: str) -> dict[str, Any]:
    """Build *repo* twice with *arm* and compare every set the protocol exposes.

    Gates everything downstream. G5 is unscoreable without it, because a
    mutation's effect cannot be separated from run-to-run drift -- if a
    baseline does not rebuild identically inside one session, the mutation
    scores are noise. And a competitor that turns out to be non-deterministic
    is a publishable finding about that tool, not an inconvenience to work
    around.

    Returns the comparison rather than asserting, so a caller can record the
    size of a mismatch. `smoke.py` asserts on `identical`.

    Builds with `fresh=True` on both passes. Without it an arm holding a frozen
    index would open the same file twice and pass a check that tested nothing,
    which is the exact shape of the four silent failures session 1's smoke
    suite caught.
    """
    runs = []
    for _ in range(2):
        art = arm.build(repo, repo_name=repo_name, fresh=True)
        try:
            runs.append(
                {
                    "files_seen": arm.files_seen(art),
                    "symbol_files": arm.symbol_files(art),
                    "call_edges": arm.call_edges(art),
                    "cross_file_edges": arm.cross_file_edges(art),
                }
            )
        finally:
            arm.close(art)

    a, b = runs
    out: dict[str, Any] = {"arm": arm.name, "repo": repo_name, "identical": True, "sets": {}}
    for key in a:
        only_a, only_b = a[key] - b[key], b[key] - a[key]
        out["sets"][key] = {
            "n_run1": len(a[key]),
            "n_run2": len(b[key]),
            "only_in_run1": len(only_a),
            "only_in_run2": len(only_b),
            # A drifting set is far more useful with an example than with a
            # count: the count says something moved, the sample says what.
            "sample_drift": sorted(map(str, list(only_a)[:3] + list(only_b)[:3])),
        }
        if only_a or only_b:
            out["identical"] = False
    return out
