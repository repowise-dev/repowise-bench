"""Finding 2: O(n^2) list-membership in a recursive walk.

Real instance: FastAPI fastapi/dependencies/utils.py get_flat_dependant.
The function carries `visited: list`, does `visited.append(cache_key)`, and
guards recursion with `if skip_repeats and sub_dependant.cache_key in visited`.
Each membership test is O(len(visited)); over a dependency tree of N nodes that
is O(N^2). Fix: `visited` becomes a set (membership O(1)).

We replicate the exact loop shape faithfully against a synthetic dependency tree
(no FastAPI import needed; the offending 6 lines are reproduced verbatim in
behavior). The tree is a wide tree of unique nodes so skip_repeats never short-
circuits, exercising the worst case the pattern incurs.

1 run per config. Reports wall-clock + speedup at increasing node counts.
"""
import sys
import time

sys.setrecursionlimit(100_000)


class Dep:
    __slots__ = ("cache_key", "dependencies")

    def __init__(self, cache_key):
        self.cache_key = cache_key
        self.dependencies = []


def make_tree(n_nodes, breadth=8):
    """A tree of n_nodes unique-cache-key dependants (breadth children each)."""
    root = Dep(0)
    nodes = [root]
    nxt = 1
    i = 0
    while nxt < n_nodes:
        parent = nodes[i]
        i += 1
        for _ in range(breadth):
            if nxt >= n_nodes:
                break
            child = Dep(nxt)
            parent.dependencies.append(child)
            nodes.append(child)
            nxt += 1
    return root


def flat_slow(dependant, visited=None):
    """visited as a list -- the pattern as written in FastAPI."""
    if visited is None:
        visited = []
    visited.append(dependant.cache_key)
    for sub in dependant.dependencies:
        if sub.cache_key in visited:  # O(len(visited)) each time
            continue
        flat_slow(sub, visited=visited)


def flat_fast(dependant, visited=None):
    """visited as a set -- the fix."""
    if visited is None:
        visited = set()
    visited.add(dependant.cache_key)
    for sub in dependant.dependencies:
        if sub.cache_key in visited:  # O(1)
            continue
        flat_fast(sub, visited=visited)


def bench(fn, tree):
    t = time.perf_counter()
    fn(tree)
    return (time.perf_counter() - t) * 1000.0  # ms


if __name__ == "__main__":
    print("Finding 2: FastAPI get_flat_dependant visited list -> set (O(n^2) -> O(n))")
    for n in (500, 2_000, 8_000):
        tree = make_tree(n)
        b = bench(flat_slow, tree)
        a = bench(flat_fast, tree)
        print(f"  nodes={n:>6}  before={b:9.2f}ms  after={a:8.3f}ms  speedup={b / max(a, 1e-6):8.1f}x")
