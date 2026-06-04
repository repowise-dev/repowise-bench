#!/usr/bin/env python3
"""agent_predictor_eval.py — history-based predictor stress-test on the
agent-era corpus.

The confirmatory question: do the history-based defect predictors that the
calibrated health/risk models rest on — prior-fix recurrence, ownership,
author experience, change entropy, churn, size — still discriminate
bug-inducing commits when the author is an agent?

Two stages:

``build``  — per repo, one full-history tree-level walk (offline on the
  blobless clones) accrues, per window commit, the history features at the
  moment of the commit: author experience under BOTH identity mappings
  (raw e-mail vs agent-identity-collapsed), mean ownership of the touched
  files, prior fix-touch recurrence of the touched files; plus one window
  numstat walk (needs blobs — ``git backfill`` first) for the Kamei
  size/diffusion/entropy features. Also snapshots per-file history stats at
  ``T_MID`` for the file-level eval (``agent_file_predictors.py``).
  Cached to ``<out-dir>/<name>.json``.

``eval``   — joins the build features with the per-commit labels
  (``agent_defect_labels.py``) and the AG-SZZ inducing sets
  (``agent_szz_induction.py``) and computes, per repo × authorship group
  (human / t1 / t2 / t3) × fix-set variant (raw / spam_collapsed /
  fully_gated):

  * univariate AUC per predictor (orientation NOT flipped — a protective
    predictor reads as AUC < 0.5; a "flip" is the human cell and the agent
    cell on opposite sides of 0.5);
  * effort-aware recall@20%-churn for prior-fix, churn and the model;
  * a leave-one-repo-out JIT model (Kamei set, trained on HUMAN commits of
    the other repos) scored on every cell of the held-out repo — the
    transfer test;
  * the agent-flag increment: LORO model with tier dummies vs without;
  * pooled per-group multivariate coefficient of author experience (the
    "does protective experience flip for agents?" cell).

  Pooling is bench hygiene: per-repo values first, cluster bootstrap over
  repos, paired within-repo deltas vs the human cell. MIN_N/MIN_POS floors
  per cell; absent ≠ zero (ownership undefined on never-seen files drops
  the row from that predictor's universe only).

Run (venv python)::

    .venv/Scripts/python.exe health-defect/agent_predictor_eval.py build \
        --repos-dir <data>/agent-repos --labels-dir <data>/agent-repos/_labels \
        --provenance-dir <data>/agent-repos/_provenance \
        --out-dir <data>/agent-repos/_predictors [--only a,b] [--workers 4]

    .venv/Scripts/python.exe health-defect/agent_predictor_eval.py eval \
        --repos-dir <data>/agent-repos --labels-dir <data>/agent-repos/_labels \
        --szz-dir <data>/agent-repos/_szz --features-dir <data>/agent-repos/_predictors \
        --out-dir <data>/agent-repos/_predictors [--pool main|exhibit] [--szz-kind ag|b]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
import warnings
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.defect_counter import _DEFAULT_EXCLUDE, _DEFAULT_INCLUDE  # noqa: E402

WINDOW_START = "2025-06-01"
WINDOW_START_TS = 1748736000  # 2025-06-01T00:00:00Z
T_MID = "2026-01-01"          # file-level feature/label split point
T_MID_TS = 1767225600
ELIG_DAYS = 90
MIN_N = 30
MIN_POS = 5
VARIANTS = ("raw", "spam_collapsed", "fully_gated")
PREDICTORS = ("churn", "nf", "entropy", "exp_email", "exp_ident",
              "ownership", "prior_fix")
KAMEI_COLS = ("log_la", "log_ld", "log_nf", "nd", "ns", "entropy", "log_exp")

_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
              ".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".rb",
              ".php", ".scala", ".swift", ".m", ".mm", ".clj", ".cljc", ".cljs",
              ".dart", ".svelte", ".vue", ".ex", ".exs"}

PASS_POOL = ["omi", "dyad", "prefect", "novu", "Umbraco-CMS", "mattermost",
             "grafana", "airbyte", "homebrew-core", "metabase", "strapi",
             "shiki", "nethermind", "dart"]
EXHIBIT_POOL = ["gh-aw", "worldmonitor", "windmill", "verifiers", "fern",
                "basic-memory", "Netcatty"]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return "." + base.rsplit(".", 1)[1].lower() if "." in base else ""


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}... failed: {r.stderr[-200:]}")
    return r.stdout


# ============================================================== build stage ==


def _numstat_walk(repo: Path) -> dict[str, dict]:
    """sha -> Kamei size/diffusion features over code files (window, blobs)."""
    out = _git(["log", f"--since={WINDOW_START}", "--no-merges", "--numstat",
                "--format=%x01%H"], repo)
    feats: dict[str, dict] = {}
    cur_sha = None
    per_file: list[int] = []
    dirs: set[str] = set()
    subs: set[str] = set()
    la = ld = nf = 0

    def flush() -> None:
        nonlocal la, ld, nf
        if cur_sha is None:
            return
        total = sum(per_file) or 1
        entropy = -sum((p / total) * math.log2(p / total)
                       for p in per_file if p) if per_file else 0.0
        feats[cur_sha] = {"la": la, "ld": ld, "nf": nf, "nd": len(dirs),
                          "ns": len(subs), "entropy": round(entropy, 4)}

    for line in out.split("\n"):
        if line.startswith("\x01"):
            flush()
            cur_sha = line[1:].strip()
            per_file, dirs, subs = [], set(), set()
            la = ld = nf = 0
        elif cur_sha is not None and "\t" in line:
            a, d, f = line.split("\t", 2)
            if _ext(f) not in _CODE_EXTS or a == "-":
                continue
            a, d = int(a), int(d)
            la += a
            ld += d
            nf += 1
            if a + d:
                per_file.append(a + d)
            segs = f.split("/")
            dirs.add("/".join(segs[:-1]))
            subs.add(segs[0])
    flush()
    return feats


def build_repo(name: str, repos_dir: Path, labels_dir: Path, prov_dir: Path,
               out_dir: Path) -> dict:
    repo = repos_dir / name
    labels = json.loads((labels_dir / f"{name}.json").read_text(encoding="utf-8"))
    window_shas = {c["sha"] for c in labels["commits"]}

    # full-history identity mapping (agent name collapses instances)
    agent_of: dict[str, str] = {}
    prov_path = prov_dir / f"{name}.json"
    if prov_path.exists():
        for r in json.loads(prov_path.read_text(encoding="utf-8"))["rows"]:
            if r["agent"]:
                agent_of[r["sha"]] = r["agent"]

    t0 = time.time()
    # one full-history, oldest-first, tree-level walk (offline on blobless)
    out = _git(["log", "--no-merges", "--reverse", "--date=unix", "--name-only",
                "--format=%x01%H%x00%ae%x00%ad%x00%s"], repo)

    inc, exc = _DEFAULT_INCLUDE, _DEFAULT_EXCLUDE
    n_by_email: Counter = Counter()
    n_by_ident: Counter = Counter()
    f_touch: Counter = Counter()                 # file -> total prior touches
    f_touch_by: dict[str, Counter] = defaultdict(Counter)  # file -> ident -> n
    f_fix: Counter = Counter()                   # file -> prior fix touches
    f_first_ts: dict[str, int] = {}
    f_agent: Counter = Counter()                 # file -> prior agent touches
    files_mid: dict[str, dict] = {}
    mid_snapped = False

    commits_out: dict[str, dict] = {}
    cur: dict | None = None
    cur_files: list[str] = []

    def snapshot_mid() -> None:
        nonlocal mid_snapped
        for f, n in f_touch.items():
            by = f_touch_by[f]
            top = by.most_common(1)[0][1] if by else 0
            files_mid[f] = {
                "n_commits": n, "n_fix": f_fix.get(f, 0),
                "n_authors": len(by),
                "top_share": round(top / n, 4) if n else None,
                "first_ts": f_first_ts.get(f),
                "agent_share": round(f_agent.get(f, 0) / n, 4) if n else None,
            }
        mid_snapped = True

    def process(c: dict, files: list[str]) -> None:
        nonlocal mid_snapped
        if not mid_snapped and c["ts"] >= T_MID_TS:
            snapshot_mid()
        ident = agent_of.get(c["sha"]) or c["email"]
        is_fix = (not any(p.search(c["subject"]) for p in exc)) and \
            any(p.search(c["subject"]) for p in inc)
        if c["sha"] in window_shas:
            own_shares, pf_sum, pf_any = [], 0, 0
            for f in files:
                prior = f_touch.get(f, 0)
                if prior:
                    own_shares.append(f_touch_by[f].get(ident, 0) / prior)
                    pf = f_fix.get(f, 0)
                    pf_sum += pf
                    pf_any |= int(pf > 0)
            commits_out[c["sha"]] = {
                "exp_email": n_by_email[c["email"]],
                "exp_ident": n_by_ident[ident],
                "ownership": round(float(np.mean(own_shares)), 4) if own_shares else None,
                "own_n": len(own_shares),
                "prior_fix_sum": pf_sum, "prior_fix_any": pf_any,
            }
        n_by_email[c["email"]] += 1
        n_by_ident[ident] += 1
        is_agent = c["sha"] in agent_of
        for f in files:
            f_touch[f] += 1
            f_touch_by[f][ident] += 1
            if is_fix:
                f_fix[f] += 1
            if is_agent:
                f_agent[f] += 1
            f_first_ts.setdefault(f, c["ts"])

    for line in out.split("\n"):
        if line.startswith("\x01"):
            if cur is not None:
                process(cur, cur_files)
            sha, email, ts, subject = line[1:].split("\x00", 3)
            cur = {"sha": sha, "email": email.lower(), "ts": int(ts),
                   "subject": subject}
            cur_files = []
        elif line.strip() and cur is not None:
            f = line.strip()
            if _ext(f) in _CODE_EXTS:
                cur_files.append(f)
    if cur is not None:
        process(cur, cur_files)
    if not mid_snapped:
        snapshot_mid()
    hist_s = round(time.time() - t0, 1)

    t1 = time.time()
    numstat = _numstat_walk(repo)
    for sha, rec in commits_out.items():
        rec.update(numstat.get(sha) or
                   {"la": None, "ld": None, "nf": None, "nd": None,
                    "ns": None, "entropy": None})
    num_s = round(time.time() - t1, 1)

    # file sizes at the T_MID boundary (effort denominator for the file eval)
    sizes: dict[str, int] = {}
    boundary = None
    try:
        boundary = _git(["rev-list", "-1", f"--before={T_MID}",
                         f"--since={'2000-01-01'}", "HEAD"], repo).strip()
        if boundary:
            for row in _git(["ls-tree", "-r", "-l", boundary], repo).split("\n"):
                parts = row.split(None, 4)
                if len(parts) == 5 and parts[1] == "blob" and parts[3].isdigit():
                    if _ext(parts[4]) in _CODE_EXTS:
                        sizes[parts[4]] = int(parts[3])
    except RuntimeError:
        sizes = {}

    result = {"repo": labels["summary"]["repo"], "dir": name,
              "window_start": WINDOW_START, "t_mid": T_MID,
              "boundary_sha": boundary,
              "stats": {"history_seconds": hist_s, "numstat_seconds": num_s,
                        "n_window": len(commits_out),
                        "n_files_mid": len(files_mid), "n_sizes": len(sizes)},
              "commits": commits_out, "files_mid": files_mid,
              "sizes_mid": sizes}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(result), encoding="utf-8")
    return result["stats"]


# =============================================================== eval stage ==


def fix_variant_shas(commits: list[dict]) -> dict[str, set[str]]:
    clean = lambda c: not c["self_fix"] and not c["is_revert"] and not c["was_reverted"]  # noqa: E731
    return {
        "raw": {c["sha"] for c in commits if c["is_fix"]},
        "spam_collapsed": {c["sha"] for c in commits if c["is_fix"] and clean(c)},
        "fully_gated": {c["sha"] for c in commits if c["issue_gated"] and clean(c)},
    }


def load_rows(name: str, labels_dir: Path, szz_dir: Path, features_dir: Path,
              szz_kind: str) -> list[dict]:
    labels = json.loads((labels_dir / f"{name}.json").read_text(encoding="utf-8"))
    szz = json.loads((szz_dir / f"{name}.json").read_text(encoding="utf-8"))
    feats = json.loads((features_dir / f"{name}.json").read_text(encoding="utf-8"))["commits"]
    head_ts = max(c["ts"] for c in labels["commits"])
    variants = fix_variant_shas(labels["commits"])
    induced: dict[str, set[str]] = {v: set() for v in VARIANTS}
    for fix_sha, sets in szz["inducing"].items():
        blamed = set(sets[szz_kind])
        for v in VARIANTS:
            if fix_sha in variants[v]:
                induced[v] |= blamed
    rows = []
    for c in labels["commits"]:
        if c["n_files"] == 0 or head_ts - c["ts"] < ELIG_DAYS * 86400:
            continue
        f = feats.get(c["sha"])
        if not f or f["la"] is None:
            continue
        churn = f["la"] + f["ld"]
        rows.append({
            "repo": labels["summary"]["repo"], "dir": name, "sha": c["sha"],
            "group": f"t{c['tier']}" if c["agent"] else "human",
            "agent": c["agent"],
            "churn": churn, "nf": f["nf"], "entropy": f["entropy"],
            "exp_email": f["exp_email"], "exp_ident": f["exp_ident"],
            "ownership": f["ownership"], "prior_fix": f["prior_fix_sum"],
            "log_la": math.log1p(f["la"]), "log_ld": math.log1p(f["ld"]),
            "log_nf": math.log1p(f["nf"]), "nd": f["nd"], "ns": f["ns"],
            "log_exp": math.log1p(f["exp_email"]),
            "log_exp_ident": math.log1p(f["exp_ident"]),
            "log_pf": math.log1p(f["prior_fix_sum"]),
            **{f"y_{v}": int(c["sha"] in induced[v]) for v in VARIANTS},
        })
    return rows


def _auc(y: list[int], s: list[float]) -> float | None:
    """Tie-aware rank AUC."""
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


def effort_recall(rows: list[dict], score: list[float], ycol: str,
                  frac: float = 0.20) -> float | None:
    total_churn = sum(max(r["churn"], 1) for r in rows)
    total_pos = sum(r[ycol] for r in rows)
    if total_pos == 0:
        return None
    order = sorted(range(len(rows)), key=lambda i: -score[i])
    spent, found = 0.0, 0
    budget = total_churn * frac
    for i in order:
        c = max(rows[i]["churn"], 1)
        if spent + c > budget and spent > 0:
            break
        spent += c
        found += rows[i][ycol]
    return found / total_pos


def _ci(samples: list[float]) -> list[float] | None:
    if len(samples) < 100:
        return None
    s = sorted(samples)
    return [round(s[int(0.025 * len(s))], 4), round(s[int(0.975 * len(s))], 4)]


def cell_auc_table(rows: list[dict], variant: str) -> dict:
    """{predictor: {group: {repo: auc}}} subject to MIN_N / MIN_POS."""
    ycol = f"y_{variant}"
    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_cell[(r["dir"], r["group"])].append(r)
    out: dict[str, dict[str, dict[str, float]]] = {p: defaultdict(dict) for p in PREDICTORS}
    for (repo, group), rs in by_cell.items():
        if len(rs) < MIN_N or sum(r[ycol] for r in rs) < MIN_POS:
            continue
        for p in PREDICTORS:
            universe = [r for r in rs if r[p] is not None]
            if len(universe) < MIN_N or sum(r[ycol] for r in universe) < MIN_POS:
                continue
            a = _auc([r[ycol] for r in universe], [float(r[p]) for r in universe])
            if a is not None:
                out[p][group][repo] = round(a, 4)
    return out


def pool_cells(table: dict, rng: random.Random, n_boot: int = 2000) -> dict:
    """Pooled mean AUC per predictor×group + Δ vs human (paired by repo)."""
    pooled: dict[str, dict] = {}
    for p, groups in table.items():
        pooled[p] = {}
        for g, per_repo in groups.items():
            vals = list(per_repo.values())
            if len(vals) < 3:
                pooled[p][g] = {"mean_auc": round(float(np.mean(vals)), 4) if vals else None,
                                "ci95": None, "n_repos": len(vals)}
                continue
            boots = [float(np.mean([rng.choice(vals) for _ in vals]))
                     for _ in range(n_boot)]
            pooled[p][g] = {"mean_auc": round(float(np.mean(vals)), 4),
                            "ci95": _ci(boots), "n_repos": len(vals)}
        # paired deltas vs human
        human = groups.get("human", {})
        for g in ("t1", "t2", "t3"):
            per_repo = groups.get(g, {})
            deltas = [per_repo[r] - human[r] for r in per_repo if r in human]
            if len(deltas) < 3:
                if deltas:
                    pooled[p][f"delta_{g}"] = {"mean_delta": round(float(np.mean(deltas)), 4),
                                               "ci95": None, "n_repos": len(deltas)}
                continue
            boots = [float(np.mean([rng.choice(deltas) for _ in deltas]))
                     for _ in range(n_boot)]
            pooled[p][f"delta_{g}"] = {"mean_delta": round(float(np.mean(deltas)), 4),
                                       "ci95": _ci(boots), "n_repos": len(deltas)}
    return pooled


def loro_model(rows: list[dict], variant: str, *, with_prior_fix: bool,
               with_tiers: bool = False) -> dict:
    """Leave-one-repo-out logistic trained on HUMAN commits of the train
    repos; scored on every commit of the held-out repo. Returns per-cell AUC
    + recall@20%churn (+ pooled coefficient table from the all-repo fit)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    ycol = f"y_{variant}"
    cols = list(KAMEI_COLS) + (["log_pf"] if with_prior_fix else [])
    tier_names = ("t1", "t2", "t3") if with_tiers else ()

    def design(rs: list[dict]) -> np.ndarray:
        X = [[r[c] for c in cols] + [float(r["group"] == t) for t in tier_names]
             for r in rs]
        return np.array(X, dtype=float)

    repos = sorted({r["dir"] for r in rows})
    cell_auc: dict[str, dict[str, float]] = defaultdict(dict)
    cell_recall: dict[str, dict[str, float]] = defaultdict(dict)
    for held in repos:
        train = [r for r in rows if r["dir"] != held and
                 (with_tiers or r["group"] == "human")]
        test = [r for r in rows if r["dir"] == held]
        ytr = np.array([r[ycol] for r in train])
        if len(train) < 200 or len(set(ytr)) < 2:
            continue
        sc = StandardScaler().fit(design(train))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            clf = LogisticRegression(C=1.0, class_weight="balanced",
                                     max_iter=5000).fit(sc.transform(design(train)), ytr)
        by_group: dict[str, list[dict]] = defaultdict(list)
        for r in test:
            by_group[r["group"]].append(r)
        by_group["all"] = test
        for g, rs in by_group.items():
            if len(rs) < MIN_N or sum(r[ycol] for r in rs) < MIN_POS:
                continue
            p = clf.predict_proba(sc.transform(design(rs)))[:, 1]
            a = _auc([r[ycol] for r in rs], list(p))
            if a is not None:
                cell_auc[g][held] = round(a, 4)
                er = effort_recall(rs, list(p), ycol)
                if er is not None:
                    cell_recall[g][held] = round(er, 4)

    # pooled coefficients (all repos, for the sign story)
    train = [r for r in rows if with_tiers or r["group"] == "human"]
    ytr = np.array([r[ycol] for r in train])
    coefs = {}
    if len(set(ytr)) == 2:
        sc = StandardScaler().fit(design(train))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            clf = LogisticRegression(C=1.0, class_weight="balanced",
                                     max_iter=5000).fit(sc.transform(design(train)), ytr)
        names = cols + list(tier_names)
        coefs = {n: round(float(w), 4) for n, w in zip(names, clf.coef_[0])}
    return {"cell_auc": dict(cell_auc), "cell_recall": dict(cell_recall),
            "coefs": coefs, "features": cols + list(tier_names)}


