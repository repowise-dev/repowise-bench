"""Step 1 of Session C: does shortening a session cut its cost variance?

Pre-registration, fixed before this file read a single row:
`local-stash/competitive-proof/session-cost-eval/06_SESSION_C_PREREG.md` section 1.

The quantity is the CV of `cost_usd` summed over T01-T03 for bare arms on cell A,
against the 31.9% measured over all 11 tasks.

Two things this reader must not do, both of which have happened in this arc:

  * sum the `session_summary` row in with the per-task rows, which double-counts
    the whole session and prints a plausible number roughly 2x too high;
  * report a window from an arm that is missing one of its three tasks, which
    compares totals over unequal task counts -- explicitly a standing trap.

Both are asserted, and `--self-test` proves each assertion fires in BOTH
directions on the real row shape rather than on a happy path.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as stats
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2]
RESULTS = BENCH / "results" / "bakeoff_2026_08" / "session-cost-eval"

WINDOW = ("T01", "T02", "T03")
TOKEN_KEYS = ("input_tokens", "output_tokens",
              "cache_read_tokens", "cache_creation_tokens")


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            r["_src"] = p.name
            rows.append(r)
    return rows


def windows(rows: list[dict], arms: list[str], condition: str,
            window: tuple = WINDOW) -> dict:
    """Per-arm totals over `window`. Raises on a short or duplicated window."""
    out = {}
    for arm in arms:
        # `type` is absent on per-task rows and 'session_summary' on the tail
        # row. Filtering on task_id alone is NOT enough: the summary carries no
        # task_id, but a future summary that grew one would be silently summed.
        got = [r for r in rows
               if r.get("arm") == arm
               and r.get("condition") == condition
               and r.get("type") != "session_summary"
               and r.get("task_id") in window]
        ids = sorted(r["task_id"] for r in got)
        if ids != sorted(window):
            raise ValueError(
                f"{arm}: window is {ids}, expected {sorted(window)}. Refusing "
                f"to compare totals over unequal task counts.")
        out[arm] = {
            "cost": sum(float(r.get("cost_usd") or 0.0) for r in got),
            "billed": sum(sum(int(r.get(k) or 0) for k in TOKEN_KEYS)
                          for r in got),
            "turns": {r["task_id"]: int(r.get("num_turns") or 0) for r in got},
            "src": got[0]["_src"],
        }
    return out


def cv(xs: list[float]) -> dict:
    m = stats.mean(xs)
    sd = stats.stdev(xs) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": m, "sd": sd,
            "cv_pct": 100.0 * sd / m if m else float("nan"),
            "spread_pct": 100.0 * (max(xs) - min(xs)) / min(xs) if min(xs) else float("nan")}


def sigma_ci(n: int) -> tuple[float, float]:
    """95% chi-square CI on sigma, as multipliers of the point estimate.

    Table lookup rather than scipy: this bench venv has no scipy and adding one
    for six numbers is not worth the dependency. Values are sqrt((n-1)/chi2)
    at the 0.975 and 0.025 quantiles.
    """
    table = {2: (0.521, 6.285), 3: (0.566, 3.729), 4: (0.599, 2.874),
             5: (0.624, 2.453), 6: (0.644, 2.202), 7: (0.661, 2.035),
             8: (0.675, 1.916), 9: (0.688, 1.826), 10: (0.699, 1.755),
             11: (0.708, 1.698), 12: (0.717, 1.651)}
    return table.get(n - 1, (float("nan"), float("nan")))


def report(label: str, w: dict) -> dict:
    costs = [v["cost"] for v in w.values()]
    billed = [float(v["billed"]) for v in w.values()]
    c, b = cv(costs), cv(billed)
    lo, hi = sigma_ci(c["n"])
    print(f"\n--- {label}  (n={c['n']})")
    for arm, v in sorted(w.items()):
        print(f"    {arm:<16} ${v['cost']:.4f}  billed={v['billed']:>10,}  "
              f"turns={v['turns']}  [{v['src']}]")
    print(f"    cost   mean ${c['mean']:.4f}  sd ${c['sd']:.4f}  "
          f"CV {c['cv_pct']:.1f}%  spread {c['spread_pct']:.1f}%")
    print(f"    billed mean {b['mean']:,.0f}  sd {b['sd']:,.0f}  "
          f"CV {b['cv_pct']:.1f}%")
    if not math.isnan(lo):
        print(f"    95% CI on sigma: {lo:.2f}x to {hi:.2f}x  "
              f"-> CV in [{c['cv_pct']*lo:.1f}%, {c['cv_pct']*hi:.1f}%]")
    return {"cost": c, "billed": b}


def detours(w: dict) -> None:
    """Per-task turns against the across-run median. A detour is >= 2x."""
    print("\n--- mechanism: per-task turns vs across-run median (detour = >=2x)")
    for tid in WINDOW:
        vals = {arm: v["turns"].get(tid, 0) for arm, v in w.items()}
        med = stats.median(vals.values())
        flags = [f"{a}={n}" for a, n in sorted(vals.items())
                 if med and n >= 2 * med]
        print(f"    {tid}: median {med:.0f}  "
              f"{'DETOUR ' + ', '.join(flags) if flags else 'no detour'}")


# ---------------------------------------------------------------------------

def self_test() -> int:
    """Every assertion proved in both directions on the real row shape."""
    ok, fail = 0, []

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        if cond:
            ok += 1
        else:
            fail.append(name)

    base = [
        {"arm": "a", "condition": "unenforced", "task_id": "T01",
         "cost_usd": 1.0, "input_tokens": 10, "num_turns": 5},
        {"arm": "a", "condition": "unenforced", "task_id": "T02",
         "cost_usd": 2.0, "input_tokens": 20, "num_turns": 6},
        {"arm": "a", "condition": "unenforced", "task_id": "T03",
         "cost_usd": 3.0, "input_tokens": 30, "num_turns": 7},
    ]
    for r in base:
        r["_src"] = "t"

    # 1. happy path
    w = windows(base, ["a"], "unenforced")
    check("sums the window", abs(w["a"]["cost"] - 6.0) < 1e-9)
    check("sums billed", w["a"]["billed"] == 60)

    # 2. the summary row must NOT be summed -- fires in both directions
    summ = dict(base[0], type="session_summary", task_id="T01", cost_usd=99.0)
    w2 = windows(base + [summ], ["a"], "unenforced")
    check("summary row excluded", abs(w2["a"]["cost"] - 6.0) < 1e-9)
    try:
        # and proof the exclusion is doing work: without the type filter this
        # window would hold 4 rows and the guard below would have caught it.
        got = [r for r in base + [summ] if r.get("task_id") in WINDOW]
        check("summary WOULD have been summed without the filter",
              abs(sum(r["cost_usd"] for r in got) - 105.0) < 1e-9)
    except Exception:
        fail.append("summary counter-proof")

    # 3. a short window must RAISE, not silently under-count
    try:
        windows(base[:2], ["a"], "unenforced")
        fail.append("short window did not raise")
    except ValueError:
        ok += 1
    # ...and must not raise on the complete one
    try:
        windows(base, ["a"], "unenforced")
        ok += 1
    except ValueError:
        fail.append("complete window wrongly raised")

    # 4. a duplicated task must RAISE
    try:
        windows(base + [dict(base[0])], ["a"], "unenforced")
        fail.append("duplicate task did not raise")
    except ValueError:
        ok += 1

    # 5. condition filter fires in both directions
    other = [dict(r, condition="enforced") for r in base]
    try:
        windows(other, ["a"], "unenforced")
        fail.append("wrong-condition rows were accepted")
    except ValueError:
        ok += 1
    check("right-condition rows accepted",
          abs(windows(other, ["a"], "enforced")["a"]["cost"] - 6.0) < 1e-9)

    # 6. cv arithmetic against a hand-checked case
    c = cv([1.0, 2.0, 3.0])
    check("cv arithmetic", abs(c["cv_pct"] - 50.0) < 1e-9)

    print(f"self-test: {ok} passed, {len(fail)} failed")
    for f in fail:
        print(f"  FAIL {f}")
    return 0 if not fail else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--short-arms", default="",
                    help="comma-separated c0-short-rN arms, once they exist")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    rows = load([RESULTS / "sessions_s3.jsonl", RESULTS / "sessions.jsonl",
                 RESULTS / "sessions_s2.jsonl"])

    print("=" * 72)
    print("SESSION C STEP 1 -- short-session variance")
    print("comparator: 31.9% CV on 11-task bare sessions (RESULT_S3.md)")
    print("=" * 72)

    free = windows(rows, ["c0-bare-r1", "c0-bare-r2", "c0-bare-r3"], "unenforced")
    r_free = report("FREE: T01-T03 prefix of the three 11-task bare runs", free)
    detours(free)

    # run 1's bare arm, same harness, same condition -> a legitimate 4th draw
    try:
        r1 = windows(rows, ["c0-bare"], "unenforced")
        free4 = {**free, **r1}
        report("FREE+run1: same harness, same condition, n=4", free4)
        detours(free4)
    except ValueError as exc:
        print(f"\n--- run-1 c0-bare not poolable: {exc}")
        free4 = free

    # cross-harness, reported and NOT pooled (standing rule E14)
    for arm in ("s2-c0bare",):
        try:
            w = windows(rows, [arm], "unenforced")
            print(f"\n--- CROSS-HARNESS sanity, NOT pooled: {arm} "
                  f"${w[arm]['cost']:.4f} over T01-T03")
        except ValueError as exc:
            print(f"\n--- {arm}: {exc}")

    if a.short_arms:
        short = windows(rows + load([RESULTS / "sessions_s3_short.jsonl"]),
                        [s for s in a.short_arms.split(",") if s], "unenforced")
        r_short = report("NEW: purpose-run 3-task bare sessions", short)
        detours(short)
        pooled = {**free4, **short}
        r_pool = report("POOLED: every same-harness 3-task bare window", pooled)
        detours(pooled)

        s = r_pool["cost"]["mean"]
        thresh = 31.9 * math.sqrt(6.01 / s)
        print(f"\n--- budget-equalised criterion (prereg 1.5)")
        print(f"    short session costs ${s:.4f}; a long one $6.01")
        print(f"    a fixed budget buys {6.01/s:.2f}x more short draws")
        print(f"    threshold: CV_short < 31.9% x sqrt({6.01/s:.2f}) = {thresh:.1f}%")
        print(f"    measured CV_short = {r_pool['cost']['cv_pct']:.1f}% -> "
              f"{'SHORT SESSIONS WIN per dollar' if r_pool['cost']['cv_pct'] < thresh else 'no gain per dollar'}")
        v = r_pool["cost"]["cv_pct"]
        verdict = ("CONFIRMED" if v <= 20 else
                   "REFUTED" if v >= 31.9 else "AMBIGUOUS")
        print(f"\n    PREREG 1.5 VERDICT: {verdict} (CV_short {v:.1f}%)")
    else:
        print("\n(no --short-arms given; free draws only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
