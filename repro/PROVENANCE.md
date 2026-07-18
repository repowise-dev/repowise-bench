# Provenance — context-tool benchmark, serious run

Stub written for the serious (publishable-grade) Track 1 + Track 2 run.
Fill in final run dates/commits as each phase completes.

## Harness / tooling versions

| Component | Version / pin | Notes |
|-----------|---------------|-------|
| Claude Code | 2.1.214 | Agent + judge driver. Cache-breakpoint strategy is version-sensitive — a CLI auto-update mid-campaign invalidates cost comparability. |
| Agent model | `sonnet` | Same across every arm. |
| Judge model | `sonnet` | Blind judge; identical rubric across arms. |
| repowise doc model | `gpt-5.4-nano` | Index synthesis model (OpenAI). |
| repowise provider / embedder | `openai` / `openai` | Forwarded to the served MCP process. |
| CodeGraph (C2) | 1.4.1 | npm; `codegraph serve --mcp --path <repo>`. |
| Serena (C3) | `uvx git+https://github.com/oraios/serena` @ HEAD | Not commit-pinned in the config; session-1 run recorded commit c7ee15c0. Structural control (no index). |
| Repomix (C4) | 1.16.1 | Packed-XML arm; packs under data/context_bench/repomix_packs/. |
| DeepWiki (C5) | remote MCP (mcp.deepwiki.com) | Uncontrolled upstream version — their wiki ≠ our pins (skew caveat). |
| bench repo | branch feat/context-tool-benchmark-suite | Serious-run configs committed. |

## Target pins (staged out-of-tree, pre-indexed)

| Target | Commit | Staging dir |
|--------|--------|-------------|
| pallets/flask | 258d68b6 | ~/bench-staging/pallets_flask |
| fastify/fastify | v5.10.0 (94bcbcc6) | ~/bench-staging/fastify_fastify |
| flask drift (stale index) | index @ 3.1.2 (2c1b30d0), worktree @ 258d68b6 | ~/bench-staging/pallets_flask_drift |

## Run design

- **Arms (Track 1, 7):** C0 bare, C1 repowise (lean, neutral), C2 codegraph,
  C3 serena, C4 repomix, C5 deepwiki, C6 repowise + authentic generated
  CLAUDE.md (lean served surface). C6 CLAUDE.md rendered via
  `repowise generate-claude-md` from each pinned index.
- **Arms (Track 2 drift, 6):** C0–C5 (C6 omitted; C1 is the abstention arm).
- **Regime:** warm+serial for every arm (`warmup: true` + `max_workers: 1`) so
  billed cost is comparable. Report billed-warm as headline, cache-neutral as
  the robustness check. Never compare billed cost across worker/warmth regimes.
- **Repeats:** flask n=2 (medians + ranges), fastify n=1 (style-confounded
  secondary result — see honesty ledger).
- **Question sets (FROZEN):** flask 24 + why-flask 8 = 32/arm; fastify 24 +
  why-fastify 7 = 31/arm; drift 15 gold pairs. Only the re-grounded + verified
  subset is in scope (the rest of flask48 is unverified and OUT).

## Run environment

```
DO_NOT_TRACK=1 REPOWISE_EMBEDDER=openai REPOWISE_PROVIDER=openai REPOWISE_DOC_MODEL=gpt-5.4-nano
OPENAI_API_KEY  # from ~/Desktop/repowise/hosted-backend/.env, never committed
```

## Commands

```
cd ~/Desktop/repowise/repowise-bench && source ~/Desktop/repowise/.venv/bin/activate
python harness/run_experiment.py --config configs/context_bench_flask_serious.yaml       # flask rep1
python harness/run_experiment.py --config configs/context_bench_flask_serious_rep2.yaml  # flask rep2
python harness/run_experiment.py --config configs/context_bench_fastify_serious.yaml     # fastify n=1
python harness/run_experiment.py --config configs/context_bench_drift_serious.yaml       # track 2 drift
```
