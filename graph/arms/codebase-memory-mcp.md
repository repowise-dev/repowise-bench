# codebase-memory-mcp

| | |
|---|---|
| upstream | https://github.com/DeusData/codebase-memory-mcp (DeusData) |
| release measured | `v0.10.8`, published 2026-08-19 (coverage); `v0.10.6` (cost and memory) |
| binary reports | `codebase-memory-mcp 0.10.8` |
| asset | `codebase-memory-mcp-windows-amd64.zip` |
| build command | `codebase-memory-mcp cli index_repository --repo-path <scratch copy>` |
| artifact | SQLite at `${CBM_CACHE_DIR}/<project>.db` |
| adapter | `graph/arms/codebase_memory_mcp.py` |

> **VERIFIED 2026-08-19 against real indexes it produced, and the gate caught
> three defects.** The arm registers and builds, and is now **included in the
> coverage tables as well as in cost and memory**.
>
> Reading the adapter against a database it produced, rather than against the
> schema in the tool's source, falsified three of its queries. All three are now
> resolved and the suite is green at 16/16.
>
> 1. **FIXED - callee identity was not stable across builds.** The `project`
>    name is derived from the absolute path indexed, so a scratch copy produced
>    `C-Users-ragha-AppData-Local-Temp-gq-gitleaks-8k_mag8d-gitleaks.logging.Error`
>    with a fresh random component every build. Two builds of one repository
>    shared **no** callee identities, so the determinism gate failed and any
>    cross-arm intersection would have come out empty - which reads as a finding
>    rather than as a path leaking into an identifier. The adapter now strips the
>    project prefix, read off the index rather than reconstructed from the build
>    path. Two builds of gitleaks agree on all their edges.
> 2. **FIXED - one synthetic pseudo-file broke a protocol invariant.** The tool
>    files builtin symbols under `<python-builtins>`, which has no row in
>    `file_hashes`, so `symbol_files` was not a subset of `files_seen`. Synthetic
>    `<...>` paths are now rejected.
> 3. **RESOLVED - the tool records no language for a file, because it does not
>    store one: it derives one.** The adapter read
>    `json_extract(properties, '$.language')` and `language` is not a key the
>    tool writes, so it returned `{}` and a language-scoped experiment saw an
>    empty denominator, which reads as a coverage of **zero** rather than as a
>    missing attribution. That is what held this arm out of the coverage tables.
>
> **Why (3) is now resolved rather than worked around.** The original reason for
> leaving it open was that deriving a language from the extension would mean
> this adapter inventing a classification scheme and applying it to another
> tool's index. Reading the tool's source falsified that premise. It has no
> stored language because it computes one from the file extension at read time,
> in the `arch_languages` path of its store layer, against its own
> extension-to-language table - and that is the derivation backing the
> `languages` breakdown its own `get_architecture` output prints to users. The
> adapter now reproduces that table, so this is the tool's published answer
> about itself rather than ours imposed on it. See **Language**, below, for the
> table, the two departures from their query, and the validation.
>
> There was also a precedent already in this benchmark that the original note
> missed: `graphify` records no language field either, and its adapter has been
> scoped by an extension map since it was written.
>
> It also walks its own output - `.codebase-memory/` is 3 of 314 rows in
> `file_hashes` on gitleaks - which is left in deliberately, because `files_seen`
> is meant to record what the tool actually walked. Note this is far too small to
> explain the `files_seen` gap against us (314 against 296 on gitleaks); the rest
> is non-source files, and language scoping is what removes them, not an exclude
> rule.

It is simultaneously a **corpus entry**, `test-repos/codebase-memory-mcp` at
pin `10cb0e03`, and a candidate arm. The two uses must never be blurred in a
result: a row reading "codebase-memory-mcp" is the tool indexing something, and
a row reading it in the repo column is somebody else indexing it.

One correction to the session note that requested this arm: the checkout is
**843 `.c` and 683 `.h`**, with only 5 `.cpp`. It is a pure-C program, which is
what its own README claims ("Pure C, no language runtime"), not the C++
repository the note described.

## What it emits

`nodes(id, project, label, name, qualified_name, file_path, start_line,
end_line, properties)` and `edges(id, project, source_id, target_id, type,
properties)`, plus `file_hashes(project, rel_path, sha256, ...)`.

## Normalisation decisions

Each of these changes the tool's numbers, so each is recorded for a reader to
disagree with.

