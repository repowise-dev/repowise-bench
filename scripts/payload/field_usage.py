"""Which FIELDS of a tool payload does the agent actually act on?

MCP_PAYLOAD_AUDIT.md section 6 opens with what it could not determine:

    Which fields the agent actually reads. `sessions.jsonl` records `tool_names`
    and `mcp_tools` counts, not key-level consumption. Every classification
    above is prior art, structural measurement, or intuition — none of it is
    usage data.

and names the fix:

    log the raw MCP result alongside the assistant turn that follows it, then
    check whether the next action cites a path that appeared *only* in
    `candidates` / `retrieval` / `best_guesses`.

**The logging half already exists.** ``session_runner.py`` writes every
invocation's full ``stream-json`` to ``<scratch>/<task>_stream.jsonl``, and a
``tool_result`` block in there carries the entire serialised payload — 25,201
characters on the first one this script was pointed at. So no runner change and
no agent spend is needed: this is an analysis over data already on disk for all
17 arms of runs 1 and 2, and it can be re-run on any future arm for free.

--------------------------------------------------------------------------
WHAT IT MEASURES, AND WHAT IT CANNOT
--------------------------------------------------------------------------

For each field of each payload it computes the field's **exclusive tokens**:
file paths and symbol ids that the field contributes and that

  * no *other* field of the same payload also contributes, and
  * had not already appeared anywhere earlier in the transcript.

If one of those tokens later shows up in an action the agent takes — a Read
path, a Grep target, an Edit, a symbol id, a shell command — that field gets
the credit, because nothing else in the session could have supplied it.

**This measures navigational value only, and that is a hard ceiling.** A field
can be entirely load-bearing and score zero here: ``answer`` is prose,
``confidence`` and ``verified`` are trust axes that change whether the agent
re-reads rather than where it looks, and ``note`` changes what it does next
without naming a file. Read a zero as "contributed no novel destination", never
as "unused". The fields the method genuinely adjudicates are exactly the ones
the audit is stuck on — ``candidates``, ``retrieval``, ``best_guesses``,
``fallback_targets``, ``citations``, ``global_hotspots``, ``co_change_partners``
— which is why it is worth having.

Undercounts, all in the safe direction: a field whose paths the agent reached
by another route is not credited; a payload whose paths all also appear in
``citations`` credits neither; and attribution does not cross task boundaries,
because the runner writes one stream per task even though the arms resume a
single session — so a path served in T04 and opened in T05 scores as unused.

--------------------------------------------------------------------------

Usage:
    python field_usage.py <stream-dir-or-file> [...]  [--json out.json]

Self-test (proves the detector fires in BOTH directions — mandatory in this
workstream, five dead detectors so far, every one of them plausible):
    python field_usage.py --self-test
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

# A path-like token: at least one "/" or a file extension, so bare English
# words never enter the token set. Also matches "pkg/mod.py::Symbol".
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-/\\]*[A-Za-z0-9_\-]\.[A-Za-z]{1,5}(?:::[A-Za-z_][\w.]*)?")

#: Tool names whose payloads are worth attributing. Everything else (Read,
#: Edit, Bash, …) is an ACTION and is scanned for credit, not attributed.
_RETRIEVAL = (
    "get_answer",
    "get_context",
    "get_symbol",
    "get_why",
    "search_codebase",
    "get_risk",
    "get_health",
    "get_overview",
    "get_dead_code",
    "get_change_risk",
)


def _short_tool(name: str) -> str:
    """``mcp__repowise__get_answer`` -> ``get_answer``."""
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def _strings(value: Any) -> Iterator[str]:
    """Every string inside *value*, without going through JSON.

    Walking the structure rather than ``json.dumps``-ing it. The dump escapes
    a Windows path's backslashes to ``\\\\``, and a later ``replace("\\\\", "/")``
    then turns each PAIR into ``//`` — so ``C:\\...\\rich\\ansi.py`` came out as
    ``//Users//...//ansi.py``, matched nothing a payload ever offered, and every
    Read of an absolute path scored as "the agent did not go there". That
    deflated the entire ``used`` column in one direction, which is the
    dangerous direction.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, sub in value.items():
            yield str(key)
            yield from _strings(sub)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif value is not None:
        yield str(value)


