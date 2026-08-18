# G2: cross-file coverage

Reproduce CodeGraph's published coverage table from its own definition, then
compute the same metric for both tools with one script.

**Status: peer side measured, our side not started.**

## The definition problem

CodeGraph's README defines the metric as:

> **Fair coverage** = the share of symbol-bearing source files that have at least
> one *resolved cross-file dependent*.

Three things in that sentence are free, and the published number cannot be
reproduced without pinning all three:

1. Which node kinds are "symbol-bearing"? `import` nodes are 48% of gitleaks'
   node table, and counting them changes the denominator.
2. Which edge kinds are a "dependent"? Their `edges.kind` vocabulary is `calls`,
   `contains`, `imports`, `instantiates`, `references`, `implements`, `extends`.
3. Which direction? "Has a dependent" describes an incoming edge. It could also
   mean either end.

So `run_peer.py` does not report one number. It reports the metric under each
setting, which turns "we cannot reproduce their table" into "their table
corresponds to this specific reading, and here is what the other readings say".

## Result: the metric counts either direction

Indexed with their own current release, `@colbymchenry/codegraph@1.5.0`, in a
scratch copy so no repository under `test-repos/` was touched.

| repo | their published figure | incoming only | either direction |
|---|---:|---:|---:|
| psf/requests | 100% | 79.4% | 97.1% |
| guzzle/guzzle | 100% | 60.3% | **100.0%** |

guzzle reproduces exactly, 131 of 131. requests misses by one file, which we
attribute to commit drift: their pin is not published and ours is `4ed3d1b3`.

The reading that reproduces is **either direction**. A file counts as covered if
it sits at either end of any cross-file edge, and since `imports` is one of the
edge kinds, a file that imports anything satisfies it. The metric is measuring
whether the walker found the file, not whether the resolver understood it.

## What that does to the head-to-head six

Their own index, their own metric, three readings:

| repo | language | symbol-bearing files | either direction | incoming | incoming `calls` |
|---|---|---:|---:|---:|---:|
| dub | typescript | 3,911 | 0.991 | 0.748 | 0.589 |
| Ocelot | csharp | 732 | 0.985 | 0.669 | 0.352 |
| celery | python | 372 | 0.979 | 0.618 | 0.489 |
| zod | typescript | 291 | 0.938 | 0.240 | 0.148 |
| gitleaks | go | 213 | 0.915 | 0.789 | 0.784 |
| caffeine | java | 664 | 0.801 | 0.622 | 0.517 |

Five of six sit above 0.9 on the published reading. On the reading that describes
whether the call graph connected anything, zod is at 0.148 and Ocelot at 0.352.
The spread between the first and last column reaches 70 points.

Note that gitleaks barely moves between the columns, 0.915 to 0.784. A Go
repository where nearly every file is genuinely reached by resolved calls is what
a healthy cell looks like, and it is the contrast that makes the zod cell
readable.

## What we will claim, and what we will not

**Will:** publish the reproduction, both because a metric that does not reproduce
from its own wording is worth knowing about, and because we found the reading
that does reproduce rather than stopping at "it does not".

**Will not:** call it dishonest. "Dependent" is loose English, not a false
statement, and measuring whether a file is connected at all is a defensible thing
to want.

**Will not:** report our own number on the saturated column and call it a win. On
a metric where five of six repositories sit above 0.9, a win is noise. The
published table will carry all three columns for both tools, and the argument
will rest on the incoming-`calls` column or not be made.

## Reproduce

```bash
# peer side, from the frozen indexes, read-only, no rebuild
python graph/experiments/g2-cross-file-coverage/run_peer.py \
    --test-repos ../test-repos \
    --out results/graph/g2/peer_coverage.json

# the published-repo check, in a scratch copy
cp -r ../test-repos/guzzle /tmp/g2/guzzle && rm -rf /tmp/g2/guzzle/.codegraph
cd /tmp/g2/guzzle && codegraph init -i
```

Peer indexes are opened through a `file:...?mode=ro` URI. They are frozen
baselines that earlier numbers reconcile against, and SQLite will create a `-wal`
beside a database opened read-write.

## Both arms, five repositories

Our side measured in a detached worktree at `8848c456` on `origin/main`, so no
uncommitted work is in it. The peer side is the frozen indexes.

The column that matters is the last pair: incoming resolved `calls`, which is
the only reading that describes whether the call graph connected the file.

| repo | language | denominator ours / theirs | incoming `calls` ours | theirs |
|---|---|---:|---:|---:|
| gitleaks | go | 213 / 213 | 0.761 | **0.784** |
| Ocelot | csharp | 732 / 732 | **0.419** | 0.352 |
| celery | python | 373 / 372 | 0.378 | **0.489** |
| caffeine | java | 536 / 664 | 0.608 | 0.517 |
| zod | typescript | 269 / 291 | 0.138 | 0.148 |

**Read this as a wash, not a win.** Two cells each way on the comparable repos,
and the two where we lead have denominators that do not match, which is a
caveat and not a footnote (below). Coverage is simply not where the difference
between these two tools lives, which is the useful thing this experiment
established. The difference lives in whether the edges are *right*, and that is
G1 and G4.

**Two denominators match exactly**, gitleaks at 213 and Ocelot at 732, computed
independently from two unrelated data structures. That is the check worth
having, and it is what makes those two rows trustworthy.

**Two do not, and until they are reconciled those rows are not comparable.**
On caffeine they count 664 symbol-bearing java files out of 668 walked, and we
count 536. That is **128 java files, 19% of the repository, where we produce no
symbol at all.** Either our walk excludes them or we parse them and extract
nothing. This is the most actionable thing G2 has produced and it is worth
chasing before any caffeine number is quoted. zod has the same shape, 269
against 291, though the peer also walks 404 typescript files and finds only 291
symbol-bearing, so that denominator is doing something of its own.

**zod is the industry's problem, not ours.** 0.138 and 0.148 are both terrible.
Nobody has connected that repository, and the miss taxonomy already says why:
65% of what we miss there is external, and the remaining large block is
return-type inference.

**celery is expected to move.** This was measured at `8848c456`, which does not
include the Python call-resolution work in flight on `feat/python-task-receiver`.
Re-measure that cell after it lands rather than treating 0.378 as current.

One instrument note worth recording, because it would have quietly cost us 27%:
our `GraphBuilder` collapses multiple call sites onto a single `calls` edge, so
counting edges off the built graph undercounts distinct call sites. `ours.py`
therefore observes `CallResolver.resolve_file` rather than reading the graph.
This is the mirror image of the peer's raw-versus-distinct trap, and it points
the other way.

`run_ours.py` does not yet persist the symbol-bearing file list, which is why
the caffeine gap can be sized but not explained. Add that first next session.

One instrument note worth recording, because it would have quietly cost us 27%:
our `GraphBuilder` collapses multiple call sites onto a single `calls` edge, so
counting edges off the built graph undercounts distinct call sites. `ours.py`
therefore observes `CallResolver.resolve_file` rather than reading the graph.
This is the mirror image of the peer's raw-versus-distinct trap, and it points
the other way.

## Next

Extend our side to the remaining five repositories, at a clean commit. The
mapping is not one-to-one and the differences have to be settled before a
published table exists:

* We separate `references` from `calls`; they do not. Their `instantiates` is our
  resolved call to a constructor.
* Our `imports` edges are file to file; theirs originate at a node.
* "Symbol-bearing" is `symbol_count > 0` on our file nodes, against at least one
  row in `nodes` on theirs, and the two walks exclude different directories.

Each of those is written down and decided in the open before the table exists,
because each one is worth a few points in whichever direction the author wants.
