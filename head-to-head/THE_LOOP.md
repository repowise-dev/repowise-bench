# The loop

The numbers are the output of this. The loop is the product.

Every stage below has a gate on it, and **every gate exists because something got
past its predecessor and produced a plausible wrong number**. None of them were
designed in advance. Each is named with the failure that put it there, so you can
judge whether it is a real precaution or a superstition.

```
  ┌─ 1. PRE-REGISTER ─────────────────────────────────────────────────┐
  │  the draw, the seal, the gate, the reporting rule, the prediction │
  │  committed as its own commit, before anything is installed        │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 2. DRAW AND SEAL ────────────▼───────────────────────────────────┐
  │  split instances by id, stratified on gold-file count             │
  │  development half is worked on · sealed half is evaluated ONCE    │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 3. STAGE ────────────────────▼───────────────────────────────────┐
  │  each instance checked out at its OWN pinned base_commit          │
  │  and asserted there before anything is built                      │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 4. ONE WORKTREE PER ARM ─────▼───────────────────────────────────┐
  │  lb-<arm>-<instance>-<repo>, never shared                         │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 5. BUILD ────────────────────▼───────────────────────────────────┐
  │  one index per (arm, instance), serial, nothing heavy beside it   │
  │  stamped on exit 0, so a killed build is not a built one          │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 6. GATE ─────────────────────▼───────────────────────────────────┐
  │  a. ALIVE          the server serves the tools the arm names      │
  │  b. EXTRACTOR      proved by hand on one real response, per arm   │
  │  c. SELF-TEST      grade a known-perfect and a known-wrong first  │
  │  d. NOT DEGRADED   no arm answering from a fallback path          │
  │  e. SURFACE        what file types can this index rank at all     │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 7. QUERY ────────────────────▼───────────────────────────────────┐
  │  one call per (arm, instance), every argument pinned and recorded │
  │  raw response written verbatim beside what was extracted from it  │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 8. GRADE ────────────────────▼───────────────────────────────────┐
  │  against ContextBench gold spans. Deterministic. No LLM judge.    │
  └───────────────────────────────┬───────────────────────────────────┘
                                  │
  ┌─ 9. REPORT ───────────────────▼───────────────────────────────────┐
  │  median beside mean · precision and files-served beside coverage  │
  │  pre-registration quoted back, including predictions that failed  │
  └───────────────────────────────────────────────────────────────────┘
```

---

## 1. Pre-register

The draw, the seal, the gate, the reporting rule and the prediction go into a
file, and that file is **its own commit, before anything is installed**. One
`configs/*.PREREGISTRATION.md` per scored run.

**Why.** A favourable result is very easy to turn into a slightly different
question. Committing the reading first means a run that comes back against you
comes back against you in public.

It is not decoration. The Opus run was pre-registered at `>=12 model / <=6
harness / 7 to 11 inconclusive`, came back at **7**, and was published as
inconclusive by one cell. `n` was fixed at 15 in a commit before any cell was
read, specifically so a favourable 15 could not become a 48.

**And the prediction gets quoted back, including when it fails.** The mui run
predicted that retrieval coverage degrades with repository size, from a measured
mechanism. It does not (r = -0.147, p = 0.60). That is written into the result
next to the prediction.

## 2. Draw and seal

Split by instance id, stratified on gold-file count, before anything is looked
at. The development half is worked on. **The sealed half is evaluated once, at
publication.**

**Why.** Without it, "we fixed a bug and the number went up" and "we tuned
against the questions" are the same sentence.

The seal is the only reason the django result means anything. We came **last at
0.228**, found a query-time gate discarding candidates before ranking, fixed it,
and `get_answer` went to 0.810 on the development half. That alone proves
nothing. What proves something is that the **sealed half scored higher, 0.876**,
and that CodeGraph, which nobody tuned against either half, scored 0.6093 and
0.6095 on them. Overfitting moves those the other way.

A pooled 112-instance figure is easy to compute (0.835) and is **not published**,
because averaging the halves loses the only number that matters.

