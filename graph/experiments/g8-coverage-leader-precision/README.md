# G8: the coverage leader's edges, priced on the nine languages no oracle reaches

Of the call edges codebase-memory-mcp emits on the languages a compiler cannot
judge, what share are real? Read from source, by hand, by G1's method.

**Status: measured. Nine languages, 270 graded rows, one arm.**

## Why this experiment exists

[The main page](../../README.md) loses the coverage table comprehensively:
codebase-memory-mcp separates from us on 15 of 35 repositories and we separate
on none. Our answer has always been that its lead is bought with wrong edges,
and [G4](../g4-oracle-anchored/) proves that on Go and TypeScript, where a
compiler can be made to arbitrate. On the other nine languages it was an
assertion, and the main page said so against itself:

> a reader is entitled to treat the other nine rows as a coverage lead of
> unknown quality.

This is the sample that page asked for.

## Headline

30 rows per language, seed 2026, stratified by resolution strategy in proportion
to population, every row read from source.

| | correct / n | 95% CI |
|---|---|---|
| **codebase-memory-mcp 0.10.8** | **137/270 = 50.7%** | [44.8, 56.7] |

**About half of the coverage leader's call edges, on the languages nobody had
judged, do not exist.** The interval is disjoint from our published 84.8%
[80.0, 88.6] by more than twenty points at the nearest ends.

## Per language

| language | repositories | correct / n | 95% CI | our G1 cell | verdict |
|---|---|---|---|---|---|
| python | celery | 23/30 = 76.7% | [59.1, 88.2] | 28/30 = 93.3% | tie |
| php | guzzle, laravel-framework, monica | 17/30 = 56.7% | [39.2, 72.6] | *(no cell)* | — |
| kotlin | javalin, ktor, exposed | 16/30 = 53.3% | [36.1, 69.8] | 27/30 = 90.0% | **we separate** |
| java | caffeine | 15/30 = 50.0% | [33.2, 66.8] | 20/30 = 66.7% | tie |
| cpp | fmt, Crow, leveldb, seastar, aria2 | 15/30 = 50.0% | [33.2, 66.8] | 23/30 = 76.7% | tie |
| ruby | faraday, jekyll, sinatra | 15/30 = 50.0% | [33.2, 66.8] | *(no cell)* | — |
| rust | ripgrep, serde, bevy | 13/30 = 43.3% | [27.4, 60.8] | 22/30 = 73.3% | tie |
| swift | Alamofire *(n=1 repo)* | 12/30 = 40.0% | [24.6, 57.7] | 23/30 = 76.7% | **we separate** |
| csharp | Ocelot | 11/30 = 36.7% | [21.9, 54.5] | 28/30 = 93.3% | **we separate** |
| **pooled** | | **137/270 = 50.7%** | [44.8, 56.7] | **229/270 = 84.8%** | **we separate** |

**We are ahead on the point estimate in all seven languages that have a G1 cell,
and separate on three of them.** Four are statistical ties at n=30 and are
reported as ties. The pooled figures separate cleanly; the per-language cells
mostly cannot, which is what n=30 buys.

## The like-for-like three-way, which is the row to quote

The pooled 50.7% above and the published 84.8% / 57.0% pair **are not over the
same nine languages**: G1's nine include go and typescript and exclude php and
ruby, and this experiment does the reverse. Quoting 50.7% against 57.0% compares
two different language sets and should not be done.

The seven languages all three arms have been hand-graded on:

| language | repowise | CodeGraph 1.5.0 | codebase-memory-mcp 0.10.8 |
|---|---|---|---|
| csharp | 28/30 = 93.3% | 20/30 = 66.7% | 11/30 = 36.7% |
| python | 28/30 = 93.3% | 19/30 = 63.3% | 23/30 = 76.7% |
| java | 20/30 = 66.7% | 18/30 = 60.0% | 15/30 = 50.0% |
| swift | 23/30 = 76.7% | 19/30 = 63.3% | 12/30 = 40.0% |
| kotlin | 27/30 = 90.0% | 13/30 = 43.3% | 16/30 = 53.3% |
| rust | 22/30 = 73.3% | 13/30 = 43.3% | 13/30 = 43.3% |
| cpp | 23/30 = 76.7% | 16/30 = 53.3% | 15/30 = 50.0% |
| **pooled** | **171/210 = 81.4%** [75.6, 86.1] | **118/210 = 56.2%** [49.4, 62.7] | **105/210 = 50.0%** [43.3, 56.7] |

`python verify_rows.py --threeway` rebuilds it, reading G1's own row files for
the first two columns rather than copying its published table.

**The two peers are a statistical tie with each other and both separate from
us.** Saying the coverage leader is *less* precise than CodeGraph is not
supported: 50.0% against 56.2%, intervals overlapping. What is supported is that
neither is within reach of 81.4%.

## What this buys the coverage table

The coverage rows we lose are unchanged and are not re-measured here, because
coverage was never the question. What changes is what a reader may conclude from them.
On the nine languages where this arm leads coverage, **roughly one edge in two
is wrong**, and a coverage metric counts a file as reached on its first edge
without asking whether that edge exists.

That is the same trade G4 measures on Go and TypeScript, now measured rather
than inferred everywhere else. It does not make our coverage number good. It
means the gap between the two coverage numbers is not a gap in what the two
tools know.

## Where the wrong edges come from

The failure is concentrated, and it is one mechanism.

| stratum kind | correct / n | rate | 95% CI |
|---|---|---:|---|
| bare-name (`suffix_match`, `unique_name`) | 72/184 | 39.1% | [32.4, 46.3] |
| typed and LSP-backed | 63/84 | 75.0% | [64.8, 83.0] |

| origin | correct / n | rate | 95% CI |
|---|---|---:|---|
| `suffix_match` | 24/104 | 23.1% | [16.0, 32.0] |
| `unique_name` | 48/80 | 60.0% | [49.0, 70.0] |
| `field_type_hint` | 11/24 | 45.8% | [27.9, 64.9] |
| every `lsp_*` origin pooled | 34/37 | 91.9% | [78.7, 97.2] |

**`suffix_match` is 38.5% of the sampled rows, drawn in proportion to
population, and answers wrongly three times in four.** It is the tool's largest single tier by volume on seven of the nine
languages. `unique_name`, the other bare-name tier, is 29.6% of the sample and wrong
two times in five. Together the two are **261,637 of the 371,636 in-language
distinct call edges across the 21 repository cells, 70.4%**, which
`verify_rows.py --population` recomputes from the draws. (Rebuilding it from the
rounded per-language shares in the preregistration instead lands near 70.0%; the
figure here is over the unrounded per-cell counts.)

The typed tiers are a different tool. Where the arm establishes a receiver type
it is good: `lsp_type_dispatch` 9/9, `cs_self_method` 6/6, `php_method_typed`
5/5, `lsp_method` 5/5, `lsp_direct` 5/5. Nothing in this audit argues that its
resolution is bad in general. It argues that its coverage is carried by the tier
that does not resolve at all, and that tier is a name match.

### Their own confidence already knows

The arm stores a `confidence` on every edge, and it is informative:

| band | correct / n | rate | 95% CI |
|---|---|---:|---|
| `confidence < 0.3` | 15/86 | 17.4% | [10.9, 26.8] |
| `0.3 <= confidence < 0.8` | 58/99 | 58.6% | [48.7, 67.8] |
| `confidence >= 0.8` | 64/85 | 75.3% | [65.2, 83.2] |

A 58-point spread, and the low band is a third of the sample. The information
needed to suppress most of these edges is already in the database that emits
them. This is worth saying plainly because it is the opposite of a subtle
defect: the tool is not confidently wrong, it is unconfidently wrong and ships
the edge anyway.

### The shapes behind the wrong rows

Every one of these was read from source, and each is in `rows/` with its reason.

