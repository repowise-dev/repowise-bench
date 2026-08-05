"""Grade a stratified subset with a second and a third judge, and publish the agreement.

PLAN.md has specified this since the judge was chosen and it has never been run.
Session 9 made it load-bearing rather than merely owed, because there are now
**two** judges in the field rather than one:

  * `_resolve_judge_model` picks the judge from the AGENT's family —
    `gpt-5.6-luna` for a Claude agent, `claude-sonnet-5` for a Codex one. That is
    correct (a GPT judge grading a GPT agent is the same self-preference bug
    arrived at from the other side) and it means a cross-HARNESS table carries
    two different graders. Any Codex-vs-Claude row is uninterpretable until the
    size of that difference is known.
  * PLAN.md's original mitigation is luna against `gpt-5.6-terra` on a stratified
    subset, which measures judge NOISE within one family.

So both comparisons are run here, over the same cells, because they answer
different questions and only one of them was ever specified:

    luna  vs  terra            -> how noisy is the instrument
    luna  vs  claude-sonnet-5  -> can a Codex row and a Claude row share a table

**The trap this exists to pre-empt, and it cuts in our favour.** We intend to
claim quality at parity. A noisy judge biases TOWARD parity, because noise washes
out real differences, so a cheap judge makes our own headline claim easier to
make. That is exactly the self-serving methodology choice this workstream exists
to avoid, which is why the agreement number is published next to the run rather
than checked privately.

Reported: mean absolute difference on the 1-10 rubric mean, Pearson and Spearman
correlation, and the per-cell disagreements sorted worst-first so a large mean
absolute difference can be traced to cells rather than asserted.

Usage:
    python -m harness.judge_agreement \
        --results results/bakeoff_2026_08/rung6/layerb_stratified_django \
        --judges gpt-5.6-terra,claude-sonnet-5 \
        --out results/bakeoff_2026_08/rung6/judge_agreement.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from harness.question_shapes import load as load_shapes
from harness.report_by_shape import judge_mean, load_rows
from harness.swe_qa_runner import judge_answer

BENCH_ROOT = Path(__file__).resolve().parents[1]
TASKS = BENCH_ROOT / "data" / "swe_qa" / "tasks.json"


def gold_answers() -> dict[str, dict]:
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))
    return {t["id"]: t for t in tasks}


def rank(xs: list[float]) -> list[float]:
    """Average ranks, so ties do not shift the correlation."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def pearson(a: list[float], b: list[float]):
    if len(a) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--judges", default="gpt-5.6-terra,claude-sonnet-5")
    ap.add_argument("--per-shape", type=int, default=2,
                    help="cells per (shape, arm) to re-grade. Stratified so the "
                         "agreement is not measured on one question class.")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = load_rows(Path(args.results))
    shape_of = {q: r["shape"] for q, r in load_shapes()["questions"].items()}
    golds = gold_answers()

    # Cells worth re-grading: clean, graded once already, and with an answer.
    usable = [r for r in rows
              if not r.get("error") and judge_mean(r) is not None and r.get("answer")]

    # Stratify the subset by (shape, arm) so the agreement number is not
    # measured on one question class or one arm. Deterministic: first n by task
    # id within each cell of the grid, no seed, no draw to argue about.
    picked: list[dict] = []
    grid: dict = {}
    for r in sorted(usable, key=lambda r: (str(r.get("task_id")), str(r.get("condition")))):
        key = (shape_of.get(r.get("task_id"), "?"), r.get("arm") or r.get("condition"))
        grid.setdefault(key, [])
        if len(grid[key]) < args.per_shape:
            grid[key].append(r)
            picked.append(r)

    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    print(f"re-grading {len(picked)} cells with {judges}")
    print(f"grid: {len(grid)} (shape, arm) combinations, <= {args.per_shape} each")

    records = []
    for i, r in enumerate(picked, 1):
        tid = r.get("task_id")
        task = golds.get(tid)
        if not task:
            print(f"  [{i}/{len(picked)}] {tid}: no gold answer, skipped")
            continue
        rec = {
            "task_id": tid,
            "arm": r.get("arm") or r.get("condition"),
            "shape": shape_of.get(tid, "?"),
            "original_judge": r.get("judge_model"),
            "original_score": judge_mean(r),
            "scores": {},
        }
        for j in judges:
            try:
                res = judge_answer(task["question"], task["answer"], r["answer"], j)
            except Exception as exc:  # noqa: BLE001 - a judge failure is data
                rec["scores"][j] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
                continue
            if "error" in res:
                rec["scores"][j] = {"error": res["error"][:200]}
                continue
            vals = [v for v in res.values() if isinstance(v, (int, float))]
            rec["scores"][j] = {"mean": statistics.mean(vals) if vals else None,
                                "raw": res}
        records.append(rec)
        got = {j: (rec["scores"][j].get("mean") if isinstance(rec["scores"][j], dict) else None)
               for j in judges}
        print(f"  [{i}/{len(picked)}] {tid:12s} {rec['arm']:18s} "
              f"orig {rec['original_score']:.2f} -> {got}")

    # ------------------------------------------------------------- agreement
    print()
    print("=" * 84)
    print("AGREEMENT against the judge that produced the run")
    print("=" * 84)
    summary = {}
    for j in judges:
        pairs = [(r["original_score"], r["scores"][j]["mean"])
                 for r in records
                 if isinstance(r["scores"].get(j), dict)
                 and r["scores"][j].get("mean") is not None]
        failed = len(records) - len(pairs)
        if not pairs:
            print(f"{j:20s}  no gradeable pairs ({failed} failures)")
            summary[j] = {"n": 0, "failures": failed}
            continue
        a = [x for x, _ in pairs]
        b = [y for _, y in pairs]
        mad = statistics.mean([abs(x - y) for x, y in pairs])
        bias = statistics.mean([y - x for x, y in pairs])
        p = pearson(a, b)
        s = pearson(rank(a), rank(b))
        within_1 = sum(1 for x, y in pairs if abs(x - y) <= 1.0) / len(pairs)
        summary[j] = {"n": len(pairs), "failures": failed, "mean_abs_diff": mad,
                      "bias_vs_original": bias, "pearson": p, "spearman": s,
                      "within_1_point": within_1}
        print(f"{j:20s}  n={len(pairs):3d}  MAD={mad:.2f}  bias={bias:+.2f}  "
              f"pearson={p if p is None else round(p, 3)}  "
              f"spearman={s if s is None else round(s, 3)}  "
              f"within 1pt={within_1:.0%}  judge failures={failed}")

    print()
    print("worst disagreements, so a MAD can be traced to cells rather than asserted:")
    for j in judges:
        rs = [(abs(r["original_score"] - r["scores"][j]["mean"]), r)
              for r in records
              if isinstance(r["scores"].get(j), dict)
              and r["scores"][j].get("mean") is not None]
        # Sort on the magnitude ALONE. `sorted` on (float, dict) tuples falls
        # through to comparing the dicts whenever two disagreements tie, which
        # raises TypeError after every judge call is already paid for.
        for d, r in sorted(rs, key=lambda pair: pair[0], reverse=True)[:5]:
            print(f"  {j:18s} {r['task_id']:12s} {r['arm']:18s} {r['shape']:18s} "
                  f"{r['original_score']:.2f} vs {r['scores'][j]['mean']:.2f}  (d={d:.2f})")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"summary": summary, "records": records,
             "original_judge": sorted({r["original_judge"] for r in records}),
             "per_shape": args.per_shape},
            indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
