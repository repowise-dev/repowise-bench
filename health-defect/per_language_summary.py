"""Per-language health-AUC summary over the calibration corpus.

Reads each repo's cached ``correlation.json`` (the bench's own per-repo metrics
with bootstrap CIs) and the config's name->language map, then prints a
per-repo and per-language breakdown for the chosen label strategy. Used to
report Phase-10 cross-language generalization (per-language AUC + CIs).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parent
RESULTS = BENCH.parents[0] / "results"
CONFIG = BENCH / "config.yaml"
LABEL = sys.argv[1] if len(sys.argv) > 1 else "keyword"

cfg = yaml.safe_load(CONFIG.read_text())
lang_of = {r["name"]: r["language"] for r in cfg["repos"]}

rows: list[tuple[str, str, float, float, float, int, int]] = []
for name, lang in lang_of.items():
    cp = RESULTS / f"health_defect_{name}" / "correlation.json"
    if not cp.exists():
        print(f"  (missing results: {name})")
        continue
    lc = json.loads(cp.read_text()).get("label_comparison", {})
    ent = lc.get(LABEL)
    if not ent or not isinstance(ent.get("auc"), dict):
        continue
    auc = ent["auc"]
    rows.append(
        (name, lang, auc["point"], auc["lo"], auc["hi"], ent["n_positives"], ent["n_files"])
    )

print(f"\n=== Per-repo health AUC ({LABEL} labels) ===")
print(f"{'repo':12} {'lang':10} {'AUC':>6}  {'95% CI':>16}  {'pos':>4} {'files':>5}")
for name, lang, pt, lo, hi, pos, nf in sorted(rows, key=lambda r: (r[1], r[0])):
    print(f"{name:12} {lang:10} {pt:6.3f}  [{lo:5.3f},{hi:5.3f}]  {pos:4} {nf:5}")

print(f"\n=== Per-language summary ({LABEL} labels) ===")
print(f"{'language':12} {'repos':>5} {'mean AUC':>9} {'pos':>5} {'files':>6}")
by_lang: dict[str, list[tuple]] = {}
for r in rows:
    by_lang.setdefault(r[1], []).append(r)
for lang in sorted(by_lang):
    rs = by_lang[lang]
    mean_auc = statistics.mean(r[2] for r in rs)
    pos = sum(r[5] for r in rs)
    nf = sum(r[6] for r in rs)
    print(f"{lang:12} {len(rs):5} {mean_auc:9.3f} {pos:5} {nf:6}")

allp = [r[2] for r in rows]
print(f"\nCorpus: {len(rows)} repos, mean per-repo AUC {statistics.mean(allp):.3f}, "
      f"total positives {sum(r[5] for r in rows)}, total files {sum(r[6] for r in rows)}")
