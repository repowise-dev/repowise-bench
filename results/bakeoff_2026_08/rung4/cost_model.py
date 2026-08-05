"""Layer A / Layer B wall-clock cost model, built only from measured rates.

Every per-index number here was measured in rung 4 on this machine, clean CPU,
one run per cell. Nothing is extrapolated from a vendor claim. Where a repo has
not been measured the entry is None and the model refuses to guess.

The model exists to answer one question Raghav asked directly: how long does the
whole benchmark take, and which knob actually moves it.

Key structural fact that drives everything: **Layer A pins every instance to its
own base_commit, so index count = instance count x arm count.** Instance count
is a dial (stratified subsample). Repo size is not.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# MEASURED, rung 4, clean CPU, seconds per full index build
# ---------------------------------------------------------------------------
MEASURED = {
    #  repo:      {arm: seconds}                      files    instances
    "django": ({"repowise": 326.4, "codegraph": 8.7, "crg": 52.5, "graphify": 98.2}, 2579, 80),
    "cli": ({"repowise": 95.0, "codegraph": 3.3, "crg": 9.9, "graphify": 16.4}, 765, 32),
    # dropped from the bake-off (finding A6), kept for the rate curve only
    "svelte": ({"repowise": 162.5, "codegraph": 7.7, "crg": 20.1, "graphify": 61.6}, 3287, 28),
    # REJECTED as a bake-off repo, measured anyway. repowise cell ran under
    # light contention (the rung-5 self-index overlapped it), so 1800.2s is an
    # upper bound; the competitor arms were not measured at all, so the
    # per-instance total below counts repowise only and is an UNDER-estimate of
    # the real all-arms cost.
    "mui": ({"repowise": 1800.2}, 30489, 45),
    "prettier": (None, None, 9),
}

# Arms that build a per-instance index. Serena is LSP-backed and builds none.
# DeepWiki cannot be pinned to a commit (finding E2) and is disqualified.
INDEX_ARMS = ["repowise", "codegraph", "crg", "graphify"]


def layer_a_hours(repos: dict[str, int], arms=INDEX_ARMS) -> tuple[float, dict]:
    """repos: {name: instances_to_run}. Returns (total_hours, per_repo)."""
    total, detail = 0.0, {}
    for repo, n in repos.items():
        times, _files, _max_n = MEASURED[repo]
        if times is None:
            detail[repo] = None
            continue
        per_instance = sum(times[a] for a in arms if a in times)
        h = per_instance * n / 3600
        detail[repo] = {
            "instances": n,
            "s_per_instance_all_arms": round(per_instance, 1),
            "hours": round(h, 2),
        }
        total += h
    return total, detail


def report(label: str, repos: dict[str, int], arms=INDEX_ARMS) -> None:
    total, detail = layer_a_hours(repos, arms)
    print(f"\n=== {label} ===")
    unknown = [r for r, d in detail.items() if d is None]
    for repo, d in detail.items():
        if d is None:
            print(f"  {repo:10s} NOT MEASURED, refusing to estimate")
            continue
        print(
            f"  {repo:10s} {d['instances']:3d} inst x {d['s_per_instance_all_arms']:7.1f}s "
            f"(all {len(arms)} arms) = {d['hours']:6.2f} h"
        )
    print(f"  {'TOTAL':10s} {total:6.2f} h serial"
          + (f"   [+ {', '.join(unknown)} unmeasured]" if unknown else ""))
    for workers in (4, 8):
        print(f"             {total / workers:6.2f} h at {workers} workers")


if __name__ == "__main__":
    print("Layer A index cost. Measured rates only, rung 4, clean CPU.")
    print("Index count = instances x arms, because every instance pins its own base_commit.")

    # what the plan of record currently implies
    report("PLAN as written: full instance counts, django + cli",
           {"django": 80, "cli": 32})

    # stratified subsample, the PLAN's own n~=150 design
    report("Stratified subsample, ~n=100 across django + cli",
           {"django": 70, "cli": 30})

    report("Two repos, minimum defensible n (>=100 per DeepSource bar)",
           {"django": 68, "cli": 32})

    # What mui would have added. repowise arm only, so this UNDERSTATES it.
    print("\n=== REJECTED: what adding mui would have cost ===")
    mui_h = 1800.2 * 45 / 3600
    base_h, _ = layer_a_hours({"django": 80, "cli": 32})
    print(f"  mui  45 inst x 1800.2s (repowise arm ALONE) = {mui_h:6.2f} h")
    print(f"  Layer A without mui (all 4 arms)            = {base_h:6.2f} h")
    print(f"  Layer A with mui, mui counted at 1 arm only = {base_h + mui_h:6.2f} h"
          f"  ({(base_h + mui_h) / base_h:.1f}x)")
    print("  The real multiple is higher: mui's other three arms are unmeasured.")
