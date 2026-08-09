# CocoIndex Code

**The sixth arm, added before publication rather than after, and it corrected two
claims this bench had already written down.** It is also the clearest case in the
field of a tool whose ranking depends entirely on which corpus you ask about:
fourth of six on JavaScript, **last of six on the sealed django half at 0.361**.

| | |
|---|---|
| What it is | AST chunking via tree-sitter, local embeddings, vector search over code chunks |
| Package | `cocoindex-io/cocoindex-code`, installed as `ccc`. **Not** `cocoindex-io/cocoindex`, which is an ETL framework and would be a category error to benchmark here |
| Version measured | **0.2.41**, read from `ccc version` on the installed binary |
| Index | `.cocoindex_code/target_sqlite.db`, SQLite plus sqlite-vec |
| Embedder | `sentence-transformers`, `Snowflake/snowflake-arctic-embed-xs`, 384 dimensions |
| API key | **none**. Local embeddings, no per-query cost, no LLM anywhere |
| Tools served | **1** |
| Tools allowlisted | **1 of 1**, its full surface |
| Coverage, sealed 42 | **0.361** · precision 0.092 · 7.1 files served · **6th of six**, and n=41, see below |
| Agent loop | **never run.** No arm has an agent-loop number for it |
| Non-code gold | **the only arm of six that retrieves any**, 2 of 8 on the JavaScript corpus |
| Cost exponent | 0.892 (R² 0.933), the only sublinear arm besides ours |

The version is read off the binary rather than a web page deliberately: PyPI
showed 0.2.40 when the arm was written, and the installed build was one release
later.

## Why it was worth the machine time

It advertises an **"instant token saving by 70%"**, which is the exact class of
claim this bench opens by reranking. It entered on the deterministic retrieval
layer, where a new arm costs index build time and no agent spend, and where a
badly set-up arm gets caught cheaply.

## What it found, and two of three go against the tool that runs this bench

**1. It is the only arm of six that retrieves non-code gold.** Every other arm,
ours included, scored zero on the documentation gold files. CocoIndex's default
include patterns name `**/*.md`, `**/*.mdx` and `**/*.json` explicitly, so those
files are reachable by construction for it and unreachable by construction for
three of the six. What it usefully proves is that **those files were reachable**,
which had been assumed rather than measured.

It pays for that on code, where it retrieves roughly a sixth of what `get_answer`
does. **This is a scope difference, not a scoreboard loss**, and the row shows
both sides of one trade in one line.

**2. It broke an instance recorded as a unanimous retrieval failure.** One
instance carries 18.4% of the gold on that corpus; all five existing arms scored
0.000 and it was hand-checked and written up as genuine and unanimous. CocoIndex
returned a gold file on it at **rank 1**, and the file was markdown. The five
zeros stand exactly as measured; the generalisation drawn from them never held.
**Only another arm could have caught that**, which is the whole argument for
adding arms before publishing.

**3. Where it places depends on the corpus, and the two answers are different.**
On the JavaScript corpus it is fourth of six, tied with code-review-graph in a way
that sample size cannot resolve: pooled favours one, mean-of-ratios favours the
other, and excluding one heavy instance they are identical. On the sealed django
half it is **last of six at 0.361 against code-review-graph's 0.445**.

The gap between those two placements is the non-code trade below. The JavaScript
corpus carries 21% documentation gold, which only CocoIndex reaches; the django
half carries 4.6%, so the thing it is uniquely good at is almost absent there
while the price it pays for that breadth is not.

## Setup traps, and the first one is serious

**1. `ccc mcp` takes no project argument at all, so the working directory decides
which repository it answers about.** `ccc mcp --help` on 0.2.41 lists exactly one
option, `--help`. There is no env var on that path either: `COCOINDEX_CODE_ROOT_PATH`
is read only by the legacy entry point, while `ccc mcp` walks **up** from the
working directory looking for `.cocoindex_code/settings.yml`.

**A server launched without an explicit `cwd` answers every question about
whatever repository sits above the harness, while reporting itself perfectly
healthy.** Every other arm here names its tree in argv, so a mis-pointed server
fails loudly. This one answers.

It is bound by `cwd` in both launch paths, and **proven positively rather than
asserted**: two different instance trees are asked the same question and the
answers must differ, because a server pinned to one repository returns identical
bytes for both. Asserting "we passed cwd" is not the check.

**2. Missing global settings are a hard exit, not a default.** `ccc index` writes
its own project settings (`auto_init=True`), so no `ccc init` is needed. But it
exits 1 with "Global settings not found" when `~/.cocoindex_code/global_settings.yml`
is missing and stdin is not a TTY, which every build in a harness is. That would
have failed all fifteen builds identically.

Resolved without guessing a model: the file was written once, before any build,
from the tool's **own** `default_user_settings()`, so the embedder is its
documented default rather than a choice this bench made. It is machine-global, so
it is recorded in the result rather than in the arm's index command.

**3. `refresh_index` defaults to true**, so an unmodified `search` reindexes
before answering, billing a rebuild to the cell and making the index a variable
mid-run. Pinned false, and every cell records the arguments actually sent.

**And a second rebuild, which is the more expensive one:** `ccc mcp` spawns a
background index at startup, unconditionally, with no flag to suppress it. On a
worktree pinned to its commit that walk finds nothing changed, so it cannot
change *what* is retrieved. It can stop retrieval happening at all.

