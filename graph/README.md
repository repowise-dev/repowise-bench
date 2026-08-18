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
| **G2** | [Cross-file coverage](experiments/g2-cross-file-coverage/) | CodeGraph's own published metric, recomputed on every arm by one script. | **four arms, six repos** |
| **G3** | [Shared-denominator recall](experiments/g3-shared-denominator/) | Of the calls that exist in the source, what share does each tool resolve? | denominators built, recall next |
| **G4** | [Oracle-anchored precision and recall](experiments/g4-oracle-anchored/) | Both, automatically, at n in the thousands, against a gold graph neither tool produced. | designed |
| **G5** | [Adversarial invariance](experiments/g5-invariance/) | Does the resolver actually resolve, or does it match names? | **scored, four arms, Go** |
| **G6** | [Graph build cost](experiments/g6-build-cost/) | Seconds and peak memory to produce the graph, and nothing else. | **four arms, six repos** |
| **G7** | [Language breadth](experiments/g7-breadth/) | Every tool claims 20 to 40 languages. How many of them work? | designed |

Four arms: **repowise**, **CodeGraph 1.5.0**, **Graphify 0.9.31** and
**code-review-graph 2.3.7**, all behind [one adapter protocol](lib/arms.py) so
an experiment takes an arm name and stops caring what the tool is. All four
rebuild byte-identically on a repeat run, so none of them is non-deterministic
and a mutation's effect can be separated from drift.

G4 and G5 are the two that do not exist anywhere in this field. G1 is the one we
already have and have not published. G2 is the one a reader will ask for first,
because it is the number our largest competitor puts on its front page.

Every number on this page was measured at **`3594ba75`**, on a clean detached
worktree, with a discarded warmup run per arm per repository.
`lib/provenance.py` refuses to run against a dirty tree without `--allow-dirty`
and stamps anything produced that way `publishable: false`. Every table is
generated from `results/graph/` by `graph/tools/render.py` rather than typed,
because the sibling retrieval bench has twice published a row that no longer
matched the data behind it.

Check the instruments still work:

```bash
python graph/smoke.py          # 14 checks, exit code is the failure count
```

Note it needs an interpreter that can import `repowise.core`. Run under one
that cannot and it used to report six passes and two skips, having never
touched our graph; it now fails loudly instead.

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

Reproduce: [`experiments/g2-cross-file-coverage/`](experiments/g2-cross-file-coverage/).

---

## Second result: a denominator can hand you a win you did not earn

Coverage is a fraction, and this benchmark spent a session looking at
numerators. The denominator turned out to be where our best-looking cell came
from.

caffeine has 668 java files. We call 536 of them symbol-bearing; CodeGraph
calls 664. Session 1 read that as our gap — 128 java files, 19% of the
repository, producing no symbol at all — and called it the largest concrete
finding the bench had surfaced.

Adding two more arms settled it. **code-review-graph independently counts 536,
exactly as we do**; Graphify counts 664, as CodeGraph does. All four agree on
668 java files, so nobody's walk is at fault. Classifying the 128
([`probe_symbol_gap.py`](experiments/probe_symbol_gap.py)):

| what the 128 files are | count |
|---|---:|
| `package-info.java` — declares a package, no type | 123 |
| `public @interface X {}` — annotation declarations | 5 |

So it is a definitional disagreement about `package-info.java`, plus five real
misses of ours.

**And it was inflating our score.** Those 123 files cannot receive an incoming
call edge, and measurement confirms that under CodeGraph not one of them does.
They are pure denominator padding:

| caffeine, incoming `calls` | repowise | codegraph |
|---|---:|---:|
| each arm's own denominator | 0.608 (326/536) | 0.517 (343/664) |
| **shared 536-file denominator** | **0.608** | **0.640** |

On its own denominator we lead by 9 points. On a fair one **CodeGraph leads by
3**. The intervals overlap, [56.6, 64.9] against [59.8, 67.9], and a
two-proportion test is not significant, so the honest answer is a tie — but the
0.608-against-0.517 row is retired, and it was ours.

This is why every arm implements `files_seen()` and why every cross-arm
comparison intersects on it before computing anything.

---

## Third result: the resolvers, adversarially

