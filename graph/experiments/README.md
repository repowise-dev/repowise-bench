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

This already exists at n=300, 30 rows per side per language across five
languages, and has never been published. Porting it means moving the two sampling
instruments and the graded rows into this repository, not rerunning the audit.

The claim it supports is **precision per edge**, not "more edges". Raw counts put
us behind on three of five languages. That distinction is the whole result and
collapsing it into "we have more edges" would be a misrepresentation of our own
data.

Blocked on one thing: Python resolution is changing in the main tree, so the
Python cell describes a population that no longer exists. Port the instrument
first, redraw that cell after the Python work settles.

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

* [g2-cross-file-coverage](g2-cross-file-coverage/) — CodeGraph's published
  metric, reproduced. Has the first numbers.
* [g4-oracle-anchored](g4-oracle-anchored/) — precision and recall against a gold
  graph neither tool produced.
* [g5-invariance](g5-invariance/) — mutations that separate a resolver from a
  name-matcher.

## Sequencing

G2 is nearly done and is what a reader asks for first. G1 is a port and unlocks
the strongest claim we already own. G5 is cheap, needs no oracle, and is the one
that produces a finding nobody else can produce. G4 is the most valuable and the
most likely to consume a session on symbol identity alone, so it goes after G5
proves the harness shape works. G6 and G7 are mechanical and can run whenever.

**G5 before G4.** G5 needs no gold standard, and if the mutation harness cannot
rebuild a byte-identical baseline, G4 was never going to work either.
