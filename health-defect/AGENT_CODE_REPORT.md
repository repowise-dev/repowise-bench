# Defect Dynamics of Agent-Authored Code

_A 28-repo empirical study of how AI-coding-agent commits break, get fixed,
and interact with history-based defect prediction. Data collected 2026-06-03
→ 2026-06-04; observation window 2025-06-01 → repo HEAD (≈12 months into the
agent era)._

---

## 1. Questions

1. **Provenance** — can agent-authored commits be identified deterministically,
   at precision safe enough to build on?
2. **Labels** — do the field's standard defect-labeling protocols (keyword
   fixes, SZZ) survive repos where agents merge most of the code?
3. **Characterization** — does agent code break differently from human code in
   the same repo?
4. **Induction & survival** — do agent commits *cause* more defects, and do
   their lines live shorter lives?
5. **Prediction** — do history-based defect predictors (prior-fix recurrence,
   ownership, author experience, change entropy, churn, size — the signals
   calibrated risk models rest on) still work on agent-authored changes?

## 2. Methodology

- **Provenance detector** (`lib/agent_provenance.py`): per-commit
  `{agent, autonomy_tier, channel, confidence}` from 8 channels — commit
  identity fields (bot accounts / service e-mails), message footers, co-author
  trailers, plus merged-PR evidence (bot PR author, agent branch prefix,
  PR-body footer) via cached per-repo PR indexes. Patterns are anchored to
  service identities (never bare names). **Blind validation: 96.15% detection
  precision** (124 commits, 6 independent reviewers, 17 repos; 6/8 channels at
  100%; 0/20 missed agents in the negative stratum). The only real FP mode —
  human follow-up commits inside agent PRs — is confidence-downgraded and
  filterable.
- **Autonomy tiers** (labelled, never pooled): **T1** bot-account commit
  (near-autonomous: Devin, Copilot coding agent, Cursor cloud) · **T2**
  human-driven agent (Claude Code / Codex footers, agent branches) · **T3**
  assisted (co-author trailer only).
- **Corpus**: 28 repos, criteria locked before any label was measured
  (`CORPUS_SELECTION_MEMO.md`): ≥1k★, real software, active, fix-rate gate
  5–25% with ≥40% → saturation exhibit. Cohorts: agent-heavy / mixed
  (non-AI-domain oversampled: grafana, mattermost, umbraco, camel, metabase,
  nethermind, dart…) / controls (pytorch, strapi, NeMo, atproto, shiki).
  **Main analysis pool = 14 PASS repos · saturation exhibit = 7 repos (100%
  agent-dominated)** · 5 controls + borderlines outside both.
- **Labels** (`agent_defect_labels.py`): keyword fix classifier (benchmark
  patterns) per commit, three protocols — **raw**, **spam-collapsed** (drop
  self-fix-within-48h churn + reverts), **fully-gated** (fix linked to an
  issue carrying a bug-class label, closing refs resolved through PR bodies).
- **Induction** (`agent_szz_induction.py`): AG-SZZ (cosmetic-line and
  fix-of-fix guards) with **B-SZZ as mandatory sensitivity** — AG drops
  fix-commits as inducers, and agents fix more, so AG alone mechanically
  shields agents. Blame at fix parents on `git backfill`-ed blobless clones;
  fixes touching >50 code files skipped as bulk refactors; outcome
  eligibility ≥90 days before HEAD.
- **Statistics**: within-repo contrasts first (Δ tier − human in the same
  repo), cluster bootstrap over repos, MIN_N floors per cell; adjusted models
  = pooled logit with repo fixed effects + log1p(lines added/deleted/files)
  Kamei controls, cluster-bootstrap odds ratios; never pool raw rates across
  repos (Simpson's guard).

Reproduction: every artifact is regenerable from the scripts in
`health-defect/` against the cached `agent-repos/` data — see §9.

## 3. The corpus is the first result: agents now write large shares of real repos

Per the provenance walk (non-merge commits):

- **github/gh-aw: 72.9% of all commits agent-attributed** (8.6k Copilot-agent
  commits — a T1 monoculture).
- **prefect (born 2018): 66.5% of commits since 2025-06 are agent commits;
  82% in 2026Q1** (Devin + Claude). A mature Python repo flipped
  agent-majority in ~12 months.
- windmill 63%, omi 62%, novu 56%, dyad 51% (2026Q2 shares).
- Non-AI-domain: mattermost 29%, umbraco 21%, camel (2009, Java) 17%,
  grafana 11%, formatjs 39%.
- Controls hold near zero (pytorch 0.7%, shiki 0.6%).

## 4. Labels: the firehose saturates them — and only where agents dominate

- Keyword file-level positive rates ≥40% (up to 100%) occur in **7/28 repos —
  all agent-heavy/agent-dominated** (gh-aw, worldmonitor, windmill, verifiers,
  fern, basic-memory, Netcatty). The openclaw pattern replicates exactly and
  only where agents dominate. The strongest wild T1 repos saturate — that is
  a finding about the population, not a sampling failure.
- **Self-fix spam separates cohorts**: 23–54% of fix commits in agent-heavy
  repos re-fix the same identity's own files within 48h (vs ≤20% mixed,
  0–15% controls).
