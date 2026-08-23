# The graded rows

Every row behind the tables in [`../README.md`](../README.md): nine languages,
both tools, each with the verdict it was given and the reason that was written
when it was read from source. **580 rows in total, 540 of which are pooled**:
C++ ships all 50 of its graded rows on each side and enters the pooled figure at
30, which is explained below.

Until this directory existed the tables were the artifact and the rows were not,
which meant a reader could check our arithmetic and nothing else. The rows are
the part worth attacking, so they are the part that ships.

```bash
python ../verify_rows.py            # rebuild every published table from these files
python ../verify_rows.py --rows go  # read one language, both sides
```

`verify_rows.py` recomputes the pooled pair and exits non-zero if it disagrees
with the headline the page prints. It currently agrees: 240/280 and 164/280. It
also checks each file against its own `depth_read`, after one was published
carrying two draws mixed together and summing to a depth its own header
contradicted; the pooled cell had agreed by coincidence.

**java carries 40 rows, not 30**, being caffeine's 30 plus spring-petclinic's 10.
caffeine's rows record `file` as a basename and spring-petclinic's as a
repository-relative path; `repo` disambiguates them.

## One file per cell

`<language>-ours.json` is repowise, `<language>-codegraph.json` is CodeGraph
1.5.0. Each carries the repositories, the repowise commit that cell was measured
at, the seed, and its own tally.

| field | meaning |
|---|---|
| `repo`, `file`, `line` | the call site, as the source spells it |
| `target` | the declaration the tool bound it to |
| `origin` | which resolution strategy answered, and the stratum the row was drawn from |
| `verdict` | `correct`, `wrong` or `ambiguous`, read from source |
| `reason` | why, in the reader's own words |

`ambiguous` is an honest verdict and is never counted as correct.

Some CodeGraph rows carry `error_class: overload`: the declaring type is right
and the overload is wrong. Those are graded `wrong` everywhere on this page, and
labelled here so the alternative grading can be recovered without regrading
anything. It is worth three rows on Ocelot and up to four on caffeine.

## Two things about the shape of the sample

**C++ was graded at 50 rows and enters the pooled figure at 30.** Ten rows per
repository across five repositories is the depth read; the pooled cross-language
cell is the seeded six of each ten, flagged `in_pooled_30` and drawn with
`random.Random(2026).sample(range(10), 6)` per repository. Without that, one
language would carry nearly double the weight of the other eight. Both readings
are reported on the main page and `verify_rows.py` prints the pooled one.

**Every cell on our side is measured at `350f6a3a`, and the `note` on each file
says how it got there.** Five cells were never re-read. Four of them produce a
byte-identical set of resolved call sites at the commit they were measured at and
at the pin, so the rows are the rows that were read; java is the fifth, where three
sites out of 52,054 were retargeted and none of the three is a graded row. Two more
cells kept most of their rows and re-drew one repository each. Two were re-drawn
whole. A file whose note describes a re-draw shares no row keys with its previous
version, which is the point of saying so.

**`cpp-ours.json` was rebuilt rather than edited, and its note says why.** It had
been carrying two draws mixed together, which is how its stated depth read of 42
came to disagree with its own rows summing to 43. The rows it holds now are the
draw that was actually taken, which for the four repositories not re-read this
time is the one in [`reread-2026-08-22/`](reread-2026-08-22/).

**`rust-ours.json` carries the draw and not the grading.** That cell was re-read
on a fresh draw after [#1708](https://github.com/repowise-dev/repowise/pull/1708)
stopped the bare-name tier answering for the standard library, and the per-row
verdicts of that read were never written down. What survives is the cell total,
22 of 30, and the composition of the eight wrong rows: four macro invocations
graded as calls, three cross-module or overload collisions, and one
standard-library type constructor. The 30 sites that were read are here, so the
sample can be checked even though the verdicts cannot; the file records
`verdict: null` on every row rather than implying otherwise, and
`verify_rows.py` says so in its output.

**This is the one cell of the eighteen that this directory cannot supply**, and
the honest reading is that one eighteenth of the audit still rests on a table
rather than on rows.
