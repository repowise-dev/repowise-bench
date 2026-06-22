"""Finding 1: membership_test_against_list_in_loop (`x in big_list` in a loop).

Pattern: building a "seen" collection and testing membership with a list.
Real instances: FastAPI-style dedup loops; Django had ~50 of these.
Fix: list -> set. O(n*m) -> O(n).

1 run per config (large n so the gap is unambiguous). Reports wall-clock + speedup.
"""
import time


def bench(fn, *a):
    t = time.perf_counter()
    fn(*a)
    return (time.perf_counter() - t) * 1000.0  # ms


def slow(n):  # the pattern as written: membership against a growing list
    seen = []
    for i in range(n):
        if i in seen:
            continue
        seen.append(i)


def fast(n):  # the fix: membership against a set
    seen = set()
    for i in range(n):
        if i in seen:
            continue
        seen.add(i)


if __name__ == "__main__":
    print("Finding 1: membership_test_against_list_in_loop (list -> set)")
    for n in (1_000, 10_000, 50_000):
        b, a = bench(slow, n), bench(fast, n)
        print(f"  n={n:>7}  before={b:9.2f}ms  after={a:8.3f}ms  speedup={b / max(a, 1e-6):8.1f}x")