- **Spam-collapse halves saturation but does not rescue it** (gh-aw
  64.5%→30.1%); **issue gating works only with GitHub-label hygiene**
  (umbraco 63% of fixes issue-linked; mattermost/camel/novu ≈ 0% — external
  trackers). Label protocol must be chosen per repo.

## 5. Characterization: agents are the maintenance crew, not the bug factory

Within-repo Δ vs human, cluster-bootstrap 95% CI (main pool):

- **Agent commits skew toward fixing**: fix share T1 +19.6pp* [+8.0, +34.5],
  T2 +8.2pp* [+3.3, +13.3] (15 repos).
- **fix90 (followed-by-fix-within-90d) shows NO significant tier effect**
  (T2 +4.3pp [−2.2, +9.4], 11 repos) — agent commits are not measurably more
  fix-attracting at this power.
- T3 self-fixes more (+5.7pp* [+3.2, +8.4]).
- The fix90 "2–4-file band" signal (+8.2pp*) **fails to replicate under SZZ**
  — the direction flips (T2 −5.5pp* raw): a fix-attraction artifact, not
  defect induction.

## 6. Induction & survival: no evidence agents induce more defects

**AG-SZZ, 112,382 eligible commits, 14 repos, size/churn-adjusted (logit:
tier + log la/ld/nf + repo FE, cluster-bootstrap OR):**

| fix set | T1 OR | T2 OR | T3 OR |
|---|---|---|---|
| raw | 0.75\* [0.43, 0.95] | **0.57\*** [0.42, 0.76] | 0.96 |
| spam-collapsed | 0.54\* [0.43, 0.79] | 0.57\* [0.44, 0.83] | 0.91\* |

B-SZZ sensitivity attenuates toward null (T2 raw 0.79 [0.68, 1.01]) but
**never flips above 1**. Claim: *no evidence agent commits induce more
defects than human commits in the same repo; point estimates protective.*

**Line survival** (sampled blame, ≥6-month exposure): **T2 lines outlive
human lines by +17.9pp\*** [+6.7, +30.1]; T3 +9.4pp* [+3.2, +15.5]. The
exception is Devin/T1 in prefect (0.57 vs 0.79 human) — consistent with its
self-fix churn.

**Agent-vs-agent**: no stable cross-repo ranking (deployment pattern
dominates identity), but one replicated cell: **Devin re-fixes its own files
~3× more than Claude in the same repo** (prefect 22.8% vs 7.2%; fern 30.4%
vs 10.7%).

## 7. Prediction: the predictors survive — except where autonomy is highest

Per-predictor univariate AUC on AG-SZZ outcomes, per repo × authorship group,
pooled with cluster-bootstrap CIs (orientation not flipped; protective
< 0.5). Main pool, raw protocol (`_predictors/PREDICTOR_EVAL.md`):

| predictor | human (14) | T2 Δ vs human (8) | T3 Δ vs human (8) | T1 (2 repos) |
|---|---|---|---|---|
| churn | 0.790 [0.764, 0.814] | +0.005 [−0.078, +0.080] | +0.013 [−0.037, +0.064] | 0.765 (Δ−0.036) |
| files touched | 0.749 | −0.004 | +0.011 | 0.718 (Δ−0.060) |
| change entropy | 0.735 | −0.019 | +0.004 | 0.711 (Δ−0.056) |
| prior-fix recurrence | 0.662 | −0.010 [−0.088, +0.064] | +0.009 | **0.473 (Δ−0.160)** |
| ownership | 0.525 | +0.023 | +0.024 | 0.459 |
| author experience (email) | 0.484 | −0.018 | −0.044 | 0.406 |
| author experience (identity-mapped) | 0.485 | −0.044 | **−0.116\*** [−0.164, −0.073] | 0.406 |