### 1. Unresolved edges, the trap that cost code-review-graph 2,000 edges

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
| `CALL_REFERENCE` | callable used as a value, resolves to one exact target | **no**, see 2 |
| `USAGE` | "ambiguous values retained as `USAGE`" | **no**, this is their unresolved bucket |

`USAGE` is excluded from every set, including the arm's own dependency
vocabulary. Its row count is reported as `usage_rows_unresolved` in `extra`,
because it is this tool's own recall gap visible in its own database and that
is interesting. But it is not an edge it earned.

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
`start_line`, the line the calling function is declared on, not the line the
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
containment scaffolding, the role our own file nodes play, and a file
yielding only those has declared nothing.

### 8. Language: their table, applied the way their own query applies it

This tool stores no language on a node, and does not need to. It derives one
from the file extension at read time, in the `arch_languages` path of its store
layer, against an extension-to-language table it carries there. That derivation
is what backs the `languages` breakdown its own `get_architecture` output prints
to a user, so it is the tool's published answer about its own index.

The adapter reproduces that table (44 entries, MIT-licensed, attributed in the
adapter beside it) rather than reaching for one of ours. **The point is not that
extensions are a good way to attribute a language. It is that this is the
attribution the tool makes about itself, so a reader disagreeing with a `cpp`
row is disagreeing with a specific 44-entry table they can go and read, not with
an unstated choice of ours.**

Two deliberate departures from their query, both stated because both move
numbers:

1. **Population.** Theirs reads `nodes WHERE label='File'`; the adapter applies
   the table to `files_seen`, which comes from `file_hashes` and is the
   protocol's denominator for every arm. `file_hashes` is the wider set, so
   scoping to their `File` nodes would silently shrink this arm's denominator
   against the other four.
2. **Case.** Their names are returned verbatim (`"Go"`, `"C++"`) and
   `run_corpus.norm_lang` folds them, so the adapter stays a mirror and every
   arm is normalised in one place. `c++ -> cpp` was added there for this.

An extension absent from their table yields no entry at all, which is their
behaviour rather than a filter of ours: their lookup returns NULL and their loop
skips the row. That is what keeps `.md`, `.json` and `.txt` out of a language
denominator, and it is why language scoping - not an exclude rule - is what
closes the `files_seen` gap against us.

**Two of their choices a reader should know before quoting a number scoped by
this table:**

* **`.h` is C, not C++, and on a header-heavy repository this is the single
  largest effect the table has.** `fmt` is 46 `.cc` and 25 `.h`. Their rule
  attributes `cpp` to the 46 and `c` to the 25, and this arm's symbol-bearing
  `cpp` count is **exactly 46** - every header excluded, no residue. We attribute
  68 and CodeGraph 67, both taking the headers in. `graphify`, the other arm with
  no language field, maps `.h` the same way and also lands on 46. So the arms
  split into two camps on a naming rule, not on what they extracted, and the
  camps are `{us, codegraph}` against `{them, graphify}`.

  The pairwise shared denominator intersects both arms' language sets, so a
  comparison involving this arm on a C++ repository is scored on the ~46-file
  intersection and is fair. But it **is** a smaller and header-free population,
  and a `cpp` row involving this arm should say so rather than let a reader
  assume the denominator matched the one in our own C++ rows.
* `.sh`/`.bash` are "Bash" where `graphify` says "shell". Neither is a corpus
  primary language, so nothing published turns on it.

#### Validation, which is what the earlier open question actually asked for

The reason this was left open was that an extension-derived attribution "moves
numbers in a direction nobody has checked". It has now been checked, against the
arm that does report a real per-file language.

On gitleaks, over the 295 files both arms walked:

| | |
|---|---|
| `go` files by our attribution | 214 |
| `go` files by their table | 214 |
| identical set, not merely equal counts | **yes** - zero on either side |
| files both arms attribute | 275 |
| disagreements of any kind | **1** (`.sh`: we say `shell`, they say `bash`) |

So on the language that carries the comparison, the derived attribution and a
parsed one agree exactly. That is the evidence for allowing it, and it is
repository-specific evidence rather than an argument.

## Caching

`cache_payload` / `open_cached` are implemented, so this arm joins the artifact
cache from its first sweep rather than being retrofitted. Retrofitting after a
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
43 agent client surfaces, writing MCP config, hooks, `AGENTS.md`, skills and
subagents into the user's environment, Claude Code included. The adapter invokes
the extracted binary directly and never calls `install`. A benchmark must not
reconfigure the machine measuring it, and here that would have edited the very
agent session running the benchmark.

## Precondition: RESOLVED, and there were two gates, not one

