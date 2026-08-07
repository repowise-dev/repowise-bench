# Pre-registration: cocoindex-code as a Layer A arm

**Written 2026-08-06, before the tool was installed and before it indexed
anything.** It enters via Layer A, which is deterministic and graded against
ContextBench gold spans with no LLM judge and no agent spend, so a new arm costs
index build time and nothing else. Layer A is also where a badly set-up arm gets
caught cheaply, which is the point: the arm that gets silently zeroed in this
workstream has never once been ours, because ours is the only output format we
already know.

---

## 1. What is being added, and what is deliberately not

**`cocoindex-io/cocoindex-code`.** An AST-chunking semantic code search CLI and
MCP server. Rust transformation engine, tree-sitter chunking, local embeddings
via sentence-transformers (`Snowflake/snowflake-arctic-embed-xs`) so it needs no
API key, portable SQLite storage, incremental reindex, 28+ languages including
TypeScript. Installs as `ccc`; the server line is `ccc mcp`.

**NOT `cocoindex-io/cocoindex`.** That is an ETL/indexing framework you build a
pipeline with. Benchmarking it against repowise would be a category error of
roughly the LangChain kind, and the distinction is recorded here rather than
left to a reader to guess why the obvious-looking repo was skipped.

**Version pinned at measurement time**, not now. PyPI shows `0.2.40`, released
2026-08-06, which is the same day this was written, so the installed version is
recorded in RESULT.md from `ccc version` rather than assumed from a web page.

## 2. Why it is worth the machine time

It advertises, verbatim, **"Instant token saving by 70%."**

That is the exact class of claim this workstream opens by reranking: three public
claim-collapses (RTK 60-90% to +7.6% worse, Caveman 65% to 8.5%, Greptile 82% to
45%) are the reason the page exists, and the axis that matters is not whose
number is biggest but whose survives an independent rerun. A 70% claim from a
tool nobody has independently rerun is the highest-value target available at
Layer A prices.

**What this run does NOT do: it does not test the 70% claim.** Layer A measures
file coverage against gold spans, not tokens. A token comparison needs an agent
harness, and every token column this workstream has produced under Claude Code
failed its own control (`50-results/layerb-opus-claude/RESULT.md` section 4).
Saying so now prevents a Layer A coverage number being read later as a verdict on
a token claim it cannot reach.

## 3. The allowlist, and why 1 of 1 is not the graphify handicap

**`client_tools` is `mcp__cocoindex__search`, and that is the full advertised
surface.** Its README's own words: "The `ccc mcp` command exposes a single tool".

This needs stating explicitly because the number looks identical to a defect this
workstream committed and published. graphify was given **1 of the 10** tools it
serves, serena 3 of 29, crg 1 of 30, and each of those was a handicap that read
as a fair setup. cocoindex serves **exactly one** MCP tool, so 1 of 1 is
everything it has. `ccc grep`, `ccc status` and `ccc doctor` are CLI subcommands
and are not served over MCP, so allowlisting them is impossible rather than
declined. Same reading as codegraph, which serves 1 and gets 1.

**Confirmed against the running server before the arm scores anything**, via
`python -m harness.served_surface`. If it serves more than one tool, the
allowlist grows to match its documentation before any cell runs, not after.

## 4. Three unresolved items, each settled before it spends

Written from the vendor's documentation before installation, deliberately: the
`[full]` variant pulls sentence-transformers and torch, and that download plus
unpack is heavy disk I/O which would have contended with the mui builds being
timed the same evening (finding E1). So the arm entry exists and is honest about
what it has not yet checked.

1. **The wrong-repo hazard, and it is the serious one.** `ccc mcp` takes no
   documented project or path argument, so it resolves its project from the
   working directory, and `generate_mcp_config` writes no `cwd` key because no
   arm so far needed one. A server resolving to the harness cwd instead of
   `{tree}` would answer every question about the **wrong repository while
   reporting itself healthy**. That is finding A9's shape exactly, and A9 cost
   this workstream a rung. Resolution: check `ccc mcp --help` for a
   path/project flag; if there is none, add a `cwd` field to
   `generate_mcp_config` rather than guess. The arm does not run until one of
   those two is true.
2. **Whether `ccc index` requires `ccc init` first.** The docs call init
   "optional" in one place and list it as a step in another. If a bare
   `ccc index` errors on an uninitialized tree, `init` goes into the arm's
   `index` command rather than being run by hand, so the build stays
   reproducible.
