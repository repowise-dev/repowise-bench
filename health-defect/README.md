# health-defect — Code Health vs. Defect Prediction Benchmark

A reproducible benchmark proving that Repowise's deterministic code health
scores predict real-world defects in open-source Python projects.

## Headline numbers

Across three public repositories (862 source files, 6-month defect window):

| Repo | Files | Spearman ρ | p-value | Defect ratio | ROC AUC | Precision@20 |
|------|------:|----------:|---------:|-------------:|--------:|-------------:|
| Django   | 542 | **-0.337** | <0.0001 | **12x** | 0.698 | **70%** |
| Pydantic | 216 | -0.229 | 0.0007 | 10x | **0.742** | 30% |
| FastAPI  | 104 | -0.272 | 0.0053 | 75x | 0.715 | 35% |

**Files scoring below 4.0 have 10-75x more bug-fixing commits than files
scoring above 8.0.** The correlation is statistically significant (p < 0.01)
across all three codebases.

Top biomarker predictors (by Cliff's delta effect size):

1. `developer_congestion` — δ = +0.78 (Django)
2. `untested_hotspot` — δ = +0.69 (Django), +0.67 (FastAPI)
3. `brain_method` — δ = +0.62 (Pydantic), +0.43 (Django)

> **Full analysis:** See [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md) for per-repo deep
> dives, biomarker breakdowns, CodeScene comparison, and limitations discussion.

---

## Methodology

```
T0 (6 months ago)                    T1 (today)
│                                     │
│  1. Run repowise health at T0      │
│     → per-file health scores (1-10) │
│                                     │
│  2. Count bug-fixing commits T0→T1  │
│     → per-file defect counts        │
│                                     │
│  3. Correlate: low score → more bugs│
└─────────────────────────────────────┘
```

### Defect identification

| Repo | Strategy | Precision |
|------|----------|-----------|
| FastAPI | Gitmoji: commits with 🐛 emoji | ~95% |
| Django | Prefix: commits starting with `Fixed #` | ~95% |
| Pydantic | Keyword: `fix`, `bug`, `patch`, `resolve` with exclusions | ~85% |

### Statistical tests

1. **Spearman rank correlation** — non-parametric, handles zero-inflated data
2. **Partial Spearman** controlling for NLOC — isolates signal beyond file size
3. **Kruskal-Wallis** across red/yellow/green health categories
4. **ROC/AUC** — binary classification (had bugs or not)
5. **Precision@K** — of the K unhealthiest files, how many had bugs?
6. **Per-biomarker Mann-Whitney U** with Cliff's delta effect sizes

### Filters

- Only source files (no tests, docs, configs)
- Minimum 10 NLOC
- Only `.py` files under `source_root`

---

## Reproduction

### Prerequisites

- Python 3.11+
- Repowise installed (or local checkout with venv)
- `scipy`, `matplotlib`, `pyyaml`

### Step 1: Clone repos

```bash
cd repowise-bench/health-defect
python run_benchmark.py --clone --skip-health
```

Or manually clone into `../repos/`:

```bash
git clone --depth=5000 https://github.com/fastapi/fastapi.git ../repos/fastapi
git clone --depth=5000 https://github.com/django/django.git ../repos/django
git clone --depth=5000 https://github.com/pydantic/pydantic.git ../repos/pydantic
```

### Step 2: Index repos

Each repo needs a Repowise index before health analysis:

```bash
cd ../repos/fastapi && repowise init -y --index-only
cd ../repos/django && repowise init -y --index-only
cd ../repos/pydantic && repowise init -y --index-only
```

### Step 3: Run benchmark

```bash
# All repos
python run_benchmark.py

# Single repo
python run_benchmark.py --repo django

# Reuse existing health scores (only re-count defects + re-analyze)
python run_benchmark.py --skip-health

# Custom repo location
python run_benchmark.py --repos-dir /path/to/repos
```

### Step 4: View results

Results are saved to `../results/health_defect_{repo}/`:

```
health_defect_django/
├── health_scores.json      # Raw repowise health output
├── defect_counts.json      # Per-file bug-fix commit counts
├── correlation.json        # All statistical results
├── joined_data.json        # Merged health + defect data
└── charts/
    ├── scatter.png          # Health score vs. defect count
    ├── density_ratio.png    # Defects per KLOC by health bucket
    ├── biomarker_importance.png  # Cliff's delta per biomarker
    ├── roc.png              # ROC curve
    └── top_k_table.png      # Top 20 unhealthiest files
```

---

## Output schema

### correlation.json

```json
{
  "descriptive": {
    "n_files": 542,
    "n_with_defects": 149,
    "pct_zero_defects": 72.5
  },
  "spearman": {
    "rho": -0.337,
    "p_value": 0.0000,
    "n": 542
  },
  "partial_spearman_nloc": -0.127,
  "kruskal_wallis": {
    "h_stat": 65.36,
    "p_value": 0.0000,
    "group_sizes": {"red": 12, "yellow": 129, "green": 401}
  },
  "density_ratio": {
    "raw_ratio": 12.7,
    "nloc_normalized_ratio": 1.2,
    "low_group": {"count": 12, "defects_per_kloc": 3.28},
    "high_group": {"count": 385, "defects_per_kloc": 2.77}
  },
  "roc_auc": {"auc": 0.698},
  "precision_at_k": {"k": 20, "precision": 0.70},
  "per_biomarker": [
    {"biomarker": "developer_congestion", "cliffs_delta": 0.775, "p_value": 0.0000}
  ]
}
```

---

## Adding a new repo

1. Add an entry to `config.yaml`:

```yaml
  - name: "myrepo"
    repo_url: "https://github.com/org/myrepo.git"
    language: "python"
    source_root: "myrepo/"
    t0_date: "2025-11-23"
    defect_strategy: "keyword"      # or "gitmoji" or "prefix"
    bug_keywords: ["fix", "bug"]
    exclude_keywords: ["typo", "docs"]
```

2. Clone and index: `cd ../repos/myrepo && repowise init -y --index-only`
3. Run: `python run_benchmark.py --repo myrepo`

### Defect strategies

| Strategy | Config fields | Best for |
|----------|---------------|----------|
| `gitmoji` | `gitmoji_bug: "🐛"` | Repos using gitmoji convention |
| `prefix` | `bug_prefix: "Fixed #"` | Repos with structured commit prefixes |
| `keyword` | `bug_keywords`, `exclude_keywords` | General fallback |
