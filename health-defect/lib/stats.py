from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Callable

from scipy.stats import kruskal, mannwhitneyu, spearmanr

ALL_BIOMARKERS = [
    "brain_method",
    "low_cohesion",
    "god_class",
    "nested_complexity",
    "complex_method",
    "bumpy_road",
    "complex_conditional",
    "large_method",
    "primitive_obsession",
    "dry_violation",
    "untested_hotspot",
    "coverage_gap",
    "developer_congestion",
    "knowledge_loss",
    "hidden_coupling",
    "function_hotspot",
    "code_age_volatility",
    "ownership_risk",
    "churn_risk",
    "change_entropy",
    "co_change_scatter",
    "large_assertion_block",
    "duplicated_assertion_block",
]


def spearman_correlation(
    scores: list[float], defects: list[int]
) -> dict[str, float]:
    if len(scores) < 3:
        return {"rho": 0.0, "p_value": 1.0, "n": len(scores)}
    rho, p_value = spearmanr(scores, defects)
    return {"rho": float(rho), "p_value": float(p_value), "n": len(scores)}


def partial_spearman(
    x: list[float], y: list[float], z: list[float]
) -> float:
    if len(x) < 3:
        return 0.0
    rho_xy, _ = spearmanr(x, y)
    rho_xz, _ = spearmanr(x, z)
    rho_yz, _ = spearmanr(y, z)
    denom = ((1 - rho_xz**2) * (1 - rho_yz**2)) ** 0.5
    if denom == 0:
        return 0.0
    return float((rho_xy - rho_xz * rho_yz) / denom)


def defect_density_ratio(
    joined: list[dict],
    low_threshold: float = 5.0,
    high_threshold: float = 8.0,
) -> dict[str, Any]:
    low = [d for d in joined if d["health_score"] < low_threshold]
    high = [d for d in joined if d["health_score"] > high_threshold]

    mean_low = _safe_mean([d["defect_count"] for d in low])
    mean_high = _safe_mean([d["defect_count"] for d in high])
    raw_ratio = mean_low / mean_high if mean_high > 0 else float("inf")

    nloc_low = sum(d["nloc"] for d in low)
    nloc_high = sum(d["nloc"] for d in high)
    defects_low = sum(d["defect_count"] for d in low)
    defects_high = sum(d["defect_count"] for d in high)

    density_low = defects_low / nloc_low * 1000 if nloc_low > 0 else 0
    density_high = defects_high / nloc_high * 1000 if nloc_high > 0 else 0
    nloc_ratio = density_low / density_high if density_high > 0 else float("inf")

    return {
        "raw_ratio": raw_ratio,
        "nloc_normalized_ratio": nloc_ratio,
        "low_group": {"count": len(low), "mean_defects": mean_low, "total_nloc": nloc_low, "defects_per_kloc": density_low},
        "high_group": {"count": len(high), "mean_defects": mean_high, "total_nloc": nloc_high, "defects_per_kloc": density_high},
    }


def defect_density_by_bucket(
    joined: list[dict], boundaries: list[float]
) -> list[dict]:
    boundaries = sorted(boundaries)
    labels = []
    lo = 0.0
    for b in boundaries:
        labels.append(f"[{lo:.0f}-{b:.0f})")
        lo = b
    labels.append(f"[{lo:.0f}-10]")

    buckets: list[list[dict]] = [[] for _ in range(len(boundaries) + 1)]
    for d in joined:
        s = d["health_score"]
        placed = False
        for i, b in enumerate(boundaries):
            if s < b:
                buckets[i].append(d)
                placed = True
                break
        if not placed:
            buckets[-1].append(d)

    results = []
    for label, bucket in zip(labels, buckets):
        n = len(bucket)
        total_defects = sum(d["defect_count"] for d in bucket)
        total_nloc = sum(d["nloc"] for d in bucket)
        results.append({
            "bucket": label,
            "file_count": n,
            "total_defects": total_defects,
            "mean_defects": total_defects / n if n > 0 else 0,
            "defects_per_kloc": total_defects / total_nloc * 1000 if total_nloc > 0 else 0,
        })
    return results