Readings:

1. **On human-driven agent code (T2/T3 — the overwhelming majority of agent
   commits in the wild), every predictor's Δ vs human has a CI containing 0.**
   History-based prediction does not break where a human drives the agent.
2. **Prior-fix recurrence — the strongest signal in the human corpus — drops
   to chance (0.473) on T1 bot-account commits in the main pool** (point
   estimate from the only 2 evaluable T1 cells, prefect 0.437 / airbyte
   0.509; no CI possible — the §3 gate's "breaks" bar is NOT met).
   Importantly, the exhibit pool's T1 cells do NOT show the collapse
   (gh-aw 0.60, windmill 0.78, fern 0.69) — so this is a
   repo/deployment-specific effect (Devin-on-mixed-repos), not a law of
   autonomy. Candidate mechanism where it appears: autonomous agents are
   *assigned* to fix-prone files, decoupling a file's fix history from the
   marginal risk of the next bot change. Verdict: watch-item, not a
   confirmed break; the thin-T1-cell problem is itself the §4 finding.
3. **Author experience neither flips nor dies.** The human multivariate
   coefficient is −0.213 — replicating the calibrated model's protective
   −0.23 on an entirely new corpus. For agents: T1 stays protective
   (−0.404*), T3 attenuates to ≈0 (+0.04 ns) under e-mail identity; under
   agent-identity mapping experience is *more* protective for T3 (univariate
   Δ−0.116*). Identity mapping matters and must be deliberate — but no flip,
   anywhere.
4. **A human-trained JIT model transfers to agent code unchanged.**
   Leave-one-repo-out logistic (Kamei features) trained on human commits
   scores: human 0.772, T2 0.778, T3 0.806, T1 0.772 — equal or better on
   agent cells. (The human cell exactly reproduces the shipped change-risk
   reference ≈0.772.)
5. **"Agent-authored" is not a predictor once size/churn are controlled:**
   adding tier dummies to the LORO model moves pooled AUC by +0.0001 (raw)
   to +0.007 (gated); coefficients are small with mixed signs — consistent
   with the §6 ORs. There is no justification for an `author_is_agent`
   feature in a risk model.
6. **File-level signals hold too** (T_MID split: features from history
   <2026-01, labels = fix-touched ≥2026-01): prior-fix AUC agent-heavy 0.701
   vs mixed 0.643 vs control 0.681; churn-volume and size likewise. A file's
   agent-share is itself only weakly predictive (0.55) — consistent with (5).

### 7b. The saturation exhibit: prediction under the firehose

SZZ walks and the identical eval were run on the 7 saturation-exhibit repos
(the openclaw question, now answerable on 7 repos instead of 1). Three-part
answer (`PREDICTOR_EVAL_EXHIBIT.md`, `FILE_PREDICTORS_EXHIBIT.md`):

1. **What the firehose breaks is file-level keyword labeling — not the
   commit-level defect structure.** At file level, positive rates of
   0.30–0.86 compress prior-fix AUC to 0.623 pooled, and in the worst repos
   to near-chance (gh-aw 86% positive → 0.556; Netcatty 62% → 0.541) — the
   openclaw collapse, replicated. At commit level with AG-SZZ outcomes, the
   same repos predict fine: churn 0.78–0.88, prior-fix 0.66–0.74, LORO
   model 0.76–0.82 across groups.
2. **The gated protocol rescues prediction — monotonically.** Raw →
   spam-collapsed → fully-gated improves every headline AUC on the
   exhibits: human churn 0.782 → 0.809 → 0.835; prior-fix 0.659 → 0.678 →
   0.704; the LORO model 0.763 → 0.804 → 0.823. De-spamming the labels is
   what matters; SZZ then concentrates the inducing sets despite the fix
   volume. (Caveat: fully-gated cells exist only where issue hygiene does —
   3–4 of 7 repos.)
3. **The identity-based signals genuinely die for agents in agent-dominated
   repos — the one CI-backed degradation in the study.** Author experience
   stays protective for humans on the exhibits (0.427†, CI excludes 0.5)
   but goes to chance for agent commits: Δt1 +0.111* [+0.041, +0.180],
   Δt2 +0.125* [+0.034, +0.203] (toward 0.5 = uninformative). An agent
   identity's "experience count" carries no risk information once the agent
   dominates the repo. Ownership weakly inverts on T2 (0.438†). This —
   not churn, not prior-fix, not the model — is what an agent-era risk
   model must treat differently.

Together with §7(2): the degradation pattern is *autonomy- and
dominance-graded* — nothing breaks for human-driven agents in mixed repos;
recurrence weakens for bot accounts; identity signals die where agents
dominate; and file-level keyword labels (the cheapest product shortcut)
are the first casualty.

## 8. Limitations

- **Selection bias** — the corpus skews toward attribution-keeping and
  AI-adjacent projects; population rates are not claimed. Non-AI-domain
  mixed repos were deliberately oversampled.
- **Age confound** — agent repos are young; every agent-heavy claim is
  within-repo or against matched controls, and tiers are never pooled.
- **Short windows** — ≈12 months of agent era; time-to-fix right-censored
  (90-day eligibility guard everywhere). Re-run in 6 months doubles the
  post-onset window.
- **Provenance false negatives** — humans committing agent output under
  their own identity are invisible; all findings are about *detected* agent
  code (precision-first detector, 96.15% blind precision).
- **Review confound** — merged agent commits are post-review code. These
  results say nothing about unreviewed agent output; the protective ORs may
  partly measure review stringency toward agents.
- **T1 thinness** — the main-pool T1 cell is 3 repos *because the strongest
  wild T1 repos saturate the labels*; T1 cells here are point estimates.
  PR-level designs are the path to T1 power (PHASE3_RESEARCH_NOTES.md §5).
- **SZZ validity** — AG vs B sensitivity is reported everywhere it matters;
  blame-based induction inherits SZZ's known limits (tangled changes,
  bulk-refactor skipping at 50 files).

