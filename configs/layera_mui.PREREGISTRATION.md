# Pre-registration: Layer A on mui/material-ui, a second language

**Committed before any index was built.** The draw below was computed and pinned
before a single instance was indexed by any tool, which is the same protocol the
django split got before rung 8, and a second repo does not get a weaker one.

---

## 1. Why mui, and why not another Python repo

Every number in `docs/BENCHMARKS.md` section 2 is `django/django`: one repo, one
commit, Python, in every model's training data. Section 1 is Python and Go. The
page's own Limits section says, in print, that **no TypeScript or JavaScript row
appears anywhere on it**. A second Python repo mostly re-measures the same thing.

ContextBench's TypeScript slice is the largest non-Python one, and mui is its
largest member.

## 2. The instances, checked rather than assumed

`data/contextbench/contextbench_verified.parquet`, 500 rows, of which **45 are
mui**: 23 under repo name `mui/material` and 22 under `mui/material-ui`.

Both names carry the same `repo_url` (`github.com/mui/material-ui.git`) and the
two names are two **source datasets**, not two repos: `source` is `Multi` for 23
and `Poly` for 22. All 45 rows have **unique `instance_id`, unique
`original_inst_id` and unique `base_commit`**, and all 45 are `language:
typescript`. So it is 45 genuine instances of one repository.

## 3. The draw: 15 development, 30 sealed

`scripts/draw_mui_instances.py`, seed **20260806**, output pinned at
`configs/mui_split.json`.

**Computed, not listed**, following `harness/question_shapes.py`: the rule and
the seed are committed and the ids are derived, so a reader who disagrees with
the stratification edits the bins rather than arguing with a pasted list.

**Stratified on gold FILE count**, because Layer A's metric is file coverage
against `gold_context`, so that is the axis along which instances differ in
difficulty, and it is where the multi-hop instances live. An unstratified draw
could hand us fifteen single-file instances and silently delete the comparison
the run exists to make.

| stratum (gold files) | pool | seats |
|---|---:|---:|
| 1 | 21 | 7 |
| 2 | 8 | 2 |
| 3-4 | 8 | 3 |
| 5+ | 8 | 3 |

**Allocation is PROPORTIONAL, not equal.** The django stratified Layer B run
used equal allocation and has to carry a caveat that its pooled mean is not an
estimate of the arms' mean over all 48. Layer A reports a pooled per-instance
mean, so proportional allocation keeps that number unbiased over the 45. Seats
go by largest remainder with **ties broken toward the larger gold-file
stratum**, stated in code rather than left to dict ordering, because the
declared purpose of stratifying is that multi-hop is represented.

**The 30 undrawn are SEALED and are evaluated once, at publication.** Same rule
as django's 42. They are listed in `mui_split.json` so the seal is auditable.

**The 116-gold-file instance is not excluded.** One of the 45 carries 116 gold
files against a median of 1. Dropping the hardest instance because it is
inconvenient is selection and it would flatter every arm including ours. If it
falls in a reported set, the **median is reported beside the mean** so one
instance cannot carry the column.

## 4. What the draw turned out to be, and the fact that changes the cost model

The 15 span **five years of mui**, 2018-05 to 2023-07, and ContextBench pins
each instance to its own `base_commit`. **They are therefore not one repo size
but twelve-fold different sizes.** Measured with `git ls-tree` at each commit,
counting `.ts/.tsx/.js/.jsx`:

| | src files |
|---|---:|
| smallest instance (2018-05) | **2,322** |
| median instance | 22,413 |
| largest instance (2023-07) | **28,346** |
| staged `bakeoff/mui` HEAD `b8a28e13` | 16,866 |
| django reference | 2,894 (python) |

**This retires the planning figure.** The brief's "~45 to 55 min per instance"
was extrapolated from mui at HEAD, but 5 of the 15 sit under 10,000 source files
and the smallest is smaller than django. The build bill is therefore materially
lower than the 20 machine-hours a flat 79 min/instance implied, and it is
**bimodal** (five instances from 2018 to 2020, ten from 2021 to 2023) rather
than uniform.

No build estimate is published from this arithmetic. The smoke replaces it.

