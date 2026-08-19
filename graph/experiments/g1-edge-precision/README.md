# G1: edge precision

Of the call edges a tool emits, what share are real? Read from source, by hand,
on both sides, by the same method.

**Status: measured. Nine languages, 270 graded rows per side, 540 in total.**

## Why this experiment exists

Every other reading in this benchmark counts edges or counts the files edges
reach. None of them asks whether an edge is *true*. A resolver that guesses
aggressively wins a coverage table and a raw edge count while producing a graph
that sends its reader to the wrong function, and no amount of counting will
distinguish it from one that resolves carefully.

Precision is the only reading here that can, and it is the only one that cannot
be automated: each row is a call site opened in its own file, with its imports
and its enclosing scope, and a target declaration opened in the file the tool
claims it landed in. A verdict inferred from names is worthless, and avoiding
that was the whole point.

## Headline

30 rows per side per language, seed 2026, stratified by resolution strategy in
proportion to population.

| | correct / n | 95% CI |
|---|---|---|
| **repowise** | **229/270 = 84.8%** | [80.0, 88.6] |
| **CodeGraph 1.5.0** | **154/270 = 57.0%** | [51.1, 62.8] |

The intervals are disjoint. Both sides have now been read on all nine languages
by the same method, at the same seed, on the same repositories. No cell in
either column is unmatched in the other.

**Roughly fifteen percent of our call edges are wrong.** That is the honest
reading of 84.8%, and it is the number to plan against rather than the gap.

## Per language, both sides

| language | repositories | repowise | 95% CI | CodeGraph | 95% CI | verdict |
|---|---|---|---|---|---|---|
| typescript | zod | 29/30 = 96.7% | [83.3, 99.4] | 7/30 = 23.3% | [11.8, 40.9] | **separates** |
| go | gitleaks | 29/30 = 96.7% | [83.3, 99.4] | 29/30 = 96.7% | [83.3, 99.4] | tie |
| csharp | Ocelot | 28/30 = 93.3% | [78.7, 98.2] | 20/30 = 66.7% | [48.8, 80.8] | **separates** |
| python | celery | 28/30 = 93.3% | [78.7, 98.2] | 19/30 = 63.3% | [45.5, 78.1] | **separates** |
| kotlin | javalin, ktor, exposed | 27/30 = 90.0% | [74.4, 96.5] | 13/30 = 43.3% | [27.4, 60.8] | **separates** |
| swift | Alamofire *(n=1 repo)* | 23/30 = 76.7% | [59.1, 88.2] | 19/30 = 63.3% | [45.5, 78.1] | tie |
| cpp | fmt, Crow, leveldb, seastar, aria2 | 23/30 = 76.7% | [59.1, 88.2] | 16/30 = 53.3% | [36.1, 69.8] | tie |
| rust | ripgrep, serde, bevy | 22/30 = 73.3% | [55.6, 85.8] | 13/30 = 43.3% | [27.4, 60.8] | tie |
| java | caffeine | 20/30 = 66.7% | [48.8, 80.8] | 18/30 = 60.0% | [42.3, 75.4] | tie |
| **pooled** | | **229/270 = 84.8%** | [80.0, 88.6] | **154/270 = 57.0%** | [51.1, 62.8] | **separates** |

**Four of nine separate. Five are ties and are reported as ties.** At n=30 the
interval is about ±16 points near a rate of 60% and about ±8 near 95%; a
point-estimate gap that sits inside two overlapping intervals is not a win, and
C++ in particular is a **tie** despite a 23-point point-estimate gap. The pooled
row is nine repository sets rather than a random sample of software.

## Per repository, which is where the spread lives

A language is not a repository. Every multi-repository cell is shown split,
because a pooled cell can hide a repository the pooled number would not predict.