## 3. Stage at the pinned commit

Every instance is checked out at its own `base_commit` and **asserted there**
before its index is built.

**Why.** These benchmarks pin one commit per instance, so an arm indexing HEAD is
answering a different question. A stale checkout is a wrong answer, not a fast
one. 75 of 75 asserted on the mui run.

## 4. One worktree per arm

`lb-<arm>-<instance>-<repo>`. Never shared. This is finding **E3**.

**Why.** Every one of these tools writes its index into a dotdir **inside the
repository it is indexing**: `.repowise/`, `.codegraph/`, `.code-review-graph/`,
`graphify-out/`, `.cocoindex_code/`. A shared checkout means each arm indexes its
predecessors' output, and **the bias favours whoever ran first, which was us**.

It costs real disk and real time (748 builds and 78 machine-hours on one run) and
there is no cheaper version of it that is still a measurement.

## 5. Build, serially, with nothing beside it

One index per (arm, instance), one at a time, stamped **only after the build
exits 0**.

**Why the stamp is separate from the marker.** Every one of those dotdirs is
created in the first second of a build. `.cocoindex_code/settings.yml` exists
before a single chunk is written. So "the directory exists" passes for a build
that was killed a second in, and the disk stamp is the real gate.

**Why serial, and why nothing heavy beside it.** Build time is a published
number, so contention is contamination. Two contaminated smoke instances inflated
**codegraph 2.5 to 3.3x** while touching **repowise only 1.03 to 1.20x**.

That asymmetry is the finding: **contention is arm-specific and cannot be
corrected after the fact**. Its consequence is that a single-instance cost ratio
from this bench is worthless. Ours moved 16.7x, then 4.0x, then about 11x in one
session, every time from contamination rather than new data. Use the fitted
curve, never one instance.

**And a killed build is cold-reset, not just un-stamped.** Several of these tools
update incrementally when their dotdir already exists, so restarting on a partial
would re-time an incremental refresh and publish it as a cold build. Faster than
the truth, and flattering to whichever tool advertises incremental indexing. One
reset even reported success it had not achieved, because a daemon still held
SQLite handles and the database reappeared under the deletion.

## 6. The gate

The pre-registered gate used to be "an index was built and is non-empty". All
arms pass that. It is much weaker than it sounds, and each item below is
something that passed it and was still wrong.

### 6a. Alive: the server serves what the arm claims

**Why.** A dead server scores identically to a bad tool.

- **code-review-graph suffixes every tool name with `_tool`.** Calling the
  unsuffixed name returned tool-absent for **84 of 84 queries**, which scores
  0.000 and reads exactly like a retrieval failure.
- **Serena needs an explicit `activate_project`** even with `--project` on the
  command line. Without it every tool answers "No active project" and the arm
  scores a clean 0.000.
- **Our own lean arm was launched with `--profile core`**, a flag the CLI does
  not have. Finding D1.

So `launch`, `served_tools`, `index`, `activate` and `warm` are all part of an
arm's definition rather than incidental plumbing. **An arm missing one of them is
not being measured, it is being defamed.**

### 6b. Extractor: proved by hand, per arm, on a real response

**Why.** Graphify writes node lines as `NODE foo() [src=path loc=L149]`. The path
pattern reading them wanted whitespace before a path, so graphify scored **0.012
MRR against a true 0.539**, a factor of 45.

Nothing in the summary row looked wrong. A broken extractor and a genuinely bad
tool are indistinguishable downstream.

So: one real response per arm, **read by a human**, and every response written to
disk verbatim beside what the extractor pulled out of it. The response shapes are
different enough that this is not optional:

| arm | response shape |
|---|---|
| repowise `get_answer` | JSON `retrieval[]`, `fallback_targets`, `candidates` |
| repowise `search_codebase` | JSON `results[].file` |
| codegraph | free prose with inline paths |
| code-review-graph | JSON `results[].file_path`, **absolute Windows paths** |
| graphify | `NODE x [src=path loc=L149]` |
| cocoindex | JSON `results[].file_path`, ranked chunks not files |

