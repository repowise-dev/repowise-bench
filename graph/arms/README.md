# Arms

One page per tool eventually. This is the survey that decided which tools are in.

Verified against the GitHub API on 2026-08-18: release tags, dates, stars, push
dates, archive status. Everything else came from project documentation and should
be re-read from source before being quoted.

## In the benchmark

| tool | version used | graph artifact | call edges queryable | why it is in |
|---|---|---|---|---|
| **repowise** | main | `.repowise/` | yes | us |
| **CodeGraph** | `@colbymchenry/codegraph@1.5.0` | SQLite, `.codegraph/codegraph.db` | yes | the only tool with a frozen index on our corpus, and the only one publishing a coverage table |
| **Graphify** | `v0.9.46` (2026-08-17) | `graph.json` | yes, `graphify query` | already an arm in the retrieval head-to-head |
| **code-review-graph** | `v2.3.7` (2026-07-18) | SQLite, `.code-review-graph/` | yes | already an arm, and the only competitor publishing an accuracy figure at all |

`v1.5.0` is CodeGraph's current tagged release and matches the binary that wrote
every frozen index in `test-repos/`, so this benchmark is already running against
their latest release. Their `main` is 104 commits ahead of the tag. Tracking an
untagged branch would mean a moving comparator, so the pin stays on the release
and gets revisited when they tag again.

## Considered and excluded, with the reason

**cocoindex** is an incremental data-transformation framework, not a graph
builder. Whether call edges exist depends entirely on which flow and sink you
configure. It stays in the retrieval head-to-head, where it is a fair arm, and is
out of this one because there is no canonical graph to open.

**Serena** persists no graph. It answers symbol and reference queries live
against a language server. There is nothing to read offline, so it cannot be in a
static graph-quality comparison. It remains a legitimate arm for agent-task
benchmarks.

**SCIP indexers** (`scip-python`, `scip-typescript`, `scip-java`, `scip-go`) have
no call-edge model. SCIP encodes definitions, references and a small relationship
taxonomy. A call graph has to be reconstructed by finding reference occurrences
inside function bodies and mapping back to the enclosing symbol, which is our
construction, not theirs. Excluded as an arm; kept as a candidate **oracle** in
[G4](../experiments/g4-oracle-anchored/) precisely because it carries `tsc`'s own
type information.

**tree-sitter-stack-graphs** was archived on 2025-09-09 and resolves definitions,
not calls. Including it would be a category error twice over.

**Joern** builds a real Code Property Graph with the most mature query language
in the survey, and is a plausible arm. It is out of the first round only because
it is not an agent-context tool and is not what a reader is comparing us against.
Worth revisiting as an **oracle** for C and C++.

**CodeQL** has genuine call-graph queries and official Windows binaries. Same
reasoning as Joern: better as a possible oracle than as a competitor.

**Blarify, potpie, code-graph-rag** are small and moving fast. `code-graph-rag`
is the interesting one: it emits explicit `CALLS` edges into Memgraph and has an
opt-in runtime-tracing mode that merges observed dynamic calls into the static
graph. That is a genuinely different design point from every purely static tool
here and it is the first candidate to add in a second round.

**Sourcetrail** was archived in 2021. **Understand** is commercial and closed, so
it cannot be scripted into an open harness without a licence.

## The finding that shaped the design

Of every tool surveyed, **none publishes a call-graph correctness number scored
against an external oracle.**

* CodeGraph publishes agent-efficiency deltas and a coverage table computed from
  its own index.
* Graphify publishes LOCOMO and LongMemEval scores, which measure a memory system
  built on the graph rather than the graph.
* code-review-graph publishes an F1 of 0.69 and precision of 0.546, scored
  against "graph-derived ground truth", which is its own graph. Self-consistency,
  not correctness.
* Serena, cocoindex, Blarify and potpie publish no accuracy figures at all.

There is real prior art in the literature to anchor against, and it is not being
used by anyone shipping these tools: PyCG's 112-case micro-benchmark (ICSE 2021,
MIT), CATS and the Judge toolchain for Java soundness, NJR-1 and XCorpus as Java
corpora, and the ISSTA 2024 "Total Recall?" dynamic-baseline methodology. No
comparable public benchmark exists for TypeScript, Go, or multi-language
repositories, which is where [G4](../experiments/g4-oracle-anchored/) and
[G5](../experiments/g5-invariance/) are aimed.