def precision_at_k(joined: list[dict], k: int = 20) -> dict[str, Any]:
    sorted_files = sorted(joined, key=lambda d: d["health_score"])
    k = min(k, len(sorted_files))
    bottom_k = sorted_files[:k]
    true_positives = sum(1 for d in bottom_k if d["defect_count"] > 0)
    return {
        "k": k,
        "true_positives": true_positives,
        "precision": true_positives / k if k > 0 else 0,
        "files": [
            {"file_path": d["file_path"], "score": d["health_score"], "defects": d["defect_count"]}
            for d in bottom_k
        ],
    }


def cliffs_delta(group_a: list[float], group_b: list[float]) -> float:
    if not group_a or not group_b:
        return 0.0
    n_a, n_b = len(group_a), len(group_b)
    dominance = 0
    for a in group_a:
        for b in group_b:
            if a > b:
                dominance += 1
            elif a < b:
                dominance -= 1
    return dominance / (n_a * n_b)


def per_biomarker_analysis(
    joined: list[dict], findings: list[dict]
) -> list[dict]:
    files_by_biomarker: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        files_by_biomarker[f["biomarker_type"]].add(f["file_path"])

    defects_by_file = {d["file_path"]: d["defect_count"] for d in joined}
    all_files = set(defects_by_file.keys())
    results = []

    for biomarker in ALL_BIOMARKERS:
        files_with = files_by_biomarker.get(biomarker, set()) & all_files
        files_without = all_files - files_with

        defects_with = [defects_by_file[f] for f in files_with]
        defects_without = [defects_by_file[f] for f in files_without]

        entry: dict[str, Any] = {
            "biomarker": biomarker,
            "files_with": len(files_with),
            "files_without": len(files_without),
            "mean_defects_with": _safe_mean(defects_with),
            "mean_defects_without": _safe_mean(defects_without),
        }

        if len(defects_with) >= 3 and len(defects_without) >= 3:
            u_stat, p_value = mannwhitneyu(
                defects_with, defects_without, alternative="greater"
            )
            entry["u_stat"] = float(u_stat)
            entry["p_value"] = float(p_value)
            entry["cliffs_delta"] = cliffs_delta(
                [float(x) for x in defects_with],
                [float(x) for x in defects_without],
            )
        else:
            entry["u_stat"] = None
            entry["p_value"] = None
            entry["cliffs_delta"] = None

        results.append(entry)

    results.sort(key=lambda r: r.get("cliffs_delta") or 0, reverse=True)
    return results


def kruskal_wallis_by_category(joined: list[dict]) -> dict[str, Any]:
    green = [d["defect_count"] for d in joined if d["health_score"] >= 8.0]
    yellow = [d["defect_count"] for d in joined if 4.0 <= d["health_score"] < 8.0]
    red = [d["defect_count"] for d in joined if d["health_score"] < 4.0]

    groups = {"green": green, "yellow": yellow, "red": red}
    non_empty = {k: v for k, v in groups.items() if len(v) >= 1}

    result: dict[str, Any] = {
        "group_sizes": {k: len(v) for k, v in groups.items()},
        "group_means": {k: _safe_mean(v) for k, v in groups.items()},
    }

    if len(non_empty) >= 2:
        vals = list(non_empty.values())
        h_stat, p_value = kruskal(*vals)
        result["h_stat"] = float(h_stat)
        result["p_value"] = float(p_value)
    else:
        result["h_stat"] = None
        result["p_value"] = None

    return result


def roc_auc(joined: list[dict]) -> dict[str, Any]:
    y_true = [1 if d["defect_count"] > 0 else 0 for d in joined]
    y_score = [10.0 - d["health_score"] for d in joined]

    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos

    if n_pos == 0 or n_neg == 0:
        return {"auc": 0.5, "fpr": [], "tpr": [], "n_pos": n_pos, "n_neg": n_neg}

    pairs = sorted(zip(y_score, y_true), key=lambda x: -x[0])
    tp = 0
    fp = 0
    fpr_list = [0.0]
    tpr_list = [0.0]
    prev_score = None

    for score, label in pairs:
        if prev_score is not None and score != prev_score:
            fpr_list.append(fp / n_neg)
            tpr_list.append(tp / n_pos)
        if label == 1:
            tp += 1
        else:
            fp += 1
        prev_score = score

    fpr_list.append(fp / n_neg)
    tpr_list.append(tp / n_pos)

    auc = 0.0
    for i in range(1, len(fpr_list)):
        auc += (fpr_list[i] - fpr_list[i - 1]) * (tpr_list[i] + tpr_list[i - 1]) / 2

    return {
        "auc": float(auc),
        "fpr": fpr_list,
        "tpr": tpr_list,
        "n_pos": n_pos,
        "n_neg": n_neg,
    }


