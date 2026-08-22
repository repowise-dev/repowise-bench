# The graded rows

Every row behind the tables in [`../README.md`](../README.md): 270 call edges
emitted by codebase-memory-mcp 0.10.8, nine languages, each with the verdict it
was given and the reason written when it was read from source.

The tables are the summary and these are the artifact. A reader who disagrees
with a verdict can edit the row and watch the table move.

```bash
python ../verify_rows.py             # rebuild every published table from these files
python ../verify_rows.py --rows ruby # one cell, with its source lines and reasons
python ../verify_rows.py --strata    # the by-origin and by-confidence reads
python ../verify_rows.py --threeway  # the seven languages all three arms share
```

## One file per language

`<language>-cbm.json` carries the repositories with their pins, the seed, and
its 30 rows.

| field | meaning |
|---|---|
| `repo`, `file`, `line` | the call site, as the arm's `properties.$.line` names it |
| `source_line` | the verbatim source text at that line, copied when it was read |
| `resolved_to`, `target_file`, `target_line` | the declaration the arm bound it to |
| `origin` | the arm's `properties.$.strategy`, and the stratum the row was drawn from |
| `confidence`, `candidates` | the arm's own stored numbers, read after the verdict, never before |
| `verdict` | `correct`, `wrong` or `ambiguous`, read from source |
| `reason` | why, naming the import, receiver type, base class or module boundary that decides it |
| `error_class` | `overload` where the declaring type is right and the overload is wrong |

`ambiguous` is an honest verdict and is never counted as correct. It was used
once in 270 rows, which is low enough to be worth stating: these rows were
mostly decidable, and a grader who reached for `ambiguous` to avoid reading
would show up as a cell full of them.

## Two things about the rows a reader should know

**`error_class: "overload"` is on 11 rows and was on 24.** The php cell came
back with the flag on all thirteen of its wrong rows, none of which is an
overload error, and the mislabel was stripped. No verdict changed and no rate
moved: those rows were `wrong` under either label. It is recorded here because
the flag exists so that the alternative grading can be recovered, and a flag
that is wrong on half its occurrences would not survive that.

**Two rows name a line that does not exist in the file.** `fmt`'s
`test/gtest/gmock-gtest-all.cc:23599` in a 14,442-line file, and `aria2`'s
`src/LibsslTLSSession.cc:2073` in a 378-line file. Both are graded `wrong`,
because a call site that is not there cannot be the site of a real edge. The
main page measures how common that is (17% to 30% of call rows on two C++
repositories, zero on thirteen non-C repositories) and reports the cell both
with and without them.

## The draws, before grading

[`../draws/`](../draws/) holds the same 270 rows as the sampler produced them,
one file per repository cell, every `verdict` still `null`. Diffing a graded
cell against its draws is how to check that grading changed verdicts and nothing
else.
