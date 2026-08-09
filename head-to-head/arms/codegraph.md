# CodeGraph

**The genuine second, on both layers, at a twentieth of the build cost.**

| | |
|---|---|
| What it is | A code graph over the repository with a single natural-language entry point |
| Index | `.codegraph/codegraph.db`, SQLite, written into the tree |
| Tools served | **1** |
| Tools allowlisted | **1 of 1**, its full surface |
| Coverage, sealed 42 | **0.610** · precision 0.093 · 14.0 files served · **2nd of six** |
| Agent loop, Codex | **-24.4%** output tokens vs bare, 4.0 tool calls · **2nd of five** |
| Build, django | **16.4s** |
| Cost exponent | 1.100 (R² 0.999) |

## What it serves, and why it gets one tool

One tool, `codegraph_explore`. Its own README says it "answers almost any
question in one call", so that is the tool it is given, **chosen from its
documentation rather than picked by us**.

**1 of 1 is a full surface, not a handicap.** This matters because the same
number reads very differently elsewhere on this page: Graphify was originally
given 1 of the 10 tools it serves, and that was a handicap. CodeGraph serves one.

## How it is set up

```yaml
index:  codegraph init {tree}
serve:  codegraph serve --mcp --path {tree} --no-watch
```

`--no-watch` because a file watcher inside a benchmark cell is a background
process competing with a timed measurement.

## What it is good at

**Retrieval, second only to us and clearly ahead of the rest of the field.** 0.610
file coverage on the 42 sealed django instances, from 14.0 files served at 0.093
precision. Head to head against `get_answer` it loses 1 to 19 with 22 ties, but
against everything else in the table it wins.

**The agent loop, likewise.** -24.4% output tokens against a bare agent on the
48-question Codex run, p < 0.0001, reaching an answer in 4.0 tool calls against
the bare agent's 7.2. Ours is -31.6% at 3.8 calls. Those are close enough that
the honest sentence is that we lead a field in which more than one tool works.

**Cost, by a distance.** 16.4s to index django where we take 366.8s. It is the
cheapest arm in the field to build at every size we measured, and its cost curve
is one of the two cleanest (R² 0.999).

## What it is bad at

**It indexes no documentation.** `files.path` carries **zero** `.md` and zero
`.json` across every instance measured. On a corpus where a fifth of gold files
are documentation, those are unreachable by construction. Same boundary we have,
for whatever reason.

**Its cost curve is superlinear** (exponent 1.100), so the gap to us narrows as
repositories grow. At 2,322 files it builds in 7% of our time; at 28,346 files,
25%.

**It is the most contention-sensitive arm in the field.** Two instances measured
beside a stray background process inflated CodeGraph **2.5 to 3.3x** while
touching repowise only 1.03 to 1.20x. Any CodeGraph timing taken on a busy
machine is unusable, and more so than for any other tool here.

## Setup traps

**Its `nodes.name` column holds import specifiers, not names.** This is a trap
for anyone auditing what a graph index actually covers rather than for running
it. A generic "count file extensions in any column called name or path" sniffer
reported that CodeGraph indexes JSON, because `.json` appears 196 times in
`nodes.name` as import targets. Its actual retrieval surface, `files.path`, has
none. **The sniffer reported the opposite of the truth**, which is why every
surface on this bench is named per arm and read from the table a query ranks
against.

**Its response is free prose with inline paths**, not JSON. Path extraction is a
text scan, which works but has no schema to fail loudly against. Its raw
responses are kept verbatim on disk for that reason.

## Where its numbers are

- Sealed django coverage: [`../../results/bakeoff_2026_08/rung8/`](../../results/bakeoff_2026_08/rung8/)
- Agent loop: [`../../results/bakeoff_2026_08/rung9/`](../../results/bakeoff_2026_08/rung9/)
- Arm definition: [`../../configs/arms.yaml`](../../configs/arms.yaml), `codegraph`
