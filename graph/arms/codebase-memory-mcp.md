# codebase-memory-mcp

| | |
|---|---|
| upstream | https://github.com/DeusData/codebase-memory-mcp (DeusData) |
| release measured | `v0.10.6`, published 2026-08-17 |
| binary reports | `codebase-memory-mcp 0.10.6` |
| asset | `codebase-memory-mcp-windows-amd64.zip` |
| build command | `codebase-memory-mcp cli index_repository --repo-path <scratch copy>` |
| artifact | SQLite at `${CBM_CACHE_DIR}/<project>.db` |
| adapter | `graph/arms/codebase_memory_mcp.py` |

> **Status: written, not yet verified against a real database.** The adapter
> below is written from the SQLite schema in the tool's own source
> (`src/store/store.c`), not from an index it produced, because the release
> binary will not run on the measurement machine — see *Precondition* at the
> bottom. Every query here is therefore a claim about the schema and not yet a
> measurement. It does not register as an arm until it can run, and
> `smoke.py --only arms` reports the reason as a SKIP rather than passing over
> it. **Do not publish a number from this arm before the smoke suite has built
> gitleaks with it.**

It is simultaneously a **corpus entry** — `test-repos/codebase-memory-mcp` at
pin `10cb0e03` — and a candidate arm. The two uses must never be blurred in a
result: a row reading "codebase-memory-mcp" is the tool indexing something, and
a row reading it in the repo column is somebody else indexing it.

One correction to the session note that requested this arm: the checkout is
**843 `.c` and 683 `.h`**, with only 5 `.cpp`. It is a pure-C program, which is
what its own README claims ("Pure C — no language runtime"), not the C++
repository the note described.

## What it emits

`nodes(id, project, label, name, qualified_name, file_path, start_line,
end_line, properties)` and `edges(id, project, source_id, target_id, type,
properties)`, plus `file_hashes(project, rel_path, sha256, ...)`.

## Normalisation decisions

Each of these changes the tool's numbers, so each is recorded for a reader to
disagree with.

### 1. Unresolved edges — the trap that cost code-review-graph 2,000 edges

**Not applicable here, structurally.** `edges.source_id` and `edges.target_id`
are `INTEGER NOT NULL REFERENCES nodes(id)`. An edge cannot exist unless both
endpoints are real nodes, so there is no bare-identifier row of the kind
code-review-graph stores in `target_qualified` and no filter is needed to reach
parity with the other arms.

The tool draws its own resolved/unresolved line one level up, in its edge
vocabulary, and this adapter follows it rather than inventing one:

| type | meaning (their README) | counted as `calls`? |
|---|---|---|
| `CALLS` | resolved invocation | yes |
| `ASYNC_CALLS` | invocation that is awaited | yes |
| `CALL_REFERENCE` | callable used as a value, resolves to one exact target | **no** — see 2 |
| `USAGE` | "ambiguous values retained as `USAGE`" | **no** — this is their unresolved bucket |

`USAGE` is excluded from every set, including the arm's own dependency
vocabulary. Its row count is reported as `usage_rows_unresolved` in `extra`,
because it is this tool's own recall gap visible in its own database and that
is interesting — but it is not an edge it earned.

### 2. `CALL_REFERENCE` is resolved but is not a call

Passing a function as an argument is not an invocation, and no other arm in
this benchmark counts one. It is therefore excluded from `call_edges` and from
`cross_file_edges(CALLS)`, and **included** in `kinds=None`, the arm's own
dependency vocabulary, where each tool is allowed its own reading. Its size is
reported as `call_reference_rows` so the choice can be reversed by a reader who
disagrees.

This is a decision that can only move the tool's number **down** relative to
the most generous reading, and it is made on the same principle applied to
every other arm.

### 3. `call_edges` lines are declaration lines, not call sites

`edges` carries no line column. The triple therefore uses the **source node's**
`start_line` — the line the calling function is declared on, not the line the
call appears on.

Consequence, stated rather than hidden: a function calling the same target
twice folds to **one** triple here, where an arm recording call-site lines
yields two. METHODOLOGY rule 2 folds on distinct `(caller_file, line,
callee_identity)`, and this arm's triples are strictly coarser than the others'.
**Its `call_edges` count is a lower bound and is not comparable like-for-like
with the per-site arms.** `cross_file_edges` is unaffected, because that folds
to `(source_file, target_file)` on every arm.

