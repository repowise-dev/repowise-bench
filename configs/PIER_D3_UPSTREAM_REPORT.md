# Upstream defect report for `datacurve-ai/pier`: the Codex agent silently disables every stdio MCP server

**Status: drafted 2026-08-28, ready to file. Not yet submitted.**
Owed since 2026-08-09. Re-verified against `datacurve-pier==0.3.0` on 2026-08-28.

This affects every pier user who runs an MCP arm through the Codex agent, not
only us. An MCP server configured this way never starts, **nothing logs an
error**, and the agent simply runs with no tools. A benchmark cell configured
with a tool and running without one is indistinguishable from a cell where the
tool did not help.

---

## Summary

`pier/agents/installed/codex.py::_build_register_mcp_servers_command` joins an
MCP server's `command` and its `args` into a single string and writes the result
into Codex's `command` key. Codex spawns that value directly rather than through
a shell, and its config schema expects `command` (the program) and `args` (a
list) as separate keys. Codex therefore looks for an executable whose *name* is
the entire joined string and fails to find it.

The same package already contains a correct implementation, in
`claude_code.py`, so the fix is a two-line change with an in-repo reference.

## Affected version

`datacurve-pier` 0.3.0 (installed from `git+https://github.com/datacurve-ai/pier`).

## The code

`pier/agents/installed/codex.py`, lines 1011-1025:

```python
def _build_register_mcp_servers_command(self) -> str | None:
    """Return a shell command that writes MCP config to $CODEX_HOME/config.toml."""
    if not self.mcp_servers:
        return None
    lines: list[str] = []
    for server in self.mcp_servers:
        lines.append(f"[mcp_servers.{server.name}]")
        if server.transport == "stdio":
            cmd_parts = [server.command] + server.args if server.command else []
            lines.append(f'command = "{shlex.join(cmd_parts)}"')      # <-- here
        else:
            lines.append(f'url = "{server.url}"')
        lines.append("")
    escaped_config = shlex.quote("\n".join(lines))
    return f'echo {escaped_config} >> "$CODEX_HOME/config.toml"'
```

`MCPServerConfig` (`pier/models/task/config.py:355`) already models the two
fields separately:

```python
name: str
transport: str = "sse"          # "sse" | "streamable-http" | "stdio"
url: str | None = None          # required for sse/streamable-http
command: str | None = None      # for stdio
args: list[str] = Field(default_factory=list)   # for stdio
```

so the information needed to emit the correct config is present and is discarded
at the point of writing.

## What it produces

A task declaring, for example:

```toml
[[environment.mcp_servers]]
name = "repowise"
transport = "stdio"
command = "repowise"
args = ["mcp", "/app", "--transport", "stdio"]
```

gets this written into `$CODEX_HOME/config.toml`:

```toml
[mcp_servers.repowise]
command = "repowise mcp /app --transport stdio"
```

Codex then attempts to execute a program literally named
`repowise mcp /app --transport stdio` and raises `FileNotFoundError`. Proved
in-container on 2026-08-09: the joined form raises, the split form spawns the
server normally.

## Why it is worse than a normal bug

**Nothing surfaces the failure.** The trial runs to completion, the agent
produces a patch, the verifier scores it, and the result carries no error. The
first repowise-armed cell in our 2026-08-09 run was a bare agent wearing our
label and returned `reward: 0`. Read naively, that is a result about the tool.

For a benchmarking runner specifically, a silently missing treatment is the
worst possible failure mode: it does not crash the run, it corrupts it.

## Suggested fix

Emit the two keys separately, matching Codex's schema:

```python
if server.transport == "stdio":
    lines.append(f'command = "{server.command}"')
    if server.args:
        lines.append(f"args = {json.dumps(server.args)}")
```

`pier/agents/installed/claude_code.py:1175-1200` already does the equivalent
thing correctly for Claude Code, keeping `command` and `args` distinct:

```python
servers[server.name] = {
    "type": "stdio",
    "command": server.command,
    "args": server.args,
}
```

so the Claude Code agent is **not** affected by this defect. Only the Codex
agent is.

### Two adjacent items worth fixing in the same pass

Both were found the same way and both also produce healthy-looking cells.

1. **Codex sanitizes the environment of its MCP children.** A variable set on
   the container and on the codex process does not reach the MCP server. In our
   case repowise could not see `OPENAI_API_KEY`, fell back to mock embedding
   vectors against a real index, and warned about it on a stream that pier's log
   plumbing swallowed. pier has no way to express per-server environment today;
   Codex's `[mcp_servers.<name>.env]` table is the documented mechanism and
   `MCPServerConfig` would need an `env: dict[str, str]` field to reach it.
2. **Codex's non-interactive approval policy is DENY.** Without per-tool
   `approval_mode = "approve"`, MCP calls return `"user cancelled MCP tool call"`
   in a run with no user in it. A cell in our own harness came back with
   `arm_exercised: true`, one tool call issued, `error: null` and a plausible
   score, on a call that had actually been cancelled.

### One unrelated crash worth a one-line fix

`_convert_events_to_trajectory` opens the Codex rollout JSONL with a bare
`open()`, so Python uses the platform default encoding. On a Windows cp1252
locale a single curly apostrophe emitted by the agent raises
`UnicodeDecodeError: 0x9d`. The trajectory is where tool-call adoption is read
from, so this blinds the very signal an MCP arm exists to measure. Passing
`encoding="utf-8"` fixes it.

## Workaround, for anyone hitting this before it is fixed

Give Codex a `command` with no arguments for pier to mangle, by baking a wrapper
into the image:

```dockerfile
RUN printf '#!/bin/sh\nexec repowise mcp /app --transport stdio "$@"\n' \
      > /usr/local/bin/repowise-mcp \
 && chmod +x /usr/local/bin/repowise-mcp
```

and declaring `command = "/usr/local/bin/repowise-mcp"` with empty `args`. Then
verify server-side rather than trusting the config: run Codex with
`RUST_LOG=codex_rmcp_client=info` and confirm a `ListToolsRequest` appears. **Do
not ask the model whether it has the tools** -- under identical config
`gpt-5.6-sol` returned different tool lists on successive runs and twice denied
having tools that were present and callable.
