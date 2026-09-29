#!/usr/bin/env python3
"""Wiki-claims runner: label every factual claim in LLM-written wiki pages.

Port of the audit's ``docs_audit/refcheck.py`` (which only checked backticked
references) into a full claim grader:

1. **Extract** claims: prose sentences, list items (with their lead-in line)
   and table rows (with their header). Deterministic appendices (concept
   index, packages table, ...) and "Questions this page answers" are skipped.
2. **Mechanical pass** (authoritative when it fails), checked against the
   repo checkout and the kg_validate oracles:
   - backticked paths must exist (tracked file or directory),
   - backticked identifiers must occur somewhere in tracked source text,
   - "A imports / depends on / is imported by B" must be an edge of the
     import oracle (``scripts/kg_validate/oracles/imports.py``),
   - "N (bug-fix) commits" about one path can't exceed that path's history.
3. **LLM verification** of the remaining claims, batched per page, with the
   cited source in context: file excerpts for named paths, definition sites
   and mention counts for named symbols, git facts, oracle import edges.
4. **Labels**: supported / wrong / misleading / speculative (plus
   ``not_a_claim`` for filler, which is excluded from every rate).

Outputs (``--out DIR``):
- ``claims.jsonl``  one row per claim with label, source and reason;
- ``findings.jsonl`` / ``labels.jsonl`` / ``truth.jsonl`` score.py rows for
  families ``wiki`` (precision = supported rate; recall vs ``--baseline``
  supported claims) and ``wiki.not_wrong`` (precision = 1 - wrong rate);
- ``review.csv``    a stratified sample of ``--review-n`` claims for a human;
- ``summary.json``  rates, LLM token use and cost estimate, and agreement
  with ``--gold`` labels when given.

Claim keys are ``sha1(page_id, normalised text)``: a verifier that drops
sentences (never rewrites them) keeps the survivors' keys, which is what the
"keep >= 60% of the baseline's supported claims" recall floor needs.

Usage::

    REPOWISE_ROOT=/path/to/repowise python harness/wiki_claims.py \\
        --repo composio --docs data/wiki_claims/composio_audit_pages.json \\
        --checkout /path/to/composio --gold data/wiki_claims/composio_audit_labels.json \\
        --out results/wiki_claims/composio

The LLM is Gemini over REST (``GEMINI_API_KEY``); ``--model`` picks it and
``--max-usd`` stops spending once the estimate crosses the cap.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import fnmatch
import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from itertools import chain
from pathlib import Path, PurePosixPath

LABELS = ("supported", "wrong", "misleading", "speculative")
NOT_A_CLAIM = "not_a_claim"
# Deterministic (template-appended) sections: cut the page at the first one.
PROSE_CUTS = (
    "\n## Concept index",
    "\n## What it does",
    "\n## Packages",
    "\n## Architecture map",
)
SKIP_SECTIONS = ("questions this page answers",)
DEFAULT_MODEL = "gemini-2.5-flash"
# USD per 1M tokens (input, output incl. thinking). Estimates only.
PRICES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3-flash-preview": (0.50, 3.00),
}
MAX_FILE_BYTES = 2_000_000
# Suffixes tried for a module path cited without its extension.
MODULE_EXTS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".go",
    "/index.ts",
    "/index.js",
    "/__init__.py",
)
SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".scala", ".c", ".h", ".cc", ".cpp",
}  # fmt: skip
PATHY_EXT = re.compile(
    r"\.(ts|tsx|js|jsx|mjs|cjs|py|go|rs|java|json|md|mdx|toml|ya?ml|sh|cfg|lock)$"
)
IDENT_TOKEN = re.compile(r"[A-Za-z_@$][\w$@/.-]*")
WORD = re.compile(r"[A-Za-z_$][\w$]*")
REF = re.compile(r"`([^`\n]{2,160})`")
# "A imports / depends on / is consumed by B" (verb forms, not "import graph").
IMPORT_VERB = re.compile(
    r"\b(imports|import (?=these|this|the|from|it|them|`)|imported (by|from)|depends? on|"
    r"dependenc(y|ies) on|relies on|pulls? in|consumed by|consumers? (include|are)|used by|importers?)\b",
    re.IGNORECASE,
)
NOT_IMPORT = re.compile(r"co-chang|coupling|co-evolv", re.IGNORECASE)
COMMITS = re.compile(
    r"\b(\d+)\s+(bug[- ]fix(?:es)?|fix(?:es)?)?\s*commits?\b", re.IGNORECASE
)
GIT_WORDS = re.compile(
    r"\b(commits?|bug[- ]fix|fix(es)?|hotspot|churn|active|activity|owner|maintainer|"
    r"bus factor|authored|contributors?)\b",
    re.IGNORECASE,
)
DEF_KINDS = (
    r"(?:class|def|async def|function\*?|interface|type|enum|const|let|var|struct|func|trait|"
    r"object|record|namespace|module)"
)
FIX_SUBJECT = re.compile(r"\bfix", re.IGNORECASE)


# ----------------------------------------------------------------- extraction


@dataclass
class Claim:
    key: str
    page_id: str
    section: str
    kind: str  # sentence | item | row | heading
    text: str
    context: str = ""
    refs: list[str] = field(default_factory=list)
    label: str | None = None
    source: str | None = None  # mechanical | llm
    reason: str = ""
    checks: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def claim_key(page_id: str, text: str) -> str:
    return hashlib.sha1(f"{page_id}\0{_norm(text)}".encode()).hexdigest()[:16]


def prose_part(content: str) -> str:
    for cut in PROSE_CUTS:
        if cut in content:
            content = content[: content.index(cut)]
    return content


_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z`*(\[“\"])")
_ABBREV = re.compile(r"\b(e\.g|i\.e|etc|vs|approx|incl)\.$", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for piece in _SENT_SPLIT.split(text):
        if parts and _ABBREV.search(parts[-1]):
            parts[-1] += " " + piece
        else:
            parts.append(piece)
    return [p.strip() for p in parts if p.strip()]


def _is_claim(text: str) -> bool:
    t = text.strip()
    if t.endswith("?"):
        return False
    return len(t.split()) >= 4 or bool(REF.search(t))


def extract_claims(page_id: str, content: str) -> list[Claim]:
    """Split one page's prose into claim units (sentences, list items, table rows)."""
    out: list[Claim] = []
    seen: set[str] = set()
    section = ""
    in_code = False
    para: list[str] = []
    leadins: list[tuple[int, str]] = []  # (indent, lead-in text) for the current list
    table_header = ""
    last_para_leadin = ""
    last_item = ""

    def add(text: str, kind: str, context: str = "") -> None:
        text = _norm(text)
        if not text or not _is_claim(text):
            return
        key = claim_key(page_id, text)
        if key in seen:
            return
        seen.add(key)
        refs = list(dict.fromkeys(m.strip() for m in REF.findall(text)))
        out.append(Claim(key, page_id, section, kind, text, _norm(context), refs))

    def flush_para() -> None:
        nonlocal last_para_leadin
        if not para:
            return
        text = " ".join(para)
        para.clear()
        last_para_leadin = text if text.rstrip().endswith(":") else ""
        sents = split_sentences(text)
        for i, s in enumerate(sents):
            if i == len(sents) - 1 and s.endswith(":"):
                continue  # a bare lead-in is context for the list that follows
            add(s, "sentence")

    skip = False
    for raw in prose_part(content).splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            flush_para()
            in_code = not in_code
            continue
        if in_code:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            flush_para()
            section = stripped.lstrip("#").strip()
            if (
                len(section.split()) >= 10
            ):  # a sentence-length heading asserts something
                add(section, "heading")
            skip = section.lower() in SKIP_SECTIONS
            leadins.clear()
            table_header = ""
            last_para_leadin = last_item = ""
            continue
        if skip:
            continue
        if not stripped:
            flush_para()
            table_header = ""
            continue
        if stripped.startswith("|"):
            flush_para()
            if re.fullmatch(r"\|[\s:|-]+\|?", stripped):
                continue
            if not table_header:
                table_header = stripped
                continue
            add(stripped, "row", table_header)
            continue
        m = re.match(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            flush_para()
            indent, item = len(m.group(1)), m.group(2).strip()
            while leadins and leadins[-1][0] >= indent:
                leadins.pop()
            if item.endswith(":"):
                leadins.append((indent, item))
                continue
            last_item = item
            ctx = " ".join([last_para_leadin] + [t for _, t in leadins]).strip()
            for s in split_sentences(item) if len(item.split()) > 25 else [item]:
                add(s, "item", ctx)
            continue
        if not para and last_item and line.startswith((" ", "\t")):
            # continuation of a list item (e.g. a definition under a bold term)
            for s in split_sentences(stripped):
                add(s, "item", last_item)
            continue
        leadins.clear()
        last_item = ""
        para.append(stripped.lstrip("> ").strip())
    flush_para()
    return out


# ------------------------------------------------------------------- oracles


def load_oracle_module(name: str):
    """Import a kg_validate oracle by path (never ``repowise.*``)."""
    root = Path(os.environ.get("REPOWISE_ROOT") or Path(__file__).resolve().parents[2])
    oracles = Path(
        os.environ.get("KG_ORACLES_DIR") or root / "scripts/kg_validate/oracles"
    )
    path = oracles / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kg_oracle_{name}", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(
            f"oracle not found: {path} (set REPOWISE_ROOT or KG_ORACLES_DIR)"
        )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class RepoFacts:
    """Everything the mechanical pass and the evidence builder know about a checkout."""

    def __init__(self, checkout: Path, cache_dir: Path | None = None, edges=None):
        self.root = checkout.resolve()
        self.cache_dir = cache_dir
        self._imports_mod = None
        self.tracked = self._tracked()
        self.tracked_set = set(self.tracked)
        self.dirs = {
            "/".join(p.split("/")[:i])
            for p in self.tracked
            for i in range(1, p.count("/") + 1)
        }
        self.dir_names = {PurePosixPath(d).name for d in self.dirs}
        self._text: dict[str, str | None] = {}
        self._idents: set[str] | None = None
        self._sorted_idents: list[str] | None = None
        self._lower: dict[str, str] = {}
        self.ref_cache: dict[str, str] = {}
        self._word_files: dict[str, Counter] | None = None
        self._git: tuple | None = None
        self._edges = edges  # set of (src, dst) or None = lazy

    # --- files
    def _tracked(self) -> list[str]:
        mod = self.imports_mod
        return mod.tracked_files(self.root)

    @property
    def imports_mod(self):
        if self._imports_mod is None:
            self._imports_mod = load_oracle_module("imports")
        return self._imports_mod

    def text(self, rel: str) -> str | None:
        if rel not in self._text:
            fp = self.root / rel
            try:
                self._text[rel] = (
                    None
                    if fp.stat().st_size > MAX_FILE_BYTES
                    else fp.read_text(encoding="utf-8", errors="ignore")
                )
            except OSError:
                self._text[rel] = None
        return self._text[rel]

    def resolve_path(self, ref: str) -> tuple[str, str] | None:
        """(kind, repo path) for a backticked path.

        Exact file or directory, a glob (kind ``glob``), a module path missing
        its extension, else a unique-ish suffix (``src/index.ts``).
        """
        base = ref.strip().rstrip("/").removeprefix("./").split("::")[0]
        base = re.sub(r":\d+(-\d+)?$", "", base)
        if base in self.tracked_set:
            return "file", base
        if base in self.dirs:
            return "dir", base
        if "*" in base or "?" in base:
            for pat in (base, "*/" + base):  # page-relative globs (`models/*`) too
                if any(
                    fnmatch.fnmatchcase(p, pat) for p in chain(self.tracked, self.dirs)
                ):
                    return "glob", pat
            return None
        for ext in MODULE_EXTS:
            if base + ext in self.tracked_set:
                return "file", base + ext
        hits = [t for t in self.tracked if t.endswith("/" + base)]
        dhits = [d for d in self.dirs if d.endswith("/" + base)]
        if hits or dhits:
            return ("file", hits[0]) if hits else ("dir", dhits[0])
        return None

    def files_under(self, path: str) -> list[str]:
        if path in self.tracked_set:
            return [path]
        if "*" in path or "?" in path:
            return [
                t
                for t in self.tracked
                if fnmatch.fnmatchcase(t, path)
                or any(
                    fnmatch.fnmatchcase(t, path.rstrip("/") + "/*" * k)
                    for k in range(1, 6)
                )
            ]
        return [t for t in self.tracked if t.startswith(path.rstrip("/") + "/")]

    # --- identifiers (refcheck's token set) and per-word file counts
    def _index(self) -> None:
        idents: set[str] = set()
        words: dict[str, Counter] = defaultdict(Counter)
        for rel in self.tracked:
            s = self.text(rel)
            if s is None:
                continue
            idents.update(IDENT_TOKEN.findall(s))
            counts = Counter(WORD.findall(s))
            idents.update(counts)
            if PurePosixPath(rel).suffix in SOURCE_EXTS or rel.endswith(
                (".json", ".toml")
            ):
                for w, n in counts.items():
                    words[w][rel] = n
        # file stems and directory names are legitimate identifiers in prose
        idents.update(
            PurePosixPath(p).name.split(".")[0] for p in chain(self.tracked, self.dirs)
        )
        idents.update(PurePosixPath(p).name for p in self.dirs)
        self._idents, self._word_files = idents, words

    @property
    def idents(self) -> set[str]:
        if self._idents is None:
            self._index()
        return self._idents

    def has_prefix(self, prefix: str) -> bool:
        if self._sorted_idents is None:
            self._sorted_idents = sorted(self.idents)
        i = bisect.bisect_left(self._sorted_idents, prefix)
        return i < len(self._sorted_idents) and self._sorted_idents[i].startswith(
            prefix
        )

    def lower(self, rel: str) -> str:
        if rel not in self._lower:
            self._lower[rel] = (self.text(rel) or "").lower()
        return self._lower[rel]

    def mentions(self, word: str) -> Counter:
        if self._word_files is None:
            self._index()
        return self._word_files.get(word, Counter())

    def definitions(self, name: str, limit: int = 3) -> list[tuple[str, int]]:
        pat = re.compile(
            rf"^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|static\s+|abstract\s+)*"
            rf"(?:{DEF_KINDS}\s+{re.escape(name)}\b|{re.escape(name)}\s*[:=]\s)",
            re.MULTILINE,
        )
        out = []
        for rel, _ in self.mentions(name).most_common(40):
            s = self.text(rel) or ""
            m = pat.search(s)
            if m:
                out.append((rel, s.count("\n", 0, m.start()) + 1))
                if len(out) >= limit:
                    break
        return out

    # --- git
    def _git_index(self) -> tuple:
        if self._git is None:
            # --raw carries blob ids, so exact renames (a delete and an add of the
            # same blob in one commit) are followed without fetching any blob:
            # partial clones work, and similarity renames are ignored.
            out = subprocess.run(
                ["git", "-C", str(self.root), "log", "--no-renames", "--raw", "--no-abbrev",
                 "--format=%x00%at%x09%aN%x09%s", "HEAD"],
                capture_output=True, text=True, errors="replace", check=False,
            ).stdout  # fmt: skip
            commits, by_file, renamed_from = [], defaultdict(list), defaultdict(set)
            for block in out.split("\0")[1:]:
                head, _, files = block.partition("\n")
                at, author, subject = (head.split("\t", 2) + ["", ""])[:3]
                idx = len(commits)
                commits.append(
                    (int(at or 0), author, bool(FIX_SUBJECT.search(subject)))
                )
                added, deleted = {}, {}
                for row in files.split("\n"):
                    meta, _, path = row.partition("\t")
                    cols = meta.split()
                    if len(cols) < 5 or not path:
                        continue
                    by_file[path].append(idx)
                    if cols[4] == "A":
                        added[cols[3]] = path
                    elif cols[4] == "D":
                        deleted[cols[2]] = path
                for blob, new in added.items():
                    if blob in deleted:
                        renamed_from[new].add(deleted[blob])
            head_time = commits[0][0] if commits else 0
            self._git = (commits, by_file, head_time, renamed_from)
        return self._git

    def commits_for(self, path: str) -> list[int]:
        """Commit indices touching a file (following renames) or anything under a directory."""
        _, by_file, _, renamed_from = self._git_index()
        if path in by_file or path in self.tracked_set:
            names, todo = {path}, [path]
            while todo:
                for old in renamed_from.get(todo.pop(), ()):
                    if old not in names:
                        names.add(old)
                        todo.append(old)
            return sorted({i for n in names for i in by_file.get(n, [])})
        prefix = path.rstrip("/") + "/"
        return sorted(
            {i for f, ids in by_file.items() if f.startswith(prefix) for i in ids}
        )

    def git_summary(self, path: str) -> str:
        commits, _, head, _ = self._git_index()
        ids = self.commits_for(path)
        if not ids:
            return f"{path}: 0 commits in history"
        recent = sum(commits[i][0] >= head - 90 * 86400 for i in ids)
        fixes = sum(commits[i][2] for i in ids)
        authors = Counter(commits[i][1] for i in ids).most_common(3)
        who = ", ".join(f"{a} {n}" for a, n in authors)
        return (
            f"{path}: {len(ids)} commits total, {fixes} with 'fix' in the subject, "
            f"{recent} in the 90 days before HEAD; top authors: {who}"
        )

    def scope_git(self, files: list[str], top: int = 5) -> str:
        commits, _, head, _ = self._git_index()
        if not files:
            return ""
        cut = head - 90 * 86400
        per = {f: self.commits_for(f) for f in files}
        recent = Counter(
            {f: sum(commits[i][0] >= cut for i in ids) for f, ids in per.items()}
        )
        fixes = Counter({f: sum(commits[i][2] for i in ids) for f, ids in per.items()})
        total = Counter({f: len(ids) for f, ids in per.items()})
        ids = {i for f_ids in per.values() for i in f_ids}
        authors = Counter(commits[i][1] for i in ids)
        n = max(1, len(ids))

        def fmt(c):
            return ", ".join(f"{f} ({k})" for f, k in c.most_common(top) if k)

        return (
            f"Scope git facts ({len(files)} files, {len(ids)} commits touching them):\n"
            f"- most commits in the last 90 days: {fmt(recent) or 'none'}\n"
            f"- most 'fix' commits (all time): {fmt(fixes) or 'none'}\n"
            f"- most commits (all time): {fmt(total) or 'none'}\n"
            f"- top authors by commits: "
            + ", ".join(f"{a} {k} ({k * 100 // n}%)" for a, k in authors.most_common(5))
        )

    # --- import oracle
    @property
    def edges(self) -> set[tuple[str, str]]:
        if self._edges is None:
            cache = self.cache_dir / "import_edges.json" if self.cache_dir else None
            if cache and cache.exists():
                self._edges = {tuple(e) for e in json.loads(cache.read_text())["edges"]}
            else:
                res = self.imports_mod.oracle_edges(self.root)
                self._edges = set(res["edges"])
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps({"edges": sorted(self._edges)}))
        return self._edges

    def imports_of(self, files: set[str]) -> set[str]:
        return {d for s, d in self.edges if s in files}

    def importers_of(self, files: set[str]) -> set[str]:
        return {s for s, d in self.edges if d in files}

    def oracle_covers(self, rel: str) -> bool:
        return self.imports_mod.lang_of(rel) is not None


