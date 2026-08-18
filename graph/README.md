# Graph quality

Does the call graph contain the right edges?

This is a different question from the one [`head-to-head/`](../head-to-head/README.md)
asks. That benchmark measures whether a tool points an agent at the right files,
and grades the answer. This one opens the index and asks whether the edges inside
it are true. A tool can win the first and lose this one: pointing at the right
neighbourhood does not require the arrows between the houses to be correct.

**Nobody in this field publishes a graph-correctness number against an outside
oracle.** We checked every comparable tool (see [arms/](arms/)). CodeGraph
publishes agent-efficiency deltas and a coverage table. Graphify publishes memory
and QA scores. code-review-graph publishes an F1 — 0.714, with precision 0.578 —
graded against ground truth derived from the same graph its predictor walks,
which measures self-consistency rather than correctness. To their credit **they
say so themselves**, calling it "circular by construction" in both their README
and their eval code, and they ship a non-circular co-change mode whose numbers
they decline to quote before measuring. The gap this benchmark fills is the
obvious one, and it is the reason it exists.

---

## Status

Nothing on this page is published externally yet. Bold rows have run at a
recorded commit across every arm; `designed` rows have a written protocol and
no run behind them. G1's rows exist and are graded but predate the current
Python resolver, so they are not on this page.

| | Experiment | What it settles | Status |
|---|---|---|---|
| **G1** | [Edge precision](experiments/g1-edge-precision/) | Of the edges a tool emits, what share are true? Hand-graded from source. | measured privately, needs porting |
| **G2** | [Cross-file coverage](experiments/g2-cross-file-coverage/) | CodeGraph's own published metric, recomputed on every arm by one script. | **four arms, 35 repos, 11 languages** |
| **G3** | [Shared-denominator recall](experiments/g3-shared-denominator/) | Of the calls that exist in the source, what share does each tool resolve? | denominators built, recall next |
| **G4** | [Oracle-anchored precision and recall](experiments/g4-oracle-anchored/) | Both, automatically, at n in the thousands, against a gold graph neither tool produced. | designed |
| **G5** | [Adversarial invariance](experiments/g5-invariance/) | Does the resolver actually resolve, or does it match names? | **scored, four arms, Go** |
| **G6** | [Graph build cost](experiments/g6-build-cost/) | Seconds and peak memory to produce the graph, and nothing else. | **four arms, six repos** (the 35-repo run is coverage only) |
| **G7** | [Language breadth](experiments/g7-breadth/) | Every tool claims 20 to 40 languages. How many of them work? | **four arms, 35 repos, 11 languages** |

Four arms: **repowise**, **CodeGraph 1.5.0**, **Graphify 0.9.31** and
**code-review-graph 2.3.7**, all behind [one adapter protocol](lib/arms.py) so
an experiment takes an arm name and stops caring what the tool is. All four
rebuild byte-identically on a repeat run, so none of them is non-deterministic
and a mutation's effect can be separated from drift.

A fifth, **codebase-memory-mcp 0.10.6**, has [an adapter and a
page](arms/codebase-memory-mcp.md) but no numbers: its release binary refuses to
start on the measurement machine, because it validates the ACL of every ancestor
of `%LOCALAPPDATA%` and rejects a profile where another local account holds
write rights there. That is a portability finding rather than an omission, and
it is the only arm of five that will not run on a stock developer profile
carrying a second account.

G4 and G5 are the two that do not exist anywhere in this field. G1 is the one we
already have and have not published. G2 is the one a reader will ask for first,
because it is the number our largest competitor puts on its front page.

Numbers on this page were measured at **`3594ba75`** unless the section says
otherwise; the 35-repository corpus below was measured at **`58576af0`**. Both
on a clean detached worktree. Cost figures always come from a run with a
discarded warmup per arm per repository.
`lib/provenance.py` refuses to run against a dirty tree without `--allow-dirty`
and stamps anything produced that way `publishable: false`. Every table is
generated from `results/graph/` by `graph/tools/render.py` rather than typed,
because the sibling retrieval bench has twice published a row that no longer
matched the data behind it.

Check the instruments still work:

