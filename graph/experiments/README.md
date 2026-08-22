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
disjoint. **The 540 graded rows are in
[g1-edge-precision/rows](g1-edge-precision/rows/)**, one file per cell, each row
with its verdict and the reason it was given; `verify_rows.py` rebuilds every
published table from them. One cell of the eighteen, rust on our side, ships its
draw without its grading and says so.

The claim it supports is **precision per edge**, not "more edges". Raw counts put
us behind on three of five languages. That distinction is the whole result and
collapsing it into "we have more edges" would be a misrepresentation of our own
data.

G4 has since reproduced this audit's Go cell from the Go compiler, at 97.6%
against the hand-graded 96.7%. Two unrelated methods agreeing to within a point
is the strongest evidence available that the hand-grading is accurate rather
than self-serving.

## G8: the coverage leader's precision, where no compiler can arbitrate

The coverage rows on the main page are led by codebase-memory-mcp on nine of
eleven languages, and G4 can only judge two of them. G8 draws G1's sample from
that arm on the other nine and reads every row from source.

**Measured and published: [g8-coverage-leader-precision](g8-coverage-leader-precision/)**,
137/270 = 50.7% [44.8, 56.7]. On the seven languages all three hand-graded arms
share, it is 50.0% against CodeGraph's 56.2% and our 81.4%: the two peers are a
tie with each other and both separate from us.

It is one arm rather than two because the other side of that comparison already
exists in G1 at the same seed on the same repositories, and re-reading it would
have bought nothing. The departures from G1's method, all of them forced by the
arm's own fields, are listed on the page.

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

**Not written up, and it will not be.** G3 has results and no page. Publishing
the numbers would need the file-set intersection built and the page written to
the standard the other experiments hold, and that work stopped when the oracle
made a shared denominator obtainable a better way: G4 compares both tools against
a denominator the compiler owns, which is what G3 was trying to approximate. The
results stay unlinked rather than half-cited.

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

**Not written up, and it will not be.** Like G3 it has results and no page. What
it was for is partly answered elsewhere: the coverage rows on the main page carry
the per-language shares over 35 repositories, with a precision number beside
them. The remainder, a breadth column for tools claiming twenty to forty
languages, is real and unbuilt, and stating that is better than shipping a page
at the end of a session.

---

## Experiments with their own directory

* [g2-cross-file-coverage](g2-cross-file-coverage/): CodeGraph's published
  metric, reproduced. Has the first numbers.
* [g4-oracle-anchored](g4-oracle-anchored/): precision and recall against a gold
  graph neither tool produced. **Measured on Go and TypeScript**, seven cells.
  The strongest evidence in this directory, because a reader can regenerate the
  answer key.
* [g5-invariance](g5-invariance/): mutations that separate a resolver from a
  name-matcher.
* [g8-coverage-leader-precision](g8-coverage-leader-precision/): G1's method,
  applied to codebase-memory-mcp on the nine languages no oracle reaches. 270
  rows, **50.7%**. Closes the hole the main page named against itself: its
  coverage lead was of unknown quality outside Go and TypeScript, and is not any
  more.

## Sequencing, and what changed

G2, G1, G5, G6 and G8 are measured. **G3 and G7 have results and no page, and
are now closed rather than pending**; each section above says why. They stay unlinked
rather than cited. G4 is measured on Go and TypeScript.

**The plan said G4 would consume a session on symbol identity alone. That was
right, and an early version of this paragraph said otherwise.** Keying on
declaration locations rather than on names did make the mapping land at a modal
offset of `(0,0)` on the first attempt, which is what the paragraph used to
claim. It was not enough: both oracles were also keying a caller at a closure,
which no tool in the comparison symbolises, and a modal offset computed over
matched edges cannot see a defect that only touches unmatched ones. Reading
twenty identities against source is what caught it, on each language in turn.

**The order to extend in is set by which languages admit an oracle at all**, not
by which experiment is next. Go and TypeScript are done. C#, Java and Kotlin
need an SDK each. Python, Ruby and PHP admit no oracle even in principle, because what a call resolves to can
change at runtime, so those languages stay hand-graded permanently. **G8 is what
"hand-graded permanently" looks like when it is actually done**: the nine
languages no oracle reaches, on the arm that leads their coverage rows.