| language | split (repowise) | split (CodeGraph) |
|---|---|---|
| kotlin | javalin 10/10, ktor 8/10, exposed 9/10 | javalin 2/10, ktor 4/10, exposed 7/10 |
| rust | ripgrep 7/10, serde 6/10, bevy 7/10 *(see note)* | ripgrep 5/10, serde 3/10, bevy 5/10 |
| cpp *(n=10 depth)* | fmt 9/10, Crow 7/10, leveldb 9/10, **seastar 4/10**, aria2 10/10 | fmt 4/10, Crow 3/10, leveldb 4/10, **seastar 6/10**, aria2 10/10 |

### seastar is the cell we lose, and it is the interesting one

**seastar is the single repository in this entire audit, on any language, where
CodeGraph beats us on a clear margin: 6/10 against our 4/10.** It is also the
repository whose C++ file-extension registration shipped knowingly below the bar
we hold other language work to.

Our seastar failures are dominated by one shape the peer does not have at all: a
chained call on an untyped receiver, `something(...).get()` on a `future<T>`,
bound to an unrelated `get`. CodeGraph infers the callee's declared return
type and **validates** the method against it, so a failed inference costs it an
edge rather than buying it a wrong one. That is a better trade than ours on this
shape, and it is worth saying plainly.

`aria2` is the other row worth more than the cell: 10/10 against 10/10, and they
resolve **24,950 distinct call edges to our 9,486**: same precision, 2.6x the
edges, on a quarter of the corpus's C++ files. Precision is not the only reading
and this row is where that shows.

### Note on the rust split

