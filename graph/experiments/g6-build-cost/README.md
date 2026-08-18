# G6: graph build cost

Walk, parse, resolve, write edges. Nothing else on either side.

**Status: five arms, 35 repositories, 175 cells, 0 failed.**

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

## The result

Median across 35 repositories of each cell's own median, three timed builds per
cell after a discarded warmup.

| arm | median build | median peak memory | vs repowise | repos where fastest |
|---|---:|---:|---:|---:|
| **repowise** | **2.77s** | **75 MB** | — | 14 |
| CodeGraph 1.5.0 | 3.65s | 757 MB | 10.1x memory | **16** |
| Graphify 0.9.31 | 12.23s | 860 MB | 11.4x memory | 0 |
| code-review-graph 2.3.7 | 9.97s | 361 MB | 4.8x memory | 0 |
| codebase-memory-mcp 0.10.6 | 6.21s | 1,113 MB | 14.8x memory | 5 |

### Memory is the unambiguous result: 35 of 35

**We are the lowest-memory arm on every repository in the corpus, without
exception.** The median is 75 MB against CodeGraph's 757 MB and
codebase-memory-mcp's 1,113 MB, and the gap widens with size rather than closing:

| | small (≤1,000 files, n=25) | large (>1,000 files, n=10) |
|---|---:|---:|
| repowise | 64 MB | 152 MB |
| CodeGraph | 749 MB | 1,164 MB |
| Graphify | 844 MB | 984 MB |
| code-review-graph | 358 MB | 407 MB |
| codebase-memory-mcp | 842 MB | 2,829 MB |

Our worst cell in the whole corpus is **bevy at 468 MB**; four arms exceed that
on repositories a tenth the size. codebase-memory-mcp is the other end —
**5,523 MB on bevy and 4,646 MB on syft** — which on a 16 GB developer laptop
running an editor is a real constraint rather than a table entry.

### Build time is not a win, and the median hides why

The median says we are faster. The per-repository count says CodeGraph is
fastest on 16 repositories to our 14. Both are true, and the split is size:

| | small (≤1,000 files) | large (>1,000 files) |
|---|---:|---:|
| repowise | 2.04s | 10.63s |
| CodeGraph | 2.37s | **8.86s** |

**We win the middle and lose the tail.** The two worst cells are `exposed`
(36.65s against 8.82s) and `bevy` (34.19s against 15.14s) — the two largest
Kotlin and Rust repositories in the corpus. Our resolution does more work per
file and that cost grows, which is the trade the precision reading
([G1](../g1-edge-precision/)) is the other half of. Anyone quoting our median
build time without the large-repository row is quoting the half that flatters us.

Graphify is slowest throughout and it is not close at scale: 129.88s on
`exposed` and 100.27s on `syft`.

## Why every number here is a median

Run-to-run spread, measured as max minus min over each cell's timed builds, as a
percentage of that cell's median:

| arm | median spread | worst cell |
|---|---:|---:|
| repowise | 2.2% | 7.3% |
| CodeGraph | 3.0% | **13.3%** |
| Graphify | 2.0% | 10.0% |
| code-review-graph | 2.4% | 9.0% |
| codebase-memory-mcp | 1.4% | 10.9% |

A single build can land 13% off its own cell's median. Several of the
per-repository differences above are smaller than that, so a single-run timing
column would have been noise presented as a result — and it would have sat on
the same page as a hand-graded precision figure, which is the pairing that makes
a reader distrust both.

Peak RSS is stable enough that one build measures it. Time is not. The sweep
takes three of each because the cheap half of that pair is not the half that
matters.

## Method

* **Every arm builds a fresh scratch copy** with every tool's index stripped, so
  nothing reads a warm cache. Nothing under `test-repos/` is indexed in place:
  the frozen peer indexes there are baselines every published number reconciles
  against.
* **One warmup build per cell, discarded.** Measured cold, gitleaks took 6.92s
  against 1.90s warm — a 3.6x spread that would otherwise land entirely on
  whichever arm ran first.
* **Nothing is restored from the artifact cache.** A restored artifact carries
  the cost of the build that filled it, on some other day. The earlier 35-repo
  run is stamped `publishable: false` for cost for exactly that reason; this one
  builds every cell.
* **Median of three.** See above.
* **Peak memory is read from a Windows job object**, not from the child process
  handle, so it covers the whole process tree. Both arms spawn workers, and
  querying the direct child alone reported 4 MB for a tree that actually peaked
  at 317 MB.
* **Our arm is `repowise-subprocess`.** In process there is no child to attach a
  job object to and the number would be the harness's peak, so the in-process
  arm records `peak_rss_mb=None` deliberately and the driver refuses it.
* **Strictly serial.** One build at a time, always. A peak measured while
  another build runs is not that build's peak, and free memory on the
  measurement machine is well under what two of these arms want at once.

## Run

```bash
python graph/experiments/g6-build-cost/run_corpus_cost.py --runs 3
python graph/experiments/g6-build-cost/run_corpus_cost.py --repos gitleaks --runs 1
python graph/tools/render_cost.py            # tables, never typed by hand
```

Each cell is written to `cells/<repo>__<arm>.json` the moment it finishes, so a
crash costs one cell rather than the sweep, and a rerun resumes from what is on
disk. A failed arm is recorded as a failed cell and the sweep continues.

`run.py` beside this file is the single-cell instrument for when one number
looks wrong; `run_corpus_cost.py` is the sweep.

Refuses to run over a dirty measured tree. `--allow-dirty` downgrades the output
to `publishable: false` rather than lying about it.

## Provenance

Measured at **`13cc339a`** — `repowise 0.44.0+dev`. `git describe` reads
`v0.44.0-1-g13cc339a`: this is one commit past the `v0.44.0` tag, so **no figure
on this page may be quoted as a 0.44.0 result**.

Full data, including every individual build: `results/graph/g6-corpus/2026-08-19-13cc339a/`.

## Caveats

* **One machine, one operating system.** Every figure is Windows on one laptop.
  Memory ratios this large are unlikely to invert on Linux, but the absolute
  numbers will move and the node-based arms carry a runtime baseline that a
  different host will size differently.
* **The corpus caps at roughly 2,000 files**, except where a `(language, kind)`
  slot has no member under the cap. `hugo`, `dub` and `django` are excluded by
  it, so the largest repositories in the wider corpus are not represented and
  the tail behaviour above is measured on the largest 10 of 35 rather than on
  genuinely large repositories.
* **codebase-memory-mcp is a cost row only.** Its adapter does not yet produce
  comparable edge sets — see [its page](../../arms/codebase-memory-mcp.md) — so
  it appears here and in no coverage or precision table.
* Build time is one of two costs and the less interesting one. What the graph is
  worth once built is [G1](../g1-edge-precision/) and G2.
