# Methodology

Every rule here was paid for. Most of them exist because an earlier measurement
produced a number that looked fine and was not.

## The measurement rules

**1. Resolved edges is the result. A coverage percentage never is.** A change
that raises coverage and lowers precision is a regression wearing a win's
clothes. G2 exists to reproduce someone else's coverage metric, not to be scored
on.

**2. Fold to distinct `(file, line, target)` before comparing anything.** Capture
is not injective on either side. Our `java.scm` and `ruby.scm` mint a
receiver-less call site for every member call *in addition to* the member site,
so caffeine emits 110,740 call sites for 75,963 call expressions. CodeGraph
likewise emits 39,079 raw `calls` edges on caffeine against 38,609 distinct. A
comparison between one side's raw count and the other's distinct count is off by
whatever the author needed.

**3. Never mix the raw and distinct bases in one sentence.** Report both side by
side or commit to one. Every table in this directory labels which it is using.

**4. A sampled precision rate already includes the wrong-edge classes you
counted.** Subtracting a known-bad class *and* applying the sampled rate
double-counts. Pick one. Our audits use sampled rates alone.

**5. Both arms in one process, behind a toggle.** Never two checkouts. A second
checkout admits an unrelated variable, and it has already cost one session's
results.

**6. A verdict inferred from a name is worthless.** Precision rows are read from
source: the call site with its imports and enclosing scope, then the target
declaration. `ambiguous` is an honest verdict and some are expected.

**7. Predict before you measure**, from an instrument that shares no code with
the thing being measured. A prediction that lands within ~10% is what makes a
result trustworthy; one that misses by 3x means the mechanism is not understood
yet, whatever the measurement says.

**8. State recall on both denominators, including when it falls.**

**9. Refusing is a result.** A mechanism that was built, gated on 15
repositories and then refused is a finding, and it gets written up like one.

**10. Peer indexes under `test-repos/*/.codegraph/` are frozen.** Read-only, via
a `file:...?mode=ro` URI, always. Published baselines reconcile against those
exact bytes and a regeneration silently invalidates every prior number. New
indexes for new experiments are built in a scratch copy, never in place.

## Cost is measured on the graph, and only the graph

Our published indexing-time row in [docs/BENCHMARKS.md §6](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md)
compares a full `repowise` index, which also generates documentation, computes
health and builds embeddings, against tools that build a graph and stop. That is
the right comparison for "how long until I can use this", and the wrong one for
this directory.

**G6 times graph construction alone on both sides**: walk, parse, resolve, write
edges. No documentation, no embeddings, no health pass. We expect to remain
slower even so, and that row gets published at whatever it comes out at. A
benchmark that only reports the columns its author wins is not evidence.

## Traps that have already bitten

* **A dirty working tree.** One audit's first pass measured another session's
  uncommitted work and read zod 15% high. Check `git status`, prefer a detached
  worktree at a named commit.
* **The peer's `unresolved_refs` table is not calls-only.** It carries a
  `reference_kind` column with `references`, `imports`, `instantiates`, `extends`
  and `decorates`. Filtering to `'calls'` is the difference between a real recall
  figure and one that is roughly 5x too pessimistic.
* **The peer's `nodes.language` is the *caller's* language.** caffeine's index
  carries kotlin, python and c callers. Restrict deliberately, and say which way.
* **Repositories that look like a mis-resolution and are not.** gitleaks vendors
  its own `regexp` wrapper that its tests import instead of the stdlib. caffeine
  has a `guava/` compatibility subtree mirroring Guava names on purpose. zod has
  parallel v3/v4/mini trees plus a `zod4` alias to a published npm package. Read
  the imports before grading a row wrong.
* **The benchmark-tuning trap.** A mechanism can score well on a benchmark repo
  for a reason that will not reproduce for a user. The live example: inferring a
  decorator's type by reading the decorator's own declaration works on celery
  only because celery is both the framework and the repository under test. In an
  application that imports celery, that declaration is external and unreadable.
  Any framework-shaped result is run against repositories that *use* the
  framework without vendoring it before it is quoted.
* **Two heavy parses at once will exhaust memory** on the measurement machine.
  Parse serially.

## Known weaknesses we publish rather than wait to be caught on

* **Java is our least precise language and our largest edge count.** Roughly 67%
  edge precision on caffeine, against roughly 52,000 edges. The cause is known:
  our `java.scm` mints a receiver-less twin of every member call, which then
  resolves by bare name at roughly 60% precision. Removing it costs caffeine 35%
  of its resolved calls, so it stands. About 17,000 of caffeine's edges are
  suspect. This is the single largest correctness exposure in our graph and it is
  stated here rather than discovered by a reader.
* **Overload blindness on both sides.** Roughly a third of the wrong rows on
  each side are a call bound by name with no regard for argument type.
* **Chained receivers are unresolved everywhere.** The one wrong edge that both
  tools produce on gitleaks is the same shape.
* **Grading choice worth disclosing.** "Right class, wrong overload" is graded
  wrong alongside class-level errors. Grading it separately moves the peer's
  Ocelot and caffeine cells up by 3 and 4 rows. Stating the choice beats letting
  a reader find it.
