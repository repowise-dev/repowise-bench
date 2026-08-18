# Graphify

`graphifyy==0.9.31` (note the double y in the package; the command is
`graphify`). Built with `graphify extract <path> --code-only`, which writes
`graphify-out/graph.json`.

Adapter: [`graphify.py`](graphify.py). Read from a real build of `gitleaks` on
2026-08-18.

**Version note.** The arms survey recorded `v0.9.46` as latest on 2026-08-17.
`uv tool install graphifyy` on 2026-08-18 resolved **0.9.31**. The published
number is whatever the harness stamps, and it stamps what it ran. This project
ships frequently and the pin must be re-read from the artifact, not from the
survey table.

---

## What it emits

NetworkX **node-link JSON**, so edges are under `links`, not `edges`:

```json
{"directed": false, "multigraph": false, "graph": {},
 "nodes": [...], "links": [...], "hyperedges": []}
```

On gitleaks: 853 nodes, 2,579 links, 65 communities, 1.27 MB.

| `relation` | count |
|---|---:|
| `calls` | 1,476 |
| `references` | 545 |
| `contains` | 476 |
| `method` | 73 |
| `embeds` | 7 |
| `rationale_for`, `defines` | 1 each |

Nodes carry `id` (deterministic snake_case), `label`, `source_file` (relative,
forward-slashed even on Windows) and `source_location` (`"L58"`). Edges carry
the same `source_file` / `source_location` pair, so **call edges have a line
number**, which is what makes them foldable to the same
`(file, line, target)` key as every other arm.

---

## The thing no other tool in this field exposes

Every edge carries **`confidence`: `EXTRACTED`, `INFERRED` or `AMBIGUOUS`**,
with a numeric `confidence_score` alongside (1.0 and 0.8 respectively in every
edge observed; `AMBIGUOUS` is legal and did not occur).

On gitleaks the split is 1,207 `EXTRACTED` against 1,372 `INFERRED` overall,
and for call edges specifically:

| | count |
|---|---:|
| `calls`, `EXTRACTED` | 104 |
| `calls`, `INFERRED` | **1,372** |

**93% of its call edges are heuristic rather than AST-certain.** This is the
most interesting property any arm in the benchmark has, because it lets G1 be
stratified: a single precision figure over a set that is mostly inferred tells
a reader far less than one figure per tier does, and the two are very likely to
differ. `call_edges(confidence=...)` exists for exactly that.

It is also the fair way to read this arm's size. Its 1,476 call edges are not
comparable, one for one, with a set that only contains resolved edges — most of
them are the tool's own guesses, and it says so.

---

## Two limitations the adapter has to declare

**It records no walk.** `manifest.json` lists the 224 files it classified as
code and parsed, not the 200 it skipped as unclassified, the 8 it skipped as
sensitive, or the docs `--code-only` excluded. `files_seen` returns what it
*processed*, which is a denominator the tool chose for itself, so a recall
figure against it flatters it. Every cross-arm comparison intersects against an
arm that records its walk properly.

**It has no symbol-kind and no language field.** An ordinary Go function node
is told from its file node only by shape: a file node's `label` equals its
`source_file`. `symbol_files` uses that test, and languages are inferred from
the extension, which is what any consumer of this format has to do.

Its output is also not deduplicated — `graphify diagnose multigraph` exists
because several edges can join one pair with different `relation`/`context`.
The protocol folds to distinct sets, so this is handled, but do not assume one
edge per pair when reading the raw file.

---

## Run configuration, and why it is a choice

Built `--code-only`. Graphify's full pipeline adds an LLM semantic pass behind
an API key. No other arm in this benchmark calls a model, and letting one do so
would compare a graph against a graph plus a language model. The AST path still
produces the whole node and edge set, including the INFERRED heuristics that
make up most of it.

A reader who runs the default command will get different numbers, and that is
worth stating rather than leaving them to discover it.

`graphify.html` loads `vis-network` from a CDN, so the generated visualisation
is not offline-safe. Irrelevant to the measurement, noted because it surprised
the survey.

## Caching this arm's artifact

`graph.json` and `manifest.json` are the only two files this adapter reads back;
`graphify-out/cache/` is graphify's own AST cache and is megabytes of nothing we
use. Both are copied out of the scratch tree before it is removed, which the
adapter did not previously do — it parsed `graph.json` into memory and dropped
the file — so a cached entry holds those two files and nothing else.

`index_size_mb` still reports the size of the whole output directory as built,
cache included, because that is what the tool wrote to disk. It is not the size
of what we keep.
