# G1: edge precision

Of the call edges a tool emits, what share are real? Read from source, by hand,
on both sides, by the same method.

**Status: measured. Nine languages, 280 graded rows per side, 560 in total.**

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
| **repowise** | **240/280 = 85.7%** | [81.1, 89.3] |
| **CodeGraph 1.5.0** | **164/280 = 58.6%** | [52.7, 64.2] |

The intervals are disjoint. Both sides have now been read on all nine languages
by the same method, at the same seed, on the same repositories. No cell in
either column is unmatched in the other.

**Roughly fifteen percent of our call edges are wrong.** That is the honest
reading of 85.7%, and it is the number to plan against rather than the gap.

## Per language, both sides

| language | repositories | repowise | 95% CI | CodeGraph | 95% CI | verdict |
|---|---|---|---|---|---|---|
| typescript | zod | 29/30 = 96.7% | [83.3, 99.4] | 7/30 = 23.3% | [11.8, 40.9] | **separates** |
| go | gitleaks | 29/30 = 96.7% | [83.3, 99.4] | 29/30 = 96.7% | [83.3, 99.4] | tie |
| csharp | Ocelot | 30/30 = 100.0% | [88.6, 100.0] | 20/30 = 66.7% | [48.8, 80.8] | **separates** |
| python | celery | 28/30 = 93.3% | [78.7, 98.2] | 19/30 = 63.3% | [45.5, 78.1] | **separates** |
| kotlin | javalin, ktor, exposed | 27/30 = 90.0% | [74.4, 96.5] | 13/30 = 43.3% | [27.4, 60.8] | **separates** |
| cpp | fmt, Crow, leveldb, seastar, aria2 | 22/30 = 73.3% | [55.6, 85.8] | 16/30 = 53.3% | [36.1, 69.8] | tie |
| swift | Alamofire *(n=1 repo)* | 23/30 = 76.7% | [59.1, 88.2] | 19/30 = 63.3% | [45.5, 78.1] | tie |
| rust | ripgrep, serde, bevy | 22/30 = 73.3% | [55.6, 85.8] | 13/30 = 43.3% | [27.4, 60.8] | tie |
| java *(n=40, two repositories)* | caffeine, spring-petclinic | 30/40 = 75.0% | [59.8, 85.8] | 28/40 = 70.0% | [54.6, 81.9] | tie |
| **pooled** | | **240/280 = 85.7%** | [81.1, 89.3] | **164/280 = 58.6%** | [52.7, 64.2] | **separates** |