```bash
python graph/smoke.py          # 16 checks, exit code is the failure count
```

Note it needs an interpreter that can import `repowise.core`. Run under one
that cannot and it used to report six passes and two skips, having never
touched our graph; it now fails loudly instead.

---

## First result: CodeGraph's coverage metric does not mean what it says

CodeGraph's README publishes a per-language coverage table (Python/requests 100%,
PHP/guzzle 100%, Go/gin 96.6%, Java/gson 93.3%, and so on for 22 languages) under
this definition:

> **Fair coverage** = the share of symbol-bearing source files that have at least
> one *resolved cross-file dependent*.

"Has a dependent" describes an **incoming** edge: something elsewhere depends on
this file. Read that way, the table does not reproduce, and it is not close. We
indexed two of their own benchmark repos with their own released binary
(`@colbymchenry/codegraph@1.5.0`, the current tagged release) and computed the
metric from the index it wrote:

| repo | their published figure | incoming edges only | either direction |
|---|---:|---:|---:|
| psf/requests | 100% | 79.4% | 97.1% |
| guzzle/guzzle | 100% | 60.3% | **100.0%** |

The metric they are actually reporting counts a file as covered if it sits at
**either end** of a cross-file edge. guzzle lands on 131/131 exactly. requests
misses by one file, which we attribute to commit drift, since their pin is not
published and ours is `4ed3d1b3`.

**That reading makes the metric close to uninformative.** A file that imports
anything at all satisfies it, and `imports` is an edge kind CodeGraph emits from
the file node itself. It measures whether the walker found the file, not whether
the resolver understood it. Run their own tool over the six repositories our
head-to-head already uses and the two readings separate by up to 70 points:

| repo | language | files | either direction (their metric) | incoming only | incoming `calls` only |
|---|---|---:|---:|---:|---:|
| dub | typescript | 3,911 | 0.991 | 0.748 | 0.589 |
| Ocelot | csharp | 732 | 0.985 | 0.669 | 0.352 |
| celery | python | 372 | 0.979 | 0.618 | 0.489 |
| zod | typescript | 291 | 0.938 | 0.240 | 0.148 |
| gitleaks | go | 213 | 0.915 | 0.789 | 0.784 |
| caffeine | java | 664 | 0.801 | 0.622 | 0.517 |

Their metric puts five of six repos above 0.9 and calls that a language result.
The incoming-`calls` column, which is the one that describes whether the call
graph connected anything, puts zod at 0.148.

