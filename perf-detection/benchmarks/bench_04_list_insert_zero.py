"""Finding 4: list_insert_zero_in_loop (`lst.insert(0, x)` in a loop).

Pattern: prepending to a list inside a loop. `list.insert(0, x)` shifts every
existing element right by one: O(n) per insert, O(n^2) over the loop.
Real instances: Django (7 found).
Fix: append (O(1) amortized) then reverse once, or use collections.deque.appendleft.
Both are O(n) total.

1 run per config. Reports wall-clock + speedup.
"""
import time
from collections import deque


def bench(fn, *a):
    t = time.perf_counter()
    fn(*a)
    return (time.perf_counter() - t) * 1000.0  # ms


def slow(n):  # the pattern as written
    out = []
    for i in range(n):
        out.insert(0, i)
    return out


def fast(n):  # the fix: append + reverse
    out = []
    for i in range(n):
        out.append(i)
    out.reverse()
    return out


def fast_deque(n):  # the alternative fix: deque.appendleft
    out = deque()
    for i in range(n):
        out.appendleft(i)
    return out


if __name__ == "__main__":
    print("Finding 4: list_insert_zero_in_loop (insert(0,x) -> append+reverse / deque)")
    for n in (5_000, 20_000, 50_000):
        b = bench(slow, n)
        a = bench(fast, n)
        d = bench(fast_deque, n)
        print(
            f"  n={n:>7}  before={b:9.2f}ms  after(append+rev)={a:7.3f}ms  "
            f"speedup={b / max(a, 1e-6):8.1f}x  (deque={d:.3f}ms, {b / max(d, 1e-6):.1f}x)"
        )