* **A standard-library or framework receiver bound to a repository declaration
  of the same name. This is the largest shape by some distance**, on the order of
  a third to a half of the 132 wrong rows depending on where "framework" is drawn.
  It is deliberately given as a range: unlike every other figure on this page it
  is a reading of the reasons rather than a field, `verify_rows.py` cannot rebuild
  it, and a reader who wants a number should bucket `rows/` themselves.
  `time.sleep` in celery bound to a test helper's `sleep`; `_placeholders.ContainsKey`
  on a `Dictionary<>` in Ocelot bound to a test double's `ContainsKey`;
  `data.key?` on a Hash in jekyll bound to `Jekyll::Cache#key?`;
  `Mockery::type(...)` in laravel bound to `Filesystem::type`.
* **Production code bound into the test tree**, around 7 rows on the same
  read-the-reasons basis and with the same caveat. A `main`-side call in
  caffeine's `BoundedLocalCache.toString` bound to `BadNode.getKey`, a private
  test-only subclass; celery's `AsyncResult.collect` bound to a mock result
  class under `t/`.
* **Right name, wrong type, in the same repository.** bevy's `schedule.add_systems`
  bound to `Schedules::add_systems`, the plural type; Alamofire's fluent
  `.validate()` chains bound to whichever unrelated class declares a `validate`.
* **Overload-level errors:** 11 rows, right declaring type and wrong overload,
  flagged `error_class: "overload"` so the alternative grading can be recovered.
  Grading them `correct` would move the pooled figure to 148/270 = 54.8%
  [48.9, 60.6], still disjoint from ours.
* **A method call bound to a field.** serde's `variant.fields.len()` bound to a
  struct field named `len`, which is not a declaration of a callable at all.

## A defect in its call-site lines, found on the way, and it is C++-only

Two benchmark pages state that this arm records no call-site line. **That is
wrong**, and the correction is in [`PREREGISTRATION.md`](PREREGISTRATION.md)
with four rows checked against source: `properties.$.line` is the call site, not
the declaration, and this experiment grades at the site because of it.

Having relied on that field, it was worth checking. Over every `CALLS` row in
each repository, how often does the line exceed the length of the file the edge
says it is in?

| repo | call rows | line past end of file | share |
|---|---:|---:|---:|
| fmt | 7,510 | 2,265 | **30.2%** |
| aria2 | 16,405 | 2,774 | **16.9%** |
| seastar | 18,676 | 164 | 0.9% |
| leveldb | 2,445 | 1 | 0.0% |
| celery | 14,157 | 5 | 0.0% |
| Crow, Alamofire, Ocelot, caffeine, ripgrep, serde, javalin, exposed, guzzle, monica, faraday, jekyll, sinatra | | 0 | 0.0% |

It is C and C++ only, and it is not confined to a stray file, since 317 of
aria2's 869 source files and 22 of fmt's 80 carry at least one. The shape says
include-expansion: a line counted in the translation unit after `#include`
substitution and then attributed to the includer. `include/fmt/ostream.h` is 167
lines and carries 222 call rows whose line is past its end.

**Consequence for this page:** two of the 270 sampled rows landed on a
nonexistent line, both in cpp, and both were graded `wrong`. Excluding them
instead moves cpp from 15/30 = 50.0% to 15/28 = 53.6% and the pooled figure from
50.7% to 51.1%. Neither reading changes anything, and both are stated so nobody
has to wonder.

**Consequence for G4:** its decision not to key on call sites was right, but the
reason recorded there, that the arm stores no line, is not the reason. The
line exists; on C++ a sixth to a third of them are unusable.

## Is 50.7% the languages, or is it us grading harder than a compiler?

A fair objection to everything above: this arm scores 0.635 to 0.987 against the
Go and TypeScript compilers in G4, and 50.7% when we read it by hand. Two things
differ at once, the languages and the method, so the gap could be either.

