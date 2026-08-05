"""Prove a Codex cell runs in the environment the benchmark thinks it does.

The Claude-side probe (`env_isolation_probe.py`) can read hook firings straight
off the agent's stream, because `--include-hook-events` puts them there. **Codex
0.145.0 emits no hook events and no init event at all**: its `--json` stream is
`thread.started / turn.started / item.* / turn.completed` and nothing about the
environment. So "zero hooks fired" is not a thing a Codex cell can report about
itself, and a probe that concluded it from silence would be concluding it from
the absence of a channel.

This probe therefore measures a hook by its **side effect**, and it carries its
own positive control so that a clean result is falsifiable:

    variant           CODEX_HOME              --ignore-user-config
    ----------------------------------------------------------------
    sentinel-loud     throwaway + hooks.json  no    -> hook MUST fire
    sentinel-flag     throwaway + hooks.json  yes   -> hook MUST fire
    bench             bench home, no hooks    yes   -> hook must NOT fire

If `sentinel-loud` does not fire, the detector is broken and the `bench` row
means nothing; the probe says so and exits non-zero rather than reporting a
clean environment it cannot actually see.

`sentinel-flag` is the row that matters and it is why this file exists rather
than a flag in a command line. Measured 2026-08-03 on this machine:

    sentinel-loud   hook fired 5x
    sentinel-flag   hook fired 3x     <-- --ignore-user-config does NOT
                                          suppress $CODEX_HOME/hooks.json
    bench           hook did not fire

`hooks.json` is a separate file from `config.toml` and the flag's help text
says it ignores `config.toml`. It means exactly that. This is the same shape as
`--settings` on the Claude side, which looked like it would remove the
operator's hooks and silently merged instead, removing one injection of two.
**Do not take a flag's name for a measurement.**

Usage::

    python harness/codex_isolation_probe.py --repo ~/Desktop/bakeoff/lb-c0-bare-django-django
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.codex_runner import (  # noqa: E402
    CODEX_EXE, configured_mcp_servers, parse_codex_stream, prepare_codex_home,
    trust_toml,
)

BENCH_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = BENCH_ROOT / ".codex_probe_scratch"

# A prompt that forces the shell tool, because a PreToolUse hook on the shell
# is the shape the operator's own hooks.json actually has. A prompt the agent
# can answer without a tool call proves nothing about a PreToolUse hook.
PROMPT = (
    "Run the shell command `git rev-parse --short HEAD` and then reply with "
    "just the hash and nothing else."
)


def _write_sentinel_home(home: Path, marker: Path, repo: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    src = Path(os.environ.get("CODEX_AUTH") or Path.home() / ".codex" / "auth.json")
    if src.exists():
        shutil.copyfile(str(src), str(home / "auth.json"))
    (home / "hooks.json").write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{
                "matcher": ".*",
                "hooks": [{
                    "type": "command",
                    # Forward slashes and NO quotes, deliberately. Quoting the
                    # path (`>> "C:\...\f.txt"`) makes the redirect fail
                    # silently, and a hook that fires and fails to write is
                    # indistinguishable here from a hook that did not fire —
                    # which is the exact failure mode this probe exists to
                    # avoid, arriving inside the probe itself. The bench paths
                    # contain no spaces, which is why this is safe.
                    "command": f"cmd /c echo FIRED >> {str(marker).replace(chr(92), '/')}",
                    "timeout": 5,
                }],
            }]
        }
    }, indent=2), encoding="utf-8")
    # Identical to what the bench home carries, so the ONLY difference between
    # the sentinel variants and the bench variant is hooks.json. Without a
    # trust entry Codex declines to run hooks in the project at all, and the
    # positive control silently stops being one: it reports zero firings for a
    # reason that has nothing to do with isolation.
    (home / "config.toml").write_text(trust_toml(repo.parent), encoding="utf-8")


def _count_firings(marker: Path) -> int:
    """How many times the sentinel hook wrote to its marker.

    Read as BYTES with the NULs stripped, and this is not fussiness. On Windows
    a redirect from `cmd /c echo` writes **UTF-16LE with a BOM**, so
    `marker.read_text(encoding="utf-8", errors="replace").count("FIRED")`
    decodes `F\\x00I\\x00R\\x00E\\x00D`, finds no occurrence of "FIRED", and
    returns 0 for a hook that fired five times.

    That cost three probe runs and it is worth leaving the scar tissue here,
    because it is the exact failure this whole workstream is about: a detector
    that returns a plausible zero and looks like a clean result. The probe was
    reporting POSITIVE CONTROL FAILED while the control was firing perfectly,
    and the reading a hurried session would have taken from it is "isolation is
    fine, the sentinel is just flaky".
    """
    if not marker.exists():
        return 0
    raw = marker.read_bytes().replace(b"\x00", b"")
    return raw.upper().count(b"FIRED")


def run_variant(label: str, home: Path, repo: Path, model: str, timeout: int,
                ignore_user_config: bool, marker: Path,
                bypass_hook_trust: bool) -> dict:
    if marker.exists():
        marker.unlink()

    # Prompt on stdin, matching the runner. `codex.cmd` is a batch shim and a
    # newline inside a positional argument truncates the command line there,
    # taking every flag after it with it. This probe's prompt is one line so it
    # survived either way, which is exactly why the runner's did not and the
    # probe did not catch it.
    cmd = [
        CODEX_EXE, "exec", "-",
        "--json", "--model", model,
        "--cd", str(repo),
        "--sandbox", "read-only",
        "--ephemeral", "--skip-git-repo-check",
    ]
    if ignore_user_config:
        cmd.append("--ignore-user-config")
    if bypass_hook_trust:
        # Without this a hook in a fresh home may be skipped as untrusted,
        # which would make a clean `bench` row a statement about trust rather
        # than about isolation. Forcing trust on the SENTINEL variants is what
        # makes them a real positive control.
        cmd.append("--dangerously-bypass-hook-trust")

    # Forward slashes, matching the runner. A backslash CODEX_HOME reached
    # Codex intact but the `-c projects."C:\..."` override this used to carry
    # alongside it did not, and the two failures looked identical from here.
    env = {**os.environ, "CODEX_HOME": str(home).replace("\\", "/"),
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    p = subprocess.run(cmd, capture_output=True, text=True, input=PROMPT,
                       encoding="utf-8", errors="replace",
                       timeout=timeout, env=env)

    lines = (p.stdout or "").splitlines()
    parsed = parse_codex_stream(lines, model)
    fired = _count_firings(marker)

    return {
        "variant": label,
        "codex_home": str(home),
        "ignore_user_config": ignore_user_config,
        "returncode": p.returncode,
        "hook_fired_count": fired,
        "shell_calls": len(parsed["commands"]),
        "blocked_by_policy": (p.stderr or "").count("blocked by policy"),
        "mcp_servers_configured": [s.get("name") for s in
                                   configured_mcp_servers(str(home))],
        "mcp_tools_issued": parsed["mcp_tools_issued"],
        "answer": (parsed["answer"] or "")[:120],
        "usage_seen": bool(parsed["output_tokens"] or parsed["input_tokens"]),
        "stream_errors": parsed["stream_errors"][:3],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--model", default="gpt-5.6-sol")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    repo = Path(a.repo).expanduser().resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    marker = (SCRATCH / "sentinel_fired.txt").resolve()
    sentinel_home = SCRATCH / "codexhome_sentinel"
    _write_sentinel_home(sentinel_home, marker, repo)
    # The bench home gets the SAME config.toml, so the variants differ only in
    # hooks.json. prepare_codex_home() deletes any config.toml it finds, so
    # this is written after it and removed again by the next real run.
    bench_home = prepare_codex_home(trees_root=repo.parent)

    rows = [
        run_variant("sentinel-loud", sentinel_home, repo, a.model, a.timeout,
                    ignore_user_config=False, marker=marker,
                    bypass_hook_trust=True),
        run_variant("sentinel-flag", sentinel_home, repo, a.model, a.timeout,
                    ignore_user_config=True, marker=marker,
                    bypass_hook_trust=True),
        run_variant("bench", bench_home, repo, a.model, a.timeout,
                    ignore_user_config=True, marker=marker,
                    bypass_hook_trust=False),
    ]

    for r in rows:
        print(f"\n=== {r['variant']} ===")
        print(f"  CODEX_HOME            {r['codex_home']}")
        print(f"  --ignore-user-config  {r['ignore_user_config']}")
        print(f"  hook fired            {r['hook_fired_count']}x")
        print(f"  shell calls           {r['shell_calls']}"
              f"   blocked-by-policy {r['blocked_by_policy']}")
        print(f"  mcp servers configured{r['mcp_servers_configured']}")
        print(f"  mcp tools issued      {r['mcp_tools_issued']}")
        print(f"  answer                {r['answer']!r}")

    loud, flag, bench = rows
    problems: list[str] = []

    if loud["hook_fired_count"] == 0:
        problems.append(
            "POSITIVE CONTROL FAILED: the sentinel hook did not fire even "
            "unshielded, so this probe cannot detect a hook and the `bench` "
            "row below is not evidence of anything."
        )
    if flag["hook_fired_count"] > 0:
        print(f"\n  ** --ignore-user-config did NOT suppress $CODEX_HOME/hooks.json "
              f"({flag['hook_fired_count']} firings). The bench-owned CODEX_HOME "
              f"is the mechanism; the flag is not.")
    if bench["hook_fired_count"] > 0:
        problems.append(
            f"bench variant fired {bench['hook_fired_count']} hook(s): the "
            f"bench CODEX_HOME is not clean."
        )
    if bench["mcp_servers_configured"]:
        problems.append(
            f"bench variant has MCP servers configured: "
            f"{bench['mcp_servers_configured']}. A bare cell must mount none."
        )
    if bench["mcp_tools_issued"]:
        problems.append(
            f"bench variant ISSUED MCP calls: {bench['mcp_tools_issued']}")
    if not bench["usage_seen"]:
        problems.append(
            "bench variant reported no token usage — the run may not have "
            "been authenticated, which on this CLI is not distinguishable "
            "from a cheap answer without looking (finding D17)."
        )
    if bench["blocked_by_policy"] and bench["shell_calls"] == 0:
        problems.append(
            f"bench variant had {bench['blocked_by_policy']} commands rejected "
            f"by the sandbox policy and completed no shell call. That is a "
            f"crippled agent, not a bare one, and it scores as the former."
        )

    print("\n=== verdict ===")
    if problems:
        for p in problems:
            print(f"  !! {p}")
    else:
        print("  clean: positive control fired, bench home fired nothing, "
              "zero MCP servers, agent could still use its shell.")

    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {a.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