def effort_aware_at_loc(joined: list[dict], fraction: float = 0.20) -> dict[str, Any]:
    """Effort-aware precision/recall when only ``fraction`` of total LOC can be
    inspected (Mende & Koschke).

    Files are ranked by predicted risk (lowest health first). We walk that
    ranking accumulating NLOC until the inspection budget (``fraction`` of total
    LOC) is spent, then ask: of the defects/defective-files that exist, how many
    fall inside that budget. This neutralizes the "just flag the big files"
    critique — spending LOC budget on one huge file buys little recall.
    """
    total_loc = sum(max(d["nloc"], 1) for d in joined)
    total_defects = sum(d["defect_count"] for d in joined)
    total_defective_files = sum(1 for d in joined if d["defect_count"] > 0)
    budget = total_loc * fraction

    # Rank by risk: lowest health first, tie-break smaller files first so the
    # budget is not wasted on one giant file.
    ranked = sorted(joined, key=lambda d: (d["health_score"], d["nloc"]))

    spent = 0.0
    files_inspected = 0
    defects_found = 0
    defective_files_found = 0
    for d in ranked:
        nloc = max(d["nloc"], 1)
        if spent + nloc > budget and files_inspected > 0:
            break
        spent += nloc
        files_inspected += 1
        defects_found += d["defect_count"]
        if d["defect_count"] > 0:
            defective_files_found += 1

    return {
        "fraction_loc": fraction,
        "loc_budget": budget,
        "loc_inspected": spent,
        "files_inspected": files_inspected,
        "defective_files_found": defective_files_found,
        # Precision: of the files we inspected, how many were defective.
        "precision": defective_files_found / files_inspected if files_inspected else 0.0,
        # Recall: of all defect touches, how many we caught within the budget.
        "recall_defects": defects_found / total_defects if total_defects else 0.0,
        "recall_files": defective_files_found / total_defective_files if total_defective_files else 0.0,
    }


def _alberg_area(ordered: list[dict], total_loc: float, total_defects: float) -> float:
    """Trapezoidal area under the cumulative-defects vs cumulative-LOC curve for
    a given module ordering (the Alberg diagram used by Popt)."""
    if total_loc <= 0 or total_defects <= 0:
        return 0.0
    area = 0.0
    cum_loc = 0.0
    cum_def = 0.0
    prev_x = 0.0
    prev_y = 0.0
    for d in ordered:
        cum_loc += max(d["nloc"], 1)
        cum_def += d["defect_count"]
        x = cum_loc / total_loc
        y = cum_def / total_defects
        area += (x - prev_x) * (y + prev_y) / 2.0
        prev_x, prev_y = x, y
    return area