# ------------------------------------------------------------ mechanical pass


def check_ref(facts: RepoFacts, ref: str) -> str:
    """refcheck.py's verdict for one backticked reference.

    ``ok_path`` / ``ok_pkg`` / ``ok_ident`` / ``bad_path`` / ``bad_ident`` /
    ``skip`` (prose in backticks, commands, elided paths, bare file types).
    """
    if ref not in facts.ref_cache:
        facts.ref_cache[ref] = _check_ref(facts, ref)
    return facts.ref_cache[ref]


def _check_ref(facts: RepoFacts, ref: str) -> str:
    r = ref.strip()
    if re.search(r"\s", r) and not r.startswith("@"):
        return "skip"
    r2 = r.rstrip("/").removeprefix("./")
    head = r2.split("::")[0]
    if "..." in head or "…" in head:
        return "skip"
    if "/" in head or PATHY_EXT.search(head):
        if facts.resolve_path(head):
            return "ok_path"
        if "/" not in head and len(head.split(".")[0]) <= 1:
            return "skip"  # a file type (`.d.ts`, `d.ts`), not a file
        segs = [p for p in head.split("/") if p]
        if not PATHY_EXT.search(head) and all(
            all(w in facts.idents for w in p.split(".") if w) for p in segs
        ):
            return "ok_ident"  # `session.execute/executeMeta`: identifiers, not a path
        # a package specifier (`@scope/*`, `vitest/ui`): it must occur in the repo
        if segs[0] not in facts.dir_names and facts.has_prefix(head.split("*")[0]):
            return "ok_pkg"
        return "bad_path"
    name = re.sub(r"\(.*\)$", "", r).lstrip("-")
    if not re.fullmatch(r"[A-Za-z_$@][\w$@.:<>-]*", name) or re.fullmatch(
        r"[\d.]+", name
    ):
        return "skip"
    name = re.sub(r"<.*>$", "", name)
    parts = [p for p in re.split(r"::|\.|:", name) if p]
    if name in facts.idents or all(p in facts.idents for p in parts):
        return "ok_ident"
    return "bad_ident"