**Java is the one cell read at 40 rows rather than 30**, because it was a single
repository and that repository turned out to be an outlier. See
[the java cell, widened](#the-java-cell-widened-and-what-it-answered) below.

**Four of nine separate. Five are ties and are reported as ties.** At n=30 the
interval is about ±16 points near a rate of 60% and about ±8 near 95%; a
point-estimate gap that sits inside two overlapping intervals is not a win, and
C++ in particular is a **tie** despite a 20-point point-estimate gap. The pooled
row is nine repository sets rather than a random sample of software.

## Per repository, which is where the spread lives

A language is not a repository. Every multi-repository cell is shown split,
because a pooled cell can hide a repository the pooled number would not predict.

| language | split (repowise) | split (CodeGraph) |
|---|---|---|
| java | **caffeine 20/30, spring-petclinic 10/10** | **caffeine 18/30, spring-petclinic 10/10** |
| kotlin | javalin 10/10, ktor 8/10, exposed 9/10 | javalin 2/10, ktor 4/10, exposed 7/10 |
| rust | ripgrep 7/10, serde 6/10, bevy 7/10 *(see note)* | ripgrep 5/10, serde 3/10, bevy 5/10 |
| cpp *(n=10 depth)* | fmt 9/10, Crow 7/10, leveldb 8/10, **seastar 5/10**, aria2 10/10 | fmt 4/10, Crow 3/10, leveldb 4/10, **seastar 6/10**, aria2 10/10 |

### seastar, read three times, and what that says about n=10

seastar has now been drawn and read three times at seed 2026, at three commits,
and it is the most useful row on this page for the wrong reason.

| commit | ours | CodeGraph |
|---|---|---|
| `13cc339a` | 4/10 | 6/10 |
| `48d400f7`, after [#1782](https://github.com/repowise-dev/repowise/pull/1782) | 8/10 | 6/10 |
| `350f6a3a`, the pinned commit this page now carries | **5/10** | 6/10 |

**A single repository read at n=10 cannot carry a claim, and this page made one
anyway.** It previously said seastar had stopped being the one repository where
CodeGraph beat us. On the current read they are ahead again by a single row, and
the three readings differ by more than any resolver change between them plausibly
did. All three are fresh draws rather than re-grades, because the population moved
under the sampler each time, so most of the spread is the draw and not the graph.

The 8/10 to 5/10 step is the clearest case. Between `48d400f7` and `350f6a3a`
seastar's call population moved by 63 sites out of 11,278, a shift of 0.6%, and
**none of the 10 newly drawn rows is one of the 63**. Every row in the new draw
is a site that existed and resolved identically at the previous commit. The read
fell by three rows without a single graded site changing, which is what sampling
variation at n=10 looks like.

What survives all three readings is the mechanism, because it is the same rows
each time it appears: a chained call on an untyped receiver, `something(...).get()`
on a `future<T>`, bound to an unrelated `get`. Four of the five wrong rows in the
current seastar draw are that shape, including `out.flush().get()` and
`fstr.read().get()` bound to `pipe_data_source_impl::get`, a class private to
`src/core/fstream.cc` that the calling test cannot see. CodeGraph infers the
callee's declared return type and **validates** the method against it, so a failed
inference costs it an edge rather than buying it a wrong one. That is a better
trade than ours on this shape, and #1782 narrowed it rather than closing it.

`aria2` is the other row worth more than the cell: 10/10 against 10/10, and they
resolve **24,950 distinct call edges to our 9,486**: same precision, 2.6x the
edges, on a quarter of the corpus's C++ files. Precision is not the only reading
and this row is where that shows.

The C++ split sums to the depth read's 39/50, not to the 22/30 pooled cell,
which is the seeded six-of-ten subsample of it. fmt, Crow, leveldb and aria2 carry
the rows read at `48d400f7`, kept because their seeded draws are identical at both
commits and no row in them changed target; seastar's ten were re-drawn and re-read
at `350f6a3a`.

**One thing the C++ diff turned up that is worth more than the cell.** 658 of fmt's
call sites changed the callee they record, and every one lands in the same target
file it did before: the symbol *name* was mis-sliced and is now correct, for example
`appender {::format` becoming `formatter<incomplete_type>::format`. The same change
repaired 129 mangled caller names on aria2 and 118 on seastar. **None of it moves a
call target**, so no verdict on this page depends on it, and it would be invisible
to any check that compares totals: the counts did not move by one.

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

By resolution origin, over **all 250 pooled rows whose verdicts are written down
as rows**. **Rust is excluded from the split and only from the split**: its cell
was re-read on a fresh draw whose per-row verdicts were never recorded, so its
eight wrong rows are counted everywhere else on this page and have no origin to
attribute. 250 plus rust's 30 is the 280 the pooled row uses.

| origin | rows | wrong | ambiguous | not correct |
|---|---:|---:|---:|---:|
| `same_package` | 28 | 8 | 1 | **32%** |
| `global_unique` | 28 | 9 | 0 | 32% |
| `import_merged` | 31 | 8 | 0 | 26% |
| `same_file` | 60 | 4 | 1 | 8% |
| `receiver_typed_global` | 5 | 1 | 0 | 20% |
| the other 15 receiver-typed and explicitly scoped tiers | 98 | 0 | 0 | **0%** |

Thirty wrong rows and two ambiguous out of 250, recomputed from the rows rather
than shifted from the previous version of this table. An earlier version of this
split was computed over 90 rows from three languages and is superseded; the shape
changed when the other six were included.

**The predictor is not how local an origin is. It is whether the tier is
uniqueness-gated or type-grounded.** Every receiver-typed or explicitly scoped
tier is 0 wrong across ~110 rows. The four bare-name tiers carry all 30 wrong
rows between them, and they do so at similar rates whether the name is looked up
in a package sibling, a merged import view, or the whole repository. A package
sibling feels closer than a repository-wide unique name and resolves no better.

`same_package` was the one bare-name tier with no uniqueness test at all: it
returned the first sibling file declaring the name. **On java that tier now
refuses when two siblings declare it.** The change is not reflected in the cells
on this page, which stay measured at `350f6a3a`; what it does is reported under
[the java cell, widened](#the-java-cell-widened-and-what-it-answered).

The bucket that is ours alone is the **chained call on an untyped receiver**
described under seastar above. Counted over the n=50 C++ depth read, where we take
11 wrong rows and they take 23, that shape is **4 of ours against 0 of theirs**,
all four in seastar. It read 3 against 0 before #1782 and 1 against 0 in the draw
taken just after, and the honest reading of those three numbers is that the bucket
is real and its size is not resolved at this sample size. It is still the only
bucket in the audit that is one-sided in their favour.

*(Their count is corrected here. This page previously said they take 20 wrong rows
on the C++ depth read; `rows/cpp-codegraph.json` has always held 27 correct of 50,
so the figure is 23. The error was in this sentence only and no rate depended on
it.)*

Two residual classes are named rather than quietly counted as losses:

* **Rust macro invocations graded as calls**, 4 of the 8 remaining wrong rows
  in the rust cell. They bind to the repository's own `macro_rules!` definition,
  so the *target* is right and only the *type* is wrong. Removing them would
  delete a real dependency in order to raise a precision number, which is the
  wrong trade; reclassifying them off `calls` is a separate change.
* **Swift property and subscript reads graded as calls**. The declaration named
  is right, the claim that it is a call is not.

Both are counted as wrong in every figure on this page.

## The java cell, widened, and what it answered

Java was the only cell on this page read on a single repository, and that made its
66.7% impossible to interpret: it could mean java resolves badly, or it could mean
caffeine does. The two have very different consequences and the page could not tell
them apart.

**caffeine is the outlier.** The tier that takes most of java's wrong rows,
`same_package`, resolves a bare name against the files sharing the caller's package.
Counting how often it fires with more than one candidate:

| repo | `same_package` sites | ambiguous | share |
|---|---:|---:|---:|
| caffeine | 31,511 | 18,390 | **58.4%** |
| jhipster-sample-app | 316 | 96 | 30.4% |
| javalin | 282 | 67 | 23.8% |
| spring-petclinic | 122 | 8 | 6.6% |

That is a hundredfold gap on repositories differing about fivefold in file count,
so it is not size. caffeine's test tree is large same-package blocks with static
imports everywhere, which is exactly the shape a package-scope lookup cannot
separate. Ordinary Spring and JHipster code imports explicitly and routes to
`import_scoped` and `import_merged` instead.

**So a second repository was read, on both sides.** spring-petclinic at
`b3ee2c53`, ten rows per side, seed 2026, same method, every row read from source.
It was chosen because its CodeGraph artifact was already frozen under
`artifacts/codegraph-1.5.0/`, so the paired column cost a read rather than a peer
run and nothing had to be re-indexed.

| | caffeine | spring-petclinic | cell |
|---|---|---|---|
| repowise | 20/30 | **10/10** | 30/40 = 75.0% |
| CodeGraph | 18/30 | **10/10** | 28/40 = 70.0% |

**Both sides are perfect on spring-petclinic.** Ordinary Spring code is not where
either resolver fails. The java row on this page was reporting a property of
caffeine and labelling it a property of the language, and the widened cell says so
without hiding the caffeine reading, which is still shown split above.

Java stays a tie: the intervals overlap heavily at n=40.

**`jhipster-sample-app` was read too and is deliberately not in the cell.** No arm
has a frozen artifact for it, so there is no paired column, and adding one would
mean running a peer. As an unpaired diagnostic it reads **3/10** on our side. Its
wrong rows are a different mechanism from this section's: `obj.getX()` and
`obj.setX()` calls for which the grammar mints a receiver-less site, which then
binds to a same-named member of a sibling DTO. It is reported here so that "java is
fine outside caffeine" is not read as a stronger claim than the evidence supports.

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
quotas sum to exactly 30 and a seeded draw inside each stratum. **java is 40**,
being 30 on caffeine plus 10 on spring-petclinic, drawn independently at the same
seed; the pooled row is 280 rather than 270 as a result and every other cell is
unchanged.

**Verdict.** Every row read from source: the call site with its enclosing scope
and the file's imports, then the target declaration. `correct` / `wrong` /
`ambiguous`, each with a one-line reason.

**C++ enters the pooled row at 30 rows even though 50 were graded.** The graded
sample is 50 per side; the pooled cell is a seeded 6-of-10-per-repository
subsample of it, so C++ carries the same weight as every other language rather
than nearly double. The 50-row read is reported above as the depth result and is
never added into the pooled figure.

## Provenance

**Every cell on our side is now measured at one commit, `350f6a3a`.** It used to
be four commits, one per group of cells, and that is what let two stale numbers
hide inside three days. One commit for all nine removes the class of error rather
than the two instances of it.

**Two resolver changes have landed since that commit and this page does not
reflect them.** Both remove edges the `same_package` and `global_unique` tiers
were getting wrong: a uniqueness test on java's package-scope lookup, and a
standard-library name list for swift. Their effect was measured on the edges they
move rather than by re-drawing a cell, because a seeded 30-row draw reshuffles
wholesale when the population changes at all and cannot see a change this size.
Of caffeine's 4,083 removed java edges, a stratified sample of 20 read 20 wrong
and 0 correct; of Alamofire's 198 removed swift edges, 11 of 11 read wrong.
Joining the graded rows against the change, three wrong java rows and three wrong
swift rows lose their edge, one wrong java row is retargeted onto the declaration
its own reason already named, and **no correct row on either cell is touched**.

That is evidence about the change and not a new cell: the surviving rows are no
longer a proportional draw from the smaller population. Both cells will be
re-drawn whole at the next pin, and until then the numbers above are what was
measured at `350f6a3a`.

| cells | repowise commit |
|---|---|
| all nine, ours | `350f6a3a` |
| cpp, CodeGraph | `13cc339a` (re-taken after #1700) |
| every other cell, CodeGraph | as originally taken; see below |

CodeGraph is `@colbymchenry/codegraph@1.5.0`, extraction version 24, for every
cell. **The peer side has never been re-measured and never needs to be**: the arm
is pinned at one version, its indexes are frozen artifacts that are never rebuilt
in place, and no change on our side can reach them. Its cells are the numbers they
have always been.

The one addition is spring-petclinic's ten java rows, and it did not involve
running the arm: the index was already frozen under
`artifacts/codegraph-1.5.0/spring-petclinic-b3ee2c53/`, and the rows were drawn
from it by the same reader that drew every other peer cell. Choosing a repository
whose artifact already existed is what kept the freeze intact.

### How nine cells reached one commit without nine re-reads

The cells were previously measured at `2017de7c` (go, typescript, csharp, python,
java), `58576af0` (kotlin, swift), `13cc339a` (rust) and `48d400f7` (cpp), and
twelve merged changes to the resolver landed across that spread. Re-reading 270
rows to find out which ones mattered would have been the obvious move and mostly
wasted.

**So the population was diffed first, and only the cells that moved were re-read.**
For all seventeen corpus repositories, the complete set of resolved call sites was
snapshotted at the commit its cell was measured at and again at `350f6a3a`, and
compared site by site: a site is `(file, line, target)`, and a site is *moved* if
it appeared, disappeared, or kept its identity while binding to a different
declaration. A cell whose repositories did not move is the same measurement at
both commits, and its rows keep their verdicts.

| cell | what moved between its old commit and `350f6a3a` | action |
|---|---|---|
| go | nothing. gitleaks identical, 2,279 sites | kept |
| typescript | nothing. zod identical, 9,174 sites | kept |
| swift | nothing. Alamofire identical, 2,610 sites | kept |
| rust | nothing. ripgrep, serde and bevy identical | kept |
| java | 3 of caffeine's 52,054 sites retargeted by #1686, none of them graded | kept |
| kotlin | javalin and ktor identical; exposed gained 9 sites | exposed's 10 re-drawn |
| cpp | 658 of fmt's recorded callees changed and none graded; seastar moved 63 | seastar's 10 re-drawn |
| csharp | Ocelot gained 622 sites | whole cell re-drawn |
| python | celery gained 1,136 sites | whole cell re-drawn |

Eighty rows were re-read instead of two hundred and seventy. **Five cells were
never re-read**: the four whose populations are byte-identical, and java, whose
three retargets are none of its graded rows. All five are certified at the pin by
evidence rather than by assumption.

**A re-drawn cell is a fresh draw, not a re-grade.** The sampler stratifies by
resolution origin and draws inside each stratum with a seeded generator, so adding
sites anywhere reshuffles the whole draw: 0 of 30 row keys survive in the csharp
and python cells. Patching individual rows would have broken the stratification,
so where a population moved the cell was replaced whole.

**Where the pooled figure went, and why it is smaller than the churn suggests.**
230/270 against the previous 229/270, both figures being the nine-cell pool as it
stood before java was widened to 40 rows. csharp rose two rows to 30/30 and C++ fell
one to 22/30; every other cell is unchanged. Neither move is attributable to a
resolver change: of the 30 csharp rows only one is a site that #1782 newly
captured, and it reads correct, while all ten seastar rows are sites that existed
and resolved identically before. **Both moves are the draw.** The direction of the
twelve changes remains unmeasured at this sample size, which is a smaller claim
than either "floor" or "regression", and it is the one the rows support.

Two changes in the same window are safely inert and are named so the list is not
mistaken for a complete inventory of the window: #1773 stores a call line as an
edge attribute and #1755 adds an edge-type constant. Neither produces an edge.

### How stale the page is against a later tip, measured rather than argued

The cells are pinned to `350f6a3a`. Nine resolver changes have merged since. The
same site-by-site diff that produced the table above was re-run for all
seventeen corpus repositories between that pin and `58403ddb6`, and then a second
question was asked that the first one does not answer: **did any GRADED row
move?** A population can move by thousands of sites and leave every verdict on
this page standing.

| cell | population before -> after | added | removed | retargeted | graded rows moved | their verdicts |
|---|---|---|---|---|---|---|
| typescript | 9,174 -> 9,174 *identical* | 0 | 0 | 0 | **0** | - |
| go | 2,279 -> 2,279 *identical* | 0 | 0 | 0 | **0** | - |
| python | 9,878 -> 9,878 *identical* | 0 | 0 | 0 | **0** | - |
| csharp | 8,946 -> 8,960 | 14 | 0 | 0 | **0** | - |
| kotlin | 41,139 -> 41,368 | 229 | 0 | 0 | **0** | - |
| swift | 2,610 -> 2,536 | 124 | 198 | 0 | **3** | 3 wrong |
| rust | 37,709 -> 35,714 | 1,029 | 3,024 | 0 | **4** | 4 wrong |
| java | 52,349 -> 48,234 | 65 | 4,180 | 7,048 | **4** | 4 wrong |
| cpp | 33,705 -> 41,754 | 8,186 | 137 | 462 | **1** | 1 wrong |

**Twelve of the 280 pooled rows moved, and every one of them is a row this page
grades `wrong`.** Eleven sites disappeared; one rebound elsewhere. **No row graded
`correct` moved on any cell.** That is the whole basis for calling the staleness
conservative, and it replaces the older and simply false claim that the changes
in between "gained zero" - C++ gained 8,186 sites and rust 1,029.

Three cells are certified byte-identical at the later tip. The other six are not,
and the correct repair is a fresh proportional draw per cell rather than patching
twelve rows, for the same reason given above: the sampler stratifies, so a
population that moves at all reshuffles the whole draw. That re-read has not been
done, so **this page remains a measurement at `350f6a3a` and says so**, with the
direction of its error now known rather than assumed.

The instrument is `dump_call_population.py` plus `g1_rows_moved.py`. The row files
do not share one schema - java writes a bare basename and `Class::method` where
others write a repo-relative path and a bare callee - so the join is
`(basename, line, callee-name)` and the script **refuses to report** unless every
row joins, because an unjoined row is indistinguishable from a deleted site and
reads as staleness that is not there. The first run of it reported all 40 java
rows as deleted, which was the join and not the resolver.

### One thing this page cannot currently reproduce

The go and typescript draws do not come back out of `sample_calls.py` at seed 2026:
replaying the sampler on the population those cells were measured on returns 26 of
gitleaks' 30 rows and only 3 of zod's 30. The same replay reproduces the other
seven cells exactly, and reproduces four freshly taken draws exactly, so the
replay is not what is wrong. The likely cause is that those two cells, the oldest
on the page, were drawn on the superseded sampler, whose population also included
heritage and reference records: that is 17 extra records in gitleaks and 12 in
zod, and a seeded draw inside a stratum shifts wholesale when the pool changes at
all. **It does not affect their verdicts**, because both populations are byte
identical at the old commit and the pin, so whatever was drawn was drawn from the
population that still stands. It is recorded because a page that ships its rows
should be able to say how they were drawn.

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

## php is graded and deliberately not pooled

`rows/php-ours.json` and `rows/php-codegraph.json` are complete, graded cells
over guzzle, monica and laravel-framework: **ours 30/30, the peer 28/30**, which
on overlapping intervals is a tie. They are **not** in `verify_rows.py`'s
`LANGUAGES` and not in the headline, so the published figure stays nine
languages at 240/280 and 164/280.

That is a decision, not an oversight, and it is recorded here because it was
previously recorded only in a commit message, where nobody reading the page
would find it. Two reasons:

* **Entering a language moves the board.** php should enter once, together with
  ruby, rather than moving the published pool twice. ruby has no rows at all
  yet.
* **The cell is at a different commit** (`18340180`) from the nine-cell pool
  (`350f6a3a`), so pooling it today would mix pins as well as languages.

**The 30/30 is real and has been read four times.** A perfect cell is a bug
until proven otherwise, so it was graded by two independent blind readers when
it was taken and by two more, blind to those, afterwards: all four return 30/30
with identical per-row labels. Its ceiling is stated rather than hidden: the
draw is proportional and php's population is dominated by its two most decidable
tiers, sixteen `self_scope` and six `import_merged` of thirty, because PHP makes
`use` imports mandatory. It says the tiers we already fire are sound, not that a
new one would be. The Laravel facade appears in none of the thirty rows and
monica alone holds roughly 850 facade static-call sites, so the cell is silent
on the shape that matters most for php coverage.

## Reproducing

The sampling harnesses are `sample_calls.py` on our side and
`sample_symmetric_peer.py` on theirs; the peer side reads the frozen indexes under
`test-repos/<repo>/.codegraph/codegraph.db`, which are read-only baselines and
are never regenerated in place.

**The 560 graded rows are in [`rows/`](rows/)**, one file per cell, each row
carrying its verdict and the reason written when it was read. `verify_rows.py`
rebuilds every table on this page from them and exits non-zero if it disagrees
with the headline; it agrees at 240/280 and 164/280. It also checks each file
against its own stated `depth_read`, which is how a file that disagreed with
itself went unnoticed for a session.

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
