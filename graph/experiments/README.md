# Experiments

One directory each. A directory holds `PREREGISTRATION.md` (written first),
`README.md` (the result, written last), and the scripts that produced it. Raw
output goes to `results/graph/<id>/<date>-<commit>/`.

The four not yet given their own directory are specified below so the shape is
settled before anyone starts.

---

## G1: edge precision

**Ported, not new.** A stratified seeded sample of each tool's distinct resolved
edges, every row read from source by hand: the call site with its imports and
enclosing scope, then the target declaration. Verdicts are right / wrong /
ambiguous, and ambiguous is an honest answer.

**Measured and published: [g1-edge-precision](g1-edge-precision/)**, nine
languages, 270 rows per side, 540 in total. Ours 84.8%, CodeGraph 57.0%,
disjoint. The graded rows themselves are not yet in the repository; only the
tables are, and porting them is the remaining work here.

The claim it supports is **precision per edge**, not "more edges". Raw counts put
us behind on three of five languages. That distinction is the whole result and
collapsing it into "we have more edges" would be a misrepresentation of our own
data.

G4 has since reproduced this audit's Go cell from the Go compiler, at 96.5%
against the hand-graded 96.7%. Two unrelated methods agreeing to within a point
is the strongest evidence available that the hand-grading is accurate rather
than self-serving.

## G3: recall on a denominator both tools share

Each tool's own recall is quoted against its own denominator, and the two
denominators are not the same object. Ours is resolved over captured sites.
CodeGraph's is resolved over resolved plus unresolved. Neither is wrong and they
cannot be compared.

G3 uses a third denominator neither tool controls: the count of invocation nodes
in the tree-sitter AST, restricted to the file set both tools walked. Both sides
fold to distinct `(file, line, target)` first.

The instrument exists (`measure_g1_headtohead.py`). What it needs is the file-set
intersection, because a tool that skips a directory must not be credited with
perfect recall on the part it read.

## G6: graph build cost

Walk, parse, resolve, write edges. Nothing else. No documentation generation, no
embeddings, no health pass, because the comparison is graph against graph.

Reported per repository: wall clock, peak resident memory, output size on disk,
and edges per second. Three runs, median, one repository at a time, since two
heavy parses at once will exhaust memory on the measurement machine and produce
a number that is about the machine.

**We expect to lose this row and will publish it at whatever it says.** For
scale, CodeGraph indexes requests, 50 files, in 732ms. Our existing phase timings
for the head-to-head six are single-threaded lower bounds taken with no
persistence, and are not publishable as build cost; G6 needs its own run.

This is deliberately a different measurement from the indexing-time row in the
OSS `docs/BENCHMARKS.md`, which compares a full index against graph-only tools.
The two must never be quoted in the same table.

## G7: language breadth, as a number

CodeGraph claims 20 native languages and more through a second path. Graphify
claims 37 grammars. code-review-graph claims 40 languages. Nobody says what a
claimed language actually delivers.

For each claimed language, on a real repository in that language: the share of
source files that produce at least one node, the share that produce at least one
resolved cross-file edge, and edges per thousand lines. A language where files
parse into nodes but nothing resolves is a parser, not a graph, and this turns
that distinction into a column.

Run over the 91 repository pool. Expect this to be unflattering somewhere on our
side too, and report where.

---

## Experiments with their own directory

* [g2-cross-file-coverage](g2-cross-file-coverage/): CodeGraph's published
  metric, reproduced. Has the first numbers.
* [g4-oracle-anchored](g4-oracle-anchored/): precision and recall against a gold
  graph neither tool produced. **Measured on Go.** The strongest evidence in
  this directory, because a reader can regenerate the answer key.
* [g5-invariance](g5-invariance/): mutations that separate a resolver from a
  name-matcher.

## Sequencing, and what changed

G2, G1, G5 and G6 are measured. G3 and G7 have results but no page, so they are
unlinked rather than cited. G4 is measured on Go.

**The plan said G4 would consume a session on symbol identity alone. It did
not.** Keying on declaration locations rather than on names made the mapping
land on the first attempt, at a modal offset of `(0,0)` across all three arms.
What actually cost time was reading the oracle's own output correctly.

**The order to extend in is set by which languages admit an oracle at all**, not
by which experiment is next. Go is done. TypeScript is reachable with the
toolchain already installed. C#, Java and Kotlin need an SDK each. Python, Ruby
and PHP admit no oracle even in principle, because what a call resolves to can
change at runtime, so those languages stay with G1 permanently.
