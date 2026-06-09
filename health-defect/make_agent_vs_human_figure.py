"""Generate the agent-vs-human code-quality figure from validated scorecards.

Two panels, both from CI-backed numbers in agent-repos/_szz and _survival:
  A. Adjusted odds of introducing a bug, by authorship tier, vs human=1.0
     (AG-SZZ, 112,382 commits, logit + repo FE + size/churn controls).
  B. Paired line-survival to HEAD, human vs human-driven agent (T2), per repo.

Output: agent_vs_human_code_quality.png (300 dpi, light + dark).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# ---- Panel A: adjusted odds ratios (raw fix-set), human baseline = 1.0 -------
# source: agent-repos/_szz/SZZ_INDUCTION.md (adjusted, cluster-bootstrap CI)
tiers = ["Bot agents\n(T1)", "Human-driven\nagents (T2)", "AI-assisted\n(T3)"]
or_mid = [0.754, 0.570, 0.960]
or_lo  = [0.432, 0.421, 0.689]
or_hi  = [0.949, 0.760, 1.082]
sig    = [True, True, False]  # CI excludes 1.0

# ---- Panel B: paired line survival, human vs T2 ------------------------------
# source: agent-repos/_survival/LINE_SURVIVAL.md (per-repo line-weighted)
pairs = [
    ("airbyte",   0.505, 0.857),
    ("prefect",   0.787, 0.938),
    ("novu",      0.510, 0.697),
    ("mattermost",0.849, 0.876),
]


def render(dark: bool, path: str):
    fg = "#e6e6e6" if dark else "#1a1a1a"
    bg = "#0d1117" if dark else "#ffffff"
    grid = "#30363d" if dark else "#dfe2e5"
    human_c = "#8b949e" if dark else "#6e7681"
    agent_c = "#3fb950" if dark else "#1a7f37"
    warn_c  = "#d29922"

    plt.rcParams.update({
        "figure.facecolor": bg, "axes.facecolor": bg,
        "text.color": fg, "axes.labelcolor": fg,
        "xtick.color": fg, "ytick.color": fg,
        "axes.edgecolor": grid, "font.size": 11,
    })
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(12, 5.2))
    fig.suptitle("Agent-written code is no more bug-prone than human code — "
                 "and its lines last longer",
                 fontsize=14.5, fontweight="bold", y=0.99)

    # Panel A -- odds ratios (log scale so 0.5 and 2.0 are symmetric)
    y = list(range(len(tiers)))[::-1]
    for yi, m, lo, hi, s in zip(y, or_mid, or_lo, or_hi, sig):
        c = agent_c if (m < 1 and s) else (warn_c if not s else agent_c)
        axA.plot([lo, hi], [yi, yi], color=c, lw=3, solid_capstyle="round", zorder=2)
        axA.scatter([m], [yi], color=c, s=90, zorder=3, edgecolor=bg, linewidth=1.2)
        axA.annotate(f"{m:.2f}" + ("*" if s else ""), (m, yi),
                     textcoords="offset points", xytext=(0, 12),
                     ha="center", fontsize=10.5, fontweight="bold", color=c)
    axA.axvline(1.0, color=human_c, ls="--", lw=1.6, zorder=1)
    axA.annotate("human baseline = 1.0", (1.0, -0.62), ha="center",
                 fontsize=9, color=human_c)
    axA.set_xscale("log")
    axA.set_xticks([0.4, 0.5, 0.7, 1.0, 1.4])
    axA.set_xticklabels(["0.4", "0.5", "0.7", "1.0", "1.4"])
    axA.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    axA.set_yticks(y)
    axA.set_yticklabels(tiers)
    axA.set_xlim(0.38, 1.5)
    axA.set_ylim(-1.0, len(tiers) - 0.4)
    axA.set_xlabel("Adjusted odds of introducing a bug  (← fewer)")
    axA.set_title("A.  Bug-induction vs human, same repo\n"
                  "AG-SZZ · 112,382 commits · size/churn-adjusted",
                  fontsize=11, loc="left")
    axA.grid(axis="x", color=grid, lw=0.6, alpha=0.6)
    for sp in ["top", "right"]:
        axA.spines[sp].set_visible(False)

    # Panel B -- paired survival slopes
    # nudge near-identical agent endpoints apart for legibility (labels only)
    label_dy = {"mattermost": +0.012, "airbyte": -0.012}
    for i, (repo, h, a) in enumerate(pairs):
        axB.plot([0, 1], [h, a], color=agent_c, lw=2, alpha=0.9, zorder=2)
        axB.scatter([0], [h], color=human_c, s=70, zorder=3)
        axB.scatter([1], [a], color=agent_c, s=70, zorder=3)
        axB.annotate(repo, (1.03, a + label_dy.get(repo, 0)),
                     fontsize=9.5, va="center", color=fg)
    axB.set_xlim(-0.15, 1.35)
    axB.set_ylim(0.45, 1.0)
    axB.set_xticks([0, 1])
    axB.set_xticklabels(["human", "agent (T2)"])
    axB.set_ylabel("Share of added lines still alive at HEAD")
    axB.set_title("B.  Line survival, human vs agent\n"
                  "paired within repo · ≥6-month exposure",
                  fontsize=11, loc="left")
    axB.grid(axis="y", color=grid, lw=0.6, alpha=0.6)
    for sp in ["top", "right"]:
        axB.spines[sp].set_visible(False)
    axB.legend(handles=[
        Patch(color=human_c, label="human lines"),
        Patch(color=agent_c, label="agent lines (+17.9pp aggregate*)"),
    ], loc="lower left", fontsize=8.5, frameon=False)

    fig.text(0.5, 0.015,
             "* 95% CI excludes the null.  28-repo study, observation window "
             "2025-06 → HEAD.  Tiers never pooled.  Source: repowise-bench "
             "agent-code study (AG-SZZ + B-SZZ sensitivity).",
             ha="center", fontsize=8, color=human_c)
    fig.subplots_adjust(left=0.11, right=0.96, top=0.86, bottom=0.13, wspace=0.32)
    fig.savefig(path, dpi=300, facecolor=bg)
    print("wrote", path)


render(dark=False, path="agent_vs_human_code_quality.png")
render(dark=True,  path="agent_vs_human_code_quality_dark.png")