3. **`refresh_index` defaults to TRUE.** An unmodified `search` call reindexes
   before querying, which bills a rebuild to a cell and makes the index a
   variable mid-run. That is the same objection that excluded crg's
   `build_or_update_graph_tool` and `embed_graph_tool`, and it cannot be fixed by
   exclusion because it is a parameter on the only tool served. **Layer A calls
   the tool directly, so Layer A pins `refresh_index=false`.** If this arm ever
   reaches Layer B, an agent's arguments cannot be pinned, so the coaching asks
   for false and the per-cell record reports what was actually passed.

## 5. The gate, and it is the same one every arm gets

**Before recording any zero, prove the arm was alive and the extractor works.**

- `ccc index` exits 0 and `.cocoindex_code/` is **non-empty**. A served index
  that is empty counts as cannot index, not as a tool that scored badly.
- `ccc status` reports a chunk or file count greater than zero **on TypeScript
  input specifically**. An indexer that silently emits nothing on `.tsx` is the
  graphify-0.012-MRR failure again.
- The server is called successfully at least once, with `isError` count zero and
  a response size recorded, so a zero is always distinguishable from a failure.
- **The path extractor is checked against real output before any grading.**
  graphify scored 0.012 MRR against a true 0.539 because a regex required
  whitespace before a path and graphify writes `[src=path]`. cocoindex returns
  code chunks and its path format is not known to any extractor in the tree.
  Resolution: one real `search` response is inspected by hand and a known-hit
  case is graded before any real cell, per finding E5.

  **Where that extractor actually lives, since the obvious place is wrong.** The
  graphify note in `configs/arms.yaml` says the fix is "Handled in
  `harness/path_extract.py`". **That file does not exist.** The Layer A machinery
  is not in the bench package at all; it is under the results tree:

  - `results/bakeoff_2026_08/rung8/rung8_runner.py` — the Layer A runner
  - `results/bakeoff_2026_08/rung8/canary_allarms.py` — per-arm build and query
  - `results/bakeoff_2026_08/rung5/retrieval_probe_multiarm.py` — the earlier probe

  The `pred__*` / `graded__*` / `SELFTEST_perfect` / `SELFTEST_wrong` files beside
  the runner show finding E5's known-perfect and known-wrong controls are already
  implemented in it, so cocoindex's extractor proof extends an existing control
  rather than inventing one. **Fix the arms.yaml pointer** so the next person does
  not repeat this search.

  **One integration gap, and it applies to all of mui rather than to cocoindex.**
  `rung8_runner.py` stages its own trees through `contextbench.core.checkout` into
  its own cache, while the mui trees are staged by `prep_mui_instances.py` at
  `bakeoff/_cbmui_src/cbmui-<id>/material-ui` with arm worktrees
  `bakeoff/lb-<arm>-cbmui-<id>-material-ui`. Those layouts do not coincide, so
  pointing the runner at mui without an adapter would find no index and rebuild
  everything in its own layout. The hook already exists (`--skip-build` plus
  `index_present(arm, tree)`), so this is a small adapter and not new machinery.
  It does not block index builds, which is the whole reason Layer A separates
  building from grading.

**Any of those failing means cocoindex is fixed, or is declared unrunnable in
this file, before it contributes a number.**

## 6. Isolation and provenance

- **One worktree per arm per instance** (finding E3). The index lands in
  `{tree}/.cocoindex_code/`, so it is per-worktree by construction and needs no
  extra handling.
- **Every instance tree asserted at its own pinned `base_commit`** before its
  build.
- **Timed builds on a quiet machine** (finding E1). Its timed build is taken
  separately from the mui smoke, not beside it.
- Local embeddings mean **no API key and no per-query cost**, so unlike our own
  arm there is no A9-class key-bridging problem, and unlike repowise there is no
  LLM synthesis to switch on by accident (finding E7).

## 7. What gets published

**Published regardless of outcome**, on the same terms as every other row. We
came last at 0.228 on django once and published that. If cocoindex beats us on
TypeScript file coverage, that ships in the same table, in the same run, with the
same prominence.

Coverage is reported with **precision and files-served beside it**, never
averaged into one figure, and at n=15 with a 12x instance-size range **no pooled
percentage is printed alone**: it travels with the mean-of-per-question value, the
median, and the largest single instance's share of the total. Where pooled and
mean-of-ratios disagree in sign, the number is an artifact and is reported as
one.