def _path_refs(facts: RepoFacts, claim: Claim) -> list[tuple[str, str, str]]:
    """(ref, kind, repo path) for every backticked ref that resolves to a path."""
    out = []
    for ref in claim.refs:
        if check_ref(facts, ref) == "ok_path":
            hit = facts.resolve_path(ref.rstrip("/").removeprefix("./"))
            if hit:
                out.append((ref, *hit))
    return out


def _bare_refs(claim: Claim) -> bool:
    """A list item that is only references: its relation lives in the lead-in."""
    return len(REF.sub("", claim.text).split()) < 4


def check_imports(facts: RepoFacts, claim: Claim, scope: list[str]) -> list[str]:
    """Import relations the oracle has no edge for, in either direction.

    "A imports / depends on / is used by B": the subject is the first path cited
    before the verb, else the page scope; every other cited source file must
    share an import edge with it. Direction is not graded (prose flips it too
    easily to parse reliably); a relation with no edge at all is fabricated.
    Table rows, co-change sentences and files the oracle doesn't parse are
    left to the LLM.
    """
    text = claim.text
    rel_text = (claim.context + " " + text) if _bare_refs(claim) else text
    verb = IMPORT_VERB.search(rel_text)
    if claim.kind == "row" or not verb or NOT_IMPORT.search(rel_text):
        return []
    paths = _path_refs(facts, claim)
    if not paths:
        return []
    pos = {ref: text.find(f"`{ref}`") for ref, _, _ in paths}
    vstart = verb.start() - (len(rel_text) - len(text))
    before = [p for p in paths if 0 <= pos[p[0]] < vstart]
    after = [p for p in paths if p not in before]
    if (
        after
        and len(before) == 1
        and vstart - pos[before[0][0]] - len(before[0][0]) < 40
    ):
        subject, who, others = (
            set(facts.files_under(before[0][2])),
            f"`{before[0][0]}`",
            after,
        )
    elif after and before:
        return []  # several subjects, several objects: leave it to the LLM
    else:  # "it depends on X" / "X and Y import this module": relative to the page scope
        subject, who, others = set(scope), "the page scope", after or before
    if not subject:
        return []
    linked = facts.imports_of(subject) | facts.importers_of(subject)
    bad = []
    for ref, _, path in others:
        files = [f for f in facts.files_under(path) if facts.oracle_covers(f)]
        if files and not linked.intersection(files) and not subject.intersection(files):
            bad.append(f"import oracle: no import edge between {who} and `{ref}`")
    return bad


