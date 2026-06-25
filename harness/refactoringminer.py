"""RefactoringMiner oracle — external, type-level confirmation of a generated refactoring.

The repowise refactoring layer generates a diff from a deterministic plan and
self-checks it in-process with an LCOM4/TCC cohesion delta (a *metric* answer:
"did cohesion improve?"). This module adds the complementary *type* answer:
RefactoringMiner (MIT, https://github.com/tsantalis/RefactoringMiner) detects
which refactoring kinds occur between two commits with very high precision, so
it can confirm a generated change is genuinely an "Extract Class" / "Move
Method" rather than merely a cohesion-friendly edit.

It is Java-only and commit-based, so it lives here in the bench harness rather
than in the product: you apply a generated refactoring as a commit on a Java
test repo, then run this oracle on that commit.

Usage (gated on the ``REFACTORINGMINER_JAR`` env var pointing at the runnable
RefactoringMiner jar; skips cleanly when unset, like the other best-effort
harness checks):

    REFACTORINGMINER_JAR=/path/RefactoringMiner.jar \\
      python -m harness.refactoringminer --repo /path/java-repo --commit <sha> \\
        --expect "Extract Class" --before-file src/A.java --after-file src/A.java

    # Validate the JSON parser without Java present:
    python -m harness.refactoringminer --self-test

The TCC before/after delta reuses repowise core's class walker
(``walk_file(...).classes[*].tcc``), so the same metric the in-process
self-check reports is computed here over the Java working tree.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- repowise core on sys.path (for the TCC walker) ------------------------
# This module lives at <repowise>/repowise-bench/harness/, so parents[2] is the
# monorepo root. Add the core package src so ``repowise.core`` imports resolve
# without an installed wheel, matching the runner convention.
_REPOWISE_ROOT = Path(os.environ.get("REPOWISE_ROOT") or Path(__file__).resolve().parents[2])
_CORE_SRC = _REPOWISE_ROOT / "packages" / "core" / "src"
if _CORE_SRC.exists() and str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

_ENV_JAR = "REFACTORINGMINER_JAR"
_DEFAULT_TIMEOUT_S = 600


# ---------------------------------------------------------------------------
# Availability gate
# ---------------------------------------------------------------------------


def jar_path() -> Path | None:
    """The RefactoringMiner jar from ``REFACTORINGMINER_JAR``, if it exists."""
    raw = os.environ.get(_ENV_JAR)
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_file() else None


def refactoringminer_available() -> tuple[bool, str]:
    """Whether the oracle can run: jar configured + a ``java`` on PATH.

    Returns ``(ok, reason)`` so the caller can ``skip`` with a clear message —
    the same posture as the other gated harness checks.
    """
    if jar_path() is None:
        return False, f"{_ENV_JAR} unset or not a file (RefactoringMiner oracle skipped)"
    if shutil.which("java") is None:
        return False, "java not found on PATH (RefactoringMiner oracle skipped)"
    return True, "ready"


# ---------------------------------------------------------------------------
# RefactoringMiner invocation + JSON parsing
# ---------------------------------------------------------------------------


@dataclass
class DetectedRefactoring:
    type: str
    description: str = ""


def parse_rm_json(text: str) -> list[DetectedRefactoring]:
    """Parse RefactoringMiner ``-json`` output into a flat refactoring list.

    RM emits ``{"commits": [{"sha1": ..., "refactorings": [{"type", "description"}]}]}``.
    Tolerant of an empty/garbled file (returns ``[]``) so a parse problem never
    masquerades as "no refactorings detected" without a traceback at the call site.
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    out: list[DetectedRefactoring] = []
    for commit in data.get("commits", []) or []:
        if not isinstance(commit, dict):
            continue
        for r in commit.get("refactorings", []) or []:
            if isinstance(r, dict) and r.get("type"):
                out.append(
                    DetectedRefactoring(type=str(r["type"]), description=str(r.get("description", "")))
                )
    return out


