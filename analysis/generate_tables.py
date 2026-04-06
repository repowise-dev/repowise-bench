#!/usr/bin/env python3
"""
Generate paper-ready tables and figures from experiment results.

Usage:
    python analysis/generate_tables.py results/

Outputs:
    results/tables/   — LaTeX tables for the paper
    results/figures/   — PNG/PDF figures
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
import csv


def load_all_results(results_root: str) -> dict:
    """Load results from all benchmark subdirs."""
    all_records = defaultdict(list)
    root = Path(results_root)

    for subdir in root.iterdir():
        if not subdir.is_dir():
            continue
        for f in subdir.glob("*.jsonl"):
            benchmark = f.stem  # swe_qa, swe_bench, etc.
            with open(f) as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                        all_records[benchmark].append(record)
                    except json.JSONDecodeError:
                        continue

    return all_records


# ============================================================
# TABLE 1: Main Results (RQ1)
# ============================================================

def generate_main_results_table(records: dict, out_dir: Path):
    """
    Table 1: Performance across benchmarks with/without Repowise.

    | Benchmark   | Metric      | C0 (bare) | C1 (graph+git) | C2 (full) | C3 (full+) |
    |-------------|-------------|-----------|-----------------|-----------|------------|
    | SWE-QA      | Avg Score   | ...       | ...             | ...       | ...        |
    | SWE-bench   | Resolve %   | ...       | ...             | ...       | ...        |
    | FEA-Bench   | Resolve %   | ...       | ...             | ...       | ...        |
    """
    conditions = ["C0_bare", "C1_graph_git", "C2_full", "C3_full_plus"]

    rows = []

    # SWE-QA: average judge score
    if "swe_qa" in records:
        row = {"Benchmark": "SWE-QA", "Metric": "Avg Score (1-10)"}
        for cond in conditions:
            cond_records = [r for r in records["swe_qa"] if r["condition"] == cond]
            scores = []
            for r in cond_records:
                js = r.get("judge_scores", {})
                if "error" not in js:
                    vals = [v for v in js.values() if isinstance(v, (int, float))]
                    if vals:
                        scores.append(sum(vals) / len(vals))
            row[cond] = f"{sum(scores)/len(scores):.1f}" if scores else "—"
        rows.append(row)

    # SWE-bench: resolve rate
    if "swe_bench" in records:
        row = {"Benchmark": "SWE-bench Verified", "Metric": "Resolve Rate (%)"}
        for cond in conditions:
            cond_records = [r for r in records["swe_bench"] if r["condition"] == cond]
            if cond_records:
                resolved = sum(1 for r in cond_records if r.get("resolved"))
                total = len(cond_records)
                row[cond] = f"{resolved/total*100:.1f}"
            else:
                row[cond] = "—"
        rows.append(row)

    # Write LaTeX
    latex = generate_latex_table(
        rows, conditions,
        caption="Main results across benchmarks. Higher is better.",
        label="tab:main_results"
    )
    (out_dir / "table1_main_results.tex").write_text(latex)

    # Also write CSV for easy inspection
    write_csv(rows, conditions, out_dir / "table1_main_results.csv")

    print(f"  ✅ Table 1: Main Results")


# ============================================================
# TABLE 2: Ablation by Question Type (RQ2)
# ============================================================

def generate_ablation_table(records: dict, out_dir: Path):
    """
    Table 2: SWE-QA scores broken down by question type × condition.

    | Q Type | C0   | C1   | C2   | C3   | Δ (C2-C0) |
    |--------|------|------|------|------|------------|
    | What   | ...  | ...  | ...  | ...  | ...        |
    | Why    | ...  | ...  | ...  | ...  | ...        |
    | Where  | ...  | ...  | ...  | ...  | ...        |
    | How    | ...  | ...  | ...  | ...  | ...        |
    """
    if "swe_qa" not in records:
        print("  ⏩ Skipping ablation table (no SWE-QA results)")
        return

    conditions = ["C0_bare", "C1_graph_git", "C2_full", "C3_full_plus"]
    rows = []

    for qtype in ["What", "Why", "Where", "How"]:
        row = {"Question Type": qtype}
        cond_avgs = {}

        for cond in conditions:
            cond_records = [
                r for r in records["swe_qa"]
                if r["condition"] == cond and r.get("question_type") == qtype
            ]
            scores = []
            for r in cond_records:
                js = r.get("judge_scores", {})
                if "error" not in js:
                    vals = [v for v in js.values() if isinstance(v, (int, float))]
                    if vals:
                        scores.append(sum(vals) / len(vals))
            avg = sum(scores) / len(scores) if scores else None
            cond_avgs[cond] = avg
            row[cond] = f"{avg:.1f}" if avg else "—"

        # Delta
        c0 = cond_avgs.get("C0_bare")
        c2 = cond_avgs.get("C2_full")
        if c0 and c2:
            row["Δ (C2−C0)"] = f"{c2 - c0:+.1f}"
        else:
            row["Δ (C2−C0)"] = "—"

        rows.append(row)

    extra_cols = ["Δ (C2−C0)"]
    latex = generate_latex_table(
        rows, conditions, extra_cols=extra_cols,
        caption="SWE-QA ablation by question type. Δ shows improvement from full Repowise over bare agent.",
        label="tab:ablation_qtype",
        row_key="Question Type"
    )
    (out_dir / "table2_ablation_qtype.tex").write_text(latex)
    write_csv(rows, conditions + extra_cols, out_dir / "table2_ablation_qtype.csv")

    print(f"  ✅ Table 2: Ablation by Question Type")


# ============================================================
# TABLE 3: Token Efficiency (RQ3)
# ============================================================

def generate_efficiency_table(records: dict, out_dir: Path):
    """
    Table 3: Resource consumption by condition across benchmarks.

    | Benchmark | Condition | Avg Tokens | Avg Tools | Avg Files | Avg Cost |
    """
    rows = []

    for bench_name, bench_records in records.items():
        conditions = sorted(set(r["condition"] for r in bench_records))
        for cond in conditions:
            cond_records = [r for r in bench_records if r["condition"] == cond]
            valid = [r for r in cond_records if r.get("total_tokens", 0) > 0]
            if not valid:
                continue

            row = {
                "Benchmark": bench_name,
                "Condition": cond,
                "Avg Input Tok": f"{sum(r['input_tokens'] for r in valid)/len(valid):,.0f}",
                "Avg Output Tok": f"{sum(r['output_tokens'] for r in valid)/len(valid):,.0f}",
                "Avg Tool Calls": f"{sum(r['num_tool_calls'] for r in valid)/len(valid):.1f}",
                "Avg Files Read": f"{sum(len(r.get('files_explored',[])) for r in valid)/len(valid):.1f}",
                "Avg Cost ($)": f"{sum(r['estimated_cost_usd'] for r in valid)/len(valid):.3f}",
            }
            rows.append(row)

    # Write CSV (LaTeX table too complex for generic generator)
    fieldnames = ["Benchmark", "Condition", "Avg Input Tok", "Avg Output Tok",
                  "Avg Tool Calls", "Avg Files Read", "Avg Cost ($)"]
    csv_path = out_dir / "table3_efficiency.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  ✅ Table 3: Token Efficiency")


# ============================================================
# HELPERS
# ============================================================

def generate_latex_table(rows, conditions, extra_cols=None,
                         caption="", label="", row_key="Benchmark"):
    """Generate a basic LaTeX table."""
    if extra_cols is None:
        extra_cols = []

    cols = [row_key] + conditions + extra_cols
    col_spec = "l" + "c" * (len(cols) - 1)

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{" + caption + "}",
        r"\label{" + label + "}",
        r"\begin{tabular}{" + col_spec + "}",
        r"\toprule",
    ]

    # Header
    header_names = {
        "C0_bare": "C0 (Bare)",
        "C1_graph_git": "C1 (Graph+Git)",
        "C2_full": "C2 (Full)",
        "C3_full_plus": "C3 (Full+)",
    }
    header = " & ".join(
        header_names.get(c, c) for c in cols
    ) + r" \\"
    lines.append(header)
    lines.append(r"\midrule")

    # Data rows
    for row in rows:
        values = []
        for c in cols:
            val = row.get(c, row.get("Metric", "—"))
            values.append(str(val))
        lines.append(" & ".join(values) + r" \\")

    lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])

    return "\n".join(lines)


def write_csv(rows, conditions, path, row_key="Benchmark"):
    """Write results as CSV for easy inspection."""
    fieldnames = [row_key] + list(conditions)
    # Add any extra keys from rows
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    if len(sys.argv) < 2:
        print("Usage: python analysis/generate_tables.py results/")
        sys.exit(1)

    results_root = sys.argv[1]
    records = load_all_results(results_root)

    if not records:
        print("❌ No results found. Run experiments first.")
        sys.exit(1)

    print(f"Loaded results: {', '.join(f'{k}({len(v)})' for k, v in records.items())}\n")

    # Create output dir
    out_dir = Path(results_root) / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)

    generate_main_results_table(records, out_dir)
    generate_ablation_table(records, out_dir)
    generate_efficiency_table(records, out_dir)

    print(f"\n📁 Tables saved to: {out_dir}")
    print("   Copy .tex files into your paper's LaTeX source")
    print("   CSV files are for your own inspection")


if __name__ == "__main__":
    main()
