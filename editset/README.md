# Rename edit set

Is `get_context(targets=[S], include=["references"])` the whole edit set for a
rename? Thirty symbols in `repowise-dev/repowise` at `ddf30359d`, every grep hit
classified by hand into code reference, string reference or prose, and bound to
the definition the tool picks (import resolution, re-exports, barrel files).
Five polymorphic methods also carry a "family" truth (overrides and calls
through the base type).

```
python editset/run_editset.py --repo <repowise checkout at ddf30359d with its index> --out refs.jsonl
python editset/score.py refs.jsonl <same checkout>
```

Per symbol: true sites, listed sites, recall, precision on non-prose sites,
whether the block was delivered, `complete`, `sites_total`, response bytes and
the kinds of every false positive (`comment_docstring`, `alias_or_noname`,
`other_symbol_or_string`). `truth/` holds the classified hits (`gt_*.json`), the
binding of each file to the picked definition (`bind_*.json`), the reviewed
lines and method-family verdicts, and the 30 calls (`calls.json`).

The scorer reads both response shapes: a flat `sites` list, and a per-file map
of kinds to line numbers (line text then comes from the checkout).
