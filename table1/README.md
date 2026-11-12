# Table 1: answer tokens against the files a grep would open

The common method for context tools, applied to Repowise: for each question, how
many tokens does one core-tool answer cost, against the tokens in the files an
agent would open after grepping the question's key terms?

## Method (fixed before any call)

- **Repositories.** Six public repositories, three sizes, four languages, full
  clones (no `--depth`), each indexed keyless with
  `repowise init --no-prose --yes --no-editor-setup`. Commits are recorded per
  row in `raw/<repo>.jsonl`.
- **Questions.** `questions.json`: ten per repository, written with their key
  terms and gold files before any tool was called. Four `get_answer`, three
  `search_codebase`, one `get_symbol`, two `get_context` reference lists. Core
  tools only: `get_answer`, `get_context`, `search_codebase`, `get_symbol`.
- **Server.** `repowise mcp <repo>` over stdio with no provider key in its
  environment, an empty global config directory and the answer cache off. One
  warm-up call, then each question once.
- **Answer tokens.** The tool result's text content, `cl100k_base` tokens.
- **Grep-opened files.** Tracked text files that match every key term
  (`git grep -l -I -i -w -F`, one pass per term, intersected; when the
  intersection is empty, the first term alone), each counted in full.
- **Ratio.** Grep-opened tokens divided by answer tokens. Per repository:
  median, pooled (sum over sum) and lowest row.
- **Needed file present.** A gold path appears in the answer (rows that have a
  gold file; reference-list rows do not).
- Two stricter baselines are kept per row for completeness: the matched grep
  lines alone (`git grep -n` output) and the gold file read in full.

## Bar

At least 100x median ratio on every repository, pass or miss reported per
repository. Every row is published, including any below 1x.

## Rerun

```
python table1/run_table1.py --repos-dir <clones> --python <python with repowise> --out <dir>
python table1/render.py <dir>
```
