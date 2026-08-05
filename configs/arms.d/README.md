# Drop-in arms

Every `*.yaml` in this directory is merged over `configs/arms.yaml`, key by key.
Adding a tool to the bake-off needs **no Python change and no edit to a tracked
file** — write a YAML block here and name the arm in an experiment config's
`conditions:`.

Two things it is for:

1. **A new competitor.** The bake-off is meant to grow, and it is meant to grow
   with contributions from people who are not us. A tool's own authors know its
   launch flags, its real entry-point tool and its setup steps better than we
   do, and four separate arms in this workstream have been scored a clean 0.000
   purely because we guessed one of those wrong. If you maintain a tool in this
   comparison and think its arm is misconfigured, this file is the fix.

2. **Overriding an arm you did not write.** A partial block merges: to change
   only the served tool surface of `codegraph`, write only that.

```yaml
# configs/arms.d/mytool.yaml
arms:
  mytool:
    description: What it is, and anything a reader would otherwise guess wrong.
    mcp:
      server_name: mytool            # becomes the mcp__mytool__* tool prefix
      command: "{uv_bin}/mytool.exe"
      args: ["serve", "--repo", "{tree}", "--transport", "stdio"]
      served_tools: null             # or an allowlist string if the server takes one
    client_tools:
      - mcp__mytool__search
    index:
      command: ["{uv_bin}/mytool.exe", "build", "{tree}"]
      timeout_seconds: 10800
    activate:                        # setup calls made before anything is scored
      - tool: embed_tool
        args: {provider: local}
        timeout_seconds: 1800
    coaching: |                      # ignored when prompt_style is `neutral`
      Describe the tools plainly. No per-tool strategy advice.
```

Templates available in any string: `{tree}` (this arm's own worktree — never
share one, see finding E3), `{repo}`, `{repo_name}`, `{repowise_exe}`,
`{bench_root}`, `{npm_bin}`, `{uv_bin}`.

## Before you claim a number from a new arm

The harness will refuse a cell whose server did not start, and refuse one that
allowlists a tool the server never advertised. It cannot catch these, and each
of them has already cost a real arm a false zero in this bake-off:

- **The tool you named is not the tool the server searches with.** `code-review-graph`'s
  `query_graph_tool` wants `pattern` + `target` and rejects a natural-language
  query outright: 84 of 84 cells came back `isError`. Its actual search entry
  point is `semantic_search_nodes_tool`.
- **The build succeeded and the index is empty of what you need.** `code-review-graph`
  builds fine without `--embedding-provider`, reports "Semantic search is now
  active", and then answers `search_mode: "none"` for every natural-language
  query.
- **"Installed the package" is not setup.** Serena needs an explicit
  `activate_project` even with `--project` on its command line; without it every
  tool answers "No active project" and the arm scores a clean zero.

Run one cell and read it by hand before running any n. Check, in this order:
the server started and served the tools you expect; the agent actually called
them (`arm_exercised`); `mcp_isError_count` is zero; and the answer is not
empty. A dead arm and a bad arm produce identical summary rows.