**It is the languages.** The same sample was drawn on go/gitleaks, a cell where
the oracle already has an answer, and graded by the same rubric by a reader who
was not told what the oracle said.

| | codebase-memory-mcp on go/gitleaks |
|---|---|
| hand-graded here, n=30 | **28/30 = 93.3%** [78.7, 98.2] |
| the Go compiler, G4, ~1,600 edges | **0.934** [0.921, 0.945] |

**93.3% against 93.4%.** The two methods agree to within a tenth of a point on
this arm, which is the same agreement G1 and G4 already found on our side and on
CodeGraph's. So hand-grading is not harsher than a compiler here, and the drop
from 93% to 50.7% is a fact about the nine languages rather than about the
instrument.

It also says something about the arm worth stating plainly: **its Go resolution is
good.** 23 of its 30 Go rows came from a cross-file LSP-backed tier and all 23 were
right. The two wrong rows are the same bare-name shape that carries the other nine
languages. What differs between Go and, say, csharp is how much of the graph the
name-matching tier is left to answer for.

This cell is a method control and is **not** part of the 270 or of any pooled
figure. It lives in `rows/VALIDATION-go-gitleaks.json` and is flagged as such
inside the file.

## The predictions, graded, misses included

[`PREREGISTRATION.md`](PREREGISTRATION.md) was written before a row was read.

| # | prediction | outcome |
|---|---|---|
| pooled | 62% [55, 69], below ours, above CodeGraph's | **missed, on both halves.** 50.7% [44.8, 56.7] falls outside the predicted interval entirely. On the direction: the prediction has to be graded on the like-for-like seven, not against the 57.0% figure this page forbids quoting here, and there it is 50.0% against CodeGraph's 56.2%, i.e. below rather than above. Only the overlap of those two intervals keeps it from being a clean directional loss |
| ranking | nine languages ordered by bare-name share | **failed.** Spearman −0.15 against the outcome, which is no predictive power at all |
| 1 | `suffix_match` below 45% | **held**, and by a distance: 23.1% [16.0, 32.0] |
| 2 | typed / LSP strata above 80% | **narrowly missed.** 75.0% [64.8, 83.0], interval contains 80 |
| 3 | stored confidence separates the bands by 30+ points | **held.** 57.9 points, 17.4% against 75.3% |
| 4 | ahead in all seven G1 languages, separating on four | **half held.** Ahead in 7 of 7; separating on 3, not 4 |
| 5 | no cell above 85% | **held.** The highest is python at 76.7% |
| refutation | pooled at or above 80%, or four cells above our G1 cell | **not triggered.** The strategic read survives |

**The ranking failure is the useful miss.** The prediction was that the share of
a language's population sitting in bare-name strata would order the languages,
and it does not: csharp is 60% bare-name and came last at 36.7%, while ruby is
87% bare-name and came in above it at 50.0%. What varies between languages is
not how much the tool falls back but how badly the fallback misses when it does,
and that is a property of the language's naming conventions rather than of the
resolver's tier mix. Nothing here predicts a language cell in advance; the
pooled level was the only part worth believing, and it was ten points optimistic.

## Method

Identical to [G1](../g1-edge-precision/) except where stated. The full
specification, fixed in advance, is in
[`PREREGISTRATION.md`](PREREGISTRATION.md); the verdict rubric every grader read
is in [`GRADING.md`](GRADING.md).