def exp_coef_by_group(rows: list[dict], variant: str,
                      exp_col: str = "log_exp") -> dict:
    """Per-group pooled logit (Kamei controls + repo FE): the experience
    coefficient with a cluster-bootstrap CI — the flip/die test."""
    import pandas as pd
    import statsmodels.api as sm

    ycol = f"y_{variant}"
    out = {}
    for g in ("human", "t1", "t2", "t3"):
        sub = [r for r in rows if r["group"] == g]
        df = pd.DataFrame(sub)
        if df.empty or df[ycol].sum() < 20 or df["dir"].nunique() < 3:
            continue
        ctrl = ["log_la", "log_ld", "log_nf", "entropy"]

        def fit(d: "pd.DataFrame") -> float | None:
            X = d[ctrl].copy()
            X.insert(0, "const", 1.0)
            X["exp"] = (d[exp_col] - d[exp_col].mean()) / (d[exp_col].std() or 1.0)
            for repo in sorted(d["dir"].unique())[1:]:
                X[f"fe_{repo}"] = (d["dir"] == repo).astype(float)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    res = sm.Logit(d[ycol], X).fit(disp=0, maxiter=200)
                return float(res.params["exp"])
            except Exception:  # noqa: BLE001
                return None

        point = fit(df)
        if point is None:
            continue
        repos = sorted(df["dir"].unique())
        rng = np.random.default_rng(20260604)
        boots = []
        for _ in range(300):
            sample = list(rng.choice(repos, size=len(repos), replace=True))
            bd = pd.concat([df[df["dir"] == r] for r in sample], ignore_index=True)
            c = fit(bd)
            if c is not None:
                boots.append(c)
        out[g] = {"coef": round(point, 4), "ci95": _ci(boots),
                  "n": int(len(df)), "n_pos": int(df[ycol].sum()),
                  "n_repos": len(repos)}
    return out