### 4. `files_seen` comes from `file_hashes`, which is a real walk record

One row per file the indexer hashed, whether or not it produced a node. This is
strictly better than code-review-graph, which has no files table at all and
under-counts by however many files parsed to nothing (8 of 224 on gitleaks).

### 5. Empty `file_path` is dropped, not rooted

External and stdlib nodes carry `file_path = ''`. Normalising those would map
them onto the repository root, where they would collide with a real file. They
are dropped from every file-level set instead.

### 6. Paths through `arms.norm_path`

`file_hashes.rel_path` is already repo-relative; `nodes.file_path` is absolute
into the scratch tree that built the index. Both go through `norm_path` with
the recorded `build_root`. A single backslash makes a cross-arm intersection
silently empty and an empty intersection reads as a finding.

### 7. Symbol labels

`Class`, `Function`, `Method`, `Interface`, `Enum`, `Type`, `Route`, `Resource`,
`Module` count as symbols. `File`, `Folder`, `Package` and `Project` are
containment scaffolding — the role our own file nodes play — and a file
yielding only those has declared nothing.

### 8. Language from `properties`, via `json_extract`

`nodes.properties` is a TEXT blob rather than a column set. `json_extract`
returns NULL for a node with no language rather than raising, so absent is
handled without a special case.

## Caching

`cache_payload` / `open_cached` are implemented, so this arm joins the artifact
cache from its first sweep rather than being retrofitted — retrofitting after a
sweep means redoing the sweep.

`build_root` is read out of the cached metadata and never recomputed. The node
paths inside the database are absolute into a scratch tree that was deleted
when the build finished; a guessed root leaves every path absolute, which reads
as an empty intersection rather than as a bug. This is the same failure
code-review-graph's adapter documents, and it is why the root is stored rather
than derived.

## Isolation

Each build gets a fresh `CBM_CACHE_DIR`, so exactly one `<project>.db` exists
in it and the project name never has to be re-derived from the path. The tool
derives that name by folding path separators to dashes, so a scratch path would
otherwise produce a different project name on every run.

The fresh cache also prevents a build joining an index a previous run left
behind. For an **incremental** indexer that is not a tidiness point: it would
mean measuring a graph this run did not produce.

## What was deliberately not run: `install`

The tool ships `codebase-memory-mcp install`, which by its own README configures
43 agent client surfaces — writing MCP config, hooks, `AGENTS.md`, skills and
subagents into the user's environment, Claude Code included. The adapter invokes
the extracted binary directly and never calls `install`. A benchmark must not
reconfigure the machine measuring it, and here that would have edited the very
agent session running the benchmark.

## Precondition: why this arm is currently absent

On the measurement machine the release binary exits before doing any work:

```
codebase-memory-mcp: secure CLI coordination could not be created (endpoint):
C:\Users\ragha\AppData: DACL entry 0 grants mutation rights 0x00010112
to untrusted identity (other S-1-5-21-...-1004)
```

`src/daemon/ipc.c` validates the DACL of every ancestor of its coordination
directory and rejects any ACE granting mutation rights to a non-trusted SID.
The coordination directory is `%LOCALAPPDATA%/cbm-daemon-<uid>`
(`win_default_runtime_parent`, `SHGetFolderPathW(CSIDL_LOCAL_APPDATA)`), and on
this profile `C:\Users\ragha\AppData` carries such an ACE for another local
account.

`CBM_CACHE_DIR`, `TMP`/`TEMP` and the working directory do not move it: the
coordination parent is not derived from any of them. The one override,
`CBM_TEST_DAEMON_RUNTIME_PARENT`, is behind `#ifdef CBM_ENABLE_TEST_SEAMS` and
is compiled out of release builds.

So this is not a bug in the adapter and not a fixable path choice — it is a
precondition the tool places on the machine. Either the ACE is removed from
`C:\Users\ragha\AppData`, or this arm is measured elsewhere. **Note this is
itself a portability finding about the tool, worth one line in the write-up:
it is the only arm of five that refuses to run on a stock developer profile
with a second local account.**