def tokens(value: Any) -> set[str]:
    """Every path-like token anywhere inside *value*, normalised.

    A ``path.py::Symbol`` id yields BOTH itself and its file half. The two
    sides of the comparison spell the same file differently — a payload says
    ``rich/console.py`` and the agent calls
    ``get_symbol("rich/console.py::Console.render_lines")`` — so treating the
    qualified id as one atomic token loses exactly the follow-ups this script
    exists to count.
    """
    out: set[str] = set()
    for text in _strings(value):
        for match in _TOKEN_RE.finditer(text.replace("\\", "/")):
            token = match.group(0)
            out.add(token)
            if "::" in token:
                out.add(token.split("::", 1)[0])
    return out


def cites(acted: set[str], wanted: set[str]) -> bool:
    """Did any action token name one of *wanted*?

    Suffix-aware in one direction only. A payload names a repo-relative path
    (``rich/ansi.py``); an action names whatever the agent typed, which is
    routinely absolute (``C:/Users/.../se-mcp-rich/rich/ansi.py``). Set
    equality answers "no" to every one of those. The reverse is not allowed:
    a payload token that merely ends with an action token would match
    ``a.py`` against ``extra/a.py`` and manufacture credit.
    """
    for want in wanted:
        for act in acted:
            if act == want or act.endswith("/" + want):
                return True
    return False


def flatten(payload: Any, prefix: tuple = (), depth: int = 0) -> dict[tuple, Any]:
    """Payload -> ``{field path tuple: subtree}``, one entry per addressable block.

    List elements collapse onto one key (``retrieval[]``) rather than being
    indexed: the question is whether the BLOCK earns its bytes, and a per-row
    breakdown would answer a question nobody is asking while making every
    payload's key set unique and unaggregatable.

    Keys are TUPLES, not dotted strings, because ``get_context`` keys its
    targets by file path: with dotted keys, sibling targets ``rich/ansi`` and
    ``rich/ansi.py`` produce ``targets.rich/ansi`` and ``targets.rich/ansi.py``,
    and the ancestry test that keeps a parent from competing with its own child
    reads the second as a descendant of the first. The sibling then drops out
    of the competitor set and its tokens are scored exclusive when they are
    not.
    """
    out: dict[tuple, Any] = {}
    if depth > 3:
        return out
    if isinstance(payload, dict):
        for key, sub in payload.items():
            path = (*prefix, str(key))
            out[path] = sub
            out.update(flatten(sub, path, depth + 1))
    elif isinstance(payload, list):
        path = (*prefix[:-1], f"{prefix[-1]}[]") if prefix else ("[]",)
        if payload and any(isinstance(m, dict) for m in payload):
            keys: set[str] = set()
            for m in payload:
                if isinstance(m, dict):
                    keys |= set(m)
            for key in keys:
                out[(*path, key)] = [m.get(key) for m in payload if isinstance(m, dict)]
    return out


def render_field(path: tuple) -> str:
    """A field-path tuple as the dotted name the tables print."""
    return ".".join(path)


def related(a: tuple, b: tuple) -> bool:
    """Is one field path an ancestor of the other?

    A child shares every token with its parent, so the two must never be
    treated as competitors when deciding what a field uniquely offers.

    Compared element-wise with a trailing ``[]`` stripped, because a list's
    per-key entries hang off ``("retrieval[]", "path")`` while the list itself
    is ``("retrieval",)`` — a plain prefix test calls those unrelated, they
    then compete, and since they carry the same tokens NEITHER comes out
    exclusive. That silently zeroes ``novel`` for every list-valued block,
    which is most of the interesting ones.
    """
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return all(x.rstrip("[]") == y.rstrip("[]") for x, y in zip(short, long))


def iter_events(path: Path) -> Iterator[dict]:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return json.dumps(content, default=str)


#: ``repowise <sub>`` -> the tool the subcommand adapts. A CLI arm's payload
#: arrives as the stdout of a ``Bash`` call, so without this the CLI half of
#: every run is invisible: the result lands under the tool name ``Bash``, which
#: is not attributable, and is swept into ``seen`` instead. That silently made
#: 8 of 17 arms contribute zero attributable payloads while the tool reported a
#: clean table — and CLI-vs-MCP is the question this workstream is built on.
_CLI_SUBCOMMAND_TOOL = {
    "ask": "get_answer",
    "context": "get_context",
    "symbol": "get_symbol",
    "why": "get_why",
    "search": "search_codebase",
    "risk": "get_risk",
    "health": "get_health",
}