def recall_cells(rows: list[dict], variant: str) -> dict:
    """recall@20%churn for the single-feature rankers, per repo×group."""
    ycol = f"y_{variant}"
    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_cell[(r["dir"], r["group"])].append(r)
    out: dict[str, dict[str, dict[str, float]]] = {
        "prior_fix": defaultdict(dict), "churn": defaultdict(dict),
        "random": defaultdict(dict)}
    rng = np.random.default_rng(20260604)
    for (repo, group), rs in by_cell.items():
        if len(rs) < MIN_N or sum(r[ycol] for r in rs) < MIN_POS:
            continue
        for name, score in (("prior_fix", [float(r["prior_fix"]) for r in rs]),
                            ("churn", [float(r["churn"]) for r in rs]),
                            ("random", list(rng.random(len(rs))))):
            er = effort_recall(rs, score, ycol)
            if er is not None:
                out[name][group][repo] = round(er, 4)
    return {k: dict(v) for k, v in out.items()}


# ------------------------------------------------------------- md rendering --


def _fmt_cell(d: dict | None, key: str) -> str:
    if not d or d.get(key) is None:
        return "—"
    ci = d.get("ci95")
    star = ""
    if ci and key == "mean_delta" and (ci[0] > 0 or ci[1] < 0):
        star = " **\\***"
    if ci and key == "mean_auc" and (ci[0] > 0.5 or ci[1] < 0.5):
        star = " †"
    s = f"{d[key]:+.3f}" if key == "mean_delta" else f"{d[key]:.3f}"
    if ci:
        s += f" [{ci[0]:.3f}, {ci[1]:.3f}]"
    return s + f" ({d['n_repos']})" + star


