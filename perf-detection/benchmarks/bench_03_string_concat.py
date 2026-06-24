"""Finding 3: string_concat_in_loop (`s += part` in a loop).

Pattern: accumulating a string with `+=` inside a loop. Python strings are
immutable, so each `+=` copies the whole accumulator: O(n^2) total.
Real instances: repowise itself + many TS repos.
Fix: append parts to a list, then `"".join(parts)` once. O(n).

1 run per config. Reports wall-clock + speedup.
"""
import time


def bench(fn, *a):
    t = time.perf_counter()
    fn(*a)
    return (time.perf_counter() - t) * 1000.0  # ms


def slow(n):  # the pattern as written
    s = ""
    for i in range(n):
        s += "x" * 16
    return s


def fast(n):  # the fix
    parts = []
    for i in range(n):
        parts.append("x" * 16)
    return "".join(parts)


if __name__ == "__main__":
    print("Finding 3: string_concat_in_loop (s += part -> list + join)")
    for n in (5_000, 20_000, 80_000):
        b, a = bench(slow, n), bench(fast, n)
        print(f"  n={n:>7}  before={b:9.2f}ms  after={a:8.3f}ms  speedup={b / max(a, 1e-6):8.1f}x")
