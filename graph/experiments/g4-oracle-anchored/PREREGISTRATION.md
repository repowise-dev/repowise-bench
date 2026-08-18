# G4: precision and recall against an oracle neither tool produced

**Written before any G4 run. Predictions below are graded honestly afterwards,
including the ones that miss.**

## Why this experiment exists

Every graph-quality number anyone in this field publishes, including ours, is
scored against something the publisher controls. Ours is hand-graded by us.
code-review-graph's F1 of 0.69 is scored against its own graph. CodeGraph's
coverage table is computed from its own index. All three have the same defect: a
tool that is confidently wrong in a consistent way scores well.

G4 removes that by grading both tools against a gold call graph produced by a
third party that has no stake in the comparison, is not a graph-for-agents
product, and in most cases is the language's own toolchain.

It also removes the n=30 ceiling. Hand-grading caps a precision estimate at about
±16 points near a 60% rate. An oracle grades every edge, so n becomes the size of
the repository.

## Oracles, in descending order of trust

| language | oracle | what it is | what it is not |
|---|---|---|---|
| Go | `golang.org/x/tools/cmd/callgraph`, RTA | the Go team's own analysis, running on the type-checked program | not sound under reflection or `go:linkname` |
| Python | [PyCG](https://github.com/vitali87/pycg) micro-benchmark, 112 hand-written cases, MIT | published gold graphs from an ICSE 2021 paper, per language feature | tiny programs, not a real repository |
| Java | [CATS](https://bitbucket.org/delors/cats) via the Judge toolchain | targeted programs exercising reflection, invokedynamic, serialization | a soundness probe, not a corpus |
| TypeScript | `scip-typescript`, references folded caller to callee | `tsc`'s own resolver, so the type information is real | SCIP has no call-edge model; the fold is ours and must be validated |

The trust ordering matters more than the coverage. Go RTA on a real repository
(gitleaks) is the strongest cell here and it is the one to build first.

## The asymmetry that makes this honest

**A static oracle over-approximates.** RTA includes edges that can never fire at
runtime. So an edge we emit that the oracle lacks is *probably* wrong, and an
edge the oracle has that we lack is *probably* a miss, but neither is certain.

Therefore G4 reports two numbers per cell and never one:

* **Recall against the oracle** is close to a true recall. If RTA found it and we
  did not, we missed it, with the caveat that RTA's own unsoundness means the
  denominator is a lower bound.
* **Precision against the oracle** is an *upper bound on wrongness*, not
  precision. An edge outside the oracle's set goes into a bucket, and a sample of
  that bucket is hand-graded to convert it into a rate.

A single F1 over the two would be dishonest and will not be reported.

## Protocol

1. Build the oracle graph. Record the tool, its version, its algorithm and its
   flags. Store the raw output.
2. Normalise both sides to `(caller_symbol, callee_symbol)` at whatever
   granularity the oracle supports. This mapping is the experiment's single
   largest risk and gets its own validation step: 20 randomly drawn identities
   are confirmed by hand before any rate is computed.
3. Restrict both tools to the file set the oracle actually analysed. An oracle
   that skips vendored code must not be allowed to charge us for edges in it.
4. Compute recall, and the outside-oracle bucket, per tool.
5. Draw 30 from each tool's outside-oracle bucket, seeded, and grade by hand from
   source using the G1 rules.
6. Publish the bucket sizes alongside the graded rates, so a reader can see how
   much of each tool's output the oracle simply cannot speak to.

## Predictions

Written now, graded later.

* **Go / gitleaks.** Recall against RTA: ours 0.45 to 0.60, peer 0.40 to 0.55.
  The two are a statistical tie on hand-graded precision at 29/30 each, so a
  large recall gap either way would be a surprise worth investigating rather than
  a result to publish immediately.
* **The outside-oracle bucket will be large on both sides**, at least 25% of
  emitted edges, because RTA will not have entry points for test-only code.
  If the bucket is under 10%, the normalisation in step 2 is wrong and the run is
  void.
* **Step 2 fails at least once.** The identity mapping between a `callgraph`
  node and one of our symbol ids is where this experiment breaks, not the
  statistics. Budgeting a session for it alone.
* **The TypeScript cell is the least likely to survive.** SCIP has no call model,
  so the fold from references to call edges is our own construction, which makes
  it an instrument we would be grading ourselves against. If the 20-identity
  validation does not come back clean, the cell is dropped and that is reported.

## What would make us abandon G4

If the outside-oracle bucket cannot be brought under about half of emitted edges
on the Go cell, the oracle is not covering enough of the program to grade
against, and G4 becomes a recall-only experiment. That is a worse experiment but
still a real one, and it gets published as recall-only rather than quietly
rescoped.