**Run concurrently, that startup reindex starves the `search` call.** On the
django corpus at six workers, **11 of 42 cells returned no answer whatsoever**
and had to be re-queried one at a time; one instance could not be answered even
alone on an idle machine under a 900s ceiling, and is excluded from its row. The
other five arms ran the same harness at the same worker counts without needing a
serial pass. **CocoIndex is the only arm in this field that has to be queried
serially to be measured**, and that is a property of the tool rather than of the
bench.

**4. A killed build must be cold-reset, and the daemon holds the database.**
`ccc index` updates incrementally when `.cocoindex_code/` exists, so restarting
on a partial re-times an incremental refresh and publishes it as a cold build:
faster than the truth and flattering to CocoIndex specifically, since incremental
indexing is one of its advertised features. Worse, a reset run while the daemon
is alive **reports success it did not achieve**: the removal returned 0 and the
database reappeared underneath it from live SQLite handles. `ccc daemon stop`
first, then reset, then verify the directory is gone.

**5. The retrieval surface cannot be read with a plain SELECT.** `code_chunks_vec`
is a sqlite-vec `vec0` virtual table, and selecting from it without the extension
raises `no such module: vec0`. Its `+`-prefixed auxiliary columns live in a plain
shadow table **positionally, in declaration order**. Positional is exactly the
assumption that made a generic sniffer report the opposite of the truth for
CodeGraph, so the shadow table is not read until the declaration has been parsed
out of `sqlite_master` and the column confirmed, with the row count cross-checked
against the vec table's own rowid registry.

## What is good about it

**Cost, relative to what it does.** 150.1s at 2,322 source files and 1,506.9s at
28,346, which is 0.90x and 0.81x our own build. Second most expensive arm in the
field after us, and sublinear at 0.892.

**No API key and no LLM.** Local embeddings throughout. Of the six arms it is the
cheapest to run once built.

**It indexes what it says it indexes**, which is more than can be said for one
arm that indexes 10,276 markdown files and retrieves none of them.

## What is bad about it

**Its cost is materially noisier than any other arm's.** R² 0.933 against the
0.968 to 0.999 the other four fit, with visible inversions (1,525s at 22,448
files against 1,105s at 24,606). The builds ran serially on a quiet machine, so
this is not machine contention. It is a property of the tool or of the instances
and this run does not establish which.

**One tool, one shape of answer.** `search` returns ranked **chunks**, several
per file, so a limit of 10 buys ten chunks rather than ten files. De-duplicated
by file, its ranked list is shorter than the limit. That is the arm's own
behaviour and what an agent would see, not a handicap, but it means a naive
comparison of "files returned" against the other arms is not like for like.

**It is bimodal, and the mean flatters it.** On the sealed django half, 20 of 41
instances score exactly 0.000 and 11 score exactly 1.000, for a median of 0.200
against a mean of 0.361. On the JavaScript corpus the median is 0.000 outright.
It either finds the file or it is nowhere near it, which is what a single ranked
chunk list under a `limit` produces.

**Precision does not compensate for the short list.** It serves 7.1 files per
query, second fewest in the field, but at 0.092 precision, fourth of six. Serving
few files is only a virtue when they are the right ones: code-review-graph
reaches 0.445 from 5.4 files at 0.240 precision.

## What this bench cannot say about it

**It does not test the 70% token saving**, which is CocoIndex's own headline and
the reason it was worth machine time. This layer measures file coverage against
gold spans. A token claim needs an agent harness, and every token column this
bench has produced under Claude Code failed its own control. **No coverage number
here is a verdict on that claim in either direction.**

Its django row is **n=41 against every other arm's n=42**, so it is not a
like-for-like 42-instance measurement. The missing instance served its tool and
never answered, so it is named and excluded rather than counted as a zero;
counting it would put the row at 0.353 instead of 0.361, which is to say the
exclusion runs in CocoIndex's favour.

Its django row is also **dated later than the other five**, measured 2026-08-09
against their 2026-08-02 to 2026-08-06. Same sealed instances, same fixed gold
spans, same deterministic grading and no judge, which is why the comparison
holds.

## Where its numbers are

JavaScript corpus:

- Gate, including the two-tree binding proof:
  [`../../results/bakeoff_2026_08/layera_mui_dev15/gate__cocoindex.json`](../../results/bakeoff_2026_08/layera_mui_dev15/gate__cocoindex.json)
- Raw responses, verbatim: `../../results/bakeoff_2026_08/layera_mui_dev15/responses/`
- Builds: [`../../results/bakeoff_2026_08/layera_cocoindex_mui/prebuild.json`](../../results/bakeoff_2026_08/layera_cocoindex_mui/prebuild.json)
- Pre-registration: `../../configs/layera_cocoindex.PREREGISTRATION.md`

Sealed django half:

- Graded cells: `../../results/bakeoff_2026_08/rung8/graded__cocoindex-sealed42__cocoindex.jsonl`
- Per-cell records, including the `cwd` each server launched in and the arguments
  actually sent: `../../results/bakeoff_2026_08/rung8/cells/cocoindex-sealed42/`
- Pre-registration: `../../configs/layera_cocoindex_contextbench.PREREGISTRATION.md`

Arm definition: [`../../configs/arms.yaml`](../../configs/arms.yaml), `cocoindex`
