# G9: repowise's call graph, graded against each language's compiler

**Status: measured for repowise alone on Java, C#, C, C++ and Rust against each
compiler, and on Python against jedi's type inference. Five held-out
repositories and ten dev repositories.** No competitor
was run. This is an accuracy table for one tool, not a comparison, and it must
not be set beside [G4](../g4-oracle-anchored/)'s five-arm cells as if it were
the same experiment.

The bars for public wording were fixed before any number existed, in
[`PREREGISTRATION.md`](PREREGISTRATION.md).

## Result

Repowise at `c00e5b0c2` (0.54.0; no call-graph code changed between the release
tag and that commit). Strict scores are primary. 95% Wilson intervals.

| Language | Graded against | Repository | Set | Precision [95% CI] | Recall [95% CI] | Overload-set P / R |
|---|---|---|---|---|---|---|
| C | clang (scip-clang) | git 2.56.0 | held out | 0.981 [0.979, 0.983] | 0.618 [0.612, 0.624] | no overloads |
| C | clang (scip-clang) | redis 8.10.2 | dev | 0.946 [0.943, 0.949] | 0.536 [0.531, 0.541] | no overloads |
| C++ | clang (scip-clang) | fmt 12.2.0 | dev | 0.709 [0.674, 0.741] | 0.311 [0.289, 0.335] | not computed |
| C# | Roslyn (scip-dotnet) | AutoMapper 14.0.0 | held out | 0.827 [0.802, 0.850] | 0.255 [0.240, 0.270] | 0.858 / 0.323 |
| C# | Roslyn (scip-dotnet) | FluentValidation 12.1.0 | dev | 0.869 [0.827, 0.903] | 0.423 [0.385, 0.462] | 0.941 / 0.498 |
| C# | Roslyn (scip-dotnet) | Polly 8.8.0 | dev | 0.774 [0.709, 0.827] | 0.125 [0.108, 0.146] | 0.911 / 0.156 |
| Java | javac (scip-java) | javapoet 1.13.0 | held out | 0.427 [0.392, 0.462] | 0.461 [0.425, 0.498] | 0.552 / 0.623 |
| Java | javac (scip-java) | jsoup 1.23.2 | dev | 0.542 [0.527, 0.557] | 0.516 [0.501, 0.531] | 0.667 / 0.643 |
| Java | javac (scip-java) | gson 2.14.0 | dev | 0.549 [0.528, 0.570] | 0.649 [0.626, 0.671] | 0.607 / 0.724 |
| Python | jedi `goto` | typer 0.27.2 | held out | 0.943 [0.931, 0.953] | 0.770 [0.752, 0.788] | no overloads |
| Python | jedi `goto` | attrs 26.1.0 | held out | 0.498 [0.467, 0.529] | 0.368 [0.342, 0.394] | no overloads |
| Python | jedi `goto` | httpx 0.28.1 | dev | 0.970 [0.959, 0.978] | 0.691 [0.669, 0.713] | no overloads |
| Python | jedi `goto` | click 8.5.0 | dev | 0.936 [0.923, 0.947] | 0.700 [0.681, 0.719] | no overloads |
| Rust | rust-analyzer | ripgrep 15.2.0 | dev | 0.825 [0.810, 0.839] | 0.456 [0.442, 0.471] | no overloads |
| Rust | rust-analyzer | fd 10.5.0 | dev | 0.907 [0.847, 0.945] | 0.546 [0.481, 0.608] | no overloads |

Not yet measured: C++ held out (re2 needs abseil built first), Rust held out
(mini-redis, just). Kotlin, Swift, Ruby and PHP are not attempted.

Every cell's score, with its bucket counts and declaration-line offsets, is in
[`results/<language>/<repository>/`](results/). The overload-set score is in the
same `score.json` under `overload_set`, or beside it as
`score_overload_set.json`.

**Held out** means picked before any work started, never opened while looking
for failure patterns, and measured once. **Dev** repositories are the ones read
to find the patterns behind wrong and missing edges. Every number above is the
released build, before any fix that came out of that reading.

## The method

1. **Call sites from syntax.** `callsites.py` runs repowise's own tree-sitter
   call queries (`<lang>.scm`) and reads only the `@call.target` capture, so a
   site here is a site repowise itself tried to resolve. The caller is the
   outermost named function containing the site, the same rule as G4.
2. **Targets from the compiler.** `scip_reader.py` loads a SCIP index and
   `join.py` looks up the symbol at each callee span and its Definition. An
   in-repo definition is an oracle edge. A definition outside the repository is
   resolved-external: it counts as resolved and draws no edge. No symbol at the
   span is unresolved, and the caller containing it is unjudged.
3. **Re-anchoring.** SCIP puts a definition on the name token. Repowise puts it
   on the declaration node, which starts at any leading annotation or modifier.
   `join.py` walks from the SCIP position to the enclosing declaration node using
   the same node-type table as the caller side, so both sides use one line.
