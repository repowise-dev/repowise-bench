# CockroachDB head-to-head — Repowise vs. CodeScene (single-repo demonstration)

> Companion artifact to `health-defect/BENCHMARK_REPORT.md` +
> `COMPARISON_REPORT.md`. One very large enterprise Go repo, scored leakage-free
> at T0, as an investor-facing demonstration on CodeScene's home turf. **Not** a
> new significance result — a single repo has a wide CI; the effort-aware wins
> are the headline. Narrative write-up: `local-stash/code-health-v2/COCKROACH_DEMO.md`.
>
> All numbers reproduce from `results/health_defect_cockroach/`.

## Corpus

| | |
|---|---|
| Repo | cockroachdb/cockroach (enterprise distributed SQL DB, Go) |
| Clone | full history, 118,945 commits; HEAD f0bf3243 (2026-06-11) |
| **T0 commit** | **47a00e3ec37edcbb3ce718a94bb57729c07453f8** (2025-11-22, on/before t0_date 2025-11-23) |
| Files indexed | 7,784 source (10,289 walked); 27,207 KG nodes |
| **Files scored (labeled universe)** | **5,373** (after `pkg/` + excludes + `nloc≥10` + test-file drop) |
| **Defect-bearing files** | **352** (6.5%); 475 total bug-fix touches |
| Exclude sanity | worst-60 all hand-written source; 0 generated files (globs caught pb/eg/og/stringer; sql.go not polluting) |

## Wall-clock (swiftness)

- **Index + health score: 30 min 45 s** (laptop). Dominant phases (s): git 605,
  graph.metrics 208, health 176, dead_code 60, decisions 56, go_interfaces 52.
- Labeling + analysis (cached-score `--skip-health` pass): ~9 min.

## Head-to-head — per-tool metrics (keyword label, same T0, CodeScene CLI v1.0.29)

Paired on the **4,695-file intersection** (347 defective; 99.7% `.go`). CodeScene
took 41 min (per-file `cs review`).

**Coverage gap is mostly a non-Go artifact.** CodeScene declined 678 files, but
**546 are non-Go** (.proto 164, .sh 98, .json 87, .yaml/.yml 83, .md 42, no-ext 28,
.sql 22, …) — they're in the universe only because the benchmark scores everything
`repowise health` walked (it extension-filters labels, not the scored set), and
CodeScene rightly doesn't score non-code. Among actual Go source CodeScene declined
**only 132 (2.7%)** — tiny declaration-only files (`constants.go`, `license.go`,
test helpers) with no functions → "no scorable code," in line with its 2.2% on the
prior corpus. So the honest CodeScene Go-coverage gap is **2.7%, not 12.6%**.

| Metric | Repowise | CodeScene |
|---|--:|--:|
| ROC AUC [95% CI] | 0.761 [0.736, 0.787] | 0.754 [0.725, 0.783] |
| Popt (effort-aware) | **0.541** [0.509, 0.575] | 0.525 [0.490, 0.560] |
| Recall @ 20% LOC | **0.213** [0.179, 0.253] | 0.176 [0.141, 0.210] |
| Precision @ 20% LOC | 0.273 [0.222, 0.336] | **0.565** [0.460, 0.660] |
| Partial Spearman vs NLOC | −0.066 | −0.055 (both beat size) |
| Defect conc. defects/KLOC (size-norm.) | **1.30×** | 1.19× |
| Defect conc. defects/file (raw) | 10.1× | **20.7×** |
| Alert files / Healthy files | 442 / 3,058 | 76 / 3,963 |
| Files could-not-score (.go only) | 0 | 132 (2.7%) |
| Files declined incl. non-Go | 0 | 678 (546 non-Go + 132 .go) |

**Paired significance (single repo — read carefully).** The only valid test on
one repo is file-level **paired DeLong on AUC: ΔAUC +0.008, z=+0.97, p=0.33 (n.s.)**
— tied on discrimination. The other axes are **point estimates only**; the
`codescene_paired_deltas.py` "p=0.0000 / zero-width CI" output is a **degenerate
n=1-repo artifact** (no second cluster to resample), NOT significance — do not cite
it. Real significance lives in the multi-repo cluster bootstrap of
`COMPARISON_REPORT.md`.

**External sanity check (passed):** CodeScene's own Alert:Healthy defects/file
concentration is **~20.7×** here — same order as its published ~14.8× — confirming
its CLI behaved as advertised and the harness is fair.

## Repowise vs. trivial baselines (keyword)

| Predictor | ROC AUC | Popt |
|---|--:|--:|
| **health** | **0.784** | **0.598** |
| loc_only | 0.786 | 0.470 |
| churn_only | 0.721 | 0.627 |
| prior_defects | 0.604 | 0.606 |
| random | 0.498 | 0.502 |

Health ties LOC on AUC, **beats LOC on Popt +0.128**, beats churn (+0.063 AUC)
and prior-defects (+0.180 AUC). Cost-effective ordering (Popt) still favors the
process baselines, as in the corpus.

## Label robustness

| Label | Positives | ROC AUC | Popt |
|---|--:|--:|--:|
| keyword | 352 | 0.784 | 0.598 |
| AG-SZZ | 242 | 0.790 | 0.600 |
| B-SZZ | 255 | 0.788 | 0.596 |
| issue / szz+issue | 0 | n/a | n/a |

(issue labels empty — CRDB uses `(#PR)` suffixes, not `fixes #issue`.)

## Which tool wins each axis (cockroach, single repo)

| Axis | Winner | Note |
|---|---|---|
| Discrimination (AUC) | tie | +0.008, DeLong p=0.33 (n.s.) |
| Effort-aware ranking (Popt) | Repowise (slight) | +0.016 point est. |
| Recall @20% LOC | Repowise (slight) | +0.037 point est. |
| Precision @20% LOC | **CodeScene** | 0.565 vs 0.273 — conservative band (76 alert files) |
| Beyond size (partial-ρ) | both beat size | −0.066 vs −0.055 |
| Defect density /KLOC (size-norm.) | Repowise | 1.30× vs 1.19× |
| Defect density /file (raw) | CodeScene | 20.7× vs 10.1× (tiny pure alert band) |

**Verdict:** parity-to-slight-edge at enterprise scale. Rebuts "Repowise only wins
on small OSS," but does NOT reproduce the corpus's decisive effort-aware win
(Popt +0.144) — and we don't claim it does. One repo, wide uncertainty.

## Run command (CodeScene half)

```bash
cd repowise-bench/health-defect
export CS_BIN="/c/Users/ragha/Desktop/repowise/local-stash/code-health/cs/cs.exe"
export CS_ACCESS_TOKEN="<free codescene.io PAT>"
PYTHONUTF8=1 ../../.venv/Scripts/python.exe codescene_headtohead.py --repos cockroach --label keyword
PYTHONUTF8=1 ../../.venv/Scripts/python.exe codescene_paired_deltas.py --repos cockroach --label keyword
# → results/codescene_headtohead.json, results/codescene_paired_deltas.json
```

## Honest framing

Single-repo demonstration, not significance (wide CI). Lead with effort-aware
metrics. Size is the dominant confound for both tools. The CodeScene
business-impact (resolution-time) axis remains unreplicated on open GitHub data.