def check_counts(facts: RepoFacts, claim: Claim) -> list[str]:
    """A commit count attached to one path can't exceed that path's whole history.

    Attached means "N commits in `x`" or "`x` (N commits": a module-wide total
    followed by "most in `x`" is about the module, not the file.
    """
    bad = []
    for ref, _, path in _path_refs(facts, claim):
        cite = claim.text.find(f"`{ref}`")
        for m in COMMITS.finditer(claim.text):
            between = (
                claim.text[m.end() : cite]
                if cite >= m.end()
                else claim.text[cite + len(ref) + 2 : m.start()]
            )
            if len(between) > 40 or re.search(
                r"\b(most|majority|mostly|fraction|share|largest|across)\b",
                between,
                re.IGNORECASE,
            ):
                continue
            n, total = int(m.group(1)), len(facts.commits_for(path))
            if total and n > total:
                bad.append(
                    f"count: {m.group(0)} claimed for `{ref}`, which has {total} commits in total"
                )
    return bad


def mechanical(facts: RepoFacts, claim: Claim, scope: list[str]) -> None:
    """Fill claim.label='wrong' (source mechanical) when an oracle contradicts it."""
    problems = []
    for ref in claim.refs:
        verdict = check_ref(facts, ref)
        if verdict == "bad_path":
            problems.append(f"path: `{ref}` does not exist")
        elif verdict == "bad_ident":
            problems.append(f"symbol: `{ref}` occurs nowhere in the repo")
    problems += check_imports(facts, claim, scope)
    problems += check_counts(facts, claim)
    claim.checks = problems
    if problems:
        claim.label, claim.source, claim.reason = (
            "wrong",
            "mechanical",
            "; ".join(problems),
        )