def _cli_tool_for(command: str | None) -> str:
    """The tool a ``repowise <sub>`` shell command stands in for, or ""."""
    if not command:
        return ""
    for raw in re.split(r"[|;&\n]", command):
        parts = raw.split()
        for i, part in enumerate(parts):
            if part.endswith("repowise") or part.endswith("repowise.exe"):
                if i + 1 < len(parts):
                    return _CLI_SUBCOMMAND_TOOL.get(parts[i + 1], "")
                return ""
    return ""


def _parse_payload(text: str) -> dict | None:
    """The tool dict inside a ``tool_result``, or None when it is not one.

    Claude Code wraps some results as ``{"result": {...}}``; a CLI adapter's
    payload is the command's stdout, which is the same dict under ``--full``
    (verified byte-close by ``payload_parity.py``) and a projection of it under
    ``--format json``. A CLI arm's stdout may carry a leading notice line
    before the document, so the scan starts at the first ``{``.
    """
    text = text.strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj = json.loads(text[start:])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    inner = obj.get("result")
    return inner if isinstance(inner, dict) else obj


class Tally:
    """Per-field running totals across every payload seen."""

    def __init__(self) -> None:
        self.present: dict[tuple[str, tuple], int] = defaultdict(int)
        self.chars: dict[tuple[str, tuple], int] = defaultdict(int)
        self.exclusive: dict[tuple[str, tuple], int] = defaultdict(int)
        self.credited: dict[tuple[str, tuple], int] = defaultdict(int)
        self.arms: set[str] = set()
        self.attributed_arms: set[str] = set()

    def rows(self) -> list[dict]:
        out = []
        for key in sorted(self.present, key=lambda k: -self.chars[k]):
            tool, field = key
            out.append(
                {
                    "tool": tool,
                    "field": render_field(field),
                    "payloads": self.present[key],
                    "chars": self.chars[key],
                    "chars_per_payload": round(self.chars[key] / self.present[key]),
                    # How often the field had anything novel to offer at all.
                    "with_exclusive_tokens": self.exclusive[key],
                    "credited": self.credited[key],
                }
            )
        return out


def analyse_stream(path: Path, tally: Tally) -> None:
    """Attribute one task's transcript.

    Single forward pass. ``seen`` accumulates every token that has appeared
    anywhere earlier — prompts, assistant prose, prior payloads, prior actions
    — so a field is never credited for a path the agent already had. Pending
    attributions stay open until the end of the task rather than until the next
    retrieval call: agents routinely act on a payload several turns later, and
    closing early would score that as unused.
    """
    seen: set[str] = set()
    # One OPEN ATTRIBUTION per (field, payload), never one per field. Sharing a
    # bucket across a task's payloads meant the first credit cleared every other
    # payload's still-open tokens, so ``used`` was capped at one per field per
    # task while ``payloads`` and ``novel`` counted every one. ``get_symbol``
    # showed 85 payloads in 24 streams against a ``used`` that could not exceed
    # 24 by construction.
    pending: list[tuple[tuple[str, tuple], set[str]]] = []
    # tool_use id -> short tool name, so a result can be matched to its call.
    calls: dict[str, str] = {}

    for event in iter_events(path):
        etype = event.get("type")
        # ``message`` is a plain string on some system/result events, so the
        # usual ``.get("content")`` chain raises rather than skipping.
        message = event.get("message")
        content = (message.get("content") if isinstance(message, dict) else None) or []
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            if etype == "assistant" and block.get("type") == "tool_use":
                raw_name = block.get("name") or "?"
                name = _short_tool(raw_name)
                if name == "Bash":
                    # A CLI arm's retrieval call. Record which tool it stands
                    # in for so its stdout is attributed, not discarded.
                    name = _cli_tool_for((block.get("input") or {}).get("command")) or "Bash"
                calls[str(block.get("id"))] = name
                # The call's own arguments are an ACTION: they are where the
                # agent chose to look, which is exactly what credits a field.
                acted = tokens(block.get("input"))
                for key, want in pending:
                    if want and cites(acted, want):
                        tally.credited[key] += 1
                        want.clear()
                seen |= acted

            elif etype == "assistant" and block.get("type") == "text":
                seen |= tokens(block.get("text"))

            elif etype == "user" and block.get("type") == "tool_result":
                name = calls.get(str(block.get("tool_use_id")), "")
                text = _result_text(block)
                payload = _parse_payload(text) if name in _RETRIEVAL else None
                if payload is None:
                    # Not an attributable payload (a Read's file body, a shell
                    # transcript). Still counts as something the agent has now
                    # seen, so no later field is credited for its paths.
                    seen |= tokens(text)
                    continue

                tally.attributed_arms.add(path.parent.name)
                blocks = flatten(payload)
                per_field = {f: tokens(v) for f, v in blocks.items()}
                for field, mine in per_field.items():
                    key = (name, field)
                    tally.present[key] += 1
                    tally.chars[key] += len(json.dumps(blocks[field], default=str))
                    # Exclusive: no OTHER field offers it, and it is new. A
                    # nested child shares every token with its parent, so a
                    # parent is compared against non-ancestors only. Tuple
                    # prefixes, so a target keyed by a file path cannot be
                    # mistaken for an ancestor of its sibling.
                    others: set[str] = set()
                    for other, theirs in per_field.items():
                        if other == field or related(other, field):
                            continue
                        others |= theirs
                    novel = mine - others - seen
                    if novel:
                        tally.exclusive[key] += 1
                        pending.append((key, set(novel)))
                seen |= tokens(text)


