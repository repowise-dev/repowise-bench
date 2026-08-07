# Head to head against the agent-context field

Six tools that claim to give a coding agent better context than grep, measured on
the same repositories, at the same pinned commits, with the same questions, each
one given its own full advertised tool surface.

**Read [THE_LOOP.md](THE_LOOP.md) if you only read one thing.** The numbers below
are its output. The loop, and specifically the list of ways a wrong number here
looks exactly like a right one, is the part that transfers.

---

## The field

| tool | what it is | index | tools served | page |
|---|---|---|---:|---|
| **repowise** | LLM-authored wiki over a symbol graph, hybrid retrieval | `.repowise/` SQLite + LanceDB | 11 | [arms/repowise.md](arms/repowise.md) |
| **CodeGraph** | code graph with one natural-language entry point | `.codegraph/` SQLite | 1 | [arms/codegraph.md](arms/codegraph.md) |
| **code-review-graph** | graph plus generated wiki, built for PR review | `.code-review-graph/` SQLite | 30 | [arms/code-review-graph.md](arms/code-review-graph.md) |
| **Graphify** | JSON code graph with community and hub analysis | `graphify-out/graph.json` | 10 | [arms/graphify.md](arms/graphify.md) |
| **Serena** | language-server wrapper, symbolic navigation | none, on demand | 29 | [arms/serena.md](arms/serena.md) |
| **CocoIndex Code** | AST chunking, local embeddings, vector search | `.cocoindex_code/` SQLite + sqlite-vec | 1 | [arms/cocoindex.md](arms/cocoindex.md) |

Each page says what the tool is, what it is good at, what it is bad at, how many
of its tools it was given and why, and **every setup trap found while making it
work**. Those traps are the most reusable thing here: four of the six had at
least one step that, if missed, produces a clean zero rather than an error.

---

## Who wins what

Nobody wins everything, and a page that said otherwise would be a marketing page.

**We lead coverage.** `get_answer` returns the most gold files of anything in the
field, 0.876 on the sealed django half against CodeGraph's 0.610, 19 wins to 1
loss head to head at p = 0.00004.

**CodeGraph is the genuine second, on both layers.** 0.610 coverage, and -24.4%
agent output tokens against our -31.6% in the agent loop. It is also **20x
cheaper to build** than we are. The honest reading is that we lead a field in
which more than one tool works.

**code-review-graph leads precision**, 0.240 against our 0.087. Part of that is
mechanical, because it serves 5.4 files to our 19.2 and precision rises for
whoever returns fewest. That is why files-served is a column here.

**CocoIndex is the only tool in the field that retrieves non-code gold**, and it
beats us on file types we do not index at all. See below.

**Graphify serves 34.5 files per query to reach 0.546**, which is the worst of
both columns. Its results also come with the caveat that our own extractor once
scored it 0.012 against a true 0.539, so its numbers get read carefully here.

**Serena is a different category and it says so.** It is a language-server
wrapper with **no retrieval-by-question tool at all**: `search_for_pattern` is
the closest thing it offers. That asymmetry is the finding, not a rigging, and it
is why Serena carries no Layer A coverage row. In the agent loop it is the
interesting counter-case: less output than a bare agent while calling tools 42%
*more* often. Busier, not leaner.

**We lose indexing time, by about 22x.** Published with the work-done split
rather than without it.

---

## The result nobody in the field had published: a build-cost curve

Five tools, 15 instances of one repository spanning a **12x size range** (2,322
to 28,346 source files at their own pinned commits), built serially with nothing
else running.

Wall seconds at both ends of the range:

| source files | repowise | CocoIndex | Graphify | code-review-graph | CodeGraph |
|---:|---:|---:|---:|---:|---:|
| 2,322 | 166.2 | 150.1 | 83.7 | 38.2 | **22.9** |
| 28,346 | 1,866.0 | 1,506.9 | 1,307.6 | 469.1 | **465.8** |

Fitted on log-log, per arm:

| arm | exponent | R² |
|---|---:|---:|
| **CocoIndex** | **0.892** | 0.933 |
| **repowise** | **0.906** | 0.968 |
| CodeGraph | 1.100 | 0.999 |
| code-review-graph | 1.186 | 0.999 |
| Graphify | 1.193 | 0.994 |

**R² of 0.999 on 13 points is essentially a perfect power law**, which is what
makes the two sublinear arms interesting and also what makes CocoIndex's 0.933
worth flagging: its per-instance cost is materially noisier than any other tool's
here, with visible inversions (1,525s at 22,448 files against 1,105s at 24,606),
and this run does not establish why.

**Our sublinear exponent is sublinear WORK, not efficiency, and we will not
publish it as efficiency.** Symbols scale at 0.669 and pages at 0.708 while build
time tracks graph-node count almost exactly (0.906 against 0.907). **Symbol
density halves across the range**: 0.86 symbols per file at 2,322 files against
0.41 at 28,346. On a 12x larger repository we extract less than half as many
symbols per file. That is a product finding against us, with a named mechanism,
and it is why the curve is published with this paragraph attached.

