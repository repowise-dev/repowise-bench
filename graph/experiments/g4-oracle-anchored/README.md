# G4: precision and recall against an oracle neither tool produced

**Status: measured on Go, three repositories, five cells, three arms.**
Predictions were written in [`PREREGISTRATION.md`](PREREGISTRATION.md) before any
run and are graded at the bottom of this page, including the one that missed.

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

## Headline: precision

Of the call edges a tool emits, the share the compiler confirms.

| cell | repowise | CodeGraph 1.5.0 | codebase-memory-mcp 0.10.8 |
|---|---:|---:|---:|
| cobra (via tests) | **0.890** [0.872, 0.905] | 0.852 [0.834, 0.868] | 0.834 [0.815, 0.851] |
| gitleaks (no tests) | **0.965** [0.954, 0.973] | 0.958 [0.947, 0.967] | 0.927 [0.913, 0.939] |
| gitleaks (with tests) | **0.936** [0.923, 0.946] | 0.920 [0.905, 0.932] | 0.880 [0.865, 0.895] |
| syft (no tests) | **0.895** [0.885, 0.904] | 0.831 [0.820, 0.842] | 0.603 [0.592, 0.615] |
| syft (with tests) | **0.752** [0.742, 0.761] | 0.680 [0.670, 0.690] | 0.524 [0.515, 0.533] |

**We are the most precise arm in all five cells.** Against
codebase-memory-mcp: five separations, no ties, no losses. Against CodeGraph:
three separations and two ties (both gitleaks cells), no losses.

On syft, roughly half of what codebase-memory-mcp emits is a call the Go
compiler says does not exist.

## Recall, which runs the other way

Of the edges the oracle has, the share the tool found.

| cell | oracle edges | repowise | CodeGraph | codebase-memory-mcp |
|---|---:|---:|---:|---:|
| cobra (via tests) | 2,150 | 0.600 | **0.670** | 0.650 |
| gitleaks (no tests) | 1,593 | 0.939 | 0.902 | **0.954** |
| gitleaks (with tests) | 1,778 | 0.866 | 0.835 | **0.886** |
| syft (no tests) | 8,003 | 0.480 | 0.477 | **0.508** |
| syft (with tests) | 23,117 | 0.249 | 0.260 | **0.274** |

codebase-memory-mcp has the highest recall in four of five cells and CodeGraph
in the fifth. **We lead in none.**

**Do not compare recall across rows.** It swings from 0.25 to 0.95, and that is
driven by how many entry points RTA had (4 on gitleaks, 268 on syft-with-tests),
not by tool quality. A larger oracle mechanically lowers every arm's recall.
Only within-row comparisons carry meaning, and a pooled recall over these cells
would be meaningless.

## What the two tables say together

**codebase-memory-mcp recovers more of the true call graph and emits far more
that is not in it.** That is one trade, visible from two directions, and neither
number states it alone. It is also the explanation for
[G2's coverage result](../../README.md), where that tool leads us on 15 of 35
repositories and we lead on none: coverage rewards drawing edges, and does not
ask whether they are real.

This is why no page here publishes a coverage number without a precision number
beside it.

## The method, and the one idea that makes it work

**Key: `(caller_decl_file, caller_decl_line) -> (callee_decl_file,
callee_decl_line)`.** Function granularity, declaration locations only.

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

The unjudged share is small in every cell, 0.4% to 11.1% and mostly under 6%, so
these rates cover nearly all of each tool's output rather than leaving a large
unknown.

**No F1 is reported and none should be.** Combining a real recall with a
precision that carries an unjudged remainder would launder that uncertainty into
a number that looks decisive.

### Validation

The preregistration names the identity mapping as where this breaks. It did not
break here: **the modal declaration-line offset is `(0,0)` for all three arms**,
at 1,495 / 1,437 / 1,520 exact matches on gitleaks. A broken mapping cannot
produce that.

Protocol step 2 also asks for **20 randomly drawn identities confirmed by
hand**, because the offset distribution is a fact about a join that already
happened: it says the two sides agree with each other, not that either is right.
That check is now done and is written up in
[`identity-validation.md`](identity-validation.md). **20 of 20 declaration
positions are correct**, and a second draw of five whole edges confirms the
direction of the join against source. Two rows are worth reading rather than
counting: an immediately invoked function literal that no arm models as a
symbol, and a `func init()` that codebase-memory-mcp does not store.

## The result that matters most is not competitive

**The oracle independently reproduced the hand-graded audit.**

[G1](../g1-edge-precision/) graded Go by hand at 30 rows per side: repowise
29/30 = 96.7%, CodeGraph 29/30 = 96.7%. The Go compiler, over roughly 1,600
edges on the same repository, says **96.5% and 95.8%**.

Two unrelated methods, one person reading source and one type checker, agree to
within about a point on both arms. That is evidence the 540-row hand-graded
audit is accurate rather than self-serving, which is worth more than any row
above.

## Limits, stated plainly

* **Go only.** Three repositories. This is not a nine-language claim and must
  not be quoted as one.
* **A library has no `main`, so RTA has no roots.** cobra can only be analysed
  through its test binaries, which is why it has no "no tests" cell. This is a
  limit of oracle-anchoring, not a property of cobra.
* **`contradicted` is very strong evidence, not proof.** RTA is unsound under
  reflection and `go:linkname`, so a genuinely dynamic edge can land in that
  bucket. This applies to all three arms equally and the gaps here are far too
  large to be explained by it, which is why the metric is named
  *precision against the oracle* rather than precision.
* **syft-with-tests carries 3 package load errors**, so that cell is marginally
  partial.
* The two variants of a repository answer different questions. Report both or
  say which one you used.

## Reproduce

```bash
cd graph/experiments/g4-oracle-anchored
go build -o oracle/oracle.exe ./oracle/
./oracle/oracle.exe -repo <path-to-repo> [-tests] -out cell.jsonl
python compare.py --oracle cell.jsonl --repo <path-to-repo> --out cell-g4.json
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
a single predicted band cannot hold across cells. Ours ranges 0.25 to 0.94. On
syft-no-tests, the cell closest in spirit to what was predicted, ours is 0.480
and CodeGraph 0.477. Both sit inside or adjacent to the predicted bands, and the
cell is a tie, which was also predicted.

**"The outside-oracle bucket will be large on both sides, at least 25% of
emitted edges. If the bucket is under 10%, the normalisation in step 2 is wrong
and the run is void."** **Missed.** Actual buckets span 6.2% to 49.4%. The
gitleaks cells fall under the void threshold.

The mechanism is visible and is not a broken normalisation. The prediction
expected a large bucket because RTA has no entry points for test-only code. But
the analysed file set excludes `_test.go` under the default variant, and step 3
then restricts **both sides** to that set. The population expected to inflate
the bucket was scoped out of both columns before counting. On syft, where much
code is genuinely unreachable from 30 entry points, the bucket is 15.9% to
43.3%, in line with the prediction.

So the rule correctly flags the gitleaks cells as too tight to lean on, and does
not void the experiment. The syft cells carry the weight.

**"Step 2 fails at least once."** **Missed, and this is the good kind.** The
identity mapping worked on the first attempt, at `(0,0)` modal offset across all
three arms, and the hand check that followed found 20 correct positions out
of 20. What failed instead was reading the oracle's own output: an early
run conflated "no call site" with "call site outside the repository" in one
counter, and `packages.NeedCompiledGoFiles` turned out to be a separate mode bit
from `NeedFiles`, so the analysed file set silently came back empty. Both were
caught before any rate was computed.