# ---------------------------------------------------------------------------
# Self-test. A detector that has not been proved in both directions is not
# evidence, and this workstream has shipped five that produced plausible
# numbers while measuring nothing.
# ---------------------------------------------------------------------------

_USED = "pkg/only_in_candidates.py"
_UNUSED = "pkg/never_touched.py"
_ABS = "C:\\Users\\bench\\tree\\pkg\\opened_by_abs_path.py"
_SYMBOL_FILE = "pkg/reached_via_symbol_id.py"


def _tool_use(cid, name, **inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": cid, "name": name, "input": inp}]}}


def _tool_result(cid, content):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid,
         "content": content if isinstance(content, str) else json.dumps(content)}]}}


def _synthetic_stream(tmp: Path) -> Path:
    """Every credit shape the real corpus contains, positive and negative.

    The first version of this fixture used one payload naming one bare
    relative POSIX path, which is the single shape the tokenizer handled. It
    passed while absolute Windows paths and ``path::Symbol`` follow-ups were
    both silently uncreditable, and while a second payload of the same tool
    could not be credited at all. Each block below exists because one of those
    shipped a wrong number.
    """
    first = {
        "answer": "It happens in the loader.",
        "citations": ["pkg/loader.py"],
        "candidates": [{"path": _USED}, {"path": _ABS.replace("\\", "/").split("/tree/")[-1]}],
        "retrieval": [{"path": _UNUSED, "excerpt": "unrelated slab"}],
    }
    # A SECOND payload from the same tool in the same task. Under one shared
    # credit bucket per field, crediting the first erased this one.
    second = {"answer": "And here.", "candidates": [{"path": _SYMBOL_FILE}]}
    events = [
        _tool_use("c1", "mcp__repowise__get_answer", question="where does loading happen"),
        _tool_result("c1", first),
        # (a) plain relative path, the shape that always worked
        _tool_use("c2", "Read", file_path=_USED),
        _tool_use("c3", "mcp__repowise__get_answer", question="and the second half"),
        _tool_result("c3", second),
        # (b) ABSOLUTE Windows path for a file the payload named relatively
        _tool_use("c4", "Read", file_path=_ABS),
        # (c) symbol id whose FILE half is what the payload offered
        _tool_use("c5", "mcp__repowise__get_symbol", symbol_id=f"{_SYMBOL_FILE}::Loader.run"),
    ]
    out = tmp / "T01_stream.jsonl"
    out.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return out


def _cli_stream(tmp: Path) -> Path:
    """A CLI arm: the payload arrives as the stdout of a ``Bash`` call."""
    payload = {"answer": "In the loader.", "candidates": [{"path": _USED}]}
    events = [
        _tool_use("b1", "Bash", command='repowise ask "where does loading happen" --full'),
        _tool_result("b1", "Resolved repo: tree\n" + json.dumps(payload)),
        _tool_use("b2", "Read", file_path=_USED),
    ]
    out = tmp / "T02_stream.jsonl"
    out.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return out


