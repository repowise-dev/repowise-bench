# Contributing

This benchmark is meant to be argued with. The most valuable contribution here is
not a new feature, it is **a number of ours that turns out to be wrong**, and the
repository is laid out so that finding one is possible for someone who does not
work here.

You do not need to run anything to contribute. Reading and disagreeing counts.

---

## Start at whatever depth you want

Each level is the evidence for the one above it. Stop wherever you like.

| | Level | Time |
|---|---|---|
| 1 | **[The summary tables](README.md)** on this page, and the OSS [`docs/BENCHMARKS.md`](https://github.com/repowise-dev/repowise/blob/main/docs/BENCHMARKS.md) | 2 min |
| 2 | **[head-to-head/README.md](head-to-head/README.md)** — who wins what, the build-cost curve, what each index can rank at all | 10 min |
| 3 | **[head-to-head/arms/](head-to-head/arms/)** — one page per competitor: what it is, what it serves, what it is good and bad at, and every setup trap | 5 min each |
| 4 | **[head-to-head/THE_LOOP.md](head-to-head/THE_LOOP.md)** — the method and all nine gates, each named with the failure that created it | 20 min |
| 5 | **[configs/arms.yaml](configs/arms.yaml)** — every launch command, allowlisted tool and exclusion with its reason. **Read this if you think an arm was set up unfairly** | 10 min |
| 6 | **[configs/\*.PREREGISTRATION.md](configs/)** — one per scored run, each committed before its run spent anything. Check the commit dates yourself | varies |
| 7 | **[results/bakeoff_2026_08/](results/bakeoff_2026_08/)** — every graded cell and every verbatim response, including the runs we invalidated, with their invalidation notes attached | as long as you like |
| 8 | **[repro/README.md](repro/README.md)** — per claim: what it costs to rerun, how long it takes, and which ones need credentials we cannot hand you | 5 min |

---

## Three things worth contributing

### 1. Your tool is in the field and its arm is wrong

**This is the contribution we most want, and it needs no Python.** An arm is a YAML
block dropped into [`configs/arms.d/*.yaml`](configs/arms.d/README.md), which merges
over the tracked registry, so you never have to edit a file someone else owns. A
partial block merges too: to change only one arm's tool surface, write only that.

This matters because **four separate arms in this bake-off have scored a clean 0.000
purely because we guessed a setup step wrong.** Graphify ran on 1 of the 10 tools it
serves. Serena would have gone in at 3 of 29 and code-review-graph at 1 of 30.
code-review-graph returned `isError` on 84 of 84 queries because its tools carry a
`_tool` suffix we had not noticed. You know your own launch flags, your real
entry-point tool and your setup steps better than we do.

Read [`configs/arms.d/README.md`](configs/arms.d/README.md) for the block shape and
the three traps that have already produced false zeros here.

**What an arm is entitled to before it scores:** its full advertised tool surface,
its own documented setup steps run for it rather than left for it to fail on, its own
worktree, and a human reading one of its real responses. Every exclusion is named in
its block with a reason. If you disagree with one, that is a one-line change.

### 2. You think one of our numbers is wrong

Open an issue. You do not have to rerun anything first, and a well-argued objection
against a published figure is more useful to us than a new arm.

Useful shapes for an objection, roughly in order of how much they move us:

- **A control we did not run.** The strongest thing anyone has said about this bench
  came from asking what a tool that did *nothing* would score. The answer was 43%
  cheaper than a bare agent, and it retired dollars-per-question as a metric here.
- **A cell whose raw response does not support the path we extracted from it.** Every
  verbatim response is on disk beside what the extractor pulled out of it, precisely
  so this is checkable rather than a matter of trust.
- **A comparison that is not like for like.** Different commit, different tool
  surface, different harness, different date.
- **A number quoted somewhere without the caveat attached to it here.** Our own
  marketing surfaces are fair game for this.

If you do rerun something, [`repro/README.md`](repro/README.md) says what each claim
costs and which ones need credentials. The `qwen3:8b` row in §2 is the one that needs
no account at all.

### 3. A whole new benchmark

Each one gets its own directory:

1. **Create** `<benchmark-name>/`
2. **Add a `README.md`** with methodology, headline numbers and reproduction steps
3. **Add a `run_benchmark.py`** runnable from within the directory
4. **Write results to** `results/<benchmark_name>_{variant}/`
5. **Add a row** to the table at the top of [README.md](README.md)

Shared repos and indexes are reusable from `repos/` and `indexes/`. New Python
dependencies go in the top-level `requirements.txt`.

---

## The rules a contribution has to meet

These are the same rules applied to our own runs, and each one exists because
breaking it produced a wrong published figure at least once. The long version, with
the failure behind each gate, is [THE_LOOP.md](head-to-head/THE_LOOP.md).

- **Pre-register before spending.** The reading rule goes in as its own commit before
  a scored run starts, so a favourable result cannot quietly become a different
  question. A pre-registration nobody can date is worth nothing.
- **Seal a half.** Split by instance id before any work begins. The sealed half is
  evaluated once, at publication.
- **One worktree per arm.** Every tool in this field writes its index into a dotdir
  inside the repository it is indexing, so a shared checkout means each arm indexes
  its predecessors' output. The bias favours whoever ran first, which was us.
- **Prove an arm was alive and its extractor works before recording a zero.** A dead
  server, a wrong tool name and a broken output parser all score exactly like a bad
  tool. Record `isError`, the served tool list and response size per call.
- **Grade a known-perfect and a known-wrong prediction before grading any real one.**
  This is the cheapest gate here and it caught the worst failure.
- **No timed build runs while another process pool is alive.** Contention inflated one
  of our own index timings by 65%, and it is arm-specific, so it cannot be corrected
  afterwards.
- **Median beside mean, precision and files-served beside coverage.** Never averaged
  into one figure.
- **Publish the losing rows.** We are the slowest indexer in this field by a wide
  margin, our precision is not the best in the table, and we could not replicate
  CodeScene's business-impact result. All three ship.
- **Invalidated runs are never deleted.** They get a banner saying why, and the
  numbers stay visible.

---

## Pull requests

- One benchmark or one arm per PR.
- If a PR changes a published number, say so in the description and say which
  surfaces carry it, including the OSS `docs/BENCHMARKS.md`.
- Raw run output belongs under `results/`, tracked, not in the PR body.
- No new Python dependency for something a few lines do.

Questions, or want to check an idea before spending time on it:
[open an issue](https://github.com/repowise-dev/repowise-bench/issues) or
[hello@repowise.dev](mailto:hello@repowise.dev).

## License

Contributions to the harness are under Apache 2.0, matching the repository. Target
repository checkouts and task corpora remain the property of their original authors.