The rust cell moved from 20/30 to 22/30 when a change stopped the bare-name tier
answering for the standard library (#1708). **That re-read was a fresh draw, not
a re-grade of the same rows**, so the per-repository split above is from the
earlier 20/30 draw and does not sum to the current cell. It is shown because the
spread is still informative; it is labelled because a split that does not sum to
its own headline is exactly the kind of thing a reader should be told rather
than left to discover.

## What each side gets wrong

The two failure distributions are not the same shape, which matters more than
the gap between the rates.

### CodeGraph: one mechanism, repeatedly

Bare-name fallback into a same-named declaration the call site cannot reach.
It accounts for the large majority of its wrong rows across zod, Ocelot, celery,
ktor, ripgrep and the C++ set, and shows up as two buckets: cross-module
same-name collision, and wrong class with the same method name.

Its worst cell, javalin at 2/10, is a Kotlin web framework whose tests statically
import a router `get`, call the router's `get(path) { handler }`, and also hold a
test HTTP client with a `get(path)` of its own.

### repowise: the same mechanism, plus one they do not have

By resolution origin, across the 120 rows of the four-language extension:

| origin | wrong rows |
|---|---:|
| `global_unique` | 13 |
| `import_merged` | 5 |
| `same_file` | 4 |
| `receiver_typed_global` | 1 |
| `same_package` | 1 |

Locally-grounded origins are nearly clean; the bare-name `global_unique` tier
carries the audit. That is the same mechanism as the peer's, and we are not
exempt from it.

The bucket that is ours alone is the **chained call on an untyped receiver**
described under seastar above. Counted over the n=50 C++ depth read, where we
take 11 wrong rows and they take 20, that shape is **3 of ours against 0 of
theirs**, the only bucket in the audit that is one-sided in their favour.

Two residual classes are named rather than quietly counted as losses:

* **Rust macro invocations graded as calls**, 4 of the 8 remaining wrong rows
  in the rust cell. They bind to the repository's own `macro_rules!` definition,
  so the *target* is right and only the *type* is wrong. Removing them would
  delete a real dependency in order to raise a precision number, which is the
  wrong trade; reclassifying them off `calls` is a separate change.
* **Swift property and subscript reads graded as calls**. The declaration named
  is right, the claim that it is a call is not.

Both are counted as wrong in every figure on this page.

## Method, identical on both sides

**Denominator.** Distinct resolved call edges.

* *repowise*: resolver records folded to one row per distinct `(file, line,
  target)`, a call site as the source text spells it, not as the grammar query
  happens to match it.
* *CodeGraph*: `SELECT DISTINCT source, target, line FROM edges WHERE
  kind='calls'`, restricted to callers of the language under audit.

Raw and distinct counts are never mixed.

**Sample.** 30 rows per side per language, seed 2026, stratified by resolution
strategy in proportion to population, with largest-remainder allocation so the
quotas sum to exactly 30 and a seeded draw inside each stratum.

**Verdict.** Every row read from source: the call site with its enclosing scope
and the file's imports, then the target declaration. `correct` / `wrong` /
`ambiguous`, each with a one-line reason.

**C++ enters the pooled row at 30 rows even though 50 were graded.** The graded
sample is 50 per side; the pooled cell is a seeded 6-of-10-per-repository
subsample of it, so C++ carries the same weight as every other language rather
than nearly double. The 50-row read is reported above as the depth result and is
never added into the pooled figure.

## Provenance

There is no single commit for this table, so each cell carries its own. **No
cell here was measured on the 0.44.0 release**, and nothing on this page should
be quoted as a 0.44.0 figure.

| cells | repowise commit |
|---|---|
| go, typescript, csharp, python, java | `2017de7c` |
| kotlin, swift | `58576af0` (0.43.0+dev) |
| rust | `13cc339a` (re-read after #1708) |
| cpp, both sides | `13cc339a` (re-taken after #1700) |

CodeGraph is `@colbymchenry/codegraph@1.5.0`, extraction version 24, for every
cell.

**Cell staleness runs conservative, and this is worth stating precisely.** Every
resolver change landed between the earliest cell and `13cc339a` (#1690, #1692,
#1708) only *removes* wrong edges. Each was measured at the time: #1690 removed
16,122 edges and gained 0, #1692 removed 1,369 on one repository and 30 on
another and gained 0, #1708 removed only. None of them adds a call edge. So an
older cell can only understate our current precision, never overstate it: **84.8%
is a floor, not a flattered number.**

## What this does not claim

* Nine repository sets, several of them a single repository. Not a random sample
  of software. Swift is one repository and is labelled as such everywhere.
* n=30 per cell. Five of the nine cells are statistical ties and must not be
  reported as wins, including C++, whose intervals overlap at both n=30 and
  n=50.
* The peer's overload-level errors are graded `wrong` alongside its class-level
  errors. Grading them separately would move its Ocelot cell up by 3 rows and
  its caffeine cell by up to 4.
* Precision is not recall and not coverage. `aria2` is the standing reminder:
  identical precision, 2.6x the edges.
* **Kotlin receiver typing is still uncertified.** Exactly one row of the 30 in
  the Kotlin sample carried a receiver-typed origin. A stratified-by-population
  draw puts almost nothing in that stratum, so this audit cannot certify that
  mechanism; doing so needs a draw restricted to receiver-typed rows.

## Reproducing

The sampling harnesses are `sample_symmetric_ours.py` and
`sample_symmetric_peer.py`; the peer side reads the frozen indexes under
`test-repos/<repo>/.codegraph/codegraph.db`, which are read-only baselines and
are never regenerated in place.

**The 540 graded rows are in [`rows/`](rows/)**, one file per cell, each row
carrying its verdict and the reason written when it was read. `verify_rows.py`
rebuilds every table on this page from them and exits non-zero if it disagrees
with the headline; it agrees at 229/270 and 154/270.

```bash
python verify_rows.py            # every table above, recomputed from the rows
python verify_rows.py --rows go  # one language, both sides, with the reasons
```

One cell of the eighteen ships its draw without its grading. The rust cell for
our side was re-read on a fresh draw after #1708 and those per-row verdicts were
never written down, so `rust-ours.json` carries the 30 sites that were read with
`verdict: null` on each, and the cell total stands on the composition recorded at
the time rather than on rows. [`rows/README.md`](rows/README.md) says which eight
rows were wrong and why.
