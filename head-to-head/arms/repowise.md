# repowise

The tool this bench belongs to. It is held to the same rules as every other arm,
and this page is written to be as useful to a skeptic as the other five.

| | |
|---|---|
| What it is | An LLM-authored wiki over a symbol graph, served by hybrid retrieval |
| Index | `.repowise/wiki.db` (SQLite) plus `.repowise/lancedb` (vectors) |
| Tools served | **11** by default in single-repo mode |
| Tools allowlisted | **7 of 11**, every exclusion named below |
| Build, django | 366.8s with prose off, 1,058s with prose on |
| Cost exponent | 0.906 (R² 0.968) |

## Two rows, never pooled

repowise is scored as **two arms**, and they are never averaged:

| | what it is | sealed django coverage | precision | files served |
|---|---|---:|---:|---:|
| `get_answer` | one synthesized, cited answer. What a user actually runs | **0.876** | 0.087 | 19.2 |
| `search_codebase` | ranked results, no LLM. The like-for-like control | **0.742** | **0.168** | **8.2** |

**They differ by 0.13, so pooling them would publish a number neither
configuration produces.** `get_answer` finds the most. `search_codebase` is the
most efficient per file served in the entire field: 0.742 coverage from 8.2
files. The zero-LLM row exists because the competitors are entitled to a
comparison that does not include a synthesis step they do not have.

## What it is good at

**Coverage.** First in the field on both layers: 0.876 against CodeGraph's 0.610
on the sealed django half, 19 wins to 1 loss head to head, p = 0.00004.

**Agent work saved.** -31.6% output tokens against a bare agent on the
48-question Codex run, largest in the field, reaching an answer in 3.8 tool calls
against the bare agent's 7.2. The saving is larger on harder questions (34.3%
against 27.2%, correlation +0.379), which is the mechanism showing through:
pre-computed structure replaces exploration, and harder questions have more
exploration to replace.

**Robustness to machine contention**, which is a benchmarking property rather than
a product one, but it is measured: two contaminated instances inflated CodeGraph
2.5 to 3.3x and repowise only 1.03 to 1.20x.

## What it is bad at

**Indexing time, by about 22x.** 366.8s against CodeGraph's 16.4s on django, and
1,058s with prose generation on. Four more layers are built in the same pass, and
that is an explanation rather than an excuse.

**Precision.** 0.087 against code-review-graph's 0.240. We return 19.2 files where
it returns 5.4. If you are paying per file read, the `search_codebase` row is the
one to compare.

**We index no documentation, by choice, and it costs us a measurable amount.**
`wiki_pages.target_path` carries **zero** `.md` and `.json` rows across every
instance measured, against 10,239 markdown files the walker saw. On a corpus
where 21% of gold files are documentation, that fifth is unreachable by
construction, and any coverage figure we print on such a repository is depressed
for a reason a reader cannot see.

The reason is measured rather than aesthetic: on documentation-heavy repositories
the docs outweigh the code (fastapi carries more documentation than code) and
indexing them **polluted the index and degraded retrieval for the code questions
the tool exists to answer**. So it is a design boundary with a stated cost, not a
gap awaiting a fix. CocoIndex made the opposite trade and this bench publishes
both sides of it.

**Our sublinear cost exponent is sublinear WORK, not efficiency.** Symbols scale
at 0.669 and pages at 0.708 while build time tracks graph-node count almost
exactly (0.906 against 0.907). **Symbol density halves across a 12x size range**:
0.86 symbols per file at 2,322 files against 0.41 at 28,346. On a much larger
repository we extract less than half as many symbols per file. Two named causes
are logged and deliberately unfixed, because both were found on a held-out corpus
and fixing then reporting would be tuning against the set.

## Setup traps, ours included

**1. The first call after a server start never returns.** The client giving up is
what unblocks it, and the next call answers in about 1.3 seconds. Every repowise
arm therefore issues one cheap `warm` call whose result is discarded, with a 15
second timeout. 15 seconds abandoned costs the same as 600 abandoned.

**2. The server is launched with exactly the config's `env` block and inherits
nothing.** The index is built with an OpenAI embedder, which reads its key from a
provider config file; the server reads the key from its own environment. Nothing
bridged the two, so a server launched the obvious way queried an
OpenAI-embedded index with mock vectors and **answered from full text alone while
reporting itself healthy**.

**3. And that failure has no warning field, which is the worst part.** `embedder`,
`embedder_degraded` and `retrieval_degraded` all read `None`, an unbroken row,
because that metadata is emitted when the embedder is healthy **or unresolved**.
The only evidence anywhere was a top-level `degraded: "no-llm-provider"` field
the harness had never read. With the key set, gold rank on the probe instance
moved 2 to 1. The runner now **refuses to start** rather than warn.

**4. "Build once, serve both layers" holds for the competitors and fails for
us.** The retrieval layer runs `--no-prose`; the agent layer is defined by prose.
So our arm needs two builds per instance where every competitor needs one. A
smoke run once built the wrong one and published timings that included LLM
generation and a "$0 LLM spend" that was not zero; **four readings were
retracted**. There is now a separate `repowise-layera` arm carrying every flag
explicitly.

**5. An unguarded `init` repoints the operator's editor**, because there is one
global repowise MCP registration. Every arm sets `REPOWISE_SKIP_EDITOR_SETUP=1`
and `DO_NOT_TRACK=1`.

## Exclusions

7 of 11 tools allowlisted. The 4 exclusions, each about the shape of a cell:

| excluded | why |
|---|---|
| `list_repos` | workspace enumeration; one repo is under test and the server runs single-repo |
| `get_change_risk` | scores a commit or a diff range. A cell is one question at one commit with no diff in it, the same exclusion Graphify's `get_pr_impact` gets |
| `get_health` | code-health scoring is a different product surface and a different question shape |
| `get_dead_code` | a reachability report over the whole repository, likewise |

**The server is left at its own default surface** rather than pinned smaller. The
cut is made on the client allowlist, uniformly, so every arm's server advertises
what its author made it advertise. Ablation arms that do pin the server
(`repowise-lean`, 4 tools) are labelled as ablations and are not the headline arm.

## Where the numbers this page cannot flatter are

- The run where we came **last at 0.228** against CodeGraph's 0.609, published
  before the cause was known:
  [`../../results/bakeoff_2026_08/rung8/`](../../results/bakeoff_2026_08/rung8/)
- The quality column, where a blind judge scored every tool in the field
  including ours a fraction *below* a bare agent
- The Opus run, published inconclusive by one cell against a rule fixed before
  any spend: [`../../results/bakeoff_2026_08/rung6/`](../../results/bakeoff_2026_08/rung6/)
- Arm definitions: [`../../configs/arms.yaml`](../../configs/arms.yaml),
  `repowise`, `repowise-layera`, and the ablations
