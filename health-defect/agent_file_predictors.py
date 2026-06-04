#!/usr/bin/env python3
"""agent_file_predictors.py — file-level history-signal stress-test.

The file-level companion to ``agent_predictor_eval.py``: do the history-based
*file* signals the shipped calibrated model leans on (prior-fix recurrence,
churn volume, ownership concentration, contributor count, size) still rank
future-defective files correctly in agent-era repos?

Leakage-free split design: per repo, features come from the T_MID snapshot
(``agent_predictor_eval.py build`` accrues every per-file stat over history
strictly before T_MID = 2026-01-01), labels are "file touched by a fix commit
at/after T_MID" (raw / spam-collapsed / fully-gated variants from the labels
records). Universe = code files present at the T_MID boundary tree with at
least one prior touch. AUC + recall@20%LOC (size-ranked effort) per repo,
pooled per cohort (cluster bootstrap over repos). ``agent_share`` — the
file's pre-T_MID share of agent-attributed commits — rides along as its own
candidate predictor.

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_file_predictors.py \
        --labels-dir <data>/agent-repos/_labels \
        --features-dir <data>/agent-repos/_predictors \
        --out-dir <data>/agent-repos/_predictors [--pool main|exhibit]
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

T_MID_TS = 1767225600  # 2026-01-01
VARIANTS = ("raw", "spam_collapsed", "fully_gated")
PREDICTORS = ("n_fix", "n_commits", "n_authors", "top_share", "size_bytes",
              "agent_share")
MIN_POS = 10

PASS_POOL = ["omi", "dyad", "prefect", "novu", "Umbraco-CMS", "mattermost",
             "grafana", "airbyte", "homebrew-core", "metabase", "strapi",
             "shiki", "nethermind", "dart"]
EXHIBIT_POOL = ["gh-aw", "worldmonitor", "windmill", "verifiers", "fern",
                "basic-memory", "Netcatty"]
COHORT = {  # corpus memo cohorts (homebrew kept separate: formula registry)
    "omi": "agent_heavy", "dyad": "agent_heavy",
    "prefect": "mixed", "novu": "mixed", "Umbraco-CMS": "mixed",
    "mattermost": "mixed", "grafana": "mixed", "airbyte": "mixed",
    "metabase": "mixed", "nethermind": "mixed", "dart": "mixed",
    "homebrew-core": "registry",
    "strapi": "control", "shiki": "control",
    "gh-aw": "exhibit", "worldmonitor": "exhibit", "windmill": "exhibit",
    "verifiers": "exhibit", "fern": "exhibit", "basic-memory": "exhibit",
    "Netcatty": "exhibit",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def _auc(y, s) -> float | None:
    pos = sum(y)
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(len(s)), key=lambda i: s[i])
    ranks = [0.0] * len(s)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    rank_pos = sum(r for r, yy in zip(ranks, y) if yy)
    return (rank_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def recall_at_loc(files: list[dict], score: list[float], ycol: str,
                  frac: float = 0.20) -> float | None:
    total = sum(max(f["size_bytes"], 1) for f in files)
    pos = sum(f[ycol] for f in files)
    if pos == 0:
        return None
    order = sorted(range(len(files)), key=lambda i: -score[i])
    spent, found = 0.0, 0
    for i in order:
        c = max(files[i]["size_bytes"], 1)
        if spent + c > total * frac and spent > 0:
            break
        spent += c
        found += files[i][ycol]
    return found / pos


def load_repo(name: str, labels_dir: Path, features_dir: Path) -> list[dict] | None:
    lp, fp = labels_dir / f"{name}.json", features_dir / f"{name}.json"
    if not lp.exists() or not fp.exists():
        return None
    labels = json.loads(lp.read_text(encoding="utf-8"))
    feats = json.loads(fp.read_text(encoding="utf-8"))
    files_mid, sizes = feats["files_mid"], feats["sizes_mid"]
    if not sizes:
        return None

    clean = lambda c: not c["self_fix"] and not c["is_revert"] and not c["was_reverted"]  # noqa: E731
    touched: dict[str, set[str]] = {v: set() for v in VARIANTS}
    for c in labels["commits"]:
        if c["ts"] < T_MID_TS:
            continue
        if c["is_fix"]:
            touched["raw"].update(c["files"])
            if clean(c):
                touched["spam_collapsed"].update(c["files"])
        if c["issue_gated"] and clean(c):
            touched["fully_gated"].update(c["files"])

    rows = []
    for path, size in sizes.items():
        fm = files_mid.get(path)
        if not fm:  # never touched before T_MID — no history features
            continue
        rows.append({
            "repo": name, "path": path, "size_bytes": size,
            "n_fix": fm["n_fix"], "n_commits": fm["n_commits"],
            "n_authors": fm["n_authors"], "top_share": fm["top_share"] or 0.0,
            "agent_share": fm["agent_share"] or 0.0,
            **{f"y_{v}": int(path in touched[v]) for v in VARIANTS},
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels-dir", type=Path, required=True)
    ap.add_argument("--features-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--pool", choices=("main", "exhibit"), default="main")
    ap.add_argument("--seed", type=int, default=20260604)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    names = EXHIBIT_POOL if args.pool == "exhibit" else PASS_POOL

    per_repo: dict[str, dict] = {}
    for n in names:
        rows = load_repo(n, args.labels_dir, args.features_dir)
        if rows is None:
            log(f"{n}: missing inputs, skipped")
            continue
        rec: dict = {"cohort": COHORT.get(n, "?"), "n_files": len(rows)}
        for v in VARIANTS:
            ycol = f"y_{v}"
            y = [r[ycol] for r in rows]
            if sum(y) < MIN_POS:
                continue
            cell = {"n_pos": sum(y), "pos_rate": round(sum(y) / len(y), 4)}
            for p in PREDICTORS:
                a = _auc(y, [float(r[p]) for r in rows])
                if a is not None:
                    cell[f"auc_{p}"] = round(a, 4)
            cell["recall20_n_fix"] = recall_at_loc(rows, [float(r["n_fix"]) for r in rows], ycol)
            cell["recall20_size"] = recall_at_loc(rows, [float(r["size_bytes"]) for r in rows], ycol)
            rng_np = np.random.default_rng(args.seed)
            cell["recall20_random"] = recall_at_loc(rows, list(rng_np.random(len(rows))), ycol)
            rec[v] = cell
        per_repo[n] = rec
        log(f"{n}: {len(rows)} files, raw pos {rec.get('raw', {}).get('pos_rate')}")

    # pooled per cohort × variant × predictor
    pooled: dict = {}
    for v in VARIANTS:
        pooled[v] = {}
        by_cohort: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for n, rec in per_repo.items():
            cell = rec.get(v)
            if not cell:
                continue
            for p in PREDICTORS:
                a = cell.get(f"auc_{p}")
                if a is not None:
                    by_cohort[rec["cohort"]][p].append(a)
                    by_cohort["ALL"][p].append(a)
        for cohort, preds in by_cohort.items():
            pooled[v][cohort] = {}
            for p, vals in preds.items():
                if len(vals) >= 3:
                    boots = sorted(
                        float(np.mean([rng.choice(vals) for _ in vals]))
                        for _ in range(2000))
                    ci = [round(boots[int(0.025 * len(boots))], 4),
                          round(boots[int(0.975 * len(boots))], 4)]
                else:
                    ci = None
                pooled[v][cohort][p] = {
                    "mean_auc": round(float(np.mean(vals)), 4),
                    "ci95": ci, "n_repos": len(vals)}

    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "pool": args.pool, "t_mid": "2026-01-01", "min_pos": MIN_POS,
           "per_repo": per_repo, "pooled": pooled}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.pool == "main" else f"_{args.pool}"
    (args.out_dir / f"file_predictors{suffix}.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")

    lines = [f"# File-level history signals — {args.pool} pool",
             f"\nGenerated {out['generated']} · features from history < 2026-01-01 "
             "(T_MID snapshot) · label = file touched by fix ≥ T_MID · "
             "AUC orientation not flipped · value [CI] (n repos).\n"]
    for v in VARIANTS:
        lines.append(f"\n## {v}\n")
        lines.append("| cohort | " + " | ".join(PREDICTORS) + " |")
        lines.append("|---|" + "---|" * len(PREDICTORS))
        for cohort in ("agent_heavy", "mixed", "control", "registry", "exhibit", "ALL"):
            preds = pooled[v].get(cohort)
            if not preds:
                continue
            cells = []
            for p in PREDICTORS:
                d = preds.get(p)
                if not d:
                    cells.append("—")
                    continue
                s = f"{d['mean_auc']:.3f}"
                if d["ci95"]:
                    s += f" [{d['ci95'][0]:.3f}, {d['ci95'][1]:.3f}]"
                cells.append(s + f" ({d['n_repos']})")
            lines.append(f"| {cohort} | " + " | ".join(cells) + " |")
        lines.append("\n| repo | cohort | pos rate | prior-fix AUC | recall@20%LOC fix/size/rand |")
        lines.append("|---|---|--:|--:|---|")
        for n, rec in sorted(per_repo.items()):
            cell = rec.get(v)
            if not cell:
                continue
            r20 = "/".join(
                "—" if cell.get(k) is None else f"{cell[k]:.2f}"
                for k in ("recall20_n_fix", "recall20_size", "recall20_random"))
            lines.append(f"| {n} | {rec['cohort']} | {cell['pos_rate']:.3f} | "
                         f"{cell.get('auc_n_fix', float('nan')):.3f} | {r20} |")
    md = args.out_dir / f"FILE_PREDICTORS{suffix.upper()}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote {md}")


if __name__ == "__main__":
    main()
