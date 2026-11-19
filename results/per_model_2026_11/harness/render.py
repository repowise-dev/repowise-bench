"""Render the per-model tables (markdown) from the run directories, in the pre-registered row order.

    python render.py > ../tables.md

Each row's expected n comes from the amendment that set its runs per arm (A1: 3, A4: 2), and only
reps r1..rN count for that row. Correctness is shown under the current gold (after A5) and under the
pre-A5 gold, so the effect of A5 is visible per row.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from analyze import load, questions, summarise

BENCH = Path(__file__).resolve().parent.parent
N_QUESTIONS = 24  # A1 grid set
# (run dir, label, runs per arm, amendment that set it); reps=None means not run, with the reason.
ROWS = [
    ("claude-fable-5-1", "Fable 5.1", None, "not run: subscription capacity (A4)"),
    ("claude-opus-5-5", "Opus 5.5", 2, "A4"),
    ("claude-sonnet-5-5", "Sonnet 5.5", 2, "A4"),
    ("claude-opus-4-8", "Opus 4.8", 2, "A4"),
    ("claude-opus-4-6", "Opus 4.6", None, "not run: subscription capacity (A4)"),
    ("claude-sonnet-4-6", "Sonnet 4.6", 3, "A1"),
    ("claude-haiku-4-5", "Haiku 4.5", 3, "A1"),
]
NOT_RUN = {"codex": ("Codex gpt-5.6-terra", "not run: Codex CLI 0.154 `exec` exposed no tools from the stdio Repowise server to the model (isolated home and operator home alike), so there is no Repowise arm to compare (A3)")}
# Gold as registered before A5, kept only to report the A5 effect.
PRE_A5 = {("LEV-02", "half_life"): r"\bhalf_life\s*\("}
REPOS = [("foreman", "foreman (Python)"), ("okf-agent-memory", "okf-agent-memory (Go)"), ("leviathan", "leviathan (Rust)")]
KINDS = ["locate", "flow", "impact"]


def pre_a5(qs: dict) -> dict:
    out = copy.deepcopy(qs)
    for (qid, label), rx in PRE_A5.items():
        for m in out[qid]["must"]:
            if m["label"] == label:
                m["regex"] = rx
    return out


def pct(v: dict) -> str:
    return f"{v['pct']:+.1f}% [{v['pct_lo']:+.1f}, {v['pct_hi']:+.1f}]"


def pair(v: dict, fmt: str) -> str:
    return f"{format(v['bare'], fmt)} / {format(v['rw'], fmt)} ({v['pct']:+.0f}%)"


def rows_for(d: str, reps: int, qs: dict) -> list[dict]:
    p = BENCH / "runs" / d
    return [r for r in load(p, qs) if r["rep"] <= reps] if p.exists() else []


def main() -> None:
    qs, qs_pre = questions(), pre_a5(questions())
    main_t = ["| Model | Runs per arm | Pairs | Repowise used | Output tokens bare / repowise | Change [95% CI] | Leaner on | Tool calls | Files opened | Wall clock (s) | Correct bare / repowise |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot_t = ["| Model | Total tokens bare / repowise | Change [95% CI] |", "|---|---:|---:|"]
    a5_t = ["| Model | Bare correct, pre-A5 / A5 | Repowise correct, pre-A5 / A5 |", "|---|---:|---:|"]
    slice_t = {"repo": [], "kind": []}
    notes, detail = [], {}
    for d, name, reps, why in ROWS:
        if reps is None:
            main_t.append(f"| {name} | 0 | 0 | | {why} | | | | | | |")
            notes.append(f"- {name}: {why}.")
            continue
        rows = rows_for(d, reps, qs)
        want = N_QUESTIONS * reps
        s = summarise(rows)
        s_pre = summarise(rows_for(d, reps, qs_pre))
        s = {k: v for k, v in s.items() if k != "cost"}
        detail[d] = {"all": s, "pre_a5": {k: s_pre[k] for k in ("correct_bare", "correct_rw")}}
        n = s["n_pairs"]
        short = "" if n >= want else f" (incomplete: {n}/{want} pairs)"
        o, c, f, w, t = (s[k] for k in ("output", "tool_calls", "files", "wall", "total_tokens"))
        main_t.append(
            f"| {name}{short} | {reps} ({why}) | {n} | {s['rw_used']}/{n} | {o['bare']:,.0f} / {o['rw']:,.0f} | {pct(o)} | "
            f"{s['leaner_q']} of {s['n_questions']} | {pair(c, '.1f')} | {pair(f, '.1f')} | {pair(w, '.0f')} | "
            f"{s['correct_bare']} / {s['correct_rw']} of {n} |")
        tot_t.append(f"| {name} | {t['bare']/1000:,.0f}k / {t['rw']/1000:,.0f}k | {pct(t)} |")
        a5_t.append(f"| {name} | {s_pre['correct_bare']} / {s['correct_bare']} of {n} | {s_pre['correct_rw']} / {s['correct_rw']} of {n} |")
        for dim, keys in (("repo", REPOS), ("kind", [(k, k) for k in KINDS])):
            for key, label in keys:
                ss = {k: v for k, v in summarise([r for r in rows if r[dim] == key]).items() if k != "cost"}
                detail[d][f"{dim}:{key}"] = ss
                so, sf = ss["output"], ss["files"]
                slice_t[dim].append(
                    f"| {name} | {label} | {so['bare']:,.0f} / {so['rw']:,.0f} | {pct(so)} | {ss['leaner_q']} of {ss['n_questions']} | "
                    f"{sf['bare']:.1f} / {sf['rw']:.1f} | {ss['correct_bare']} / {ss['correct_rw']} of {ss['n_pairs']} |")
    for name, why in NOT_RUN.values():
        main_t.append(f"| {name} | 0 | 0 | | {why} | | | | | | |")
        notes.append(f"- {name}: {why}.")
    head = "| Model | {} | Output tokens bare / repowise | Change [95% CI] | Leaner on | Files opened bare / repowise | Correct bare / repowise |\n|---|---|---:|---:|---:|---:|---:|"
    out = ["## Per model", "", *main_t, "", "Not run:", *notes, "",
           "## Correctness before and after A5", "", *a5_t, "",
           "## Total tokens (recorded, not a headline metric)", "", *tot_t, "",
           "## By repository", "", head.format("Repo"), *slice_t["repo"], "",
           "## By question kind", "", head.format("Kind"), *slice_t["kind"]]
    print("\n".join(out))
    (BENCH / "summary.json").write_text(json.dumps(detail, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
