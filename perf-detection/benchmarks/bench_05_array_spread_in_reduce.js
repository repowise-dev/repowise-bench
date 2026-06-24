// Finding 5: array_spread_in_reduce -- reduce((a, x) => [...a, x], [])
//
// Pattern: building an array inside a reduce by spreading the accumulator each
// step. `[...a, x]` allocates and copies the whole accumulator every iteration:
// O(n^2) total allocation/copy.
// Real instance: dub (TypeScript).
// Fix: push into one array (a.push(x); return a), or a plain loop. O(n).
//
// 1 run per config. Reports wall-clock (performance.now) + speedup.

function bench(fn, n) {
  const t = performance.now();
  fn(n);
  return performance.now() - t; // ms
}

function slow(n) {
  // the pattern as written
  return Array.from({ length: n }, (_, i) => i).reduce((a, x) => [...a, x], []);
}

function fast(n) {
  // the fix: mutate one accumulator
  return Array.from({ length: n }, (_, i) => i).reduce((a, x) => {
    a.push(x);
    return a;
  }, []);
}

console.log("Finding 5: array_spread_in_reduce ([...a,x] -> a.push(x)) [Node]");
for (const n of [2000, 8000, 20000]) {
  const b = bench(slow, n);
  const a = bench(fast, n);
  console.log(
    `  n=${String(n).padStart(7)}  before=${b.toFixed(2).padStart(9)}ms  ` +
      `after=${a.toFixed(3).padStart(8)}ms  speedup=${(b / Math.max(a, 1e-6)).toFixed(1).padStart(8)}x`
  );
}