def popt(joined: list[dict]) -> dict[str, Any]:
    """Normalized cost-effectiveness Popt (Mende & Koschke 2009).

    Compares the model's effort/recall curve against the optimal (defect-density
    ordering) and worst curves: Popt = 1 − (A_opt − A_model)/(A_opt − A_worst).
    0.5 ≈ random; 1.0 = optimal. Reported alongside Precision@20%LOC because
    both are effort-aware and immune to the size confound.
    """
    total_loc = sum(max(d["nloc"], 1) for d in joined)
    total_defects = sum(d["defect_count"] for d in joined)
    if total_defects <= 0 or len(joined) < 3:
        return {"popt": None, "n": len(joined)}

    # Model orders by predicted risk (lowest health first).
    model_order = sorted(joined, key=lambda d: (d["health_score"], d["nloc"]))
    # Optimal orders by realized defect density (defects per LOC) descending.
    optimal_order = sorted(
        joined, key=lambda d: d["defect_count"] / max(d["nloc"], 1), reverse=True
    )
    worst_order = list(reversed(optimal_order))

    a_model = _alberg_area(model_order, total_loc, total_defects)
    a_opt = _alberg_area(optimal_order, total_loc, total_defects)
    a_worst = _alberg_area(worst_order, total_loc, total_defects)

    denom = a_opt - a_worst
    value = 1.0 - (a_opt - a_model) / denom if denom else 0.5
    return {"popt": float(value), "area_model": a_model, "area_optimal": a_opt, "area_worst": a_worst}


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def bootstrap_ci(
    joined: list[dict],
    metric: Callable[[list[dict]], float | None],
    *,
    n_boot: int = 1000,
    ci: float = 0.95,
    seed: int = 12345,
) -> dict[str, Any]:
    """Bootstrap CI for a per-file metric (AUC, Popt, …) by resampling files
    with replacement within the repo. Deterministic given ``seed`` so the report
    reproduces. Returns point estimate, ``[lo, hi]`` and ``n``."""
    point = metric(joined)
    n = len(joined)
    if point is None or n < 3:
        return {"point": point, "lo": None, "hi": None, "n": n, "n_boot": 0}
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        resampled = [joined[rng.randrange(n)] for _ in range(n)]
        try:
            v = metric(resampled)
        except Exception:  # noqa: BLE001 — degenerate resample, skip
            v = None
        if v is not None and v == v:  # not None, not NaN
            samples.append(float(v))
    if not samples:
        return {"point": float(point), "lo": None, "hi": None, "n": n, "n_boot": 0}
    samples.sort()
    alpha = (1.0 - ci) / 2.0
    return {
        "point": float(point),
        "lo": _percentile(samples, alpha),
        "hi": _percentile(samples, 1.0 - alpha),
        "n": n,
        "n_boot": len(samples),
        "ci": ci,
    }


def auc_metric(joined: list[dict]) -> float:
    return roc_auc(joined)["auc"]


def popt_metric(joined: list[dict]) -> float | None:
    return (popt(joined) or {}).get("popt")


def descriptive_stats(joined: list[dict]) -> dict[str, Any]:
    scores = [d["health_score"] for d in joined]
    defects = [d["defect_count"] for d in joined]
    n = len(joined)
    n_with_defects = sum(1 for d in defects if d > 0)

    return {
        "n_files": n,
        "n_with_defects": n_with_defects,
        "n_zero_defects": n - n_with_defects,
        "pct_zero_defects": (n - n_with_defects) / n * 100 if n > 0 else 0,
        "health_score": {
            "mean": _safe_mean(scores),
            "median": _median(scores),
            "min": min(scores) if scores else 0,
            "max": max(scores) if scores else 0,
            "std": _std(scores),
        },
        "defect_count": {
            "mean": _safe_mean(defects),
            "median": _median(defects),
            "min": min(defects) if defects else 0,
            "max": max(defects) if defects else 0,
            "total": sum(defects),
        },
    }


def analyze_all(
    joined: list[dict],
    findings: list[dict],
    defaults: dict,
) -> dict[str, Any]:
    scores = [d["health_score"] for d in joined]
    defects = [d["defect_count"] for d in joined]
    nlocs = [float(d["nloc"]) for d in joined]

    return {
        "descriptive": descriptive_stats(joined),
        "spearman": spearman_correlation(scores, [int(d) for d in defects]),
        "partial_spearman_nloc": partial_spearman(scores, [float(d) for d in defects], nlocs),
        "kruskal_wallis": kruskal_wallis_by_category(joined),
        "density_ratio": defect_density_ratio(
            joined,
            low_threshold=defaults["health_buckets"][0],
            high_threshold=defaults["health_buckets"][-1],
        ),
        "density_by_bucket": defect_density_by_bucket(joined, defaults["health_buckets"]),
        "precision_at_k": precision_at_k(joined, k=defaults["precision_k"]),
        "effort_at_20pct_loc": effort_aware_at_loc(joined, fraction=0.20),
        "popt": popt(joined),
        "per_biomarker": per_biomarker_analysis(joined, findings),
        "roc_auc": roc_auc(joined),
    }


def _safe_mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _std(values: list) -> float:
    if len(values) < 2:
        return 0.0
    m = _safe_mean(values)
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)