## 9. Reproduction

All commands run from the bench worktree with the venv interpreter,
`$env:PYTHONIOENCODING="utf-8"`, data under `repowise-bench/agent-repos/`:

```powershell
$py = "C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe"
$d  = "C:\Users\ragha\Desktop\repowise\repowise-bench\agent-repos"

# 1. clones + PR indexes + provenance + validation (Phase 1)
& $py health-defect\clone_agent_corpus.py --dest $d
& $py health-defect\provenance_walk.py --repos-dir $d --out-dir $d\_provenance
# 2. labels + issue gating (Phase 2)
& $py health-defect\agent_defect_labels.py --repos-dir $d --provenance-dir $d\_provenance --out-dir $d\_labels
# 3. SZZ induction (git backfill the clones first)
& $py health-defect\agent_szz_induction.py --repos-dir $d --labels-dir $d\_labels --out-dir $d\_szz
& $py health-defect\agent_szz_analysis.py  --szz-dir $d\_szz --labels-dir $d\_labels --out-dir $d\_szz [--szz-kind b]
# 4. survival + agent-vs-agent + characterization
& $py health-defect\agent_line_survival.py ... ; & $py health-defect\agent_vs_agent.py ... ; & $py health-defect\agent_characterization.py ...
# 5. predictor stress-test (this report's §7)
& $py health-defect\agent_predictor_eval.py build --repos-dir $d --labels-dir $d\_labels --provenance-dir $d\_provenance --out-dir $d\_predictors [--pool exhibit]
& $py health-defect\agent_predictor_eval.py eval  --labels-dir $d\_labels --szz-dir $d\_szz --features-dir $d\_predictors --out-dir $d\_predictors [--pool exhibit] [--szz-kind b]
& $py health-defect\agent_file_predictors.py      --labels-dir $d\_labels --features-dir $d\_predictors --out-dir $d\_predictors [--pool exhibit]
```

## 10. Follow-up (the 6-month re-run, ~2026-12)

- Re-walk provenance + labels on the same corpus: post-onset windows double,
  fern/gh-aw-class T1 repos get evaluable gated cells.
- Before/after onset event study (design in PHASE3_RESEARCH_NOTES.md §3).
- PR-level T1 induction design on the cached PR indexes.
- Out-of-sample check of the firehose-flag thresholds before any product
  ship of the flag.
