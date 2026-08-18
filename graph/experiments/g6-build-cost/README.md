# G6: graph build cost

Walk, parse, resolve, write edges. Nothing else on either side.

**Status: both arms run, smoke only.**

## Why this is not the indexing-time row we already publish

The [OSS benchmarks page §6](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md)
reports that we are about 22x slower to index than CodeGraph, Graphify and
code-review-graph. That number is honest and it compares a full `repowise` index,
which also generates documentation, computes health and builds embeddings,
against tools that build a graph and stop. It answers "how long until I can use
this".

It does not answer "how expensive is your graph", and a graph-quality benchmark
that borrowed it would be charging our graph for four other layers. G6 times the
graph and only the graph, on both sides.

**The two must never appear in the same table.** They have different
denominators and a reader who sees 22x and 1.3x on one page will conclude one of
them is wrong.

## First numbers

gitleaks, go, 226 files. Median of 3 after a discarded warmup.

| arm | seconds | range | peak memory | note |
|---|---:|---|---:|---|
| CodeGraph | 1.95 | [1.89, 2.24] | 710 MB | `codegraph init -i`, whole product is the graph |
| repowise | 2.55 | [2.38, 2.65] | not measured | walk 
+ parse + resolve + build, in process |

**Smoke test, not a result.** The tree measured had uncommitted ingestion
changes. Re-run at a clean commit.

Two things to note before anyone gets attached to the 1.3x. It is one small
repository, and the ratio on a large one is the number that matters, since our
resolution does more work per file and that cost grows. And our memory column is
empty: we run in process, so there is no child to attach a job object to, and
reporting this interpreter's peak would report the harness. Our memory needs a
subprocess arm before that column means anything.

## Method

* Every run gets a fresh copy of the repository with every tool's index stripped,
  so nothing reads a warm cache. Nothing under `test-repos/` is indexed in place.
* **One warmup run per arm, discarded.** Measured cold, gitleaks took 6.92s
  against 1.90s warm, a 3.6x spread that would otherwise land entirely on
  whichever arm ran first. Both arms are treated identically.
* Median of three, not mean. One background process should not move a published
  number.
* Peak memory is read from a Windows **job object**, not from the child process
  handle. Both arms spawn workers, and querying the direct child alone reported
  4 MB for a tree that actually peaked at 317 MB. See `lib/procmeter.py`.

## Run

```bash
python graph/experiments/g6-build-cost/run.py \
    --repo ../test-repos/gitleaks --name gitleaks --language go \
    --runs 3 --arms peer,ours --out results/graph/g6/gitleaks.json
```

Refuses to run over a dirty `repowise` tree. `--allow-dirty` downgrades the
output to `publishable: false` rather than lying about it.

## Next

* A subprocess arm for our side so the memory column exists.
* The rest of the corpus, largest first, since caffeine is where the ratio will
  actually be decided.
* Graphify and code-review-graph arms.