# ------------------------------------------------------------------- evidence


def _window(text: str, line: int, before: int = 15, after: int = 35) -> tuple[int, str]:
    lines = text.splitlines()
    lo = max(0, line - 1 - before)
    return lo + 1, "\n".join(lines[lo : line - 1 + after])


def _excerpt(path: str, text: str, focus: list[str], cap: int = 3500) -> str:
    lines = text.splitlines()
    hits = []
    for w in focus:
        for i, ln in enumerate(lines):
            if re.search(rf"\b{re.escape(w)}\b", ln):
                hits.append(i + 1)
                break
    if not hits:
        body = "\n".join(lines[:70])
        return (
            f"--- {path} (lines 1-{min(70, len(lines))} of {len(lines)})\n{body[:cap]}"
        )
    chunks = []
    for h in sorted(set(hits))[:3]:
        start, body = _window(text, h, 8, 25)
        chunks.append(f"--- {path}:{start} (of {len(lines)} lines)\n{body}")
    return "\n".join(chunks)[:cap]


def claim_evidence(
    facts: RepoFacts, claim: Claim, scope: list[str]
) -> list[tuple[str, str]]:
    """(dedupe key, text) evidence blocks for one claim."""
    blocks: list[tuple[str, str]] = []
    idents = [
        re.sub(r"\(.*\)$", "", r).split(".")[-1]
        for r in claim.refs
        if check_ref(facts, r) == "ok_ident"
    ]
    full = claim.context + " " + claim.text
    paths = _path_refs(facts, claim)
    for ref, kind, path in paths[:4]:
        if kind == "file":
            t = facts.text(path)
            if t is not None:
                blocks.append(
                    (f"file:{path}:{','.join(idents)}", _excerpt(path, t, idents))
                )
        else:
            files = facts.files_under(path)
            listing = "\n".join(files[:40]) + (
                f"\n... ({len(files)} files)" if len(files) > 40 else ""
            )
            blocks.append(
                (
                    f"dir:{path}",
                    f"--- directory {path} ({len(files)} tracked files)\n{listing}",
                )
            )
    for name in dict.fromkeys(idents):
        defs = facts.definitions(name)
        m = facts.mentions(name)
        total = sum(m.values())
        where = ", ".join(f"{f} ({n})" for f, n in m.most_common(8))
        blocks.append(
            (f"uses:{name}", f"--- `{name}`: {total} occurrences in {len(m)} files: {where}"
             + (f"; definitions: {', '.join(f'{f}:{ln}' for f, ln in defs)}" if defs else ""))
        )  # fmt: skip
        for f, ln in defs[:1]:
            if not any(f == p for _, _, p in paths):
                start, body = _window(facts.text(f) or "", ln, 3, 30)
                blocks.append(
                    (f"def:{name}", f"--- {f}:{start} (definition of `{name}`)\n{body}")
                )
    if GIT_WORDS.search(full):
        for _, _, path in paths[:3]:
            blocks.append((f"git:{path}", f"--- git: {facts.git_summary(path)}"))
    if IMPORT_VERB.search(full):
        for _, _, path in paths[:3]:
            files = set(facts.files_under(path))
            imps = sorted(facts.imports_of(files) - files)[:12]
            imby = sorted(facts.importers_of(files) - files)[:12]
            summary = (
                f"--- import oracle for {path}: imports {imps or 'nothing in-repo'}; "
                f"imported by {imby or 'nothing in-repo'}"
            )
            blocks.append((f"imp:{path}", summary))
    if not paths and not idents and scope:
        words = {w for w in WORD.findall(claim.text) if len(w) > 4}
        scored = []
        for f in scope:
            t = facts.lower(f)
            s = sum(1 for w in words if w.lower() in t)
            if s:
                scored.append((s, f))
        for _, f in sorted(scored, reverse=True)[:2]:
            focus = [
                w
                for w in words
                if re.search(rf"\b{re.escape(w)}\b", facts.text(f) or "")
            ]
            blocks.append(
                (
                    f"file:{f}:{','.join(focus[:3])}",
                    _excerpt(f, facts.text(f) or "", focus[:3], 2500),
                )
            )
    return blocks