**And the metric is provably insensitive to real improvement.** `#1684` added
**758 resolved call edges** to celery, 8.7% more, independently predicted before
it was measured. Coverage moved by **zero files**, 0.378 before and after.
Resolution improvements land in files that already had an edge, so the metric
saturates long before the graph stops getting better. A tool optimising this
number would take no credit for that change, and would pay no penalty for
undoing it. [Details](experiments/g2-cross-file-coverage/#the-coverage-metric-is-provably-insensitive-to-real-improvement).

**What we will and will not say about this.** We will publish the reproduction,
because a metric that cannot be reproduced from its own definition is worth
knowing about, and because we found the reading that does reproduce rather than
stopping at "it does not". We will not call it dishonest: "dependent" is loose
English, not a false statement, and the either-direction reading is a defensible
thing to want to measure. What we will not do is report our own number under
their metric and call it a win, because on a metric this saturated a win is
noise. G2's published table will carry all three columns for both tools.

Reproduce: [`experiments/g2-cross-file-coverage/`](experiments/g2-cross-file-coverage/).

---

## Second result: a denominator can hand you a win you did not earn

Coverage is a fraction, and this benchmark spent a session looking at
numerators. The denominator turned out to be where our best-looking cell came
from.

caffeine has 668 java files. We call 536 of them symbol-bearing; CodeGraph
calls 664. Session 1 read that as our gap — 128 java files, 19% of the
repository, producing no symbol at all — and called it the largest concrete
finding the bench had surfaced.

Adding two more arms settled it. **code-review-graph independently counts 536,
exactly as we do**; Graphify counts 664, as CodeGraph does. All four agree on
668 java files, so nobody's walk is at fault. Classifying the 128
([`probe_symbol_gap.py`](experiments/probe_symbol_gap.py)):

| what the 128 files are | count |
|---|---:|
| `package-info.java` — declares a package, no type | 123 |
| `public @interface X {}` — annotation declarations | 5 |

So it is a definitional disagreement about `package-info.java`, plus five real
misses of ours.

**And it was inflating our score.** Those 123 files cannot receive an incoming
call edge, and measurement confirms that under CodeGraph not one of them does.
They are pure denominator padding:

| caffeine, incoming `calls` | repowise | codegraph |
|---|---:|---:|
| each arm's own denominator | 0.608 (326/536) | 0.517 (343/664) |
| **shared 536-file denominator** | **0.608** | **0.640** |

On its own denominator we lead by 9 points. On a fair one **CodeGraph leads by
3**. The intervals overlap, [56.6, 64.9] against [59.8, 67.9], and a
two-proportion test is not significant, so the honest answer is a tie — but the
0.608-against-0.517 row is retired, and it was ours.

This is why every arm implements `files_seen()` and why every cross-arm
comparison intersects on it before computing anything.

---

## Third result: the resolvers, adversarially

G5 mutates a repository in a way whose correct answer is known in advance. It
is the one experiment here that a coverage number cannot approximate, because a
resolver that binds by bare name and one that models scope score identically on
unmutated source.

gitleaks, Go, at `3594ba75`:

| arm | M1 decoy twin | M2 consistent rename | M3 shadowing |
|---|---|---|---|
| repowise | pass | pass | **fail** |
| codegraph | pass | pass | **fail** |
| graphify | untestable | untestable | untestable |
| code-review-graph | untestable | untestable | untestable |

**M1** adds a same-named declaration in a package nothing imports; 319 call
sites name that symbol. Neither we nor CodeGraph put a single edge on the
decoy. **M2** renames a symbol everywhere, isomorphically; both graphs come
through with zero edges lost and zero gained across 284 affected edges. Neither
tool is a name-matcher.

**M3** shadows an imported package with a local variable. Both of us still bind
the call through it. Ours resolves `secrets.NewSecret(...)` to the package
function after `secrets` has become an `int`; CodeGraph does the same. Nobody
in this field passes it.

`untestable` is not a pass and is never reported as one. Graphify and
code-review-graph resolved no edge to the mutated symbol at the baseline, so
the mutation cannot change their answer. An arm that resolves nothing cannot be
tricked, and scoring that as a pass would rank it above one that resolves
almost everything.

Reproduce: [`experiments/g5-invariance/`](experiments/g5-invariance/).

---

## Fourth result: we were wrong about build cost

This page previously said we lose on build cost and expect to keep losing. That
was carried over from the full-index comparison in
[docs/BENCHMARKS.md §6](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md),
which is a different denominator. Measured on graph construction alone, across
six repositories with a discarded warmup each:

| repo | repowise | codegraph | graphify | code-review-graph |
|---|---:|---:|---:|---:|
| dub (4,066 files) | **11.6s** | 22.3s | 176.5s | 56.5s |
| caffeine | **10.3s** | 11.2s | 49.8s | 33.9s |
| Ocelot | 6.1s | **4.3s** | 39.5s | 11.3s |
| celery | 4.6s | **3.9s** | 31.3s | 22.1s |
| zod | **3.4s** | 3.9s | 16.2s | 11.6s |
| gitleaks | **1.7s** | 1.7s | 5.0s | 3.7s |

Peak RSS, from a Windows job object over the whole process tree:

| repo | repowise | codegraph | graphify | code-review-graph |
|---|---:|---:|---:|---:|
| dub | **141 MB** | 1,752 MB | 1,534 MB | 373 MB |
| caffeine | **161 MB** | 1,534 MB | 995 MB | 477 MB |
| gitleaks | **54 MB** | 708 MB | 834 MB | 369 MB |

We are fastest on four of six including both largest, and use roughly a tenth
of CodeGraph's memory. Our figures come from the `repowise-subprocess` arm,
which builds the same graph in a child process precisely so it can be measured
the way every competitor already was — in process there is no child to attach a
job object to, and this column was empty until now.

Two things to keep saying anyway. This is graph construction only: no
documentation, no embeddings, no health pass, and it must never be quoted
beside the full-index row. And CodeGraph produces more distinct call edges than
we do on three of the six, so seconds alone is not a quality claim.

### "But isn't CodeGraph Rust and repowise Python?"

Partly, and it matters less than the framing suggests. Their README leads with
"Kernel powered by Rust" and "the fastest complete code graph", and we are a
Python program that came out ahead on four of six repositories. That deserves
an explanation rather than a victory lap, because the obvious reading — that a
scripting language beat compiled code — is not what happened.

**The Rust is in the parser, and their own README scopes it that way**: parsing
and extraction run in a compiled kernel with "one boundary crossing per file".
Resolution, graph assembly and storage stay in JavaScript on a bundled Node 24
runtime. Our side is the same shape: tree-sitter is compiled C, and Python only
orchestrates it. **On the hot loop both tools are running native code.** The
language difference lives in the glue, and the glue is not where the seconds
are.

**They persist a real index and we do not.** CodeGraph writes 115 MB of SQLite
on dub; our graph lives in memory and is discarded when the process exits.
Serialisation is genuine work that our number excludes, and that is a caveat in
their favour, not ours. The honest sentence is "faster to build the graph", not
"faster", and this benchmark should never write the second one.

**The memory gap is the more solid result.** 141 MB against 1,752 MB is a wide
enough margin that no accounting choice closes it, and it is mostly structural:
a V8 heap plus a bundled runtime plus holding a graph in memory to write it out,
against a build that streams.

**Their headline claim is about a number we have not measured.** The speed
CodeGraph advertises is mostly *incremental re-sync* — roughly 0.3s to fold one
saved file into a 4,400-file project, never re-scanning the tree. That is a real
capability, it is a different measurement from a cold build, and **we expect to
lose it**. It is listed as unmeasured in G6 rather than quietly omitted.

---

## Fifth result: at six repositories we looked better than we are

Every per-language number this page carried before was a fact about one
repository wearing a language's name. The six head-to-head repositories are
typescript x2, java x1, csharp x1, python x1, go x1 — four of five languages
resting on a single repository, which is precisely what
[rule 13](METHODOLOGY.md) forbids.

The corpus is now **35 repositories across 11 languages**, pinned in
[`corpus/corpus.lock`](corpus/corpus.lock), ten of them at n=3 with a library,
an application and a framework each. Swift has only a library and is reported
as carrying no language-level claim.

Median incoming cross-file `calls` coverage, per language, ours against the
strongest competitor:

| language | n | repowise | CodeGraph | Graphify | code-review-graph | |
|---|---:|---:|---:|---:|---:|---|
| java | 3 | **0.606** | 0.434 | 0.340 | 0.261 | ours +0.172 |
| csharp | 4 | 0.291 | 0.280 | 0.128 | 0.000 | tie |
| go | 3 | 0.639 | 0.639 | 0.466 | 0.000 | tie |
| php | 3 | 0.334 | 0.317 | 0.135 | **0.443** | tie |
| ruby | 3 | 0.337 | 0.318 | 0.197 | 0.000 | tie |
| typescript | 3 | 0.390 | **0.426** | 0.064 | 0.434 | theirs +0.036 |
| kotlin | 3 | 0.439 | **0.498** | 0.350 | 0.067 | theirs +0.060 |
| python | 3 | 0.378 | **0.489** | 0.333 | 0.329 | theirs +0.111 |
| cpp | 6 | 0.336 | **0.496** | 0.184 | 0.000 | theirs +0.161 |
| rust | 3 | 0.342 | **0.513** | 0.118 | 0.200 | theirs +0.170 |
| swift | 1 | 0.388 | 0.582 | 0.368 | 0.000 | *n=1, no claim* |
| **median of the ten at n>=3** | | **0.360** | **0.462** | 0.197 | 0.200 | |

**On this reading we win one language, tie four and lose six.** That is the
opposite of the impression six repositories gave, and it is published because a
benchmark that only reports the corpus where it wins is an advertisement. On the
shared denominator below — the fair comparison — it becomes **one ours, six
tied, four theirs**, and the four that survive are the real finding.

### On the denominator both tools agree on

The table above is each arm's own metric on its own population, which is what
CodeGraph's published metric is and why it is reproduced that way. It is **not a
comparison**: two arms disagree about which files can carry an edge at all. Our
denominator is smaller than theirs in 8 of 11 languages and larger in exactly
two — cpp and rust — and that asymmetry has already reversed one headline on
this page, when 123 `package-info.java` files padded the peer's caffeine
denominator.

Recomputed on the files **both** arms call symbol-bearing, within the walk they
share, pooled per language with 95% Wilson intervals:

| language | repos | shared denom | ours | theirs | verdict |
|---|---:|---:|---:|---:|---|
| java | 3 | 1,820 | **0.685** | 0.495 | **ours** |
| csharp | 4 | 2,099 | 0.334 | 0.304 | tie |
| go | 3 | 1,382 | 0.527 | 0.551 | tie |
| php | 3 | 4,316 | 0.271 | 0.280 | tie |
| ruby | 3 | 322 | 0.332 | 0.348 | tie |
| swift | 1 | 98 | 0.388 | 0.582 | tie |
| typescript | 3 | 617 | 0.379 | 0.392 | tie |
| python | 3 | 824 | 0.373 | **0.455** | theirs |
| kotlin | 3 | 3,384 | 0.447 | **0.591** | theirs |
| rust | 3 | 1,995 | 0.341 | **0.489** | theirs |
| cpp | 6 | 2,035 | 0.226 | **0.419** | theirs |

**One ours, four theirs, six tied** on non-overlapping intervals — against one
win, four ties and six losses on the own-denominator reading. TypeScript and
Swift move from loss to tie once the populations match and the intervals are
honoured.

**cpp and rust do not move, and cpp gets worse.** Pooled, we go from 0.336 to
0.226 there, because the shared denominator removes files only we counted. So
those two are real resolution gaps and not denominator artifacts, which is what
makes them the right place to spend hand-graded precision rows next.

Two things the pooled figures hide, printed rather than left in the data:

* **cpp's pooled 0.226 against a per-repository median of 0.357** is one
  repository doing the work: `aria2` carries 1,118 of the 2,035 shared files at
  0.200. Weighting by size is what pooling means, but a reader should see it.
* **`nlohmann-json` is hard for everyone** — 0.108 us, 0.143 them. A
  header-only template library is close to the worst case for file-granular
  call resolution on both sides.

Java strengthens on the fair denominator rather than weakening: **0.685 against
0.495**, our clearest result on the page.

### The two things that stop this being the whole story

**Coverage is not correctness, and this page says so first.**
[Rule 1](METHODOLOGY.md) exists because a change that raises coverage and lowers
precision is a regression wearing a win's clothes; the same holds between two
tools. Our defensible claim has always been precision per edge, not more edges.

The hand-graded audit now covers **nine languages at 230/270 = 85.2%**
[80.5, 88.9], 30 rows each spread over three repositories, every row read from
source. That is **down** from the 89.3% this page used to quote, and it is worth
more: 89.3% was five languages chosen for continuity with earlier work, all of
them ones where we are strong.

**The peer has not been read on the four new languages, so there is no
head-to-head precision claim there.** The 62.0% figure for CodeGraph is a
five-language number and it must not be printed beside the nine-language row.
Where both sides have been graded, we lead by roughly 27 points pooled; on cpp,
rust, kotlin and swift nobody has read their edges at all.

**And rust is weak on both axes at once.** It is our worst precision cell,
**20/30**, where 6 of the 10 misses are prelude and std name collisions
(`.unwrap()`, `Ok(...)`) landing on unrelated in-repo symbols and 3 are macros
captured as calls; and it is a confirmed shared-denominator coverage loss,
0.341 against 0.489. Coverage and precision usually trade against each other,
so a language losing both is a genuine resolution gap rather than a metric
artifact. cpp is the same shape with weaker evidence — its 86.7% is carried by
one repository (fmt 10/10, aria2 10/10, Crow 6/10) and is not a language claim.

So the honest statement is: **we lead on precision where both sides have been
graded, we trail on coverage on four languages after the fair recount, and rust
is genuinely weak on both.** What is missing is not more of our own rows — it is
the peer's rows on cpp, rust, kotlin and swift, and a test of whether the
coverage gap is in **resolution reach** rather than in which files we call
symbol-bearing. Two candidate explanations for that gap, receiver typing and
symbol extraction, have each been measured and refused.

### A language does not have "a" rate

The spread across three kinds is reported instead of a mean, because the
disagreement is the finding. On our arm TypeScript reads **0.138 on zod and
0.589 on hono** — a 0.451 spread. Rust reads 0.175 on serde and 0.490 on
ripgrep. Quoting either end as "the TypeScript number" is the mistake the
three-kinds rule was written to prevent, and we nearly made it.

### What a claimed language delivers, for the tool that claims most

code-review-graph **walks the language and resolves zero cross-file call edges**
on 17 of the 35 repositories: all 6 cpp, all 4 csharp, all 3 go, all 3 ruby and
Alamofire. It resolves 12,395 on TypeScript. That is a per-language capability
gap rather than a broken run, and printing the zero is the entire point of G7 —
every tool in this field claims 20 to 40 languages and none says what a claimed
language actually produces.

Our own worst rows are the other half of the same table. On zod we declare
symbols in only **269 of 400** TypeScript files and on hono **260 of 382**; a
file that yields no symbol cannot contribute an edge, so a third of those
repositories is unreachable before edge resolution is even attempted.

### Provenance

Measured at **`58576af0`** — `repowise 0.43.0+dev`. There is no 0.44.0 tag; the
latest release tag is `v0.43.0` and `origin/main` carries unreleased work past
it.

Competitor artifacts were restored from the prebuild cache and warmup was
skipped, so this run is stamped `publishable: false` **for cost**. Coverage is
unaffected and the tables above stand: a restore reproduces every set the
protocol exposes byte for byte, which `smoke.py` asserts by storing and
restoring a real index with the SQLite sidecars deliberately present. See
[rule 12](METHODOLOGY.md). **No cost number on this page comes from that run.**

## How this is organised

```
graph/
  README.md            this page: the results index
  METHODOLOGY.md       the rules every experiment follows, and why each one exists
  corpus/              the repositories, their pins, and why each is in
  arms/                one page per tool: version, how it is built, what it emits
  lib/                 shared readers and statistics, no experiment logic
  experiments/<id>/    PREREGISTRATION.md, README.md with the result, run scripts
results/graph/<id>/    raw output, one directory per run
```

One experiment per directory, each self-contained, each with its prediction
written down before the run. Nothing on this page cites a number that does not
have a path under `results/graph/` behind it.

## What a reader should be suspicious of

* **The head-to-head six are still six.** G2's paired arm-versus-arm rows and
  every cost figure come from those, and a paired sign test over six cannot
  reach significance below a 6-0 sweep. The 35-repository corpus fixes the
  breadth problem, not that one: it is measured with competitor artifacts
  restored from cache, so it carries coverage and not cost.
* **Ten languages at n=3 is still n=3.** Three repositories bound the spread;
  they do not estimate a language. Swift is n=1 and says so.
* **Every tool is held to a metric one of them designed.** G2 is CodeGraph's
  metric and we are reproducing it. G1, G4 and G5 are ours, and a reader should
  discount them the same way.
* **Our worst cell is Java**, at roughly 67% edge precision, and it is also our
  largest edge count. [METHODOLOGY.md](METHODOLOGY.md) explains why it stands.
* **Two arms are being read through an adapter we wrote.** Graphify's call
  edges are 93% `INFERRED` by its own tagging, and code-review-graph stores
  unresolved callees in the same table as resolved ones, so we filter to the
  ones that resolve. Both choices are argued in [arms/](arms/) and both change
  those tools' numbers substantially. A reader who disagrees with either should
  say so; the counts either way are recorded in every result file.
* **Three of our own results have moved against us** — the caffeine coverage
  cell, the build-cost claim, and now the per-language coverage picture, which
  at 35 repositories has us losing six languages and winning one. All three are
  above rather than in a changelog.