4. **Scoring.** `score.py` imports G4's `compare.py` and scores the oracle with
   it unchanged: matched, contradicted (an edge from a judgeable caller the
   oracle does not have), unjudged. Precision = matched / (matched +
   contradicted). Recall = matched / oracle edges in judgeable callers. Only
   repowise `calls` edges are scored. External targets are excluded on both
   sides.
5. **Overload-set score** (`score.py --overload-set --language <lang>`), a
   secondary key. Repowise keeps one node per overload set. This key groups a
   callee by (file, enclosing type, method name), so an edge to the right
   method and the wrong overload counts as matched. It isolates how much of the
   strict gap is that identity choice.
6. **Python** uses `jedi_provider.py` in place of a SCIP index. It feeds the
   same tree-sitter sites to jedi's `goto` (`jedi_worker.py`, in an interpreter
   with only jedi installed) and writes the same `oracle.jsonl` shape. A call
   site where jedi returns more than one in-repo definition can be scored as
   unjudged with `score.py --ambiguous-callers`; both readings are published
   for typer and attrs and differ by under a point.

### Checks before a language was reported

- **Method check against G4.** The SCIP join was run on G4's gitleaks and zod
  cells against the same repowise index G4's own oracles scored. gitleaks lands
  within 1.3 points of precision and 1.4 of recall, and the residual is
  explained: RTA only judges code reachable from `main`, this oracle judges
  every resolved call. zod did not agree (SCIP 0.806 / 0.562 against `tsc`
  0.993 / 0.776). A full `pnpm install` changed no number, and 40 disagreeing
  edges read by hand showed real resolver differences (a parameter shadowing a
  same-named class, calls through structurally typed parameters), not a
  tooling fault. No TypeScript number from this method is quoted anywhere. Both
  pairs are in [`results/_method_check/`](results/_method_check/).
- **Identity.** The modal declaration-line offset must be (0, 0), and 20
  identities drawn with `identity_check.py` are confirmed against source. Every
  cell passed. attrs passes weakly (34% of in-scope edges at (0, 0), still the
  mode).
- **Hand reading of the reference.** 20 contradicted and 10 missed edges per
  language, read against source, to find errors in the oracle itself. None were
  found. Polly was read at 10 and 5. javapoet and AutoMapper have the identity
  check only.

### Known reference defects, all charged to repowise

- scip-clang gives same-named `static` functions in different translation units
  one symbol. 709 redis oracle edges (1.4%) point into a `static` function the
  caller cannot link to.
- scip-dotnet gives `IFoo` and `IFoo<T>` one symbol (first definition wins).
- scip-dotnet resolves `new X(...)` to the type, not the constructor. `join.py`
  redirects a true object creation to the selected constructor.
- scip-python 0.6.6 was tried first and dropped: some top-level classes had no
  Definition role, and `pkg.Class(args)` through a star re-export resolved to an
  unrelated class. jedi replaced it.

None of the first two is corrected in the scores.

## Per-language run notes

All SCIP indexers except scip-dotnet were run on Linux (Ubuntu 22.04 under
WSL). scip-java 0.13.1 ships no Windows launcher, and scip-clang 0.4.0 needs
glibc 2.38, so 0.3.1 was used. Build each repository at its pinned tag in a
scratch copy, never in a shared checkout.

| Language | Build and index | Oracle build |
|---|---|---|
| Java | `scip-java index -- clean compile -DskipTests` (Maven, JDK 21) | `oracle_build.py --language java [--exclude-tests]` |
| C# | `dotnet restore`, `dotnet build`, then `scip-dotnet index` on the solution | `oracle_build.py --language csharp [--exclude-tests]` |
| C | `bear -- make -j4` for `compile_commands.json`, then `scip-clang --compdb-path compile_commands.json` | `oracle_build.py --language c --exclude-tests` |
| C++ | `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, then scip-clang as for C | `oracle_build.py --language cpp --exclude-tests` |
| Rust | `cargo check`, then `rust-analyzer scip .` | `oracle_build.py --language rust --exclude-tests` |
| Python | none | `jedi_provider.py --repo <repo> --out-oracle <file>` |

Then, in the interpreter that has the repowise build under test installed:

```
python score.py --oracle <oracle.jsonl> --repo <repo> --repo-name <name> \
    --arms repowise --out <score.json>
python score.py --overload-set --language <lang> --oracle <oracle.jsonl> \
    --repo <repo> --repo-name <name> --out <score_overload_set.json>