def page_header(facts: RepoFacts, page: dict, scope: list[str]) -> str:
    lines = [
        f"Page: {page.get('title', '')} ({page['page_id']}), target `{page.get('target_path', '')}`"
    ]
    if scope:
        sizes = [
            f"{f} ({(facts.text(f) or '').count(chr(10))} lines)" for f in scope[:60]
        ]
        more = f"\n... and {len(scope) - 60} more" if len(scope) > 60 else ""
        lines.append("Files this page covers:\n" + "\n".join(sizes) + more)
        lines.append(facts.scope_git(scope))
        sset = set(scope)
        out_edges = Counter(d for s, d in facts.edges if s in sset and d not in sset)
        in_edges = Counter(s for s, d in facts.edges if d in sset and s not in sset)
        lines.append(
            "Import oracle: scope imports "
            + (
                ", ".join(f for f, _ in out_edges.most_common(15))
                or "nothing outside itself"
            )
            + "\nImported by (outside scope): "
            + (", ".join(f for f, _ in in_edges.most_common(15)) or "nothing")
        )
    return "\n".join(lines)


def page_scope(facts: RepoFacts, page: dict) -> list[str]:
    """The page's own files plus everything under its target directory."""
    files = (page.get("metadata") or {}).get("file_paths") or []
    target = page.get("target_path") or ""
    if page.get("page_type") == "module_page" and target in facts.dirs:
        files = [*files, *facts.files_under(target)]
    return [f for f in dict.fromkeys(files) if f in facts.tracked_set]


# ------------------------------------------------------------------------ LLM

PROMPT = """You are fact-checking claims from an auto-generated wiki page about a code repository.
Judge each claim against the EVIDENCE below (real source excerpts, git history facts and a
ground-truth import graph from the repository at the documented commit). Do not trust the page.

Labels:
- supported: the evidence confirms the claim, or it is a faithful plain description of what the cited code does.
- wrong: the evidence contradicts it (wrong file/role/count/owner/edge, "unused" when it is used, a thing that does not exist).
- misleading: partly true but mischaracterises role, scope or emphasis, overstates, or presents something stale/secondary as the main point.
- speculative: an inference about intent, design rationale or behaviour that the evidence neither confirms nor contradicts (typical wording: "likely", "suggests", "reflects", "implies").
- not_a_claim: no factual content (navigation, filler, a question).

Rules: a claim with several parts is wrong if any factual part is contradicted. "This package /
subsystem / it" means the page target and the files it covers (PAGE CONTEXT): describing the target as
something narrower or different from what those files are is wrong. Missing evidence alone makes a
claim speculative, not wrong. Occurrence counts
tell you whether a symbol is used: more than one occurrence outside its definition means it is used.
"Most active"/"most fixes"/owner shares must match the scope git facts. Import/dependency claims must
match the import oracle. Keep reasons under 25 words and cite the evidence (file:line) when you can.

Return JSON: {{"verdicts": [{{"id": "c1", "label": "...", "reason": "..."}}, ...]}} with one entry per claim.

=== PAGE CONTEXT
{header}

=== EVIDENCE
{evidence}

=== CLAIMS
{claims}
"""


@dataclass
class LLMUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_calls: int = 0

    def usd(self, model: str) -> float | None:
        price = PRICES.get(model)
        if price is None:
            return None
        return (self.input_tokens * price[0] + self.output_tokens * price[1]) / 1e6


def gemini_generate(
    prompt: str, model: str, api_key: str, retries: int = 5
) -> tuple[str, dict]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
    ).encode()
    for attempt in range(retries):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts), data.get(
                "usageMetadata", {}
            )
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * 2**attempt)
                continue
            raise RuntimeError(f"gemini {e.code}: {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError, KeyError):
            if attempt < retries - 1:
                time.sleep(5 * 2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")


class Verifier:
    def __init__(
        self,
        model: str,
        cache_dir: Path | None,
        max_usd: float,
        api_key: str | None = None,
    ):
        self.model = model
        self.cache_dir = cache_dir
        self.max_usd = max_usd
        self.usage = LLMUsage()
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )

    def _call(self, prompt: str) -> str:
        key = hashlib.sha256(f"{self.model}\0{prompt}".encode()).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            hit = json.loads(path.read_text())
            self.usage.cached_calls += 1
            self._count(hit["usage"])
            return hit["text"]
        spent = self.usage.usd(self.model) or 0.0
        if spent >= self.max_usd:
            raise RuntimeError(
                f"LLM budget exhausted (${spent:.2f} >= ${self.max_usd})"
            )
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        text, usage = gemini_generate(prompt, self.model, self.api_key)
        self._count(usage)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"text": text, "usage": usage}))
        return text

    def _count(self, usage: dict) -> None:
        self.usage.calls += 1
        self.usage.input_tokens += usage.get("promptTokenCount", 0)
        self.usage.output_tokens += usage.get("candidatesTokenCount", 0) + usage.get(
            "thoughtsTokenCount", 0
        )

    def verify(
        self, header: str, batch: list[tuple[Claim, list[tuple[str, str]]]]
    ) -> None:
        evidence: dict[str, str] = {}
        for _, blocks in batch:
            for k, t in blocks:
                evidence.setdefault(k, t)
        claims_txt = "\n".join(
            f"c{i + 1}: " + (f"[context: {c.context}] " if c.context else "") + c.text
            for i, (c, _) in enumerate(batch)
        )
        prompt = PROMPT.format(
            header=header,
            evidence="\n\n".join(evidence.values()) or "(none)",
            claims=claims_txt,
        )
        raw = self._call(prompt)
        try:
            verdicts = {v["id"]: v for v in json.loads(raw).get("verdicts", [])}
        except (json.JSONDecodeError, AttributeError):
            verdicts = {}
        for i, (c, _) in enumerate(batch):
            v = verdicts.get(f"c{i + 1}", {})
            label = str(v.get("label", "")).strip().lower()
            if label in LABELS or label == NOT_A_CLAIM:
                c.label, c.source, c.reason = (
                    label,
                    "llm",
                    str(v.get("reason", ""))[:300],
                )