def render_md(result: dict, pool_name: str, szz_kind: str) -> str:
    lines = [f"# Predictor stress-test — {pool_name} pool "
             f"({szz_kind.upper()}-SZZ outcomes)",
             f"\nGenerated {result['generated']} · window {WINDOW_START} → HEAD · "
             f"eligibility ≥{ELIG_DAYS} d before HEAD · MIN_N={MIN_N}, "
             f"MIN_POS={MIN_POS} per cell · AUC orientation NOT flipped "
             "(protective < 0.5) · cluster-bootstrap 95% CI over repos · "
             "value (n repos) · † AUC CI excludes 0.5 · \\* Δ CI excludes 0.\n"]
    for v in VARIANTS:
        pooled = result["variants"][v]["pooled_auc"]
        lines.append(f"\n## {v} — pooled per-predictor AUC by authorship group\n")
        lines.append("| predictor | human | t1 | t2 | t3 | Δt1 | Δt2 | Δt3 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for p in PREDICTORS:
            row = pooled.get(p, {})
            lines.append(
                f"| {p} | " + " | ".join(
                    [_fmt_cell(row.get(g), "mean_auc") for g in ("human", "t1", "t2", "t3")] +
                    [_fmt_cell(row.get(f"delta_{g}"), "mean_delta") for g in ("t1", "t2", "t3")]
                ) + " |")
        model = result["variants"][v]
        for tag in ("model_kamei", "model_kamei_pf"):
            m = model.get(tag)
            if not m:
                continue
            lines.append(f"\n### {v} · LORO model `{tag}` (trained on human commits)\n")
            lines.append("| cell | mean AUC | recall@20%churn |")
            lines.append("|---|---|---|")
            for g in ("human", "t1", "t2", "t3", "all"):
                aucs = list((m["cell_auc"].get(g) or {}).values())
                recs = list((m["cell_recall"].get(g) or {}).values())
                if not aucs:
                    continue
                lines.append(f"| {g} | {np.mean(aucs):.3f} ({len(aucs)}) | "
                             + (f"{np.mean(recs):.3f} ({len(recs)})" if recs else "—") + " |")
            lines.append(f"\ncoefs: `{m['coefs']}`")
        ec = model.get("exp_coef") or {}
        if ec:
            lines.append(f"\n### {v} · author-experience coefficient by group "
                         "(logit + size/entropy controls + repo FE)\n")
            lines.append("| group | std coef | 95% CI | n (pos) | repos |")
            lines.append("|---|--:|---|---|--:|")
            for g, d in ec.items():
                ci = d["ci95"]
                star = " **\\***" if ci and (ci[0] > 0 or ci[1] < 0) else ""
                lines.append(f"| {g} | {d['coef']:+.3f}{star} | "
                             f"{ci if ci else '—'} | {d['n']} ({d['n_pos']}) | "
                             f"{d['n_repos']} |")
        af = model.get("agent_flag")
        if af:
            lines.append(f"\n### {v} · agent-flag increment (LORO, all-commit cells)\n")
            lines.append(f"tier coefs in pooled fit: "
                         f"{ {k: v for k, v in af['coefs'].items() if k.startswith('t')} }")
            deltas = af.get("auc_delta_per_repo") or {}
            if deltas:
                vals = list(deltas.values())
                lines.append(f"\nAUC Δ (with-tiers − without), per held-out repo: "
                             f"mean {np.mean(vals):+.4f} "
                             f"(min {min(vals):+.4f}, max {max(vals):+.4f}, "
                             f"n={len(vals)})")
        rec = model.get("recall_cells")
        if rec:
            lines.append(f"\n### {v} · recall@20%churn, single-feature rankers\n")
            lines.append("| ranker | human | t1 | t2 | t3 |")
            lines.append("|---|---|---|---|---|")
            for name in ("prior_fix", "churn", "random"):
                cells = rec.get(name, {})
                row = []
                for g in ("human", "t1", "t2", "t3"):
                    vals = list((cells.get(g) or {}).values())
                    row.append(f"{np.mean(vals):.3f} ({len(vals)})" if vals else "—")
                lines.append(f"| {name} | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- cli --


def cmd_build(args: argparse.Namespace) -> None:
    names = [s.strip() for s in args.only.split(",") if s.strip()] or \
        (EXHIBIT_POOL if args.pool == "exhibit" else PASS_POOL)
    todo = [n for n in names
            if args.force or not (args.out_dir / f"{n}.json").exists()]
    skipped = [n for n in names if n not in todo]
    for n in skipped:
        log(f"{n}: exists, skipping")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(build_repo, n, args.repos_dir, args.labels_dir,
                          args.provenance_dir, args.out_dir): n for n in todo}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                s = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"{name}: ERROR {e}")
                continue
            log(f"{name}: window {s['n_window']} commits, history {s['history_seconds']}s, "
                f"numstat {s['numstat_seconds']}s, files@mid {s['n_files_mid']}")
    log("build done")


