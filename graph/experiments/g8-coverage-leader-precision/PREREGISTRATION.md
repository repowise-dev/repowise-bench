# G8 preregistration

**Written 2026-08-22, before a single row was graded.** It was drafted as "G7"
and renumbered to G8 before publication, because G7 was already taken by the
closed language-breadth experiment in the index; nothing else in it was touched
after the first verdict was read. The population census
below was taken first, because the allocation depends on it; no verdict had been
read when the predictions were written, and the draw is deterministic under seed
2026, so the order of those two steps cannot move a number.

The point of this file is method rule 7: a surprising precision result is
credible only if the prediction it surprises was written down first. The
predictions are graded in the experiment README, **including the ones that miss**.

## What is being measured

Of the call edges codebase-memory-mcp 0.10.8 emits on the nine corpus languages
where no compiler oracle can judge it, what share are real? Read from source, by
hand, by G1's method.

Go and TypeScript are excluded because G4 already judges this arm there against
a compiler.

## Predictions

### Pooled

**62% [55, 69].** Below our published 84.8% and above CodeGraph's 57.0%, with an
interval disjoint from ours and overlapping CodeGraph's.

The reasoning: 68% of this arm's population across the nine cells sits in two
bare-name strata, `suffix_match` and `unique_name`, which are mechanically the
same fallback that carries CodeGraph's wrong rows in G1. What should pull it
above CodeGraph is the rest: it ships per-language typed tiers CodeGraph has no
equivalent of (`cs_method_typed`, `php_method_typed`, `lsp_type_dispatch`,
`lsp_kt_this`, `lsp_method_dispatch`), and those should grade well.

### Per language

Ordered by predicted rate. The ranking is the prediction that matters, more than
the levels: it is driven by one number, the share of that language's population
sitting in the two bare-name strata.

| language | bare-name share of population | predicted | our G1 cell |
|---|---:|---:|---:|
| java | 43.5% | 73% | 66.7% |
| python | 40.2% | 72% | 93.3% |
| csharp | 60.0% | 70% | 93.3% |
| rust | 65.8% | 62% | 73.3% |
| swift | 72.8% | 60% | 76.7% |
| cpp | 70.4% | 60% | 76.7% |
| php | 78.0% | 55% | *(no G1 cell)* |
| kotlin | 73.3% | 53% | 90.0% |
| ruby | 87.2% | 47% | *(no G1 cell)* |

### Mechanism predictions, which are the falsifiable part

1. **`suffix_match` grades below 45%.** It is the largest stratum in seven of
   the nine languages and carries a stored confidence as low as 0.06.
2. **The typed and LSP-backed strata grade above 80%** pooled: every origin
   matching `lsp_*`, `*_typed`, `field_type_hint`, `same_module`.
3. **Their own stored `confidence` separates the two.** Rows below 0.3 grade
   worse than rows at or above 0.8, by at least 30 points.
4. **We are ahead in all seven languages that have a G1 cell**, on the point
   estimate, and separate on at least four of them.
5. **No language cell comes back above 85%.**

### The result that would refute the strategic read

If the pooled figure comes back at or above 80%, or if four or more languages
land above our G1 cell for the same language, then the coverage lead is not
bought with wrong edges outside Go and TypeScript, their resolver is genuinely
better on those languages, and sessions 4 and 5 should be argued differently.
That is a real possibility and it is written here so it cannot be explained away
afterwards.

## Method, fixed in advance

Everything here is G1's, and the departures are listed rather than absorbed.

* **n = 30 per language**, 270 rows, seed 2026.
* **Strata** `properties.strategy`, in proportion to population, largest-remainder
  allocation, seeded draw inside each stratum. `stratified()` is imported from
  the G1 harness rather than re-implemented.
* **Denominator** distinct `(caller_file, call_line, target_qualified_name)` on
  `type IN ('CALLS','ASYNC_CALLS')`. `CALL_REFERENCE` and `USAGE` are excluded,
  per the arm page's edge vocabulary. Raw and distinct counts are never mixed.
* **Language scope** the caller file's extension through the arm's own
  extension table. `.h` is C on that table, so the cpp cells are header-free and
  the denominator is not the one our own C++ rows use.
* **Verdict** `correct` / `wrong` / `ambiguous`, each read from source: the call
  site with its enclosing scope and the file's imports, then the target
  declaration in the file the tool claims it landed in. `ambiguous` is never
  counted as correct. A verdict inferred from an identifier is not a verdict.

### Repositories, and why these

The seven languages with a G1 cell use **G1's repository set exactly**, so the
cells are comparable with the published 84.8% / 57.0% pair. php and ruby have no
G1 cell, so they use the three corpus repositories behind their coverage row.

| language | repositories | rows each |
|---|---|---|
| csharp | Ocelot | 30 |
| python | celery | 30 |
| java | caffeine | 30 |
| swift | Alamofire | 30 |
| kotlin | javalin, ktor, exposed | 10 |
| rust | ripgrep, serde, bevy | 10 |
| cpp | fmt, Crow, leveldb, seastar, aria2 | 6 |
| php | guzzle, laravel-framework, monica | 10 |
| ruby | faraday, jekyll, sinatra | 10 |