def batches(items: list, max_claims: int, max_chars: int):
    cur, size = [], 0
    for claim, blocks in items:
        add = sum(len(t) for _, t in blocks)
        if cur and (len(cur) >= max_claims or size + add > max_chars):
            yield cur
            cur, size = [], 0
        cur.append((claim, blocks))
        size += add
    if cur:
        yield cur


# ------------------------------------------------------------------ reporting


def rates(claims: list[Claim]) -> dict:
    judged = [c for c in claims if c.label in LABELS]
    n = len(judged)
    counts = Counter(c.label for c in judged)
    return {
        "claims": len(claims),
        "judged": n,
        "unlabelled": sum(c.label is None for c in claims),
        "not_a_claim": sum(c.label == NOT_A_CLAIM for c in claims),
        "counts": {k: counts.get(k, 0) for k in LABELS},
        **{f"{k}_rate": round(counts.get(k, 0) / n, 4) if n else None for k in LABELS},
    }


def score_rows(repo: str, claims: list[Claim], baseline: list[dict] | None):
    """score.py findings/labels/truth rows for families wiki and wiki.not_wrong."""
    findings, labels, truth = [], [], []
    for c in claims:
        if c.label not in LABELS:
            continue
        for fam, ok in (
            ("wiki", c.label == "supported"),
            ("wiki.not_wrong", c.label != "wrong"),
        ):
            findings.append(
                {"repo": repo, "family": fam, "key": c.key, "tier": c.source}
            )
            labels.append(
                {"repo": repo, "family": fam, "finding_key": c.key, "label": ok}
            )
    for row in baseline or []:
        if row.get("label") == "supported":
            truth.append({"repo": repo, "family": "wiki", "key": row["key"]})
    return findings, labels, truth


def review_sample(claims: list[Claim], n: int, seed: int = 0) -> list[Claim]:
    """Stratified by label (proportional, at least 5 of each present label when possible)."""
    rng = random.Random(seed)
    pool = defaultdict(list)
    for c in claims:
        if c.label in LABELS:
            pool[c.label].append(c)
    total = sum(len(v) for v in pool.values())
    if total <= n:
        return [c for v in pool.values() for c in v]
    quota = {k: max(min(5, len(v)), round(n * len(v) / total)) for k, v in pool.items()}
    while sum(quota.values()) > n:
        quota[max(quota, key=quota.get)] -= 1
    while sum(quota.values()) < n:
        k = max(
            (k for k in pool if quota[k] < len(pool[k])),
            key=lambda k: len(pool[k]) - quota[k],
        )
        quota[k] += 1
    out = []
    for k in sorted(pool):
        out += rng.sample(pool[k], quota[k])
    return out


def write_review(path: Path, sample: list[Claim]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "page_id", "claim", "context", "refs", "auto_label", "auto_source",
                    "auto_reason", "human_label", "human_note"])  # fmt: skip
        for c in sample:
            w.writerow([c.key, c.page_id, c.text, c.context, " ".join(c.refs), c.label,
                        c.source, c.reason, "", ""])  # fmt: skip


def match_gold(claims: list[Claim], anchors: list[str]) -> Claim | None:
    """The shortest claim on the page containing every anchor (case-insensitive)."""
    lows = [a.lower() for a in anchors]
    hits = [
        c for c in claims if all(a in (c.context + " " + c.text).lower() for a in lows)
    ]
    hits.sort(key=lambda c: (not all(a in c.text.lower() for a in lows), len(c.text)))
    return hits[0] if hits else None


def agreement(claims: list[Claim], gold: dict) -> dict:
    """Compare against an audit: per-group supported rate, and per enumerated claim."""
    by_page = defaultdict(list)
    for c in claims:
        by_page[c.page_id].append(c)
    groups = []
    for g in gold.get("groups", []):
        ours = rates([c for p in g["pages"] for c in by_page.get(p, [])])
        audit_n = sum(g["counts"].values())
        groups.append(
            {
                "name": g["name"],
                "pages": len(g["pages"]),
                "audit_claims": audit_n,
                "audit_supported": round(g["counts"]["correct"] / audit_n, 4),
                "ours_judged": ours["judged"],
                "ours_supported": ours["supported_rate"],
            }
        )
    tot = Counter()
    for g in gold.get("groups", []):
        tot.update(g["counts"])
    audit_total = sum(tot.values())
    pages = {p for g in gold.get("groups", []) for p in g["pages"]}
    ours_all = rates([c for c in claims if c.page_id in pages])
    items, unmatched = [], []
    for g in gold.get("claims", []):
        c = match_gold(by_page.get(g["page_id"], []), g["anchors"])
        if c is None:
            unmatched.append(g)
            continue
        items.append(
            {"page_id": g["page_id"], "audit": g["label"], "ours": c.label, "source": c.source,
             "key": c.key, "claim": c.text[:200], "note": g.get("note", "")}
        )  # fmt: skip
    judged = [i for i in items if i["ours"] in LABELS]
    agree = sum((i["audit"] == "correct") == (i["ours"] == "supported") for i in judged)
    exact = sum(i["ours"] == i["audit"] for i in judged)
    by_label = {}
    for lab in ("correct", *LABELS):
        rows = [i for i in judged if i["audit"] == lab]
        if rows:
            want = "supported" if lab == "correct" else None
            ok = sum(
                (i["ours"] == want) if want else (i["ours"] != "supported")
                for i in rows
            )
            by_label[lab] = {
                "n": len(rows),
                "agree": ok,
                "rate": round(ok / len(rows), 4),
            }
    diffs = [
        abs(g["ours_supported"] - g["audit_supported"])
        for g in groups
        if g["ours_supported"] is not None
    ]
    return {
        "audit_supported_rate": round(tot["correct"] / audit_total, 4)
        if audit_total
        else None,
        "ours_supported_rate": ours_all["supported_rate"],
        "audit_counts": dict(tot),
        "ours_counts": ours_all["counts"],
        "group_mae_pp": round(100 * sum(diffs) / len(diffs), 1) if diffs else None,
        "groups": groups,
        "claim_level": {
            "gold_claims": len(gold.get("claims", [])),
            "matched": len(items),
            "judged": len(judged),
            # supported vs not-supported, on the claims the audit enumerated
            "binary_agreement": round(agree / len(judged), 4) if judged else None,
            "exact_label_agreement": round(exact / len(judged), 4) if judged else None,
            "by_audit_label": by_label,
        },
        "unmatched": [
            {"page_id": g["page_id"], "anchors": g["anchors"]} for g in unmatched
        ],
        "items": items,
    }


