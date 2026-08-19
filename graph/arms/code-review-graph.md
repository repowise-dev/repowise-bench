# code-review-graph

`code-review-graph==2.3.7`, PyPI. Built with `code-review-graph build --repo <path>`,
which writes SQLite to `<repo>/.code-review-graph/graph.db`.

Adapter: [`code_review_graph.py`](code_review_graph.py). Everything below was
read from the installed package and from a real build of `gitleaks` on
2026-08-18, not from marketing copy.

---

## The accuracy claim, and what it actually measures

This is **the only competitor in the survey that publishes an accuracy number
at all**, which is why it gets its own section rather than a row in a table.

Their README reports **0.714 average F1, 0.578 average precision, recall 1.000**
across 13 commits in 6 repositories.

That recall of exactly 1.000 is the tell, and to their credit they explain it
themselves rather than leaving it to be found. From the docstring of
`code_review_graph/eval/benchmarks/impact_accuracy.py`, in the installed
package:

> **graph-derived (circular, an upper bound)**, the historical mode. Ground
> truth is the changed files plus files with CALLS/IMPORTS_FROM edges into
> them, i.e. derived from the same graph the predictor traverses. Recall in
> this mode is an upper bound by construction, not independent evidence.

And from the shipped README:

> Blast-radius analysis recovers every file in the ground truth on all 13
> evaluation commits, **but read that as an upper bound, not as "100% recall"**:
> in this mode the ground truth [...] is derived from the same graph the
> predictor traverses, so it is circular by construction.

The mechanism confirms it: `_graph_neighbor_files()` builds the answer key by
walking `CALLS` and `IMPORTS_FROM` edges out of the same `graph.db` that the
thing being scored also queries. One database is both the exam and the mark
scheme.

**What we will say about it.** That the headline is a self-consistency
measurement, that self-consistency is a real property and not the same thing as
accuracy, and that **the authors disclose this in their own words**, "circular
by construction" and "not independent evidence", in both their code and their
documentation. They also ship a genuinely non-circular mode, grading against
files a human actually co-changed in the same commit, and explicitly decline to
quote its numbers before measuring them. That is more candour than the field
average, and it should be reported as such.

**Two corrections to our own earlier notes.** The figures are 0.714 and 0.578.
The pair `F1 0.69 / precision 0.546` that this benchmark's design notes carried
appears nowhere in 2.3.7's code or documentation, and must not be quoted.

---

## What it emits

Schema version 9. No `files` table; a file is a `nodes` row with `kind='File'`.

| table | what it holds |
|---|---|
| `nodes` | `kind` in `File`, `Function`, `Class`, `Test`, `Type`; `qualified_name` is UNIQUE and is the join key everywhere |
| `edges` | `kind`, `source_qualified`, `target_qualified`, `file_path`, `line`, `confidence_tier` |
| `flows`, `risk_index`, `communities` | derived analysis, not read by this benchmark |

On gitleaks: 781 nodes (459 `Function`, 216 `File`, 65 `Class`, 41 `Test`) and
6,299 edges: `CALLS` 4,367, `IMPORTS_FROM` 797, `TESTED_BY` 568, `CONTAINS`
565, `REFERENCES` 2.

---

## Three things the adapter has to correct for

**1. Unresolved calls sit in the same table as resolved ones.** When it cannot
resolve a callee it stores the bare identifier (`make`, `Notify`, `Flags`)
in `target_qualified` rather than dropping the row. Both other arms emit an
edge only when they bound it to a declaration, so `call_edges` joins to `nodes`
and keeps only rows that resolve. Without that join this arm would be credited
with roughly two thousand edges it did not earn.

**2. Every path is absolute, with Windows backslashes**, baked in from wherever
`build` ran. The adapter records the build root and normalises through
`arms.norm_path`. Two databases built on different machines share no path
strings at all, so nothing can be compared across runs without this.

**3. Its `File` nodes are a lossy record of the walk.** The build log says 224
files parsed; 216 have rows. A file that parses to zero nodes gets no row of
any kind and is invisible. `files_seen` is therefore labelled a lossy proxy,
and any shared-denominator comparison intersects against an arm that records
its walk properly.

Also worth knowing: the CLI's final summary printed 6,392 edges where
`SELECT count(*)` gives 6,299. Trust the table.

---

## The result that matters, on gitleaks

| | count |
|---|---:|
| `CALLS` rows | 4,367 |
| targets that are even qualified (contain `::`) | 180 |
| targets matching a real declaration | **105** |
| of those, **cross-file** | **0** |

**Every resolved call edge it produces on gitleaks is inside a single file.**
Its call graph does not connect one file to another on this repository, which
is why its cross-file coverage is zero rather than small.

The 75 qualified-but-unmatched rows are a resolution bug of its own: it
constructs `diagnostics.go::StartCPUProfile` for a method whose declaration it
stored as `diagnostics.go::DiagnosticsManager.StartCPUProfile`, so it fails to
join against a node it wrote itself.

This overturns the expectation this benchmark was carrying. The retrieval
head-to-head found it served the fewest files at the highest precision, and the
design notes predicted "a small, tight, high-precision edge set" that would be
the natural high-precision end of the G1 table. It is small, but it is small
because it is almost entirely intra-file, and an intra-file call graph is not a
high-precision version of a cross-file one. It is answering a different
question. G1 rows drawn from this arm must say so.

**One fairness caveat we should keep making.** `TESTED_BY` (568 rows) does link
files across the tree, and it is excluded from the dependency reading here on
the grounds that test coverage is not a code dependency, the same grounds on
which we exclude our own `co_changes`. A reader who thinks that is the wrong
call should know the number, so it is recorded in every result.

---

## Reproduce

```bash
pip install code-review-graph==2.3.7
python -c "import sys; sys.path.insert(0,'graph/lib'); import arms; \
  a=arms.get_arm('code-review-graph'); \
  art=a.build(__import__('pathlib').Path('../test-repos/gitleaks'), repo_name='gitleaks'); \
  print(len(a.call_edges(art)), len(a.cross_file_edges(art))); a.close(art)"
```

Community detection falls back to directory grouping unless `igraph` is
installed, so `communities` rows are not graph communities in a default install.
Irrelevant to the edge sets above, but do not read that table as advertised.

## Caching this arm's artifact

The database stores paths **absolute into the scratch tree that built it**, and
that tree is deleted the moment the build finishes. Normalisation strips the
build root as a prefix, so the root is recorded on the artifact rather than
recomputed.

A cached artifact therefore has to restore `build_root` out of its stored
metadata. Guessing it, or deriving it from the repository path, leaves every
row absolute, which does not raise, it just makes every cross-arm intersection
empty, and an empty intersection reads like a finding. `open_cached` refuses to
build an artifact when the metadata carries no root.