def self_test() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tally = Tally()
        analyse_stream(_synthetic_stream(Path(td)), tally)
        cli = Tally()
        analyse_stream(_cli_stream(Path(td)), cli)

    cand = ("get_answer", ("candidates",))
    retr = ("get_answer", ("retrieval",))

    checks = [
        # Direction 1: acted on -> credited. Three shapes, three payloads.
        ("relative path, absolute path and symbol-id follow-ups all credit",
         tally.credited[cand] == 2),
        ("a second payload of the same tool is credited independently",
         tally.present[cand] == 2 and tally.exclusive[cand] == 2),
        # Direction 2: not acted on -> not credited. Without this the tool
        # would just be counting fields.
        ("the ignored field is NOT credited", tally.credited[retr] == 0),
        ("the ignored field did offer an exclusive path", tally.exclusive[retr] == 1),
        ("a field with no novel token is not counted",
         tally.exclusive[("get_answer", ("answer",))] == 0),
        # A CLI arm's payload is attributed, not swept into `seen`.
        ("a Bash-delivered CLI payload is attributed", cli.present[cand] == 1),
        ("and is credited when acted on", cli.credited[cand] == 1),
        # Suffix matching must not run the other way, or `a.py` in a payload
        # would be credited by any action naming `vendor/a.py`.
        ("suffix matching is one-directional",
         cites({"pkg/a.py"}, {"a.py"}) and not cites({"a.py"}, {"pkg/a.py"})),
        # Sibling targets keyed by file path must still compete (with dotted
        # keys, `targets.rich/ansi.py` read as a child of `targets.rich/ansi`).
        ("path-keyed siblings are not mistaken for ancestors",
         not related(("targets", "rich/ansi.py"), ("targets", "rich/ansi"))),
        # ...while a list block and its per-key entries genuinely are related.
        ("a list block and its element keys are related",
         related(("candidates",), ("candidates[]", "path"))),
    ]
    ok = True
    for label, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    if not ok:
        print("\nThe detector is not trustworthy. Do not run it on real streams.")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()

    out_path = None
    if "--json" in argv:
        i = argv.index("--json")
        out_path = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2 :]

    streams: list[Path] = []
    for raw in argv:
        p = Path(raw)
        streams.extend(sorted(p.rglob("*_stream.jsonl")) if p.is_dir() else [p])
    if not streams:
        print(__doc__)
        return 2

    tally = Tally()
    for stream in streams:
        tally.arms.add(stream.parent.name)
        analyse_stream(stream, tally)

    # Coverage, printed rather than assumed. An arm whose payloads this tool
    # cannot parse contributes nothing while still being counted in "187
    # streams", which reads as coverage it does not have.
    silent = sorted(tally.arms - tally.attributed_arms)

    rows = tally.rows()
    if out_path:
        out_path.write_text(
            json.dumps(
                {
                    "streams": len(streams),
                    "arms": len(tally.arms),
                    "arms_with_payloads": len(tally.attributed_arms),
                    "arms_without_payloads": silent,
                    "rows": rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"wrote {out_path}")

    print(f"\n{len(streams)} streams; {len(tally.attributed_arms)} of {len(tally.arms)} arms "
          f"served a parseable payload")
    if silent:
        print(f"no attributable payload from: {', '.join(silent)}")
    print()
    print(f"{'tool':<16}{'field':<34}{'n':>4}{'chars/call':>11}{'novel':>7}{'used':>6}")
    for r in rows:
        if r["chars_per_payload"] < 200:
            continue
        print(
            f"{r['tool']:<16}{r['field']:<34}{r['payloads']:>4}"
            f"{r['chars_per_payload']:>11,}{r['with_exclusive_tokens']:>7}{r['credited']:>6}"
        )
    print(
        "\n`novel` = payloads where the field offered a path nothing else had;"
        "\n`used`  = of those, how often the agent then went there."
        "\nA zero means no novel DESTINATION, not unused: prose and trust"
        "\nfields are outside this method's reach. See the module docstring."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