G5 mutates a repository in a way whose correct answer is known in advance. It
is the one experiment here that a coverage number cannot approximate, because a
resolver that binds by bare name and one that models scope score identically on
unmutated source.

gitleaks, Go, at `3594ba75`:

| arm | M1 decoy twin | M2 consistent rename | M3 shadowing |
|---|---|---|---|
| repowise | pass | pass | **fail** |
| codegraph | pass | pass | **fail** |
| graphify | untestable | untestable | untestable |
| code-review-graph | untestable | untestable | untestable |

**M1** adds a same-named declaration in a package nothing imports; 319 call
sites name that symbol. Neither we nor CodeGraph put a single edge on the
decoy. **M2** renames a symbol everywhere, isomorphically; both graphs come
through with zero edges lost and zero gained across 284 affected edges. Neither
tool is a name-matcher.

**M3** shadows an imported package with a local variable. Both of us still bind
the call through it. Ours resolves `secrets.NewSecret(...)` to the package
function after `secrets` has become an `int`; CodeGraph does the same. Nobody
in this field passes it.

`untestable` is not a pass and is never reported as one. Graphify and
code-review-graph resolved no edge to the mutated symbol at the baseline, so
the mutation cannot change their answer. An arm that resolves nothing cannot be
tricked, and scoring that as a pass would rank it above one that resolves
almost everything.

Reproduce: [`experiments/g5-invariance/`](experiments/g5-invariance/).

---

## Fourth result: we were wrong about build cost

This page previously said we lose on build cost and expect to keep losing. That
was carried over from the full-index comparison in
[docs/BENCHMARKS.md §6](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md),
which is a different denominator. Measured on graph construction alone, across
six repositories with a discarded warmup each:

| repo | repowise | codegraph | graphify | code-review-graph |
|---|---:|---:|---:|---:|
| dub (4,066 files) | **11.6s** | 22.3s | 176.5s | 56.5s |
| caffeine | **10.3s** | 11.2s | 49.8s | 33.9s |
| Ocelot | 6.1s | **4.3s** | 39.5s | 11.3s |
| celery | 4.6s | **3.9s** | 31.3s | 22.1s |
| zod | **3.4s** | 3.9s | 16.2s | 11.6s |
| gitleaks | **1.7s** | 1.7s | 5.0s | 3.7s |

Peak RSS, from a Windows job object over the whole process tree:

| repo | repowise | codegraph | graphify | code-review-graph |
|---|---:|---:|---:|---:|
| dub | **141 MB** | 1,752 MB | 1,534 MB | 373 MB |
| caffeine | **161 MB** | 1,534 MB | 995 MB | 477 MB |
| gitleaks | **54 MB** | 708 MB | 834 MB | 369 MB |

We are fastest on four of six including both largest, and use roughly a tenth
of CodeGraph's memory. Our figures come from the `repowise-subprocess` arm,
which builds the same graph in a child process precisely so it can be measured
the way every competitor already was — in process there is no child to attach a
job object to, and this column was empty until now.

Two things to keep saying anyway. This is graph construction only: no
documentation, no embeddings, no health pass, and it must never be quoted
beside the full-index row. And CodeGraph produces more distinct call edges than
we do on three of the six, so seconds alone is not a quality claim.

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
  repository, chosen for continuity with earlier work, not sampled. The paired
  sign test over six repositories cannot reach significance below a 6-0 sweep,
  so no corpus-level coverage claim is made from it.
* **Every tool is held to a metric one of them designed.** G2 is CodeGraph's
  metric and we are reproducing it. G1, G4 and G5 are ours, and a reader should
  discount them the same way.
* **Our worst cell is Java**, at roughly 67% edge precision, and it is also our
  largest edge count. [METHODOLOGY.md](METHODOLOGY.md) explains why it stands.
* **Two arms are being read through an adapter we wrote.** Graphify's call
  edges are 93% `INFERRED` by its own tagging, and code-review-graph stores
  unresolved callees in the same table as resolved ones, so we filter to the
  ones that resolve. Both choices are argued in [arms/](arms/) and both change
  those tools' numbers substantially. A reader who disagrees with either should
  say so; the counts either way are recorded in every result file.
* **Two of our own results moved against us this session** — the caffeine
  coverage cell and the build-cost claim — and both are above rather than in a
  changelog.
