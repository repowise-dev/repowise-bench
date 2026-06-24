#!/usr/bin/env python3
"""Exact percentile-bootstrap TOST from the persisted per-replicate OOF-AUC delta
vectors (candidate_eval now writes `oof_auc_delta.delta_boot`). Replaces the
normal-approximation TOST of T0.2 for the headline signals, and firms up the two
boundary verdicts (code naturalness, review coverage).

Percentile-bootstrap TOST at SESOI Delta: the two one-sided achieved-significance
levels are p_lo = P(delta* <= -Delta) and p_hi = P(delta* >= +Delta), estimated as
the bootstrap-tail fractions; equivalence p = max(p_lo, p_hi), equivalence declared
at alpha when the 90% percentile CI [p5, p95] lies inside [-Delta, +Delta].

Read-only over results/*_scorecards_boot.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

RES = Path(__file__).resolve().parent.parent.parent / "repowise-bench" / "results"
# (signal label, file, card-container key, card key)
SIGNALS = [
    ("graph centrality (pagerank)", "centrality_scorecards_boot.json", "cards", "pagerank"),
    ("code naturalness", "naturalness_scorecards_boot.json", "file_level_cards", "naturalness_mean_line_surprisal"),
    ("change bursts (n_bursts)", "change_burst_scorecards_boot.json", "cards", "n_bursts"),
    ("review coverage (reviewed_fraction)", "review_coverage_scorecards_boot.json", "cards", "reviewed_fraction"),
    ("error-handling (eh_density)", "error_handling_scorecards_boot.json", "cards", "eh_density"),
]
SESOIS = [0.005, 0.01, 0.02]


def tost(boot: np.ndarray, delta: float):
    out = {"delta_point": round(delta, 5), "n_boot": len(boot),
           "ci90": [round(float(np.percentile(boot, 5)), 5),
                    round(float(np.percentile(boot, 95)), 5)],
           "ci95": [round(float(np.percentile(boot, 2.5)), 5),
                    round(float(np.percentile(boot, 97.5)), 5)]}
    for D in SESOIS:
        p_lo = float(np.mean(boot <= -D))     # H0: delta <= -D
        p_hi = float(np.mean(boot >= D))      # H0: delta >= +D
        p = max(p_lo, p_hi)
        within = out["ci90"][0] >= -D and out["ci90"][1] <= D
        out[f"tost@{D}"] = {"p": round(p, 4), "equivalent": bool(within and p < 0.05)}
    return out


def main() -> None:
    rows = []
    for name, fname, container, key in SIGNALS:
        fp = RES / fname
        if not fp.exists():
            print(f"  ({name}: {fname} missing — skipped)")
            continue
        d = json.loads(fp.read_text())
        cards = d.get(container) or d.get("cards") or d.get("file_level_cards") or {}
        card = cards.get(key)
        if card is None:
            print(f"  ({name}: card {key} not found in {fname})")
            continue
        boot = np.asarray(card["oof_auc_delta"].get("delta_boot", []), dtype=float)
        if boot.size == 0:
            print(f"  ({name}: no delta_boot vector)")
            continue
        r = tost(boot, card["oof_auc_delta"]["delta"])
        r["signal"] = name
        rows.append(r)

    print(f"\n{'signal':38s} {'delta':>8s} {'90% CI':>20s} "
          f"{'TOST@.01 p':>11s} {'equiv@.01':>10s}")
    for r in rows:
        lo, hi = r["ci90"]
        t = r["tost@0.01"]
        print(f"{r['signal']:38s} {r['delta_point']:+8.4f} "
              f"[{lo:+.4f}, {hi:+.4f}] {t['p']:11.4f} {str(t['equivalent']):>10s}")

    out = {"sesois": SESOIS, "method": "percentile-bootstrap TOST from persisted delta_boot",
           "signals": rows}
    op = RES / "bootstrap_tost.json"
    op.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {op}")


if __name__ == "__main__":
    main()
