# Graphify

**The arm this bench got most wrong, and the reason every extractor is now proved
by hand.**

| | |
|---|---|
| What it is | A JSON code graph with community, hub and shortest-path analysis |
| Index | `graphify-out/graph.json`, a flat JSON file in the tree |
| Tools served | **10** |
| Tools allowlisted | **7 of 10** (originally 1 of 10, which was wrong) |
| Cost exponent | 1.193 (R² 0.994) |

## The 0.012 that was really 0.539

Graphify writes its node lines like this:

```
NODE parse_config() [src=repowise/config.py loc=L149]
```

The path pattern reading that output **wanted whitespace before a path**. There
is none: the path sits immediately after `src=`. So Graphify scored **0.012 MRR
against a true 0.539**, a factor of 45, and nothing about the summary row looked
wrong. A broken extractor and a genuinely bad tool are indistinguishable
downstream.

**This is the single most instructive failure on this bench.** It is why every
arm's path extractor is now proved by hand against one real captured response
before a single cell is graded, and why every raw response is written to disk
verbatim beside the paths pulled out of it. A number produced by a benchmark that
has not done that is not evidence about the tool.

## The 1 of 10 that was also wrong

Graphify's first appearance in a scored run gave it **1 of the 10 tools it
serves**. That was a handicap, and it read as a fair setup.

It now gets 7. The 3 exclusions are all PR-review tools with nothing to read in a
cell: `list_prs`, `triage_prs`, `get_pr_impact`. There is no pull request and no
diff in a cell, the same exclusion repowise's `get_change_risk` gets.

**An arm we handicapped is not an arm we measured.** The allowlist rule in
[../THE_LOOP.md](../THE_LOOP.md) exists because of this arm and Serena.

## How it is set up

```yaml
index:  graphify update {tree}
serve:  graphify-mcp --transport stdio --graph {tree}/graphify-out/graph.json
```

Note the server takes the **graph file**, not the tree. It is the only arm here
that does.

## What it is good at

**It is the only competitor that indexes documentation at scale.** 10,276 `.md`
and 1,219 `.json` files in `nodes[].source_file` across the measured instances,
against zero for CodeGraph, code-review-graph and repowise.

**Graph analysis nobody else offers.** Communities, god nodes, shortest path
between two nodes, whole-graph statistics. If the question is structural rather
than "where is this behaviour", it is the tool in this field built for it.

## What it is bad at

**Coverage per file served, badly.** 0.546 coverage from **34.5 files per
query**, the largest response in the field by more than double, at 0.033
precision. That is the worst of both columns: it returns the most and finds
less than the tools returning a third as much.

**Its documentation index does not convert into documentation hits.** It indexes
10,276 markdown files and returned none of the gold ones on the corpus where a
fifth of gold is documentation. Unlike the three arms that cannot reach those
files at all, **Graphify's zero there is genuine**. Separating those two cases is
precisely what that gate was added to do.

**Adoption under Claude Code.** 3 of 15 questions, and 0 of 15 in a later
repetition. Its agent-loop effect on Codex is the smallest positive in the field
at -8.9%.

## Where its numbers are

- Sealed django coverage: [`../../results/bakeoff_2026_08/rung8/`](../../results/bakeoff_2026_08/rung8/)
- The extractor: `PATH_RE` and `paths_from_text` in
  [`../../results/bakeoff_2026_08/rung5/retrieval_probe_multiarm.py`](../../results/bakeoff_2026_08/rung5/retrieval_probe_multiarm.py)
- Arm definition: [`../../configs/arms.yaml`](../../configs/arms.yaml), `graphify`
