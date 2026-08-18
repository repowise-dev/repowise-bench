# Graph quality

Does the call graph contain the right edges?

This is a different question from the one [`head-to-head/`](../head-to-head/README.md)
asks. That benchmark measures whether a tool points an agent at the right files,
and grades the answer. This one opens the index and asks whether the edges inside
it are true. A tool can win the first and lose this one: pointing at the right
neighbourhood does not require the arrows between the houses to be correct.

**Nobody in this field publishes a graph-correctness number against an outside
oracle.** We checked every comparable tool (see [arms/](arms/)). CodeGraph
publishes agent-efficiency deltas and a coverage table. Graphify publishes memory
and QA scores. code-review-graph publishes an F1 of 0.69 scored *against its own
graph*, which measures self-consistency rather than correctness. The gap this
benchmark fills is the obvious one, and it is the reason it exists.

---

## Status

Nothing on this page is published yet. This is the design plus the first
scouting numbers, not a result set. Rows marked **measured** have a number in
hand; rows marked **designed** have a written protocol and no run behind them.

| | Experiment | What it settles | Status |
|---|---|---|---|
| **G1** | [Edge precision](experiments/g1-edge-precision/) | Of the edges a tool emits, what share are true? Hand-graded from source. | measured privately, needs porting |
| **G2** | [Cross-file coverage](experiments/g2-cross-file-coverage/) | CodeGraph's own published metric, recomputed on both sides by one script. | **both arms run** |
| **G3** | [Shared-denominator recall](experiments/g3-shared-denominator/) | Of the calls that exist in the source, what share does each tool resolve? | designed |
| **G4** | [Oracle-anchored precision and recall](experiments/g4-oracle-anchored/) | Both, automatically, at n in the thousands, against a gold graph neither tool produced. | designed |
| **G5** | [Adversarial invariance](experiments/g5-invariance/) | Does the resolver actually resolve, or does it match names? | mutations built, scorer next |
| **G6** | [Graph build cost](experiments/g6-build-cost/) | Seconds and peak memory to produce the graph, and nothing else. | **both arms run** |
| **G7** | [Language breadth](experiments/g7-breadth/) | Every tool claims 20 to 40 languages. How many of them work? | designed |

G4 and G5 are the two that do not exist anywhere in this field. G1 is the one we
already have and have not published. G2 is the one a reader will ask for first,
because it is the number our largest competitor puts on its front page.

**Every number produced so far is a smoke test, not a result**, because the
`repowise` tree it measured has uncommitted ingestion changes from another
session. `lib/provenance.py` refuses to run without `--allow-dirty` and stamps
anything produced that way `publishable: false`, and the two files under
`results/graph/` carry a `-SMOKE` suffix for the same reason. Re-run at a clean
commit before quoting anything.

Check the instruments still work:

```bash
python graph/smoke.py          # 9 checks, exit code is the failure count
```

---

## First result: CodeGraph's coverage metric does not mean what it says

CodeGraph's README publishes a per-language coverage table (Python/requests 100%,
PHP/guzzle 100%, Go/gin 96.6%, Java/gson 93.3%, and so on for 22 languages) under
this definition:

> **Fair coverage** = the share of symbol-bearing source files that have at least
> one *resolved cross-file dependent*.

"Has a dependent" describes an **incoming** edge: something elsewhere depends on
this file. Read that way, the table does not reproduce, and it is not close. We
indexed two of their own benchmark repos with their own released binary
(`@colbymchenry/codegraph@1.5.0`, the current tagged release) and computed the
metric from the index it wrote:

| repo | their published figure | incoming edges only | either direction |
|---|---:|---:|---:|
| psf/requests | 100% | 79.4% | 97.1% |
| guzzle/guzzle | 100% | 60.3% | **100.0%** |

The metric they are actually reporting counts a file as covered if it sits at
**either end** of a cross-file edge. guzzle lands on 131/131 exactly. requests
misses by one file, which we attribute to commit drift, since their pin is not
published and ours is `4ed3d1b3`.

**That reading makes the metric close to uninformative.** A file that imports
anything at all satisfies it, and `imports` is an edge kind CodeGraph emits from
the file node itself. It measures whether the walker found the file, not whether
the resolver understood it. Run their own tool over the six repositories our
head-to-head already uses and the two readings separate by up to 70 points:

| repo | language | files | either direction (their metric) | incoming only | incoming `calls` only |
|---|---|---:|---:|---:|---:|
| dub | typescript | 3,911 | 0.991 | 0.748 | 0.589 |
| Ocelot | csharp | 732 | 0.985 | 0.669 | 0.352 |
| celery | python | 372 | 0.979 | 0.618 | 0.489 |
| zod | typescript | 291 | 0.938 | 0.240 | 0.148 |
| gitleaks | go | 213 | 0.915 | 0.789 | 0.784 |
| caffeine | java | 664 | 0.801 | 0.622 | 0.517 |

Their metric puts five of six repos above 0.9 and calls that a language result.
The incoming-`calls` column, which is the one that describes whether the call
graph connected anything, puts zod at 0.148.

**And the metric is provably insensitive to real improvement.** `#1684` added
**758 resolved call edges** to celery, 8.7% more, independently predicted before
it was measured. Coverage moved by **zero files**, 0.378 before and after.
Resolution improvements land in files that already had an edge, so the metric
saturates long before the graph stops getting better. A tool optimising this
number would take no credit for that change, and would pay no penalty for
undoing it. [Details](experiments/g2-cross-file-coverage/#the-coverage-metric-is-provably-insensitive-to-real-improvement).

**What we will and will not say about this.** We will publish the reproduction,
because a metric that cannot be reproduced from its own definition is worth
knowing about, and because we found the reading that does reproduce rather than
stopping at "it does not". We will not call it dishonest: "dependent" is loose
English, not a false statement, and the either-direction reading is a defensible
thing to want to measure. What we will not do is report our own number under
their metric and call it a win, because on a metric this saturated a win is
noise. G2's published table will carry all three columns for both tools.

Our side of this table is not measured yet. Numbers above are the peer's index
only, and the honest expectation is that we score similarly on the saturated
column, because it is saturated.

Reproduce: [`experiments/g2-cross-file-coverage/`](experiments/g2-cross-file-coverage/).

---

## How this is organised

```
graph/
  README.md            this page: the results index
  METHODOLOGY.md       the rules every experiment follows, and why each one exists
  corpus/              the repositories, their pins, and why each is in
  arms/                one page per tool: version, how it is built, what it emits
  lib/                 shared readers and statistics, no experiment logic
  experiments/<id>/    PREREGISTRATION.md, README.md with the result, run scripts
results/graph/<id>/    raw output, one directory per run
```

One experiment per directory, each self-contained, each with its prediction
written down before the run. Nothing on this page cites a number that does not
have a path under `results/graph/` behind it.

## What a reader should be suspicious of

* **Six repositories is not a language.** Every per-language figure here is one
  repository, chosen for continuity with earlier work, not sampled.
* **Both tools are held to a metric one of them designed.** G2 is CodeGraph's
  metric and we are reproducing it. G1, G4 and G5 are ours, and a reader should
  discount them the same way.
* **We lose on build cost and expect to keep losing.** See G6.
* **Our worst cell is Java**, at roughly 67% edge precision, and it is also our
  largest edge count. [METHODOLOGY.md](METHODOLOGY.md) explains why it stands.
