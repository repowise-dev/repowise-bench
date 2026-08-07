# code-review-graph

**The most tools in the field, the highest precision, and three setup steps that
each score a clean zero when missed.**

| | |
|---|---|
| What it is | A code graph plus a generated wiki, built for pull-request review |
| Index | `.code-review-graph/graph.db`, SQLite, plus embeddings |
| Tools served | **30** |
| Tools allowlisted | **20 of 30**, every exclusion named below |
| Graph size, django | 40,904 nodes, 380,168 edges |
| Cost exponent | 1.186 (R² 0.999) |

## What it serves, and what was excluded

30 tools, 20 allowlisted. The 10 exclusions, each about the shape of a cell
rather than the quality of the tool:

| excluded | why |
|---|---|
| `apply_refactor_tool`, `refactor_tool` | mutate source. No arm is given Edit or Write, so a refactor cannot be exercised and allowing it would break read-only parity |
| `generate_wiki_tool` | writes artifacts into the tree |
| `build_or_update_graph_tool`, `embed_graph_tool`, `run_postprocess_tool` | setup. The harness runs the equivalents before the cell; letting the agent rebuild mid-cell makes the index an uncontrolled variable and bills the rebuild to one cell |
| `detect_changes_tool`, `get_review_context_tool` | diff- and PR-shaped. There is no diff and no pull request in a cell |
| `cross_repo_search_tool`, `list_repos_tool` | multi-repo. One repo is under test, the same exclusion repowise's `list_repos` gets |

**`get_wiki_page_tool` and `get_docs_section_tool` are allowlisted even though the
build may not have generated a wiki.** If they come back with an error that is a
missing setup step in this arm's definition, which is exactly the class of defect
the per-server error count exists to surface. It is a defect to fix, not a tool to
quietly drop.

## Setup traps, and there are three

**None of these is guessable from its README, and each one produced a clean zero.**

**1. Every tool name carries a `_tool` suffix.** Calling the unsuffixed name
returned tool-absent for **84 of 84 queries**. That scores 0.000 and looks exactly
like a retrieval failure.

**2. `query_graph_tool` is not the search entry point.** It wants a `pattern` and
a `target` and rejects a natural-language query outright. The one whose own
description is "Search for code entities by name, keyword, or semantic
similarity" is **`semantic_search_nodes_tool`**.

**3. Provider and model must be passed on every search call, and the graph must
be embedded first.** `build --embedding-provider local` prints "Semantic search is
now active" and the search tool will still answer with `search_mode: "none"`
without them. Two separate halves of the same trap:

```yaml
index:
  # --embedding-provider and --embedding-model "must be supplied together";
  # passing the provider alone exits 2 with a usage error.
  command: [code-review-graph, build, --repo, "{tree}",
            --embedding-provider, local,
            --embedding-model, sentence-transformers/all-MiniLM-L6-v2]
activate:
  - tool: embed_graph_tool
    args: {provider: local, model: sentence-transformers/all-MiniLM-L6-v2}
```

and then `provider` and `model` again on **every** `semantic_search_nodes_tool`
call.

**4. It reports absolute Windows paths** in `results[].file_path`, unlike every
other arm here, which report repo-relative forward-slash paths. Path matching
normalises for it.

## What it is good at

**Precision, and it leads the field.** 0.240 on the sealed django half against our
0.087, from **5.4 files served** against our 19.2. Part of that is mechanical,
because precision rises for whoever returns fewest, but it is the most
conservative retriever here and if you are paying per file read that is the
profile you want.

**Breadth of surface.** 30 tools including flow analysis, community and hub
detection, impact radius and a generated wiki. Nothing else in the field
approaches that count.

## What it is bad at

**Coverage.** 0.445 on the sealed half, last of the four Layer A arms on django.
Its conservatism is the same property from the other side.

**Adoption, catastrophically, on one harness.** Under Claude Code with Sonnet the
agent called it **zero times across 15 questions**, despite 30 advertised tools
over a fully built and embedded graph. Under Codex it was called on all 15. That
is a fact about the pairing of tool and harness, not about the tool, and it is
why this bench never prints an adoption figure without its harness and date.

It is also the arm that produced the finding that killed dollars-per-question as
a metric here: behaving identically to a bare agent while carrying 28,118 extra
characters of tool schema, it measured **43% cheaper than the bare agent**. See
[../THE_LOOP.md](../THE_LOOP.md).

**Build cost.** The second most expensive competitor, superlinear at 1.186, and
the largest index on disk in the field.

## Where its numbers are

- Sealed django coverage: [`../../results/bakeoff_2026_08/rung8/`](../../results/bakeoff_2026_08/rung8/)
- Agent loop: [`../../results/bakeoff_2026_08/rung9/`](../../results/bakeoff_2026_08/rung9/)
- Arm definition: [`../../configs/arms.yaml`](../../configs/arms.yaml), `code-review-graph`
