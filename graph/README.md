# Graph quality

**Does the call graph contain the right edges?**

Take one line of Go:

```go
w.Write(payload)
```

To draw an edge, a tool has to work out what `w` is. If it cannot, it has two
options: emit nothing, or find something named `Write` and point at that. The
second is tempting because it makes the graph look bigger, and a wrong arrow is
indistinguishable from a right one once it is in the database.

This page measures which tools do that, and how often.

It is a different question from the one [`head-to-head/`](../head-to-head/README.md)
asks. That benchmark measures whether a tool points an agent at the right files.
This one opens the index and asks whether the edges inside it are true. A tool
can win the first and lose this one: pointing at the right neighbourhood does
not require the arrows between the houses to be correct.

**Contents:** [Results at a glance](#results-at-a-glance) ·
[Precision against a compiler](#precision-against-a-compiler) ·
[The coverage rows we lose](#the-coverage-rows-we-lose) ·
[Why a coverage number is not the result](#why-a-coverage-number-is-not-the-result) ·
[A denominator that flattered us](#a-denominator-that-flattered-us) ·
[Can the resolver be fooled](#can-the-resolver-be-fooled) ·
[Build cost, where we were wrong](#build-cost-where-we-were-wrong) ·
[Six repositories flattered us](#six-repositories-flattered-us) ·
[How this is organised](#how-this-is-organised) ·
[What to be suspicious of](#what-to-be-suspicious-of)

---

## Results at a glance

| Question | Answer | Where |
|---|---|---|
| Are our edges true? | **In all 7 oracle cells, no tool that finds as much of the graph gets more of it right.** Checked against the Go compiler and the TypeScript checker | [below](#precision-against-a-compiler) |
| Do we find every edge? | **No.** We lose oracle recall in all 5 Go cells, and cross-file coverage on 15 of 35 repositories | [below](#the-coverage-rows-we-lose) |
| Hand-graded precision, 9 languages | **84.8% vs 57.0%** against CodeGraph, intervals disjoint | [G1](experiments/g1-edge-precision/) |
| Can the resolver be tricked? | Nobody passes all three mutations. Two arms cannot be tested at all | [below](#can-the-resolver-be-fooled) |
| Build speed | **We are not faster.** Fastest on 14 of 35 repositories against CodeGraph's 16 | [G6](experiments/g6-build-cost/) |
| Memory | **Lowest of five arms on 35 of 35**, median 75 MB against 757 MB | [G6](experiments/g6-build-cost/) |

Two of those six run against us, and one corrects a claim we previously made.

**Nobody else in this field publishes a graph-correctness number against an
outside oracle.** We checked every comparable tool (see [arms/](arms/)).
CodeGraph publishes agent-efficiency deltas and a coverage table. Graphify
publishes memory and QA scores. code-review-graph publishes an F1 of 0.714 whose
ground truth is derived from the same graph its predictor walks, which measures
self-consistency rather than correctness. To their credit they say so
themselves, calling it "circular by construction" in both their README and their
eval code. That gap is why this benchmark exists.

---

## Status

Bold rows have run at a recorded commit across every arm. A row without bold
has a written protocol and no run behind it.

| | Experiment | What it settles | Status |
|---|---|---|---|
| **G1** | [Edge precision](experiments/g1-edge-precision/) | Of the edges a tool emits, what share are true? Hand-graded from source. | **both sides, nine languages, 540 graded rows** |
| **G2** | [Cross-file coverage](experiments/g2-cross-file-coverage/) | CodeGraph's own published metric, recomputed on every arm by one script. | **five arms, 35 repos, 11 languages** |
| G3 | Shared-denominator recall *(closed unwritten)* | Of the calls that exist in the source, what share does each tool resolve? | results, no page; [why](experiments/README.md#g3-recall-on-a-denominator-both-tools-share) |
| **G4** | [Oracle-anchored precision and recall](experiments/g4-oracle-anchored/) | Both, automatically, at n in the thousands, against a gold graph neither tool produced. | **five arms, Go and TypeScript, seven cells, 37,853 oracle edges** |
| **G5** | [Adversarial invariance](experiments/g5-invariance/) | Does the resolver actually resolve, or does it match names? | **scored, four arms, Go** |
| **G6** | [Graph build cost](experiments/g6-build-cost/) | Seconds and peak memory to produce the graph, and nothing else. | **five arms, 35 repos, 175 cells, 0 failed** |
| G7 | Language breadth *(closed unwritten)* | Every tool claims 20 to 40 languages. How many of them work? | results, no page; [why](experiments/README.md#g7-language-breadth-as-a-number) |

### The five arms

All behind [one adapter protocol](lib/arms.py), so an experiment takes an arm
name and stops caring what the tool is. Every one rebuilds byte-identically on
a repeat run, so a mutation's effect can be separated from drift.

| arm | version | in which tables |
|---|---|---|
| **repowise** | commit under test | all |
| **CodeGraph** | 1.5.0 | all |
| **Graphify** | 0.9.31 | all |
| **code-review-graph** | 2.3.7 | all |
| **codebase-memory-mcp** | 0.10.8, cost at 0.10.6 | all |

Each arm has [a page](arms/) recording its version, how it is built, what it
emits, and every normalisation decision that changes its numbers. Two of those
decisions are large enough to name here: Graphify's call edges are 93%
`INFERRED` by its own tagging, and code-review-graph stores unresolved callees
beside resolved ones, so we filter to the ones that resolve.

**Reading an adapter against a database the tool produced, rather than against
the schema in its source, has now falsified queries on three separate arms.**
The most recent found two real defects and one thing that only looked like one:
that tool stores no language because it derives one from the file extension at
read time, so our adapter reproduces its table rather than inventing an
attribution. Details on [its page](arms/codebase-memory-mcp.md).

That arm is also the only one of five that **refuses to start on a stock
developer profile carrying a second local account**. It validates the ACL of
every ancestor of two separate directories, fails closed, and a release build
offers no override. Its own default cache location fails its own check. There
are open upstream issues for four different triggers, so this is a known class
rather than something we discovered.

### Which experiments matter most

**G4 is the strongest thing on this page**, because its answer key comes from
the Go compiler rather than from us and anyone with the toolchain can regenerate
it. G4 and G5 are the two that do not exist anywhere else in this field. G1 took
the most human time and was, until G4, the only reading here that asked whether
an edge is true. G2 is the one a reader asks for first, because it is the number
our largest competitor puts on its front page.

### Where the numbers come from

Numbers were measured at **`3594ba75`** unless a section says otherwise; the
35-repository corpus at **`58576af0`**; G4 and the cost sweep at **`13cc339a`**.
All on a clean detached worktree, with a discarded warmup per arm per repository
for anything timed.

`lib/provenance.py` refuses to run against a dirty tree without `--allow-dirty`
and stamps anything produced that way `publishable: false`. **Every table here is
generated from `results/graph/` by the renderers in `tools/`, never typed**,
because the sibling retrieval bench twice published a row that no longer matched
the data behind it.

Check the instruments still work:

```bash
python graph/smoke.py          # 16 checks, exit code is the failure count
```

Note it needs an interpreter that can import `repowise.core`. Run under one
that cannot and it used to report six passes and two skips, having never
touched our graph; it now fails loudly instead.

---

## Precision against a compiler

Every other number on this page, including ours, is scored against something the
publisher controls. [G4](experiments/g4-oracle-anchored/) is not. Its answer key
is the Go team's own RTA call graph, computed over the type-checked program by
`golang.org/x/tools`, and on TypeScript the `tsc` checker's own resolution of
every call site. We cannot tune either one and anyone with the toolchain can
regenerate both.

Of the call edges each tool emits, the share the compiler confirms:

| cell | repowise | CodeGraph | codebase-memory-mcp |
|---|---:|---:|---:|
| cobra (via tests) | **0.972** | 0.929 | 0.912 |
| gitleaks (no tests) | 0.976 | 0.972 | 0.934 |
| gitleaks (with tests) | 0.974 | 0.971 | 0.922 |
| syft (no tests) | **0.943** | 0.872 | 0.635 |
| syft (with tests) | **0.950** | 0.864 | 0.673 |
| zod (no tests) | 0.992 | 0.729 | 0.987 |
| hono (no tests) | 0.977 | 0.805 | 0.949 |

**Two arms beat us on precision and both draw a much smaller graph.** On cobra,
code-review-graph scores 0.997 from 360 edges against our 1,455, recovering 17%
of the call graph to our 68%. Graphify takes both gitleaks cells by a
narrower version of the same trade, at 89% recall against our 95%.
Precision on its own is exactly as gameable as coverage on its own, in the
opposite direction: resolve one call correctly and you score 1.000.

**The claim that survives all five arms is about the pair.** In all seven cells,
**no arm that recovers at least as much of the call graph as we do is more
precise than we are.** It names no threshold, so adding an arm can only break it;
two arms were added after it was written and it held. Outright, we are the most
precise in one cell and tied in one more; against the two arms this experiment
started with it is seven of seven, and it should always carry that label.

**Recall runs the other way against the arm above us and our way against the two
below.** codebase-memory-mcp leads four of five Go cells and invents far more
that is not there; on syft more than a third of what it emits is a call the
compiler denies. Both halves are on
[the G4 page](experiments/g4-oracle-anchored/), which also decomposes what we
miss, and recall there must not be compared across repositories, because it
scales with how many entry points the oracle had rather than with tool quality.

### The result that matters most is not competitive

**The oracle reproduced the hand-graded audit.** G1 read Go by hand at 30 rows
per side and got 96.7% for us and 96.7% for CodeGraph. The Go compiler, over
roughly 1,600 edges on the same repository, says **97.6% and 97.2%**.

A person reading source and a type checker agreeing to within about a point is
the strongest evidence available that the 540-row hand-graded audit is accurate
rather than self-serving. It is worth more than any competitive row here.

### Where this can and cannot go

An oracle needs a compiler that can produce a ground-truth call graph. **Go and
TypeScript are done, and this programme stops at two.** C#, Java and Kotlin each
need a toolchain this machine does not have, and each repository would need a
working build on top of that; C++ needs a working CMake build per repository and
is heavier still. Rust has the toolchain and no sound call-graph tool exists for
it. **Python, Ruby and PHP admit no oracle even in principle**, because what a
call resolves to can change at runtime, so those languages stay hand-graded
permanently. That last one is a fact about the languages, not a gap in the
harness; the rest are work nobody has done here, which is a different and weaker
excuse, and it is stated rather than left to be inferred from the absence of a
row.

---

## The coverage rows we lose

codebase-memory-mcp beats us on cross-file `calls` coverage, on the fair shared
denominator, across the whole corpus:

| language | repos | denominator | repowise | codebase-memory-mcp |
|---|---:|---:|---:|---:|
| cpp | 6 | 1401 | 0.201 | **0.331** |
| csharp | 4 | 2099 | 0.334 | **0.473** |
| go | 3 | 1306 | 0.556 | 0.585 |
| java | 3 | 1820 | 0.685 | 0.710 |
| kotlin | 3 | 3384 | 0.447 | **0.548** |
| php | 3 | 4316 | 0.271 | **0.426** |
| python | 3 | 824 | 0.373 | **0.460** |
| ruby | 3 | 322 | 0.332 | **0.484** |
| rust | 3 | 2089 | 0.331 | **0.485** |
| swift | 1 | 98 | 0.388 | **0.602** |
| typescript | 3 | 626 | 0.377 | 0.363 |

**Per repository across all 35: we separate on none, they separate on 15, and 20
are ties.** That is a comprehensive loss on this metric and it is printed here at
full size.

G4 explains it. Coverage counts the files an edge reaches and never asks whether
the edge is real, so a tool that emits more edges wins it whether or not they
exist. The same tool that leads every row above is the one the Go compiler
contradicts on roughly half its output.

This is why **rule 1 of the [methodology](METHODOLOGY.md) says a coverage
percentage is never the result**, and why no table here prints one without a
precision number beside it. Generated by `tools/render_coverage.py`.

**Every row above now has a precision figure beside it, and until 2026-08-22 nine
of them did not.** codebase-memory-mcp had an oracle-anchored figure on go and
typescript only, so the finding that its coverage lead is bought with wrong edges
was measured on two rows and carried by inference on the other nine. That hole is
closed: [G8](experiments/g8-coverage-leader-precision/) draws G1's sample from
this arm on the nine languages no oracle reaches and reads all 270 rows from
source.

| | correct / n | 95% CI |
|---|---|---|
| **codebase-memory-mcp 0.10.8, nine hand-graded languages** | **137/270 = 50.7%** | [44.8, 56.7] |

**About half of the edges behind the coverage rows it leads do not exist.** On
the seven languages all three hand-graded arms share, it is 50.0% [43.3, 56.7]
against CodeGraph's 56.2% [49.4, 62.7] and our 81.4% [75.6, 86.1]: the two peers are
a tie with each other, and both separate from us. The failure is one mechanism: its
two bare-name tiers are 70% of its call edges and grade 39.1%, while its
type-backed tiers grade 91.9%.

Read the two tables together rather than either alone. It still reaches more
files than we do on every row above, and this page still prints that loss at full
size. What the sample settles is that the gap between the two coverage numbers is
not a gap in what the two tools know.

---

## Why a coverage number is not the result

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

## A denominator that flattered us

Coverage is a fraction, and this benchmark spent a session looking at
numerators. The denominator turned out to be where our best-looking cell came
from.

caffeine has 668 java files. We call 536 of them symbol-bearing; CodeGraph
calls 664. Session 1 read that as our gap, 128 java files or 19% of the
repository, repository producing no symbol at all, and called it the largest concrete
finding the bench had surfaced.

Adding two more arms settled it. **code-review-graph independently counts 536,
exactly as we do**; Graphify counts 664, as CodeGraph does. All four agree on
668 java files, so nobody's walk is at fault. Classifying the 128
([`probe_symbol_gap.py`](experiments/probe_symbol_gap.py)):

| what the 128 files are | count |
|---|---:|
| `package-info.java`, declares a package, no type | 123 |
| `public @interface X {}`, annotation declarations | 5 |

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
two-proportion test is not significant, so the honest answer is a tie. But the
0.608-against-0.517 row is retired, and it was ours.

This is why every arm implements `files_seen()` and why every cross-arm
comparison intersects on it before computing anything.

---

## Can the resolver be fooled

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

## Build cost, where we were wrong

This page previously said we lose on build cost and expect to keep losing. That
was carried over from the full-index comparison in
[docs/BENCHMARKS.md §6](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md),
which is a different denominator. Measured on graph construction alone, now
across **all 35 corpus repositories on five arms**: 175 cells, 0 failed, three
timed builds each after a discarded warmup, nothing restored from cache:

| arm | median build | median peak memory | vs repowise | repos where fastest |
|---|---:|---:|---:|---:|
| **repowise** | **2.77s** | **75 MB** | n/a | 14 |
| CodeGraph | 3.65s | 757 MB | 10.1x memory | **16** |
| Graphify | 12.23s | 860 MB | 11.4x memory | 0 |
| code-review-graph | 9.97s | 361 MB | 4.8x memory | 0 |
| codebase-memory-mcp | 6.21s | 1,113 MB | 14.8x memory | 5 |

**Memory is the result, and it is unambiguous: we are the lowest-memory arm on
35 of 35 repositories.** No exceptions, and the gap widens with size: 64 MB
against CodeGraph's 749 MB on repositories under 1,000 files, 152 MB against
1,164 MB above it. Our worst cell in the corpus is bevy at 468 MB; three arms
exceed that on repositories a tenth the size, and codebase-memory-mcp reaches
**5,523 MB** on the same repository.

**Build time is not a win, and the six-repo table used to say it was.** At n=6
we were fastest on four including both largest. At n=35 CodeGraph is fastest on
16 to our 14, and the split is size: we lead 2.04s to 2.37s under 1,000 files
and trail 10.63s to 8.86s above it. The two worst cells are `exposed` (36.65s
against 8.82s) and `bevy` (34.19s against 15.14s). **We win the middle and lose
the tail**, and our resolution doing more work per file is why. The other half
of that trade is [G1](experiments/g1-edge-precision/).

Our figures come from the `repowise-subprocess` arm, which builds the same graph
in a child process precisely so it can be measured the way every competitor
already was: in process there is no child to attach a job object to, and this
column was empty until now.

Two things to keep saying anyway. This is graph construction only: no
documentation, no embeddings, no health pass, and it must never be quoted
beside the full-index row. And CodeGraph produces more distinct call edges than
we do on several repositories, so seconds alone is not a quality claim.

Full per-repository tables are in [G6](experiments/g6-build-cost/).

### "But isn't CodeGraph Rust and repowise Python?"

Partly, and it matters less than the framing suggests. Their README leads with
"Kernel powered by Rust" and "the fastest complete code graph", and we are a
Python program that is within a rounding distance of it on build time and an
order of magnitude below it on memory. That deserves an explanation rather than
a victory lap, because the obvious reading, that a scripting language beat
compiled code, is not what happened, and on the largest repositories they are
comfortably faster than us.

**The Rust is in the parser, and their own README scopes it that way**: parsing
and extraction run in a compiled kernel with "one boundary crossing per file".
Resolution, graph assembly and storage stay in JavaScript on a bundled Node 24
runtime. Our side is the same shape: tree-sitter is compiled C, and Python only
orchestrates it. **On the hot loop both tools are running native code.** The
language difference lives in the glue, and the glue is not where the seconds
are.

**They persist a real index and we do not.** CodeGraph writes 115 MB of SQLite
on dub; our graph lives in memory and is discarded when the process exits.
Serialisation is genuine work that our number excludes, and that is a caveat in
their favour, not ours. The honest sentence is "faster to build the graph", not
"faster", and this benchmark should never write the second one.

**The memory gap is the more solid result.** 75 MB against 757 MB at the corpus
median, on 35 of 35 repositories, is a wide enough margin that no accounting
choice closes it, and it is mostly structural:
a V8 heap plus a bundled runtime plus holding a graph in memory to write it out,
against a build that streams.

**Their headline claim is about a number we have not measured.** The speed
CodeGraph advertises is mostly *incremental re-sync*, roughly 0.3s to fold one
saved file into a 4,400-file project, never re-scanning the tree. That is a real
capability, it is a different measurement from a cold build, and **we expect to
lose it**. It is listed as unmeasured in G6 rather than quietly omitted.

---

## Six repositories flattered us

Every per-language number this page carried before was a fact about one
repository wearing a language's name. The six head-to-head repositories are
typescript x2, java x1, csharp x1, python x1, go x1. Four of five languages
resting on a single repository, which is precisely what
[rule 13](METHODOLOGY.md) forbids.

The corpus is now **35 repositories across 11 languages**, pinned in
[`corpus/corpus.lock`](corpus/corpus.lock), ten of them at n=3 with a library,
an application and a framework each. Swift has only a library and is reported
as carrying no language-level claim.

Median incoming cross-file `calls` coverage, per language, ours against the
strongest competitor:

| language | n | repowise | CodeGraph | Graphify | code-review-graph | |
|---|---:|---:|---:|---:|---:|---|
| java | 3 | **0.606** | 0.434 | 0.340 | 0.261 | ours +0.172 |
| csharp | 4 | 0.291 | 0.280 | 0.128 | 0.000 | tie |
| go | 3 | 0.639 | 0.639 | 0.466 | 0.000 | tie |
| php | 3 | 0.334 | 0.317 | 0.135 | **0.443** | tie |
| ruby | 3 | 0.337 | 0.318 | 0.197 | 0.000 | tie |
| typescript | 3 | 0.390 | **0.426** | 0.064 | 0.434 | theirs +0.036 |
| kotlin | 3 | 0.439 | **0.498** | 0.350 | 0.067 | theirs +0.060 |
| python | 3 | 0.378 | **0.489** | 0.333 | 0.329 | theirs +0.111 |
| cpp | 6 | 0.336 | **0.496** | 0.184 | 0.000 | theirs +0.161 |
| rust | 3 | 0.342 | **0.513** | 0.118 | 0.200 | theirs +0.170 |
| swift | 1 | 0.388 | 0.582 | 0.368 | 0.000 | *n=1, no claim* |
| **median of the ten at n>=3** | | **0.360** | **0.462** | 0.197 | 0.200 | |

**On this reading we win one language, tie four and lose six.** That is the
opposite of the impression six repositories gave, and it is published because a
benchmark that only reports the corpus where it wins is an advertisement. On the
shared denominator below, which is the fair comparison, it becomes **one ours, six
tied, four theirs**, and the four that survive are the real finding.

### On the denominator both tools agree on

The table above is each arm's own metric on its own population, which is what
CodeGraph's published metric is and why it is reproduced that way. It is **not a
comparison**: two arms disagree about which files can carry an edge at all. Our
denominator is smaller than theirs in 8 of 11 languages and larger in exactly
two, cpp and rust, and that asymmetry has already reversed one headline on
this page, when 123 `package-info.java` files padded the peer's caffeine
denominator.

Recomputed on the files **both** arms call symbol-bearing, within the walk they
share, pooled per language with 95% Wilson intervals:

| language | repos | shared denom | ours | theirs | verdict |
|---|---:|---:|---:|---:|---|
| java | 3 | 1,820 | **0.685** | 0.495 | **ours** |
| csharp | 4 | 2,099 | 0.334 | 0.304 | tie |
| go | 3 | 1,382 | 0.527 | 0.551 | tie |
| php | 3 | 4,316 | 0.271 | 0.280 | tie |
| ruby | 3 | 322 | 0.332 | 0.348 | tie |
| swift | 1 | 98 | 0.388 | 0.582 | tie |
| typescript | 3 | 617 | 0.379 | 0.392 | tie |
| python | 3 | 824 | 0.373 | **0.455** | theirs |
| kotlin | 3 | 3,384 | 0.447 | **0.591** | theirs |
| rust | 3 | 1,995 | 0.341 | **0.489** | theirs |
| cpp | 6 | 2,035 | 0.226 | **0.419** | theirs |

**One ours, four theirs, six tied** on non-overlapping intervals, against one
win, four ties and six losses on the own-denominator reading. TypeScript and
Swift move from loss to tie once the populations match and the intervals are
honoured.

**cpp and rust do not move, and cpp gets worse.** Pooled, we go from 0.336 to
0.226 there, because the shared denominator removes files only we counted. So
those two are real resolution gaps and not denominator artifacts, which is what
makes them the right place to spend hand-graded precision rows next.

Two things the pooled figures hide, printed rather than left in the data:

* **cpp's pooled 0.226 against a per-repository median of 0.357** is one
  repository doing the work: `aria2` carries 1,118 of the 2,035 shared files at
  0.200. Weighting by size is what pooling means, but a reader should see it.
* **`nlohmann-json` is hard for everyone**: 0.108 us, 0.143 them. A
  header-only template library is close to the worst case for file-granular
  call resolution on both sides.

Java strengthens on the fair denominator rather than weakening: **0.685 against
0.495**, our clearest result on the page.

### The two things that stop this being the whole story

**Coverage is not correctness, and this page says so first.**
[Rule 1](METHODOLOGY.md) exists because a change that raises coverage and lowers
precision is a regression wearing a win's clothes; the same holds between two
tools. Our defensible claim has always been precision per edge, not more edges.

The hand-graded audit now covers **nine languages on both sides**, 30 rows per
side per language, every row read from source:
**ours 229/270 = 84.8%** [80.0, 88.6] against **theirs 154/270 = 57.0%**
[51.1, 62.8]. The intervals are disjoint. Full tables, per-repository splits and
the failure taxonomy are in [G1](experiments/g1-edge-precision/).

Our figure is **down** from the 89.3% this page used to quote, and it is worth
more: 89.3% was five languages chosen for continuity with earlier work, all of
them ones where we are strong. Read the 84.8% the other way round and it says
**roughly fifteen percent of our call edges are wrong**, which is the number to
plan against.

**Four of the nine cells separate; five are ties and are reported as ties**.
go, java, swift, rust and cpp. C++ is a tie in particular: a 23-point
point-estimate gap that sits inside two overlapping intervals is not a win, at
either n=30 or the n=50 depth read.

**And rust is weak on both axes at once.** It is our worst cell but one,
**22/30**, where the residual is 4 macro invocations graded as calls, 3
cross-module or overload collisions and 1 std type constructor; and it is a
confirmed shared-denominator coverage loss, 0.341 against 0.489. Coverage and
precision usually trade against each other, so a language losing both is a
genuine resolution gap rather than a metric artifact.

**One cell goes clearly to the peer, and it is worth publishing unprompted.** On
`seastar` they read **6/10 against our 4/10**, the only repository in the audit
on any language, where they beat us on a clear margin. Our failures there are
chained calls on an untyped receiver bound to an unrelated method; they infer the
callee's declared return type and validate against it, so a failed inference
costs them an edge instead of buying them a wrong one. On `aria2` both sides read
10/10 and they resolve 24,950 distinct call edges to our 9,486.

So the honest statement is: **we lead on precision pooled and on four of nine
languages, five languages are ties, one repository goes clearly to them, and we
trail on coverage on four languages after the fair recount.** What is still
missing is a test of whether the coverage gap is in **resolution reach** rather
than in which files we call symbol-bearing. Two candidate explanations for that
gap, receiver typing and symbol extraction, have each been measured and refused.

### A language does not have "a" rate

The spread across three kinds is reported instead of a mean, because the
disagreement is the finding. On our arm TypeScript reads **0.138 on zod and
0.589 on hono**, a 0.451 spread. Rust reads 0.175 on serde and 0.490 on
ripgrep. Quoting either end as "the TypeScript number" is the mistake the
three-kinds rule was written to prevent, and we nearly made it.

### What a claimed language delivers, for the tool that claims most

code-review-graph **walks the language and resolves zero cross-file call edges**
on 17 of the 35 repositories: all 6 cpp, all 4 csharp, all 3 go, all 3 ruby and
Alamofire. It resolves 12,395 on TypeScript. That is a per-language capability
gap rather than a broken run, and printing the zero is the entire point of G7:
every tool in this field claims 20 to 40 languages and none says what a claimed
language actually produces.

Our own worst rows are the other half of the same table. On zod we declare
symbols in only **269 of 400** TypeScript files and on hono **260 of 382**; a
file that yields no symbol cannot contribute an edge, so a third of those
repositories is unreachable before edge resolution is even attempted.

### Provenance

Measured at **`58576af0`**, `repowise 0.43.0+dev`.

**`v0.44.0` exists and is `7f44232e`.** An earlier version of this line said it
did not, which was wrong. What is true is narrower and matters more: **`#1708`
(`13cc339a`) landed after that tag**, and the rust and cpp precision cells were
measured on it, so those cells reflect code that is not in 0.44.0. Nothing on
this page should be quoted as "measured on 0.44.0"; every table here carries the
commit it was taken at, and [G1](experiments/g1-edge-precision/) pins a commit
per cell.

Where cells were taken at different commits, the staleness runs **conservative**.
Every resolver change between the earliest cell and `13cc339a` (`#1690`,
`#1692`, `#1708`) only removes wrong edges: measured at the time, they removed
16,122, 1,399 and a further set respectively, and **gained 0** between them. An
older cell can therefore only understate our precision, never overstate it.

Competitor artifacts were restored from the prebuild cache and warmup was
skipped, so this run is stamped `publishable: false` **for cost**. Coverage is
unaffected and the tables above stand: a restore reproduces every set the
protocol exposes byte for byte, which `smoke.py` asserts by storing and
restoring a real index with the SQLite sidecars deliberately present. See
[rule 12](METHODOLOGY.md). **No cost number on this page comes from that run.**

## How this is organised

```
graph/
  README.md            this page: the results index
  METHODOLOGY.md       the rules every experiment follows, and why each one exists
  corpus/              the repositories, their pins, and why each is in
  arms/                one page per tool: version, how it is built, what it emits
  lib/                 shared readers and statistics, no experiment logic
  experiments/<id>/    PREREGISTRATION.md, README.md with the result, run scripts
  tools/               table renderers; every table on these pages is generated
results/graph/<id>/    raw output, one directory per run
```

One experiment per directory, each self-contained, each with its prediction
written down before the run. Nothing on this page cites a number that does not
have a path under `results/graph/` behind it.

## What to be suspicious of

* **The head-to-head six are still six.** G2's paired arm-versus-arm rows and
  every cost figure come from those, and a paired sign test over six cannot
  reach significance below a 6-0 sweep. The 35-repository corpus fixes the
  breadth problem, not that one: it is measured with competitor artifacts
  restored from cache, so it carries coverage and not cost.
* **Ten languages at n=3 is still n=3.** Three repositories bound the spread;
  they do not estimate a language. Swift is n=1 and says so.
* **Every tool is held to a metric one of them designed.** G2 is CodeGraph's
  metric and we are reproducing it. G1 and G5 are ours, and a reader should
  discount them the same way. G4 is the exception and is why it now leads this
  page: the Go team wrote the oracle, not us.
* **G4 is two languages.** Go over three repositories and TypeScript over two,
  seven cells in total. It is not a nine-language claim and the page says so. Its `contradicted` bucket is strong evidence
  rather than proof: RTA is unsound under reflection and `go:linkname`, which is
  why the metric is named precision *against the oracle*.
* **Our worst cell is Java**, at roughly 67% edge precision, and it is also our
  largest edge count. [METHODOLOGY.md](METHODOLOGY.md) explains why it stands.
* **Two arms are being read through an adapter we wrote, and both of them now
  beat us on precision in some cell**, so the reading matters more than it did
  when they only appeared in coverage. Graphify's call edges are 93% `INFERRED`
  by its own tagging and we score all of them rather than the AST-certain tenth,
  which is the choice least favourable to us. code-review-graph stores
  unresolved callees in the same table as resolved ones, so we filter to the
  ones that resolve, which is the choice most favourable to it: on gitleaks that
  is 76 edges out of 4,367 rows. Both choices are argued in [arms/](arms/) and both change
  those tools' numbers substantially. A reader who disagrees with either should
  say so; the counts either way are recorded in every result file.
* **Four of our own results have moved against us**: the caffeine coverage
  cell, the build-cost claim, the per-language coverage picture, and the arrival
  of a fifth arm that beats us on coverage in every language and on recall in
  every G4 cell. All four are above rather than in a changelog.
* **We lose coverage and win precision, and we are the ones who decided which
  of those is the result.** That choice is argued in
  [METHODOLOGY.md](METHODOLOGY.md) rule 1 and was written down before this arm
  existed, but a reader is entitled to weigh the two differently.