**Gate 1, the coordination endpoint.** Cleared on 2026-08-19 by removing two
explicit ACEs from `C:\Users\ragha\AppData`, both left behind by a Codex sandbox
no longer in use: `Raghav_Strix\CodexSandboxUsers` and an orphaned SID
`S-1-5-21-...-249729005`. Note that `icacls /remove:g` silently matches nothing
for the orphaned SID and still exits 0; it takes a PowerShell `PurgeAccessRules`
plus `Set-Acl`. Backup and exact restore commands are in the main repo at
`local-stash/acl-backup/RESTORE.md`.

**Gate 2, the cache directory, and this one is in no note that requested this
arm.** With gate 1 clear the tool failed again, differently:

```
secure CLI coordination could not be created (cache-private):
C:\Users\ragha\Desktop: DACL entry 0 grants mutation rights 0x00010112
to untrusted identity (other S-1-5-21-3419747168-2900033029-1073337155-1004)
```

It validates the ancestors of `CBM_CACHE_DIR` **separately** from the ancestors
of the coordination endpoint, and `C:\Users\ragha\Desktop` carries its own
offending ACE, with a different SID again.

**Do not strip that one.** Point the cache somewhere clean instead:

```powershell
$env:CBM_CACHE_DIR = "C:\Users\ragha\AppData\Local\cbm-cache"
```

**This changed the adapter, and the change is now made.** `_DEFAULT_HOME` is
`C:/Users/ragha/Desktop/bench-worktrees/cbm`, which sits under `Desktop`, so a
cache derived from it failed gate 2. Caches now come from `_CACHE_PARENT` --
`%LOCALAPPDATA%/cbm-bench` -- while the **per-build fresh `CBM_CACHE_DIR` is
preserved**: each build still gets its own `mkdtemp` under that parent, which is
what stops an incremental indexer joining an index a previous build left behind
and reporting a graph this run did not produce. The binary itself may stay on
`Desktop`; only the cache and coordination trees are validated.

**There is a third offending path, and it is the tool's own default.** With
`CBM_CACHE_DIR` unset the tool falls back to `C:\Users\ragha\.cache`, whose
ancestors fail the same check:

```
secure CLI coordination could not be created (cache-private):
C:\Users\ragha\.cache: DACL entry 0 grants mutation rights 0x00010112
to untrusted identity (other S-1-5-21-...-1004)
```

This mattered beyond the builds: the registration probe ran with no
`CBM_CACHE_DIR`, so it hit that default and reported the arm permanently
unavailable for a reason **no actual build would have hit**. The probe now runs
through the same cache parent every build uses. An arm can be lost to its own
availability check.

`procmeter.run_measured` also gained an `env` parameter as part of this: the
adapter was already passing `env=` to it and the function did not accept one, so
every build would have raised `TypeError` the moment the gate cleared.

**The repository under test may stay on `Desktop`.** Only the cache and
coordination paths are validated; the fixture that succeeded was itself under
`Desktop\bench-worktrees`.

**The portability finding stands and is worth one line in any write-up.** Of the
arms measured this is the only one that refuses to run on a stock developer
profile carrying a second local account. It fails closed, offers no override in a
release build, and enforces the check on two independent path trees.

**It is also corroborated upstream, which is how it should be written up.** This
is not a quirk of one machine and should not be published as though we had
discovered it: the behaviour has open issues filed against it by other people,
covering four distinct triggers.

| upstream issue | trigger |
|---|---|
| a request to make the Windows cache-directory hardening configurable | asks for exactly the opt-out whose absence we hit |
| ancestor walk refuses app-container / capability SIDs | containerised hosts cannot start |
| WSL2 Windows drive mounts are world-writable `0777` | `/mnt/c`, `/mnt/d`, `/mnt/e` all fail the parent check |
| executable-identity check false-positives on a renamed Administrator account | RID 500 |

plus a standing Windows-platform umbrella issue. **Cite these rather than
asserting the finding from our own run.** A reader who suspects a misconfigured
benchmark machine can check that other people hit the same wall for four
unrelated reasons, and an accurate write-up says the maintainers are tracking it
rather than implying they are not.

## Original diagnosis, kept for the record

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

So this is not a bug in the adapter and not a fixable path choice. It is a
precondition the tool places on the machine. Either the ACE is removed from
`C:\Users\ragha\AppData`, or this arm is measured elsewhere. **Note this is
itself a portability finding about the tool, worth one line in the write-up:
it is the only arm of five that refuses to run on a stock developer profile
with a second local account.**