def run_refactoringminer(
    repo_dir: Path, commit: str, *, timeout_s: int = _DEFAULT_TIMEOUT_S
) -> list[DetectedRefactoring]:
    """Detect the refactorings introduced by *commit* (vs its first parent).

    Invokes ``java -jar <jar> -bc <repo> <commit> -json <tmp>`` and parses the
    result. Models the subprocess shape on the product's ``distill`` wrapper:
    captured, text, UTF-8, errors replaced. Raises ``RuntimeError`` if the jar
    is unavailable (callers should gate with :func:`refactoringminer_available`
    first) or the process fails.
    """
    jar = jar_path()
    if jar is None:
        raise RuntimeError(f"{_ENV_JAR} is not set to a valid jar")

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "rm.json"
        cmd = [
            "java",
            "-jar",
            str(jar),
            "-bc",
            str(repo_dir),
            commit,
            "-json",
            str(out_path),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        if proc.returncode != 0 and not out_path.exists():
            raise RuntimeError(
                f"RefactoringMiner failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
            )
        text = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
    return parse_rm_json(text)


def confirms_type(refactorings: list[DetectedRefactoring], expected_type: str) -> bool:
    """Whether any detected refactoring matches *expected_type* (case-insensitive)."""
    want = expected_type.strip().lower()
    return any(r.type.strip().lower() == want for r in refactorings)


# Map the repowise refactoring_type to the RefactoringMiner type label, so a
# caller can hand us the plan's own type string.
RM_TYPE_FOR: dict[str, str] = {
    "extract_class": "Extract Class",
    "extract_helper": "Extract Method",  # the closest RM kind for clone -> shared helper
    "move_method": "Move Method",
    "break_cycle": "",  # RM has no "break import cycle" kind; not oracle-checkable
}


# ---------------------------------------------------------------------------
# TCC before/after — reuse the product's class walker
# ---------------------------------------------------------------------------


def tcc_by_class(source: bytes, *, language: str = "java", filename: str = "A.java") -> dict[str, float]:
    """``{class_name: tcc}`` for *source*, via repowise core's walker.

    Best-effort: returns ``{}`` if the walker / language pack is unavailable, so
    the oracle degrades to the RefactoringMiner verdict alone.
    """
    try:
        from repowise.core.analysis.health.complexity import walk_file
    except Exception:
        return {}
    try:
        fc = walk_file(filename, language, source)
    except Exception:
        return {}
    return {c.name: round(float(getattr(c, "tcc", 1.0)), 3) for c in getattr(fc, "classes", []) or []}


@dataclass
class TccDelta:
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    before_min: float | None = None
    after_min: float | None = None
    # True when the worst class's cohesion did not get worse (split classes
    # should each be at least as tight as the original blob).
    improved: bool = False


def tcc_delta(before: bytes, after: bytes, *, language: str = "java") -> TccDelta:
    """Compare TCC across two versions of a file's source."""
    b = tcc_by_class(before, language=language)
    a = tcc_by_class(after, language=language)
    b_min = min(b.values(), default=None)
    a_min = min(a.values(), default=None)
    improved = b_min is not None and a_min is not None and a_min >= b_min and len(a) >= len(b)
    return TccDelta(before=b, after=a, before_min=b_min, after_min=a_min, improved=bool(improved))


# ---------------------------------------------------------------------------
# Combined oracle verdict
# ---------------------------------------------------------------------------


@dataclass
class OracleVerdict:
    available: bool
    reason: str
    expected_type: str = ""
    rm_type: str = ""
    detected: list[dict] = field(default_factory=list)
    type_confirmed: bool = False
    tcc: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(
    repo_dir: Path,
    commit: str,
    *,
    refactoring_type: str,
    before_source: bytes | None = None,
    after_source: bytes | None = None,
    language: str = "java",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> OracleVerdict:
    """Run both halves of the oracle for one generated refactoring.

    *refactoring_type* is the repowise plan type (e.g. ``extract_class``); it is
    mapped to the RefactoringMiner label. When *before_source*/*after_source*
    are given, the TCC before/after delta is computed too.
    """
    ok, reason = refactoringminer_available()
    rm_label = RM_TYPE_FOR.get(refactoring_type, "")
    verdict = OracleVerdict(available=ok, reason=reason, expected_type=refactoring_type, rm_type=rm_label)

    if before_source is not None and after_source is not None:
        verdict.tcc = tcc_delta(before_source, after_source, language=language).__dict__

    if not ok:
        return verdict
    if not rm_label:
        verdict.reason = f"no RefactoringMiner type maps to '{refactoring_type}'"
        return verdict

    detected = run_refactoringminer(repo_dir, commit, timeout_s=timeout_s)
    verdict.detected = [asdict(d) for d in detected]
    verdict.type_confirmed = confirms_type(detected, rm_label)
    verdict.reason = "ok"
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SELF_TEST_JSON = """
{"commits": [{"sha1": "abc", "refactorings": [
  {"type": "Extract Class", "description": "Extract Class Foo from Bar"},
  {"type": "Move Method", "description": "Move Method baz()"}
]}]}
"""


def _self_test() -> int:
    parsed = parse_rm_json(_SELF_TEST_JSON)
    assert [r.type for r in parsed] == ["Extract Class", "Move Method"], parsed
    assert confirms_type(parsed, "extract class") is True
    assert confirms_type(parsed, "Inline Method") is False
    assert parse_rm_json("not json") == []
    print("refactoringminer self-test: OK (parser + type match)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RefactoringMiner oracle for a generated refactoring")
    parser.add_argument("--self-test", action="store_true", help="validate the JSON parser (no Java needed)")
    parser.add_argument("--repo", type=Path, help="path to the Java git repo")
    parser.add_argument("--commit", help="commit SHA that applied the generated refactoring")
    parser.add_argument("--type", default="extract_class", help="repowise refactoring_type")
    parser.add_argument("--expect", help="override the expected RefactoringMiner type label")
    parser.add_argument("--before-file", type=Path, help="file content before (for TCC)")
    parser.add_argument("--after-file", type=Path, help="file content after (for TCC)")
    parser.add_argument("--language", default="java")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.repo or not args.commit:
        parser.error("--repo and --commit are required (or use --self-test)")

    before = args.before_file.read_bytes() if args.before_file and args.before_file.exists() else None
    after = args.after_file.read_bytes() if args.after_file and args.after_file.exists() else None

    verdict = evaluate(
        args.repo,
        args.commit,
        refactoring_type=args.type,
        before_source=before,
        after_source=after,
        language=args.language,
    )
    if args.expect:
        verdict.type_confirmed = confirms_type(
            [DetectedRefactoring(**d) for d in verdict.detected], args.expect
        )
        verdict.rm_type = args.expect

    print(json.dumps(verdict.to_dict(), indent=2))
    if not verdict.available:
        return 0  # skipped, not a failure
    return 0 if verdict.type_confirmed else 1


if __name__ == "__main__":
    raise SystemExit(main())