An extractor also has to **refuse an error shape** rather than fall through to a
text scan. Letting a regex mine an error message for anything path-shaped is how
a dead arm scores like a live one.

### 6c. Self-test: grade a known-perfect and a known-wrong prediction first

**Why.** This is the cheapest gate here and it caught the worst failure.

`mui/material-ui` carries a fixture path 130 characters long. Under a worktree
root already spending about 100, it crosses Windows' 260-character MAX_PATH. Git
prints `Filename too long`, fails to reset the index, removes the worktree, and
the checkout returns nothing. The grader then scored the instance
`checkout_failed` and **every arm lost it**. Not an error and not a crash:
`0/1 instances`.

It was found only because a **known-perfect prediction scored 0**. Fixed with
`core.longpaths=true` on the base clone, which works even where the machine-wide
registry key reads 0, and asserted in code before every grading pass.

### 6d. Not degraded: no arm answering from a fallback path

**Why, and this one was ours.** Our own arm was querying with **no embedder while
every health field read clean**. `embedder`, `embedder_degraded` and
`retrieval_degraded` all read `None`, an unbroken row, because that metadata is
emitted when the embedder is healthy **or unresolved**, and the unresolved branch
is the dangerous one.

The root was a checkout with no provider config, so the key resolved to nothing,
the server fell back to a mock embedder, built 8-dimension question vectors
against a 1536-dimension index, and the vector search swallowed the raise on
every query. The arm answered from full text and symbols alone. With the key set,
gold rank on the probe instance moved **2 to 1**.

The runner now **refuses to start** rather than warn. **Absence of a warning is
not evidence of health.**

### 6e. Surface: what file types can this index rank at all

**Why.** 21% of one corpus's gold files are `.md` or `.json`. An arm that does
not index documentation cannot retrieve them, and its miss reads as a retrieval
failure when it is a **file-type exclusion**. Those are different facts about a
tool and they should not share a cell.

So each arm's own index is read off disk, from **the table a query can actually
rank from**, before its number stands.

**Read the surface, not any column called path.** The first version of this check
was a generic sniffer and it reported the **opposite** of the truth for codegraph,
whose `nodes.name` holds import specifiers, so `.json` appears 196 times there
while `files.path` has none.

| arm | retrieval surface |
|---|---|
| repowise | `wiki_pages.target_path` |
| codegraph | `files.path` |
| code-review-graph | `nodes.file_path` |
| graphify | `nodes[].source_file` |
| cocoindex | `code_chunks_vec.file_path`, a sqlite-vec virtual table |

## 7. Query, with every argument pinned and recorded

One call per (arm, instance). Any parameter that can change the index is pinned,
and **what was actually sent is recorded per cell** rather than what the runner
intended to send.

**Why.** cocoindex's only tool takes `refresh_index`, which **defaults to true**:
an unmodified call reindexes before answering, billing a rebuild to the cell and
making the index a variable mid-run. code-review-graph needs `provider` and
`model` on every search call or it silently answers with `search_mode: "none"`,
having reported "Semantic search is now active" at build time.

The launch directory is recorded for every arm too, not just the one that needs
it, because **a row that cannot name the directory its server ran in cannot rule
out having answered about a different repository**. One arm in this field
(`ccc mcp`) takes no project argument at all and resolves upward from the working
directory, which means a mis-pointed server *answers* rather than failing.

## 8. Grade

Against ContextBench `gold_context`. Deterministic, no LLM judge, no agent, no
spend. This is the cheapest layer to make credible, which is why it goes first.

## 9. Report

- **Median beside mean.** On one corpus the gold counts are 7, 5, 5, 4, 3, 3, 2,
  2 and then seven 1s, so a mean is carried by a few instances.
- **Precision and files-served beside coverage**, never averaged in. Precision
  rises for whoever returns fewest files, so a precision column without a
  files-served column beside it is a ranking of verbosity.
- **Two repowise rows, never pooled.** `get_answer` and `search_codebase` differ
  by 0.16, so pooling would publish a number neither configuration produces.
