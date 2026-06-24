# Runtime confirmation - raw benchmark results

Before/after microbenchmarks for 7 already-verified performance findings. Each
script times the pattern **as written** against the **obvious fix**, same inputs,
back to back on one machine. 1 run per config (n chosen large enough that the gap
is unambiguous). Reproduce with the commands at the bottom.

**Environment:** Python 3.13.5, Node v22.19.0, sqlite 3.50.2, win32.
All runs 2026-06-22.

## Raw output

```
Finding 1: membership_test_against_list_in_loop (list -> set)
  n=   1000  before=     3.16ms  after=   0.058ms  speedup=    54.3x
  n=  10000  before=   345.51ms  after=   0.571ms  speedup=   605.0x
  n=  50000  before=  8307.74ms  after=   3.295ms  speedup=  2521.0x

Finding 2: FastAPI get_flat_dependant visited list -> set (O(n^2) -> O(n))
  nodes=   500  before=     1.07ms  after=   0.076ms  speedup=    14.0x
  nodes=  2000  before=    17.10ms  after=   0.338ms  speedup=    50.6x
  nodes=  8000  before=   216.26ms  after=   1.164ms  speedup=   185.8x

Finding 3: string_concat_in_loop (s += part -> list + join)
  n=   5000  before=     0.39ms  after=   0.155ms  speedup=     2.5x
  n=  20000  before=     1.87ms  after=   0.756ms  speedup=     2.5x
  n=  80000  before=    17.07ms  after=   3.007ms  speedup=     5.7x

Finding 4: list_insert_zero_in_loop (insert(0,x) -> append+reverse / deque)
  n=   5000  before=     5.79ms  after(append+rev)=  0.157ms  speedup=    36.8x  (deque=0.173ms, 33.4x)
  n=  20000  before=    92.84ms  after(append+rev)=  0.570ms  speedup=   162.8x  (deque=0.673ms, 137.9x)
  n=  50000  before=   560.20ms  after(append+rev)=  1.600ms  speedup=   350.1x  (deque=1.694ms, 330.7x)

Finding 5: array_spread_in_reduce ([...a,x] -> a.push(x)) [Node]
  n=   2000  before=     4.23ms  after=   0.185ms  speedup=    22.9x
  n=   8000  before=    40.45ms  after=   0.708ms  speedup=    57.2x
  n=  20000  before=   927.50ms  after=   1.153ms  speedup=   804.6x

Finding 6: N+1 query (per-item SELECT -> single WHERE id IN (...))
  N=   100  before=     3.10ms  after=   0.198ms  speedup=    15.6x
  N=   500  before=    14.81ms  after=   0.513ms  speedup=    28.9x
  N=  2000  before=    54.27ms  after=   1.816ms  speedup=    29.9x

Finding 7: resource_construction_in_loop (connect-per-iter -> connect once)
  N=  1000  before=   307.44ms  after=  33.461ms  speedup=     9.2x
  N=  5000  before=  1480.15ms  after= 143.125ms  speedup=    10.3x
  N= 20000  before=  6443.80ms  after= 600.081ms  speedup=    10.7x
```

## Reproduce

```
.venv\Scripts\python.exe repowise-bench/perf-detection/benchmarks/bench_01_membership_list.py
.venv\Scripts\python.exe repowise-bench/perf-detection/benchmarks/bench_02_fastapi_flat_dependant.py
.venv\Scripts\python.exe repowise-bench/perf-detection/benchmarks/bench_03_string_concat.py
.venv\Scripts\python.exe repowise-bench/perf-detection/benchmarks/bench_04_list_insert_zero.py
node                       repowise-bench/perf-detection/benchmarks/bench_05_array_spread_in_reduce.js
.venv\Scripts\python.exe repowise-bench/perf-detection/benchmarks/bench_06_n_plus_1_query.py
.venv\Scripts\python.exe repowise-bench/perf-detection/benchmarks/bench_07_connect_in_loop.py
```

## Honest notes

- **Findings 1, 2, 4, 5 are O(n^2) -> O(n) collapses.** The speedup grows with n,
  so the headline number depends on the input size. We report several n per finding
  rather than one cherry-picked size.
- **Finding 3 (string concat) is the modest one: ~2.5x.** This is expected and we
  report it honestly. CPython special-cases `s += t` with an in-place buffer resize
  when the accumulator has a single reference, so the worst-case O(n^2) is largely
  avoided in this exact micro-shape. The `"".join` fix is still faster (and is
  genuinely O(n^2) in other interpreters / PyPy / when the accumulator is aliased),
  but the gain here is small. A modest true number beats a fake big one.
- **Finding 6 (N+1) is ~30x** at N>=500 against a local SQLite (zero network
  latency). Against a real networked database where each round-trip is 0.5-5 ms,
  the same N+1 -> batched fix is worth orders of magnitude more; SQLite is the
  conservative floor.
- **Finding 7 (connect-in-loop) is ~10x and flat across n** - it is a
  *constant-overhead* finding (per-connect cost saved N times), not an algorithmic
  collapse, so the ratio does not grow with n. This is exactly the distinction the
  pillar's centrality ranking (E3) exists to handle: a 10x-but-on-a-cold-path
  finding should rank below a 2x-but-on-the-hot-path one.