## 5. The smoke, and why these two instances

Two instances, all arms, **index builds only, no agent runs, no grading**.

Drawn deterministically as the **oldest and newest `base_commit` among the dev
15**, so the two measurements **bracket** the range rather than sampling its
middle:

| instance | commit | date | src files |
|---|---|---|---:|
| `SWE-PolyBench__typescript__maintenance__bugfix__2bb4ea7a` | `04fae47c2a` | 2018-05-19 | 2,322 |
| `Multi-SWE-Bench__typescript__maintenance__bugfix__8fcb53e6` | `eb8e95bacf` | 2023-07-24 | 28,346 |

Both are **members of the dev 15**, so their indexes are not thrown away: they
are 2 of the 15 the full run needs, and the smoke is a prefix of the real build
rather than a side quest.

Three questions it answers before any large commitment:

1. **What does an mui index actually cost per tool**, in wall clock and disk, at
   both ends of a 12x size range.
2. **Does every competitor index TypeScript at all?** Four of the five were only
   ever pointed at Python. An indexer that silently emits an empty graph on
   `.tsx` is the graphify-0.012-MRR failure again and must be caught here.
3. **Does our own TypeScript ingestion hold up** on a repo this size.

**Gate.** Any tool that cannot index mui is fixed, or is declared unrunnable on
TypeScript **in this file**, before the remaining 13 instances start. A served
index that is empty counts as cannot: **before recording any zero, prove the arm
was alive and the extractor works.**

**Disk headroom is checked before the 13 start**, not assumed. An overnight run
already died once at 5.7 GB free. Current free space is 270 GB; measured index
sizes are repowise 613 MB and code-review-graph 709 MB on django, and crg is
expected to be the largest consumer.

**Timing is measured on a quiet machine.** Finding E1: a timed build under a
live process pool inflated by 65%. The smoke does not start while any agent run
is in flight.

## 6. The arm set, and the one decision deferred to the smoke

The smoke runs **all five arms**. The 13 that follow may run a **narrower** set
if the measured build cost makes five unaffordable in the available machine
time.

`repowise` and `CodeGraph` are retained unconditionally: CodeGraph carries
section 1's headline comparison and is the cheapest competitor to build.
Graphify and code-review-graph are the deferrable ones, crg first, because it
was already the largest index and the second slowest build on django.

**This is a scheduling decision on measured build cost, which is not an outcome
variable, taken before any retrieval is graded.** It cannot move the coverage
comparison. Any deferral is recorded in RESULT.md with the measured cost that
forced it, and a deferred arm can be added later against the same trees.

## 7. Grading

**Deterministic. No LLM judge anywhere in this number.** Ground truth is
ContextBench's `gold_context`; a tool either returns the gold file spans or it
does not. This is why Layer A goes first and why it is the cheapest layer to
make credible.

Primary metric is **file coverage**, with **precision and files-served reported
beside it**, never averaged into one figure. The django table reports two
repowise rows, `get_answer` and `search_codebase`, never pooled, and this run
keeps that.

## 8. What would invalidate this run

1. **Any instance tree not at its own pinned `base_commit`.** Asserted before
   each build. A stale checkout is a wrong answer, not a fast one.
2. **Arms sharing a tree.** Finding E3: every arm writes its index into a dotdir
   in the repo, so a shared checkout means each arm indexes its predecessors'
   output, and the bias favours whoever ran first, which was us. One worktree
   per arm per instance.
3. **A prediction missing its `traj_data` wrapper**, which scores
   `no_context_extracted` even when it names exactly the gold file. Finding E5.
   Grade a known-perfect and a known-wrong prediction before grading any real one.
4. **A timed build taken under a live process pool.** Finding E1.
5. **Touching the sealed 30** before publication.

## 9. Deliverable

`local-stash/competitive-proof/50-results/layera-mui/RESULT.md`, with this file
quoted back against what happened including any prediction that failed, the
per-tool build cost measured at both ends of the size range, the per-arm
alive-and-extracting proof, and coverage reported with precision and
files-served beside it.

Published regardless of outcome. We came last at 0.228 on django once and
published that; a TypeScript row that goes against us ships on the same terms.