**Denominator.** Distinct `(caller_file, call_line, target_qualified_name)` on
`type IN ('CALLS','ASYNC_CALLS')`. `CALL_REFERENCE` and `USAGE` are excluded,
following the edge vocabulary on [the arm's page](../../arms/codebase-memory-mcp.md).
Raw and distinct counts are never mixed, which is why the exclusion below is
stated in the unit it is counted in: **4,267 raw rows carry no line at all** and
are dropped before the fold, in three of the twenty-one repository cells. The
**389,710 distinct edges** that remain all carry one. An edge with no line cannot
be graded at a site.

**Sample.** 30 rows per language, seed 2026, stratified on `properties.strategy`
in proportion to population, largest-remainder allocation, seeded draw per
stratum. The allocation function is imported from G1's harness rather than
reimplemented.

**Verdict.** `correct` / `wrong` / `ambiguous`, read from source: the call site
with its enclosing scope and the file's imports, then the target declaration in
the file the tool names. `ambiguous` is never counted as correct; it was used
once in 270 rows.

**The grading was audited before this page was written.** Nine graders produced
the nine cells, so a systematic grader error is the main threat to the figure. An
independent reader re-graded a 14-row spot-check from source, at least one
`correct` and one `wrong` per language, chosen towards rows whose reason read
confident but thin, and briefed to look hardest at the `wrong` rows since a real
edge marked wrong is the error that would flatter us. **14 of 14 agreed.** Two
further wrong rows were re-read separately and both stood. Two mechanical checks
back that up: all 270 graded rows match their pre-grading draw with only the
verdict fields changed, and every row carries a verdict, a reason and a verbatim
source line.

**Nothing was rebuilt.** Every reading is against the frozen artifact cache at
`artifacts/codebase-memory-mcp-0.10.8/`, one SQLite index per repository at the
corpus pin, built 2026-08-19. Each source checkout in `test-repos/` was verified
at the same pin before grading. A consequence worth recording: the arm's Windows
ACL precondition, which is the expensive part of running it, never had to be
cleared for this experiment.

### Where this departs from G1, and what each departure costs

* **`.h` is C on this arm's language table, so the cpp cell is header-free.**
  Our own C++ rows take headers in. The two cpp cells are over populations of
  different size, and the comparison in the three-way table above is between a
  46-file-ish population and a 68-file one on fmt. This is the arm's own
  attribution of its own index and is not adjusted.
* **cpp is six rows per repository, not G1's ten-then-subsample-six.** The cell
  size is the same 30 and carries the same weight. There is no fifty-row C++
  depth read on this page.
* **cpp omits `nlohmann-json`**, which the coverage row includes and G1's cpp
  cell does not. Matching G1 was worth more than matching the coverage row.
* **php and ruby are new cells with no counterpart on either side.** They enter
  the pooled 50.7% and are excluded from every comparison with a published
  figure, which is why the three-way table is seven languages.

## What this does not claim

* **Nine repository sets, several a single repository.** Not a random sample of
  software. Swift is one repository and is labelled as such everywhere.
* **n=30 per cell.** Six of the nine per-language comparisons against our G1
  cell are statistical ties and are reported as ties. Only the pooled figures
  separate.
* **This is precision, not coverage and not recall.** The arm reaches more files
  than we do on every language here and that is unchanged by this page. G4's
  standing reminder applies: precision alone is as gameable as coverage alone,
  in the opposite direction.
* **It is not a claim that this arm is worse than CodeGraph.** 50.0% against
  56.2% on the shared seven, intervals overlapping. A tie.
* **Our own cells are the published G1 ones, not re-read here.** They were
  graded at earlier commits by the same method; G1's provenance table says which.
* **One arm, one version.** `codebase-memory-mcp 0.10.8`, the release measured
  throughout this benchmark.

## Reproducing

```bash
python verify_rows.py             # every table above, recomputed from the rows
python verify_rows.py --strata    # the by-origin and by-confidence reads
python verify_rows.py --threeway  # the seven-language table, reading G1's rows
python verify_rows.py --rows ruby   # one cell, with its source lines and reasons
python verify_rows.py --population # the bare-name share of the drawn-from population

python sample_cbm.py Ocelot --lang csharp --census   # population, draws nothing
python sample_cbm.py Ocelot --lang csharp --n 30 --out draws/csharp-Ocelot.json
```

`draws/` holds the 21 drawn cells exactly as the sampler produced them, before
any verdict was written; `rows/` holds the same rows graded. A reader who
disagrees with a verdict can edit the row and watch the table move.
