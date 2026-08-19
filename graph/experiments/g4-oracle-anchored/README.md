# G4: precision and recall against an oracle neither tool produced

**Status: measured on Go and TypeScript, five repositories, seven cells, five
arms.** Predictions were written in [`PREREGISTRATION.md`](PREREGISTRATION.md)
before any run and are graded at the bottom of this page, including the ones
that missed.

The five Go cells below were recomputed after the caller key was corrected to
match the TypeScript oracle's. Any earlier Go figure on this page is superseded
and should not be quoted; [the method section](#the-method-and-the-one-idea-that-makes-it-work)
says what changed and why.

## Why this experiment exists

Every graph-quality number in this field, including ours, is scored against
something the publisher controls. Ours was hand-graded by us. CodeGraph's
coverage table is computed from its own index. All of them share a defect: a
tool that is confidently wrong in a consistent way scores well, and a reader has
no way to check.

G4 removes that. The answer key is the **Go team's own RTA call graph**, from
`golang.org/x/tools/go/callgraph/rta`, computed over the fully type-checked
program. We did not produce it, we cannot tune it, and anyone with the Go
toolchain can regenerate it.

It also removes the n=30 ceiling. Hand-grading caps a precision estimate at
roughly ±13 points near a 95% rate. An oracle grades every edge, so n becomes
the size of the repository.

## Headline: precision, and why it is not the whole claim

Of the call edges a tool emits, the share the compiler confirms.

| cell | repowise | CodeGraph | codebase-memory-mcp | Graphify | code-review-graph |
|---|---|---|---|---|---|
| cobra (with tests) | 0.972 [0.963, 0.980] | 0.929 [0.916, 0.940] | 0.912 [0.898, 0.925] | 0.971 [0.958, 0.980] | **0.997 [0.984, 1.000]** |
| gitleaks (no tests) | 0.976 [0.967, 0.982] | 0.972 [0.962, 0.979] | 0.934 [0.921, 0.945] | **0.997 [0.993, 0.999]** | 0.759 [0.630, 0.854] |
| gitleaks (with tests) | 0.974 [0.965, 0.981] | 0.971 [0.961, 0.978] | 0.922 [0.909, 0.934] | **0.995 [0.990, 0.998]** | 0.800 [0.692, 0.877] |
| syft (no tests) | 0.943 [0.935, 0.949] | 0.872 [0.862, 0.881] | 0.635 [0.623, 0.646] | 0.771 [0.758, 0.783] | **0.968 [0.958, 0.975]** |
| syft (with tests) | 0.950 [0.945, 0.955] | 0.864 [0.857, 0.871] | 0.673 [0.665, 0.682] | 0.802 [0.793, 0.811] | **0.966 [0.957, 0.973]** |
| zod (no tests) | 0.992 [0.984, 0.996] | 0.729 [0.694, 0.762] | 0.987 [0.977, 0.992] | 0.825 [0.783, 0.860] | 0.932 [0.914, 0.947] |
| hono (no tests) | 0.977 [0.961, 0.987] | 0.805 [0.771, 0.835] | 0.949 [0.926, 0.965] | 0.980 [0.963, 0.989] | 0.966 [0.947, 0.979] |

**Read this table with the next one open, or it will mislead you.** Two arms
score above us here, and both do it by drawing far less of the graph. On cobra,
code-review-graph is the most precise arm on the page at 0.997, from **360 edges
against our 1,455**, and it recovers 17% of the call graph where we recover 68%.
On gitleaks, Graphify takes both cells at 0.995 and above, from a graph that
finds 89% of the calls where we find 95%. That is the narrowest of these trades
and the one worth taking seriously: Graphify is not a vacuous arm on gitleaks.

Precision on its own is as gameable as coverage on its own, in the opposite
direction. A tool that resolves one call and gets it right scores 1.000. That is
why this experiment has never reported precision without recall beside it, and
why the claim below is a statement about the pair.

### The claim that survives all five arms

**In all seven cells, no arm that recovers at least as much of the call graph as
we do is more precise than we are.**

That is the whole finding in one line, and it is not tunable: it names no
threshold, and adding an arm can only break it, never help it. Two arms were
added to this experiment after the claim was first written, and it held.

The weaker readings, stated so nobody has to infer them:

* **Most precise arm outright: one cell of seven** (zod), tied for most precise
  in one more (hono). Against the two arms this experiment started with,
  CodeGraph and codebase-memory-mcp, we are the most precise in seven of seven,
  and that is the narrower claim it should always be labelled as.
* **Beaten on precision in five cells**, by code-review-graph on cobra and both
  syft cells and by Graphify on both gitleaks cells. Each of those is a real
  result and each comes with a recall column that is worse than ours.
* **On syft, the largest cell here, we are the most precise arm that recovers
  more than a quarter of the graph.** The arm above us recovers a fifth.

Arm versions: CodeGraph 1.5.0, codebase-memory-mcp 0.10.8, Graphify 0.9.31,
code-review-graph 2.3.7.

## Recall, which runs the other way

Of the edges the oracle has, the share the tool found.

| cell | repowise | CodeGraph | codebase-memory-mcp | Graphify | code-review-graph |
|---|---|---|---|---|---|
| cobra (with tests) | 0.684 [0.664, 0.704] | 0.763 [0.745, 0.781] | 0.743 [0.724, 0.761] | 0.433 [0.412, 0.455] | 0.174 [0.158, 0.191] |
| gitleaks (no tests) | 0.955 [0.943, 0.964] | 0.920 [0.906, 0.933] | 0.967 [0.957, 0.975] | 0.886 [0.870, 0.901] | 0.026 [0.019, 0.035] |
| gitleaks (with tests) | 0.914 [0.900, 0.926] | 0.895 [0.880, 0.909] | **0.945 [0.933, 0.954]** | 0.832 [0.814, 0.849] | 0.032 [0.025, 0.041] |
| syft (no tests) | 0.513 [0.502, 0.524] | 0.508 [0.497, 0.519] | **0.542 [0.531, 0.553]** | 0.447 [0.436, 0.458] | 0.201 [0.192, 0.210] |
| syft (with tests) | 0.322 [0.316, 0.328] | 0.338 [0.332, 0.344] | **0.361 [0.355, 0.367]** | 0.273 [0.267, 0.279] | 0.086 [0.082, 0.089] |
| zod (no tests) | 0.703 [0.677, 0.727] | 0.373 [0.347, 0.401] | 0.694 [0.668, 0.719] | 0.248 [0.225, 0.273] | 0.652 [0.626, 0.678] |
| hono (no tests) | 0.731 [0.697, 0.762] | 0.684 [0.649, 0.717] | 0.686 [0.650, 0.719] | 0.688 [0.653, 0.722] | 0.691 [0.656, 0.724] |

**We lead recall in the two TypeScript cells and in none of the five Go cells**,
where codebase-memory-mcp leads four and CodeGraph the fifth. Against the two
arms added here we lead recall in seven of seven, by margins that are the whole
explanation for their precision scores.

**code-review-graph is the clearest case of what recall buys.** It stores
unresolved callees beside resolved ones; on gitleaks, 4,367 `CALLS` rows reduce
to **76** whose callee resolves to a node in its own database. Scoring only the
resolved rows is the fair reading and is what its own adapter does, but it means
its precision figure is computed over 2.6% of the call graph. An arm that
resolves almost nothing cannot be caught being wrong, which is the same reason
[G5](../g5-invariance/) refuses to score a vacuous arm as passing a mutation.

**Do not compare recall across rows.** It swings from 0.03 to 0.97, and that is
driven by how many entry points RTA had (4 on gitleaks, 268 on syft-with-tests),
not by tool quality. A larger oracle mechanically lowers every arm's recall.
Only within-row comparisons carry meaning, and a pooled recall over these cells
would be meaningless.

### What the oracle analysed

| cell | files | oracle edges | functions judged | unjudged share, per arm |
|---|---:|---:|---:|---|
| cobra (with tests) | 35 | 2059 | 584 | repowise 0%, CodeGraph 0%, codebase-memory-mcp 0%, Graphify 1%, code-review-graph 0% |
| gitleaks (no tests) | 184 | 1584 | 415 | repowise 3%, CodeGraph 3%, codebase-memory-mcp 3%, Graphify 1%, code-review-graph 13% |
| gitleaks (with tests) | 203 | 1747 | 480 | repowise 2%, CodeGraph 1%, codebase-memory-mcp 1%, Graphify 1%, code-review-graph 4% |
| syft (no tests) | 663 | 7898 | 2979 | repowise 6%, CodeGraph 11%, codebase-memory-mcp 6%, Graphify 6%, code-review-graph 6% |
| syft (with tests) | 1107 | 22590 | 4632 | repowise 2%, CodeGraph 5%, codebase-memory-mcp 3%, Graphify 2%, code-review-graph 3% |
| zod (no tests) | 116 | 1269 | 753 | repowise 46%, CodeGraph 34%, codebase-memory-mcp 35%, Graphify 10%, code-review-graph 29% |
| hono (no tests) | 185 | 706 | 480 | repowise 16%, CodeGraph 37%, codebase-memory-mcp 32%, Graphify 9%, code-review-graph 26% |

## What the two tables say together

**The five arms fall either side of us, and neither side is where you want to
be.**

*Above us on recall, below us on precision.* codebase-memory-mcp recovers more
of the true call graph than anyone and emits far more that is not in it: on
syft, more than a third of what it draws is a call the compiler denies. That is
one trade seen from two directions, and neither number states it alone. It is
also the explanation for [G2's coverage result](../../README.md), where that
tool leads us on 15 of 35 repositories and we lead on none. Coverage rewards
drawing edges and never asks whether they are real.

*Above us on precision, below us on recall.* Graphify and code-review-graph make
the opposite trade. code-review-graph is the extreme case: 0.997 precision on
cobra from a graph containing 17% of the calls, and on gitleaks a precision
figure computed over 76 resolved edges out of 4,367 rows it stored. A graph that
small is very hard to be wrong in and not much use to walk.

**So there are two ways to win one of these columns and neither is worth
having.** Draw everything and be wrong a third of the time, or draw a tenth of
it and be right. The only reading that means anything is the pair, and on the
pair no arm here beats us in any cell.

This is why no page here publishes a coverage number without a precision number
beside it, and why this page has never published precision without recall.

## Where our own recall goes

Recall is the column we lose, so it gets a decomposition rather than a
disclaimer. On syft-no-tests we miss 3,846 of 7,898 oracle edges. Two properties
the oracle records directly, dynamic dispatch and a `func` literal at either
endpoint, account for most of it:

| bucket | share of the miss |
|---|---|
| dynamic dispatch only | 1,691 (44.0%) |
| dispatch **and** a closure endpoint | 1,511 (39.3%) |
| closure endpoint only | 80 (2.1%) |
| neither | 564 (14.7%) |

**The buckets overlap and neither may be quoted as the whole gap.** Adding the
first two is how an earlier reading of this data reached "dispatch is 79% of the
miss".

The overlap is the useful part. Closures look like a large hole, 1,309 missed
edges have a literal as the callee, and the obvious fix is to give a `func`
literal a symbol. Filter for the calls that fix alone would recover, that is the
static ones, and the number is **50**. The other 1,259 are dynamic dispatches
that would still need the dispatch ceiling cleared. On gitleaks the same figure
is 8 of 72.

Interface dispatch is that ceiling and nobody in this comparison has cleared it:
of 3,303 dispatch edges on syft we match 12, CodeGraph 35, codebase-memory-mcp
81. Fan-out is 6.5 distinct targets per site on syft and 12.1 on syft-with-tests.
Matching RTA's recall there means adopting RTA's over-approximation, which is
the behaviour the precision table charges codebase-memory-mcp for.

```bash
python decompose_miss.py --oracle syft-notests.jsonl --repo <path-to-syft>     --repo-name syft --out syft-notests-miss.json
```

## The method, and the one idea that makes it work

**Key: `(caller_decl_file, caller_decl_line) -> (callee_decl_file,
callee_decl_line)`.** Function granularity, declaration locations only.

**A caller is keyed at the outermost function the call is written inside.** A
`func` literal is a function to the compiler and gets its own SSA node, but no
arm in this comparison stores a symbol for one: all three attribute a call made
inside a closure to the function the closure is written in. Keying the caller at
the literal would therefore mark the edge wrong for every arm at once, which
measures the oracle's key rather than any resolver. The TypeScript oracle
[found this first](TYPESCRIPT.md), where correcting it moved every arm by more
than twenty points; the Go oracle now uses the same rule. The callee side stays
at its own declaration, because a closure that is genuinely called is a real
target the arms do not carry and hiding it would turn a measured recall gap into
a silent one.

**No name is ever compared.** A name-matched join is the failure this experiment
exists to remove, and it would quietly favour whichever tool spells identifiers
most like the oracle.

**Call-site granularity is deliberately not used.** codebase-memory-mcp records
no line for a call site, storing the calling function's declaration line
instead. A site-keyed join would zero that arm out for a reason about its
storage rather than its resolver.

### Splitting "outside the oracle", which is what makes precision automatable

A static oracle over-approximates, so an edge outside its set is *probably*
wrong but not certainly. The naive reading would need hand-grading. It does not,
because **RTA records which functions it reached, and it analyses every call
site inside a function it reaches.** So:

| bucket | condition | meaning |
|---|---|---|
| **matched** | edge is in the oracle | confirmed |
| **contradicted** | caller reachable, edge absent | the oracle **denies** this call |
| **unjudged** | caller never reached | the oracle cannot speak |

`precision = matched / (matched + contradicted)`. The unjudged bucket is
reported at full size and charged to nobody.

One refinement follows from the caller key. RTA reaches a `func` literal only if
something calls it, so a callback it never sees invoked is analysed nowhere. A
function is therefore judgeable only if RTA reached it **and** reached every
closure written inside it; otherwise it is withheld and its edges fall to
unjudged. Without that, an arm would be contradicted over the oracle's own blind
spot. On the cells here the withheld count is nil to small, so the correction
costs almost nothing and removes the objection entirely.

The unjudged share is small in every cell, 0.4% to 11.1% and mostly under 6%, so
these rates cover nearly all of each tool's output rather than leaving a large
unknown.

**No F1 is reported and none should be.** Combining a real recall with a
precision that carries an unjudged remainder would launder that uncertainty into
a number that looks decisive.

### Validation

The preregistration names the identity mapping as where this breaks. **The modal
declaration-line offset is `(0,0)` for all five arms**, at 1,512 / 1,458 / 1,532
/ 1,404 / 41 exact matches on gitleaks. A broken mapping cannot produce that,
and the two arms added last reproduced it without any adjustment, which is the
cheapest evidence available that their readers were not written to flatter
them.

That is necessary and not sufficient, and this experiment has the receipt for
why: a modal offset is computed over matched edges, and matched edges are by
construction the ones that agree, so a defect touching only unmatched edges is
invisible to it. The caller-granularity defect corrected above was exactly that
shape. It was caught by reading source, not by any aggregate.

Protocol step 2 also asks for **20 randomly drawn identities confirmed by
hand**, because the offset distribution is a fact about a join that already
happened: it says the two sides agree with each other, not that either is right.
That check is done, and was taken again after the caller key changed, in
[`identity-validation.md`](identity-validation.md). **20 of 20 declaration
positions are correct**, and a second draw of five whole edges confirms the
direction of the join against source. Two rows are worth reading rather than
counting, both function literals in the callee role: real targets that no arm
models as a symbol, which cost all three arms recall equally and can never
produce a contradicted edge for anyone.

## The result that matters most is not competitive

**The oracle independently reproduced the hand-graded audit.**

[G1](../g1-edge-precision/) graded Go by hand at 30 rows per side: repowise
29/30 = 96.7%, CodeGraph 29/30 = 96.7%. The Go compiler, over roughly 1,600
edges on the same repository, says **97.6% and 97.2%**.

Two unrelated methods, one person reading source and one type checker, agree to
within about a point on both arms. That is evidence the 540-row hand-graded
audit is accurate rather than self-serving, which is worth more than any row
above.

## TypeScript

A second language has an oracle: the `tsc` type checker, on zod and hono.
Written up, with the two defects the identity validation caught before any rate
was quoted, in [`TYPESCRIPT.md`](TYPESCRIPT.md).

**zod is the one cell where we are the best arm on both columns at once**, the
most precise and the most complete, which happens nowhere else on this page. On
hono we have the highest recall and tie Graphify for the highest precision.

Read the two languages together, because the arms trade places between them.
codebase-memory-mcp is the least precise arm in all five Go cells and our near
equal in both TypeScript cells. Graphify is the most precise arm on gitleaks and
the second least precise on zod. CodeGraph is within a point of us on gitleaks
and 26 points behind on zod. **No arm here is uniformly good, which is the
argument for reading a whole table rather than a headline.**

## Limits, stated plainly

* **Go and TypeScript only**, five Go cells over three repositories and two
  TypeScript cells over two. This is not a nine-language claim and must not be
  quoted as one.
* **"Most precise" is a claim about a pair of columns, not one.** Two arms beat
  us on precision in five of seven cells and both are behind us on recall in
  every cell. Any quotation of a precision figure from this page that does not
  carry the recall beside it is a misuse of it, including by us.
* **A library has no `main`, so RTA has no roots.** cobra can only be analysed
  through its test binaries, which is why it has no "no tests" cell. This is a
  limit of oracle-anchoring, not a property of cobra.
* **`contradicted` is very strong evidence, not proof.** RTA is unsound under
  reflection and `go:linkname`, so a genuinely dynamic edge can land in that
  bucket. This applies to all five arms equally and the gaps here are far too
  large to be explained by it, which is why the metric is named
  *precision against the oracle* rather than precision.
* **syft-with-tests carries 3 package load errors**, so that cell is marginally
  partial.
* The two variants of a repository answer different questions. Report both or
  say which one you used.

## Reproduce

```bash
cd graph/experiments/g4-oracle-anchored
(cd oracle && go build -o oracle.exe .)
./oracle/oracle.exe -repo <path-to-repo> [-tests] -out cell.jsonl
python compare.py --oracle cell.jsonl --repo <path-to-repo> --out cell-g4.json
python render_g4.py <cell>-g4.json ...   # every table on this page
```

```bash
python validate_identities.py --oracle cell.jsonl --repo <path-to-repo> --out draw.json
python render_identity_validation.py --graded draw.json
```

`-tests` includes `_test.go` in the analysed set; without it a library yields no
roots. The oracle records its algorithm, roots, load errors and analysed file
set in a header object, so a rate can be reconciled against the exact answer key
that produced it.

Both sides are restricted to the file set the oracle actually type-checked
before anything is counted, so an oracle that skipped code cannot charge a tool
for edges inside it.

## Predictions, graded

The preregistration was written before any run. Grading it honestly is part of
the design.

**"Recall against RTA: ours 0.45 to 0.60, peer 0.40 to 0.55."** Partially right,
for a reason it did not anticipate: recall depends so heavily on root count that
a single predicted band cannot hold across cells. Ours ranges 0.32 to 0.96. On
syft-no-tests, the cell closest in spirit to what was predicted, ours is 0.513
and CodeGraph 0.508. Both sit inside the predicted bands, and the cell is a tie,
which was also predicted.

**"The outside-oracle bucket will be large on both sides, at least 25% of
emitted edges. If the bucket is under 10%, the normalisation in step 2 is wrong
and the run is void."** **Missed.** Actual buckets span 3.2% to 40.3%. The
cobra and gitleaks cells fall under the void threshold.

The mechanism is visible and is not a broken normalisation. The prediction
expected a large bucket because RTA has no entry points for test-only code. But
the analysed file set excludes `_test.go` under the default variant, and step 3
then restricts **both sides** to that set. The population expected to inflate
the bucket was scoped out of both columns before counting. On syft, where much
code is genuinely unreachable from 30 entry points, the bucket runs to 40.3% for
the loosest arm, in line with the prediction.

So the rule correctly flags the tight cells as too tight to lean on, and does
not void the experiment. The syft cells carry the weight.

**"Step 2 fails at least once."** **Hit, on the second language, and the first
write-up of this page got it wrong.** It was first graded as missed, on the
grounds that the identity mapping worked immediately at `(0,0)` modal offset and
the hand check found 20 correct positions out of 20. Both of those statements
were true and the grade was still wrong: the caller was being keyed at a `func`
literal, which no arm symbolises, and neither the offset nor a draw dominated by
top-level functions could see it. The TypeScript oracle surfaced it, this one
inherited the same defect, and every Go rate on this page has been recomputed
since. That is step 2 doing precisely what the preregistration expected of it.

Two further failures were of the reading-your-own-output kind: an early run
conflated "no call site" with "call site outside the repository" in one counter,
and `packages.NeedCompiledGoFiles` turned out to be a separate mode bit from
`NeedFiles`, so the analysed file set silently came back empty. Both were caught
before any rate was computed.
