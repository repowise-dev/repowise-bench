# Performance Detection: Methodology & Full Results

Engineering appendix to [`README.md`](./README.md). This is the rigorous version:
exact per-repo numbers, experimental design, integrity checks, threats to
validity, and reproduction commands. All runs dated 2026-06-22.

---

## 1. What the detector does

Repowise's performance pillar flags **static performance-risk patterns**
(code shapes that waste work) across six languages (Python, TypeScript/JS,
Java, Go, C#, Rust). The core marker family:

| Marker | Pattern |
|--------|---------|
| `io_in_loop` | an execution-sink I/O call (db / network / filesystem / subprocess) inside a data-dependent loop, **same-function and cross-function** |
| `nested_loop_with_io` | I/O in the inner body of a nested loop (O(n·m) round-trips) |
| `hot_path_sync_io` | blocking I/O in a high-centrality function |
| `membership_test_against_list_in_loop` | `x in big_list` inside a loop (O(n·m), should be a set) |
| `string_concat_in_loop`, `serial_await_in_loop`, `resource_construction_in_loop`, ... | other loop-level waste |
| language-specific | `defer_in_loop` (Go), `regex_compile_in_loop` (Java/Go), `pandas_iterrows_in_loop` (Py), ... |

The three platform assets that make this possible (and that a file-local linter
lacks) are a **whole-program call graph**, a **classified dependency registry**
(`io_kind ∈ {db, network, filesystem, subprocess, lock}`), and **per-function
centrality + churn**.

---

## 2. Experiment E1: real-tool baseline (the moat)

**Question:** do the industry-standard file-local linters already find these bugs?

**Method:** run each language's canonical linter and the Repowise detector over
the *same* files, then compare finding sets. "Moat" = findings in categories the
linter has no rule for (verifiable against each tool's published rule registry).

### Python: ruff 0.15.6 (`--select PERF,ASYNC`)

| Repo | Files | ruff total (PERF/ASYNC) | Repowise total | **Moat (no ruff rule)** | Cross-fn |
|------|------:|------------------------:|---------------:|------------------------:|---------:|
| django | 3,005 | 136 | 193 | 191 | 30 |
| fastapi | 2,730 | 24 | 20 | 20 | 7 |
| pydantic | 692 | 13 | 15 | 15 | 3 |
| scrapy | 462 | 23 | 11 | 11 | 1 |
| celery | 464 | 72 | 8 | 8 | 1 |
| microdot | 176 | 10 | 12 | 8 | 0 |
| **Total** | **7,529** | **278** | **259** | **253 (98%)** | **42** |

ruff's PERF rules are a different class (loop->comprehension, try-except-in-loop);
its ASYNC family overlaps our `blocking_sync_in_async` only (6 findings, where ruff
is actually broader: 17 vs our 6). **ruff has zero io-in-loop / N+1 rules.**

### TypeScript/JS: ESLint 9 + typescript-eslint (`no-await-in-loop`)

| Repo | Files | ESLint no-await-in-loop | **Moat (no ESLint rule)** | Cross-fn | Serial-await agreement |
|------|------:|------------------------:|--------------------------:|---------:|-----------------------:|
| dub | 3,996 | 341 | 230 | 43 | 153/171 = 89% |
| hono | 386 | 76 | 21 | 0 | n/a |
| zod | 409 | 14 | 11 | 2 | n/a |
| taxonomy | 129 | 0 | 0 | 0 | n/a |
| **Total** | **4,920** | **431** | **262** | **45** | **89%** |

ESLint's only perf-adjacent rule (`no-await-in-loop`) is **purely syntactic**:
it flags every await in a loop (431, mostly harmless), 2.5× broader than our
classified-I/O `serial_await_in_loop`. Where we both fire, 89% agree. ESLint has
**no** io-in-loop / N+1 / string-concat / membership rule.

### Go: golangci-lint v2.3.0 (prealloc, gocritic, bodyclose, makezero)

On gitleaks (214 Go files): golangci-lint's perf linters found **0**; Repowise
found **42** (24 `io_in_loop`, 7 `hot_path_sync_io`, 9 `defer_in_loop`, 2
`regex_compile_in_loop`). golangci-lint bundles 100+ linters; none detects
io-in-loop / N+1 / cross-function.

### Rust: clippy

clippy's `perf` lint group (`needless_collect`, `redundant_clone`, ...) is all
file-local micro-optimization with **no** io-in-loop / N+1 / cross-function lint
(verifiable from the clippy registry). The Repowise Rust perf dialect now ships
(`regex_compile_in_loop`, `resource_construction_in_loop`,
`blocking_sync_in_async`), but a measured clippy run on the Rust corpus was
blocked by the Windows build toolchain, so the clippy-vs-Repowise head-to-head on
Rust was not completed end-to-end. That specific comparison therefore stays
catalogue-level (a measured run is the clear next target; bevy / deepwiki-rs / rtk
are ready corpora).

### E1 conclusion

Across 3 languages / 12,449 files, linters and the pillar produce **near-disjoint**
finding sets: **515 of the pillar's Python+TS findings (95%+) lie in categories the
linters have no rule for**, including **~90 interprocedural** findings structurally
impossible for a file-local tool. Where rules overlap, the tools agree and the
commodity tool is often broader, establishing **complementarity, not competition.**

---

## 3. Precision (hand-labeled)

| Corpus | Repos | Marker | n | Precision | 95% CI |
|--------|-------|--------|--:|----------:|--------|
| Mature | Python web-app corpus + repowise | io_in_loop | 53 | **96.2%** | [87, 99] |
| Mature | dub (TS, Prisma) | io_in_loop | 30 | **100%** | [88, 100] |
| Mature | syft/gin/gitleaks/osv (Go) | io_in_loop | 30 | **96.7%** | [83, 99] |
| Hard (vibe-coded) | openclaw (~20k-file TS monorepo) | io_in_loop | 30 | **90% shape / 73% actionable** | [74, 96] |

The academic gate is **≥70%** (Jin et al., PLDI'12). All clear it. The hard-corpus
gate caught and fixed a real false-positive class (see §5).

---

## 3b. Experiment E4: runtime confirmation

**Question:** the findings flag *patterns* that waste work. Does the waste cost
measurable runtime, and how much does the obvious fix recover?

**Method:** for 7 already-verified findings, time the pattern **as written** vs the
**obvious fix**, same inputs, back to back, one machine. 1 run per config (n large
enough that the gap is unambiguous; several n reported per finding so the headline
isn't tied to one size). Python via the project venv, Node for the TS/JS finding,
stdlib `sqlite3` against a real on-disk db for the I/O ones. Scripts + raw output:
`benchmarks/` (`bench_01..07`, `RESULTS.md`). Environment: Python 3.13.5, Node
v22.19.0, sqlite 3.50.2.

| # | Finding (marker) | Real source | Fix | n (repr) | Speedup (repr) | Speedup (peak) | Shape |
|---|------------------|-------------|-----|---------:|---------------:|---------------:|-------|
| 1 | `membership_test_against_list_in_loop` | FastAPI-style; Django ~50 | list -> set | 10,000 | 605x | 2,521x @50k | O(n²)->O(n) |
| 2 | O(n²) list-membership, recursive walk | FastAPI `dependencies/utils.py` `get_flat_dependant` | `visited` list -> set | 2,000 nodes | 51x | 186x @8k | O(n²)->O(n) |
| 3 | `string_concat_in_loop` | repowise + many TS repos | list + `"".join` | 20,000 | 2.5x | 5.7x @80k | bounded (CPython `+=` opt) |
| 4 | `list_insert_zero_in_loop` | Django (7) | append+reverse / `deque` | 20,000 | 163x | 350x @50k | O(n²)->O(n) |
| 5 | `array_spread_in_reduce` | dub (TS) | push into one array | 8,000 | 57x | 805x @20k | O(n²)->O(n) (Node) |
| 6 | `io_in_loop` / N+1 | repowise `scheduler.py`; synthetic | one `WHERE id IN (...)` | 500 | 29x | 30x @2k | N round-trips -> 1 |
| 7 | `resource_construction_in_loop` | repowise `app.py` | open once, reuse | 5,000 | 10x | 11x @20k | constant overhead × N |

**Result:** 7/7 measurably faster. Median speedup **186x** at the largest sizes
tested, **51x** at representative mid-range sizes, range 2.5x -> 2,521x.

**Honest reading:**
- Findings 1, 2, 4, 5 are genuine **O(n²)->O(n)** collapses; their speedup grows
  with n (we report several sizes rather than cherry-pick one).
- Finding 3 (string-concat) is the floor at **2.5x**: CPython special-cases `s += t`
  with an in-place resize when the accumulator is singly-referenced, so the textbook
  O(n²) is mostly dodged in this exact shape. The `"".join` fix is still faster and
  *is* O(n²) on PyPy / when the accumulator is aliased; here the win is modest, and
  we report it as such.
- Finding 6 (N+1) is **~30x against local SQLite** with zero network latency: the
  conservative floor; a networked DB at 0.5 to 5 ms/round-trip multiplies it.
- Finding 7 (connect-in-loop) is **~10x and flat in n**, a constant-overhead win,
  not an algorithmic collapse. This is precisely why the pillar ranks by centrality
  (E3): runtime magnitude alone isn't the signal, blast radius is.

The FastAPI finding (#2) was replicated faithfully from real source (`visited`
list, `visited.append(cache_key)`, `if cache_key in visited: continue`) over a
synthesized dependency tree; the others are the canonical minimal form of each
marker.

---

## 4. Experiment E3: does ranking surface what matters?

**Question:** does ranking by centrality × churn × severity concentrate the
findings where real perf fixes later landed, vs detection order or severity alone?

**Method:** mine each repo's git history for perf-fix commits (`perf:`, `N+1`,
`optimize`, `O(n`, `latency`, `batch`, ...); take the changed line ranges). A finding
is **impactful** if it sits within ±8 lines of such a change (line-level) or in a
file that was ever perf-fixed (file-level, drift-immune). Score three rankings with
Precision@k and NDCG@20.

### repowise (primary; young repo, line-stable, fully integrity-checked)

n = 209 findings, 49 perf-fix commits, 108 impactful (base rate 0.52).

| Ranking | P@5 | P@10 | P@20 | NDCG@20 |
|---------|----:|-----:|-----:|--------:|
| unranked (detection order) | 0.20 | 0.10 | 0.30 | 0.267 |
| severity only | 0.20 | 0.10 | 0.25 | 0.292 |
| severity × **centrality** (clean) | 0.80 | 0.50 | 0.30 | 0.436 |
| severity × churn (clean, excl. perf commits) | 0.40 | 0.70 | 0.70 | 0.599 |
| **severity × centrality × churn** | **1.00** | **0.70** | **0.75** | **0.755** |

**Integrity checks (this is the part that makes it trustworthy):**
- **Centrality is clean signal.** Call-graph in-degree is structurally independent
  of commit history. Alone it lifts NDCG 0.29 -> **0.44** and P@5 0.20 -> **0.80**,
  zero circularity.
- **Churn is not an artifact.** Worry: a perf-fix commit both defines the label and
  increments the file's churn. Recomputing churn **excluding the perf-fix commits
  themselves** barely moved it (NDCG 0.634 -> 0.599). The hotspot effect is real.
- **Combined: NDCG 0.755 vs 0.292** (2.6×), P@5 = 1.00, well above the 0.52 base.

### openclaw (external generalization; 20k-file monorepo)

n = 2,634 findings, 2,205 perf-fix commits. File-level label (drift-immune, base 0.58):

| Ranking | P@5 | P@10 | P@20 | NDCG@20 |
|---------|----:|-----:|-----:|--------:|
| unranked | 0.00 | 0.00 | 0.35 | 0.248 |
| severity only | 0.20 | 0.40 | 0.30 | 0.264 |
| **severity × centrality × churn** | **0.60** | **0.80** | 0.55 | **0.595** |

Confirms the direction at 12× the finding count: **2.4× lift** over detection order,
P@10 0.80.

### Django (honest non-result; documents the proxy's boundary)

n = 196 findings, but only **1–6 impactful** regardless of line/file granularity or
history window. Root cause: **Django's static-visible findings (DDL/migration/test
infra) and Django's actual perf work are disjoint.** Django's famous N+1s are
**ORM lazy-load** (a query hidden behind attribute access), the explicitly
**static-blind** class we deliberately don't claim. So the impact-label proxy has
almost no positives to rank. This is a property of the corpus + label, not a ranking
failure, and it precisely illustrates the boundary of what static detection can
claim. The proxy only has signal where a project's perf work is the
statically-visible call-in-loop kind (repowise, openclaw).

---

## 5. A real false-positive class, caught and fixed

The hard-corpus precision gate (§3) surfaced a systematic TS/JS false positive:
`for...of` over an **inline array literal** (`for (const p of ["/a", "/b"])`) or an
ALL_CAPS constant was flagged as io-in-loop, though the iteration count is a small
compile-time constant. The Python dialect already skipped this; the TS/JS dialect
did not. Fix shipped (ported the Python `is_constant_loop` logic), with regression
tests; the defect score is byte-for-byte unchanged. **Measured impact on openclaw
(the ~20k-file monorepo): io_in_loop 1,805 -> 1,615 (−190 false positives), with no
true-positive loss.** Shipped as repowise PR #545.

This is the gate doing its job: a precision discipline that catches its own FP
classes at scale, not a detector taken on faith.

---

## 6. Threats to validity

- **Construct.** The "impactful" label is a proxy (a perf fix landed nearby). It
  undercounts: most genuine N+1s are never fixed, so a real finding far from any
  perf commit counts against the proxy. Treat E3 numbers as a floor.
- **Corpus dependence of E3.** As Django shows, the proxy needs the project's perf
  work to be statically visible (call-in-loop), not ORM-lazy-load. Reported, not
  hidden.
- **Static call-graph soundness.** Dynamic dispatch, reflection, monkey-patching
  produce no edge -> missed cross-function findings. This caps **recall, not
  precision** (a missing edge is a missed finding, never a false one).
- **Line drift.** The line-level impact label degrades on old repos (historical
  diff line numbers diverge from HEAD). Mitigated with a file-level label and a
  recent-history window; both reported.
- **Single-rater labels.** Precision labels are currently one or two raters;
  inter-rater agreement (κ) on a shared subsample is a planned addition.

---

## 7. Reproduce

```bash
# E1 baselines (Python / TS / Go)
python probes/probe_baseline_ruff.py   test-repos/django  django
python probes/probe_baseline_eslint.py test-repos/dub     dub
golangci-lint run --enable=prealloc,makezero,bodyclose --output.json.path=out.json ./...

# Precision gate (any repo)
python probes/probe_perf_multilang.py  test-repos/<repo>  <label>   # then hand-label the queue

# E3 ranking
python probes/probe_perf_ranking.py    test-repos/openclaw openclaw         # all history
python probes/probe_perf_ranking.py    test-repos/django   django 2023-01-01 # recent window

# E4 runtime confirmation (before/after microbenchmarks)
.venv\Scripts\python.exe benchmarks/bench_01_membership_list.py   # ... bench_02..04, 06, 07
node                       benchmarks/bench_05_array_spread_in_reduce.js
```

Detector source lives in the public `repowise` package
(`packages/core/.../analysis/health/perf/`). Probe harnesses and raw labeled data
are in the `performance-pillar` workspace.

---

## 8. Status & roadmap

**Shipped:** the six-language detector (Python, TS/JS, Java, Go, C#, Rust), the
three platform primitives, ~20 gated markers, E1 (3 languages measured),
precision validation (4 corpora), E3 (2 repos + boundary characterization), E4
(runtime confirmation, 7 findings), and PR #545 (the constant-loop FP fix).

**Next:** the measured clippy-vs-Repowise head-to-head on the Rust corpus (the
dialect ships; the end-to-end run is blocked on the Windows build toolchain);
inter-rater agreement (E5); the full component ablation (E2, isolating each
platform asset's contribution to precision).