def cmd_eval(args: argparse.Namespace) -> None:
    names = [s.strip() for s in args.only.split(",") if s.strip()] or \
        (EXHIBIT_POOL if args.pool == "exhibit" else PASS_POOL)
    rng = random.Random(args.seed)
    rows: list[dict] = []
    for n in names:
        for path, what in ((args.szz_dir / f"{n}.json", "szz"),
                           (args.features_dir / f"{n}.json", "features"),
                           (args.labels_dir / f"{n}.json", "labels")):
            if not path.exists():
                log(f"{n}: missing {what}, skipping repo")
                break
        else:
            rs = load_rows(n, args.labels_dir, args.szz_dir, args.features_dir,
                           args.szz_kind)
            rows.extend(rs)
            log(f"{n}: {len(rs)} eligible rows")
    if not rows:
        log("no rows")
        return

    result = {"generated": datetime.now(timezone.utc).isoformat(),
              "pool": args.pool, "szz_kind": args.szz_kind,
              "min_n": MIN_N, "min_pos": MIN_POS, "variants": {}}
    for v in VARIANTS:
        log(f"eval variant {v}...")
        table = cell_auc_table(rows, v)
        base = loro_model(rows, v, with_prior_fix=False)
        base_pf = loro_model(rows, v, with_prior_fix=True)
        tiers = loro_model(rows, v, with_prior_fix=False, with_tiers=True)
        auc_delta = {r: round(tiers["cell_auc"]["all"][r] - base["cell_auc"]["all"][r], 4)
                     for r in (tiers["cell_auc"].get("all") or {})
                     if r in (base["cell_auc"].get("all") or {})}
        result["variants"][v] = {
            "per_repo_auc": table,
            "pooled_auc": pool_cells(table, rng),
            "model_kamei": base,
            "model_kamei_pf": base_pf,
            "agent_flag": {"coefs": tiers["coefs"],
                           "auc_delta_per_repo": auc_delta},
            "exp_coef": exp_coef_by_group(rows, v),
            "exp_coef_ident": exp_coef_by_group(rows, v, exp_col="log_exp_ident"),
            "recall_cells": recall_cells(rows, v),
        }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.pool == "main" else f"_{args.pool}"
    if args.szz_kind != "ag":
        suffix += f"_{args.szz_kind}"
    (args.out_dir / f"predictor_eval{suffix}.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")
    md = render_md(result, args.pool, args.szz_kind)
    md_path = args.out_dir / f"PREDICTOR_EVAL{suffix.upper()}.md"
    md_path.write_text(md, encoding="utf-8")
    log(f"wrote {md_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--repos-dir", type=Path, required=True)
    b.add_argument("--labels-dir", type=Path, required=True)
    b.add_argument("--provenance-dir", type=Path, required=True)
    b.add_argument("--out-dir", type=Path, required=True)
    b.add_argument("--only", default="")
    b.add_argument("--pool", choices=("main", "exhibit"), default="main")
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--force", action="store_true")
    e = sub.add_parser("eval")
    e.add_argument("--labels-dir", type=Path, required=True)
    e.add_argument("--szz-dir", type=Path, required=True)
    e.add_argument("--features-dir", type=Path, required=True)
    e.add_argument("--out-dir", type=Path, required=True)
    e.add_argument("--only", default="")
    e.add_argument("--pool", choices=("main", "exhibit"), default="main")
    e.add_argument("--szz-kind", choices=("ag", "b"), default="ag")
    e.add_argument("--seed", type=int, default=20260604)
    args = ap.parse_args()
    if args.cmd == "build":
        cmd_build(args)
    else:
        cmd_eval(args)


if __name__ == "__main__":
    main()