**Never quote a single-instance cost ratio from this bench.** Ours moved 16.7x,
then 4.0x, then about 11x in one session, every time from machine contention
rather than new data. Contention is arm-specific: two contaminated instances
inflated CodeGraph 2.5 to 3.3x and repowise only 1.03 to 1.20x. Use the curve.

---

## What each index can rank at all

Read off each arm's own database, from the table a query actually ranks against,
across 15 instances. This distinguishes a **ranking failure** from a **file-type
exclusion**, which are different facts about a tool.

| arm | retrieval surface | indexed files | `.md` | `.json` |
|---|---|---:|---:|---:|
| Graphify | `nodes[].source_file` | 259,588 | **10,276** | **1,219** |
| CodeGraph | `files.path` | 248,250 | 0 | 0 |
| code-review-graph | `nodes.file_path` | 248,095 | 0 | 0 |
| repowise | `wiki_pages.target_path` | 44,903 | **0** | **0** |
| CocoIndex | `code_chunks_vec.file_path` | 30,133 *(largest instance)* | 798 | 942 |

On a corpus where **21% of the gold files are `.md` or `.json`**, this is the
difference between five arms that could reach those files and three that could
not.

**We are one of the three, deliberately.** repowise indexes code files only, by
choice. On documentation-heavy repositories the docs outweigh the code (fastapi
carries more documentation than code) and indexing them polluted the index and
degraded retrieval for the code questions the tool exists to answer. So our zero
on documentation gold is a **design boundary with a measured cost**, not a
ranking failure and not a gap awaiting a fix.

**CocoIndex made the opposite trade and this bench shows both sides of it in one
row.** It is the only arm of six to retrieve any documentation gold, and it pays
for that on code gold, where it retrieves a sixth of what we do. That trade is
the single most useful thing adding a sixth tool produced, and it is the argument
for adding arms **before** publishing rather than after: an arm added afterwards
either forces a revision or quietly never happens.

That same arm also **broke a finding this bench had already written down**. One
instance carrying 18.4% of the gold had been scored 0.000 by all five arms and
hand-checked, and was recorded as a genuine unanimous retrieval failure. CocoIndex
returned a gold file on it at **rank 1**, and the file was markdown. The five
zeros stand exactly as measured; the generalisation drawn from them never held.
**Only another arm could have caught that.**

---

## What is measured, and what is not yet

| corpus | language | arms | status |
|---|---|---:|---|
| `django/django`, 112 instances, 70 dev / **42 sealed** | Python | 5 | **published**, sealed half evaluated once |
| `django/django`, 48 questions, agent loop | Python | 6 | **published**, Codex and Claude Code |
| `mui/material-ui`, 45 instances, 15 dev / **30 sealed** | JavaScript / TypeScript | 6 | **development half graded, sealed half untouched** |

**The mui coverage row is deliberately not on this page.** The 30 sealed
instances have never been run, and publishing a development-half figure is
exactly what the split exists to prevent. What is published from that run is
everything that is not an outcome variable: the build-cost curve above, the index
surfaces above, the setup traps on each arm page, and the gate failures in
[THE_LOOP.md](THE_LOOP.md).

The JavaScript/TypeScript corpus is also **JavaScript-majority rather than
TypeScript**, which is worth knowing before anyone reads it as a TypeScript
result: mui is 88% `.js`/`.jsx` at every pinned commit, and its gold files are 19
`.js`, 11 `.ts`, 5 `.md`, 3 `.json` and **zero `.tsx`**.

**Also not measured, and stated rather than implied:**

- **CocoIndex's advertised 70% token saving.** This layer measures file coverage
  against gold spans. A token claim needs an agent harness, and every token
  column this bench has produced under Claude Code failed its own control. No
  coverage number here is a verdict on that claim in either direction.
- **Competitor tool versions**, deliberately not collected during timed builds
  and still not collected for every arm.
- **Long multi-hour tasks.** Every agent-loop question here is answered in four
  to seven turns.

---

## Levels of depth

Stop wherever you like. Each level is the evidence for the one above it.

1. **The headline tables**, on this page and in [../README.md](../README.md).
2. **One page per competitor**, in [arms/](arms/): what it is, what it serves,
   what it is good and bad at, and every setup trap.
3. **The method and its gates**, in [THE_LOOP.md](THE_LOOP.md).
4. **The arm definitions**, in [../configs/arms.yaml](../configs/arms.yaml). Every
   launch command, every allowlisted tool, every exclusion with its reason. This
   is the file to read if you think an arm was set up unfairly.
5. **The pre-registrations**, `../configs/*.PREREGISTRATION.md`, each committed
   before its run spent anything.
6. **Graded cells and verbatim responses**, under
   [../results/bakeoff_2026_08/](../results/bakeoff_2026_08/). Every raw response
   is on disk beside the paths the extractor pulled out of it, which is what
   makes level 2's trap list checkable rather than anecdotal.
7. **[../repro/README.md](../repro/README.md)**, which says per claim what it
   costs to reproduce, how long it takes, and which ones need credentials we
   cannot hand you.
