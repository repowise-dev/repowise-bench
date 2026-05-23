from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def scatter_health_vs_defects(joined: list[dict], output_path: str | Path) -> None:
    scores = [d["health_score"] for d in joined]
    defects = [d["defect_count"] for d in joined]
    has_findings = [d.get("finding_count", 0) > 0 for d in joined]

    colors = ["#e74c3c" if f else "#2ecc71" for f in has_findings]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.scatter(scores, defects, c=colors, alpha=0.7, edgecolors="white", s=60)

    if len(scores) >= 3:
        from scipy.stats import spearmanr

        rho, p = spearmanr(scores, defects)
        z = np.polyfit(scores, defects, 1)
        poly = np.poly1d(z)
        xs = np.linspace(min(scores), max(scores), 100)
        ax.plot(xs, poly(xs), "--", color="#3498db", alpha=0.7)
        ax.text(
            0.05,
            0.95,
            f"Spearman ρ = {rho:.3f}\np = {p:.4f}",
            transform=ax.transAxes,
            verticalalignment="top",
            fontsize=10,
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )

    ax.set_xlabel("Health Score", fontsize=12)
    ax.set_ylabel("Bug-Fix Commits", fontsize=12)
    ax.set_title("Health Score vs. Defect Count", fontsize=14)
    ax.set_xlim(0, 10.5)

    from matplotlib.lines import Line2D

    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#e74c3c", markersize=8, label="Has biomarker findings"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#2ecc71", markersize=8, label="No findings"),
    ]
    ax.legend(handles=legend_elements, loc="upper right")

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)


def bar_defect_density_by_bucket(
    bucket_data: list[dict], output_path: str | Path
) -> None:
    labels = [b["bucket"] for b in bucket_data]
    densities = [b["defects_per_kloc"] for b in bucket_data]
    counts = [b["file_count"] for b in bucket_data]

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ["#e74c3c", "#f39c12", "#f1c40f", "#2ecc71"]
    colors = colors[: len(labels)]

    bars = ax.bar(labels, densities, color=colors, edgecolor="white")

    for bar, count in zip(bars, counts):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.05,
            f"n={count}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax.set_xlabel("Health Score Bucket", fontsize=12)
    ax.set_ylabel("Defects per KLOC", fontsize=12)
    ax.set_title("Defect Density by Health Bucket", fontsize=14)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)


def bar_biomarker_importance(
    biomarker_results: list[dict], output_path: str | Path
) -> None:
    valid = [b for b in biomarker_results if b.get("cliffs_delta") is not None]
    if not valid:
        return

    valid.sort(key=lambda b: b["cliffs_delta"], reverse=True)
    names = [b["biomarker"] for b in valid]
    deltas = [b["cliffs_delta"] for b in valid]

    fig, ax = plt.subplots(figsize=(10, max(4, len(valid) * 0.5)))
    colors = ["#e74c3c" if d > 0 else "#3498db" for d in deltas]
    ax.barh(names, deltas, color=colors, edgecolor="white")
    ax.axvline(x=0, color="gray", linewidth=0.5)
    ax.set_xlabel("Cliff's Delta (positive = more defects)", fontsize=11)
    ax.set_title("Biomarker Effect on Defect Count", fontsize=14)
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)


def roc_curve_plot(
    roc_data: dict[str, Any], output_path: str | Path
) -> None:
    fpr = roc_data["fpr"]
    tpr = roc_data["tpr"]
    auc = roc_data["auc"]

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot(fpr, tpr, color="#e74c3c", lw=2, label=f"ROC (AUC = {auc:.3f})")
    ax.plot([0, 1], [0, 1], "--", color="gray", lw=1, label="Random")
    ax.set_xlabel("False Positive Rate", fontsize=12)
    ax.set_ylabel("True Positive Rate", fontsize=12)
    ax.set_title("ROC Curve: Health Score as Defect Predictor", fontsize=14)
    ax.legend(loc="lower right")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150)
    plt.close(fig)


def table_top_k_files(
    joined: list[dict], k: int, output_path: str | Path
) -> None:
    sorted_files = sorted(joined, key=lambda d: d["health_score"])
    top_k = sorted_files[:k]

    fig, ax = plt.subplots(figsize=(14, max(3, k * 0.35 + 1.5)))
    ax.axis("off")

    headers = ["File", "Score", "Bugs", "NLOC", "CCN", "Nesting", "Test?"]
    rows = []
    for d in top_k:
        short_path = d["file_path"]
        if len(short_path) > 45:
            short_path = "..." + short_path[-42:]
        rows.append([
            short_path,
            f"{d['health_score']:.1f}",
            str(d["defect_count"]),
            str(d["nloc"]),
            str(d.get("max_ccn", "-")),
            str(d.get("max_nesting", "-")),
            "Y" if d.get("has_test_file") else "N",
        ])

    table = ax.table(
        cellText=rows,
        colLabels=headers,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.4)

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#34495e")
            cell.set_text_props(color="white", fontweight="bold")
        elif row > 0 and col == 0:
            cell.set_text_props(ha="left", fontfamily="monospace")
        if row > 0:
            defects = int(rows[row - 1][2])
            if defects > 0:
                cell.set_facecolor("#fadbd8")
            else:
                cell.set_facecolor("#d5f5e3")

    ax.set_title(f"Top {k} Unhealthiest Files", fontsize=14, pad=20)
    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


def generate_all_charts(
    joined: list[dict],
    correlation: dict,
    charts_dir: str | Path,
) -> None:
    charts_dir = Path(charts_dir)
    charts_dir.mkdir(parents=True, exist_ok=True)

    for d in joined:
        findings_for_file = [
            f for f in correlation.get("_findings", [])
            if f["file_path"] == d["file_path"]
        ]
        d["finding_count"] = len(findings_for_file)

    scatter_health_vs_defects(joined, charts_dir / "scatter.png")
    bar_defect_density_by_bucket(
        correlation["density_by_bucket"], charts_dir / "density_ratio.png"
    )
    bar_biomarker_importance(
        correlation["per_biomarker"], charts_dir / "biomarker_importance.png"
    )
    roc_curve_plot(correlation["roc_auc"], charts_dir / "roc.png")

    k = correlation["precision_at_k"]["k"]
    table_top_k_files(joined, k, charts_dir / "top_k_table.png")