- **Never a pooled percentage alone at small n.** It travels with the
  mean-of-per-item value, the median, and the largest single item's share of the
  total. **Where pooled and mean-of-ratios disagree in sign, the number is an
  artifact and is reported as one.** One arm posted -4.2% pooled against a
  control having never called its server once; its median difference was -4
  tokens, one question supplied 108% of the total, and the mean-of-per-question
  summary came out **+1.5%**, the opposite sign.
- **Do not drop the hardest instance.** One mui instance carries 18.4% of the
  gold and the whole field scored near zero on it. The figure including it and
  the figure excluding it are both published, and neither is quietly preferred.
- **Publish regardless of outcome**, and quote the pre-registration back
  including any prediction that failed.

---

## The allowlist rule

Stated once, applied to every arm, rather than decided per arm by whoever added
it. It is in the header of [`../configs/arms.yaml`](../configs/arms.yaml).

> An arm's `client_tools` is its server's **full advertised surface**, minus tools
> irrelevant to the task shape. Every exclusion is named in the arm's block with
> a reason. The cut is made on the client allowlist, uniformly, and not by
> pinning one arm's server smaller than another's.

**It exists because we got it wrong, against a competitor, in our own favour, and
shipped it into a table.** Measured surfaces against what those arms were
originally given:

| arm | tools served | allowlisted before | allowlisted after |
|---|---:|---:|---:|
| repowise | 11 | 8 of 4 pinned | 7 |
| codegraph | 1 | 1 | 1 |
| graphify | 10 | **1** | 7 |
| serena | 29 | **3** | 10 |
| code-review-graph | 30 | **1** | 20 |
| cocoindex | 1 | n/a | 1 |

Graphify ran on 1 of the 10 tools it serves. Serena and code-review-graph would
have gone in at 3 of 29 and 1 of 30. That does not prove the asymmetry caused
their results, but **an arm we handicapped is not an arm we measured**.

Two things keep "irrelevant to the task shape" a checkable claim rather than a
licence. It is a claim about **the cell**, not about the tool's quality: a cell is
one read-only question at one commit, so there is no pull request for a PR-triage
tool to read and no diff for a diff-impact tool, and no arm is given Edit, Write
or Bash so a refactor tool cannot be exercised at all. And every exclusion is
listed per arm, so disagreeing with one is a one-line change.

**A correction that runs against us is kept in the file rather than edited out.**
Serena's `execute_shell_command` was excluded on the grounds that "Bash is denied
to every arm". Bash was not denied: the bare control issued 11 Bash calls in one
run and 8 in another. So a competitor was denied a tool because every arm lacked
something every arm in fact had. It is still excluded pending a rerun, because
flipping it today would put a fresh Serena row beside months-old rows for
everyone else, and any published Serena row has to carry that note.

---

## Adding an arm

A YAML block in [`../configs/arms.yaml`](../configs/arms.yaml). No Python, no
runner change. Files dropped into `configs/arms.d/*.yaml` merge over it, so a
third party can add an arm without editing a tracked file.

```yaml
your-tool:
  description: >
    What it is, and which of its tools it is given, chosen from its own
    documentation. Name every exclusion and why.
  mcp:
    server_name: yourtool
    command: "{uv_bin}/yourtool.exe"
    args: ["serve", "--repo", "{tree}"]
    served_tools: null          # leave the server at its own default
  client_tools: [mcp__yourtool__search]
  index:
    command: ["{uv_bin}/yourtool.exe", "index", "{tree}"]
    timeout_seconds: 10800
  activate: []                  # calls made after start, before anything scored
  coaching: |
    ...
```

Then the gate in section 6 applies to it exactly as it applies to us: prove it
alive, read one of its responses by hand, write its extractor, read its retrieval
surface off disk, and self-test the grader before recording a single number.

**What an arm is entitled to before it scores:** its full served surface, its own
documented setup steps run for it rather than left for it to fail on, its own
worktree, and a human reading one of its real responses. Everything on this page
is the list of ways we failed to give one of those.