**cpp is six rows per repository, not ten.** G1 graded ten and subsampled six
into its pooled cell so C++ would not carry double weight; drawing six directly
reaches the same cell size without grading forty rows that are then discarded.
There is therefore no fifty-row C++ depth read on this page.

**cpp omits `nlohmann-json`**, which the coverage row includes. G1's cpp cell is
the five repositories above, and matching it was worth more than matching the
coverage row's six.

## Population census, taken before the draw

Distinct in-language call edges, and the share in the two bare-name strata.

| language | repo | raw rows | distinct | in language | no call line | bare-name share |
|---|---|---:|---:|---:|---:|---:|
| csharp | Ocelot | 11,730 | 11,730 | 11,724 | 0 | 60.0% |
| python | celery | 16,768 | 16,098 | 15,996 | 0 | 40.2% |
| java | caffeine | 42,630 | 42,630 | 42,370 | 0 | 43.5% |
| swift | Alamofire | 5,553 | 5,439 | 4,979 | 0 | 72.8% |
| kotlin | javalin | 8,539 | 8,536 | 7,793 | 0 | 61.5% |
| kotlin | ktor | 50,789 | 50,789 | 49,913 | 0 | 77.1% |
| kotlin | exposed | 30,012 | 27,832 | 26,670 | 0 | 81.3% |
| rust | ripgrep | 6,143 | 5,879 | 5,739 | 264 | 62.0% |
| rust | serde | 1,835 | 1,831 | 1,831 | 4 | 69.0% |
| rust | bevy | 60,337 | 59,791 | 58,719 | 546 | 65.8% |
| cpp | fmt | 7,510 | 7,413 | 2,587 | 0 | 70.0% |
| cpp | Crow | 1,270 | 1,243 | 360 | 0 | 63.3% |
| cpp | leveldb | 2,445 | 2,398 | 2,240 | 0 | 60.0% |
| cpp | seastar | 18,695 | 18,552 | 13,530 | 0 | 81.0% |
| cpp | aria2 | 16,405 | 16,395 | 14,181 | 0 | 75.2% |
| php | guzzle | 6,849 | 6,849 | 6,849 | 0 | 74.1% |
| php | laravel-framework | 91,691 | 91,232 | 91,225 | 459 | 80.7% |
| php | monica | 12,095 | 12,088 | 12,063 | 0 | 79.1% |
| ruby | faraday | 722 | 722 | 719 | 0 | 91.8% |
| ruby | jekyll | 2,396 | 2,392 | 2,263 | 0 | 89.2% |
| ruby | sinatra | 2,180 | 2,155 | 2,147 | 0 | 80.5% |

`no call line` rows are excluded from the denominator: an edge with no line
cannot be graded at a site.

> **Corrected 2026-08-22, after the fact and flagged rather than overwritten.**
> This paragraph originally summed the exclusion as "1,273 rows of 384,300". Both
> figures were taken from a partial census run before the last three cells
> finished, and both are wrong. The right numbers, from the per-cell table above:
> **4,267 raw rows carry no line**, in three of the twenty-one cells, and the
> **389,710 distinct edges** that remain all carry one. The per-cell counts in the
> table were always right, no prediction depended on the summary line, and no
> published rate moves. It is corrected here rather than silently edited because a
> preregistration whose numbers change without a note is worth nothing.

## One instrument finding, recorded before it changes anything

Two pages in this benchmark say this arm stores no call-site line: normalisation
decision 3 on `arms/codebase-memory-mcp.md`, and the call-site-granularity note
in G4. **That is wrong, and it was checked against source before this sample was
drawn.** There is no line *column* on `edges`, but `properties` carries
`$.line`, and it is the call site rather than the declaration.

Four rows, checked by opening the file at the line the field names:

| file | line | field says | source at that line |
|---|---:|---|---|
| `Source/Core/Request.swift` | 453 | `eventMonitor?.request` | `eventMonitor?.request(self, didSuspendTask: task)` |
| `Tests/AuthenticationInterceptorTests.swift` | 817 | `session.request(...).validate` | `let request = session.request(.default, interceptor: compositeInterceptor).validate().response {` |
| `Example/Source/DetailViewController.swift` | 208 | `Sections` | `if Sections(rawValue: section) == .body, let elapsedTime {` |
| `Source/Core/SessionDelegate.swift` | 382 | `fileManager.createDirectory` | `try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)` |

It is present on every `CALLS` row in eighteen of the twenty-one cells above and
on 95% or better in the other three. What remains true is the **multiplicity**
half of that note: `edges` is unique on `(source_id, target_id, type)`, so a
caller invoking one target twice stores one row and one of the two lines. The
count is still a lower bound. The line is real, and this experiment grades at
the site because of it.