```

`dump_buckets.py` writes the contradicted and missed edges for hand reading,
and `confidence_tier.py` rescores at resolver confidence 0.9 and above.

Cell-specific notes:

- **gson**: Error Prone's multi-line compiler arguments do not survive
  scip-java's javac wrapper. Its `maven-compiler-plugin` Error Prone config was
  removed in the scratch copy (it adds checks and changes no resolution). Extra
  flags: `-Dspotless.check.skip=true -Dgson.isTestModule=false`.
- **javapoet**: its old Error Prone compiler integration does not load under
  scip-java. In the scratch copy, the Error Prone `compilerId` was dropped,
  `maven-compiler-plugin` raised to 3.13.0 and `java.version` to 17. No source
  file was touched.
- **commons-lang, commons-text**: not scored. The build succeeds but never goes
  through scip-java's javac, so no index is written.
- **Polly**: four library projects built under the installed .NET SDKs (8 and
  9), so its recall is a floor and is not comparable with FluentValidation's.
- **AutoMapper**: measured at v14.0.0, the newest tag that builds without .NET
  10. A prerelease build-tool package (`Microsoft.DotNet.ApiCompat`) no longer
  restores from nuget.org and was dropped in the scratch copy. Its test
  projects are in scope, and the oracle leaves 59% of repowise's edges there
  unjudged.
- **fmt**: header-only, so `-DFMT_TEST=ON` is needed for the compile database to
  reach the headers.
- **Test files**: the Python cells and AutoMapper include them. Each
  `score.json` header carries the analysed file count for its cell.

## Reproducing

```
# protobuf bindings for SCIP (scip.proto from github.com/sourcegraph/scip v0.10.0)
python -m grpc_tools.protoc -I <dir-with-scip.proto> --python_out=<out> scip.proto
export SCIP_PB2_DIR=<out>
# repowise's call queries, when the build interpreter does not have repowise installed
export REPOWISE_QUERY_DIR=<repowise>/packages/core/src/repowise/core/ingestion/queries
```

The build step needs `protobuf`, `tree_sitter` and the grammar packages
`callsites.py` imports (`tree_sitter_go`, `_typescript`, `_java`, `_c_sharp`,
`_rust`, `_python`, `_cpp`). The scoring step needs the repowise build under
test. They can be one interpreter when the protobuf runtimes agree.

Tool versions as run: scip-java 0.13.1 (JDK 21), scip-dotnet 0.2.14 (.NET SDK
8.0.425), scip-clang 0.3.1 (Ubuntu clang 14 compile databases), rust-analyzer
1.96.0, jedi 0.20.0, SCIP schema 0.10.0, scip-go 0.2.7 and scip-typescript
0.4.0 for the method check.

**jedi is not fully deterministic.** Three rebuilds of the httpx oracle gave
2,076 to 2,078 in-repo edges, against 2,077 in the scored one, so a Python cell
can move by a few tenths of a point on a rerun. A SCIP oracle rebuilt from the
same index (fd) is identical.

### Pins

| Repository | Tag | Commit |
|---|---|---|
| git/git | v2.56.0 | `a018953688f1b10bddf91bff8747068f5f4746a4` |
| redis/redis | 8.10.2 | `498ecd0d6d007db11ddb3aea9428552598a78622` |
| fmtlib/fmt | 12.2.0 | `1be298e1bd68957e4cd352e1f676f00e07dcfb57` |
| AutoMapper/AutoMapper | v14.0.0 | `bf76a5819e1bad7af7db5382bf0f3a0178048a15` |
| FluentValidation/FluentValidation | 12.1.0 | `b960f42b6065d1aeecddb0c747e532b1842a96ef` |
| App-vNext/Polly | 8.8.0 | `9ad5ae9b4ff9beb4321b0762249ce173b984f817` |
| square/javapoet | javapoet-1.13.0 | `714e05ca60179285746604452324262b126dcb2d` |
| jhy/jsoup | jsoup-1.23.2 | `fbc7775c462f5b023d8db54c9a73d7ec17d53300` |
| google/gson | gson-parent-2.14.0 | `3ff35d6269894901ab8006258395aafc4b9765cd` |
| fastapi/typer | 0.27.2 | `99eb220df7c69a0f14a0b69214042677e0760b9d` |
| python-attrs/attrs | 26.1.0 | `7bfc49e9b22d5ba25b6e429524c3d49fee27cb36` |
| encode/httpx | 0.28.1 | `26d48e0634e6ee9cdc0533996db289ce4b430177` |
| pallets/click | 8.5.0 | `8b19813f2bfca99f1018a587a8cf54fc959f2e5d` |
| BurntSushi/ripgrep | 15.2.0 | `e89fff89ac9af12e8d4ce9d5fd07beb408ca730f` |
| sharkdp/fd | v10.5.0 | `4f81778774463bf414a184cbe6d5219ad2229646` |

## What this does not show

- **No comparison.** No other tool was graded here.
- **One run per repository.** Recall depends on entry points and dynamic
  dispatch, so do not compare it across rows, and do not pool any column.
- **Where both methods exist, they disagree on Java and C#.** G1's hand grade
  reads Java at 30/40 and C# at 30/30, against 0.43 to 0.55 and 0.77 to 0.87
  here. The hand-graded rows come from different repositories, and a person
  reading a call to an overloaded method sees one node for the set. Which of
  those explains the gap is not yet measured.