# ----------------------------------------------------------------------- main


def load_pages(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["pages"] if isinstance(data, dict) else data


def run(args) -> dict:
    pages = load_pages(args.docs)
    gold = json.loads(args.gold.read_text()) if args.gold else None
    wanted = None
    if args.pages:
        wanted = args.pages.split(",")
    elif gold:
        wanted = [p for g in gold["groups"] for p in g["pages"]]
    if wanted:
        by_id = {p["page_id"]: p for p in pages}
        missing = [p for p in wanted if p not in by_id]
        if missing:
            raise SystemExit(f"pages not in {args.docs}: {missing}")
        pages = [by_id[p] for p in wanted]
    else:
        pages = [p for p in pages if p.get("has_prose", True)]

    cache = args.cache
    facts = RepoFacts(args.checkout, cache / args.repo if cache else None)
    verifier = (
        None
        if args.no_llm
        else Verifier(args.model, cache / "llm" if cache else None, args.max_usd)
    )
    all_claims: list[Claim] = []
    for page in pages:
        claims = extract_claims(page["page_id"], page["content"])
        scope = page_scope(facts, page)
        for c in claims:
            mechanical(facts, c, scope)
        todo = [(c, claim_evidence(facts, c, scope)) for c in claims if c.label is None]
        if verifier and todo:
            header = page_header(facts, page, scope)
            for batch in batches(todo, args.batch_claims, args.batch_chars):
                try:
                    verifier.verify(header, batch)
                except RuntimeError as e:
                    print(f"[wiki_claims] {page['page_id']}: {e}", file=sys.stderr)
                    if "budget" in str(e):
                        verifier = None
                        break
        all_claims += claims
        r = rates(claims)
        print(
            f"{page['page_id']}: {r['judged']} judged, supported {r['supported_rate']}",
            file=sys.stderr,
        )

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    with (out / "claims.jsonl").open("w") as fh:
        for c in all_claims:
            fh.write(json.dumps(asdict(c)) + "\n")
    baseline = None
    if args.baseline:
        baseline = [
            json.loads(line)
            for line in args.baseline.read_text().splitlines()
            if line.strip()
        ]
    findings, labels, truth = score_rows(args.repo, all_claims, baseline)
    for name, rows in (("findings", findings), ("labels", labels), ("truth", truth)):
        with (out / f"{name}.jsonl").open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    write_review(out / "review.csv", review_sample(all_claims, args.review_n))
    summary = {
        "repo": args.repo,
        "pages": len(pages),
        "model": None if args.no_llm else args.model,
        **rates(all_claims),
        "by_source": {
            s: rates([c for c in all_claims if c.source == s])
            for s in ("mechanical", "llm")
        },
        "baseline_supported_kept": (
            round(
                len({t["key"] for t in truth} & {c.key for c in all_claims})
                / len(truth),
                4,
            )
            if truth
            else None
        ),
    }
    if verifier is not None or not args.no_llm:
        u = (verifier or Verifier(args.model, None, 0)).usage
        summary["llm"] = {
            **asdict(u),
            "est_usd": u.usd(args.model),
            "prices_per_mtok": PRICES.get(args.model),
        }
    if gold:
        summary["agreement"] = agreement(all_claims, gold)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument(
        "--docs", type=Path, required=True, help="pages JSON ({pages:[...]} or a list)"
    )
    ap.add_argument(
        "--checkout",
        type=Path,
        required=True,
        help="repo at the documented commit (full history)",
    )
    ap.add_argument(
        "--pages",
        help="comma-separated page ids (default: gold pages, else all prose pages)",
    )
    ap.add_argument(
        "--gold", type=Path, help="audit labels to measure agreement against"
    )
    ap.add_argument(
        "--baseline",
        type=Path,
        help="a previous claims.jsonl: its supported claims become recall truth",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--model", default=os.environ.get("WIKI_CLAIMS_MODEL", DEFAULT_MODEL)
    )
    ap.add_argument("--no-llm", action="store_true", help="mechanical pass only")
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument("--batch-claims", type=int, default=12)
    ap.add_argument("--batch-chars", type=int, default=40_000)
    ap.add_argument("--review-n", type=int, default=50)
    ap.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/repowise-bench/wiki_claims"
    )
    args = ap.parse_args(argv)
    s = run(args)
    print(
        json.dumps(
            {
                k: s[k]
                for k in ("claims", "judged", "counts", "supported_rate", "wrong_rate")
            },
            indent=1,
        )
    )
    if "llm" in s:
        print(f"LLM: {s['llm']['calls']} calls, est ${s['llm']['est_usd']}")
    if "agreement" in s:
        a = s["agreement"]
        print(
            f"agreement: audit supported {a['audit_supported_rate']} vs ours {a['ours_supported_rate']}; "
            f"group MAE {a['group_mae_pp']}pp; claim-level {a['claim_level']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
