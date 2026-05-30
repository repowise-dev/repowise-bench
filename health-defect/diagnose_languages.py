"""Why does the health score do worse on some languages? Per-language autopsy.

Reads cached joined_data.json for every config repo and decomposes the
per-language AUC by the three known failure levers (Phase 9): class imbalance
(degenerate positive rate), the size confound (small-file regime), and signal
starvation (how often biomarkers fire at all). No re-index.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import yaml
from sklearn.metrics import roc_auc_score

BENCH = Path(__file__).resolve().parent
RESULTS = BENCH.parents[0] / "results"
cfg = yaml.safe_load((BENCH / "config.yaml").read_text())
lang_of = {r["name"]: r["language"] for r in cfg["repos"]}


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def load_rows(name: str) -> list | None:
    """joined_data.json + keyword labels joined on (normalized) path."""
    d = RESULTS / f"health_defect_{name}"
    jp, kp = d / "joined_data.json", d / "defect_counts_keyword.json"
    if not jp.exists() or not kp.exists():
        return None
    counts = {_norm(k): v for k, v in json.loads(kp.read_text()).items()}
    rows = json.loads(jp.read_text())
    for r in rows:
        r["defect_count"] = counts.get(_norm(r["file_path"]), 0)
    return rows


def auc(rows):
    y = [1 if r["defect_count"] else 0 for r in rows]
    s = [r["health_score"] for r in rows]
    if len(set(y)) < 2:
        return float("nan")
    # health: lower score = riskier, so negate for "higher = more defect-prone".
    return roc_auc_score(y, [-v for v in s])


by_lang: dict[str, list] = {}
per_repo = []
for name, lang in lang_of.items():
    rows = load_rows(name)
    if rows is None:
        continue
    by_lang.setdefault(lang, []).extend(rows)
    pos = sum(1 for r in rows if r["defect_count"])
    zero_find = sum(1 for r in rows if r.get("finding_count", 0) == 0)
    per_repo.append((
        name, lang, len(rows), pos / len(rows),
        statistics.median(r["nloc"] for r in rows),
        statistics.mean(r.get("finding_count", 0) for r in rows),
        zero_find / len(rows), auc(rows),
    ))

print(f"{'repo':12}{'lang':11}{'n':>5}{'pos%':>6}{'medNLOC':>8}"
      f"{'mFind':>7}{'%0find':>7}{'AUC':>6}")
for name, lang, n, pr, mn, mf, zf, a in sorted(per_repo, key=lambda r: (r[1], r[0])):
    print(f"{name:12}{lang:11}{n:5}{pr*100:6.0f}{mn:8.0f}{mf:7.1f}{zf*100:7.0f}{a:6.3f}")

print(f"\n{'language':11}{'files':>6}{'pos%':>6}{'medNLOC':>8}{'mFind':>7}"
      f"{'%0find':>7}{'pooledAUC':>10}{'small-AUC':>10}{'large-AUC':>10}")
CORPUS_MED = statistics.median(r["nloc"] for rs in by_lang.values() for r in rs)
for lang in sorted(by_lang):
    rows = by_lang[lang]
    pos = sum(1 for r in rows if r["defect_count"])
    zf = sum(1 for r in rows if r.get("finding_count", 0) == 0) / len(rows)
    small = [r for r in rows if r["nloc"] <= CORPUS_MED]
    large = [r for r in rows if r["nloc"] > CORPUS_MED]
    print(f"{lang:11}{len(rows):6}{pos/len(rows)*100:6.0f}"
          f"{statistics.median(r['nloc'] for r in rows):8.0f}"
          f"{statistics.mean(r.get('finding_count',0) for r in rows):7.1f}"
          f"{zf*100:7.0f}{auc(rows):10.3f}{auc(small):10.3f}{auc(large):10.3f}")
print(f"\ncorpus median NLOC = {CORPUS_MED:.0f} (small = <=median, large = >median)")
